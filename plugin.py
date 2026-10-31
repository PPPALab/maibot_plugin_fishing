"""Native MaiBot SDK entrypoint. No LLM tools/actions or OneBot HTTP calls."""
import asyncio
import base64
import contextlib
from contextvars import Context
import copy
import fcntl
from dataclasses import asdict
from datetime import date, datetime
import json
import logging
from pathlib import Path
import re
import sqlite3
import uuid

from maibot_sdk import Command, MaiBotPlugin

from .config import FishingConfig
from .blocking import GameWorker, drain_on_cancel, run_blocking
from .core.database.unit_of_work import atomic, bind_services, connection
from .core.services.sicbo_service import SicboBet, SicboGame
from .core.utils import get_now
from .engine import FishingPlugin as GameEngine
from .events import GameEvent, Reply

logger = logging.getLogger(__name__)
COMMANDS = json.loads(Path(__file__).with_name('commands.json').read_text())
# Bodies exist upstream but are not decorated in main.py; expose without inventing
# new gameplay. They remain separate from the 124 original command declarations.
SUPPLEMENTAL = [
    {'method': 'use_item', 'aliases': ['使用道具'], 'admin': False, 'handler': 'inventory_handlers'},
    {'method': 'use_bait', 'aliases': ['使用鱼饵'], 'admin': False, 'handler': 'inventory_handlers'},
    {'method': 'refine_help', 'aliases': ['精炼帮助'], 'admin': False, 'handler': 'inventory_handlers'},
    {'method': 'aquarium_help', 'aliases': ['水族箱帮助'], 'admin': False, 'handler': 'aquarium_handlers'},
    {'method': 'sicbo', 'aliases': ['骰子'], 'admin': False, 'handler': 'gacha_handlers'},
]
ROUTES = {alias: command for command in COMMANDS for alias in command['aliases']}
ROUTES.update({alias: command for command in SUPPLEMENTAL for alias in command['aliases']})
PATTERN = r'^/?(?:' + '|'.join(re.escape(x) for x in sorted(ROUTES, key=len, reverse=True)) + r')(?:\s|$)'


class FishingPlugin(MaiBotPlugin):
    config_model = FishingConfig

    @classmethod
    def build_config_schema(cls, **kwargs):
        from .config import expand_nested_settings
        return expand_nested_settings(super().build_config_schema(**kwargs))

    def __init__(self):
        super().__init__()
        from .config_upgrade import repair_legacy_config
        repair_legacy_config(Path(__file__).with_name('config.toml'))
        self.lock = asyncio.Lock()
        self.engine = None
        self.tasks = []
        self.web_task = None
        self.accepting = False
        self.process_lock = None
        self.worker = None
        self._unload_task = None

    async def on_load(self):
        # Single owner for persisted in-memory games, across workers and reloads.
        self.settings = self.config.checked()
        self.data_dir = Path(self.ctx.paths.data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.process_lock = (self.data_dir / '.astrbot-fishing.lock').open('a+b')
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.process_lock.close()
            self.process_lock = None
            raise RuntimeError('此插件数据目录已有运行实例或正在恢复备份')
        try:
            await self._load_game()
        except BaseException:
            await self.on_unload()
            raise

    async def _load_game(self):
        self.settings = self.config.checked()
        self.data_dir = Path(self.ctx.paths.data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = str(self.data_dir / 'astrbot-fishing.sqlite3')
        self.runtime_dir = Path(self.ctx.paths.runtime_dir).resolve()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.worker = GameWorker()
        async with self.lock:
            await self.worker.run(self._initialize_game)
        self.engine.native_start_web = self._request_start_web
        self.engine.native_stop_web = self._request_stop_web
        self.accepting = True
        self.tasks = [asyncio.create_task(self.scheduler(), name='astrbot-fishing-scheduler', context=Context())]
        if self.settings['webui']['enabled']:
            try:
                await self.start_web()
            except Exception:
                logger.exception('后台启动失败；游戏继续运行')

    async def _initialize_game(self):
        # Migrations may manage their own connections, before the game transaction.
        from .core.database.migration import run_migrations
        run_migrations(self.db_path, str(Path(__file__).parent / 'core/database/migrations'))
        with atomic(self.db_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS port_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS port_receipts (key TEXT PRIMARY KEY, created REAL NOT NULL)')
            self.engine = GameEngine(str(self.data_dir), self.settings, str(self.runtime_dir))
            bind_services(self.engine, self.db_path)
            self.engine.sicbo_service.set_message_callback(self._queue_announcement)
            self.restore_games()
            if db.failed:
                raise RuntimeError('游戏初始化事务失败，拒绝开放命令')

    async def _request_start_web(self):
        self._controls.append('start')
        return '__fishing_start_web__'

    async def _request_stop_web(self):
        self._controls.append('stop')
        return '__fishing_stop_web__'

    async def _queue_announcement(self, info, result):
        self._announcements.append((info, result))

    @contextlib.contextmanager
    def game_transaction(self):
        """只快照业务状态，不复制连接、任务或锁；提交失败也统一恢复。"""
        sicbo = self.engine.sicbo_service
        mechanics = self.engine.game_mechanics_service
        zones = self.engine.fishing_zone_service
        # Zone CRUD replaces this cache, rather than mutating its strategies.
        # Other service fields are immutable configuration/registries or legacy
        # background flags (native runtime never starts those background loops).
        before = (copy.deepcopy(sicbo.games), mechanics._server_suppressed,
                  mechanics._last_suppression_date, sicbo.countdown_seconds, sicbo.message_mode,
                  zones.strategies)
        try:
            with atomic(self.db_path) as db:
                yield db
                if db.failed:
                    raise RuntimeError('数据库事务被标记为失败')
                self.save_games()
                if db.failed:
                    raise RuntimeError('游戏状态保存失败')
        except BaseException:
            (sicbo.games, mechanics._server_suppressed, mechanics._last_suppression_date,
             sicbo.countdown_seconds, sicbo.message_mode, zones.strategies) = before
            raise

    def restore_games(self):
        mechanics = connection(self.db_path).execute("SELECT value FROM port_state WHERE key='mechanics'").fetchone()
        if mechanics:
            record = json.loads(mechanics[0])
            self.engine.game_mechanics_service._server_suppressed = bool(record.get('suppressed'))
            self.engine.game_mechanics_service._last_suppression_date = date.fromisoformat(record['date']) if record.get('date') else None
        row = connection(self.db_path).execute("SELECT value FROM port_state WHERE key='sicbo'").fetchone()
        if row:
            for sid, record in json.loads(row[0]).items():
                for key in ('start_time', 'end_time'):
                    record[key] = datetime.fromisoformat(record[key])
                record['bets'] = [SicboBet(**{**b, 'created_at': datetime.fromisoformat(b['created_at'])}) for b in record['bets']]
                self.engine.sicbo_service.games[sid] = SicboGame(**record)

    def save_games(self):
        mechanics = self.engine.game_mechanics_service
        connection(self.db_path).execute("INSERT OR REPLACE INTO port_state VALUES ('mechanics', ?)",
            (json.dumps({'suppressed': mechanics._server_suppressed,
                         'date': mechanics._last_suppression_date.isoformat() if mechanics._last_suppression_date else None}),))
        games = {sid: asdict(game) for sid, game in self.engine.sicbo_service.games.items() if game.is_active and not game.is_settled}
        connection(self.db_path).execute("INSERT OR REPLACE INTO port_state VALUES ('sicbo', ?)",
                                         (json.dumps(games, default=lambda x: x.isoformat(), ensure_ascii=False),))

    @Command('astrbot_fishing', description='钓鱼游戏（原版全部中文命令）', pattern=PATTERN)
    async def handle_game(self, text='', stream_id='', user_id='', group_id='', platform='', message=None, **kwargs):
        del kwargs
        if not self.accepting:
            return False, '钓鱼游戏正在加载或重载', True
        try:
            event = GameEvent(text=text, user_id=user_id, stream_id=stream_id, group_id=group_id,
                              platform=platform, message=message, admin=str(user_id) in self.settings['admin_ids'])
        except ValueError as error:
            await self.ctx.send.text(str(error), stream_id)
            return False, str(error), True
        command = ROUTES.get(event.message_str.split(' ', 1)[0])
        if command is None:
            await self.ctx.send.text('无法从可信消息段解析游戏命令', stream_id)
            return False, '命令解析失败', True
        if command['admin'] and not event.is_admin():
            await self.ctx.send.text('❌ 此命令仅限配置中的机器人管理员', stream_id)
            return False, '无权限', True
        from .fallbacks import image_text
        event.image_fallback = lambda: image_text(self.engine, event, command['method'])
        async with self.lock:
            if not self.accepting:
                return False, '钓鱼游戏正在加载或重载', True
            try:
                outcome = await self.worker.run(self._execute_command, command, event, platform, message, stream_id)
                if outcome is None:
                    return True, '重复消息已忽略', True
                replies, announcements, controls, effective_uid = outcome
            except Exception:
                logger.exception('游戏命令失败，事务已回滚: %s', command['method'])
                replies = [Reply('text', '❌ 操作失败，数据库事务已回滚；请联系管理员查看日志。')]
                announcements, controls, effective_uid = [], [], event.user_id
            for control in controls:
                result = await (self.start_web() if control == 'start' else self.stop_web())
                replies = [Reply(reply.kind, result, reply.fallback)
                           if reply.content == f'__fishing_{control}_web__' else reply for reply in replies]
            # Fixed upstream temporary filenames must be consumed before the next command.
            for reply in replies:
                await self.send_reply(reply, stream_id, {event.user_id, effective_uid})
            for info, reply in announcements:
                await self.send_reply(reply, info['unified_msg_origin'])
        return True, '游戏命令已处理', True

    async def _execute_command(self, command, event, platform, message, stream_id):
        replies = []
        self._announcements, self._controls = [], []
        with self.game_transaction() as db:
            # Scope ID by platform/account/stream, never by display name.
            receipt = ':'.join((platform, str((message or {}).get('account_id') or ''), stream_id, event.message_id))
            if event.message_id and db.execute('SELECT 1 FROM port_receipts WHERE key=?', (receipt,)).fetchone():
                return None
            db.execute('INSERT OR REPLACE INTO port_state VALUES (?, ?)', ('stream:' + event.user_id, stream_id))
            if command.get('handler'):
                from . import handlers
                handler = getattr(getattr(handlers, command['handler']), command['method'])
                results = handler(self.engine, event)
            else:
                results = getattr(self.engine, command['method'])(event)
            async for reply in results:
                replies.append(reply)
            if event.message_id:
                db.execute('INSERT INTO port_receipts VALUES (?, ?)', (receipt, get_now().timestamp()))
            effective_uid = self.engine._get_effective_user_id(event)
            announcements = self._prepare_announcements(self._announcements)
        return replies, announcements, self._controls, effective_uid

    async def send_reply(self, reply, stream_id, mention_ids=frozenset()):
        if reply.kind == 'image':
            try:
                data = await run_blocking(self._encode_image, reply.content)
                if await self.ctx.send.image(data, stream_id):
                    return True
            except Exception:
                logger.exception('图片发送失败')
            sent = True
            for offset in range(0, len(reply.fallback), 1800):
                sent = bool(await self.ctx.send.text(reply.fallback[offset:offset+1800], stream_id)) and sent
            return sent
        # Upstream emits CQ strings. Never forward them as executable platform text,
        # and never promote arbitrary nickname content into a mention target.
        targets = re.findall(r'\[CQ:at,qq=(\d+)\]', reply.content)
        text = re.sub(r'\[CQ:at,qq=(\d+)\]', lambda m: '@' + m[1], reply.content)
        for target in dict.fromkeys(targets):
            if target in mention_ids:
                try:
                    await self.ctx.send.custom('at', target, stream_id)
                except Exception:
                    logger.warning('发送原生 @ 段失败，保留文字账号')
        return await self.ctx.send.text(text, stream_id)

    @staticmethod
    def _encode_image(path):
        return base64.b64encode(Path(path).read_bytes()).decode('ascii')

    def prepare_announcement(self, session_info, result):
        stream = session_info.get('unified_msg_origin')
        if not stream:
            return
        if self.engine.sicbo_service.is_image_mode():
            try:
                from .draw.sicbo import draw_sicbo_result, save_image_to_temp
                dice = result['dice']
                profits = {}
                for item in result.get('settlement', []):
                    profits[item['user_id']] = profits.get(item['user_id'], 0) + item['profit']
                players = []
                for uid, profit in profits.items():
                    user = self.engine.user_repo.get_by_id(uid)
                    players.append({'username': user.nickname if user else uid, 'profit': profit})
                image = draw_sicbo_result(*dice, [], players)
                path = save_image_to_temp(image, 'sicbo_result_' + uuid.uuid4().hex, str(self.runtime_dir))
                return Reply('image', path, result['message'])
            except Exception:
                logger.exception('骰宝结果图片失败，回退文字')
        return Reply('text', result.get('message', '骰宝结算结束'))

    def _prepare_announcements(self, announcements):
        return [(info, self.prepare_announcement(info, result)) for info, result in announcements
                if info.get('unified_msg_origin')]

    async def tick(self, iteration=0):
        async with self.lock:
            if not self.accepting:
                return
            announcements, auto_notifications = await self.worker.run(self._execute_tick, iteration)
            for info, reply in announcements:
                # Sending is after the durable settlement commit.
                await self.send_reply(reply, info['unified_msg_origin'])
            for stream, text in auto_notifications:
                try:
                    await self.ctx.send.text(text, stream)
                except Exception:
                    logger.exception('自动钓鱼通知发送失败，渔获已保存')

    async def _execute_tick(self, iteration):
        announcements = []
        with self.game_transaction():
            for sid, game in list(self.engine.sicbo_service.games.items()):
                if game.is_active and game.end_time <= get_now():
                    result = await self.engine.sicbo_service._settle_game(sid)
                    announcements.append((game.session_info or {}, result))
            auto_notifications = self.auto_tick() if iteration % 40 == 0 else []
            if iteration % 600 == 0:
                for uid in self.engine.user_repo.get_all_user_ids():
                    self.engine.achievement_service._process_user_achievements(uid)
            if iteration % 60 == 0:
                self.engine.exchange_service.price_service.update_daily_prices()
            if iteration % 3600 == 0:
                if self.engine.is_tax:
                    self.engine.fishing_service.apply_daily_taxes()
                self.engine.red_packet_service.cleanup_expired_packets()
                from .media import cleanup_cache
                cleanup_cache(self.runtime_dir)
            prepared = self._prepare_announcements(announcements)
        return prepared, auto_notifications

    def auto_tick(self):
        fishing = self.engine.fishing_service
        notifications = []
        if fishing._reset_rare_fish_daily_quota():
            fishing.enforce_zone_pass_requirements_for_all_users()
        for uid in self.engine.user_repo.get_all_user_ids(auto_fishing_only=True):
            user = self.engine.user_repo.get_by_id(uid)
            if not user:
                continue
            cooldown = fishing.config.get('fishing', {}).get('cooldown_seconds', 180)
            accessory = self.engine.inventory_repo.get_user_equipped_accessory(uid)
            if accessory:
                template = self.engine.item_template_repo.get_accessory_by_id(accessory.accessory_id)
                if template and template.name == '海洋之心':
                    cooldown /= 2
            if user.last_fishing_time and user.last_fishing_time.year > 1 and (get_now() - user.last_fishing_time).total_seconds() < cooldown:
                continue
            zone = self.engine.inventory_repo.get_zone_by_id(user.fishing_zone_id)
            if not zone:
                continue
            if not user.can_afford(zone.fishing_cost):
                user.auto_fishing_enabled = False
                self.engine.user_repo.update(user)
                continue
            result = fishing.go_fish(uid)
            if result:
                texts = list(result.get('equipment_broken_messages') or [])
                if not result.get('success') and '已自动传送回' in result.get('message', ''):
                    texts.append(result['message'])
                if texts:
                    row = connection(self.db_path).execute('SELECT value FROM port_state WHERE key=?', ('stream:' + uid,)).fetchone()
                    if row:
                        notifications.extend((row[0], text) for text in texts)
        return notifications

    async def scheduler(self):
        iteration = 0
        while True:
            try:
                await self.tick(iteration)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('游戏后台任务失败，将重试')
            iteration += 1
            await asyncio.sleep(1)

    async def start_web(self):
        if not self.settings['webui']['enabled']:
            return '后台未启用；请先在配置中启用并设置登录密码'
        if self.web_task and not self.web_task.done():
            return '后台已运行'
        from .web import build_app
        from hypercorn.asyncio import serve
        from hypercorn.config import Config
        config = Config()
        host = self.settings['webui'].get('listen_host', '127.0.0.1')
        config.bind = [f"{host}:{self.settings['webui']['port']}"]
        config.use_reloader = False
        self.web_task = asyncio.create_task(serve(build_app(self), config), name='fishing-web', context=Context())
        self.web_task.add_done_callback(self.web_finished)
        if host == '127.0.0.1':
            return f"后台启动中，仅本地 http://127.0.0.1:{self.settings['webui']['port']}/admin/"
        return f"后台启动中，容器内监听0.0.0.0:{self.settings['webui']['port']}；请通过已映射的宿主机局域网IP访问，并确认该IP在后台主机白名单中"

    @staticmethod
    def web_finished(task):
        if not task.cancelled() and task.exception():
            logger.error('后台停止: %s', task.exception())

    async def stop_web(self):
        if self.web_task:
            self.web_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                try:
                    await self.web_task
                except Exception:
                    # A failed optional server must not abort game/lock cleanup.
                    logger.exception('后台任务已失败，继续关闭插件资源')
            self.web_task = None
        return '后台已关闭'

    async def on_unload(self):
        self.accepting = False
        if self._unload_task is None or self._unload_task.done():
            self._unload_task = asyncio.create_task(self._finish_unload(), context=Context())
        await drain_on_cancel(self._unload_task)

    async def _finish_unload(self):
        await self.stop_web()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        async with self.lock:
            try:
                if self.engine:
                    await run_blocking(self.engine.game_mechanics_service.thread_pool.shutdown,
                                       wait=True, cancel_futures=True)
                if self.worker:
                    await self.worker.close()
                    self.worker = None
            finally:
                self.engine = None
                if self.process_lock:
                    self.process_lock.close()
                    self.process_lock = None

    async def on_config_update(self, scope, config_data, version):
        del scope, version
        # Validate before disturbing a running instance. Reload reconstructs all derived values.
        FishingConfig.model_validate(config_data).checked()
        await self.on_unload()
        await self.on_load()


def create_plugin():
    return FishingPlugin()

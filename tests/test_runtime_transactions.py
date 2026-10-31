import asyncio
import sqlite3
import threading
from contextlib import contextmanager

import pytest

from fishing_port.core.database.unit_of_work import atomic, connection
from test_native_adapter import command, load_plugin


@contextmanager
def broken_commit(monkeypatch):
    from fishing_port.core.database.unit_of_work import Connection
    initialize = Connection.__init__

    class Raw:
        def __init__(self, raw):
            self.raw = raw

        def __getattr__(self, name):
            return getattr(self.raw, name)

        def commit(self):
            raise sqlite3.OperationalError('注入提交失败')

    def connect(state, path):
        initialize(state, path)
        state.raw = Raw(state.raw)

    with monkeypatch.context() as patch:
        patch.setattr(Connection, '__init__', connect)
        yield


@pytest.mark.asyncio
async def test_explicit_rollback_discards_reply_and_receipt(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    original = p.engine.register_user

    async def fail(event):
        async for reply in original(event):
            yield reply
        connection(p.db_path).rollback()

    try:
        monkeypatch.setattr(p.engine, 'register_user', fail)
        await command(p, '注册', mid='retry')
        assert len(p.ctx.send.messages) == 1
        assert '事务已回滚' in p.ctx.send.messages[0][1]
        with atomic(p.db_path) as db:
            assert p.engine.user_repo.get_by_id('12345') is None
            assert not db.execute('SELECT 1 FROM port_receipts').fetchone()
        monkeypatch.setattr(p.engine, 'register_user', original)
        await command(p, '注册', mid='retry')
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345') is not None
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_failed_command_restores_suppression(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    mechanics = p.engine.game_mechanics_service

    async def fail(event):
        mechanics._server_suppressed = True
        from datetime import date
        mechanics._last_suppression_date = date(2026, 1, 1)
        yield event.plain_result('不应发送的成功结果')
        raise RuntimeError('后续写入失败')

    try:
        monkeypatch.setattr(p.engine, 'register_user', fail)
        await command(p, '注册')
        assert mechanics._server_suppressed is False
        assert mechanics._last_suppression_date is None
    finally:
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['command', 'tick', 'initialize', 'web'])
async def test_sqlite_lock_does_not_block_heartbeat(tmp_path, operation):
    p = await load_plugin(tmp_path)
    if operation == 'initialize':
        await p.on_unload()
    if operation == 'web':
        from fishing_port.web import build_app
        p.settings['webui']['secret_key'] = 'test-password'
        client = build_app(p).test_client()
        async with client.session_transaction() as session:
            session.update(logged_in=True, is_admin=True)
    locked, heartbeat, released = threading.Event(), threading.Event(), threading.Event()
    observed = []

    def hold_lock():
        with sqlite3.connect(p.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            locked.set()
            observed.append(heartbeat.wait(2))
            db.rollback()
        released.set()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    await asyncio.to_thread(locked.wait)
    try:
        operations = {'command': lambda: command(p, '注册'), 'tick': lambda: p.tick(1),
                      'initialize': p.on_load}
        if operation == 'web':
            task = asyncio.create_task(client.get('/admin/users'))
        else:
            task = asyncio.create_task(operations[operation]())
        # Queued behind the task: a synchronous lock wait prevents this heartbeat.
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await task
        assert observed == [True]
    finally:
        heartbeat.set()
        await asyncio.to_thread(holder.join)
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['update', 'log', 'commit', 'rollback', 'cancel'])
async def test_real_wipe_bomb_failure_cannot_leak_into_next_commit_or_restart(tmp_path, monkeypatch, failure):
    p = await load_plugin(tmp_path)
    await command(p, '注册')
    p.ctx.send.messages.clear()
    try:
        with monkeypatch.context() as patch:
            patch.setattr('fishing_port.core.services.game_mechanics_service.random.uniform', lambda *_: 20.0)
            if failure in ('update', 'log'):
                def fail(*args):
                    raise RuntimeError('后续写入失败')
                target, method = ((p.engine.user_repo, 'update') if failure == 'update'
                                  else (p.engine.log_repo, 'add_wipe_bomb_log'))
                patch.setattr(target, method, fail)
            elif failure in ('rollback', 'cancel'):
                original = p.engine.log_repo.add_wipe_bomb_log

                def fail(*args):
                    original(*args)
                    if failure == 'cancel':
                        raise asyncio.CancelledError()
                    connection(p.db_path).rollback()
                patch.setattr(p.engine.log_repo, 'add_wipe_bomb_log', fail)
            if failure == 'commit':
                with broken_commit(patch):
                    await command(p, '擦弹 100', mid='wipe')
            elif failure == 'cancel':
                with pytest.raises(asyncio.CancelledError):
                    await command(p, '擦弹 100', mid='wipe')
            else:
                await command(p, '擦弹 100', mid='wipe')
        mechanics = p.engine.game_mechanics_service
        assert not mechanics._server_suppressed
        assert mechanics._last_suppression_date is None
        if failure == 'cancel':
            assert not p.ctx.send.messages
        else:
            assert '事务已回滚' in p.ctx.send.messages[-1][1]
        with atomic(p.db_path) as db:
            user = p.engine.user_repo.get_by_id('12345')
            assert user.coins == 200
            assert user.wipe_bomb_attempts_today == 0
            assert not db.execute('SELECT 1 FROM port_receipts WHERE key LIKE ?', ('%:wipe',)).fetchone()
        await command(p, '注册', uid='23456')
        await p.on_unload()
        p = await load_plugin(tmp_path)
        assert not p.engine.game_mechanics_service._server_suppressed
        assert p.engine.game_mechanics_service._last_suppression_date is None
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 200
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_successful_suppression_is_durable_and_deduplicated(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        monkeypatch.setattr('fishing_port.core.services.game_mechanics_service.random.uniform', lambda *_: 20.0)
        await command(p, '擦弹 100', mid='once')
        assert p.engine.game_mechanics_service._server_suppressed
        assert (await command(p, '擦弹 100', mid='once'))[1] == '重复消息已忽略'
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 2100
        await p.on_unload()
        p = await load_plugin(tmp_path)
        assert p.engine.game_mechanics_service._server_suppressed
        assert p.engine.game_mechanics_service._last_suppression_date is not None
    finally:
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_cancellation_and_unload_wait_for_final_worker_result(tmp_path, monkeypatch, failure):
    p = await load_plugin(tmp_path)
    loop = asyncio.get_running_loop()
    started, release = asyncio.Event(), threading.Event()
    original = p.engine.register_user

    async def delayed(event):
        async for reply in original(event):
            yield reply
        p.engine.game_mechanics_service._server_suppressed = True
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        if failure:
            connection(p.db_path).rollback()

    monkeypatch.setattr(p.engine, 'register_user', delayed)
    task = asyncio.create_task(command(p, '注册', mid='cancelled'))
    unloading = None
    try:
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        unloading = asyncio.create_task(p.on_unload())
        await asyncio.sleep(0)
        unloading.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not unloading.done()
        assert p.lock.locked() and p.process_lock is not None
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(asyncio.CancelledError):
            await unloading
        assert not p.ctx.send.messages
        assert p.process_lock is None and p.worker is None
        p = await load_plugin(tmp_path)
        assert p.engine.game_mechanics_service._server_suppressed is (not failure)
        with atomic(p.db_path):
            assert (p.engine.user_repo.get_by_id('12345') is None) is failure
        if not failure:
            assert (await command(p, '注册', mid='cancelled'))[1] == '重复消息已忽略'
    finally:
        release.set()
        await asyncio.gather(task, *([unloading] if unloading else []), return_exceptions=True)
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['rollback', 'commit', 'raise', 'cancel'])
async def test_tick_failure_restores_bets_and_discards_notifications(tmp_path, monkeypatch, failure):
    from datetime import timedelta
    from fishing_port.core.utils import get_now

    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        p.engine.sicbo_service.set_message_mode('text')
        await command(p, '开庄')
        await command(p, '鸭大 100')
        sid, game = next(iter(p.engine.sicbo_service.games.items()))
        game.end_time = get_now() - timedelta(seconds=1)
        p.ctx.send.messages.clear()
        with monkeypatch.context() as patch:
            patch.setattr('fishing_port.core.services.sicbo_service.random.randint', lambda *_: 6)
            patch.setattr(p, 'auto_tick', lambda: [('qq-group-1', '不应发送的自动钓鱼通知')])
            original = p.save_games

            def fail():
                original()
                p.engine.game_mechanics_service._server_suppressed = True
                if failure == 'rollback':
                    connection(p.db_path).rollback()
                elif failure == 'raise':
                    raise RuntimeError('tick写入失败')
                elif failure == 'cancel':
                    raise asyncio.CancelledError()
            if failure == 'commit':
                with broken_commit(patch), pytest.raises(sqlite3.OperationalError):
                    await p.tick(40)
            else:
                patch.setattr(p, 'save_games', fail)
                with pytest.raises(asyncio.CancelledError if failure == 'cancel' else RuntimeError):
                    await p.tick(40)
        assert not p.ctx.send.messages
        assert p.engine.sicbo_service.games[sid].is_active
        assert not p.engine.sicbo_service.games[sid].is_settled
        assert len(p.engine.sicbo_service.games[sid].bets) == 1
        assert not p.engine.game_mechanics_service._server_suppressed
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 100
        await p.tick(1)
        assert p.engine.sicbo_service.games[sid].is_settled
        assert p.ctx.send.messages
    finally:
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['commit', 'rollback', 'handler'])
async def test_forced_settlement_callback_never_sends_before_commit(tmp_path, monkeypatch, failure):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        p.settings['admin_ids'] = ['12345']
        p.engine.sicbo_service.set_message_mode('text')
        await command(p, '开庄')
        await command(p, '鸭大 100')
        sid = next(iter(p.engine.sicbo_service.games))
        p.ctx.send.messages.clear()
        with monkeypatch.context() as patch:
            if failure == 'commit':
                with broken_commit(patch):
                    await command(p, '骰宝结算', mid='settle')
            else:
                original = p.engine.sicbo_service._settle_game

                async def fail(*args):
                    result = await original(*args)
                    if failure == 'handler':
                        raise RuntimeError('结算协程异常，处理器会捕获')
                    connection(p.db_path).rollback()
                    return result
                # Wrap the actual service method so nested async failure handling
                # is exercised, including the handler's broad exception catch.
                if failure == 'handler':
                    def raising(*args):
                        raise RuntimeError('结算协程异常，处理器会捕获')
                    patch.setattr(p.engine.sicbo_service, '_analyze_dice_result', raising)
                else:
                    patch.setattr(p.engine.sicbo_service, '_settle_game', fail)
                await command(p, '骰宝结算', mid='settle')
        assert len(p.ctx.send.messages) == 1
        assert '事务已回滚' in p.ctx.send.messages[0][1]
        assert p.engine.sicbo_service.games[sid].is_active
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 100
        await command(p, '骰宝结算', mid='settle')
        assert p.engine.sicbo_service.games[sid].is_settled
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_send_failure_keeps_commit_and_receipt(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    host_thread = threading.get_ident()
    try:
        async def fail_send(*args):
            assert threading.get_ident() == host_thread
            # A new writer can acquire the database: network send is outside UoW.
            with sqlite3.connect(p.db_path, timeout=0) as db:
                db.execute('BEGIN IMMEDIATE')
                assert db.execute('SELECT 1 FROM users WHERE user_id=?', ('12345',)).fetchone()
            raise OSError('SDK发送失败')

        with monkeypatch.context() as patch:
            patch.setattr(p.ctx.send, 'text', fail_send)
            with pytest.raises(OSError):
                await command(p, '注册', mid='sent-failed')
        assert (await command(p, '注册', mid='sent-failed'))[1] == '重复消息已忽略'
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 200
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_web_failed_commit_restores_state_and_never_returns_success(tmp_path, monkeypatch):
    from fishing_port.web import build_app
    p = await load_plugin(tmp_path)
    try:
        p.settings['webui']['secret_key'] = 'test-password'
        app = build_app(p)
        client = app.test_client()
        async with client.session_transaction() as session:
            session.update(logged_in=True, is_admin=True, csrf='token')
        with broken_commit(monkeypatch):
            response = await client.post('/admin/users/create', json={'user_id': '12345', 'coins': 200},
                                         headers={'X-CSRF-Token': 'token'})
        assert response.status_code == 500
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345') is None
        assert not p.lock.locked()
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_web_controls_stay_on_host_loop_and_rollback_discards_them(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    loop = asyncio.get_running_loop()
    calls = []

    async def start():
        assert asyncio.get_running_loop() is loop
        calls.append('start')
        return '测试后台已启动'

    try:
        p.settings['admin_ids'] = ['12345']
        monkeypatch.setattr(p, 'start_web', start)
        await command(p, '开启钓鱼后台管理')
        assert calls == ['start']
        assert p.ctx.send.messages[-1][1] == '测试后台已启动'
        with broken_commit(monkeypatch):
            await command(p, '开启钓鱼后台管理')
        assert calls == ['start']
        assert '事务已回滚' in p.ctx.send.messages[-1][1]
    finally:
        await p.on_unload()

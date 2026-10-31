import asyncio
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from maibot_sdk.context import PluginContext, PluginPaths

from fishing_port import create_plugin
from fishing_port.config import FishingConfig
from fishing_port.core.database.unit_of_work import atomic
from fishing_port.events import GameEvent


class Sender:
    def __init__(self):
        self.messages = []

    async def text(self, text, stream_id):
        self.messages.append(('text', text, stream_id))
        return True

    async def image(self, image, stream_id):
        self.messages.append(('image', image, stream_id))
        return True

    async def custom(self, kind, data, stream_id):
        self.messages.append((kind, data, stream_id))
        return True


async def load_plugin(tmp_path):
    plugin = create_plugin()
    context = PluginContext('fishing.astrbot-native', paths=PluginPaths(tmp_path, tmp_path / 'runtime'))
    context.send = Sender()
    plugin._set_context(context)
    plugin._plugin_config_instance = FishingConfig()
    await plugin.on_load()
    # Deterministic tests drive ticks manually, without wall-clock background activity.
    for task in plugin.tasks:
        task.cancel()
    await asyncio.gather(*plugin.tasks, return_exceptions=True)
    plugin.tasks.clear()
    return plugin


async def command(plugin, text, uid='12345', mid='', raw=None):
    message = {'message_id': mid, 'account_id': '99999',
               'message_info': {'user_info': {'user_id': uid, 'user_nickname': '测试玩家'}},
               'raw_message': raw or [{'type': 'text', 'data': text}]}
    return await plugin.handle_game(text=text, stream_id='qq-group-1', user_id=uid, group_id='123', platform='qq', message=message)


@pytest.mark.asyncio
async def test_load_register_restart(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        assert (await command(p, '注册', mid='1'))[2] is True
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345') is not None
        assert (await command(p, '注册', mid='1'))[1] == '重复消息已忽略'
        with sqlite3.connect(p.db_path) as db:
            assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 42
    finally:
        await p.on_unload()
    p = await load_plugin(tmp_path)
    try:
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345') is not None
    finally:
        await p.on_unload()


def test_identity_and_mentions():
    event = GameEvent(text='转账 @冒充者 10', user_id='123', stream_id='s', message={
        'account_id': '999', 'raw_message': [{'type': 'text', 'data': '/转账 '},
        {'type': 'at', 'data': {'target_user_id': '456'}}, {'type': 'text', 'data': ' 10'}]})
    assert event.message_str == '转账 456 10'
    assert event.message_obj.message[0].qq == '456'
    with pytest.raises(ValueError):
        GameEvent(text='注册', user_id='伪造', stream_id='s')
    with pytest.raises(ValueError):
        GameEvent(text='注册', user_id='123', stream_id='s', message={'message_info': {'user_info': {'user_id': '456'}}})


@pytest.mark.asyncio
async def test_admin_and_transfer(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        await command(p, '注册', uid='23456')
        await command(p, '转账 23456 10', mid='transfer')
        with atomic(p.db_path):
            a = p.engine.user_repo.get_by_id('12345').coins
            b = p.engine.user_repo.get_by_id('23456').coins
        await command(p, '转账 23456 10', mid='transfer')
        await command(p, '修改金币 12345 999999')
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == a
            assert p.engine.user_repo.get_by_id('23456').coins == b
        assert '管理员' in p.ctx.send.messages[-1][1]
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_transaction_rollback(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            initial = p.engine.user_repo.get_by_id('12345').coins
        with pytest.raises(RuntimeError):
            with atomic(p.db_path):
                user = p.engine.user_repo.get_by_id('12345')
                user.coins += 1000
                p.engine.user_repo.update(user)
                raise RuntimeError('模拟中断：原仓储 commit 不得提交外层')
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == initial
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_tick_and_shutdown(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        await p.tick(0)
    finally:
        await p.on_unload()
    assert not p.tasks and p.engine is None


@pytest.mark.asyncio
async def test_unload_after_web_server_failure(tmp_path, monkeypatch):
    import hypercorn.asyncio

    async def fail_to_serve(*args, **kwargs):
        raise OSError('模拟后台端口绑定失败')

    monkeypatch.setattr(hypercorn.asyncio, 'serve', fail_to_serve)
    p = await load_plugin(tmp_path)
    try:
        p.settings['webui'].update(enabled=True, secret_key='x' * 32)
        await p.start_web()
        await asyncio.gather(p.web_task, return_exceptions=True)
        await p.on_unload()
        assert p.web_task is None
        assert p.engine is None
        assert p.process_lock is None
        assert not p.tasks
        # A failed optional server must not prevent reloading the game data directory.
        replacement = await load_plugin(tmp_path)
        await replacement.on_unload()
    finally:
        p.web_task = None
        await p.on_unload()


def test_sdk_contract_and_no_framework_runtime():
    import ast
    p = create_plugin()
    components = p.get_components()
    assert len(components) == 1
    assert components[0]['type'] == 'COMMAND'
    assert p.build_config_schema()['sections']
    root = Path(__file__).parents[1]
    paths = [root / name for name in ('plugin.py', 'engine.py', 'events.py', 'utils.py')]
    for directory in ('core', 'handlers', 'draw', 'manager'):
        paths += list((root / directory).rglob('*.py'))
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or '').startswith(('astrbot', 'nonebot')), path
            elif isinstance(node, ast.Import):
                assert not any(alias.name.startswith(('astrbot', 'nonebot')) for alias in node.names), path

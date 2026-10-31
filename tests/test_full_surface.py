"""Offline command surface and media/security integration, using real game services."""
import asyncio
from datetime import timedelta
import re
import sqlite3

import pytest

from fishing_port import COMMANDS, SUPPLEMENTAL
from fishing_port.core.database.unit_of_work import atomic
from fishing_port.core.utils import get_now
from fishing_port import media
from fishing_port.web import build_app
from test_native_adapter import command, load_plugin


@pytest.fixture(autouse=True)
def offline_media(monkeypatch):
    async def fail(url):
        raise OSError('离线测试不允许图片网络请求')
    monkeypatch.setattr(media, 'download', fail)


@pytest.mark.asyncio
async def test_all_124_commands_smoke(tmp_path, caplog):
    p = await load_plugin(tmp_path)
    failures = []
    try:
        await command(p, '注册')
        p.settings['admin_ids'] = ['12345']
        for route in COMMANDS + SUPPLEMENTAL:
            before = len(p.ctx.send.messages)
            await command(p, route['aliases'][0])
            new = p.ctx.send.messages[before:]
            if any('数据库事务已回滚' in item[1] for item in new if item[0] == 'text'):
                failures.append(route['method'])
        assert not failures, f'命令异常: {failures}'
        errors = [r.getMessage() for r in caplog.records if r.levelname == 'ERROR']
        assert not errors, errors
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_web_every_route_denies_anonymous(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        p.settings['webui']['secret_key'] = 'x' * 48
        app = build_app(p)
        client = app.test_client()
        checked = 0
        for rule in app.url_map.iter_rules():
            if rule.endpoint == 'admin_bp.login':
                continue
            path = re.sub(r'<(?:\w+:)?\w+>', '1', str(rule))
            method = 'GET' if 'GET' in rule.methods else 'POST'
            response = await client.open(path, method=method)
            assert response.status_code == 401, (path, response.status_code)
            checked += 1
        assert checked > 50
        login = await client.get('/admin/login')
        token = re.search(r'name="csrf_token" value="([^"]+)"', await login.get_data(as_text=True))[1]
        assert (await client.post('/admin/login', form={'secret_key': 'x'*48})).status_code == 403
        response = await client.post('/admin/login', form={'secret_key': 'x'*48, 'csrf_token': token})
        assert response.status_code == 302
        assert (await client.get('/admin/')).status_code == 200
        assert (await client.post('/admin/fish/add', form={'name': '伪造请求'})).status_code == 403
        # Authenticated real routes exercise injected services and shared transactions.
        for page in ('fish', 'users', 'rods', 'baits', 'accessories', 'items', 'shops', 'gacha', 'zones', 'titles', 'market', 'exchange'):
            response = await client.get('/admin/' + page)
            assert response.status_code == 200, (page, await response.get_data(as_text=True))
        await client.get('/admin/logout')
        assert (await client.get('/admin/')).status_code == 401
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_media_cache_and_placeholder(tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    calls = []
    async def download(url):
        calls.append(url)
        buffer = BytesIO()
        Image.new('RGB', (100, 80), 'red').save(buffer, 'PNG')
        return buffer.getvalue()
    monkeypatch.setattr(media, 'download', download)
    first = await media.get_fish_icon('https://example.org/fish.png', str(tmp_path))
    second = await media.get_fish_icon('https://example.org/fish.png', str(tmp_path))
    assert first.size == second.size == (60, 48)
    assert len(calls) == 1
    media.cleanup_cache(tmp_path, now=10**12)
    assert not list((tmp_path / 'fish_icon_cache').iterdir())
    assert (await media.get_fish_icon('', str(tmp_path))).size == (60, 60)
    with pytest.raises(ValueError):
        await _reject_private()


async def _reject_private():
    # Call the real downloader, not the offline fixture.
    from importlib import util
    spec = util.spec_from_file_location('fishing_port.media_real', media.__file__)
    real = util.module_from_spec(spec)
    spec.loader.exec_module(real)
    await real.download('http://127.0.0.1/private')


@pytest.mark.asyncio
async def test_sicbo_restart_settles_once(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            user.coins = 10000
            p.engine.user_repo.update(user)
        await command(p, '开庄')
        await command(p, '鸭大 100', mid='bet1')
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 9900
        assert p.engine.sicbo_service.games
    finally:
        await p.on_unload()
    p = await load_plugin(tmp_path)
    try:
        assert p.engine.sicbo_service.games
        for game in p.engine.sicbo_service.games.values():
            assert len(game.bets) == 1
            game.end_time = get_now() - timedelta(seconds=1)
        await p.tick(1)
        with atomic(p.db_path):
            coins = p.engine.user_repo.get_by_id('12345').coins
        await p.tick(2)
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == coins
        with sqlite3.connect(p.db_path) as db:
            assert db.execute("SELECT value FROM port_state WHERE key='sicbo'").fetchone()[0] == '{}'
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_real_status_image_send_failure_has_data(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        async def failed_send(*args):
            return False
        p.ctx.send.image = failed_send
        await command(p, '状态')
        texts = [item[1] for item in p.ctx.send.messages if item[0] == 'text']
        assert any('文字快照' in text for text in texts)
        assert any('200' in text for text in texts)
    finally:
        await p.on_unload()

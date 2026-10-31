import asyncio
import re

import pytest

from fishing_port.config import FishingConfig
from fishing_port.lan import is_local_address
from fishing_port.web import build_app
from test_native_adapter import load_plugin


def test_lan_is_opt_in_and_default_remains_local():
    values = FishingConfig().checked()['webui']
    assert values['enabled'] is False
    assert values['listen_host'] == '127.0.0.1'
    assert values['allowed_hosts'] == ['localhost', '127.0.0.1', '[::1]']


@pytest.mark.parametrize('host', ['*', '.example.com', 'http://192.168.1.10', '192.168.1.10:7777',
                                '0.0.0.0', '8.8.8.8', '[::]', 'localhost/admin'])
def test_dangerous_host_allowlist_rejected(host):
    with pytest.raises(ValueError):
        FishingConfig(webui={'allowed_hosts': [host]}).checked()


@pytest.mark.parametrize(('address', 'expected'), [
    ('127.0.0.1', True), ('192.168.1.2', True), ('10.1.2.3', True), ('172.17.0.1', True),
    ('::1', True), ('fd00::1', True), ('::ffff:192.168.1.2', True),
    ('8.8.8.8', False), ('172.32.0.1', False), ('100.64.0.1', False), ('169.254.1.1', False),
    ('0.0.0.0', False), ('2001:4860:4860::8888', False), ('bad', False),
])
def test_only_explicit_local_networks_are_allowed(address, expected):
    assert is_local_address(address) is expected


@pytest.mark.asyncio
async def test_lan_host_auth_csrf_and_public_peer_rejection(tmp_path):
    plugin = await load_plugin(tmp_path)
    try:
        plugin.settings['webui'].update(secret_key='x' * 48, listen_host='0.0.0.0',
                                        allowed_hosts=['localhost', '127.0.0.1', '192.168.1.10'])
        app = build_app(plugin)
        client = app.test_client()
        kwargs = {'headers': {'Host': '192.168.1.10:7777'},
                  'scope_base': {'client': ('192.168.1.20', 12345)}}
        response = await client.get('/admin/login', **kwargs)
        assert response.status_code == 200
        token = re.search(r'name="csrf_token" value="([^"]+)"', await response.get_data(as_text=True))[1]
        assert (await client.get('/admin/', **kwargs)).status_code == 401
        assert (await client.post('/admin/login', form={'secret_key': 'x' * 48}, **kwargs)).status_code == 403
        assert (await client.post('/admin/login', form={'secret_key': 'x' * 48, 'csrf_token': token}, **kwargs)).status_code == 302
        assert (await client.get('/admin/', **kwargs)).status_code == 200
        assert (await client.get('/admin/login', headers={'Host': 'attacker.example'},
                                 scope_base={'client': ('192.168.1.20', 12345)})).status_code == 400
        assert (await client.get('/admin/login', headers={'Host': '192.168.1.10:7777',
                                                         'X-Forwarded-For': '192.168.1.20'},
                                 scope_base={'client': ('8.8.8.8', 12345)})).status_code == 403
    finally:
        await plugin.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('host', ['127.0.0.1', '0.0.0.0'])
async def test_actual_listener_uses_configured_interface(tmp_path, monkeypatch, host):
    plugin = await load_plugin(tmp_path)
    captured = []

    async def serve(app, config):
        captured.extend(config.bind)

    monkeypatch.setattr('hypercorn.asyncio.serve', serve)
    try:
        plugin.settings['webui'].update(enabled=True, secret_key='x' * 48, listen_host=host)
        await plugin.start_web()
        await asyncio.sleep(0)
        assert captured == [f'{host}:7777']
    finally:
        await plugin.on_unload()

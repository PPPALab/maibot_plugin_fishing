"""普通密码、旧密钥兼容，以及原有认证限制。"""
import re

import pytest

from fishing_port.config import FishingConfig
from fishing_port.web import build_app
from test_native_adapter import load_plugin


@pytest.mark.parametrize('password', ['', '   ', '\t\n'])
def test_enabled_admin_rejects_empty_password(password):
    with pytest.raises(ValueError, match='登录密码'):
        FishingConfig(webui={'enabled': True, 'secret_key': password}).checked()


@pytest.mark.asyncio
@pytest.mark.parametrize('password', ['ordinary-pass', '钓鱼密码', 'x' * 48])
async def test_password_login_keeps_csrf_rate_limit_and_random_session_key(tmp_path, password):
    config = FishingConfig(webui={'enabled': True, 'secret_key': password})
    assert config.checked()['webui']['secret_key'] == password
    p = await load_plugin(tmp_path)
    try:
        p.settings['webui']['secret_key'] = password
        app = build_app(p)
        assert app.secret_key != password
        assert app.secret_key != build_app(p).secret_key
        client = app.test_client()
        assert (await client.get('/admin/')).status_code == 401
        response = await client.get('/admin/login')
        assert response.status_code == 200
        token = re.search(r'name="csrf_token" value="([^"]+)"', await response.get_data(as_text=True))[1]
        assert (await client.post('/admin/login', form={'secret_key': password})).status_code == 403
        # 错误密码不得放行；中文比较不得抛异常。
        response = await client.post('/admin/login', form={'secret_key': '错误密码', 'csrf_token': token})
        assert response.status_code == 200
        assert (await client.get('/admin/')).status_code == 401
        response = await client.post('/admin/login', form={'secret_key': password, 'csrf_token': token})
        assert response.status_code == 302
        assert (await client.get('/admin/')).status_code == 200
        for _ in range(2):
            assert (await client.post('/admin/login', form={'secret_key': 'wrong', 'csrf_token': token})).status_code == 200
        assert (await client.post('/admin/login', form={'secret_key': password, 'csrf_token': token})).status_code == 429
    finally:
        await p.on_unload()

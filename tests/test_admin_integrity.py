"""后台失败请求不能留下用户、装备或被方向颠倒的库存变更。"""
import pytest

from fishing_port.core.database.unit_of_work import atomic
from fishing_port.web import build_app
from test_native_adapter import command, load_plugin


@pytest.mark.asyncio
async def test_invalid_admin_creation_leaves_no_user(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        result = p.engine.user_service.create_user_for_admin({'user_id': '12345', 'coins': -10})
        assert not result['success']
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345') is None
    finally:
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['add_item_to_user_inventory', 'remove_item_from_user_inventory'])
@pytest.mark.parametrize('quantity', [-1, 0, True, 1.5])
async def test_admin_inventory_requires_positive_integer(tmp_path, operation, quantity):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            p.engine.inventory_repo.update_bait_quantity('12345', 1, 3)
        result = getattr(p.engine.user_service, operation)('12345', 'bait', 1, quantity)
        assert not result['success']
        with atomic(p.db_path):
            assert p.engine.inventory_repo.get_user_bait_inventory('12345')[1] == 3
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_admin_equipment_failure_rolls_back_previous_items(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        repo = p.engine.inventory_repo
        with atomic(p.db_path):
            before = len(repo.get_user_rod_instances('12345'))
        original = repo.add_rod_instance
        calls = 0

        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('模拟第二件发放失败')
            return original(*args)

        monkeypatch.setattr(repo, 'add_rod_instance', fail_second)
        result = p.engine.user_service.add_item_to_user_inventory('12345', 'rod', 1, 2)
        assert not result['success']
        with atomic(p.db_path):
            assert len(repo.get_user_rod_instances('12345')) == before
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_http_admin_failure_and_success_are_atomic(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        p.settings['webui']['secret_key'] = 'x' * 48
        client = build_app(p).test_client()
        async with client.session_transaction() as session:
            session.update(logged_in=True, is_admin=True, csrf='test-token')
        headers = {'X-CSRF-Token': 'test-token'}
        response = await client.post('/admin/users/create', json={
            'user_id': '12345', 'coins': -10,
        }, headers=headers)
        assert not (await response.get_json())['success']
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345') is None
        response = await client.post('/admin/users/create', json={
            'user_id': '12345', 'nickname': '测试', 'coins': 200,
        }, headers=headers)
        assert (await response.get_json())['success']
        for operation, quantity, expected_success in [('add', 3, True), ('remove', -1, False), ('remove', 1, True)]:
            response = await client.post(f'/admin/users/12345/inventory/{operation}', json={
                'item_type': 'bait', 'item_id': 1, 'quantity': quantity,
            }, headers=headers)
            assert (await response.get_json())['success'] is expected_success
        with atomic(p.db_path):
            assert p.engine.inventory_repo.get_user_bait_inventory('12345')[1] == 2
        assert not p.lock.locked()
    finally:
        await p.on_unload()

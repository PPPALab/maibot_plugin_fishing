from datetime import timedelta, timezone

import pytest

from fishing_port.core.database.unit_of_work import atomic
from fishing_port.core.utils import get_now
from test_native_adapter import command, load_plugin


@pytest.mark.asyncio
@pytest.mark.parametrize('deadline,allowed', [
    ('offset', False), ('invalid', False), ('missing', False), ('boundary', False),
    ('future_offset', True), ('future_local', True), ('future_z', True),
])
async def test_limited_pool_deadline_controls_charging(tmp_path, monkeypatch, deadline, allowed):
    import fishing_port.core.services.gacha_service as gacha

    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        now = get_now().replace(microsecond=0)
        monkeypatch.setattr(gacha, 'get_now', lambda: now)
        value = {
            'offset': (now - timedelta(hours=1)).astimezone(timezone.utc).isoformat(),
            'invalid': 'not-a-date',
            'missing': None,
            'boundary': now.replace(microsecond=0).isoformat(),
            'future_offset': (now + timedelta(hours=1)).astimezone(timezone.utc).isoformat(),
            'future_local': (now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
            'future_z': (now + timedelta(hours=1)).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
        }[deadline]
        with atomic(p.db_path):
            p.engine.gacha_repo.add_pool_template({
                'name': '截止时间测试', 'cost_coins': 10,
                'is_limited_time': True, 'open_until': value,
            })
            pool = next(x for x in p.engine.gacha_repo.get_all_pools() if x.name == '截止时间测试')
            p.engine.gacha_repo.add_pool_item(pool.gacha_pool_id, {
                'item_type': 'bait', 'item_id': 1, 'quantity': 1, 'weight': 1,
            })
        result = p.engine.gacha_service.perform_draw('12345', pool.gacha_pool_id)
        assert result['success'] is allowed, result
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == (190 if allowed else 200)
            assert p.engine.inventory_repo.get_user_bait_inventory('12345').get(1, 0) == int(allowed)
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_multi_ten_late_reward_failure_rolls_back_all(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            p.engine.gacha_repo.add_pool_template({'name': '发奖回滚测试', 'cost_coins': 10})
            pool = next(x for x in p.engine.gacha_repo.get_all_pools() if x.name == '发奖回滚测试')
            p.engine.gacha_repo.add_pool_item(pool.gacha_pool_id, {
                'item_type': 'bait', 'item_id': 1, 'quantity': 1, 'weight': 1,
            })
        original = p.engine.gacha_service._grant_reward
        count = 0

        def fail_after_first_ten(*args):
            nonlocal count
            count += 1
            if count == 11:
                raise RuntimeError('第十一份奖励失败')
            return original(*args)

        monkeypatch.setattr(p.engine.gacha_service, '_grant_reward', fail_after_first_ten)
        await command(p, f'十连 {pool.gacha_pool_id} 2')
        assert count == 11
        assert '回滚' in p.ctx.send.messages[-1][1]
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 200
            assert not p.engine.inventory_repo.get_user_bait_inventory('12345')
            assert not p.engine.log_repo.get_gacha_records('12345', 100)
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_multi_ten_insufficient_total_budget_is_atomic(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            user.coins = 150
            p.engine.user_repo.update(user)
            p.engine.gacha_repo.add_pool_template({'name': '批量费用测试', 'cost_coins': 10})
            pool = next(x for x in p.engine.gacha_repo.get_all_pools() if x.name == '批量费用测试')
            p.engine.gacha_repo.add_pool_item(pool.gacha_pool_id, {
                'item_type': 'bait', 'item_id': 1, 'quantity': 1, 'weight': 1,
            })
        await command(p, f'十连 {pool.gacha_pool_id} 2')
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 150
            assert not p.engine.inventory_repo.get_user_bait_inventory('12345')
            assert not p.engine.log_repo.get_gacha_records('12345', 100)
    finally:
        await p.on_unload()

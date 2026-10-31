"""审查发现的品质、限时鱼饵、装备发放及跨日配额回归场景。"""
from datetime import timedelta

import pytest

from fishing_port.core.database.unit_of_work import atomic
from fishing_port.core.utils import get_now
from test_native_adapter import command, load_plugin


@pytest.mark.asyncio
@pytest.mark.parametrize('ordinary_count', [0, 1])
async def test_full_pond_removes_selected_quality(tmp_path, monkeypatch, ordinary_count):
    import fishing_port.core.services.fishing_service as fishing

    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            user.fish_pond_capacity = ordinary_count + 1
            p.engine.user_repo.update(user)
            p.engine.inventory_repo.add_fish_to_inventory('12345', 1, 1, quality_level=1)
            if ordinary_count:
                p.engine.inventory_repo.add_fish_to_inventory('12345', 1, ordinary_count, quality_level=0)
            caught = p.engine.item_template_repo.get_fish_by_id(2)
        monkeypatch.setattr(fishing.random, 'random', lambda: 0.0)
        monkeypatch.setattr(fishing.random, 'choice', lambda stacks: next(s for s in stacks if s.quality_level == 1))
        monkeypatch.setattr(p.engine.fishing_service, '_get_fish_template', lambda *args: caught)
        result = p.engine.fishing_service.go_fish('12345')
        assert result['success'], result
        with atomic(p.db_path):
            inventory = p.engine.inventory_repo.get_fish_inventory('12345')
            assert sum(s.quantity for s in inventory) == ordinary_count + 1
            assert sum(s.quantity for s in inventory if s.fish_id == 1 and s.quality_level == 0) == ordinary_count
            assert not any(s.fish_id == 1 and s.quality_level == 1 for s in inventory)
    finally:
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_active', [False, True])
async def test_timed_bait_expires_after_automatic_activation(tmp_path, monkeypatch, legacy_active):
    import fishing_port.core.services.fishing_service as fishing

    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        now = get_now()
        monkeypatch.setattr(fishing, 'get_now', lambda: now)
        # 空竿也应消耗使用时间，避免测试依赖随机鱼种及绘图。
        monkeypatch.setattr(fishing.random, 'random', lambda: 1.0)
        with atomic(p.db_path):
            bait = p.engine.item_template_repo.get_bait_by_id(9)
            assert bait.duration_minutes > 0
            p.engine.inventory_repo.update_bait_quantity('12345', 9, 1)
            if legacy_active:
                user = p.engine.user_repo.get_by_id('12345')
                user.current_bait_id = 9
                user.bait_start_time = None
                p.engine.user_repo.update(user)
        p.engine.fishing_service.go_fish('12345')
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            assert user.current_bait_id == 9
            assert user.bait_start_time == now
        now += timedelta(minutes=bait.duration_minutes - 1)
        p.engine.fishing_service.go_fish('12345')
        with atomic(p.db_path):
            assert p.engine.inventory_repo.get_user_bait_inventory('12345')[9] == 1
            assert p.engine.user_repo.get_by_id('12345').bait_start_time == now - timedelta(minutes=bait.duration_minutes - 1)
        now += timedelta(minutes=2)
        p.engine.fishing_service.go_fish('12345')
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            assert user.current_bait_id is None
            assert user.bait_start_time is None
            assert p.engine.inventory_repo.get_user_bait_inventory('12345').get(9, 0) == 0
    finally:
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('reward_type', ['rod', 'accessory'])
async def test_shop_multiple_equipment_rewards(tmp_path, monkeypatch, reward_type):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        repo = p.engine.inventory_repo
        getter = repo.get_user_rod_instances if reward_type == 'rod' else repo.get_user_accessory_instances
        add_name = 'add_rod_instance' if reward_type == 'rod' else 'add_accessory_instance'
        with atomic(p.db_path):
            before = len(getter('12345'))
            shop = p.engine.shop_repo.create_shop({'name': '多件装备商店'})
            item = p.engine.shop_repo.create_shop_item(shop['shop_id'], {'name': '装备礼包', 'stock_total': 10})
            iid = item['item_id']
            p.engine.shop_repo.add_item_cost(iid, {'cost_type': 'coins', 'cost_amount': 10})
            p.engine.shop_repo.add_item_reward(iid, {
                'reward_type': reward_type, 'reward_item_id': 1,
                'reward_quantity': 3, 'reward_refine_level': 2,
            })
        # 第二件发放失败时，第一件、扣款和销售计数也必须回滚。
        original_add = getattr(repo, add_name)
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('第二件装备发放失败')
            return original_add(*args, **kwargs)

        monkeypatch.setattr(repo, add_name, fail_second)
        with pytest.raises(RuntimeError, match='第二件'):
            p.engine.shop_service.purchase_item('12345', iid, quantity=2)
        with atomic(p.db_path):
            assert len(getter('12345')) == before
            assert p.engine.user_repo.get_by_id('12345').coins == 200
            assert p.engine.shop_repo.get_shop_item_by_id(iid)['stock_sold'] == 0
        monkeypatch.setattr(repo, add_name, original_add)
        result = p.engine.shop_service.purchase_item('12345', iid, quantity=2)
        assert result['success'], result
        assert 'x6' in result['message']
        with atomic(p.db_path):
            equipment = getter('12345')
            assert len(equipment) == before + 6
            assert sum(e.refine_level == 2 for e in equipment) == 6
            assert p.engine.user_repo.get_by_id('12345').coins == 180
            assert p.engine.shop_repo.get_shop_item_by_id(iid)['stock_sold'] == 2
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_daily_quota_restart_and_transaction_retry(tmp_path, monkeypatch):
    import fishing_port.core.services.fishing_service as fishing

    reset_time = get_now().replace(hour=0, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(fishing, 'get_last_reset_time', lambda hour: reset_time)
    p = await load_plugin(tmp_path)
    try:
        # 首次升级无刷新标记，补刷新一次，之后同日重启不清零。
        assert p.engine.fishing_service._reset_rare_fish_daily_quota()
        with atomic(p.db_path):
            zone = p.engine.inventory_repo.get_zone_by_id(2)
            zone.rare_fish_caught_today = zone.daily_rare_fish_quota
            p.engine.inventory_repo.update_fishing_zone(zone)
    finally:
        await p.on_unload()

    p = await load_plugin(tmp_path)
    try:
        assert not p.engine.fishing_service._reset_rare_fish_daily_quota()
        with atomic(p.db_path):
            assert p.engine.inventory_repo.get_zone_by_id(2).rare_fish_caught_today == zone.daily_rare_fish_quota
    finally:
        await p.on_unload()

    reset_time += timedelta(days=1)
    p = await load_plugin(tmp_path)
    try:
        # 模拟刷新之后同一业务事务失败，重试必须仍能补刷新。
        with pytest.raises(RuntimeError, match='回滚'):
            with atomic(p.db_path):
                assert p.engine.fishing_service._reset_rare_fish_daily_quota()
                raise RuntimeError('回滚')
        with atomic(p.db_path):
            assert p.engine.inventory_repo.get_zone_by_id(2).rare_fish_caught_today == zone.daily_rare_fish_quota
        assert p.engine.fishing_service._reset_rare_fish_daily_quota()
        assert not p.engine.fishing_service._reset_rare_fish_daily_quota()
        with atomic(p.db_path):
            assert p.engine.inventory_repo.get_zone_by_id(2).rare_fish_caught_today == 0
    finally:
        await p.on_unload()

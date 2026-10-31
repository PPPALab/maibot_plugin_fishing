from datetime import timedelta
import importlib

import pytest

from fishing_port.core.database.unit_of_work import atomic
from fishing_port.core.utils import get_now
from test_native_adapter import command, load_plugin


@pytest.mark.asyncio
@pytest.mark.parametrize('bait_id,remaining', [(1, 0), (5, 1), (12, 1)])
async def test_bait_consumption_respects_template(tmp_path, monkeypatch, bait_id, remaining):
    import fishing_port.core.services.fishing_service as fishing

    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            p.engine.inventory_repo.update_bait_quantity('12345', bait_id, 1)
        monkeypatch.setattr(fishing.random, 'random', lambda: 1.0)
        # 自动选择的第一次使用也必须扣一次性饵；无消耗拟饵重复使用不扣除。
        for _ in range(2):
            p.engine.fishing_service.go_fish('12345')
            with atomic(p.db_path):
                assert p.engine.inventory_repo.get_user_bait_inventory('12345').get(bait_id, 0) == remaining
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_manual_timed_bait_cannot_refresh_or_switch_for_free(tmp_path, monkeypatch):
    import fishing_port.core.services.inventory_service as inventory

    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        now = get_now()
        monkeypatch.setattr(inventory, 'get_now', lambda: now, raising=False)
        with atomic(p.db_path):
            p.engine.inventory_repo.update_bait_quantity('12345', 9, 1)
            p.engine.inventory_repo.update_bait_quantity('12345', 1, 1)
        assert p.engine.inventory_service.use_bait('12345', 9)['success']
        with atomic(p.db_path):
            started = p.engine.user_repo.get_by_id('12345').bait_start_time
            assert started == now
        now += timedelta(minutes=20)
        assert p.engine.inventory_service.use_bait('12345', 9)['success']
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').bait_start_time == started
        assert p.engine.inventory_service.use_bait('12345', 1)['success']
        with atomic(p.db_path):
            assert p.engine.inventory_repo.get_user_bait_inventory('12345').get(9, 0) == 0
        assert not p.engine.inventory_service.use_bait('12345', 9)['success']
    finally:
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('capacity,quota_left,expected_caught,expected_stored', [
    (1, 3, 2, 1), (10, 3, 2, 2), (10, 1, 1, 1),
])
async def test_multiple_catches_obey_capacity_and_rare_quota(
    tmp_path, monkeypatch, capacity, quota_left, expected_caught, expected_stored,
):
    import fishing_port.core.services.fishing_service as fishing

    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        p.engine.fishing_service._reset_rare_fish_daily_quota()
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            user.fish_pond_capacity = capacity
            p.engine.user_repo.update(user)
            p.engine.inventory_repo.update_bait_quantity('12345', 14, 1)
            zone = p.engine.inventory_repo.get_zone_by_id(user.fishing_zone_id)
            zone.daily_rare_fish_quota = 10
            zone.rare_fish_caught_today = 10 - quota_left
            p.engine.inventory_repo.update_fishing_zone(zone)
            fish = p.engine.item_template_repo.get_fishes_by_rarity(4)[0]
        monkeypatch.setattr(fishing.random, 'random', lambda: 0.0)
        monkeypatch.setattr(p.engine.fishing_service, '_get_fish_template', lambda *args: fish)
        result = p.engine.fishing_service.go_fish('12345')
        assert result['success']
        with atomic(p.db_path):
            assert sum(s.quantity for s in p.engine.inventory_repo.get_fish_inventory('12345')) == expected_stored
            assert p.engine.inventory_repo.get_zone_by_id(zone.id).rare_fish_caught_today == 10 - quota_left + expected_caught
            assert p.engine.user_repo.get_by_id('12345').total_fishing_count == expected_caught
    finally:
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['rod', 'accessory'])
async def test_gacha_equipment_reward_quantity(tmp_path, kind):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        getter = (p.engine.inventory_repo.get_user_rod_instances if kind == 'rod'
                  else p.engine.inventory_repo.get_user_accessory_instances)
        with atomic(p.db_path):
            before = len(getter('12345'))
            p.engine.gacha_repo.add_pool_template({'name': '装备数量回归', 'cost_coins': 10})
            pool = next(pool for pool in p.engine.gacha_repo.get_all_pools() if pool.name == '装备数量回归')
            p.engine.gacha_repo.add_pool_item(pool.gacha_pool_id, {
                'item_type': kind, 'item_id': 1, 'quantity': 3, 'weight': 1,
            })
        result = p.engine.gacha_service.perform_draw('12345', pool.gacha_pool_id, num_draws=2)
        assert result['success'], result
        with atomic(p.db_path):
            assert len(getter('12345')) == before + 6
            assert p.engine.user_repo.get_by_id('12345').coins == 180
        assert [item['quantity'] for item in result['results']] == [3, 3]
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            user.coins = 1000
            p.engine.user_repo.update(user)
        await command(p, f'十连 {pool.gacha_pool_id} 2')
        assert '共获得 60 件物品' in p.ctx.send.messages[-1][1]
        assert '× 60' in p.ctx.send.messages[-1][1]
        with atomic(p.db_path):
            assert len(getter('12345')) == before + 66
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_seed_effect_migration_preserves_custom_templates(tmp_path):
    migration = importlib.import_module('fishing_port.core.database.migrations.042_repair_seeded_bait_effects')
    p = await load_plugin(tmp_path)
    try:
        with atomic(p.db_path) as db:
            # 恢复旧初始化遗漏字段后的形态，同时保留一条人工调整的模板。
            db.execute('''UPDATE baits SET success_rate_modifier=0, rare_chance_modifier=0,
                garbage_reduction_modifier=0, value_modifier=1, quantity_modifier=1, is_consumable=0''')
            db.execute('UPDATE baits SET quantity_modifier=3 WHERE bait_id=2')
            migration.up(db.cursor())
            migration.up(db.cursor())
            assert p.engine.item_template_repo.get_bait_by_id(14).quantity_modifier == 2
            assert p.engine.item_template_repo.get_bait_by_id(9).garbage_reduction_modifier == .8
            assert p.engine.item_template_repo.get_bait_by_id(1).is_consumable
            assert not p.engine.item_template_repo.get_bait_by_id(5).is_consumable
            assert p.engine.item_template_repo.get_bait_by_id(2).quantity_modifier == 3
    finally:
        await p.on_unload()

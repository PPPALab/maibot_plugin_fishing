import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import importlib.util
from pathlib import Path
import sqlite3

import pytest

from fishing_port.core.database.unit_of_work import atomic
from fishing_port.core.utils import get_now
from test_native_adapter import command, load_plugin


@pytest.mark.asyncio
async def test_paid_miss_cooldown_and_auto(tmp_path, monkeypatch):
    import fishing_port.core.services.fishing_service as fishing
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        monkeypatch.setattr(fishing.random, 'random', lambda: .999999)
        await command(p, '钓鱼')
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            assert user.last_fishing_time
            coins = user.coins
        await command(p, '钓鱼')
        assert '等待' in p.ctx.send.messages[-1][1]
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == coins
            user = p.engine.user_repo.get_by_id('12345')
            user.last_fishing_time = get_now() - timedelta(hours=1)
            p.engine.user_repo.update(user)
        await command(p, '自动钓鱼')
        await p.tick(40)
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            assert user.auto_fishing_enabled
            assert (get_now() - user.last_fishing_time).total_seconds() < 10
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_quality_aquarium_roundtrip(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            p.engine.inventory_repo.add_fish_to_inventory('12345', 1, 3, quality_level=1)
        assert p.engine.aquarium_service.add_fish_to_aquarium('12345', 1, 2, 1)['success']
        assert p.engine.aquarium_service.remove_fish_from_aquarium('12345', 1, 1, 1)['success']
        with atomic(p.db_path):
            inventory = p.engine.inventory_repo.get_fish_inventory('12345')
            fish = [f for f in inventory if f.fish_id == 1 and f.quality_level == 1]
            assert sum(f.quantity for f in fish) == 2
            assert p.engine.inventory_repo.get_user_total_fish_count('12345', 1) == 3
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_market_single_purchase(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        await command(p, '注册', uid='23456')
        with atomic(p.db_path):
            p.engine.inventory_repo.add_fish_to_inventory('12345', 1, 3, quality_level=1)
        assert p.engine.market_service.put_item_on_sale('12345', 'fish', 1, 10, quantity=2, quality_level=1)['success']
        listing = p.engine.market_service.get_market_listings()['fish'][0]
        listing_id = listing.market_id if hasattr(listing, 'market_id') else listing['market_id']
        result = p.engine.market_service.buy_market_item('23456', listing_id)
        assert result['success'], result
        assert not p.engine.market_service.buy_market_item('23456', listing_id)['success']
        with atomic(p.db_path):
            assert p.engine.inventory_repo.get_user_total_fish_count('12345', 1) == 1
            assert p.engine.inventory_repo.get_user_total_fish_count('23456', 1) == 2
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_shop_stock_and_fault_atomicity(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            shop = p.engine.shop_repo.create_shop({'name': '离线测试商店'})
            item = p.engine.shop_repo.create_shop_item(shop['shop_id'], {'name': '鱼', 'stock_total': 1})
            iid = item['item_id']
            p.engine.shop_repo.add_item_cost(iid, {'cost_type': 'coins', 'cost_amount': 10})
            p.engine.shop_repo.add_item_reward(iid, {'reward_type': 'fish', 'reward_item_id': 1, 'reward_quantity': 1})
        give = p.engine.shop_service._give_rewards
        def fail(*args, **kwargs):
            raise RuntimeError('模拟扣款后奖励失败')
        monkeypatch.setattr(p.engine.shop_service, '_give_rewards', fail)
        with pytest.raises(RuntimeError):
            p.engine.shop_service.purchase_item('12345', iid)
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 200
            assert p.engine.shop_repo.get_shop_item_by_id(iid)['stock_sold'] == 0
        monkeypatch.setattr(p.engine.shop_service, '_give_rewards', give)
        assert p.engine.shop_service.purchase_item('12345', iid)['success']
        assert not p.engine.shop_service.purchase_item('12345', iid)['success']
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 190
            assert p.engine.inventory_repo.get_user_total_fish_count('12345', 1) == 1
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_threaded_transfer_no_overdraft(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        await command(p, '注册', uid='23456')
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(lambda _: p.engine.user_service.transfer_coins('12345', '23456', 50), range(12)))
        successes = sum(bool(r['success']) for r in results)
        assert 1 <= successes <= 4
        with atomic(p.db_path):
            a = p.engine.user_repo.get_by_id('12345').coins
            b = p.engine.user_repo.get_by_id('23456').coins
            assert a >= 0 and b >= 200
            assert a + b <= 400  # difference is original transfer tax
    finally:
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('schema_version', [40, 41, 42])
async def test_backup_restore_and_process_exclusion(tmp_path, schema_version):
    spec = importlib.util.spec_from_file_location('backup_tool', Path(__file__).parents[1] / 'tools/database.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    p = await load_plugin(tmp_path / 'data')
    try:
        await command(p, '注册')
        file = tmp_path / 'backup.sqlite3'
        if schema_version < 42:
            # 模拟早期原生插件备份，恢复后仍可自动升级。
            with atomic(p.db_path) as db:
                if schema_version == 40:
                    db.execute('DROP TABLE fishing_quota_reset')
                db.execute('UPDATE schema_version SET version = ?', (schema_version,))
        tool.backup(tmp_path / 'data', file)
        with pytest.raises(BlockingIOError):
            tool.restore(tmp_path / 'data', file)
    finally:
        await p.on_unload()
    tool.restore(tmp_path / 'data', file)
    with sqlite3.connect(tmp_path / 'data/astrbot-fishing.sqlite3') as db:
        assert db.execute("SELECT user_id FROM users WHERE user_id='12345'").fetchone()
    p = await load_plugin(tmp_path / 'data')
    try:
        with atomic(p.db_path) as db:
            assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 42
            assert p.engine.user_repo.get_by_id('12345') is not None
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_config_reload(tmp_path):
    from fishing_port.config import FishingConfig
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        new = FishingConfig(fishing={'cooldown_seconds': 7, 'quality_bonus_max_chance': .35})
        p._plugin_config_instance = new
        await p.on_config_update('plugin', new.model_dump(), '2')
        assert p.engine.game_config['fishing']['cooldown_seconds'] == 7
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345')
        assert len(p.tasks) == 1
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_equipment_lock_and_quality_catch(tmp_path, monkeypatch):
    import fishing_port.core.services.fishing_service as fishing
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            rods = p.engine.item_template_repo.get_all_rods()
            rod = p.engine.inventory_repo.add_rod_instance('12345', rods[-1].rod_id, 100)
        iid = rod.rod_instance_id
        assert p.engine.inventory_service.lock_rod('12345', iid)['success']
        assert not p.engine.inventory_service.sell_equipment('12345', iid, 'rod')['success']
        assert p.engine.inventory_service.unlock_rod('12345', iid)['success']
        assert p.engine.inventory_service.equip_item('12345', iid, 'rod')['success']
        monkeypatch.setattr(fishing.random, 'random', lambda: 0.0)
        result = p.engine.fishing_service.go_fish('12345')
        assert result['success'], result
        assert 'quality_level' in result['fish']
        with atomic(p.db_path):
            assert p.engine.inventory_repo.get_fish_inventory('12345')
        # Probability modifier must remain normalized and never negative.
        distribution = p.engine.fishing_service._apply_rare_chance_to_distribution([.5, .2, .15, .1, .04, .01], .3)
        assert abs(sum(distribution) - 1) < 1e-6
        assert all(x >= 0 for x in distribution)
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_redpacket_concurrent_claim_and_restart(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        await command(p, '注册', uid='23456')
        sent = p.engine.red_packet_service.send_red_packet('12345', 'qq_group_123', 'normal', 100, 1)
        assert sent['success'], sent
        packet_id = sent['packet_id']
    finally:
        await p.on_unload()
    p = await load_plugin(tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _: p.engine.red_packet_service.claim_red_packet('23456', 'qq_group_123', packet_id), range(4)))
        assert sum(r['success'] for r in results) == 1
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 100
            assert p.engine.user_repo.get_by_id('23456').coins == 300
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_signin_gacha_and_wheel(tmp_path, monkeypatch):
    import fishing_port.core.services.game_mechanics_service as mechanics
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        assert p.engine.user_service.daily_sign_in('12345')['success']
        assert not p.engine.user_service.daily_sign_in('12345')['success']
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            user.coins = 100000
            user.premium_currency = 100
            p.engine.user_repo.update(user)
            pools = p.engine.gacha_repo.get_all_pools()
        assert pools
        result = p.engine.gacha_service.perform_draw('12345', pools[0].gacha_pool_id, 1)
        assert result['success'], result
        monkeypatch.setattr(mechanics.random, 'random', lambda: 0.0)
        assert p.engine.game_mechanics_service.start_wheel_of_fate('12345', 500)['success']
        result = p.engine.game_mechanics_service.cash_out_wheel_of_fate('12345')
        assert result['success'], result
        assert not p.engine.game_mechanics_service.cash_out_wheel_of_fate('12345')['success']
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_image_send_failure_text_fallback(tmp_path):
    from fishing_port.events import Reply
    from PIL import Image
    p = await load_plugin(tmp_path)
    try:
        path = tmp_path / 'image.png'
        Image.new('RGB', (10, 10)).save(path)
        async def fail(*args):
            return False
        p.ctx.send.image = fail
        assert await p.send_reply(Reply('image', str(path), '原始结果文字'), 's')
        assert p.ctx.send.messages[-1] == ('text', '原始结果文字', 's')
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_refine_and_achievement_no_duplicate_reward(tmp_path, monkeypatch):
    import random
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            user.coins = 1000000
            user.total_fishing_count = 100
            p.engine.user_repo.update(user)
            template = p.engine.item_template_repo.get_all_rods()[0]
            rod = p.engine.inventory_repo.add_rod_instance('12345', template.rod_id, 100)
            p.engine.inventory_repo.add_rod_instance('12345', template.rod_id, 100)
        monkeypatch.setattr(random, 'random', lambda: 0.0)
        result = p.engine.inventory_service.refine('12345', rod.rod_instance_id, 'rod')
        assert result['success'], result
        with atomic(p.db_path):
            rods = p.engine.inventory_repo.get_user_rod_instances('12345')
            assert len(rods) == 1 and rods[0].refine_level == 2
        p.engine.achievement_service._process_user_achievements('12345')
        with atomic(p.db_path):
            first = p.engine.user_repo.get_by_id('12345').coins
            progress = p.engine.achievement_repo.get_user_progress('12345')
            assert any(item.get('completed_at') for item in progress.values())
        p.engine.achievement_service._process_user_achievements('12345')
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == first
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_exchange_real_database_and_exception_rollback(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            user.coins = 100000
            p.engine.user_repo.update(user)
        assert p.engine.exchange_service.open_exchange_account('12345')['success']
        price = p.engine.exchange_service.price_service
        price.update_daily_prices()
        status = p.engine.exchange_service.get_market_status()
        assert status['success'], status
        assert p.engine.exchange_service.purchase_commodity('12345', 'dried_fish', 2, 100)['success']
        sold = p.engine.exchange_service.sell_commodity('12345', 'dried_fish', 1, 150)
        assert sold['success'], sold
        original = p.engine.exchange_repo.add_user_commodity
        def fail(*args, **kwargs):
            raise RuntimeError('模拟扣款后库存写入失败')
        monkeypatch.setattr(p.engine.exchange_repo, 'add_user_commodity', fail)
        with atomic(p.db_path):
            before = p.engine.user_repo.get_by_id('12345').coins
        failed = p.engine.exchange_service.purchase_commodity('12345', 'dried_fish', 1, 100)
        assert not failed['success']
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == before
        monkeypatch.setattr(p.engine.exchange_repo, 'add_user_commodity', original)
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_unclean_process_exit_rolls_back(tmp_path):
    import subprocess
    import sys
    p = await load_plugin(tmp_path)
    await command(p, '注册')
    await p.on_unload()
    process = subprocess.run([sys.executable, '-c',
        "import sqlite3,sys,os; c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN IMMEDIATE'); "
        "c.execute(\"UPDATE users SET coins=999999 WHERE user_id='12345'\"); os._exit(91)",
        str(tmp_path / 'astrbot-fishing.sqlite3')], check=False)
    assert process.returncode == 91
    p = await load_plugin(tmp_path)
    try:
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 200
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_zone_pass_and_social_quality_conservation(tmp_path, monkeypatch):
    import random
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        await command(p, '注册', uid='23456')
        with atomic(p.db_path):
            item = p.engine.item_template_repo.get_all_items()[0]
            from dataclasses import asdict
            zone = asdict(p.engine.inventory_repo.get_zone_by_id(2))
            p.engine.fishing_zone_service.update_zone(2, {**zone, 'is_active': True, 'requires_pass': True, 'required_item_id': item.item_id})
        assert not p.engine.fishing_service.set_user_fishing_zone('12345', 2)['success']
        with atomic(p.db_path):
            p.engine.inventory_repo.add_item_to_user('12345', item.item_id, 1)
        assert p.engine.fishing_service.set_user_fishing_zone('12345', 2)['success']
        with atomic(p.db_path):
            assert p.engine.inventory_repo.get_user_item_inventory('12345').get(item.item_id, 0) == 0
            p.engine.inventory_repo.add_fish_to_inventory('23456', 1, 110, quality_level=1)
        result = p.engine.game_mechanics_service.steal_fish('12345', '23456')
        assert result['success'], result
        assert not p.engine.game_mechanics_service.steal_fish('12345', '23456')['success']
        monkeypatch.setattr(random, 'random', lambda: 0.0)
        result = p.engine.game_mechanics_service.electric_fish('12345', '23456')
        assert result['success'], result
        with atomic(p.db_path):
            all_fish = p.engine.inventory_repo.get_fish_inventory('12345') + p.engine.inventory_repo.get_fish_inventory('23456')
            assert sum(f.quantity for f in all_fish) == 110
            assert all(f.quality_level == 1 for f in all_fish)
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_inventory_cannot_silently_overdraw(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            p.engine.inventory_repo.add_fish_to_inventory('12345', 1, 2, quality_level=1)
        with pytest.raises(ValueError):
            with atomic(p.db_path):
                p.engine.inventory_repo.update_fish_quantity('12345', 1, -1, quality_level=0)
                p.engine.inventory_repo.add_fish_to_inventory('12345', 1, 999, quality_level=1)
        with atomic(p.db_path):
            assert p.engine.inventory_repo.get_user_total_fish_count('12345', 1) == 2
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_wipe_bomb_suppression_survives_restart(tmp_path):
    import json
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            user.wipe_bomb_forecast = json.dumps({'mode': 'accurate', 'multiplier': 20.0})
            p.engine.user_repo.update(user)
        await command(p, '擦弹 100', mid='wipe')
        assert p.engine.game_mechanics_service._server_suppressed
    finally:
        await p.on_unload()
    p = await load_plugin(tmp_path)
    try:
        assert p.engine.game_mechanics_service._check_server_suppression()
        with atomic(p.db_path):
            user = p.engine.user_repo.get_by_id('12345')
            assert user.coins == 2100
            assert user.wipe_bomb_attempts_today == 1
    finally:
        await p.on_unload()

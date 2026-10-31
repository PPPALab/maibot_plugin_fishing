"""交易所卖出批次、过期商品税基与实际资金/库存回归。"""
from datetime import datetime, timedelta

import pytest

from fishing_port.core.database.unit_of_work import atomic
from fishing_port.core.domain.models import UserCommodity
from test_native_adapter import command, load_plugin


def add_batch(plugin, *, quantity, price, expired=False, uid='12345'):
    now = datetime.now()
    batch = plugin.engine.exchange_repo.add_user_commodity(UserCommodity(
        instance_id=0, user_id=uid, commodity_id='dried_fish',
        quantity=quantity, purchase_price=price, purchased_at=now,
        expires_at=now + timedelta(days=-1 if expired else 2),
    ))
    return batch.instance_id


@pytest.mark.asyncio
@pytest.mark.parametrize('valid_quantity', [0, 10, 20])
async def test_expired_sales_tax_only_actual_profit(tmp_path, valid_quantity):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            add_batch(p, quantity=10, price=100, expired=True)
            if valid_quantity:
                add_batch(p, quantity=valid_quantity, price=100)
            before = p.engine.user_repo.get_by_id('12345').coins
        service = p.engine.exchange_service.inventory_service
        service.config['tax_rate'] = 0.1
        result = service.sell_commodity('12345', 'dried_fish', 10 + valid_quantity, 200)
        assert result['success'], result
        # 腐败批次没有收入；分别覆盖亏损、持平及实际盈利。
        profit = valid_quantity * 200 - (10 + valid_quantity) * 100
        expected_tax = int(max(profit, 0) * 0.1)
        assert result['tax_amount'] == expected_tax
        assert result['profit_loss']['total_income'] == valid_quantity * 200
        assert result['profit_loss']['profit_loss'] == profit
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == before + valid_quantity * 200 - expected_tax
            assert not p.engine.exchange_repo.get_user_commodities('12345')
            tax = p.engine.log_repo.get_tax_records('12345')[-1]
            assert tax.tax_amount == expected_tax
            assert tax.original_amount == max(profit, 0)
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_command_sells_selected_batch_not_oldest(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            old_id = add_batch(p, quantity=2, price=100, expired=True)
            selected_id = add_batch(p, quantity=3, price=200)
        monkeypatch.setattr(p.engine.exchange_service, 'get_market_status', lambda: {
            'success': True, 'prices': {'dried_fish': 200},
        })
        code = p.engine.exchange_service.inventory_service._to_base36(selected_id)
        await command(p, f'交易所 卖出 C{code} 1')
        with atomic(p.db_path):
            batches = {b.instance_id: b for b in p.engine.exchange_repo.get_user_commodities('12345')}
            assert batches[old_id].quantity == 2
            assert batches[selected_id].quantity == 2
            assert p.engine.user_repo.get_by_id('12345').coins == 400
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_instance_sale_rejects_other_users_batch(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        await command(p, '注册', uid='23456')
        with atomic(p.db_path):
            own_id = add_batch(p, quantity=2, price=100)
            other_id = add_batch(p, quantity=2, price=100, uid='23456')
        result = p.engine.exchange_service.sell_commodity_by_instance('12345', other_id, 1, 200)
        assert not result['success'], result
        with atomic(p.db_path):
            assert p.engine.exchange_repo.get_user_commodity_by_instance_id(own_id).quantity == 2
            assert p.engine.exchange_repo.get_user_commodity_by_instance_id(other_id).quantity == 2
            assert p.engine.user_repo.get_by_id('12345').coins == 200
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_selected_batch_sale_rolls_back_when_tax_log_fails(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            selected_id = add_batch(p, quantity=2, price=100)

        def fail_log(record):
            raise RuntimeError('模拟税收日志写入失败')

        monkeypatch.setattr(p.engine.log_repo, 'add_tax_record', fail_log)
        result = p.engine.exchange_service.sell_commodity_by_instance('12345', selected_id, 1, 200)
        assert not result['success'], result
        with atomic(p.db_path):
            assert p.engine.exchange_repo.get_user_commodity_by_instance_id(selected_id).quantity == 2
            assert p.engine.user_repo.get_by_id('12345').coins == 200
            assert not p.engine.log_repo.get_tax_records('12345')
    finally:
        await p.on_unload()

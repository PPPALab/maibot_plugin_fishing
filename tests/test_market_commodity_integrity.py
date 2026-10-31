from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest

from fishing_port.core.database.unit_of_work import atomic
from fishing_port.core.domain.models import UserCommodity
from test_native_adapter import command, load_plugin


@pytest.mark.asyncio
@pytest.mark.parametrize('quantity', [3, 10])
@pytest.mark.parametrize('finish', ['delist', 'buy'])
async def test_commodity_listing_preserves_unlisted_stock(tmp_path, quantity, finish):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        expires = datetime.now() + timedelta(days=2)
        with atomic(p.db_path):
            p.engine.exchange_repo.add_user_commodity(UserCommodity(
                instance_id=0, user_id='12345', commodity_id='dried_fish',
                quantity=10, purchase_price=100, purchased_at=datetime.now(),
                expires_at=expires,
            ))
            stock = p.engine.exchange_repo.get_user_commodities('12345')[0]
        result = p.engine.market_service.put_item_on_sale(
            '12345', 'commodity', stock.instance_id, 10, quantity=quantity,
        )
        assert result['success'], result
        with atomic(p.db_path):
            remaining = p.engine.exchange_repo.get_user_commodities('12345')
            listings, _ = p.engine.market_repo.get_all_listings()
            assert sum(c.quantity for c in remaining) == 10 - quantity
            assert len(listings) == 1
            assert listings[0].quantity == quantity
            assert listings[0].expires_at == expires
            if remaining:
                assert remaining[0].instance_id == stock.instance_id
                assert remaining[0].purchase_price == stock.purchase_price
                assert remaining[0].expires_at == expires
        if finish == 'delist':
            assert p.engine.market_service.delist_item('12345', listings[0].market_id)['success']
        else:
            await command(p, '注册', uid='23456')
            with atomic(p.db_path):
                buyer = p.engine.user_repo.get_by_id('23456')
                buyer.exchange_account_status = True
                p.engine.user_repo.update(buyer)
            assert p.engine.market_service.buy_market_item('23456', listings[0].market_id)['success']
        with atomic(p.db_path):
            seller_stock = p.engine.exchange_repo.get_user_commodities('12345')
            buyer_stock = p.engine.exchange_repo.get_user_commodities('23456')
            assert sum(c.quantity for c in seller_stock + buyer_stock) == 10
            if finish == 'buy':
                assert sum(c.quantity for c in buyer_stock) == quantity
            assert not p.engine.market_repo.get_all_listings()[0]
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_commodity_listing_failure_rolls_back_stock_and_fee(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            p.engine.exchange_repo.add_user_commodity(UserCommodity(
                instance_id=0, user_id='12345', commodity_id='dried_fish',
                quantity=10, purchase_price=100, purchased_at=datetime.now(),
                expires_at=datetime.now() + timedelta(days=2),
            ))
            stock = p.engine.exchange_repo.get_user_commodities('12345')[0]
            coins = p.engine.user_repo.get_by_id('12345').coins

        def fail(listing):
            raise RuntimeError('模拟挂单写入失败')

        monkeypatch.setattr(p.engine.market_repo, 'add_listing', fail)
        with pytest.raises(RuntimeError, match='模拟挂单写入失败'):
            p.engine.market_service.put_item_on_sale(
                '12345', 'commodity', stock.instance_id, 100, quantity=3,
            )
        with atomic(p.db_path):
            assert p.engine.exchange_repo.get_user_commodity_by_instance_id(stock.instance_id).quantity == 10
            assert p.engine.user_repo.get_by_id('12345').coins == coins
            assert not p.engine.market_repo.get_all_listings()[0]
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_concurrent_commodity_listing_cannot_overdraw(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        with atomic(p.db_path):
            p.engine.exchange_repo.add_user_commodity(UserCommodity(
                instance_id=0, user_id='12345', commodity_id='dried_fish',
                quantity=10, purchase_price=100, purchased_at=datetime.now(),
                expires_at=datetime.now() + timedelta(days=2),
            ))
            stock = p.engine.exchange_repo.get_user_commodities('12345')[0]
            coins = p.engine.user_repo.get_by_id('12345').coins
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _: p.engine.market_service.put_item_on_sale(
                '12345', 'commodity', stock.instance_id, 100, quantity=3,
            ), range(4)))
        # The market schema permits only one active listing per commodity batch.
        assert sum(result['success'] for result in results) == 1
        assert all('已有市场挂单' in result['message'] for result in results if not result['success'])
        with atomic(p.db_path):
            remaining = p.engine.exchange_repo.get_user_commodities('12345')
            listings, _ = p.engine.market_repo.get_all_listings()
            assert sum(c.quantity for c in remaining) == 7
            assert sum(listing.quantity for listing in listings) == 3
            tax = int(100 * p.engine.market_service.config['market']['listing_tax_rate'])
            assert p.engine.user_repo.get_by_id('12345').coins == coins - tax
    finally:
        await p.on_unload()

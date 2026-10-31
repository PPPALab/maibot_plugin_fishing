from datetime import timedelta

import pytest

from fishing_port.core.database.unit_of_work import atomic
from fishing_port.core.utils import get_now
from test_native_adapter import command, load_plugin


@pytest.mark.asyncio
@pytest.mark.parametrize('seconds_remaining', [30, 0, -1])
async def test_reopening_preserves_unsettled_bets(tmp_path, monkeypatch, seconds_remaining):
    p = await load_plugin(tmp_path)
    try:
        p.engine.sicbo_service.set_message_mode('text')
        await command(p, '注册')
        await command(p, '开庄')
        await command(p, '鸭大 100')
        sid, game = next(iter(p.engine.sicbo_service.games.items()))
        game.end_time = get_now() + timedelta(seconds=seconds_remaining)

        # A command may acquire the lock before the deadline scheduler does.
        await command(p, '开庄', mid='reopen')
        pending = p.engine.sicbo_service.games[sid]
        assert pending.game_id == game.game_id
        assert len(pending.bets) == 1
        assert pending.total_pot == 100
        assert pending.bets[0].amount == 100
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 100
    finally:
        await p.on_unload()

    # Rejected reopening must preserve the durable bet as well as memory.
    p = await load_plugin(tmp_path)
    try:
        game = p.engine.sicbo_service.games[sid]
        assert len(game.bets) == 1
        game.end_time = get_now() - timedelta(seconds=1)
        dice = iter([6, 6, 5])
        monkeypatch.setattr('fishing_port.core.services.sicbo_service.random.randint', lambda *_: next(dice))
        p.engine.sicbo_service.set_message_mode('text')
        await p.tick(1)
        await p.tick(2)
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345').coins == 300
        assert game.is_settled
        await command(p, '开庄')
        assert p.engine.sicbo_service.games[sid].is_active
        assert not p.engine.sicbo_service.games[sid].bets
    finally:
        await p.on_unload()

import pytest

from fishing_port.core.database.unit_of_work import atomic
from test_native_adapter import command, load_plugin


@pytest.mark.asyncio
async def test_wrong_password_response_does_not_reveal_correct_password(tmp_path):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        await command(p, '注册', uid='23456')
        sent = p.engine.red_packet_service.send_red_packet(
            '12345', 'qq:group:123', 'password', 100, 1, password='只有知情者才知道',
        )
        await command(p, f"领红包 {sent['packet_id']} 错误猜测", uid='23456')
        response = p.ctx.send.messages[-1][1]
        assert '口令错误' in response
        assert '只有知情者才知道' not in response
        with atomic(p.db_path):
            packet = p.engine.red_packet_repo.get_red_packet_by_id(sent['packet_id'])
            assert packet.remaining_count == 1
            assert p.engine.user_repo.get_by_id('23456').coins == 200
    finally:
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('specified_id', [False, True])
async def test_multiword_password_claim(tmp_path, specified_id):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        await command(p, '注册', uid='23456')
        await command(p, '发红包 100 1 口令 恭喜 发财')
        with atomic(p.db_path):
            packet = p.engine.red_packet_repo.get_active_red_packets_in_group('qq:group:123')[0]
            assert packet.password == '恭喜 发财'
            before = p.engine.user_repo.get_by_id('23456').coins
        text = f'领红包 {packet.packet_id} 恭喜 发财' if specified_id else '领红包 恭喜 发财'
        await command(p, text, uid='23456', mid='multiword-password')
        assert '领取成功' in p.ctx.send.messages[-1][1]
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('23456').coins == before + 100
            assert p.engine.red_packet_repo.has_user_claimed(packet.packet_id, '23456')
        await command(p, text, uid='23456', mid='multiword-password')
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('23456').coins == before + 100
    finally:
        await p.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize('specified_id', [False, True])
@pytest.mark.parametrize('packet_type', ['normal', 'lucky', 'password'])
async def test_unregistered_claim_does_not_consume_packet(tmp_path, specified_id, packet_type):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        sent = p.engine.red_packet_service.send_red_packet(
            '12345', 'qq:group:123', packet_type, 100, 1, password='测试口令',
        )
        assert sent['success']
        packet_id = sent['packet_id']
        text = f'领红包 {packet_id}' if specified_id else '领红包'
        if packet_type == 'password':
            text += ' 测试口令'
        await command(p, text, uid='23456')
        assert '注册' in p.ctx.send.messages[-1][1]
        with atomic(p.db_path):
            packet = p.engine.red_packet_repo.get_red_packet_by_id(packet_id)
            assert packet.remaining_amount == 100
            assert packet.remaining_count == 1
            assert not packet.is_expired
            assert not p.engine.red_packet_repo.has_user_claimed(packet_id, '23456')
            assert p.engine.user_repo.get_by_id('23456') is None
        # Rejection must leave the packet available after the player registers.
        await command(p, '注册', uid='23456')
        with atomic(p.db_path):
            before = p.engine.user_repo.get_by_id('23456').coins
        await command(p, text, uid='23456')
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('23456').coins == before + 100
            assert p.engine.red_packet_repo.has_user_claimed(packet_id, '23456')
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_claim_credit_failure_rolls_back_packet_and_record(tmp_path, monkeypatch):
    p = await load_plugin(tmp_path)
    try:
        await command(p, '注册')
        await command(p, '注册', uid='23456')
        sent = p.engine.red_packet_service.send_red_packet(
            '12345', 'qq:group:123', 'normal', 100, 1,
        )
        assert sent['success']
        packet_id = sent['packet_id']
        with atomic(p.db_path):
            before = p.engine.user_repo.get_by_id('23456').coins
        update = p.engine.user_repo.update

        def fail_after_credit(user):
            update(user)
            raise RuntimeError('模拟红包入账后失败')

        with monkeypatch.context() as patch:
            patch.setattr(p.engine.user_repo, 'update', fail_after_credit)
            with pytest.raises(RuntimeError, match='模拟红包入账后失败'):
                p.engine.red_packet_service.claim_red_packet('23456', 'qq:group:123', packet_id)
        with atomic(p.db_path):
            packet = p.engine.red_packet_repo.get_red_packet_by_id(packet_id)
            assert packet.remaining_amount == 100
            assert packet.remaining_count == 1
            assert not packet.is_expired
            assert not p.engine.red_packet_repo.get_claim_records_by_packet(packet_id)
            assert p.engine.user_repo.get_by_id('23456').coins == before
        result = p.engine.red_packet_service.claim_red_packet('23456', 'qq:group:123', packet_id)
        assert result['success']
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('23456').coins == before + 100
    finally:
        await p.on_unload()

"""Text snapshots for original images; never re-execute a paid command on failure."""
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path


def serialize(value):
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def image_text(engine, event, method):
    uid = engine._get_effective_user_id(event)
    if method == 'state':
        from .draw.state import get_user_state_data
        data = get_user_state_data(engine.user_repo, engine.inventory_repo, engine.item_template_repo,
                                   engine.log_repo, engine.buff_repo, engine.game_config, uid)
    elif method == 'user_backpack':
        from .draw.backpack import get_user_backpack_data
        data = get_user_backpack_data(engine.inventory_service, uid, max_items_per_category=50)
    elif method == 'fish_pokedex':
        data = engine.fishing_service.get_user_pokedex(uid)
    elif method == 'ranking':
        args = event.message_str.split()
        key = args[1] if len(args) > 1 else ''
        sort = 'fish_count' if key in ('数量', '钓获', 'fish') else 'total_weight_caught' if key in ('重量', 'weight') else 'max_coins' if key in ('历史', '最高', 'max', 'history', '历史最高') else 'coins'
        data = engine.user_service.get_leaderboard_data(sort)
    elif method == 'fishing_help':
        commands = json.loads(Path(__file__).with_name('commands.json').read_text())
        return '【钓鱼帮助：文字模式】\n' + '\n'.join(
            '/'+c['aliases'][0]+'：'+c['description'] for c in commands if event.is_admin() or not c['admin'])
    else:
        # All original Sicbo images use the same service's configuration/room state.
        from .handlers.sicbo_handlers import _get_game_session_id
        sid = _get_game_session_id(event)
        service = engine.sicbo_service
        if method == 'sicbo_odds':
            data = {'净赔率': service.odds_table, '指定点数': '出现1/2/3个时净赔率为1/2/3倍', '规则': '豹子杀大小单双'}
        elif method == 'sicbo_help':
            data = {'流程': f'开庄后{service.countdown_seconds}秒截止下注并结算',
                    '下注': '鸭大/鸭小/鸭单/鸭双/鸭豹子/鸭一点至六点/鸭4点至17点 金额',
                    '范围': [service.min_bet, service.max_bet], '规则': '豹子杀大小单双'}
        elif method == 'my_bets':
            data = service.get_user_bets(uid, sid)
        else:
            game = service.games.get(sid)
            data = asdict(game) if game else service.get_game_status(sid)
    return '【图片不可用，以下为原数据文字快照】\n' + json.dumps(data, ensure_ascii=False, indent=2, default=serialize)

"""
红包功能处理器
"""

from typing import TYPE_CHECKING

from ..events import GameEvent
from ..utils import parse_amount, parse_count

if TYPE_CHECKING:
    from ..engine import FishingPlugin


def _get_group_session_id(event: GameEvent) -> str:
    """
    获取红包的群组会话ID
    在群聊中使用群ID确保所有群成员共享同一个红包池
    在私聊中返回None（红包不支持私聊）
    """
    group_id = event.get_group_id()
    if group_id:
        # 群聊：使用 platform:group:群ID 作为会话ID
        platform_name = getattr(event.platform_meta, 'platform_name', 'aiocqhttp')
        return f"{platform_name}:group:{group_id}"
    else:
        # 私聊：不支持
        return None


async def send_red_packet(plugin: "FishingPlugin", event: GameEvent):
    """
    发送红包
    用法：
    /发红包 [金额] [数量] [类型] [口令]
    /发红包 1000           - 发1个1000金币的普通红包
    /发红包 1000 5         - 发5个各1000金币的普通红包
    /发红包 十万 三个       - 发3个各10万金币的普通红包（支持中文）
    /发红包 1000 5 拼手气  - 发总额1000金币的拼手气红包，分5个
    /发红包 1000 5 口令 恭喜发财 - 发5个各1000金币的口令红包
    """
    user_id = plugin._get_effective_user_id(event)
    
    # 获取群组会话ID
    group_id = _get_group_session_id(event)
    if not group_id:
        yield event.plain_result("❌ 红包功能只能在群聊中使用")
        return
    
    args = event.message_str.split()
    
    # 显示帮助
    if len(args) < 2:
        help_text = (
            "🧧 发红包使用说明\n\n"
            "【指令格式】\n"
            "/发红包 [金额] [数量] [类型] [口令]\n\n"
            "【参数说明】\n"
            "金额：必填，最低100金币（支持中文）\n"
            "数量：选填，默认1个（支持中文）\n"
            "类型：选填，可选 拼手气/口令\n"
            "口令：口令红包必填\n\n"
            "【示例】\n"
            "/发红包 1000\n"
            "  → 发1个1000金币的普通红包\n\n"
            "/发红包 5000 5\n"
            "  → 发5个各1000金币的普通红包\n\n"
            "/发红包 十万 三个\n"
            "  → 发3个各10万金币的普通红包\n\n"
            "/发红包 1000 5 拼手气\n"
            "  → 发总额1000的拼手气红包，分5个\n\n"
            "/发红包 1000 3 口令 恭喜发财\n"
            "  → 发3个各1000金币的口令红包\n\n"
            "【红包类型】\n"
            "🎁 普通红包：每个金额相同\n"
            "🎲 拼手气红包：随机金额，拼运气\n"
            "🔐 口令红包：需要口令才能领取"
        )
        yield event.plain_result(help_text)
        return
    
    # 解析参数
    try:
        amount = parse_amount(args[1])
    except ValueError as e:
        yield event.plain_result(f"❌ 金额格式错误: {e}")
        return
    
    count = 1
    packet_type = 'normal'
    password = None
    
    if len(args) >= 3:
        try:
            count = parse_count(args[2])
        except ValueError as e:
            yield event.plain_result(f"❌ 数量格式错误: {e}")
            return
    
    if len(args) >= 4:
        type_arg = args[3]
        if type_arg in ['拼手气', '手气', 'lucky']:
            packet_type = 'lucky'
        elif type_arg in ['口令', 'password']:
            packet_type = 'password'
            if len(args) < 5:
                yield event.plain_result("❌ 口令红包必须指定口令\n用法：/发红包 金额 数量 口令 你的口令")
                return
            password = ' '.join(args[4:])  # 口令可能包含空格
    
    # 发送红包
    result = plugin.red_packet_service.send_red_packet(
        sender_id=user_id,
        group_id=group_id,
        packet_type=packet_type,
        amount_per_packet=amount,
        count=count,
        password=password
    )
    
    yield event.plain_result(result["message"])


async def claim_red_packet(plugin: "FishingPlugin", event: GameEvent):
    """
    领取红包
    用法：
    /领红包             - 领取最新的非口令红包
    /领红包 123         - 领取指定ID的红包
    /领红包 123 口令    - 领取指定ID的口令红包
    /领红包 口令        - 领取最新的匹配口令的红包
    """
    user_id = plugin._get_effective_user_id(event)
    
    # 获取群组会话ID
    group_id = _get_group_session_id(event)
    if not group_id:
        yield event.plain_result("❌ 红包功能只能在群聊中使用")
        return
    
    args = event.message_str.split(maxsplit=2)
    packet_id = None
    password = None
    
    # 解析参数
    if len(args) >= 2:
        # 尝试解析第一个参数是否为红包ID
        try:
            packet_id = int(args[1])
            # 如果有第三个参数，作为口令
            if len(args) >= 3:
                password = args[2]
        except ValueError:
            # 第一个参数不是数字，保留完整口令（发红包支持口令包含空格）。
            password = ' '.join(args[1:])
    
    # 领取红包
    result = plugin.red_packet_service.claim_red_packet(
        user_id=user_id,
        group_id=group_id,
        packet_id=packet_id,
        password=password
    )
    
    yield event.plain_result(result["message"])


async def red_packet_details(plugin: "FishingPlugin", event: GameEvent):
    """
    查看红包详情
    用法：/红包详情 [红包ID]
    """
    args = event.message_str.split()
    
    if len(args) < 2:
        yield event.plain_result("❌ 请指定红包ID\n用法：/红包详情 [红包ID]")
        return
    
    try:
        packet_id = int(args[1])
    except ValueError:
        yield event.plain_result("❌ 红包ID必须是数字")
        return
    
    result = plugin.red_packet_service.get_red_packet_details(packet_id)
    yield event.plain_result(result["message"])


async def list_red_packets(plugin: "FishingPlugin", event: GameEvent):
    """
    列出当前群组可领取的红包
    用法：/红包列表
    """
    # 获取群组会话ID
    group_id = _get_group_session_id(event)
    if not group_id:
        yield event.plain_result("❌ 红包功能只能在群聊中使用")
        return
    
    result = plugin.red_packet_service.list_group_red_packets(group_id)
    yield event.plain_result(result["message"])


async def revoke_red_packet(plugin: "FishingPlugin", event: GameEvent):
    """
    撤回红包
    用法：/撤回红包 [红包ID]
    """
    user_id = plugin._get_effective_user_id(event)
    
    args = event.message_str.split()
    
    if len(args) < 2:
        yield event.plain_result("❌ 请指定红包ID\n用法：/撤回红包 [红包ID]")
        return
    
    try:
        packet_id = int(args[1])
    except ValueError:
        yield event.plain_result("❌ 红包ID必须是数字")
        return
    
    # 检查是否为机器人管理员
    is_admin = event.is_admin()
    
    result = plugin.red_packet_service.revoke_red_packet(packet_id, user_id, is_admin)
    yield event.plain_result(result["message"])


async def cleanup_red_packets(plugin: "FishingPlugin", event: GameEvent):
    """
    [管理员] 清理红包
    用法：
    /清理红包          - 清理当前群的所有红包并退回金额
    /清理红包 所有     - 清理全局所有群的红包（需谨慎）
    """
    # 验证管理员权限（仅机器人管理员）
    if not event.is_admin():
        yield event.plain_result("❌ 此命令仅限机器人管理员使用")
        return
    
    args = event.message_str.split()
    
    # 获取群组会话ID（与发红包时使用相同的格式）
    group_id = _get_group_session_id(event)
    
    # 带参数"所有"：清理全局所有红包
    if len(args) >= 2 and args[1] in ["所有", "all"]:
        # 清理全局所有红包
        result = plugin.red_packet_service.clean_all_red_packets()
        yield event.plain_result(result["message"])
        return
    
    # 不带参数：清理当前群的红包
    if not group_id:
        yield event.plain_result("❌ 此命令只能在群聊中使用\n提示：如需清理全局红包，请使用 /清理红包 所有")
        return
    
    result = plugin.red_packet_service.clean_group_red_packets(group_id)
    yield event.plain_result(result["message"])

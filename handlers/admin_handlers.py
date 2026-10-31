import asyncio
from hypercorn.config import Config
from hypercorn.asyncio import serve

import logging

logger = logging.getLogger(__name__)
from ..events import GameEvent
from ..events import Mention

from ..utils import parse_target_user_id, _is_port_available, parse_amount
from ..manager.server import create_app
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..engine import FishingPlugin


async def modify_coins(plugin: "FishingPlugin", event: GameEvent):
    """修改用户金币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查金币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定金币数量，例如：/修改金币 @用户 1000 或 /修改金币 123456789 1000"
        )
        return

    coins = args[2]
    if not coins.isdigit():
        yield event.plain_result("❌ 金币数量必须是数字，请检查后重试。")
        return

    if result := plugin.user_service.modify_user_coins(target_user_id, int(coins)):
        yield event.plain_result(
            f"✅ 成功修改用户 {target_user_id} 的金币数量为 {coins} 金币"
        )
    else:
        yield event.plain_result("❌ 出错啦！请稍后再试。")


async def modify_premium(plugin: "FishingPlugin", event: GameEvent):
    """修改用户高级货币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查高级货币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定高级货币数量，例如：/修改高级货币 @用户 100 或 /修改高级货币 123456789 100"
        )
        return

    premium = args[2]
    if not premium.isdigit():
        yield event.plain_result("❌ 高级货币数量必须是数字，请检查后重试。")
        return

    user = plugin.user_repo.get_by_id(target_user_id)
    if not user:
        yield event.plain_result("❌ 用户不存在或未注册，请检查后重试。")
        return
    user.premium_currency = int(premium)
    plugin.user_repo.update(user)
    yield event.plain_result(f"✅ 成功修改用户 {target_user_id} 的高级货币为 {premium}")


async def reward_premium(plugin: "FishingPlugin", event: GameEvent):
    """奖励用户高级货币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查高级货币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定高级货币数量，例如：/奖励高级货币 @用户 100 或 /奖励高级货币 123456789 100"
        )
        return

    premium = args[2]
    if not premium.isdigit():
        yield event.plain_result("❌ 高级货币数量必须是数字，请检查后重试。")
        return

    user = plugin.user_repo.get_by_id(target_user_id)
    if not user:
        yield event.plain_result("❌ 用户不存在或未注册，请检查后重试。")
        return
    user.premium_currency += int(premium)
    plugin.user_repo.update(user)
    yield event.plain_result(f"✅ 成功给用户 {target_user_id} 奖励 {premium} 高级货币")


async def deduct_premium(plugin: "FishingPlugin", event: GameEvent):
    """扣除用户高级货币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查高级货币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定高级货币数量，例如：/扣除高级货币 @用户 100 或 /扣除高级货币 123456789 100"
        )
        return

    premium = args[2]
    if not premium.isdigit():
        yield event.plain_result("❌ 高级货币数量必须是数字，请检查后重试。")
        return

    user = plugin.user_repo.get_by_id(target_user_id)
    if not user:
        yield event.plain_result("❌ 用户不存在或未注册，请检查后重试。")
        return
    if int(premium) > user.premium_currency:
        yield event.plain_result("❌ 扣除的高级货币不能超过用户当前拥有数量")
        return
    user.premium_currency -= int(premium)
    plugin.user_repo.update(user)
    yield event.plain_result(f"✅ 成功扣除用户 {target_user_id} 的 {premium} 高级货币")


async def reward_all_coins(plugin: "FishingPlugin", event: GameEvent):
    """给所有注册用户发放金币"""
    args = event.message_str.split(" ")
    if len(args) < 2:
        yield event.plain_result("❌ 请指定奖励的金币数量，例如：/全体奖励金币 1000 或 /全体奖励金币 一万")
        return
    
    try:
        amount_int = parse_amount(args[1])
        if amount_int <= 0:
            yield event.plain_result("❌ 奖励数量必须是正整数，请检查后重试。")
            return
    except ValueError as e:
        yield event.plain_result(f"❌ 数量格式错误：{str(e)}")
        return
    user_ids = plugin.user_repo.get_all_user_ids()
    if not user_ids:
        yield event.plain_result("❌ 当前没有注册用户。")
        return
    updated = 0
    for uid in user_ids:
        user = plugin.user_repo.get_by_id(uid)
        if not user:
            continue
        user.coins += amount_int
        plugin.user_repo.update(user)
        updated += 1
    yield event.plain_result(f"✅ 已向 {updated} 位用户每人发放 {amount_int} 金币")


async def reward_all_premium(plugin: "FishingPlugin", event: GameEvent):
    """给所有注册用户发放高级货币"""
    args = event.message_str.split(" ")
    if len(args) < 2:
        yield event.plain_result(
            "❌ 请指定奖励的高级货币数量，例如：/全体奖励高级货币 100"
        )
        return
    amount = args[1]
    if not amount.isdigit() or int(amount) <= 0:
        yield event.plain_result("❌ 奖励数量必须是正整数，请检查后重试。")
        return
    amount_int = int(amount)
    user_ids = plugin.user_repo.get_all_user_ids()
    if not user_ids:
        yield event.plain_result("❌ 当前没有注册用户。")
        return
    updated = 0
    for uid in user_ids:
        user = plugin.user_repo.get_by_id(uid)
        if not user:
            continue
        user.premium_currency += amount_int
        plugin.user_repo.update(user)
        updated += 1
    yield event.plain_result(f"✅ 已向 {updated} 位用户每人发放 {amount_int} 高级货币")


async def deduct_all_coins(plugin: "FishingPlugin", event: GameEvent):
    """从所有注册用户扣除金币（不低于0）"""
    args = event.message_str.split(" ")
    if len(args) < 2:
        yield event.plain_result("❌ 请指定扣除的金币数量，例如：/全体扣除金币 1000")
        return
    amount = args[1]
    if not amount.isdigit() or int(amount) <= 0:
        yield event.plain_result("❌ 扣除数量必须是正整数，请检查后重试。")
        return
    amount_int = int(amount)
    user_ids = plugin.user_repo.get_all_user_ids()
    if not user_ids:
        yield event.plain_result("❌ 当前没有注册用户。")
        return
    affected = 0
    total_deducted = 0
    for uid in user_ids:
        user = plugin.user_repo.get_by_id(uid)
        if not user:
            continue
        if user.coins <= 0:
            continue
        deduct = min(user.coins, amount_int)
        if deduct <= 0:
            continue
        user.coins -= deduct
        plugin.user_repo.update(user)
        affected += 1
        total_deducted += deduct
    yield event.plain_result(
        f"✅ 已从 {affected} 位用户总计扣除 {total_deducted} 金币（每人至多 {amount_int}）"
    )


async def deduct_all_premium(plugin: "FishingPlugin", event: GameEvent):
    """从所有注册用户扣除高级货币（不低于0）"""
    args = event.message_str.split(" ")
    if len(args) < 2:
        yield event.plain_result(
            "❌ 请指定扣除的高级货币数量，例如：/全体扣除高级货币 100"
        )
        return
    amount = args[1]
    if not amount.isdigit() or int(amount) <= 0:
        yield event.plain_result("❌ 扣除数量必须是正整数，请检查后重试。")
        return
    amount_int = int(amount)
    user_ids = plugin.user_repo.get_all_user_ids()
    if not user_ids:
        yield event.plain_result("❌ 当前没有注册用户。")
        return
    affected = 0
    total_deducted = 0
    for uid in user_ids:
        user = plugin.user_repo.get_by_id(uid)
        if not user:
            continue
        if user.premium_currency <= 0:
            continue
        deduct = min(user.premium_currency, amount_int)
        if deduct <= 0:
            continue
        user.premium_currency -= deduct
        plugin.user_repo.update(user)
        affected += 1
        total_deducted += deduct
    yield event.plain_result(
        f"✅ 已从 {affected} 位用户总计扣除 {total_deducted} 高级货币（每人至多 {amount_int}）"
    )


async def reward_coins(plugin: "FishingPlugin", event: GameEvent):
    """奖励用户金币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查金币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定金币数量，例如：/奖励金币 @用户 1000 或 /奖励金币 @用户 一万"
        )
        return

    try:
        coins = parse_amount(args[2])
        if coins <= 0:
            yield event.plain_result("❌ 金币数量必须是正整数，请检查后重试。")
            return
    except ValueError as e:
        yield event.plain_result(f"❌ 数量格式错误：{str(e)}")
        return

    if (current_coins := plugin.user_service.get_user_currency(target_user_id)) is None:
        yield event.plain_result("❌ 用户不存在或未注册，请检查后重试。")
        return
    if result := plugin.user_service.modify_user_coins(
        target_user_id, int(current_coins.get("coins") + coins)
    ):
        yield event.plain_result(f"✅ 成功给用户 {target_user_id} 奖励 {coins} 金币")
    else:
        yield event.plain_result("❌ 出错啦！请稍后再试。")


async def deduct_coins(plugin: "FishingPlugin", event: GameEvent):
    """扣除用户金币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查金币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定金币数量，例如：/扣除金币 @用户 1000 或 /扣除金币 123456789 1000"
        )
        return

    coins = args[2]
    if not coins.isdigit():
        yield event.plain_result("❌ 金币数量必须是数字，请检查后重试。")
        return

    if (current_coins := plugin.user_service.get_user_currency(target_user_id)) is None:
        yield event.plain_result("❌ 用户不存在或未注册，请检查后重试。")
        return
    if int(coins) > current_coins.get("coins"):
        yield event.plain_result("❌ 扣除的金币数量不能超过用户当前拥有的金币数量")
        return
    if result := plugin.user_service.modify_user_coins(
        target_user_id, int(current_coins.get("coins") - int(coins))
    ):
        yield event.plain_result(f"✅ 成功扣除用户 {target_user_id} 的 {coins} 金币")
    else:
        yield event.plain_result("❌ 出错啦！请稍后再试。")


async def start_admin(plugin: "FishingPlugin", event: GameEvent):
    yield event.plain_result(await plugin.native_start_web())


async def stop_admin(plugin: "FishingPlugin", event: GameEvent):
    """关闭钓鱼后台管理"""
    yield event.plain_result(await plugin.native_stop_web())


async def sync_initial_data(plugin: "FishingPlugin", event: GameEvent):
    """从 initial_data.py 同步所有初始设定（道具、商店等）。"""
    try:
        plugin.data_setup_service.sync_all_initial_data()
        yield event.plain_result("✅ 所有初始设定数据同步成功！")
    except Exception as e:
        logger.error(f"同步初始设定数据时出错: {e}")
        yield event.plain_result(f"❌ 同步初始设定数据失败: {e}")


async def impersonate_start(plugin: "FishingPlugin", event: GameEvent):
    """管理员开始扮演一名用户。"""
    admin_id = event.get_sender_id()
    args = event.message_str.split(" ")

    # 如果已经在线，则显示当前状态
    if admin_id in plugin.impersonation_map:
        target_user_id = plugin.impersonation_map[admin_id]
        target_user = plugin.user_repo.get_by_id(target_user_id)
        nickname = target_user.nickname if target_user else "未知用户"
        yield event.plain_result(f"您当前正在代理用户: {nickname} ({target_user_id})")
        return

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(
            f"用法: /代理上线 <目标用户ID> 或 /代理上线 @用户\n{error_msg}"
        )
        return

    target_user = plugin.user_repo.get_by_id(target_user_id)
    if not target_user:
        yield event.plain_result("❌ 目标用户不存在。")
        return

    plugin.impersonation_map[admin_id] = target_user_id
    nickname = target_user.nickname
    yield event.plain_result(
        f"✅ 您已成功代理用户: {nickname} ({target_user_id})。\n现在您发送的所有游戏指令都将以该用户的身份执行。\n使用 /代理下线 结束代理。"
    )


async def impersonate_stop(plugin: "FishingPlugin", event: GameEvent):
    """管理员结束扮演用户。"""
    admin_id = event.get_sender_id()
    if admin_id in plugin.impersonation_map:
        del plugin.impersonation_map[admin_id]
        yield event.plain_result("✅ 您已成功结束代理。")
    else:
        yield event.plain_result("❌ 您当前没有在代理任何用户。")


async def reward_all_items(plugin: "FishingPlugin", event: GameEvent):
    """给所有注册用户发放道具"""
    args = event.message_str.split(" ")
    if len(args) < 4:
        yield event.plain_result(
            "❌ 请指定道具类型、道具ID和数量，例如：/全体发放道具 item 1 5"
        )
        return

    item_type = args[1]
    item_id_str = args[2]
    quantity_str = args[3]

    # 验证道具ID
    if not item_id_str.isdigit():
        yield event.plain_result("❌ 道具ID必须是数字，请检查后重试。")
        return
    item_id = int(item_id_str)

    # 验证数量
    if not quantity_str.isdigit() or int(quantity_str) <= 0:
        yield event.plain_result("❌ 数量必须是正整数，请检查后重试。")
        return
    quantity = int(quantity_str)

    # 验证道具类型
    valid_types = ["item", "bait", "rod", "accessory"]
    if item_type not in valid_types:
        yield event.plain_result(
            f"❌ 不支持的道具类型。支持的类型：{', '.join(valid_types)}"
        )
        return

    # 验证道具是否存在
    item_template = None
    if item_type == "item":
        item_template = plugin.item_template_repo.get_item_by_id(item_id)
    elif item_type == "bait":
        item_template = plugin.item_template_repo.get_bait_by_id(item_id)
    elif item_type == "rod":
        item_template = plugin.item_template_repo.get_rod_by_id(item_id)
    elif item_type == "accessory":
        item_template = plugin.item_template_repo.get_accessory_by_id(item_id)

    if not item_template:
        yield event.plain_result(f"❌ 道具不存在，请检查道具ID和类型。")
        return

    # 获取所有用户ID
    user_ids = plugin.user_repo.get_all_user_ids()
    if not user_ids:
        yield event.plain_result("❌ 当前没有注册用户。")
        return

    # 给所有用户发放道具
    success_count = 0
    failed_count = 0

    for user_id in user_ids:
        try:
            result = plugin.user_service.add_item_to_user_inventory(
                user_id, item_type, item_id, quantity
            )
            if result.get("success", False):
                success_count += 1
            else:
                failed_count += 1
        except Exception as e:
            failed_count += 1
            logger.error(f"给用户 {user_id} 发放道具失败: {e}")

    item_name = getattr(item_template, "name", f"ID:{item_id}")
    yield event.plain_result(
        f"✅ 全体发放道具完成！\n📦 道具：{item_name} x{quantity}\n✅ 成功：{success_count} 位用户\n❌ 失败：{failed_count} 位用户"
    )


async def replenish_fish_pools(plugin: "FishingPlugin", event: GameEvent):
    """补充鱼池 - 重置所有钓鱼区域的稀有鱼剩余数量"""
    try:
        # 获取所有钓鱼区域
        all_zones = plugin.inventory_repo.get_all_zones()
        
        if not all_zones:
            yield event.plain_result("❌ 没有找到任何钓鱼区域。")
            return
        
        # 重置所有有配额的区域的稀有鱼计数
        reset_count = 0
        zone_details = []
        
        for zone in all_zones:
            if zone.daily_rare_fish_quota > 0:  # 只重置有配额的区域
                zone.rare_fish_caught_today = 0
                plugin.inventory_repo.update_fishing_zone(zone)
                reset_count += 1
                zone_details.append(f"🎣 {zone.name}：配额 {zone.daily_rare_fish_quota} 条")
        
        if reset_count == 0:
            yield event.plain_result("❌ 没有找到任何有稀有鱼配额的钓鱼区域。")
            return
        
        # 构建结果消息
        result_msg = f"✅ 鱼池补充完成！已重置 {reset_count} 个钓鱼区域的稀有鱼剩余数量。\n\n"
        result_msg += "📋 重置详情：\n"
        result_msg += "\n".join(zone_details)
        result_msg += f"\n\n🔄 所有区域的稀有鱼(4星及以上)剩余数量已重置为满配额状态。"
        
        yield event.plain_result(result_msg)
        
        logger.info(f"管理员 {event.get_sender_id()} 执行了鱼池补充操作，重置了 {reset_count} 个钓鱼区域")
        
    except Exception as e:
        logger.error(f"补充鱼池时发生错误: {e}")
        yield event.plain_result(f"❌ 补充鱼池时发生错误：{str(e)}")
        return


async def grant_title(plugin: "FishingPlugin", event: GameEvent):
    """授予用户称号"""
    args = event.message_str.split(" ")
    
    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return
    
    # 检查称号名称参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定称号名称，例如：/授予称号 @用户 钓鱼大师 或 /授予称号 123456789 钓鱼大师"
        )
        return
    
    title_name = " ".join(args[2:])  # 支持称号名称中包含空格
    
    result = plugin.user_service.grant_title_to_user_by_name(target_user_id, title_name)
    yield event.plain_result(result["message"])


async def revoke_title(plugin: "FishingPlugin", event: GameEvent):
    """移除用户称号"""
    args = event.message_str.split(" ")
    
    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return
    
    # 检查称号名称参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定称号名称，例如：/移除称号 @用户 钓鱼大师 或 /移除称号 123456789 钓鱼大师"
        )
        return
    
    title_name = " ".join(args[2:])  # 支持称号名称中包含空格
    
    result = plugin.user_service.revoke_title_from_user_by_name(target_user_id, title_name)
    yield event.plain_result(result["message"])


async def create_title(plugin: "FishingPlugin", event: GameEvent):
    """创建自定义称号"""
    args = event.message_str.split(" ")
    
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定称号名称和描述，例如：/创建称号 称号名称 描述 [显示格式]\n"
            "显示格式可选，默认为 {name}，可以使用 {name} 和 {username} 占位符"
        )
        return
    
    title_name = args[1]
    description = " ".join(args[2:-1]) if len(args) > 3 and args[-1].startswith("{") else " ".join(args[2:])
    display_format = args[-1] if len(args) > 3 and args[-1].startswith("{") else "{name}"
    
    # 如果描述为空，使用默认值
    if not description:
        description = f"自定义称号：{title_name}"
    
    result = plugin.user_service.create_custom_title(title_name, description, display_format)
    yield event.plain_result(result["message"])

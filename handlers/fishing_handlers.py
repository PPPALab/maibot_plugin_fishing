from ..events import GameEvent
import logging
logger = logging.getLogger(__name__)
from ..core.utils import get_now
from ..utils import safe_datetime_handler, to_percentage, safe_get_file_path
from ..draw.pokedex import draw_pokedex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..engine import FishingPlugin


def _normalize_now_for(lst_time):
    """根据 lst_time 的时区信息，规范化当前时间的 tzinfo。"""
    now = get_now()
    if lst_time and lst_time.tzinfo is None and now.tzinfo is not None:
        return now.replace(tzinfo=None)
    if lst_time and lst_time.tzinfo is not None and now.tzinfo is None:
        return now.replace(tzinfo=lst_time.tzinfo)
    return now


def _compute_cooldown_seconds(base_seconds, equipped_accessory):
    """根据是否装备海洋之心动态计算冷却时间。"""
    if equipped_accessory and equipped_accessory.get("name") == "海洋之心":
        return base_seconds / 2
    return base_seconds


def _build_fish_message(result, fishing_cost):
    if result["success"]:
        fish = result['fish']
        # 构建品质显示
        quality_display = ""
        if fish.get('quality_level') == 1:
            quality_display = " ✨高品质"
        
        message = (
            f"🎣 恭喜你钓到了：{fish['name']}{quality_display}\n"
            f"✨稀有度：{'★' * fish['rarity']} \n"
            f"⚖️重量：{fish['weight']} 克\n"
            f"💰价值：{fish['value']} 金币\n"
            f"💸消耗：{fishing_cost} 金币/次"
        )
        if fish.get('quantity', 1) > 1:
            message += f"\n🐟 数量：{fish['quantity']} 条"
        released = fish.get('quantity', 1) - fish.get('stored_quantity', fish.get('quantity', 1))
        if released > 0:
            message += f"\n🌊 鱼塘容量不足，本次渔获放生 {released} 条"
        if "equipment_broken_messages" in result:
            for broken_msg in result["equipment_broken_messages"]:
                message += f"\n{broken_msg}"
        return message
    return f"{result['message']}\n💸消耗：{fishing_cost} 金币/次"


class FishingHandlers:
    def __init__(self, plugin: "FishingPlugin"):
        self.plugin = plugin
        self.user_service = plugin.user_service
        self.fishing_service = plugin.fishing_service
        self.inventory_service = plugin.inventory_service
        self.gacha_service = plugin.gacha_service
        self.market_service = plugin.market_service
        self.shop_service = plugin.shop_service
        self.item_template_repo = plugin.item_template_repo
        self.achievement_service = plugin.achievement_service
        self.aquarium_service = plugin.aquarium_service
        self.exchange_service = plugin.exchange_service

    def _get_fishing_cost(self, user):
        zone = self.plugin.inventory_repo.get_zone_by_id(user.fishing_zone_id)
        return zone.fishing_cost if zone else 10

    async def fish(self, event: GameEvent):
        """钓鱼"""
        user_id = self.plugin._get_effective_user_id(event)
        user = self.plugin.user_repo.get_by_id(user_id)
        if not user:
            yield event.plain_result("❌ 您还没有注册，请先使用 /注册 命令注册。")
            return
        # 检查用户钓鱼CD
        lst_time = user.last_fishing_time
        info = self.user_service.get_user_current_accessory(user_id)
        if info["success"] is False:
            yield event.plain_result(f"❌ 获取用户饰品信息失败：{info['message']}")
            return
        equipped_accessory = info.get("accessory")
        base_cooldown = self.plugin.game_config["fishing"]["cooldown_seconds"]
        cooldown_seconds = _compute_cooldown_seconds(base_cooldown, equipped_accessory)
        # 修复时区问题
        now = _normalize_now_for(lst_time)
        if lst_time and (now - lst_time).total_seconds() < cooldown_seconds:
            wait_time = cooldown_seconds - (now - lst_time).total_seconds()
            yield event.plain_result(f"⏳ 您还需要等待 {int(wait_time)} 秒才能再次钓鱼。")
            return
        fishing_cost = self._get_fishing_cost(user)
        result = self.fishing_service.go_fish(user_id)
        if not result:
            yield event.plain_result("❌ 出错啦！请稍后再试。")
            return
        yield event.plain_result(_build_fish_message(result, fishing_cost))

    async def auto_fish(self, event: GameEvent):
        """自动钓鱼"""
        user_id = self.plugin._get_effective_user_id(event)
        result = self.fishing_service.toggle_auto_fishing(user_id)
        yield event.plain_result(result["message"])

    async def fishing_area(self, event: GameEvent):
        """查看当前钓鱼区域"""
        user_id = self.plugin._get_effective_user_id(event)
        args = event.message_str.split(" ")
        if len(args) < 2:
            result = self.fishing_service.get_user_fishing_zones(user_id)
            if not result:
                yield event.plain_result("❌ 出错啦！请稍后再试。")
                return
            if not result.get("success"):
                yield event.plain_result(f"❌ 查看钓鱼区域失败：{result['message']}")
                return
            zones = result.get("zones", [])
            message = "【🌊 钓鱼区域】\n"
            for zone in zones:
                status_icons = []
                if zone["whether_in_use"]:
                    status_icons.append("✅")
                if not zone["is_active"]:
                    status_icons.append("🚫")
                if zone.get("requires_pass"):
                    status_icons.append("🔑")
                status_text = " ".join(status_icons) if status_icons else ""
                message += (
                    f"区域名称: {zone['name']} (ID: {zone['zone_id']}) {status_text}\n"
                )
                message += f"描述: {zone['description']}\n"
                message += f"💰 钓鱼消耗: {zone.get('fishing_cost', 10)} 金币/次\n"
                if zone.get("requires_pass"):
                    required_item_name = zone.get("required_item_name", "通行证")
                    message += f"🔑 需要 {required_item_name} 才能进入\n"
                if zone.get("available_from") or zone.get("available_until"):
                    message += "⏰ 开放时间: "
                    if zone.get("available_from") and zone.get("available_until"):
                        from_time = zone["available_from"].strftime("%Y-%m-%d %H:%M")
                        until_time = zone["available_until"].strftime("%Y-%m-%d %H:%M")
                        message += f"{from_time} 至 {until_time}\n"
                    elif zone.get("available_from"):
                        from_time = zone["available_from"].strftime("%Y-%m-%d %H:%M")
                        message += f"{from_time} 开始\n"
                    elif zone.get("available_until"):
                        until_time = zone["available_until"].strftime("%Y-%m-%d %H:%M")
                        message += f"至 {until_time} 结束\n"
                remaining_rare = max(
                    0, zone["daily_rare_fish_quota"] - zone["rare_fish_caught_today"]
                )
                if zone.get("daily_rare_fish_quota", 0) > 0:
                    message += f"剩余稀有鱼类数量: {remaining_rare}\n"
                message += "\n"
            message += "使用「/钓鱼区域 ID」命令切换钓鱼区域。\n"
            yield event.plain_result(message)
            return
        zone_id = args[1]
        if not zone_id.isdigit():
            yield event.plain_result("❌ 钓鱼区域 ID 必须是数字，请检查后重试。")
            return
        zone_id = int(zone_id)

        # 动态获取所有有效的区域ID
        all_zones = self.plugin.fishing_zone_service.get_all_zones()
        valid_zone_ids = [zone["id"] for zone in all_zones]

        if zone_id not in valid_zone_ids:
            yield event.plain_result(
                f"❌ 无效的钓鱼区域 ID。有效ID为: {', '.join(map(str, valid_zone_ids))}"
            )
            yield event.plain_result("💡 请使用「/钓鱼区域 <ID>」命令指定区域ID")
            return

        # 切换用户的钓鱼区域
        result = self.fishing_service.set_user_fishing_zone(user_id, zone_id)
        yield event.plain_result(result["message"] if result else "❌ 出错啦！请稍后再试。")

    async def fish_pokedex(self, event: GameEvent):
        """查看鱼类图鉴"""
        user_id = self.plugin._get_effective_user_id(event)
        args = event.message_str.split()
        page = 1
        if len(args) > 1 and args[1].isdigit():
            page = int(args[1])

        pokedex_data = self.fishing_service.get_user_pokedex(user_id)
        if not pokedex_data or not pokedex_data.get("success"):
            yield event.plain_result(
                f"❌ 查看图鉴失败: {pokedex_data.get('message', '未知错误')}"
            )
            return

        pokedex_list = pokedex_data.get("pokedex", [])
        if not pokedex_list:
            yield event.plain_result("❌ 您还没有捕捉到任何鱼类，快去钓鱼吧！")
            return

        user_info = self.plugin.user_repo.get_by_id(user_id)

        # 绘制图片
        output_path = safe_get_file_path(self.plugin, f"pokedex_{user_id}_page_{page}.png")

        try:
            await draw_pokedex(
                pokedex_data,
                {"nickname": user_info.nickname, "user_id": user_id},
                output_path,
                page=page,
                data_dir=self.plugin.runtime_dir,
            )
            yield event.image_result(output_path)
        except Exception as e:
            logger.error(f"绘制图鉴图片失败: {e}", exc_info=e)
            yield event.plain_result("❌ 绘制图鉴时发生错误，请稍后再试或联系管理员。")

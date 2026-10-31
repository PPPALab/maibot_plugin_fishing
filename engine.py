import os
import asyncio
import logging

logger = logging.getLogger(__name__)

from .events import GameEvent

# ==========================================================
# 导入所有仓储层 & 服务层（与旧版保持一致的精确导入）
# ==========================================================
from .core.repositories.sqlite_user_repo import SqliteUserRepository
from .core.repositories.sqlite_item_template_repo import SqliteItemTemplateRepository
from .core.repositories.sqlite_inventory_repo import SqliteInventoryRepository
from .core.repositories.sqlite_gacha_repo import SqliteGachaRepository
from .core.repositories.sqlite_market_repo import SqliteMarketRepository
from .core.repositories.sqlite_shop_repo import SqliteShopRepository
from .core.repositories.sqlite_log_repo import SqliteLogRepository
from .core.repositories.sqlite_achievement_repo import SqliteAchievementRepository
from .core.repositories.sqlite_user_buff_repo import SqliteUserBuffRepository
from .core.repositories.sqlite_exchange_repo import SqliteExchangeRepository # 新增交易所Repo
from .core.repositories.sqlite_red_packet_repo import SqliteRedPacketRepository # 新增红包Repo

from .core.services.data_setup_service import DataSetupService
from .core.services.item_template_service import ItemTemplateService
from .core.services.user_service import UserService
from .core.services.fishing_service import FishingService
from .core.services.inventory_service import InventoryService
from .core.services.shop_service import ShopService
from .core.services.market_service import MarketService
from .core.services.gacha_service import GachaService
from .core.services.achievement_service import AchievementService
from .core.services.game_mechanics_service import GameMechanicsService
from .core.services.effect_manager import EffectManager
from .core.services.fishing_zone_service import FishingZoneService
from .core.services.exchange_service import ExchangeService # 新增交易所Service
from .core.services.sicbo_service import SicboService # 新增骰宝Service
from .core.services.red_packet_service import RedPacketService # 新增红包Service
from .core.services import item_effects  # 效果模块包

from .core.database.migration import run_migrations

# ==========================================================
# 导入所有指令函数
# ==========================================================
from .handlers import (
    admin_handlers, 
    common_handlers, 
    inventory_handlers, 
    fishing_handlers, 
    market_handlers, 
    social_handlers, 
    gacha_handlers, 
    aquarium_handlers, 
    sicbo_handlers,
    red_packet_handlers,
)
from .handlers.fishing_handlers import FishingHandlers
from .handlers.exchange_handlers import ExchangeHandlers


class FishingPlugin:

    def __init__(self, data_dir: str, config: dict, runtime_dir: str):

        # --- 1. 加载配置 ---
        # 从新的嵌套结构中读取配置
        tax_config = config.get("tax", {})
        self.is_tax = tax_config.get("is_tax", True)  # 是否开启税收
        self.threshold = tax_config.get("threshold", 100000)  # 起征点
        self.step_coins = tax_config.get("step_coins", 100000)
        self.step_rate = tax_config.get("step_rate", 0.01)
        self.max_rate = tax_config.get("max_rate", 0.2)  # 最大税率
        self.min_rate = tax_config.get("min_rate", 0.001)  # 最小税率
        self.area2num = config.get("area2num", 2000)
        self.area3num = config.get("area3num", 500)
        
        # 插件ID
        self.plugin_id = "fishing.astrbot-native"

        # --- 1.1. 数据与临时文件路径管理 ---
        self.data_dir = data_dir
        self.runtime_dir = runtime_dir
        self.tmp_dir = os.path.join(self.runtime_dir, "tmp")
        os.makedirs(self.tmp_dir, exist_ok=True)

        db_path = os.path.join(self.data_dir, "astrbot-fishing.sqlite3")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        # --- 1.2. 配置数据完整性检查注释 ---
        # 以下配置项必须在此处从 AstrBotConfig 中提取并放入 game_config，
        # 以确保所有服务在接收 game_config 时能够正确读取配置值
        # 
        # 配置数据流：_conf_schema.json → AstrBotConfig (config) → game_config → 各个服务
        # 
        # 从框架读取嵌套配置
        # 注意：框架会自动解析 _conf_schema.json 中的嵌套对象
        fishing_config = config.get("fishing", {})
        steal_config = config.get("steal", {})
        electric_fish_config = config.get("electric_fish", {})
        game_global_config = config.get("game", {})
        user_config = config.get("user", {})
        market_config = config.get("market", {})
        sell_prices_config = config.get("sell_prices", {})
        
        # 直接从框架获取 exchange 配置（不重建）
        exchange_config = config.get("exchange", {})
        if not exchange_config:
            # 如果框架返回空字典，说明嵌套配置不被支持，手动构建默认值
            logger.warning("[CONFIG] Exchange config is empty, using defaults")
            exchange_config = {
                "account_fee": 100000,
                "capacity": 1000,
                "tax_rate": 0.05,
                "volatility": {"dried_fish": 0.08, "fish_roe": 0.12, "fish_oil": 0.10},
                "event_chance": 0.1,
                "max_change_rate": 0.2,
                "min_price": 1,
                "max_price": 1000000,
                "sentiment_weights": {"panic": 0.1, "pessimistic": 0.2, "neutral": 0.4, "optimistic": 0.2, "euphoric": 0.1},
                "merge_window_minutes": 30,
                "initial_prices": {"dried_fish": 6000, "fish_roe": 12000, "fish_oil": 10000}
            }
        else:
            logger.info(f"[CONFIG] Exchange capacity loaded: {exchange_config.get('capacity', 'NOT SET')}")
        
        self.game_config = {
            "fishing": {
                "cost": config.get("fish_cost", 10), 
                "cooldown_seconds": fishing_config.get("cooldown_seconds", 180)
            },
            "quality_bonus_max_chance": fishing_config.get("quality_bonus_max_chance", 0.35),
            "steal": {
                "cooldown_seconds": steal_config.get("cooldown_seconds", 14400)
            },
            "electric_fish": {
                "enabled": electric_fish_config.get("enabled", True),
                "cooldown_seconds": electric_fish_config.get("cooldown_seconds", 7200),
                "base_success_rate": electric_fish_config.get("base_success_rate", 0.6),
                "failure_penalty_max_rate": electric_fish_config.get("failure_penalty_max_rate", 0.5)
            },
            "wipe_bomb": {
                "max_attempts_per_day": game_global_config.get("wipe_bomb_attempts", 3)
            },
            "wheel_of_fate_daily_limit": game_global_config.get("wheel_of_fate_daily_limit", 3),
            "daily_reset_hour": game_global_config.get("daily_reset_hour", 0),
            "user": {
                "initial_coins": user_config.get("initial_coins", 200)
            },
            "market": {
                "listing_tax_rate": market_config.get("listing_tax_rate", 0.05)
            },
            "tax": {
                "is_tax": self.is_tax,
                "threshold": self.threshold,
                "step_coins": self.step_coins,
                "step_rate": self.step_rate,
                "min_rate": self.min_rate,
                "max_rate": self.max_rate
            },
            "pond_upgrades": [
                { "from": 480, "to": 999, "cost": 50000 },
                { "from": 999, "to": 9999, "cost": 500000 },
                { "from": 9999, "to": 99999, "cost": 50000000 },
                { "from": 99999, "to": 999999, "cost": 5000000000 },
            ],
            "sell_prices": {
                "rod": { 
                    "1": sell_prices_config.get("by_rarity_1", 100),
                    "2": sell_prices_config.get("by_rarity_2", 500),
                    "3": sell_prices_config.get("by_rarity_3", 2000),
                    "4": sell_prices_config.get("by_rarity_4", 5000),
                    "5": sell_prices_config.get("by_rarity_5", 10000),
                    "6": sell_prices_config.get("by_rarity_6", 20000),
                    "7": sell_prices_config.get("by_rarity_7", 50000),
                    "8": sell_prices_config.get("by_rarity_8", 100000),
                    "9": sell_prices_config.get("by_rarity_9", 200000),
                    "10": sell_prices_config.get("by_rarity_10", 500000)
                },
                "accessory": { 
                    "1": sell_prices_config.get("by_rarity_1", 100),
                    "2": sell_prices_config.get("by_rarity_2", 500),
                    "3": sell_prices_config.get("by_rarity_3", 2000),
                    "4": sell_prices_config.get("by_rarity_4", 5000),
                    "5": sell_prices_config.get("by_rarity_5", 10000),
                    "6": sell_prices_config.get("by_rarity_6", 20000),
                    "7": sell_prices_config.get("by_rarity_7", 50000),
                    "8": sell_prices_config.get("by_rarity_8", 100000),
                    "9": sell_prices_config.get("by_rarity_9", 200000),
                    "10": sell_prices_config.get("by_rarity_10", 500000)
                },
                "refine_multiplier": {
                    "1": 1.0, "2": 1.6, "3": 3.0, "4": 6.0, "5": 12.0,
                    "6": 25.0, "7": 55.0, "8": 125.0, "9": 280.0, "10": 660.0
                }
            },
            "exchange": exchange_config  # 直接使用框架的配置
        }
        
        self.game_config["tax"].update(tax_config)
        self.game_config["sicbo"] = config.get("sicbo", {})

        # 初始化数据库模式
        plugin_root_dir = os.path.dirname(__file__)
        migrations_path = os.path.join(plugin_root_dir, "core", "database", "migrations")
        # Native lifecycle runs all migrations before opening the initialization UoW.

        # --- 2. 组合根：实例化所有仓储层 ---
        self.user_repo = SqliteUserRepository(db_path)
        self.item_template_repo = SqliteItemTemplateRepository(db_path)
        self.inventory_repo = SqliteInventoryRepository(db_path)
        self.gacha_repo = SqliteGachaRepository(db_path)
        self.market_repo = SqliteMarketRepository(db_path)
        self.shop_repo = SqliteShopRepository(db_path)
        self.log_repo = SqliteLogRepository(db_path)
        self.achievement_repo = SqliteAchievementRepository(db_path)
        self.buff_repo = SqliteUserBuffRepository(db_path)
        self.exchange_repo = SqliteExchangeRepository(db_path)

        # --- 3. 组合根：实例化所有服务层，并注入依赖 ---
        # 3.1 核心服务必须在效果管理器之前实例化，以解决依赖问题
        self.fishing_zone_service = FishingZoneService(self.item_template_repo, self.inventory_repo, self.game_config)
        self.game_mechanics_service = GameMechanicsService(self.user_repo, self.log_repo, self.inventory_repo,
                                                          self.item_template_repo, self.buff_repo, self.game_config)

        # 3.3 实例化其他核心服务
        self.gacha_service = GachaService(self.gacha_repo, self.user_repo, self.inventory_repo, self.item_template_repo,
                                         self.log_repo, self.achievement_repo)
        # UserService 依赖 GachaService，因此在 GachaService 之后实例化
        self.user_service = UserService(self.user_repo, self.log_repo, self.inventory_repo, self.item_template_repo, self.gacha_service, self.game_config, self.achievement_repo)
        self.inventory_service = InventoryService(
            self.inventory_repo,
            self.user_repo,
            self.item_template_repo,
            None,  # 先设为None，稍后设置
            self.game_mechanics_service,
            self.game_config,
        )
        self.shop_service = ShopService(self.item_template_repo, self.inventory_repo, self.user_repo, self.shop_repo, self.game_config)
        # MarketService 依赖 exchange_repo
        self.market_service = MarketService(self.market_repo, self.inventory_repo, self.user_repo, self.log_repo,
                                           self.item_template_repo, self.exchange_repo, self.game_config)
        self.achievement_service = AchievementService(self.achievement_repo, self.user_repo, self.inventory_repo,
                                                     self.item_template_repo, self.log_repo)
        self.fishing_service = FishingService(
            self.user_repo,
            self.inventory_repo,
            self.item_template_repo,
            self.log_repo,
            self.buff_repo,
            self.fishing_zone_service,
            self.game_config,
        )
        
        # 导入并初始化水族箱服务
        from .core.services.aquarium_service import AquariumService
        self.aquarium_service = AquariumService(
            self.inventory_repo,
            self.user_repo,
            self.item_template_repo
        )
        
        # 初始化交易所服务
        self.exchange_service = ExchangeService(self.user_repo, self.exchange_repo, self.game_config, self.log_repo, self.market_service)
        
        # 初始化骰宝服务
        self.sicbo_service = SicboService(self.user_repo, self.log_repo, self.game_config)
        
        # 设置骰宝服务的消息发送回调
        self.sicbo_service.set_message_callback(self.send_announcement)
        
        # 初始化红包服务
        self.red_packet_repo = SqliteRedPacketRepository(db_path)
        self.red_packet_service = RedPacketService(self.red_packet_repo, self.user_repo)
        
        # 初始化交易所处理器
        self.exchange_handlers = ExchangeHandlers(self)
        
        #初始化钓鱼处理器
        self.fishing_handlers = FishingHandlers(self)


        # 3.2 实例化效果管理器并自动注册所有效果（需要在fishing_service之后）
        self.effect_manager = EffectManager()
        self.effect_manager.discover_and_register(
            effects_package=item_effects,
            dependencies={
                "user_repo": self.user_repo, 
                "buff_repo": self.buff_repo,
                "game_mechanics_service": self.game_mechanics_service,
                "fishing_service": self.fishing_service,
                "log_repo": self.log_repo,
                "game_config": self.game_config,
            },
        )
        
        # 设置inventory_service的effect_manager
        self.inventory_service.effect_manager = self.effect_manager

        self.item_template_service = ItemTemplateService(self.item_template_repo, self.gacha_repo)

        # --- 5. 初始化核心游戏数据 ---
        data_setup_service = DataSetupService(
            self.item_template_repo, self.gacha_repo, self.shop_repo
        )
        data_setup_service.setup_initial_data()
        # 确保初始道具存在（在已有数据库上也可幂等执行）
        data_setup_service.create_initial_items()

        # 商店完全由后台管控，不再自动种子化

        # --- 6. (临时) 实例化数据服务，供调试命令使用 ---
        self.data_setup_service = data_setup_service

        # --- Web后台配置 ---
        self.web_admin_task = None
        webui_config = config.get("webui", {})
        self.secret_key = webui_config.get("secret_key")
        if not self.secret_key:
            self.secret_key = None
        self.port = webui_config.get("port", 7777)

        # 管理员扮演功能
        self.impersonation_map = {}

    def _get_effective_user_id(self, event: GameEvent):
        """获取在当前上下文中应当作为指令执行者的用户ID。
        - 默认返回消息发送者ID
        - 若发送者是管理员且已开启代理，则返回被代理用户ID
        注意：仅在非管理员指令中调用该方法；管理员指令应使用真实管理员ID。
        """
        admin_id = event.get_sender_id()
        return self.impersonation_map.get(admin_id, admin_id)

    async def register_user(self, event: GameEvent):
        """注册成为钓鱼游戏玩家，开始你的钓鱼之旅"""
        async for r in common_handlers.register_user(self, event):
            yield r

    async def fish(self, event: GameEvent):
        """进行一次钓鱼，消耗金币并获得鱼类或物品"""
        async for r in self.fishing_handlers.fish(event):
            yield r

    async def sign_in(self, event: GameEvent):
        """每日签到领取奖励，连续签到奖励更丰厚"""
        async for r in common_handlers.sign_in(self, event):
            yield r

    async def auto_fish(self, event: GameEvent):
        """开启或关闭自动钓鱼功能，自动钓鱼会定期帮你钓鱼"""
        async for r in self.fishing_handlers.auto_fish(event): 
            yield r

    async def fishing_log(self, event: GameEvent):
        """查看你的钓鱼历史记录"""
        async for r in common_handlers.fishing_log(self, event):
            yield r

    async def state(self, event: GameEvent):
        """查看你的游戏状态，包括金币、等级、装备等信息"""
        async for r in common_handlers.state(self, event):
            yield r

    async def fishing_help(self, event: GameEvent):
        """查看钓鱼游戏的帮助信息和所有可用命令"""
        async for r in common_handlers.fishing_help(self, event):
            yield r

    async def user_backpack(self, event: GameEvent):
        """查看你的背包，包含所有物品和装备"""
        async for r in inventory_handlers.user_backpack(self, event):
            yield r

    async def pond(self, event: GameEvent):
        """查看你的鱼塘，查看所有已钓到的鱼"""
        async for r in inventory_handlers.pond(self, event):
            yield r

    async def peek_pond(self, event: GameEvent):
        """偷看别人的鱼塘，查看其他玩家的鱼。用法：偷看鱼塘 @用户"""
        async for r in inventory_handlers.peek_pond(self, event):
            yield r

    async def pond_capacity(self, event: GameEvent):
        """查看当前鱼塘容量和升级信息"""
        async for r in inventory_handlers.pond_capacity(self, event):
            yield r

    async def upgrade_pond(self, event: GameEvent):
        """升级鱼塘容量，可以存放更多的鱼"""
        async for r in inventory_handlers.upgrade_pond(self, event):
            yield r

    async def aquarium(self, event: GameEvent):
        """查看你的水族箱，欣赏展示的珍贵鱼类"""
        async for r in aquarium_handlers.aquarium(self, event):
            yield r

    async def add_to_aquarium(self, event: GameEvent):
        """将鱼从鱼塘放入水族箱展示。用法：放入水族箱 鱼的编号"""
        async for r in aquarium_handlers.add_to_aquarium(self, event):
            yield r

    async def remove_from_aquarium(self, event: GameEvent):
        """将鱼从水族箱移回鱼塘。用法：移出水族箱 鱼的编号"""
        async for r in aquarium_handlers.remove_from_aquarium(self, event):
            yield r

    async def upgrade_aquarium(self, event: GameEvent):
        """升级水族箱容量，可以展示更多珍贵鱼类"""
        async for r in aquarium_handlers.upgrade_aquarium(self, event):
            yield r

    async def rod(self, event: GameEvent):
        """查看你拥有的所有鱼竿"""
        async for r in inventory_handlers.rod(self, event):
            yield r

    async def refine_equipment(self, event: GameEvent):
        """精炼装备提升属性。用法：精炼 装备编号"""
        async for r in inventory_handlers.refine_equipment(self, event):
            yield r

    async def sell_equipment(self, event: GameEvent):
        """出售装备换取金币。用法：出售 装备编号"""
        async for r in inventory_handlers.sell_equipment(self, event):
            yield r

    async def bait(self, event: GameEvent):
        """查看你拥有的所有鱼饵"""
        async for r in inventory_handlers.bait(self, event):
            yield r

    async def items(self, event: GameEvent):
        """查看你拥有的所有道具"""
        async for r in inventory_handlers.items(self, event):
            yield r

    async def open_all_money_bags(self, event: GameEvent):
        """一次性打开所有钱袋，获得金币"""
        async for r in inventory_handlers.open_all_money_bags(self, event):
            yield r

    async def accessories(self, event: GameEvent):
        """查看你拥有的所有饰品"""
        async for r in inventory_handlers.accessories(self, event):
            yield r

    async def lock_equipment(self, event: GameEvent):
        """锁定装备防止误操作。用法：锁定 装备编号"""
        async for r in inventory_handlers.lock_equipment(self, event):
            yield r

    async def unlock_equipment(self, event: GameEvent):
        """解锁已锁定的装备。用法：解锁 装备编号"""
        async for r in inventory_handlers.unlock_equipment(self, event):
            yield r

    async def use_equipment(self, event: GameEvent):
        """使用或装备物品。用法：使用 物品编号"""
        async for r in inventory_handlers.use_equipment(self, event):
            yield r

    async def coins(self, event: GameEvent):
        """查看你当前拥有的金币数量"""
        async for r in inventory_handlers.coins(self, event):
            yield r

    async def transfer_coins(self, event: GameEvent):
        """转账金币给其他玩家。用法：转账 @用户 金额"""
        async for r in common_handlers.transfer_coins(self, event):
            yield r

    async def update_nickname(self, event: GameEvent):
        """更新你的游戏昵称。用法：更新昵称 新昵称"""
        async for r in common_handlers.update_nickname(self, event):
            yield r

    async def premium(self, event: GameEvent):
        """查看你当前拥有的高级货币（钻石/星石）数量"""
        async for r in inventory_handlers.premium(self, event):
            yield r

    async def fishing_area(self, event: GameEvent):
        """查看所有钓鱼区域和切换钓鱼区域。用法：钓鱼区域 [区域编号]"""
        async for r in self.fishing_handlers.fishing_area(event):
            yield r

    async def fish_pokedex(self, event: GameEvent):
        """查看鱼类图鉴，了解所有可钓到的鱼"""
        async for r in self.fishing_handlers.fish_pokedex(event): 
            yield r

    async def sell_all(self, event: GameEvent):
        """卖出鱼塘中所有的鱼，换取金币"""
        async for r in market_handlers.sell_all(self, event):
            yield r

    async def sell_keep(self, event: GameEvent):
        """卖出鱼塘中的鱼，但保留指定数量。用法：保留卖出 保留数量"""
        async for r in market_handlers.sell_keep(self, event):
            yield r

    async def sell_everything(self, event: GameEvent):
        """卖掉所有可以出售的物品，包括鱼、装备等"""
        async for r in market_handlers.sell_everything(self, event):
            yield r

    async def sell_by_rarity(self, event: GameEvent):
        """按稀有度出售鱼。用法：出售稀有度 星级"""
        async for r in market_handlers.sell_by_rarity(self, event):
            yield r

    async def sell_all_rods(self, event: GameEvent):
        """出售所有未装备且未锁定的鱼竿"""
        async for r in market_handlers.sell_all_rods(self, event):
            yield r

    async def sell_all_accessories(self, event: GameEvent):
        """出售所有未装备且未锁定的饰品"""
        async for r in market_handlers.sell_all_accessories(self, event):
            yield r

    async def shop(self, event: GameEvent):
        """查看所有可用的商店"""
        async for r in market_handlers.shop(self, event):
            yield r

    async def buy_in_shop(self, event: GameEvent):
        """从商店购买商品。用法：商店购买 商品编号 [数量]"""
        async for r in market_handlers.buy_in_shop(self, event):
            yield r

    async def market(self, event: GameEvent):
        """查看玩家市场中的所有上架商品"""
        async for r in market_handlers.market(self, event):
            yield r

    async def list_any(self, event: GameEvent):
        """将物品上架到市场出售。用法：上架 物品编号 价格"""
        async for r in market_handlers.list_any(self, event):
            yield r

    async def buy_item(self, event: GameEvent):
        """从市场购买玩家上架的商品。用法：购买 订单编号"""
        async for r in market_handlers.buy_item(self, event):
            yield r

    async def my_listings(self, event: GameEvent):
        """查看你在市场上架的所有商品"""
        async for r in market_handlers.my_listings(self, event):
            yield r

    async def delist_item(self, event: GameEvent):
        """从市场下架你上架的商品。用法：下架 订单编号"""
        async for r in market_handlers.delist_item(self, event):
            yield r

    async def gacha(self, event: GameEvent):
        """进行一次抽卡，有机会获得稀有装备和道具"""
        async for r in gacha_handlers.gacha(self, event):
            yield r

    async def ten_gacha(self, event: GameEvent):
        """进行十次连续抽卡，有保底机制"""
        async for r in gacha_handlers.ten_gacha(self, event):
            yield r

    async def view_gacha_pool(self, event: GameEvent):
        """查看当前卡池中的所有物品及其概率"""
        async for r in gacha_handlers.view_gacha_pool(self, event):
            yield r

    async def gacha_history(self, event: GameEvent):
        """查看你的抽卡历史记录"""
        async for r in gacha_handlers.gacha_history(self, event):
            yield r

    async def wipe_bomb(self, event: GameEvent):
        """使用擦弹道具，有机会重置保底计数"""
        async for r in gacha_handlers.wipe_bomb(self, event):
            yield r

    async def wipe_bomb_history(self, event: GameEvent):
        """查看你的擦弹历史记录"""
        async for r in gacha_handlers.wipe_bomb_history(self, event):
            yield r

    async def wheel_of_fate_start(self, event: GameEvent):
        """开始命运之轮游戏"""
        async for r in gacha_handlers.start_wheel_of_fate(self, event):
            yield r

    async def wheel_of_fate_continue(self, event: GameEvent):
        """在命运之轮游戏中选择继续冒险"""
        async for r in gacha_handlers.continue_wheel_of_fate(self, event):
            yield r

    async def wheel_of_fate_stop(self, event: GameEvent):
        """在命运之轮游戏中选择放弃并结算奖励"""
        async for r in gacha_handlers.stop_wheel_of_fate(self, event):
            yield r

    async def send_red_packet(self, event: GameEvent):
        """发送红包。用法：发红包 [金额] [数量] [类型] [口令]"""
        async for r in red_packet_handlers.send_red_packet(self, event):
            yield r

    async def claim_red_packet(self, event: GameEvent):
        """领取红包。用法：领红包 [口令]"""
        async for r in red_packet_handlers.claim_red_packet(self, event):
            yield r

    async def list_red_packets(self, event: GameEvent):
        """查看当前群组可领取的红包列表"""
        async for r in red_packet_handlers.list_red_packets(self, event):
            yield r

    async def red_packet_details(self, event: GameEvent):
        """查看红包详情。用法：红包详情 [红包ID]"""
        async for r in red_packet_handlers.red_packet_details(self, event):
            yield r

    async def revoke_red_packet(self, event: GameEvent):
        """撤回红包并退还未领取的金额。用法：撤回红包 [红包ID]"""
        async for r in red_packet_handlers.revoke_red_packet(self, event):
            yield r

    async def start_sicbo(self, event: GameEvent):
        """开启骰宝游戏，倒计时120秒供玩家下注"""
        async for r in sicbo_handlers.start_sicbo_game(self, event):
            yield r

    async def bet_big(self, event: GameEvent):
        """鸭大（总点数11-17）。用法：鸭大 金额"""
        async for r in sicbo_handlers.bet_big(self, event):
            yield r

    async def bet_small(self, event: GameEvent):
        """鸭小（总点数4-10）。用法：鸭小 金额"""
        async for r in sicbo_handlers.bet_small(self, event):
            yield r

    async def bet_odd(self, event: GameEvent):
        """鸭单（总点数为奇数）。用法：鸭单 金额"""
        async for r in sicbo_handlers.bet_odd(self, event):
            yield r

    async def bet_even(self, event: GameEvent):
        """鸭双（总点数为偶数）。用法：鸭双 金额"""
        async for r in sicbo_handlers.bet_even(self, event):
            yield r

    async def bet_triple(self, event: GameEvent):
        """鸭豹子（三个骰子相同）。用法：鸭豹子 金额"""
        async for r in sicbo_handlers.bet_triple(self, event):
            yield r

    async def bet_one_point(self, event: GameEvent):
        """鸭一点（骰子出现1）。用法：鸭一点 金额"""
        async for r in sicbo_handlers.bet_one_point(self, event):
            yield r

    async def bet_two_point(self, event: GameEvent):
        """鸭二点（骰子出现2）。用法：鸭二点 金额"""
        async for r in sicbo_handlers.bet_two_point(self, event):
            yield r

    async def bet_three_point(self, event: GameEvent):
        """鸭三点（骰子出现3）。用法：鸭三点 金额"""
        async for r in sicbo_handlers.bet_three_point(self, event):
            yield r

    async def bet_four_point(self, event: GameEvent):
        """鸭四点（骰子出现4）。用法：鸭四点 金额"""
        async for r in sicbo_handlers.bet_four_point(self, event):
            yield r

    async def bet_five_point(self, event: GameEvent):
        """鸭五点（骰子出现5）。用法：鸭五点 金额"""
        async for r in sicbo_handlers.bet_five_point(self, event):
            yield r

    async def bet_six_point(self, event: GameEvent):
        """鸭六点（骰子出现6）。用法：鸭六点 金额"""
        async for r in sicbo_handlers.bet_six_point(self, event):
            yield r

    async def bet_4_points(self, event: GameEvent):
        """鸭总点数4点。用法：鸭4点 金额"""
        async for r in sicbo_handlers.bet_4_points(self, event):
            yield r

    async def bet_5_points(self, event: GameEvent):
        """鸭总点数5点。用法：鸭5点 金额"""
        async for r in sicbo_handlers.bet_5_points(self, event):
            yield r

    async def bet_6_points(self, event: GameEvent):
        """鸭总点数6点。用法：鸭6点 金额"""
        async for r in sicbo_handlers.bet_6_points(self, event):
            yield r

    async def bet_7_points(self, event: GameEvent):
        """鸭总点数7点。用法：鸭7点 金额"""
        async for r in sicbo_handlers.bet_7_points(self, event):
            yield r

    async def bet_8_points(self, event: GameEvent):
        """押总点数8点。用法：押8点 金额"""
        async for r in sicbo_handlers.bet_8_points(self, event):
            yield r

    async def bet_9_points(self, event: GameEvent):
        """押总点数9点。用法：押9点 金额"""
        async for r in sicbo_handlers.bet_9_points(self, event):
            yield r

    async def bet_10_points(self, event: GameEvent):
        """押总点数10点。用法：押10点 金额"""
        async for r in sicbo_handlers.bet_10_points(self, event):
            yield r

    async def bet_11_points(self, event: GameEvent):
        """押总点数11点。用法：押11点 金额"""
        async for r in sicbo_handlers.bet_11_points(self, event):
            yield r

    async def bet_12_points(self, event: GameEvent):
        """押总点数12点。用法：押12点 金额"""
        async for r in sicbo_handlers.bet_12_points(self, event):
            yield r

    async def bet_13_points(self, event: GameEvent):
        """押总点数13点。用法：押13点 金额"""
        async for r in sicbo_handlers.bet_13_points(self, event):
            yield r

    async def bet_14_points(self, event: GameEvent):
        """押总点数14点。用法：押14点 金额"""
        async for r in sicbo_handlers.bet_14_points(self, event):
            yield r

    async def bet_15_points(self, event: GameEvent):
        """押总点数15点。用法：押15点 金额"""
        async for r in sicbo_handlers.bet_15_points(self, event):
            yield r

    async def bet_16_points(self, event: GameEvent):
        """押总点数16点。用法：押16点 金额"""
        async for r in sicbo_handlers.bet_16_points(self, event):
            yield r

    async def bet_17_points(self, event: GameEvent):
        """押总点数17点。用法：押17点 金额"""
        async for r in sicbo_handlers.bet_17_points(self, event):
            yield r

    async def sicbo_status(self, event: GameEvent):
        """查看当前骰宝游戏状态"""
        async for r in sicbo_handlers.sicbo_status(self, event):
            yield r

    async def my_bets(self, event: GameEvent):
        """查看本局游戏中的下注情况"""
        async for r in sicbo_handlers.my_bets(self, event):
            yield r

    async def sicbo_help(self, event: GameEvent):
        """查看骰宝游戏帮助"""
        async for r in sicbo_handlers.sicbo_help(self, event):
            yield r

    async def sicbo_odds(self, event: GameEvent):
        """查看骰宝赔率详情"""
        async for r in sicbo_handlers.sicbo_odds(self, event):
            yield r

    async def ranking(self, event: GameEvent):
        """查看金币、鱼类等各种排行榜"""
        async for r in social_handlers.ranking(self, event):
            yield r

    async def steal_fish(self, event: GameEvent):
        """偷取其他玩家的鱼，但有失败风险。用法：偷鱼 @用户"""
        async for r in social_handlers.steal_fish(self, event):
            yield r

    async def electric_fish(self, event: GameEvent):
        """对其他玩家使用电鱼，成功可获得金币。用法：电鱼 @用户"""
        async for r in social_handlers.electric_fish(self, event):
            yield r

    async def dispel_protection(self, event: GameEvent):
        """驱散目标玩家的保护效果。用法：驱灵 @用户"""
        async for r in social_handlers.dispel_protection(self, event):
            yield r

    async def view_titles(self, event: GameEvent):
        """查看你拥有的所有称号"""
        async for r in social_handlers.view_titles(self, event):
            yield r

    async def use_title(self, event: GameEvent):
        """装备或卸下称号。用法：使用称号 称号编号"""
        async for r in social_handlers.use_title(self, event):
            yield r

    async def view_achievements(self, event: GameEvent):
        """查看你的成就完成情况"""
        async for r in social_handlers.view_achievements(self, event):
            yield r

    async def tax_record(self, event: GameEvent):
        """查看你的税收缴纳记录"""
        async for r in social_handlers.tax_record(self, event):
            yield r

    async def exchange_main(self, event: GameEvent):
        """查看交易所信息和进行交易。用法：交易所 [买入/卖出] [商品] [数量]"""
        async for r in self.exchange_handlers.exchange_main(event):
            yield r

    async def view_inventory(self, event: GameEvent):
        """查看你在交易所的持仓情况"""
        async for r in self.exchange_handlers.view_inventory(event):
            yield r

    async def clear_inventory(self, event: GameEvent):
        """清空交易所持仓，将所有商品按当前价格卖出"""
        async for r in self.exchange_handlers.clear_inventory(event):
            yield r

    async def modify_coins(self, event: GameEvent):
        """[管理员] 修改指定玩家的金币数量。用法：修改金币 @用户 数量"""
        async for r in admin_handlers.modify_coins(self, event):
            yield r

    async def modify_premium(self, event: GameEvent):
        """[管理员] 修改指定玩家的高级货币数量。用法：修改高级货币 @用户 数量"""
        async for r in admin_handlers.modify_premium(self, event):
            yield r

    async def reward_premium(self, event: GameEvent):
        """[管理员] 奖励指定玩家高级货币。用法：奖励高级货币 @用户 数量"""
        async for r in admin_handlers.reward_premium(self, event):
            yield r

    async def deduct_premium(self, event: GameEvent):
        """[管理员] 扣除指定玩家的高级货币。用法：扣除高级货币 @用户 数量"""
        async for r in admin_handlers.deduct_premium(self, event):
            yield r

    async def reward_all_coins(self, event: GameEvent):
        """[管理员] 给所有玩家奖励金币。用法：全体奖励金币 数量"""
        async for r in admin_handlers.reward_all_coins(self, event):
            yield r

    async def reward_all_premium(self, event: GameEvent):
        """[管理员] 给所有玩家奖励高级货币。用法：全体奖励高级货币 数量"""
        async for r in admin_handlers.reward_all_premium(self, event):
            yield r

    async def deduct_all_coins(self, event: GameEvent):
        """[管理员] 扣除所有玩家的金币。用法：全体扣除金币 数量"""
        async for r in admin_handlers.deduct_all_coins(self, event):
            yield r

    async def deduct_all_premium(self, event: GameEvent):
        """[管理员] 扣除所有玩家的高级货币。用法：全体扣除高级货币 数量"""
        async for r in admin_handlers.deduct_all_premium(self, event):
            yield r

    async def reward_coins(self, event: GameEvent):
        """[管理员] 奖励指定玩家金币。用法：奖励金币 @用户 数量"""
        async for r in admin_handlers.reward_coins(self, event):
            yield r

    async def deduct_coins(self, event: GameEvent):
        """[管理员] 扣除指定玩家的金币。用法：扣除金币 @用户 数量"""
        async for r in admin_handlers.deduct_coins(self, event):
            yield r

    async def start_admin(self, event: GameEvent):
        """[管理员] 启动Web后台管理服务器"""
        async for r in admin_handlers.start_admin(self, event):
            yield r

    async def stop_admin(self, event: GameEvent):
        """[管理员] 关闭Web后台管理服务器"""
        async for r in admin_handlers.stop_admin(self, event):
            yield r

    async def sync_initial_data(self, event: GameEvent):
        """[管理员] 同步游戏初始设定数据到数据库"""
        async for r in admin_handlers.sync_initial_data(self, event):
            yield r

    async def grant_title(self, event: GameEvent):
        """[管理员] 授予用户称号。用法：授予称号 @用户 称号名称"""
        async for r in admin_handlers.grant_title(self, event):
            yield r

    async def revoke_title(self, event: GameEvent):
        """[管理员] 移除用户称号。用法：移除称号 @用户 称号名称"""
        async for r in admin_handlers.revoke_title(self, event):
            yield r

    async def create_title(self, event: GameEvent):
        """[管理员] 创建自定义称号。用法：创建称号 称号名称 描述 [显示格式]"""
        async for r in admin_handlers.create_title(self, event):
            yield r

    async def impersonate_start(self, event: GameEvent):
        """[管理员] 代理其他玩家进行操作。用法：代理上线 @用户"""
        async for r in admin_handlers.impersonate_start(self, event):
            yield r

    async def impersonate_stop(self, event: GameEvent):
        """[管理员] 结束代理模式，恢复为管理员身份"""
        async for r in admin_handlers.impersonate_stop(self, event):
            yield r

    async def reward_all_items(self, event: GameEvent):
        """[管理员] 给所有玩家发放道具。用法：全体发放道具 道具ID 数量"""
        async for r in admin_handlers.reward_all_items(self, event):
            yield r

    async def replenish_fish_pools(self, event: GameEvent):
        """[管理员] 重置所有钓鱼区域的稀有鱼剩余数量"""
        async for r in admin_handlers.replenish_fish_pools(self, event):
            yield r

    async def cleanup_red_packets(self, event: GameEvent):
        """[管理员] 清理红包。用法：/清理红包 [所有]（不带参数清理当前群，带"所有"清理全局）"""
        async for r in red_packet_handlers.cleanup_red_packets(self, event):
            yield r

    async def force_settle_sicbo(self, event: GameEvent):
        """[管理员] 跳过倒计时直接结算当前骰宝游戏"""
        async for r in sicbo_handlers.force_settle_sicbo(self, event):
            yield r

    async def set_sicbo_countdown(self, event: GameEvent):
        """[管理员] 设置骰宝游戏倒计时时间"""
        async for r in sicbo_handlers.set_sicbo_countdown(self, event):
            yield r

    async def set_sicbo_mode(self, event: GameEvent):
        """[管理员] 设置骰宝消息模式（图片/文本）"""
        async for r in sicbo_handlers.set_sicbo_mode(self, event):
            yield r

    async def send_announcement(self, session_info, result):
        return None

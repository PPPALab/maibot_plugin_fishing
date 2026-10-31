"""Typed MaiBot settings; preserve existing TOML paths and runtime dict values."""
from typing import Literal

from maibot_sdk import Field, PluginConfigBase
from maibot_sdk.config import generate_plugin_config_schema
from pydantic import field_validator

from .lan import normalize_allowed_hosts


def setting(default, label, description, *, widget=None, step=None, **constraints):
    ui = {'label': label, 'hint': description}
    if widget:
        ui['x-widget'] = widget
    if step is not None:
        ui['step'] = step
    elif isinstance(default, float):
        ui['step'] = 0.001
    return Field(default=default, description=description, json_schema_extra=ui, **constraints)


class SettingsSection(PluginConfigBase):
    # Old dict settings allowed custom keys. Keep them during normalization/save.
    model_config = {'extra': 'allow', 'validate_assignment': True}


class PluginSectionConfig(SettingsSection):
    """宿主启用状态和配置版本。通常无需修改版本号。"""
    __ui_label__ = '插件状态'
    __ui_order__ = 0
    enabled: bool = setting(True, '启用插件', '关闭后暂停本插件的游戏命令和调度')
    config_version: str = Field(default='1', description='配置结构版本',
                                json_schema_extra={'label': '配置版本', 'disabled': True})


class WebConfig(SettingsSection):
    """独立钓鱼后台，默认关闭/仅本机；Docker局域网访问需同时配置监听、主机白名单和端口映射。"""
    __ui_label__ = '钓鱼管理后台'
    __ui_order__ = 1
    enabled: bool = setting(False, '开启钓鱼后台', '开启前须配置登录密码；请勿暴露公网')
    listen_host: Literal['127.0.0.1', '0.0.0.0'] = setting('127.0.0.1', '容器内监听地址',
        'Docker局域网访问选0.0.0.0；宿主机ports仍须绑定具体局域网IP', widget='select')
    allowed_hosts: list[str] = Field(default_factory=lambda: ['localhost', '127.0.0.1', '[::1]'],
        description='允许访问后台的精确主机列表', json_schema_extra={'label': '后台主机白名单',
        'hint': '加入Docker宿主机的局域网IP，如192.168.1.10；不填http://、端口或*'})
    port: int = setting(7777, '后台端口', '容器内本地监听端口，不会自动修改端口映射', ge=1024, le=65535)
    secret_key: str = setting('', '后台登录密码', '开启后台时不能为空或全空白；建议使用独立的强密码，勿公开分享', widget='password')

    @field_validator('allowed_hosts')
    @classmethod
    def validate_allowed_hosts(cls, hosts):
        return normalize_allowed_hosts(hosts)


class TaxConfig(SettingsSection):
    """税收参数；比例使用0–1的小数，例如0.05表示5%。"""
    __ui_label__ = '税收与转账'
    __ui_order__ = 3
    is_tax: bool = setting(True, '开启每日税收', '是否启用游戏每日税收')
    threshold: int = setting(1000000, '税收起征点', '超过该金币余额后按税收规则计算', ge=0)
    step_coins: int = setting(100000, '税率递增金币步长', '每个税率阶梯对应的金币数量，必须大于0', ge=1)
    step_rate: float = setting(.01, '每阶梯增加税率', '比例：0.01表示增加1%', ge=0, le=1)
    min_rate: float = setting(.001, '最低税率', '比例：0.001表示0.1%', ge=0, le=1)
    max_rate: float = setting(.2, '最高税率', '比例：0.2表示20%，不得低于最低税率', ge=0, le=1)
    transfer_tax_rate: float = setting(.05, '转账手续费率', '用户之间转账的扣税比例，0.05表示5%', ge=0, le=1)


class FishingSettings(SettingsSection):
    """钓鱼冷却与品质概率。"""
    __ui_label__ = '钓鱼规则'
    __ui_order__ = 4
    cooldown_seconds: int = setting(180, '钓鱼冷却（秒）', '两次钓鱼之间的基础等待时间', ge=0)
    quality_bonus_max_chance: float = setting(.35, '高品质概率上限', '0–1之间的小数，0.35表示35%', ge=0, le=1)


class StealConfig(SettingsSection):
    """偷鱼玩法参数。"""
    __ui_label__ = '偷鱼规则'
    __ui_order__ = 5
    cooldown_seconds: int = setting(14400, '偷鱼冷却（秒）', '默认14400秒，即4小时', ge=0)


class ElectricFishConfig(SettingsSection):
    """电鱼成功率及失败惩罚；比例使用0–1的小数。"""
    __ui_label__ = '电鱼规则'
    __ui_order__ = 6
    enabled: bool = setting(True, '启用电鱼', '是否允许玩家使用电鱼功能')
    cooldown_seconds: int = setting(7200, '电鱼冷却（秒）', '默认7200秒，即2小时', ge=0)
    base_success_rate: float = setting(.6, '基础成功率', '0.6表示60%', ge=0, le=1)
    failure_penalty_max_rate: float = setting(.5, '失败最大金币扣除比例', '0.5表示最多扣除当前金币的50%', ge=0, le=1)


class GameConfig(SettingsSection):
    """全局重置时间和小游戏每日次数。"""
    __ui_label__ = '每日重置与小游戏'
    __ui_order__ = 7
    daily_reset_hour: int = setting(0, '每日重置小时', '使用游戏时区，0表示午夜，范围0–23', ge=0, le=23)
    wheel_of_fate_daily_limit: int = setting(3, '命运之轮每日次数', '每个玩家每天可进入的次数', ge=0)
    wipe_bomb_attempts: int = setting(3, '擦弹每日基础次数', '不含道具增加的额外次数', ge=0)


class UserConfig(SettingsSection):
    """仅影响新注册玩家，不会重置已有玩家余额。"""
    __ui_label__ = '新玩家'
    __ui_order__ = 8
    initial_coins: int = setting(200, '注册初始金币', '新注册玩家获得的金币，不影响现有用户', ge=0)


class MarketConfig(SettingsSection):
    """玩家市场收费设置。"""
    __ui_label__ = '玩家市场'
    __ui_order__ = 9
    listing_tax_rate: float = setting(.05, '上架手续费率', '0.05表示5%，下架不退还已收手续费', ge=0, le=1)


class SellPricesConfig(SettingsSection):
    """鱼竿、饰品的系统回收基础价；精炼乘数沿用原规则。"""
    __ui_label__ = '装备回收价格'
    __ui_order__ = 10
    by_rarity_1: int = setting(100, '1星基础回收价', '单位：金币', ge=0)
    by_rarity_2: int = setting(500, '2星基础回收价', '单位：金币', ge=0)
    by_rarity_3: int = setting(2000, '3星基础回收价', '单位：金币', ge=0)
    by_rarity_4: int = setting(5000, '4星基础回收价', '单位：金币', ge=0)
    by_rarity_5: int = setting(10000, '5星基础回收价', '单位：金币', ge=0)
    by_rarity_6: int = setting(20000, '6星基础回收价', '沿用原运行时默认值，单位：金币', ge=0)
    by_rarity_7: int = setting(50000, '7星基础回收价', '沿用原运行时默认值，单位：金币', ge=0)
    by_rarity_8: int = setting(100000, '8星基础回收价', '沿用原运行时默认值，单位：金币', ge=0)
    by_rarity_9: int = setting(200000, '9星基础回收价', '沿用原运行时默认值，单位：金币', ge=0)
    by_rarity_10: int = setting(500000, '10星基础回收价', '沿用原运行时默认值，单位：金币', ge=0)


class VolatilityConfig(SettingsSection):
    """交易所每种商品的基础价格波动参数。"""
    __ui_label__ = '交易所：商品波动'
    dried_fish: float = setting(.08, '鱼干波动率', '比例：0.08表示8%', ge=0, le=1)
    fish_roe: float = setting(.12, '鱼卵波动率', '比例：0.12表示12%', ge=0, le=1)
    fish_oil: float = setting(.1, '鱼油波动率', '比例：0.1表示10%', ge=0, le=1)


class SentimentConfig(SettingsSection):
    """市场情绪的相对抽样权重；不要求合计为1，但总和须大于0。"""
    __ui_label__ = '交易所：市场情绪权重'
    panic: float = setting(.1, '恐慌', '相对抽样权重，必须非负', ge=0)
    pessimistic: float = setting(.2, '悲观', '相对抽样权重，必须非负', ge=0)
    neutral: float = setting(.4, '中性', '相对抽样权重，必须非负', ge=0)
    optimistic: float = setting(.2, '乐观', '相对抽样权重，必须非负', ge=0)
    euphoric: float = setting(.1, '狂热', '相对抽样权重，必须非负', ge=0)


class InitialPricesConfig(SettingsSection):
    """交易所初始化参考价，不会直接改写已有市场历史。"""
    __ui_label__ = '交易所：初始价格'
    dried_fish: int = setting(6000, '鱼干初始价格', '单位：金币', ge=1)
    fish_roe: int = setting(12000, '鱼卵初始价格', '单位：金币', ge=1)
    fish_oil: int = setting(10000, '鱼油初始价格', '单位：金币', ge=1)


class ExchangeConfig(SettingsSection):
    """交易所价格更新、费用和风险参数。"""
    __ui_label__ = '交易所'
    __ui_order__ = 11
    update_timing: str = setting('9:00, 15:00, 21:00', '价格更新时间', '逗号分隔的时刻，如9:00, 15:00, 21:00')
    account_fee: int = setting(100000, '交易所开户费', '单位：金币', ge=0)
    capacity: int = setting(1000, '默认持仓容量', '每个账户默认可持有的商品份数', ge=1)
    tax_rate: float = setting(.05, '交易所税率', '按原规则对正利润征税，0.05表示5%', ge=0, le=1)
    volatility: VolatilityConfig = Field(default_factory=VolatilityConfig)
    event_chance: float = setting(.1, '价格事件触发概率', '0.1表示10%', ge=0, le=1)
    max_change_rate: float = setting(.2, '单次价格最大变化率', '0.2表示20%', ge=0, le=1)
    min_price: int = setting(1, '商品最低价格', '单位：金币，不能高于最高价格', ge=1)
    max_price: int = setting(1000000, '商品最高价格', '单位：金币，不能低于最低价格', ge=1)
    sentiment_weights: SentimentConfig = Field(default_factory=SentimentConfig)
    merge_window_minutes: int = setting(30, '价格更新合并窗口（分钟）', '合并临近更新时刻，避免短时间内重复更新', ge=0)
    initial_prices: InitialPricesConfig = Field(default_factory=InitialPricesConfig)


class SicboConfig(SettingsSection):
    """骰宝房间默认设置。"""
    __ui_label__ = '骰宝'
    __ui_order__ = 15
    countdown_seconds: int = setting(60, '下注倒计时（秒）', '每局默认下注时间，范围10–300秒', ge=10, le=300)
    min_bet: int = setting(100, '最低下注金币', '单次下注下限，必须大于0', ge=1)
    max_bet: int = setting(1000000, '最高下注金币', '不得低于最低下注金币', ge=1)
    message_mode: Literal['image', 'text'] = setting('image', '消息显示模式', 'image为图片，text为文字', widget='select')


class FishingConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    admin_ids: list[str] = Field(default_factory=list, description='可信 QQ 数字账号；不从群头衔推断管理员',
                                json_schema_extra={'label': '管理员QQ账号', 'hint': '逐项添加数字QQ号，仅此列表具有游戏管理员权限'})
    webui: WebConfig = Field(default_factory=WebConfig)
    tax: TaxConfig = Field(default_factory=TaxConfig)
    fishing: FishingSettings = Field(default_factory=FishingSettings)
    steal: StealConfig = Field(default_factory=StealConfig)
    electric_fish: ElectricFishConfig = Field(default_factory=ElectricFishConfig)
    game: GameConfig = Field(default_factory=GameConfig)
    user: UserConfig = Field(default_factory=UserConfig)
    market: MarketConfig = Field(default_factory=MarketConfig)
    sell_prices: SellPricesConfig = Field(default_factory=SellPricesConfig)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    sicbo: SicboConfig = Field(default_factory=SicboConfig)
    fish_cost: int = setting(10, '基础钓鱼费用', '区域配置的初始化参考值；已有区域实际费用请在钓鱼后台修改', ge=0)
    area2num: int = setting(2000, '区域2稀有鱼配额', '区域配置的初始化参考值；已有区域请在钓鱼后台管理', ge=0)
    area3num: int = setting(500, '区域3稀有鱼配额', '区域配置的初始化参考值；已有区域请在钓鱼后台管理', ge=0)

    def checked(self):
        if any(not account.isdecimal() for account in self.admin_ids):
            raise ValueError('admin_ids 必须是 QQ 数字账号')
        if self.webui.enabled and not self.webui.secret_key.strip():
            raise ValueError('启用后台必须设置非空登录密码')
        if self.tax.min_rate > self.tax.max_rate:
            raise ValueError('最低税率不能超过最高税率')
        if self.sicbo.min_bet > self.sicbo.max_bet:
            raise ValueError('骰宝最低下注不能超过最高下注')
        if self.exchange.min_price > self.exchange.max_price:
            raise ValueError('交易所最低价格不能超过最高价格')
        if sum(getattr(self.exchange.sentiment_weights, name) for name in SentimentConfig.model_fields) <= 0:
            raise ValueError('市场情绪权重之和必须大于0')
        return self.model_dump()


def expand_nested_settings(schema):
    """SDK handles one level; Host UI also supports dotted nested section paths.

    Render exchange mappings as real primitive fields without changing TOML or
    saved JSON structure. Use only the SDK's public schema generator.
    """
    schema['sections']['general']['order'] = 2
    for index, (name, model) in enumerate((('volatility', VolatilityConfig),
                                           ('sentiment_weights', SentimentConfig),
                                           ('initial_prices', InitialPricesConfig)), start=12):
        schema['sections']['exchange']['fields'].pop(name)
        section = generate_plugin_config_schema(model)['sections']['general']
        section.update(name=f'exchange.{name}', title=model.__ui_label__,
                       description=model.__doc__, order=index)
        schema['sections'][section['name']] = section
    schema['layout'] = {'type': 'tabs', 'tabs': [
        {'id': 'general', 'title': '通用与后台', 'sections': ['plugin', 'general', 'webui'], 'order': 0},
        {'id': 'fishing', 'title': '钓鱼与社交', 'sections': ['fishing', 'user', 'steal', 'electric_fish', 'market'], 'order': 1},
        {'id': 'economy', 'title': '税收与回收', 'sections': ['tax', 'sell_prices'], 'order': 2},
        {'id': 'games', 'title': '每日与骰宝', 'sections': ['game', 'sicbo'], 'order': 3},
        {'id': 'exchange', 'title': '交易所', 'sections': ['exchange', 'exchange.volatility',
                                                           'exchange.sentiment_weights', 'exchange.initial_prices'], 'order': 4},
    ]}
    return schema

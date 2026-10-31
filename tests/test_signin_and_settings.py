import pytest

from fishing_port import create_plugin
from fishing_port.core.database.unit_of_work import atomic
from test_native_adapter import command, load_plugin


@pytest.mark.asyncio
async def test_fresh_signin_explains_unconfigured_daily_supply(tmp_path):
    plugin = await load_plugin(tmp_path)
    try:
        await command(plugin, '注册')
        with atomic(plugin.db_path):
            before = plugin.engine.user_repo.get_by_id('12345')
            initial_coins, initial_premium = before.coins, before.premium_currency
        plugin.ctx.send.messages.clear()
        await command(plugin, '签到')
        messages = '\n'.join(item[1] for item in plugin.ctx.send.messages if item[0] == 'text')
        assert '签到成功' in messages
        assert '卡池不存在或卡池为空' not in messages, messages
        assert '每日补给未配置奖励' in messages
        with atomic(plugin.db_path):
            user = plugin.engine.user_repo.get_by_id('12345')
            assert initial_coins + 100 <= user.coins <= initial_coins + 300
            assert user.premium_currency == initial_premium + 1
            assert not plugin.engine.gacha_repo.get_pool_items(3)  # no invented prizes
            assert plugin.engine.log_repo.get_gacha_records_count_today('12345', 3) == 0
    finally:
        await plugin.on_unload()


def test_settings_schema_has_no_opaque_objects():
    schema = create_plugin().get_webui_config_schema()

    def walk(node):
        if isinstance(node, dict):
            assert node.get('type') != 'object' or node.get('properties'), node
            if 'ui_type' in node:
                assert not isinstance(node.get('default'), dict), node
                assert node.get('label') != node.get('name'), node
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(schema)
    shown = [section for tab in schema['layout']['tabs'] for section in tab['sections']]
    assert sorted(shown) == sorted(schema['sections'])


@pytest.mark.asyncio
async def test_configured_supply_is_awarded_once_without_extra_cost(tmp_path):
    plugin = await load_plugin(tmp_path)
    try:
        await command(plugin, '注册')
        with atomic(plugin.db_path):
            initial = plugin.engine.user_repo.get_by_id('12345').coins
            plugin.engine.gacha_repo.add_pool_item(3, {'item_type': 'coins', 'item_id': 0, 'quantity': 17, 'weight': 1})
        plugin.ctx.send.messages.clear()
        await command(plugin, '签到')
        message = '\n'.join(item[1] for item in plugin.ctx.send.messages if item[0] == 'text')
        assert '每日补给: 你获得了 17 金币' in message
        with atomic(plugin.db_path):
            balance = plugin.engine.user_repo.get_by_id('12345').coins
            assert initial + 117 <= balance <= initial + 317
            assert plugin.engine.log_repo.get_gacha_records_count_today('12345', 3) == 1
        await command(plugin, '签到')
        with atomic(plugin.db_path):
            assert plugin.engine.user_repo.get_by_id('12345').coins == balance
            assert plugin.engine.log_repo.get_gacha_records_count_today('12345', 3) == 1
    finally:
        await plugin.on_unload()


def test_typed_settings_keep_legacy_values_and_runtime_dict_shape():
    from fishing_port.config import FishingConfig
    config = FishingConfig(admin_ids=['12345'], fishing={'cooldown_seconds': 7, 'custom_flag': True},
                           sell_prices={'by_rarity_8': 123456},
                           exchange={'volatility': {'fish_oil': .22}, 'sentiment_weights': {'neutral': .7}})
    values = config.checked()
    assert values['fishing']['cooldown_seconds'] == 7
    assert values['fishing']['custom_flag'] is True
    assert values['sell_prices']['by_rarity_8'] == 123456
    assert values['exchange']['volatility']['fish_oil'] == .22
    assert values['exchange']['sentiment_weights']['neutral'] == .7
    assert values['admin_ids'] == ['12345']


def test_schema_paths_resolve_to_actual_config_values():
    from fishing_port.config import FishingConfig
    schema = create_plugin().get_webui_config_schema()
    values = FishingConfig().checked()
    # Host folds SDK root fields into virtual 'general' for the UI and unfolds
    # them again on save; the TOML remains flat. Do not override its name/path.
    general = schema['sections']['general']
    assert general['name'] == 'general'
    values['general'] = {name: values.pop(name) for name in general['fields']}
    for section in schema['sections'].values():
        current = values
        for part in filter(None, section['name'].split('.')):
            current = current[part]
        for name, field in section['fields'].items():
            assert current[name] == field['default']
            assert field.get('description')
    assert schema['sections']['webui']['fields']['secret_key']['ui_type'] == 'password'
    assert schema['sections']['sicbo']['fields']['message_mode']['choices'] == ['image', 'text']
    assert schema['sections']['fishing']['fields']['cooldown_seconds']['ui_type'] == 'number'


@pytest.mark.parametrize('values', [
    {'fishing': {'cooldown_seconds': -1}},
    {'electric_fish': {'base_success_rate': 2}},
    {'tax': {'step_coins': 0}},
    {'sicbo': {'message_mode': 'bad'}},
    {'game': {'daily_reset_hour': 24}},
    {'exchange': {'volatility': {'fish_oil': -.1}}},
])
def test_invalid_settings_rejected(values):
    from fishing_port.config import FishingConfig
    with pytest.raises(ValueError):
        FishingConfig(**values).checked()


@pytest.mark.asyncio
async def test_empty_supply_does_not_consume_claim_before_admin_configures_it(tmp_path):
    plugin = await load_plugin(tmp_path)
    try:
        await command(plugin, '注册')
        await command(plugin, '签到')
        with atomic(plugin.db_path):
            assert plugin.engine.log_repo.get_gacha_records_count_today('12345', 3) == 0
            plugin.engine.gacha_repo.add_pool_item(3, {'item_type': 'coins', 'item_id': 0, 'quantity': 17, 'weight': 1})
            before = plugin.engine.user_repo.get_by_id('12345').coins
        await command(plugin, '抽卡 3')
        with atomic(plugin.db_path):
            assert plugin.engine.user_repo.get_by_id('12345').coins == before + 17
            assert plugin.engine.log_repo.get_gacha_records_count_today('12345', 3) == 1
        await command(plugin, '抽卡 3')
        with atomic(plugin.db_path):
            assert plugin.engine.user_repo.get_by_id('12345').coins == before + 17
            assert plugin.engine.log_repo.get_gacha_records_count_today('12345', 3) == 1
    finally:
        await plugin.on_unload()

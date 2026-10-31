import sys
import tomllib

import pytest


@pytest.mark.parametrize('section', ['', '[plugin]\nenabled = false\n', 'plugin = {enabled = false}\n'])
def test_legacy_config_repaired_before_host_validation(tmp_path, section):
    from fishing_port.config_upgrade import repair_legacy_config
    path = tmp_path / 'config.toml'
    # root fields must never become members of [plugin].
    before = section + '\n[webui]\nport = 7890\nsecret_key = "keep-me"\n'
    if not section.startswith('['):
        before = 'admin_ids = ["12345"]\nfish_cost = 37\n' + before
    path.write_text(before)
    old = tomllib.loads(before)
    assert repair_legacy_config(path)
    after = tomllib.loads(path.read_text())
    assert after.pop('plugin')['config_version'] == '1'
    assert after == {k: v for k, v in old.items() if k != 'plugin'}
    if 'plugin' in old:
        assert tomllib.loads(path.read_text())['plugin']['enabled'] is False
    assert list(tmp_path.glob('config.toml.before-version-*.bak'))[0].read_text() == before
    assert not repair_legacy_config(path)
    assert len(list(tmp_path.glob('*.bak'))) == 1


def test_constructor_repairs_real_plugin_directory_before_host_reads(tmp_path, monkeypatch):
    module = sys.modules['fishing_port']
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'plugin.py'))
    path = tmp_path / 'config.toml'
    path.write_text('admin_ids = ["12345"]\n')
    module.FishingPlugin()
    assert tomllib.loads(path.read_text())['plugin']['config_version'] == '1'


def test_invalid_config_never_overwritten(tmp_path):
    from fishing_port.config_upgrade import repair_legacy_config
    path = tmp_path / 'config.toml'
    path.write_text('admin_ids = [')
    with pytest.raises(ValueError):
        repair_legacy_config(path)
    assert path.read_text() == 'admin_ids = ['


def test_existing_version_and_disabled_setting_untouched(tmp_path):
    from fishing_port.config_upgrade import repair_legacy_config
    path = tmp_path / 'config.toml'
    before = '[plugin]\nenabled = false\nconfig_version = "1"\n'
    path.write_text(before)
    assert not repair_legacy_config(path)
    assert path.read_text() == before


def test_config_comments_unknown_fields_and_empty_version_preserved(tmp_path):
    from fishing_port.config_upgrade import repair_legacy_config
    path = tmp_path / 'config.toml'
    path.write_text('# keep root comment\nadmin_ids = ["7"] # admins\n[plugin]\n'
                    'enabled = false\nconfig_version = ""\n[custom]\nfuture = "keep"\n')
    assert repair_legacy_config(path)
    result = path.read_text()
    assert '# keep root comment' in result and '# admins' in result
    assert tomllib.loads(result)['custom']['future'] == 'keep'
    assert tomllib.loads(result)['plugin'] == {'enabled': False, 'config_version': '1'}


def test_config_symlink_never_modified(tmp_path):
    from fishing_port.config_upgrade import repair_legacy_config
    target = tmp_path / 'target.toml'
    target.write_text('admin_ids = []\n')
    path = tmp_path / 'config.toml'
    path.symlink_to(target)
    with pytest.raises(ValueError, match='符号链接'):
        repair_legacy_config(path)
    assert target.read_text() == 'admin_ids = []\n'

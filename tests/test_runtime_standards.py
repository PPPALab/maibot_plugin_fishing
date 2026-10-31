from pathlib import Path

import pytest
from maibot_sdk.context import PluginContext, PluginPaths

from fishing_port import create_plugin
from fishing_port.config import FishingConfig
from fishing_port.core.database.unit_of_work import atomic, connection
from test_native_adapter import Sender, command


def configured_plugin(tmp_path):
    p = create_plugin()
    context = PluginContext('github.PPPALab.maibot_plugin_fishing', paths=PluginPaths(tmp_path / 'data', tmp_path / 'runtime'))
    context.send = Sender()
    p._set_context(context)
    p._plugin_config_instance = FishingConfig()
    return p


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['raise', 'rollback'])
async def test_failed_initialization_never_accepts_commands(tmp_path, monkeypatch, failure):
    from fishing_port.core.services.data_setup_service import DataSetupService

    p = configured_plugin(tmp_path)
    original = DataSetupService.create_initial_items
    calls = 0

    def fail_existing_items(service):
        nonlocal calls
        calls += 1
        original(service)
        if calls == 2:
            if failure == 'raise':
                raise RuntimeError('初始化故障')
            connection(p.db_path).rollback()

    monkeypatch.setattr(DataSetupService, 'create_initial_items', fail_existing_items)
    try:
        with pytest.raises(RuntimeError):
            await p.on_load()
        assert not p.accepting
        assert p.process_lock is None
        assert p.engine is None
    finally:
        await p.on_unload()


@pytest.mark.asyncio
async def test_render_and_cache_use_host_runtime_path(tmp_path, monkeypatch):
    from fishing_port import media

    async def offline(*args):
        raise ValueError('离线测试')

    monkeypatch.setattr(media, 'download', offline)
    p = configured_plugin(tmp_path)
    try:
        await p.on_load()
        assert Path(p.engine.tmp_dir).is_relative_to(tmp_path / 'runtime')
        await command(p, '注册')
        await command(p, '状态')
        image_paths = list((tmp_path / 'runtime').rglob('*.png'))
        assert image_paths
        assert (tmp_path / 'runtime' / 'avatar_cache').is_dir()
        assert not list((tmp_path / 'data').rglob('*.png'))
        with atomic(p.db_path):
            assert p.engine.user_repo.get_by_id('12345')
    finally:
        await p.on_unload()

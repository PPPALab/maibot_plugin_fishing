"""Repair only the missing legacy version envelope, before Host validates it.

Called during instance construction, not on_load (which is too late).
No settings are reset or moved between TOML tables.
"""
import fcntl
import logging
import os
from pathlib import Path
import stat
import tempfile
import uuid

import tomlkit

logger = logging.getLogger(__name__)


def repair_legacy_config(path):
    path = Path(path)
    if not path.exists():
        return False  # Host creates a fresh file from config_model.
    if path.is_symlink():
        raise ValueError('拒绝自动修改符号链接配置；请先备份并手动迁移')
    with path.with_suffix('.upgrade.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = path.read_bytes()
        document = tomlkit.parse(original.decode('utf-8'))
        section = document.get('plugin')
        if section is not None and not isinstance(section, dict):
            raise ValueError('[plugin] 必须是 TOML 配置表；未修改原文件')
        if section is not None and str(section.get('config_version') or '').strip():
            return False
        if section is None:
            section = tomlkit.table()
            section['enabled'] = True
            document['plugin'] = section
        section['config_version'] = '1'
        replacement = tomlkit.dumps(document).encode('utf-8')
        # Both backup and temporary file are private (config may contain secrets).
        backup = path.with_name(path.name + '.before-version-' + uuid.uuid4().hex + '.bak')
        with backup.open('xb') as handle:
            os.chmod(backup, 0o600)
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor, temporary = tempfile.mkstemp(prefix='.config-upgrade-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(replacement)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            if path.read_bytes() != original:
                raise RuntimeError('配置在迁移时发生变化，已中止写回；请重试')
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        logger.info('旧钓鱼配置已补齐 plugin.config_version；原设置保留，备份: %s', backup.name)
        return True

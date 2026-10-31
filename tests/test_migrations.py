from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from fishing_port.core.database import migration

MIGRATIONS = Path(migration.__file__).with_name('migrations')


@pytest.mark.parametrize('start', range(43))
def test_upgrade_every_version(tmp_path, start):
    partial = tmp_path / 'partial'
    partial.mkdir()
    for path in sorted(MIGRATIONS.glob('[0-9][0-9][0-9]_*.py'))[:start]:
        (partial / path.name).symlink_to(path)
    db_path = str(tmp_path / 'test.sqlite3')
    migration.run_migrations(db_path, str(partial))
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT version FROM schema_version').fetchone()[0] == start
    migration.run_migrations(db_path, str(MIGRATIONS))
    migration.run_migrations(db_path, str(MIGRATIONS))
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 42
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_failed_migration_cannot_commit_early(tmp_path, monkeypatch):
    path = str(tmp_path / 'test.sqlite3')
    def broken(cursor):
        cursor.execute('CREATE TABLE must_rollback (value TEXT)')
        cursor.connection.commit()
        raise RuntimeError('crash after attempted inner commit')
    monkeypatch.setattr(migration.importlib, 'import_module', lambda name: SimpleNamespace(up=broken))
    with pytest.raises(RuntimeError):
        migration.run_migrations(path, str(MIGRATIONS))
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 0
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='must_rollback'").fetchone()

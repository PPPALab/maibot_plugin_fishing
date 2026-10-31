"""Offline backup/restore for THIS plugin only. Never touches the MaiBot database.

Backup uses the SQLite backup API and works while the plugin runs. Restore requires
the exclusive process lock, validates schema/integrity, and snapshots the old DB.
"""
import argparse
from datetime import datetime, timezone
import fcntl
from pathlib import Path
import sqlite3

DB_NAME = 'astrbot-fishing.sqlite3'


def validate(db):
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('SQLite 完整性检查失败')
    version = db.execute('SELECT version FROM schema_version').fetchone()[0]
    if version not in (40, 41, 42):
        raise ValueError('仅接受本插件 schema 40/41/42；不兼容 fishing2/MaiBot 数据库')
    if version >= 41:
        db.execute('SELECT reset_time FROM fishing_quota_reset LIMIT 1').fetchall()
    db.execute('SELECT key, value FROM port_state LIMIT 1').fetchall()
    if db.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('数据库存在外键损坏')


def snapshot(source, output):
    output = Path(output)
    # Do not overwrite any existing user file.
    with output.open('xb'):
        pass
    try:
        with sqlite3.connect(output) as destination:
            source.backup(destination)
            validate(destination)
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def backup(data_dir, output):
    path = Path(data_dir).resolve() / DB_NAME
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as source:
        validate(source)
        snapshot(source, output)


def restore(data_dir, source_file):
    directory = Path(data_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / DB_NAME
    source_path = Path(source_file).resolve()
    if source_path == target:
        raise ValueError('恢复源不能是目标数据库自身')
    with (directory / '.astrbot-fishing.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True) as source:
            validate(source)
            if target.exists():
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                backup(directory, directory / f'before-restore-{stamp}.sqlite3')
            with sqlite3.connect(target) as destination:
                source.backup(destination)
                validate(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'restore'])
    parser.add_argument('--data-dir', required=True, type=Path, help='SDK 为本插件分配的 data_dir')
    parser.add_argument('--file', required=True, type=Path, help='备份输出（不可覆盖）或恢复源')
    args = parser.parse_args()
    if args.operation == 'backup':
        backup(args.data_dir, args.file)
    else:
        restore(args.data_dir, args.file)


if __name__ == '__main__':
    main()

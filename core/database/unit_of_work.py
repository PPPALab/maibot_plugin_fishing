"""Shared connection and durable outer transaction across upstream repositories.

Repository commits/context exits cannot prematurely commit the business operation.
Nested service failures poison the outer transaction. Never changes sqlite globally.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Mapping
from datetime import date, datetime
from functools import wraps
import inspect
import itertools
from pathlib import Path
import sqlite3

_active = ContextVar('fishing_transaction', default=None)
_savepoints = itertools.count()
COMMERCE_OPERATIONS = frozenset({
    'transfer_coins', 'purchase_item', 'purchase_offer', 'purchase_in_shop',
    'buy_market_item', 'put_item_on_sale', 'delist_item', 'purchase_commodity',
    'sell_commodity', 'sell_commodity_by_instance', 'clear_all_inventory',
    'clear_commodity_inventory', 'send_red_packet', 'claim_red_packet',
    'revoke_red_packet', 'add_fish_to_aquarium', 'remove_fish_from_aquarium',
    'create_user_for_admin', 'add_item_to_user_inventory', 'remove_item_from_user_inventory',
})


def serialize_parameters(parameters):
    """Use the legacy ISO storage format explicitly, without global sqlite adapters.

    Datetimes retain naive/aware status, UTC offset and fractional seconds. Reading
    stays textual: domain repositories, not sqlite's deprecated converters, own
    interpretation of their date/time columns.
    """
    def serialize(value):
        if isinstance(value, datetime):
            return value.isoformat(' ')
        if isinstance(value, date):
            return value.isoformat()
        return value

    if isinstance(parameters, Mapping):
        return {key: serialize(value) for key, value in parameters.items()}
    return tuple(serialize(value) for value in parameters)


class Cursor:
    def __init__(self, state, cursor):
        self.state, self.raw = state, cursor

    def execute(self, sql, parameters=()):
        if sql.strip().upper() in ('BEGIN', 'BEGIN TRANSACTION', 'BEGIN IMMEDIATE'):
            return self
        try:
            self.raw.execute(sql, serialize_parameters(parameters))
        except BaseException:
            self.state.failed = True
            raise
        return self

    def executemany(self, sql, parameters):
        try:
            self.raw.executemany(sql, (serialize_parameters(row) for row in parameters))
        except BaseException:
            self.state.failed = True
            raise
        return self

    def __getattr__(self, name):
        if name == 'connection':
            return self.state
        return getattr(self.raw, name)

    def __iter__(self):
        return iter(self.raw)


class Connection:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        # No PARSE_DECLTYPES/PARSE_COLNAMES: repositories explicitly parse ISO text.
        self.raw = sqlite3.connect(self.path, timeout=30)
        self.raw.row_factory = sqlite3.Row
        self.raw.execute('PRAGMA foreign_keys=ON')
        self.raw.execute('PRAGMA journal_mode=WAL')
        self.raw.execute('PRAGMA synchronous=FULL')
        self.failed = False

    def cursor(self):
        return Cursor(self, self.raw.cursor())

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self.cursor().executemany(sql, parameters)

    def commit(self):
        pass  # only the owner may commit

    def rollback(self):
        self.failed = True

    def close(self):
        pass  # owner closes, never a repository

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind:
            self.failed = True
        return False


@contextmanager
def atomic(path):
    state = _active.get()
    if state is not None:
        if state.path != str(Path(path).resolve()):
            raise RuntimeError('Cross-database transaction forbidden')
        try:
            yield state
        except BaseException:
            state.failed = True
            raise
        return
    state = Connection(path)
    token = _active.set(state)
    try:
        state.raw.execute('BEGIN IMMEDIATE')
        yield state
        if state.failed:
            state.raw.rollback()
        else:
            state.raw.commit()
    except BaseException:
        state.raw.rollback()
        raise
    finally:
        _active.reset(token)
        state.raw.close()


def connection(path):
    state = _active.get()
    if state is None or state.path != str(Path(path).resolve()):
        raise RuntimeError('Repository access requires atomic(plugin_db_path)')
    return state


def bind_services(engine, path):
    """Protect standalone service calls, including background and manager routes."""
    seen = set()
    def bind(obj):
        if id(obj) in seen:
            return
        seen.add(id(obj))
        for name, method in inspect.getmembers(obj, inspect.ismethod):
            if name.startswith('__') or (name.startswith(('start_', 'stop_')) and name.endswith('_task')):
                continue
            if name.endswith('_loop') or name in ('set_message_callback', 'discover_and_register'):
                continue
            if inspect.iscoroutinefunction(method):
                @wraps(method)
                async def protected_async(*args, _method=method, **kwargs):
                    # Async settlement exceptions may be caught by a handler;
                    # poison the owning transaction before that catch runs.
                    with atomic(path):
                        return await _method(*args, **kwargs)
                setattr(obj, name, protected_async)
                continue
            @wraps(method)
            def protected(*args, _method=method, **kwargs):
                with atomic(path) as state:
                    commerce = _method.__name__ in COMMERCE_OPERATIONS
                    if commerce:
                        savepoint = f'business_{next(_savepoints)}'
                        state.execute(f'SAVEPOINT {savepoint}')
                    result = _method(*args, **kwargs)
                    # `success=False` may be a valid paid fishing miss, not an error.
                    # Rollback is driven by exceptions/explicit repository rollback,
                    # never by the gameplay outcome flag.
                    # A business rejection must undo partial service writes but is not
                    # a database failure. Never clear an explicit/SQL failure flag.
                    if commerce:
                        if isinstance(result, dict) and result.get('success') is False:
                            state.execute(f'ROLLBACK TO {savepoint}')
                        state.execute(f'RELEASE {savepoint}')
                    return result
            setattr(obj, name, protected)
        for value in vars(obj).copy().values():
            if type(value).__module__.startswith(engine.__class__.__module__.rsplit('.', 1)[0] + '.core.services'):
                bind(value)
    for name, obj in vars(engine).items():
        if name.endswith('_service'):
            bind(obj)

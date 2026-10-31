"""Reproducible mechanical port of the pinned upstream; run once on a clean clone.

Rules and handler bodies are retained. No framework shim or sys.modules injection.
Handwritten adapter modules are intentionally not generated here.
"""
import ast
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SHA = "22e09e9359abfecc5ba450f1d971c79e8962dec2"


def original(path):
    return subprocess.check_output(["git", "show", f"{SHA}:{path}"], cwd=ROOT, text=True)


def write(path, text):
    (ROOT / path).write_text(text, encoding="utf-8")


def detach(text, depth):
    prefix = "." * depth
    text = re.sub(r"(?m)^(\s*)from astrbot.api import logger\s*$",
                  r'\1import logging\n\1logger = logging.getLogger(__name__)', text)
    text = re.sub(r"from astrbot.api.event import .*", f"from {prefix}events import GameEvent", text)
    text = re.sub(r"from astrbot\S* import At[^\n]*", f"from {prefix}events import Mention", text)
    text = re.sub(r"(?m)^from astrbot[^\n]*\n", "", text)
    return text.replace("AstrMessageEvent", "GameEvent").replace("isinstance(comp, At)", "isinstance(comp, Mention)")


def main():
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", SHA], cwd=ROOT, text=True).splitlines()
    for name in names:
        if name.endswith('.py') and name != 'main.py' and not name.startswith('tests/'):
            text = detach(original(name), 1 if '/' not in name else 2)
            if name == 'utils.py':
                text = text.replace('from .events import Mention  # noqa: E402', 'from .events import Mention  # noqa: E402')
                text = text.replace('isinstance(comp, At)', 'isinstance(comp, Mention)')
                # Port never accepts a fabricated nickname/CQ-code as an account identifier.
                text = text.replace('return target_user_id, None', 'return (target_user_id, None) if target_user_id.isdecimal() else (None, "❌ 目标必须是 QQ 数字账号或真实 @ 段")')
            if name == 'core/database/migration.py':
                text = text.replace('f"data.plugins.astrbot_plugin_fishing.core.database.migrations.{filename[:-3]}"', 'f"{__package__}.migrations.{filename[:-3]}"')
                text = text.replace('logger.warning(f"迁移目录', 'raise FileNotFoundError(f"迁移目录')
            if name.startswith('core/repositories/sqlite_'):
                tree = ast.parse(text)
                lines = text.splitlines(keepends=True)
                for node in reversed(list(ast.walk(tree))):
                    if isinstance(node, ast.FunctionDef) and node.name == '_get_connection':
                        lines[node.lineno-1:node.end_lineno] = [
                            '    def _get_connection(self):\n',
                            '        from ..database.unit_of_work import connection\n',
                            '        return connection(self.db_path)\n']
                text = ''.join(lines)
            if name == 'core/database/connection_manager.py':
                text = text.replace('conn = self._get_connection()', 'from .unit_of_work import connection\n        conn = connection(self.db_path)')
            if name.startswith('handlers/'):
                text = text.replace('from ..main import FishingPlugin', 'from ..engine import FishingPlugin')
            write(name, text)

    source = original('main.py')
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    commands = []
    for node in cls.body:
        for dec in getattr(node, 'decorator_list', []):
            if isinstance(dec, ast.Call) and ast.unparse(dec.func) == 'filter.command':
                aliases = [ast.literal_eval(dec.args[0])]
                for kw in dec.keywords:
                    if kw.arg == 'alias':
                        aliases += sorted(ast.literal_eval(kw.value))
                commands.append(dict(method=node.name, aliases=aliases,
                                     admin=any('permission_type' in ast.unparse(d) for d in node.decorator_list),
                                     description=ast.get_docstring(node) or ''))
    write('commands.json', json.dumps(commands, ensure_ascii=False, indent=2) + '\n')
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    lines = source.splitlines(keepends=True)
    init_text = ''.join(lines[init.lineno-1:init.end_lineno])
    init_text = init_text.replace('def __init__(self, context: Context, config: AstrBotConfig):', 'def __init__(self, data_dir: str, config: dict):')
    init_text = init_text.replace('        super().__init__(context)\n', '')
    start = init_text.index('        try:\n')
    end = init_text.index('        self.tmp_dir', start)
    init_text = init_text[:start] + '        self.data_dir = data_dir\n' + init_text[end:]
    start = init_text.index('        # --- 4. 启动后台任务')
    end = init_text.index('        # --- 5.', start)
    init_text = init_text[:start] + init_text[end:]
    # Initialization callback is installed by the native adapter before any game starts.
    init_text = init_text.replace('self._send_sicbo_announcement', 'self.send_announcement')
    init_text = init_text.replace('        self.game_config = {', '        self.game_config = {')
    init_text = init_text.replace('        # 初始化数据库模式', '        self.game_config["tax"].update(tax_config)\n        self.game_config["sicbo"] = config.get("sicbo", {})\n\n        # 初始化数据库模式')
    imports = detach(''.join(lines[:cls.lineno-1]), 1)
    imports = imports.replace('from .events import GameEvent\n', 'from .events import GameEvent\n')
    bodies = [init_text]
    for node in cls.body:
        if node is init:
            continue
        if node.name == '_get_effective_user_id' or any(node.name == c['method'] for c in commands):
            bodies.append(''.join(lines[node.lineno-1:node.end_lineno]))
    engine = imports + 'class FishingPlugin:\n\n' + '\n'.join(bodies)
    engine = engine.replace('AstrMessageEvent', 'GameEvent')
    engine += '\n    async def send_announcement(self, session_info, result):\n        return None\n'
    write('engine.py', engine)
    # main.py becomes a native entrypoint, no executable legacy runtime remains.
    write('main.py', '"""Native entrypoint (see plugin.py)."""\nfrom .plugin import create_plugin\n\n__all__ = ["create_plugin"]\n')
    schema = json.loads(original('_conf_schema.json'))
    def defaults(fields):
        return {k: defaults(v['items']) if v.get('type') == 'object' else v.get('default') for k, v in fields.items()}
    config = defaults(schema)
    config['webui'].update(enabled=False, secret_key='')
    config['admin_ids'] = []
    write('defaults.json', json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    print(f'Ported {len(commands)} commands; pinned upstream retained in Git history.')


if __name__ == '__main__':
    main()

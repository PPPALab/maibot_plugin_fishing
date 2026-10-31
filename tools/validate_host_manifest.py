"""Validate with actual pinned MaiBot Pydantic manifest model, without booting Host.

Only model definitions/constants and non-Host imports are compiled verbatim from
the supplied Host source. This is an offline diagnostic, not a runtime shim or a
replacement for Runner integration testing. No Host files are modified.
"""
import argparse
import ast
import json
from pathlib import Path
import types
import sys


def load_model(source):
    tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            nodes.append(node)
        elif isinstance(node, ast.ImportFrom):
            if not node.level and not (node.module or '').startswith('src.'):
                nodes.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == 'logger' for target in targets):
                continue
            nodes.append(node)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            nodes.append(node)
        if isinstance(node, ast.ClassDef) and node.name == 'PluginManifest':
            break
    module = types.ModuleType('_fishing_host_manifest_diagnostic')
    sys.modules[module.__name__] = module
    try:
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), module.__dict__)
        return module.PluginManifest
    finally:
        sys.modules.pop(module.__name__, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=Path(__file__).resolve().parents[1] / '_manifest.json')
    args = parser.parse_args()
    source = args.host_root / 'src/plugin_runtime/runner/manifest_validator.py'
    model = load_model(source)
    result = model.model_validate(json.loads(args.manifest.read_text(encoding='utf-8')))
    print(f'Host PluginManifest validation passed: {result.id} v{result.version}')


if __name__ == '__main__':
    main()

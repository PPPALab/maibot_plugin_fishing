"""Host manifest registration contract, before SDK entrypoint import.

Required fields are taken from MaiBot main c6429547af711555e3d4d1b8d04b413572cb616c,
src/plugin_runtime/runner/manifest_validator.py: PluginManifest / ManifestI18n.
SDK collect_components alone does not validate the installation manifest.
"""
import json
from pathlib import Path
import tomllib

from packaging.requirements import Requirement


def test_host_required_manifest_fields():
    manifest = json.loads((Path(__file__).parents[1] / '_manifest.json').read_text())
    required = {'manifest_version', 'version', 'name', 'description', 'author',
                'license', 'urls', 'host_application', 'sdk', 'capabilities', 'i18n', 'id'}
    missing = required - manifest.keys()
    assert not missing, f'manifest 校验失败: 缺少必需字段: {", ".join(sorted(missing))}'
    assert manifest['manifest_version'] == 2
    assert manifest['id'] == 'github.PPPALab.maibot_plugin_fishing'
    project = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text())['project']
    assert manifest['version'] == project['version']
    assert manifest['license'] == project['license'] == 'AGPL-3.0-only'
    assert manifest['urls']['repository'] == 'https://github.com/PPPALab/maibot_plugin_fishing'
    i18n = manifest['i18n']
    assert i18n['default_locale']
    assert i18n['default_locale'] in i18n['supported_locales']
    assert len(i18n['supported_locales']) == len(set(i18n['supported_locales']))
    # Literal Chinese command strings need no fabricated/missing resource folder.
    if i18n.get('locales_path') is not None:
        assert (Path(__file__).parents[1] / i18n['locales_path']).is_dir()


def test_manifest_declares_project_runtime_dependencies():
    root = Path(__file__).parents[1]
    manifest = json.loads((root / '_manifest.json').read_text())
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    packages = {entry['name'].lower(): entry['version_spec']
                for entry in manifest['dependencies'] if entry['type'] == 'python_package'}
    for dependency in project['dependencies']:
        requirement = Requirement(dependency)
        # The runner supplies the pinned SDK itself.
        if requirement.name.lower() == 'maibot-plugin-sdk':
            continue
        assert requirement.name.lower() in packages
        assert Requirement(requirement.name + packages[requirement.name.lower()]).specifier == requirement.specifier

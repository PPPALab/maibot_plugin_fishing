from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


def test_release_readme_and_user_docs_have_working_local_links():
    for path in [ROOT / name for name in (
        'README.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'THIRD_PARTY_NOTICES.md')]:
        for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', path.read_text()):
            if '://' in target or target.startswith('#'):
                continue
            assert (path.parent / target.split('#')[0]).exists(), (path, target)


def test_git_ignores_private_artifacts_local_docs_and_logs_not_release_files():
    if not shutil.which('git') or not (ROOT / '.git').exists():
        pytest.skip('Source archive without Git metadata')
    private = ['config.toml', 'config.toml.before-version-example.bak', '.env', '.env.local', '.local/notes.md',
               'data/game.sqlite3', 'avatar_cache/player.png', 'docs/test-results.xml',
               'docs/CONFIGURATION.md', 'docs/DOCKER.md', 'docs/TEST_REPORT.md',
               'docs/evidence/docker-load-20261030.log', 'runtime.log', 'logs/runtime.log']
    public = ['README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
              'draw/resource/OFL-Noto.txt', 'tests/test_lan_admin.py']
    result = subprocess.run(['git', 'check-ignore', '--no-index', '--stdin'], cwd=ROOT,
                            input='\n'.join(private + public) + '\n', text=True, capture_output=True)
    assert result.returncode in (0, 1)
    assert set(result.stdout.splitlines()) == set(private)

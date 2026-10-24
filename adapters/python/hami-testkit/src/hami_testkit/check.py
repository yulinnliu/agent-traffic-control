"""Read-only checks for a repository's explicitly adopted testkit contract."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
import zipfile

import yaml


def adopted(root: Path) -> bool:
    return (root / 'vendor/hami-testkit.json').is_file()


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('testkit input escapes repository')
    return path


def repository_problems(root: Path, *, installed: bool = False, hooks: bool = False) -> list[str]:
    """Validate static wiring, with optional environment and installed-hook checks."""
    root = root.resolve()
    problems = []
    try:
        project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
        config = project.get('tool', {}).get('hami-testkit', {})
        release = json.loads((root / 'vendor/hami-testkit.json').read_text(encoding='utf-8'))
        if release.get('managed_by') != 'agent-traffic-control':
            problems.append('testkit release record lacks managed ownership')
        version = release['version']
        if f'hami-testkit=={version}' not in project.get('dependency-groups', {}).get('dev', []):
            problems.append('development dependency must pin the release version')
        wheel_relative = 'vendor/' + release['wheel']
        source = project.get('tool', {}).get('uv', {}).get('sources', {}).get('hami-testkit', {})
        if source != {'path': wheel_relative}:
            problems.append('development source must reference the committed release wheel')
        wheel = _inside(root, wheel_relative)
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if digest != release['wheel_sha256']:
            problems.append('committed wheel digest differs from release record')
        lock = tomllib.loads((root / 'uv.lock').read_text(encoding='utf-8'))
        packages = [p for p in lock.get('package', []) if p.get('name') == 'hami-testkit']
        if len(packages) != 1 or packages[0].get('version') != version:
            problems.append('lockfile must contain the exact testkit release')
        elif (packages[0].get('source') != {'path': wheel_relative}
              or not any(w.get('hash') == 'sha256:' + digest for w in packages[0].get('wheels', []))):
            problems.append('lockfile testkit artifact identity differs')
        testpaths = project.get('tool', {}).get('pytest', {}).get('ini_options', {}).get('testpaths', [])
        if not testpaths:
            problems.append('pytest must declare required testpaths')
        for group in testpaths:
            target = _inside(root, group)
            files = [target] if target.is_file() and target.suffix == '.py' else list(target.rglob('test_*.py'))
            if not target.exists() or not files:
                problems.append(f'required test group is missing or empty: {group}')
        gate = _inside(root, config['gate'])
        imports = [n.module or '' for n in ast.walk(ast.parse(gate.read_text(encoding='utf-8')))
                   if isinstance(n, ast.ImportFrom)]
        if not any(name == 'hami_testkit' or name.startswith('hami_testkit.') for name in imports):
            problems.append('configured gate does not import shared testkit')
        hook = _inside(root, config['hook'])
        hook_text = hook.read_text(encoding='utf-8')
        command = rf'^exec uv run .*python {re.escape(config["gate"])} --run\s*$'
        if not re.search(command, hook_text, re.MULTILINE):
            problems.append('configured pre-push hook does not delegate to the gate')
        workflow = yaml.safe_load(_inside(root, config['workflow']).read_text(encoding='utf-8'))
        job = workflow['jobs'][config['ci_job']]
        steps = job.get('steps', [])
        check_command = 'uv run python -m hami_testkit.check --installed'
        check_steps = [(i, step) for i, step in enumerate(steps) if step.get('run', '').strip() == check_command]
        sync_steps = [i for i, step in enumerate(steps) if step.get('run', '').strip().startswith('uv sync ')]
        if ('if' in job or job.get('continue-on-error') or len(check_steps) != 1
                or not sync_steps or check_steps[0][0] <= max(sync_steps)
                or 'if' in check_steps[0][1] or check_steps[0][1].get('continue-on-error')):
            problems.append('CI must run an unconditional testkit check after dependency sync')
        if installed:
            distribution = importlib.metadata.distribution('hami-testkit')
            if distribution.version != version:
                problems.append('installed testkit version differs from release record')
            with zipfile.ZipFile(wheel) as archive:
                for name in archive.namelist():
                    if name.startswith('hami_testkit/') and name.endswith('.py'):
                        if distribution.locate_file(name).read_bytes() != archive.read(name):
                            problems.append(f'installed testkit source differs: {name}')
        if hooks:
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
            result = subprocess.run(['git', '-C', str(root), 'config', '--get', 'core.hooksPath'],
                                    capture_output=True, text=True, env=env, check=False)
            directory = Path(result.stdout.strip())
            active = (directory if directory.is_absolute() else root / directory) / 'pre-push'
            if result.returncode or not active.is_file() or active.read_text(encoding='utf-8') != hook_text:
                problems.append('installed pre-push hook is missing or differs; run clone setup')
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, yaml.YAMLError,
            zipfile.BadZipFile, importlib.metadata.PackageNotFoundError) as error:
        problems.append(f'testkit contract unavailable: {type(error).__name__}: {error}')
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--hooks', action='store_true')
    args = parser.parse_args(argv)
    problems = repository_problems(args.root, installed=args.installed, hooks=args.hooks)
    for problem in problems:
        print('ERROR ' + problem)
    if not problems:
        print('testkit contract: PASS')
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())

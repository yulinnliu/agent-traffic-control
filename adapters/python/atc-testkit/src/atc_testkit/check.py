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


DISTRIBUTION = "atc-testkit"
MODULE = "atc_testkit"
RECORD = "vendor/atc-testkit.json"
CHECK_COMMAND = f"uv run python -m {MODULE}.check --installed"
PLUGIN = "atc_testkit.plugin"
REQUIRED_STRINGS = ("gate", "hook", "workflow", "ci_job", "unmarked_baseline", "slow_ci_job")
REQUIRED_LISTS = ("product_module_prefixes",)
OPTIONAL_LISTS = ("credential_variables", "data_root_variables", "automation_markdown_prefixes", "lint_scope",
                  "hook_uv_options", "ci_platforms")
TOOLCHAINS = ("python",)  # the names atc_testkit.toolchain registers; this file also loads outside the package
INITIALIZATION = re.compile(r"[A-Za-z_][\w.]*:[A-Za-z_]\w*")


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('testkit input escapes repository')
    return path


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _data_problems(root: Path, project: dict, config: dict, workflow: dict) -> list[str]:
    """The data of docs/TESTKIT.md § What a project supplies, and the markers and plugin of § Adoption and § Markers."""
    problems = []
    for key in REQUIRED_STRINGS:
        if not isinstance(config.get(key), str) or not config[key]:
            problems.append(f'[tool.atc-testkit] must name {key}')
    for key in REQUIRED_LISTS:
        if not _strings(config.get(key)) or not config[key]:
            problems.append(f'[tool.atc-testkit] must list {key}')
    for key in OPTIONAL_LISTS:
        if key in config and not _strings(config[key]):
            problems.append(f'[tool.atc-testkit] {key} must be a list of strings')
    ignore = config.get('ignore', {})
    if not isinstance(ignore, dict) or not all(key in ('nt', 'posix') and _strings(value) for key, value in ignore.items()):
        problems.append('[tool.atc-testkit] ignore must map nt and posix to lists of paths')
    if 'toolchain' in config and config['toolchain'] not in TOOLCHAINS:
        problems.append(f'[tool.atc-testkit] toolchain must be one of {", ".join(TOOLCHAINS)}')
    if 'ci_init' in config and not (isinstance(config['ci_init'], str) and INITIALIZATION.fullmatch(config['ci_init'])):
        problems.append('[tool.atc-testkit] ci_init must be module:function')
    if isinstance(config.get('unmarked_baseline'), str) and not _inside(root, config['unmarked_baseline']).is_file():
        problems.append(f'unmarked baseline is missing: {config["unmarked_baseline"]}')
    if config.get('slow_ci_job') not in workflow['jobs']:
        problems.append('[tool.atc-testkit] slow_ci_job must name a job of the workflow')
    if project.get('tool', {}).get('pytest', {}).get('ini_options', {}).get('markers'):
        problems.append('pyproject.toml must list no markers: the plugin registers them')
    dev = project.get('dependency-groups', {}).get('dev', [])
    if not any(isinstance(item, str) and re.match(r'pytest-xdist\b', item) for item in dev):
        problems.append('development dependencies must include pytest-xdist')
    conftest = root / 'conftest.py'
    plugins = [node.value for node in ast.walk(ast.parse(conftest.read_text(encoding='utf-8')))
               if isinstance(node, ast.Constant)] if conftest.is_file() else []
    if PLUGIN not in plugins:
        problems.append(f'root conftest.py must register {PLUGIN}')
    return problems


def _hook_pattern(config: dict) -> str:
    gate = re.escape(config['gate'])
    options = ''.join(re.escape(option) + ' ' for option in config.get('hook_uv_options', []))
    return rf'^exec uv run --frozen {options}python {gate} --run\s*$'


def repository_problems(root: Path, *, installed: bool = False, hooks: bool = False) -> list[str]:
    """Validate static wiring, with optional environment and installed-hook checks."""
    root = root.resolve()
    problems = []
    try:
        project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
        config = project.get('tool', {}).get(DISTRIBUTION, {})
        release = json.loads((root / RECORD).read_text(encoding='utf-8'))
        if release.get('managed_by') != 'agent-traffic-control':
            problems.append('testkit release record lacks managed ownership')
        version = release['version']
        if f'{DISTRIBUTION}=={version}' not in project.get('dependency-groups', {}).get('dev', []):
            problems.append('development dependency must pin the release version')
        wheel_relative = 'vendor/' + release['wheel']
        source = project.get('tool', {}).get('uv', {}).get('sources', {}).get(DISTRIBUTION, {})
        if source != {'path': wheel_relative}:
            problems.append('development source must reference the committed release wheel')
        wheel = _inside(root, wheel_relative)
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if digest != release['wheel_sha256']:
            problems.append('committed wheel digest differs from release record')
        lock = tomllib.loads((root / 'uv.lock').read_text(encoding='utf-8'))
        packages = [p for p in lock.get('package', []) if p.get('name') == DISTRIBUTION]
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
        if not any(name == MODULE or name.startswith(MODULE + '.') for name in imports):
            problems.append('configured gate does not import shared testkit')
        hook = _inside(root, config['hook'])
        hook_text = hook.read_text(encoding='utf-8')
        if not re.search(_hook_pattern(config), hook_text, re.MULTILINE):
            problems.append('configured pre-push hook does not delegate to the gate')
        workflow = yaml.safe_load(_inside(root, config['workflow']).read_text(encoding='utf-8'))
        job = workflow['jobs'][config['ci_job']]
        steps = job.get('steps', [])
        check_steps = [(i, step) for i, step in enumerate(steps) if step.get('run', '').strip() == CHECK_COMMAND]
        sync_steps = [i for i, step in enumerate(steps) if step.get('run', '').strip().startswith('uv sync ')]
        if ('if' in job or job.get('continue-on-error') or len(check_steps) != 1
                or not sync_steps or check_steps[0][0] <= max(sync_steps)
                or 'if' in check_steps[0][1] or check_steps[0][1].get('continue-on-error')):
            problems.append('CI must run an unconditional testkit check after dependency sync')
        problems.extend(_data_problems(root, project, config, workflow))
        if installed:
            distribution = importlib.metadata.distribution(DISTRIBUTION)
            if distribution.version != version:
                problems.append('installed testkit version differs from release record')
            with zipfile.ZipFile(wheel) as archive:
                for name in archive.namelist():
                    if name.startswith(MODULE + '/') and name.endswith('.py'):
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
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__)
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

"""Opted-in wiring failures block verification without modifying project files."""
from pathlib import Path
import hashlib
import json
import sys
import zipfile

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1] / 'hami-testkit/src'))
from hami_testkit.check import repository_problems


@pytest.fixture
def repository(tmp_path):
    def write(relative, content):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path
    write('pyproject.toml', '''[dependency-groups]
dev = ["hami-testkit==0.2.0"]
[tool.uv.sources]
hami-testkit = { path = "vendor/hami_testkit-0.2.0-py3-none-any.whl" }
[tool.pytest.ini_options]
testpaths = ["tests"]
[tool.hami-testkit]
gate = "scripts/prepush_select.py"
hook = "scripts/git-hooks/pre-push"
workflow = ".github/workflows/ci.yml"
ci_job = "python"
''')
    write('tests/test_case.py', 'def test_case(): pass\n')
    write('scripts/prepush_select.py', 'from hami_testkit import selection\n')
    write('scripts/git-hooks/pre-push', '#!/bin/sh\nexec uv run python scripts/prepush_select.py --run\n')
    write('.github/workflows/ci.yml', '''jobs:
  python:
    steps:
      - run: uv sync --all-packages --frozen
      - run: uv run python -m hami_testkit.check --installed
''')
    wheel = tmp_path / 'vendor/hami_testkit-0.2.0-py3-none-any.whl'
    wheel.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('hami_testkit/__init__.py', b'')
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    write('vendor/hami-testkit.json', json.dumps(dict(managed_by='agent-traffic-control',version='0.2.0',wheel=wheel.name,wheel_sha256=digest)))
    write('uv.lock', f'''[[package]]
name = "hami-testkit"
version = "0.2.0"
source = {{ path = "vendor/{wheel.name}" }}
wheels = [{{ hash = "sha256:{digest}" }}]
''')
    return tmp_path


def test_valid_contract_is_read_only(repository):
    before = {str(p):p.read_bytes() for p in repository.rglob('*') if p.is_file()}
    assert repository_problems(repository) == []
    assert before == {str(p):p.read_bytes() for p in repository.rglob('*') if p.is_file()}


@pytest.mark.parametrize(('relative','old','new'), [
    ('pyproject.toml', 'hami-testkit==0.2.0', 'hami-testkit>=0.2.0'),
    ('pyproject.toml', 'vendor/hami_testkit-0.2.0', 'other/hami_testkit-0.2.0'),
    ('uv.lock', 'version = "0.2.0"', 'version = "0.1.1"'),
    ('uv.lock', 'sha256:', 'wrong:'),
    ('vendor/hami-testkit.json', 'agent-traffic-control', 'project-owned'),
    ('scripts/prepush_select.py', 'from hami_testkit import selection', '# from hami_testkit import selection'),
    ('scripts/git-hooks/pre-push', 'exec uv run', '# exec uv run'),
    ('.github/workflows/ci.yml', '- run: uv run python -m hami_testkit.check --installed', '# check omitted'),
])
def test_miswiring_is_an_error(repository, relative, old, new):
    path=repository/relative
    path.write_text(path.read_text(encoding='utf-8').replace(old,new),encoding='utf-8')
    assert repository_problems(repository)


@pytest.mark.parametrize('mutation', ['job_if','step_if','job_if_boolean','step_if_boolean','job_continue','step_continue','before_sync'])
def test_ci_check_cannot_be_conditional_or_run_before_sync(repository, mutation):
    path=repository/'.github/workflows/ci.yml'
    doc=yaml.safe_load(path.read_text())
    job=doc['jobs']['python']
    if mutation=='job_if':job['if']='false'
    elif mutation=='step_if':job['steps'][1]['if']='false'
    elif mutation=='job_if_boolean':job['if']=False
    elif mutation=='step_if_boolean':job['steps'][1]['if']=False
    elif mutation=='job_continue':job['continue-on-error']=True
    elif mutation=='step_continue':job['steps'][1]['continue-on-error']=True
    else:job['steps'].reverse()
    path.write_text(yaml.safe_dump(doc),encoding='utf-8')
    assert repository_problems(repository)


def test_missing_and_empty_required_tests_are_errors(repository):
    (repository/'tests/test_case.py').unlink()
    assert any('missing or empty' in p for p in repository_problems(repository))
    (repository/'tests').rmdir()
    assert any('missing or empty' in p for p in repository_problems(repository))


def test_workspace_bridge_only_inspects_opted_in_projects(repository, tmp_path):
    import testkit_contract
    assert testkit_contract.problems(repository) == []
    absent=tmp_path/'unadopted'
    absent.mkdir()
    assert testkit_contract.problems(absent) == []
    (repository/'vendor/hami-testkit.json').write_text('{}')
    assert testkit_contract.problems(repository)



def test_onboarding_reports_testkit_drift_without_rewriting_project_config(repository,tmp_path,monkeypatch):
    import onboard_project
    workspace=tmp_path/'workspace'
    (workspace/'shared/templates').mkdir(parents=True)
    (workspace/'shared/templates/PROJECT_AGENTS.md').write_text('managed policy')
    (workspace/'CLAUDE.md').write_text('workspace entrypoint')
    (repository/'CLAUDE.md').write_text('project-owned guidance')
    monkeypatch.setattr(onboard_project,'canonical_skills',lambda root:{})
    monkeypatch.setattr(onboard_project,'governance_plan',lambda project,root:[])
    monkeypatch.setattr(onboard_project,'policy_plan',lambda project,template:onboard_project.PlanItem('skip',project/'AGENTS.md','policy current'))
    before={str(p):p.read_bytes() for p in repository.rglob('*') if p.is_file()}
    plan=onboard_project.build_plan(repository,workspace)
    assert any('testkit contract current' in item.detail for item in plan)
    onboard_project.apply_plan(plan,'sync')
    assert before=={str(p):p.read_bytes() for p in repository.rglob('*') if p.is_file()}
    path=repository/'pyproject.toml'
    path.write_text(path.read_text().replace('hami-testkit==0.2.0','hami-testkit>=0.2.0'))
    plan=onboard_project.build_plan(repository,workspace)
    assert any(item.action=='collision' and item.path.name=='hami-testkit.json' for item in plan)

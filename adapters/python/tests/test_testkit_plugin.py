"""docs/TESTKIT.md § Adoption, § Markers and the plugin's walk from a direct pytest to its temporary directory."""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYPROJECT = """[project]
name = "fixture-project"
version = "1"
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--strict-markers"
[tool.atc-testkit]
unmarked_baseline = "baseline.txt"
"""
CONFTEST = 'pytest_plugins = ("atc_testkit.plugin",)\n'
REPORTING_TEST = """import os, tempfile
import pytest

@pytest.mark.unit
def test_reports(tmp_path):
    print("TEMPDIR", tempfile.gettempdir())
    print("TMP_PATH", tmp_path)
    print("ATC_TMP", os.environ.get("ATC_TMP"))
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (root / "baseline.txt").write_text("# none\n", encoding="utf-8")
    (root / "tests/test_a.py").write_text(REPORTING_TEST, encoding="utf-8")
    return root


def pytest_in(root: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    environment.update(env)
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-s", "-n", "0", *args], cwd=root,
                          env=environment, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def reported(output: str, name: str) -> str:
    """The value a test printed; pytest's progress prefix can share the line under ``-s``."""
    return next(line.split(name + " ", 1)[1] for line in output.splitlines() if name + " " in line)


def test_a_direct_pytest_ends_up_below_systmp(project: Path, atc_tmp: Path) -> None:
    run = pytest_in(project)
    assert run.returncode == 0, run.stdout + run.stderr
    systmp = atc_tmp / "systmp"
    assert reported(run.stdout, "TEMPDIR") == str(systmp)
    assert reported(run.stdout, "ATC_TMP") == str(atc_tmp)
    assert Path(reported(run.stdout, "TMP_PATH")).is_relative_to(systmp / f"pytest-of-{getpass.getuser()}" / "pytest-0")


def test_a_temporary_directory_below_the_root_stays(project: Path, atc_tmp: Path) -> None:
    scratch = atc_tmp / "scratch"
    scratch.mkdir()
    run = pytest_in(project, TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch))
    assert reported(run.stdout, "TEMPDIR") == str(scratch)


def test_a_basetemp_changes_nothing(project: Path, atc_tmp: Path, tmp_path: Path) -> None:
    run = pytest_in(project, "--basetemp", str(tmp_path / "given"))
    assert run.returncode == 0, run.stdout + run.stderr
    assert reported(run.stdout, "TEMPDIR") != str(atc_tmp / "systmp")
    assert not (atc_tmp / "systmp").exists()


def test_a_debug_temproot_changes_nothing(project: Path, atc_tmp: Path, tmp_path: Path) -> None:
    run = pytest_in(project, PYTEST_DEBUG_TEMPROOT=str(tmp_path))
    assert run.returncode == 0, run.stdout + run.stderr
    assert reported(run.stdout, "TEMPDIR") != str(atc_tmp / "systmp")
    assert Path(reported(run.stdout, "TMP_PATH")).is_relative_to(tmp_path)


def test_ci_keeps_the_platform_location(project: Path, atc_tmp: Path) -> None:
    run = pytest_in(project, CI="1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert reported(run.stdout, "TEMPDIR") != str(atc_tmp / "systmp")


def test_an_unsuitable_root_stops_the_run_with_its_token(project: Path, atc_tmp: Path) -> None:
    run = pytest_in(project, ATC_TMP="relative")
    assert run.returncode == 9, run.stdout + run.stderr
    assert "ATC_TMP_UNSUITABLE" in run.stdout + run.stderr


def test_xdist_workers_inherit_the_location(project: Path, atc_tmp: Path) -> None:
    run = pytest_in(project, "-n", "2")
    assert run.returncode == 0, run.stdout + run.stderr
    bases = list((atc_tmp / "systmp").glob("pytest-of-*/pytest-0/popen-gw*"))
    assert bases


def test_the_plugin_loads_no_other_plugin_into_a_project(project: Path, atc_tmp: Path) -> None:
    assert "pytester" not in pytest_in(project, "--fixtures").stdout


def test_every_marker_is_registered_with_one_description(project: Path, atc_tmp: Path) -> None:
    run = pytest_in(project, "--markers")
    for name in ("unit", "integration", "render", "statistical", "slow", "serial", "interactive_desktop"):
        assert run.stdout.count(f"@pytest.mark.{name}:") == 1


@pytest.mark.parametrize(("body", "expected"), [
    ("def test_x(): pass\n", "unmarked test is not in the legacy baseline"),
    ("import pytest\n@pytest.mark.slow\ndef test_x(): pass\n", "slow requires one semantic marker"),
    ("import pytest\n@pytest.mark.unit\n@pytest.mark.render\ndef test_x(): pass\n", "multiple semantic markers"),
])
def test_the_marker_contract_is_enforced(project: Path, atc_tmp: Path, body: str, expected: str) -> None:
    (project / "tests/test_b.py").write_text(body, encoding="utf-8")
    run = pytest_in(project)
    assert run.returncode != 0 and expected in run.stdout + run.stderr


def test_the_named_baseline_allows_a_listed_test(project: Path, atc_tmp: Path) -> None:
    (project / "tests/test_b.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (project / "baseline.txt").write_text("tests/test_b.py::test_x\n", encoding="utf-8")
    run = pytest_in(project)
    assert run.returncode == 0, run.stdout + run.stderr


CHILD_TEST = """import subprocess, sys, time
import pytest
from atc_testkit import children

SLEEPER = "import sys,time; time.sleep(120)"

@pytest.mark.unit
def test_starts(tmp_path):
    children.start([sys.executable, "-c", SLEEPER, str(tmp_path)])
"""
ESCAPED_TEST = """import subprocess, sys
import pytest

@pytest.mark.unit
def test_escapes(tmp_path):
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", str(tmp_path)], creationflags=flags)
    (tmp_path.parents[1] / "pid.txt").write_text(str(child.pid))
"""


def test_a_child_started_through_the_testkit_ends_with_its_test(project: Path, atc_tmp: Path) -> None:
    (project / "tests/test_a.py").write_text(CHILD_TEST, encoding="utf-8")
    run = pytest_in(project)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "CHILD_PROCESSES_ALIVE" not in run.stdout


def test_a_surviving_child_fails_the_run_with_its_token(project: Path, tmp_path: Path, atc_tmp: Path) -> None:
    (project / "tests/test_a.py").write_text(ESCAPED_TEST, encoding="utf-8")
    base = tmp_path / "base"
    run = pytest_in(project, "--basetemp", str(base))
    pid = int((tmp_path / "pid.txt").read_text())
    try:
        assert run.returncode == 1, run.stdout + run.stderr
        assert "CHILD_PROCESSES_ALIVE" in run.stdout and str(pid) in run.stdout
    finally:
        from atc_testkit import desktop
        desktop.kill_tree(pid)


def test_sessions_with_other_base_directories_do_not_fail_one_another(project: Path, tmp_path: Path, atc_tmp: Path) -> None:
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", str(tmp_path / "other-base")],
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        run = pytest_in(project, "--basetemp", str(tmp_path / "base"))
        assert run.returncode == 0, run.stdout + run.stderr
    finally:
        other.kill()
        other.wait()

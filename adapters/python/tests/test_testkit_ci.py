"""docs/TESTKIT.md § CI runner and the CI invocations of § Stages."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from atc_testkit import ci, stages

CONFTEST = 'pytest_plugins = ("atc_testkit.plugin",)\n'
PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n[tool.atc-testkit]\nci_init = "setup_hook:prepare"\n'
SETUP = 'def prepare():\n    print("PROJECT_INIT", flush=True)\n'
FAILING = """import pytest

@pytest.mark.unit
def test_fails():
    try:
        raise KeyError("root cause")
    except KeyError as error:
        raise ValueError("outer") from error
"""
PASSING = "import pytest\n\n@pytest.mark.unit\ndef test_ok():\n    pass\n"
SLOW = "import pytest\n\n@pytest.mark.unit\n@pytest.mark.slow\ndef test_slow():\n    print('SLOW_RAN')\n"
PROJECT_RUNNER = """import sys
from atc_testkit import ci

def context(item, call):
    return {"field": item.name}

def hints(reporter):
    return ["CI_HINT project hint"]

raise SystemExit(ci.main(context=context, hints=hints, init=lambda: print("OWN_INIT", flush=True)))
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "setup_hook.py").write_text(SETUP, encoding="utf-8")
    return root


def runner(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    env["CI"] = "1"
    return subprocess.run([sys.executable, "-m", "atc_testkit.ci", "-p", "no:cacheprovider", *args] if not args or not args[0].startswith("--s")
                          else [sys.executable, "-m", "atc_testkit.ci", *args],
                          cwd=root, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def test_a_failing_test_prints_context_causes_and_the_report(project: Path) -> None:
    (project / "tests/test_a.py").write_text(FAILING, encoding="utf-8")
    run = runner(project, "-n", "0")
    assert run.returncode == 1
    assert 'CI_FAILURE_CONTEXT {"context": {}, "phase": "call", "test": "tests/test_a.py::test_fails"}' in run.stderr
    causes = [line for line in run.stderr.splitlines() if line.startswith("CI_FAILURE_CAUSE")]
    assert "root cause" in causes[0] and "outer" in causes[1]
    assert "CI_FAILURE_REPORT tests/test_a.py::test_fails call" in run.stderr
    assert 'CI_PENDING_THREADS []' in run.stderr


def test_a_report_a_worker_sent_gets_the_same_lines(project: Path) -> None:
    (project / "tests/test_a.py").write_text(FAILING, encoding="utf-8")
    run = runner(project, "-n", "1")
    assert run.returncode == 1 and "CI_FAILURE_CONTEXT" in run.stderr and "CI_FAILURE_CAUSE" in run.stderr


def test_only_the_last_characters_of_a_report_are_kept(capsys: pytest.CaptureFixture[str]) -> None:
    class Report:
        when = "call"
        longrepr = "plain"
        longreprtext = "a" * 5000 + "END"
    ci.print_failure("t", Report, {})
    assert capsys.readouterr().err.count("a") < ci.REPORT_LIMIT + 10


def test_non_daemon_threads_are_listed_at_shutdown() -> None:
    import threading
    release = threading.Event()
    thread = threading.Thread(target=release.wait, name="left-running")
    thread.start()
    try:
        assert any(entry["name"] == "left-running" for entry in ci.pending_threads())
    finally:
        release.set()
        thread.join()


def test_the_named_initialization_runs_before_pytest(project: Path) -> None:
    (project / "tests/test_a.py").write_text(PASSING, encoding="utf-8")
    run = runner(project, "-n", "0")
    assert run.returncode == 0 and "PROJECT_INIT" in run.stdout
    assert run.stdout.index("PROJECT_INIT") < run.stdout.index("passed")


def test_a_project_adds_context_hints_and_its_own_initialization(project: Path) -> None:
    (project / "tests/test_a.py").write_text(FAILING, encoding="utf-8")
    (project / "own_runner.py").write_text(PROJECT_RUNNER, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    run = subprocess.run([sys.executable, "own_runner.py", "-n", "0"], cwd=project, env={**env, "CI": "1"},
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert run.returncode == 1 and '"field": "test_fails"' in run.stderr
    assert "CI_HINT project hint" in run.stdout and "OWN_INIT" in run.stdout and "PROJECT_INIT" not in run.stdout


def test_a_failing_context_function_keeps_the_result(project: Path) -> None:
    (project / "tests/test_a.py").write_text(FAILING, encoding="utf-8")
    text = PROJECT_RUNNER.replace('return {"field": item.name}', 'raise RuntimeError("x")')
    (project / "own_runner.py").write_text(text, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    run = subprocess.run([sys.executable, "own_runner.py", "-n", "0"], cwd=project, env={**env, "CI": "1"},
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert run.returncode == 1 and '"diagnostic_error": "RuntimeError"' in run.stderr


def test_the_stages_run_through_the_runner_with_its_worker_count(project: Path) -> None:
    (project / "tests/test_a.py").write_text(PASSING, encoding="utf-8")
    run = runner(project, "--stages", "--workers", "2")
    assert run.returncode == 0, run.stdout + run.stderr
    text = (project / ".tmp/ci-parallel.log").read_text(encoding="utf-8")
    assert "-n 2 --dist loadscope" in text
    assert (project / ".tmp/ci-serial.log").is_file() and (project / ".tmp/ci-interactive.log").is_file()


def test_stages_that_collect_nothing_are_refused(project: Path) -> None:
    (project / "tests/test_a.py").write_text(SLOW, encoding="utf-8")
    run = runner(project, "--stages")
    assert run.returncode == stages.PYTEST_NO_TESTS_COLLECTED and "NO_TESTS_COLLECTED" in run.stderr


def test_the_slow_invocation_runs_slow_tests_and_passes_when_it_collects_none(project: Path) -> None:
    (project / "tests/test_a.py").write_text(SLOW, encoding="utf-8")
    assert "SLOW_RAN" in runner(project, "--slow", "-s").stdout
    (project / "tests/test_a.py").write_text(PASSING, encoding="utf-8")
    assert runner(project, "--slow").returncode == 0


def test_the_slow_invocation_starts_no_private_desktop(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(project)
    monkeypatch.setattr(ci, "run_pytest", lambda args, **_options: (args, 0)[1] if args == ["-q", "-m", "slow", "-n", "0"] else 9)
    assert ci.main(["--slow"]) == 0

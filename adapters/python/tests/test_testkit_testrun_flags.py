"""docs/TESTKIT.md § Commands: the flag form of ``atc test run``, its log, summary line and selection."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from atc_testkit import scratch, testrun

PASSING = "def test_value():\n    assert True\n"
SELECTOR = ("import json, sys\n"
            "print('noise')\n"
            "print(json.dumps(['tests/test_value.py'] if any(p.startswith('src/') for p in sys.argv[1:]) else []))\n")


def run_command(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("PYTEST_", "GIT_")) and key != "ATC_PRIVATE_DESKTOP"}
    environment.update(env, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "atc_testkit.commands", "test", "run", *args], env=environment,
                          capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src/flagged_pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src/flagged_pkg/__init__.py").write_text("X = 1\n", encoding="utf-8")
    (root / "tests/test_value.py").write_text(PASSING, encoding="utf-8")
    (root / "tests/test_other.py").write_text("def test_other():\n    assert False\n", encoding="utf-8")
    (root / "sel.py").write_text(SELECTOR, encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "flagged"\nversion = "1"\n'
                                         '[tool.atc-testkit]\nselector = ["sel.py"]\n', encoding="utf-8")
    return root


def summary_line(run: subprocess.CompletedProcess[str]) -> str:
    return run.stdout.splitlines()[-1]


@pytest.mark.integration
def test_a_run_prints_its_log_and_one_summary_line(tree: Path, atc_tmp: Path) -> None:
    run = run_command("--scratch", "f1", "--tree", str(tree), "--python", sys.executable, "--label", "my run",
                      "--", "tests/test_value.py", ATC_PRIVATE_DESKTOP="0")
    assert run.returncode == 0, run.stdout + run.stderr
    first, last = run.stdout.splitlines()[0], summary_line(run)
    log = Path(first.removeprefix("LOG "))
    assert log.parent == atc_tmp / "f1" / testrun.RUNS and log.name.endswith("-my_run.log")
    assert last == f"PASS 1 passed; log: {log}"
    assert len(run.stdout.splitlines()) == 2
    text = log.read_text(encoding="utf-8")
    assert "desktop: " in text and "1 passed" in text


@pytest.mark.integration
def test_a_failure_names_each_failed_test(tree: Path, atc_tmp: Path) -> None:
    run = run_command("--scratch", "f2", "--tree", str(tree), "--python", sys.executable, "--", "tests")
    assert run.returncode == 1
    assert summary_line(run).startswith("FAIL 1 failed, 1 passed; failed: tests/test_other.py::test_other; log: ")


@pytest.mark.integration
def test_progress_streams_the_output(tree: Path, atc_tmp: Path) -> None:
    run = run_command("--scratch", "f3", "--tree", str(tree), "--python", sys.executable, "--progress",
                      "--", "tests/test_value.py")
    assert run.returncode == 0 and len(run.stdout.splitlines()) > 2


@pytest.mark.integration
def test_the_tree_comes_from_the_registry_and_python_from_its_venv(tree: Path, atc_tmp: Path) -> None:
    run = run_command("--scratch", "f4")
    assert run.returncode == 9 and run.stderr.startswith("TREE_NOT_FOUND ")
    scratch.register(atc_tmp, atc_tmp / "f4", tree)
    run = run_command("--scratch", "f4")
    assert run.returncode == 9 and run.stderr.startswith("VENV_MISSING ") and "atc tmp checkout" in run.stderr


@pytest.mark.integration
def test_a_timeout_reports_the_tail_and_never_a_pass(tree: Path, atc_tmp: Path) -> None:
    (tree / "tests/test_slow.py").write_text("import time\ndef test_slow():\n    time.sleep(60)\n", encoding="utf-8")
    run = run_command("--scratch", "f5", "--tree", str(tree), "--python", sys.executable, "--timeout", "3",
                      "--", "tests/test_slow.py")
    assert run.returncode == testrun.TIMEOUT_EXIT
    assert summary_line(run).startswith("TIMEOUT log: ")


@pytest.mark.integration
def test_select_runs_what_the_declared_selector_prints(tree: Path, atc_tmp: Path) -> None:
    git(tree, "init", "-q", "-b", "main")
    git(tree, "add", ".")
    git(tree, "commit", "-q", "-m", "x")
    run = run_command("--scratch", "f6", "--tree", str(tree), "--python", sys.executable, "--select", "HEAD")
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines() == ["SELECT HEAD: 0 changed paths, 0 test paths", "NO_TESTS no test selected"]
    (tree / "src/flagged_pkg/new.py").write_text("Y = 2\n", encoding="utf-8")
    (tree / "tests/test_value.py").write_text(PASSING + "\n", encoding="utf-8")
    run = run_command("--scratch", "f6", "--tree", str(tree), "--python", sys.executable, "--select", "HEAD")
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[0] == "SELECT HEAD: 2 changed paths, 1 test paths"
    assert summary_line(run).startswith("PASS 1 passed; log: ")


@pytest.mark.integration
def test_select_in_an_export_reads_its_source_commit(tree: Path, atc_tmp: Path, tmp_path: Path) -> None:
    git(tree, "init", "-q", "-b", "main")
    git(tree, "add", ".")
    git(tree, "commit", "-q", "-m", "x")
    (tree / "src/flagged_pkg/__init__.py").write_text("X = 2\n", encoding="utf-8")
    git(tree, "commit", "-q", "-am", "y")
    head = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    export = atc_tmp / "f7" / "tree"
    for path in subprocess.run(["git", "-C", str(tree), "ls-files"], capture_output=True, text=True).stdout.split():
        (export / path).parent.mkdir(parents=True, exist_ok=True)
        (export / path).write_bytes((tree / path).read_bytes())
    run = run_command("--scratch", "f7x", "--python", sys.executable, "--tree", str(export), "--select", "HEAD~1")
    assert run.returncode == 9 and run.stderr.startswith("SELECTION_SOURCE_UNKNOWN ")
    scratch.register(atc_tmp, atc_tmp / "f7", export, task="ATC-0008", source=str(tree), commit=head)
    run = run_command("--scratch", "f7", "--python", sys.executable, "--select", "HEAD~1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[0] == "SELECT HEAD~1: 1 changed paths, 1 test paths"


@pytest.mark.integration
@pytest.mark.parametrize(("pyproject", "selector", "token"), [
    ('[project]\nname = "flagged"\n', None, "SELECTION_UNDECLARED"),
    (None, "print('not json')\n", "SELECTOR_FAILED"),
    (None, "import sys\nsys.exit(3)\n", "SELECTOR_FAILED"),
])
def test_an_undeclared_or_failing_selector_runs_nothing(tree: Path, atc_tmp: Path, pyproject: str | None,
                                                        selector: str | None, token: str) -> None:
    if pyproject is not None:
        (tree / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    if selector is not None:
        (tree / "sel.py").write_text(selector, encoding="utf-8")
    git(tree, "init", "-q", "-b", "main")
    git(tree, "add", ".")
    git(tree, "commit", "-q", "-m", "x")
    (tree / "src/flagged_pkg/__init__.py").write_text("X = 3\n", encoding="utf-8")
    run = run_command("--scratch", "f8", "--tree", str(tree), "--python", sys.executable, "--select", "HEAD")
    assert run.returncode == 9 and run.stderr.startswith(f"{token} "), run.stdout + run.stderr
    assert not (atc_tmp / "f8" / testrun.RUNS).exists()


@pytest.mark.integration
def test_a_base_the_tree_does_not_know_is_refused(tree: Path, atc_tmp: Path) -> None:
    git(tree, "init", "-q", "-b", "main")
    git(tree, "add", ".")
    git(tree, "commit", "-q", "-m", "x")
    run = run_command("--scratch", "f9", "--tree", str(tree), "--python", sys.executable, "--select", "nope")
    assert run.returncode == 9 and run.stderr.startswith("SELECTION_BASE_UNKNOWN ")


@pytest.mark.unit
@pytest.mark.parametrize(("lines", "code", "expected"), [
    (["3 passed in 0.1s"], 0, "PASS 3 passed; log: L"),
    (["FAILED tests/a.py::t - assert 0", "ERROR tests/b.py::u - x", "1 failed, 1 error in 1s"], 1,
     "FAIL 1 failed, 1 error; failed: tests/a.py::t, tests/b.py::u; log: L"),
    (["no tests ran in 0.01s"], 5, "NO_TESTS; log: L"),
    (["boom"], 3, "ERROR exit 3; log: L"),
])
def test_the_summary_line(lines: list[str], code: int, expected: str) -> None:
    assert testrun.summary(testrun.Result(code, lines), Path("L")) == (expected, code)
    assert testrun.summary(testrun.Result(None, lines), Path("L")) == ("TIMEOUT log: L", testrun.TIMEOUT_EXIT)
    assert testrun.summary(None, Path("L")) == ("INTERRUPTED log: L", testrun.INTERRUPTED_EXIT)

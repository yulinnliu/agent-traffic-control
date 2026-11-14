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
# The desktop choice, and the bytecode settings a host could use to hide a write into the tree.
DROPPED_SETTINGS = ("ATC_PRIVATE_DESKTOP", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")


TASK = "ATC-0044"


def run_command(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    """The command in the caller's environment less `DROPPED_SETTINGS`, with `--task TASK` unless it names one."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("PYTEST_", "GIT_")) and key not in DROPPED_SETTINGS}
    environment.update(env, PYTHONIOENCODING="utf-8")
    task = () if "--task" in args else ("--task", TASK)
    return subprocess.run([sys.executable, "-m", "atc_testkit.commands", "test", "run", *task, *args], env=environment,
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


def bytecode(tree: Path) -> list[Path]:
    """What a run compiled into the tree it tests; docs/TESTKIT.md § Commands step 2 keeps it in the scratch."""
    return sorted(tree.rglob("__pycache__"))


@pytest.mark.integration
def test_a_run_prints_its_log_and_one_summary_line(tree: Path, atc_tmp: Path) -> None:
    run = run_command("--scratch", "f1", "--tree", str(tree), "--python", sys.executable, "--label", "my run",
                      "--", "tests/test_value.py", ATC_PRIVATE_DESKTOP="0")
    assert run.returncode == 0, run.stdout + run.stderr
    first, last = run.stdout.splitlines()[0], summary_line(run)
    log = Path(first.removeprefix("LOG "))
    assert log.parent == atc_tmp / "f1" / testrun.RUNS and log.name.endswith("-my_run.log")
    assert last == f"PASS 1 passed; log: {log}"
    assert len(run.stdout.splitlines()) == 3 and run.stdout.splitlines()[1].startswith("workers: ")
    text = log.read_text(encoding="utf-8")
    assert "desktop: " in text and "1 passed" in text
    assert bytecode(tree) == []


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
def test_a_scratch_records_its_task_and_refuses_another(tree: Path, atc_tmp: Path) -> None:
    common = ("--scratch", "f9", "--tree", str(tree), "--python", sys.executable, "--", "tests/test_value.py")
    assert run_command("--task", "DEMO-0007", *common).returncode == 0
    run = run_command("--task", "DEMO-0008", *common)
    assert run.returncode == 9 and run.stderr.startswith("SCRATCH_TASK_MISMATCH ") and "DEMO-0007" in run.stderr
    [row] = scratch.registered(atc_tmp)
    assert row.task == "DEMO-0007"


@pytest.mark.integration
def test_a_run_without_a_task_is_a_usage_error_and_creates_nothing(tree: Path, atc_tmp: Path) -> None:
    run = subprocess.run([sys.executable, "-m", "atc_testkit.commands", "test", "run", "--scratch", "f11", "--tree",
                          str(tree)], env={**os.environ, "ATC_TMP": str(atc_tmp)}, capture_output=True, text=True,
                         encoding="utf-8", stdin=subprocess.DEVNULL)
    assert run.returncode == 2 and "--task" in run.stderr
    assert not (atc_tmp / "f11").exists() and scratch.registered(atc_tmp) == []


@pytest.mark.integration
def test_a_task_that_is_no_task_id_is_refused_before_the_scratch(tree: Path, atc_tmp: Path) -> None:
    run = run_command("--scratch", "f10", "--tree", str(tree), "--task", "atc-1")
    assert run.returncode == 9 and run.stderr.startswith("TASK_INVALID ")
    assert not (atc_tmp / "f10").exists() and scratch.registered(atc_tmp) == []


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
    assert bytecode(tree) == []
    (tree / "src/flagged_pkg/new.py").write_text("Y = 2\n", encoding="utf-8")
    (tree / "tests/test_value.py").write_text(PASSING + "\n", encoding="utf-8")
    run = run_command("--scratch", "f6", "--tree", str(tree), "--python", sys.executable, "--select", "HEAD",
                      "--wide")
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[0] == "SELECT HEAD: 2 changed paths, 1 test paths"
    assert summary_line(run).startswith("PASS 1 passed; log: ")
    assert bytecode(tree) == []


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
    run = run_command("--scratch", "f7", "--task", "ATC-0008", "--python", sys.executable, "--select", "HEAD~1",
                      "--wide")
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


@pytest.mark.integration
def test_a_wide_selection_is_skipped_and_touched_runs_the_changed_tests(tree: Path, atc_tmp: Path) -> None:
    git(tree, "init", "-q", "-b", "main")
    git(tree, "add", ".")
    git(tree, "commit", "-q", "-m", "x")
    (tree / "src/flagged_pkg/__init__.py").write_text("X = 2\n", encoding="utf-8")
    run = run_command("--scratch", "f8", "--tree", str(tree), "--python", sys.executable, "--select", "HEAD")
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines() == [
        "SELECT HEAD: 1 changed paths, 1 test paths, 1 of 2 tests (50%) >= select_full_fraction 0.5",
        f"{testrun.SKIPPED_WIDE} the reviewer's atc verify runs these; run them here with --wide, or the touched "
        "files with --touched"]
    (tree / "pyproject.toml").write_text('[project]\nname = "flagged"\nversion = "1"\n[tool.atc-testkit]\n'
                                         'selector = ["sel.py"]\nselect_full_fraction = 0.75\n', encoding="utf-8")
    run = run_command("--scratch", "f8", "--tree", str(tree), "--python", sys.executable, "--select", "HEAD")
    assert run.returncode == 0 and run.stdout.splitlines()[0] == "SELECT HEAD: 2 changed paths, 1 test paths"
    (tree / "pyproject.toml").write_text('[project]\nname = "flagged"\nversion = "1"\n[tool.atc-testkit]\n'
                                         'selector = ["sel.py"]\nselect_full_fraction = 2\n', encoding="utf-8")
    run = run_command("--scratch", "f8", "--tree", str(tree), "--python", sys.executable, "--select", "HEAD")
    assert run.returncode == 9 and run.stderr.startswith("ATC_CONFIG_INVALID ") and "select_full_fraction" in run.stderr


@pytest.mark.integration
def test_touched_runs_the_changed_test_files_when_the_selector_has_no_direct_form(tree: Path,
                                                                                    atc_tmp: Path) -> None:
    (tree / "sel.py").write_text("import sys\nsys.exit(2 if '--direct' in sys.argv else 0)\n", encoding="utf-8")
    git(tree, "init", "-q", "-b", "main")
    git(tree, "add", ".")
    git(tree, "commit", "-q", "-m", "x")
    (tree / "tests/test_value.py").write_text(PASSING + "\n", encoding="utf-8")
    run = run_command("--scratch", "f9", "--tree", str(tree), "--python", sys.executable, "--select", "HEAD",
                      "--touched")
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.splitlines()[0] == "SELECT HEAD --touched: 1 changed paths, 1 test paths"
    assert summary_line(run).startswith("PASS 1 passed; log: ")


@pytest.mark.unit
@pytest.mark.parametrize("arguments", [["--wide"], ["--touched"], ["--select", "HEAD", "--wide", "--touched"]])
def test_wide_and_touched_take_select_and_exclude_each_other(tmp_path: Path, arguments: list[str],
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        testrun.run(["--scratch", "x", *arguments], {})
    assert stopped.value.code == 2 and ("--wide" in capsys.readouterr().err)


def unregistered(root: Path) -> list[str]:
    """The scratch directories under ``root`` that no registry line names: what ATC-0044 decision (a) forbids."""
    rows = [os.path.normcase(row.scratch) for row in scratch.registered(root)]
    return sorted(child.name for child in root.iterdir()
                  if child.is_dir() and child.name.lower() not in scratch.RESERVED_NAMES
                  and os.path.normcase(child) not in rows)


@pytest.mark.integration
@pytest.mark.parametrize(("arguments", "token"), [
    ((), "TREE_NOT_FOUND"),
    (("--tree", "{tree}"), "VENV_MISSING"),
    (("--tree", "{tree}", "--python", "{absent}"), "PYTHON_NOT_FOUND"),
    (("--tree", "{absent}"), "TREE_NOT_FOUND"),
])
def test_a_refusal_before_the_registry_line_creates_no_directory(tree: Path, atc_tmp: Path, tmp_path: Path,
                                                                   arguments: tuple[str, ...], token: str) -> None:
    values = {"tree": str(tree), "absent": str(tmp_path / "absent" / "python.exe")}
    run = run_command("--scratch", "f12", *(argument.format(**values) for argument in arguments))
    assert run.returncode == 9 and run.stderr.startswith(f"{token} "), run.stderr
    assert not (atc_tmp / "f12").exists() and unregistered(atc_tmp) == [] and scratch.registered(atc_tmp) == []


@pytest.mark.integration
def test_a_refusal_after_the_registry_line_leaves_a_registered_scratch(tree: Path, atc_tmp: Path) -> None:
    run = run_command("--scratch", "f13", "--tree", str(tree), "--python", sys.executable, "--select", "0" * 40)
    assert run.returncode == 9 and run.stderr.startswith("SELECTION_SOURCE_UNKNOWN ")
    assert (atc_tmp / "f13").is_dir() and unregistered(atc_tmp) == []
    assert [(row.scratch, row.task) for row in scratch.registered(atc_tmp)] == [(str(atc_tmp / "f13"), TASK)]


@pytest.mark.integration
def test_a_scratch_path_that_holds_a_file_is_refused_unregistered(tree: Path, atc_tmp: Path) -> None:
    (atc_tmp / "f14").write_text("a file", encoding="utf-8")
    run = run_command("--scratch", "f14", "--tree", str(tree), "--python", sys.executable)
    assert run.returncode == 9 and run.stderr.startswith("SCRATCH_NOT_CREATED ")
    assert scratch.registered(atc_tmp) == []

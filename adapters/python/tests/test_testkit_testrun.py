"""docs/TESTKIT.md § Commands: ``atc test run`` and ``atc test mutants``, and the ``atc`` dispatch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import atc_cli
from atc_testkit import commands, python_toolchain, scratch, testrun
from atc_testkit.locations import Refusal

MODULE = ("from pathlib import Path\nimport os, tempfile\n"
          "def value():\n    return 1\n")
REPORTING_TEST = """import os, tempfile
from pathlib import Path
import fixture_pkg

def test_value(tmp_path):
    assert fixture_pkg.value() == 1
    print("TEMPDIR", tempfile.gettempdir())
    print("BASE", tmp_path)
    print("DATA", os.environ.get("QUANT_DATA_ROOT"), os.environ.get("OTHER_ROOT"))
    print("HAMI", os.environ.get("HAMI_X"))
    print("FILE", fixture_pkg.__file__)
"""


def run_command(*args: str, cwd: Path | None = None, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    environment.update(env, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "atc_testkit.commands", *args], cwd=cwd, env=environment,
                          capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)


def run_tests(tree: Path | str, scratch_name: str, python: str, *args: str, cwd: Path | None = None,
              **env: str) -> subprocess.CompletedProcess[str]:
    """``atc test run`` with pytest's output streamed, so a test's prints reach stdout."""
    return run_command("test", "run", "--scratch", scratch_name, "--tree", str(tree), "--python", python,
                       "--progress", "--", *args, cwd=cwd, **env)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src/fixture_pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src/fixture_pkg/__init__.py").write_bytes(MODULE.replace("\n", "\r\n").encode())
    (root / "tests/test_value.py").write_text(REPORTING_TEST, encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "fixture-name"\nversion = "1"\n', encoding="utf-8")
    return root


@pytest.mark.integration
def test_a_run_isolates_the_temporary_and_data_directories(tree: Path, atc_tmp: Path) -> None:
    run = run_tests(tree, "s1", sys.executable, "-s", "tests", HAMI_X="set", ATC_PRIVATE_DESKTOP="0")
    assert run.returncode == 0, run.stdout + run.stderr
    lines = run.stdout.splitlines()
    scratch_dir = atc_tmp / "s1"
    values = dict(line.split(" ", 1) for line in lines if line.split(" ")[0] in ("TEMPDIR", "BASE", "DATA", "HAMI", "FILE"))
    assert values["TEMPDIR"] == str(scratch_dir)
    assert Path(values["BASE"]).is_relative_to(scratch_dir / "bt")
    assert values["DATA"] == f"{scratch_dir / 'qdr'} None"
    assert values["HAMI"] == "None"
    assert Path(values["FILE"]).is_relative_to(tree.resolve())


def test_the_exit_code_is_pytests(tree: Path, atc_tmp: Path) -> None:
    (tree / "tests/test_bad.py").write_text("def test_bad(): assert False\n", encoding="utf-8")
    assert run_tests(str(tree), "s", sys.executable, "tests").returncode == 1


def test_the_tree_names_its_data_roots(tree: Path, atc_tmp: Path) -> None:
    (tree / "pyproject.toml").write_text('[tool.atc-testkit]\ndata_root_variables = ["OTHER_ROOT"]\n', encoding="utf-8")
    run = run_tests(str(tree), "s", sys.executable, "-s", "tests", QUANT_DATA_ROOT="ambient")
    assert f"DATA ambient {atc_tmp / 's/qdr'}" in run.stdout


def test_xdist_is_turned_off_when_installed_and_the_cache_provider_always(tree: Path, atc_tmp: Path) -> None:
    (tree / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-n 2"\n', encoding="utf-8")
    run = run_tests(str(tree), "s", sys.executable, "-s", "tests")
    assert run.returncode == 0 and "created:" not in run.stdout and "BASE" in run.stdout
    assert not (tree / ".pytest_cache").exists()


def test_a_relative_tree_and_python_resolve_against_the_caller(tree: Path, atc_tmp: Path) -> None:
    run = run_tests("tree", "s", sys.executable, "tests", cwd=tree.parent)
    assert run.returncode == 0, run.stdout + run.stderr
    python = Path(sys.executable)
    run = run_tests(tree, "s", os.path.join(os.curdir, python.name), "tests", cwd=python.parent)
    assert run.returncode == 0, run.stdout + run.stderr


def test_a_bare_python_name_is_found_on_the_path(tree: Path, atc_tmp: Path) -> None:
    run = run_tests(str(tree), "s", "python", "tests",
                      PATH=os.pathsep.join((os.path.dirname(sys.executable), os.environ["PATH"])))
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize(("arguments", "token"), [
    (("{absent}", "s", "{python}"), "TREE_NOT_FOUND"),
    (("{tree}", "s", "{absent}"), "PYTHON_NOT_FOUND"),
    (("{tree}", "s", "no-such-python-here"), "PYTHON_NOT_FOUND"),
    (("{tree}", "{outside}", "{python}"), "SCRATCH_OUTSIDE_TMP"),
    (("{tree}", "..", "{python}"), "SCRATCH_OUTSIDE_TMP"),
    (("{tree}", ".", "{python}"), "SCRATCH_OUTSIDE_TMP"),
    (("{tree}", "systmp", "{python}"), "SCRATCH_OUTSIDE_TMP"),
    (("{tree}", "CANDIDATES", "{python}"), "SCRATCH_OUTSIDE_TMP"),
    (("{tree}", "{nested}", "{python}"), "SCRATCH_OUTSIDE_TMP"),
    (("{tree}", "{long}", "{python}"), "SCRATCH_PATH_TOO_LONG"),
    (("{tree}", "{blocked}", "{python}"), "SCRATCH_NOT_CREATED"),
])
def test_a_refusal_exits_nine_with_its_token_and_creates_nothing(tree: Path, atc_tmp: Path, tmp_path: Path,
                                                                 arguments: tuple[str, ...], token: str) -> None:
    (atc_tmp / "blocked").write_text("a file", encoding="utf-8")
    values = {"absent": str(tmp_path / "absent/x"), "python": sys.executable, "tree": str(tree),
              "outside": str(tmp_path / "out"), "nested": str(atc_tmp / "a/b"), "blocked": "blocked",
              "long": "a" * (65 - len(str(atc_tmp)))}
    run = run_tests(*(argument.format(**values) for argument in arguments), "tests")
    assert run.returncode == 9, run.stdout + run.stderr
    assert run.stderr.startswith(token + " ")
    assert not (tmp_path / "out").exists() and not (atc_tmp / values["long"]).exists()


def test_the_scratch_bound_is_sixty_four_characters(tree: Path, atc_tmp: Path) -> None:
    name = "a" * (64 - len(str(atc_tmp)) - 1)
    assert run_tests(str(tree), name, sys.executable, "tests").returncode == 0


def test_a_scratch_that_cannot_be_resolved_is_refused(atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(_path):
        raise OSError("cannot read")
    monkeypatch.setattr(scratch.locations, "long_name", unreadable)
    with pytest.raises(Refusal) as refused:
        scratch.resolve_scratch("name", atc_tmp)
    assert refused.value.token == "SCRATCH_NOT_RESOLVED" and refused.value.code == 9


def test_a_path_to_a_direct_child_is_accepted(atc_tmp: Path) -> None:
    assert scratch.resolve_scratch(str(atc_tmp / "s"), atc_tmp) == atc_tmp / "s"


def test_an_unsuitable_root_exits_nine(tree: Path, atc_tmp: Path) -> None:
    run = run_tests(str(tree), "s", sys.executable, "tests", ATC_TMP="relative")
    assert run.returncode == 9 and run.stderr.startswith("ATC_TMP_UNSUITABLE ")


def test_a_pythonpath_directory_absent_after_entering_is_refused(tree: Path, atc_tmp: Path, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(testrun, "pythonpath_directories", lambda _tree: [str(tmp_path / "gone")])
    with pytest.raises(Refusal) as refused:
        testrun.execute(str(tree), "s", sys.executable, ["tests"], dict(os.environ))
    assert refused.value.token == "PYTHONPATH_MISSING" and refused.value.code == 8


@pytest.mark.parametrize("body", ["raise ImportError('broken')\n", "import sys\nraise SystemExit(0) if False else None\nraise RuntimeError('x')\n"])
def test_a_package_that_cannot_load_is_refused(tree: Path, atc_tmp: Path, body: str) -> None:
    (tree / "src/fixture_pkg/__init__.py").write_text(body, encoding="utf-8")
    run = run_tests(str(tree), "s", sys.executable, "tests")
    assert run.returncode == 8 and run.stderr.startswith("COPY_IMPORT_MISMATCH ")


def test_a_package_loaded_from_outside_the_tree_is_refused(tree: Path, atc_tmp: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside).mkdir()
    (outside / "fixture_pkg.py").write_text("X = 1\n", encoding="utf-8")
    body = f"import sys, importlib\nsys.modules.pop(__name__)\nsys.path.remove(sys.path[0]) if False else None\nsys.path.insert(0, {str(outside)!r})\nimport importlib.util\nspec = importlib.util.spec_from_file_location(__name__, {str(outside / 'fixture_pkg.py')!r})\nmodule = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\nsys.modules[__name__] = module\n"
    (tree / "src/fixture_pkg/__init__.py").write_text(body, encoding="utf-8")
    run = run_tests(str(tree), "s", sys.executable, "tests")
    assert run.returncode == 8 and "fixture_pkg" in run.stderr


def test_the_guard_descends_through_namespace_directories(tree: Path, atc_tmp: Path) -> None:
    package = tree / "packages/one/src/name_space/inner"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise ImportError('deep')\n", encoding="utf-8")
    run = run_tests(str(tree), "s", sys.executable, "tests")
    assert run.returncode == 8 and "name_space.inner" in run.stderr


def test_the_registry_gets_one_line_per_scratch(tree: Path, atc_tmp: Path) -> None:
    for _ in range(2):
        assert run_tests(str(tree), "s", sys.executable, "tests").returncode == 0
    (atc_tmp / "s2").mkdir()
    assert run_tests(str(tree), "s2", sys.executable, "tests").returncode == 0
    rows = scratch.registered(atc_tmp)
    assert [(row.project, row.scratch, row.tree) for row in rows] == [
        ("fixture-name", str(atc_tmp / "s"), str(tree.resolve())), ("fixture-name", str(atc_tmp / "s2"), str(tree.resolve()))]


@pytest.mark.skipif(os.name != "nt", reason="a private desktop exists on Windows only")
def test_a_private_desktop_run_excludes_interactive_tests(tree: Path, atc_tmp: Path) -> None:
    body = ("import ctypes, pytest\nfrom ctypes import wintypes\n"
            "def test_private():\n"
            "    u = ctypes.WinDLL('user32'); k = ctypes.WinDLL('kernel32')\n"
            "    u.GetThreadDesktop.restype = wintypes.HANDLE\n"
            "    u.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]\n"
            "    b = ctypes.create_unicode_buffer(256)\n"
            "    u.GetUserObjectInformationW(u.GetThreadDesktop(k.GetCurrentThreadId()), 2, b, 512, None)\n"
            "    assert b.value.startswith('atc_')\n"
            "@pytest.mark.interactive_desktop\ndef test_interactive():\n    assert False\n")
    (tree / "tests/test_desktop.py").write_text(body, encoding="utf-8")
    (tree / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers = ["interactive_desktop: x"]\n', encoding="utf-8")
    for value in ("1", ""):
        run = run_tests(str(tree), "s", sys.executable, "tests/test_desktop.py",
                          ATC_PRIVATE_DESKTOP=value)
        assert run.returncode == 0, run.stdout + run.stderr
        assert "1 passed, 1 deselected" in run.stdout and testrun.EXCLUSION_NOTE in run.stdout


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="a private desktop exists on Windows only")
def test_zero_runs_on_the_interactive_desktop_and_another_value_is_refused(tree: Path, atc_tmp: Path) -> None:
    body = "import pytest\n@pytest.mark.interactive_desktop\ndef test_interactive():\n    pass\n"
    (tree / "tests/test_desktop.py").write_text(body, encoding="utf-8")
    (tree / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers = ["interactive_desktop: x"]\n', encoding="utf-8")
    run = run_tests(str(tree), "s", sys.executable, "tests/test_desktop.py", ATC_PRIVATE_DESKTOP="0")
    assert run.returncode == 0 and "1 passed" in run.stdout and testrun.EXCLUSION_NOTE not in run.stdout
    run = run_tests(str(tree), "s", sys.executable, "tests", ATC_PRIVATE_DESKTOP="yes")
    assert run.returncode == 9 and run.stderr.startswith("PRIVATE_DESKTOP_INVALID ")


def test_the_exclusion_joins_a_given_marker_expression() -> None:
    def excluded(arguments):
        expression, rest = python_toolchain.split_marker(arguments)
        return python_toolchain.combine(expression, "not interactive_desktop"), rest
    assert excluded(["tests"]) == ("not interactive_desktop", ["tests"])
    assert excluded(["-m", "slow", "tests"]) == ("(slow) and not interactive_desktop", ["tests"])
    assert excluded(["-munit", "-x"]) == ("(unit) and not interactive_desktop", ["-x"])


@pytest.mark.integration
def test_a_run_without_its_scratch_option_prints_the_usage() -> None:
    run = run_command("test", "run", "a", "b", "c")
    assert run.returncode == 2 and "--scratch" in run.stderr


# ``atc test mutants``


GIT_IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false")


class Export:
    def __init__(self, worktree: Path, sha: str, place: Path):
        self.worktree, self.sha, self.place = worktree, sha, place
        self.file = place / "tree/src/fixture_pkg/__init__.py"

    def run(self, tmp_path: Path, mutants: list[dict], *options: str, **env: str) -> subprocess.CompletedProcess[str]:
        listing = tmp_path / "mutants.json"
        listing.write_text(json.dumps(mutants), encoding="utf-8")
        return run_command("test", "mutants", self.place.name, self.sha, str(self.worktree), str(listing),
                           "--python", sys.executable, *options, "--tests", "tests", **env)


@pytest.fixture()
def export(tree: Path, atc_tmp: Path) -> Export:
    (tree / "tests/test_value.py").write_text(
        "import fixture_pkg\ndef test_value():\n    assert fixture_pkg.value() == 1\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def git(*args: str) -> str:
        return subprocess.run(["git", *GIT_IDENTITY, *args], cwd=tree, env=env, capture_output=True, text=True,
                              check=True).stdout.strip()
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-q", "-m", "x")
    sha = git("rev-parse", "HEAD")
    place = atc_tmp / "m"
    target = place / "tree"
    target.mkdir(parents=True)
    for path in git("ls-files").splitlines():
        (target / path).parent.mkdir(parents=True, exist_ok=True)
        (target / path).write_bytes((tree / path).read_bytes())
    return Export(tree, sha, place)


KILLED = {"label": "K one", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "return 1", "new": "return 2"}]}
SURVIVING = {"label": "S two", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "import os, tempfile", "new": "import os"}]}


def test_mutants_are_killed_or_survive_and_files_are_restored(export: Export, tmp_path: Path) -> None:
    run = export.run(tmp_path, [KILLED, SURVIVING])
    lines = [line for line in run.stdout.splitlines() if line != testrun.EXCLUSION_NOTE]
    assert run.returncode == 0, run.stdout + run.stderr
    assert lines[0].startswith("BASELINE-OK")
    assert lines[1].startswith("KILLED") and "K one" in lines[1] and "FAILED" in lines[1]
    assert lines[2].startswith("SURVIVED") and "S two" in lines[2]
    assert lines[3] == "restored-ok src/fixture_pkg/__init__.py"
    assert lines[-1] == "done: 2 mutants, 1 survived S two"
    assert b"return 1" in export.file.read_bytes()


ERRORING = {"label": "E three", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "return 1", "new": "return 1 / 0"}]}


def mutant_lines(run: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in run.stdout.splitlines() if line != testrun.EXCLUSION_NOTE]


@pytest.mark.integration
def test_the_assertion_rule_kills_only_by_assertion(export: Export, tmp_path: Path) -> None:
    run = export.run(tmp_path, [KILLED, ERRORING], "--kill-rule", "assertion")
    lines = mutant_lines(run)
    assert run.returncode == 1, run.stdout + run.stderr
    assert lines[1].startswith("KILLED") and "K one" in lines[1]
    assert lines[2].startswith("INCONCLUSIVE E three -> failed by") and "not by an assertion" in lines[2]
    assert lines[-1] == "done: 2 of 2 mutants, 0 survived"
    assert sorted(path.name for path in (export.place / "junit").iterdir()) == [
        "000-baseline.xml", "001-K_one.xml", "002-E_three.xml"]


@pytest.mark.integration
def test_the_failure_rule_kills_on_any_failed_test(export: Export, tmp_path: Path) -> None:
    run = export.run(tmp_path, [ERRORING])
    assert run.returncode == 0 and mutant_lines(run)[1].startswith("KILLED"), run.stdout + run.stderr
    assert not (export.place / "junit").exists()


@pytest.mark.integration
def test_a_mutants_own_tests_judge_it_and_join_the_baseline(export: Export, tmp_path: Path) -> None:
    (export.place / "tree/tests/test_other.py").write_text("def test_other():\n    pass\n", encoding="utf-8")
    run = export.run(tmp_path, [{**KILLED, "tests": ["tests/test_other.py"]}])
    assert run.returncode == 0 and mutant_lines(run)[1].startswith("SURVIVED K one"), run.stdout + run.stderr
    (export.place / "tree/tests/test_other.py").write_text("def test_other():\n    assert 0\n", encoding="utf-8")
    run = export.run(tmp_path, [{**KILLED, "tests": ["tests/test_other.py"]}])
    assert run.returncode == 1 and mutant_lines(run)[0].startswith("BASELINE-RED")


def junit_report(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "r.xml"
    path.write_text(f'<testsuites><testsuite><testcase name="t">{body}</testcase></testsuite></testsuites>',
                    encoding="utf-8")
    return path


@pytest.mark.unit
@pytest.mark.parametrize(("body", "verdict"), [
    ('<failure message="assert 2 == 1"/>', testrun.KILLED),
    ('<failure message="AssertionError: x"/>', testrun.KILLED),
    ('<failure message="Failed: DID NOT RAISE &lt;class \'ValueError\'&gt;"/>', testrun.KILLED),
    ('<failure message="Failed: boom"/>', testrun.INCONCLUSIVE),
    ('<failure message="ZeroDivisionError: division by zero"/>', testrun.INCONCLUSIVE),
    ('<failure message="assert 0"/></testcase><testcase name="u"><error message="fixture"/>', testrun.INCONCLUSIVE),
])
def test_the_assertion_rule_reads_the_junit_report(tmp_path: Path, body: str, verdict: str) -> None:
    failed = testrun.Result(1, ["FAILED tests/test_value.py::test_value - x"])
    assert testrun._verdict(failed, junit_report(tmp_path, body))[0] == verdict
    assert testrun._verdict(failed)[0] == testrun.KILLED
    assert testrun._verdict(failed, tmp_path / "absent.xml") == (testrun.INCONCLUSIVE, "no junit report (FileNotFoundError)")


def test_a_red_baseline_applies_no_mutant(export: Export, tmp_path: Path) -> None:
    export.file.write_text("def value():\n    return 9\n", encoding="utf-8")
    applicable = {"label": "K one", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "return 9", "new": "return 1"}]}
    run = export.run(tmp_path, [applicable])
    lines = [line for line in run.stdout.splitlines() if line != testrun.EXCLUSION_NOTE]
    assert run.returncode == 1 and lines[0].startswith("BASELINE-RED") and "K one" not in run.stdout + run.stderr


@pytest.mark.parametrize("old", ["absent text", "\n"])
def test_an_old_that_does_not_occur_exactly_once_aborts(export: Export, tmp_path: Path, old: str) -> None:
    edit = {"label": "bad", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": old, "new": "x"}]}
    run = export.run(tmp_path, [edit, KILLED])
    assert run.returncode == 1 and "need exactly 1" in run.stderr and "K one" not in run.stdout


def test_edits_follow_the_files_line_endings(export: Export, tmp_path: Path) -> None:
    edit = {"label": "crlf", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "def value():\n    return 1",
                                        "new": "def value():\n    return 3"}]}
    run = export.run(tmp_path, [edit])
    assert run.returncode == 0 and "KILLED" in run.stdout and "restored-ok" in run.stdout
    assert b"\r\n" in export.file.read_bytes()


@pytest.mark.integration
def test_a_same_size_write_in_the_files_last_second_reaches_the_run(export: Export, tmp_path: Path,
                                                                     capsys: pytest.CaptureFixture[str],
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    write_bytes = Path.write_bytes

    def in_the_same_second(self: Path, data: bytes) -> int:
        if os.path.normcase(self) != os.path.normcase(export.file):
            return write_bytes(self, data)
        last = self.stat().st_mtime
        written = write_bytes(self, data)
        os.utime(self, (last, last))
        return written
    listing = tmp_path / "mutants.json"
    other_file = {"label": "other file", "edits": [{"path": "tests/test_value.py", "old": "def test_value():",
                                                    "new": "def test_value(): "}]}
    listing.write_text(json.dumps([KILLED, other_file]), encoding="utf-8")
    bytecode_written = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
    with monkeypatch.context() as held_writes:
        held_writes.setattr(Path, "write_bytes", in_the_same_second)
        held_writes.chdir(tmp_path)
        code = testrun.mutants([export.place.name, export.sha, str(export.worktree), str(listing), "--python",
                                sys.executable, "--tests", "tests"], bytecode_written)
    lines = [line for line in capsys.readouterr().out.splitlines() if line != testrun.EXCLUSION_NOTE]
    assert code == 0, lines
    assert lines[1].startswith("KILLED") and "K one" in lines[1]
    assert lines[2].startswith("SURVIVED") and "other file" in lines[2]


def test_an_import_guard_refusal_aborts_and_restores(export: Export, tmp_path: Path) -> None:
    edit = {"label": "guard", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "def value():", "new": "raise ImportError('x')\ndef value():"}]}
    run = export.run(tmp_path, [edit, KILLED])
    assert run.returncode == 8 and "COPY_IMPORT_MISMATCH" in run.stderr and "K one" not in run.stdout
    assert "restored-ok src/fixture_pkg/__init__.py" in run.stdout and b"raise" not in export.file.read_bytes()


def test_a_file_that_differs_from_its_object_is_a_restore_mismatch(export: Export, tmp_path: Path) -> None:
    export.file.write_text(export.file.read_text(encoding="utf-8") + "# drifted\n", encoding="utf-8")
    run = export.run(tmp_path, [KILLED])
    assert run.returncode == 1 and "RESTORE-MISMATCH src/fixture_pkg/__init__.py" in run.stdout


@pytest.mark.parametrize(("result", "verdict"), [
    (testrun.Result(0, ["1 passed"]), testrun.SURVIVED),
    (testrun.Result(1, ["FAILED tests/a.py::t", "ERROR tests/b.py::t"]), testrun.KILLED),
    (testrun.Result(1, ["ERROR tests/a.py::t - PermissionError"]), testrun.INCONCLUSIVE),
    (testrun.Result(1, ["no summary"]), testrun.INCONCLUSIVE),
    (testrun.Result(2, ["FAILED looks like a failure"]), testrun.INCONCLUSIVE),
    (testrun.Result(3, []), testrun.INCONCLUSIVE),
    (testrun.Result(4, []), testrun.INCONCLUSIVE),
    (testrun.Result(5, []), testrun.INCONCLUSIVE),
    (testrun.Result(None, []), testrun.INCONCLUSIVE),
])
def test_only_a_failed_test_is_a_kill(result: testrun.Result, verdict: str) -> None:
    assert testrun._verdict(result)[0] == verdict


@pytest.mark.parametrize(("old", "new", "expected"), [
    ("import fixture_pkg\n", "raise RuntimeError('at collection')\nimport fixture_pkg\n", "pytest exit 2"),
    ("def test_value():", "import pytest\n@pytest.fixture(autouse=True)\ndef broken():\n    raise RuntimeError('at setup')\n"
                          "def test_value():", "pytest exit 1: ERROR"),
])
def test_a_run_no_test_failed_in_is_inconclusive_and_ends_the_list(export: Export, tmp_path: Path, old: str, new: str,
                                                                    expected: str) -> None:
    edit = {"label": "odd", "edits": [{"path": "tests/test_value.py", "old": old, "new": new}]}
    run = export.run(tmp_path, [edit, KILLED])
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"INCONCLUSIVE odd -> {expected}" in run.stdout and "K one" not in run.stdout
    assert "KILLED" not in run.stdout and "restored-ok tests/test_value.py" in run.stdout
    assert run.stdout.splitlines()[-1] == "done: 1 of 2 mutants, 0 survived"


def test_an_abort_on_the_last_mutant_prints_the_aborted_form(export: Export, tmp_path: Path) -> None:
    odd = {"label": "odd", "edits": [{"path": "tests/test_value.py", "old": "import fixture_pkg\n",
                                      "new": "raise RuntimeError('at collection')\nimport fixture_pkg\n"}]}
    run = export.run(tmp_path, [KILLED, odd])
    assert run.returncode == 1, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1] == "done: 2 of 2 mutants, 0 survived"
    run = export.run(tmp_path, [odd])
    assert run.returncode == 1, run.stdout + run.stderr
    assert run.stdout.splitlines()[-1] == "done: 1 of 1 mutants, 0 survived"


def test_an_exception_during_a_mutant_still_restores_and_reports(export: Export, tmp_path: Path,
                                                                  capsys: pytest.CaptureFixture[str],
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter([testrun.Result(0, ["1 passed"])])

    def execute(*_args, **_kwargs):
        for result in results:
            return result
        raise TimeoutError("CHILD_TREE_STILL_RUNNING")
    monkeypatch.setattr(testrun, "execute", execute)
    listing = tmp_path / "mutants.json"
    listing.write_text(json.dumps([KILLED]), encoding="utf-8")
    code = testrun.mutants([export.place.name, export.sha, str(export.worktree), str(listing), "--python", sys.executable,
                            "--tests", "tests"])
    captured = capsys.readouterr()
    assert code == 1 and captured.err.splitlines()[0] == "TimeoutError: CHILD_TREE_STILL_RUNNING"
    assert captured.out.splitlines()[-2:] == ["restored-ok src/fixture_pkg/__init__.py", "done: 0 of 1 mutants, 0 survived"]
    assert b"return 1" in export.file.read_bytes()


def test_an_error_applying_a_mutant_still_reports(export: Export, tmp_path: Path) -> None:
    run = export.run(tmp_path, [{"label": "a directory", "edits": [{"path": "src", "old": "x", "new": "y"}]}])
    assert run.returncode == 1 and "Traceback" not in run.stderr and run.stderr.strip()
    assert run.stdout.splitlines()[-1] == "done: 0 of 1 mutants, 0 survived"


def test_a_timeout_names_itself_as_the_reason() -> None:
    assert "timed out" in testrun._verdict(testrun.Result(None, []))[1]


def test_a_timeout_returns_no_code(tmp_path: Path) -> None:
    result = testrun._capture([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, os.environ, False, 1.0)
    assert result.code is None


def test_a_result_line_stays_on_stdout_and_a_refusal_goes_to_stderr(atc_tmp: Path, capsys: pytest.CaptureFixture[str],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATC_TMP", "relative")
    assert commands.main(["tmp", "list"]) == 9
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("ATC_TMP_UNSUITABLE ")
    monkeypatch.setenv("ATC_TMP", str(atc_tmp))
    (atc_tmp / "s").mkdir()
    assert commands.main(["tmp", "clean", "s"]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"DELETED {atc_tmp / 's'}\n" and captured.err == ""
    assert commands.main(["tmp", "clean", "systmp"]) == 1
    captured = capsys.readouterr()
    assert captured.out.startswith("REFUSED ") and captured.err.startswith("STOP SCRATCH_REFUSED: 1 refused;")


def test_mutants_need_the_export(export: Export, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    listing = tmp_path / "m.json"
    listing.write_text("[]", encoding="utf-8")
    other = export.place.parent / "other"
    code = commands.main(["test", "mutants", other.name, export.sha, str(export.worktree), str(listing),
                          "--python", sys.executable, "--tests", "tests"])
    assert code == 9 and capsys.readouterr().err.startswith("TREE_NOT_FOUND ")


@pytest.mark.integration
@pytest.mark.parametrize("sha", ["0" * 40, "not-a-revision"])
def test_mutants_refuse_a_sha_that_names_no_commit_before_the_baseline(
        export: Export, tmp_path: Path, capsys: pytest.CaptureFixture[str], sha: str) -> None:
    listing = tmp_path / "m.json"
    listing.write_text(json.dumps([KILLED]), encoding="utf-8")
    other = export.place.parent / "other"
    code = commands.main(["test", "mutants", other.name, sha, str(export.worktree), str(listing),
                          "--python", sys.executable, "--tests", "tests"])
    captured = capsys.readouterr()
    assert code == 9 and captured.err.startswith(f"SHA_NOT_FOUND {sha} names no commit in ")
    assert captured.out == "" and not other.exists()


# dispatch


@pytest.mark.parametrize("arguments", [("test",), ("test", "other"), ("tmp",), ("tmp", "list", "x"), ("tmp", "list", "--json", "x"), ("tmp", "clean"),
                                       ("tmp", "clean", "--tree-name", "p"), ("tmp", "clean", "--project"),
                                       ("tmp", "clean", "--tree"), ("other",)])
def test_a_wrong_command_exits_two(arguments: tuple[str, ...], atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert commands.main(list(arguments)) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("usage: atc ")


@pytest.mark.parametrize(("arguments", "attribute", "module", "expected"), [
    (["test", "run", "--scratch", "a"], "run", testrun, (["--scratch", "a"],)),
    (["test", "mutants", "a"], "mutants", testrun, (["a"],)),
    (["tmp", "list"], "list_scratches", scratch, ()),
    (["tmp", "list", "--json"], "list_scratches", scratch, ({"as_json": True},)),
    (["tmp", "prune"], "prune", scratch, ()),
    (["tmp", "clean", "a", "b"], "clean", scratch, (["a", "b"], {"with_runs": False})),
    (["tmp", "clean", "--with-runs", "a"], "clean", scratch, (["a"], {"with_runs": True})),
])
def test_atc_reaches_the_package_with_its_arguments_and_returns_its_exit_code(
        monkeypatch: pytest.MonkeyPatch, arguments: list[str], attribute: str, module, expected: tuple) -> None:
    seen = []

    def record(*args, say=None, **kwargs):
        assert say is None or callable(say)  # tmp clean and prune pass the line printer that counts REFUSED lines
        seen.append(args + ((kwargs,) if kwargs else ()))
        return 41
    monkeypatch.setattr(module, attribute, record)
    assert atc_cli.main(arguments) == 41
    assert seen == [expected]


def test_a_prune_refusal_ends_with_a_stop_line_on_stderr(monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    def refusing(*, say):
        say("DELETED a")
        say("REFUSED b (is itself a link)")
        return 1
    monkeypatch.setattr(scratch, "prune", refusing)
    assert commands.main(["tmp", "prune"]) == 1
    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["DELETED a", "REFUSED b (is itself a link)"]
    assert captured.err.startswith("STOP PRUNE_REFUSED: 1 refused;")


@pytest.mark.integration
def test_mutants_refuse_an_invalid_private_desktop_value_before_the_baseline(
        export: Export, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    listing = tmp_path / "m.json"
    listing.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("ATC_PRIVATE_DESKTOP", "yes")
    code = commands.main(["test", "mutants", export.place.name, export.sha, str(export.worktree), str(listing),
                          "--python", sys.executable, "--tests", "tests"])
    captured = capsys.readouterr()
    assert code == 9 and captured.err.startswith("PRIVATE_DESKTOP_INVALID ") and "BASELINE" not in captured.out

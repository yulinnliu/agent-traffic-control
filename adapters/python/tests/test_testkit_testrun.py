"""docs/TESTKIT.md § Commands: the scratch run and the mutant run behind ``atc test ID``."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from atc_testkit import elapsed, isolation, lines, locations, python_toolchain, scratch, testrun, workers
from atc_testkit.locations import Refusal

TASK = "ATC-0044"
# A child process runs one of testrun's entry points; a refusal is printed as ``TOKEN detail`` with its exit code.
ENTRY = ("import sys\n"
         "from atc_testkit import testrun\n"
         "from atc_testkit.locations import Refusal\n"
         "try:\n"
         "    code = getattr(testrun, sys.argv[1])(sys.argv[2:])\n"
         "except Refusal as refusal:\n"
         "    print(f'{refusal.token} {refusal.detail}', file=sys.stderr)\n"
         "    code = refusal.code\n"
         "sys.exit(code)\n")


def mutants(argv: list[str]) -> int:
    """``testrun.mutants`` in this process, a refusal printed as the child prints it."""
    try:
        return testrun.mutants(argv)
    except Refusal as refusal:
        print(f"{refusal.token} {refusal.detail}", file=sys.stderr)
        return refusal.code
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


def run_command(entry: str, *args: str, cwd: Path | None = None, **env: str) -> subprocess.CompletedProcess[str]:
    """``testrun.<entry>(args)`` in a child process with the caller's environment."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    environment.update(env, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-c", ENTRY, entry, *args], cwd=cwd, env=environment,
                          capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)


def run_tests(tree: Path | str, scratch_name: str, python: str, *args: str, cwd: Path | None = None,
              **env: str) -> subprocess.CompletedProcess[str]:
    """One scratch run with pytest's output streamed, so a test's prints reach stdout."""
    return run_command("run", "--scratch", scratch_name, "--task", TASK, "--tree", str(tree), "--python", python,
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
    run = run_tests(tree, "s1", sys.executable, "-s", "-n", "0", "tests", HAMI_X="set", ATC_PRIVATE_DESKTOP="0")
    assert run.returncode == 0, run.stdout + run.stderr
    lines = run.stdout.splitlines()
    scratch_dir = atc_tmp / "s1"
    values = dict(line.split(" ", 1) for line in lines if line.split(" ")[0] in ("TEMPDIR", "BASE", "DATA", "HAMI", "FILE"))
    assert values["TEMPDIR"] == str(scratch_dir)
    assert Path(values["BASE"]).is_relative_to(scratch_dir / "bt")
    assert values["DATA"] == f"{scratch_dir / 'qdr'} None"
    assert values["HAMI"] == "None"
    assert Path(values["FILE"]).is_relative_to(tree.resolve())


ENVIRONMENT_TEST = """import json, os
from pathlib import Path

def test_environment():
    names = ["HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CACHE_HOME", "UV_CACHE_DIR",
             "UV_PYTHON_INSTALL_DIR", "RUFF_CACHE_DIR", "PRE_COMMIT_HOME", "PYTHONIOENCODING", "GIT_X", "SOME_API_KEY", "MY_CRED",
             "QUANT_DATA_ROOT"]
    seen = {name: os.environ.get(name) for name in names}
    seen["gitconfig"] = (Path(os.environ["HOME"]) / ".gitconfig").is_file()
    print("ENV " + json.dumps(seen))
"""


@pytest.mark.integration
def test_a_runs_tests_get_the_verifiers_environment_with_the_scratch_as_working_directory(tree: Path,
                                                                                         atc_tmp: Path) -> None:
    (tree / "pyproject.toml").write_text('[tool.atc-testkit]\ncredential_variables = ["MY_CRED"]\n', encoding="utf-8")
    (tree / "tests/test_environment.py").write_text(ENVIRONMENT_TEST, encoding="utf-8")
    run = run_tests(tree, "s", sys.executable, "-s", "-n", "0", "tests/test_environment.py", GIT_X="1",
                    SOME_API_KEY="k", MY_CRED="c", PYTHONIOENCODING="cp1252")
    assert run.returncode == 0, run.stdout + run.stderr
    seen = json.loads(next(line for line in run.stdout.splitlines() if line.startswith("ENV "))[4:])
    place = atc_tmp / "s"
    caches = locations.cache_directories(dict(os.environ))
    reference = isolation.IsolatedRun(place, environ=dict(os.environ, QUANT_DATA_ROOT=str(place / "qdr")),
                                      credentials=["MY_CRED"]).environment(testrun.CHILD_STAGE)
    assert seen == {"HOME": str(place / "home"), "USERPROFILE": str(place / "home"),
                    "APPDATA": reference["APPDATA"], "LOCALAPPDATA": reference["LOCALAPPDATA"],
                    "XDG_CACHE_HOME": reference["XDG_CACHE_HOME"], **caches, "PYTHONIOENCODING": "utf-8",
                    "GIT_X": None, "SOME_API_KEY": None, "MY_CRED": None, "QUANT_DATA_ROOT": str(place / "qdr"),
                    "gitconfig": True}


def test_the_exit_code_is_pytests(tree: Path, atc_tmp: Path) -> None:
    (tree / "tests/test_bad.py").write_text("def test_bad(): assert False\n", encoding="utf-8")
    assert run_tests(str(tree), "s", sys.executable, "tests").returncode == 1


@pytest.mark.integration
def test_a_run_prints_and_records_its_elapsed_time_before_its_summary(tree: Path, atc_tmp: Path) -> None:
    (tree / "tests/test_bad.py").write_text("def test_bad(): assert False\n", encoding="utf-8")
    before = len(pool_records())
    run = run_command("run", "--scratch", "s", "--task", TASK, "--tree", str(tree), "--python", sys.executable,
                      "--label", "related", "--", "-n", "0", "tests")
    lines = run.stdout.splitlines()
    assert run.returncode == 1 and lines[-1].startswith("FAIL "), run.stdout + run.stderr
    assert re.fullmatch(r"ELAPSED related \d+\.\d+s workers 1 FAIL", lines[-2]), lines
    added = pool_records()[before:]
    assert [(r["task"], r["command"], r["stage"], r["workers"], r["result"]) for r in added] == [
        (TASK, "test", "related", 1, "FAIL")]
    assert added[0]["sha"] == "" and added[0]["seconds"] >= 0


@pytest.mark.unit
def test_stage_records_append_one_line_each_and_a_reader_skips_what_is_no_object(tmp_path: Path,
                                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    clock = elapsed.Clock("ATC-0001", "a" * 40, "test", tmp_path / "run")
    assert clock.stage("all", 12.34, 4, "PASS") == "ELAPSED all 12.3s workers 4 PASS"
    assert capsys.readouterr().out == "ELAPSED all 12.3s workers 4 PASS\n"
    with (tmp_path / "run" / elapsed.FILE).open("a", encoding="utf-8") as handle:
        handle.write("not json\n[1]\n")
    elapsed.Clock("", "", "verify", tmp_path / "run").stage("checks", 0.04, 1, "FAIL")
    assert [(r["task"], r["sha"], r["command"], r["stage"], r["seconds"], r["workers"], r["result"])
            for r in elapsed.records(tmp_path / "run")] == [
        ("ATC-0001", "a" * 40, "test", "all", 12.3, 4, "PASS"), ("", "", "verify", "checks", 0.0, 1, "FAIL")]
    assert elapsed.records(tmp_path / "absent") == []
    elapsed.Clock("x", "y", "test", None).stage("all", 1, 1, "PASS")
    assert not (tmp_path / "None").exists()


@pytest.mark.integration
def test_a_runs_commit_is_its_export_source_else_its_trees_head(tree: Path) -> None:
    exported = types.SimpleNamespace(commit="c" * 40)
    assert testrun.tree_commit(tree, exported) == "c" * 40
    assert testrun.tree_commit(tree, None) == ""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    for args in (("init", "-q"), ("add", "."), ("commit", "-q", "-m", "x")):
        subprocess.run(["git", *GIT_IDENTITY, *args], cwd=tree, env=env, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()
    assert testrun.tree_commit(tree / "tests", None) == head
    assert testrun.tree_commit(tree, types.SimpleNamespace(commit="")) == head
    assert testrun.tree_commit(tree, exported) == "c" * 40


@pytest.mark.unit
@pytest.mark.parametrize(("arguments", "count"), [
    ([], 1), (["-n", "0"], 1), (["-n", "6"], 6), (["-n6"], 6), (["--numprocesses=3", "x"], 3), (["-n", "auto"], 1)])
def test_a_runs_recorded_workers_are_its_last_worker_option(arguments: list[str], count: int) -> None:
    assert testrun._run_workers(arguments) == count


def test_the_tree_names_its_data_roots(tree: Path, atc_tmp: Path) -> None:
    (tree / "pyproject.toml").write_text('[tool.atc-testkit]\ndata_root_variables = ["OTHER_ROOT"]\n', encoding="utf-8")
    run = run_tests(str(tree), "s", sys.executable, "-s", "-n", "0", "tests", QUANT_DATA_ROOT="ambient")
    assert f"DATA ambient {atc_tmp / 's/qdr'}" in run.stdout


@pytest.mark.integration
def test_the_runs_worker_option_wins_over_addopts_and_the_cache_provider_is_off(tree: Path,
                                                                               atc_tmp: Path) -> None:
    (tree / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-n 2"\n', encoding="utf-8")
    run = run_tests(str(tree), "s", sys.executable, "-s", "-n", "0", "tests")
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
        testrun.execute(str(tree), "s", sys.executable, ["tests"], dict(os.environ), task=TASK)
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
        run = run_tests(str(tree), "s", sys.executable, "-n", "0", "tests/test_desktop.py",
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
    run = run_command("run", "a", "b", "c")
    assert run.returncode == 2 and "--scratch" in run.stderr


# the mutant run


GIT_IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false")


class Export:
    def __init__(self, worktree: Path, sha: str, place: Path):
        self.worktree, self.sha, self.place = worktree, sha, place
        self.file = place / "tree/src/fixture_pkg/__init__.py"

    def run(self, tmp_path: Path, mutants: list[dict], *options: str, **env: str) -> subprocess.CompletedProcess[str]:
        listing = tmp_path / "mutants.json"
        listing.write_text(json.dumps(mutants), encoding="utf-8")
        return run_command("mutants", self.place.name, self.sha, str(self.worktree), str(listing),
                           "--task", TASK, "--python", sys.executable, *options, "--tests", "tests", **env)


@pytest.fixture()
def export(tree: Path, atc_tmp: Path) -> Export:
    (tree / "tests/test_value.py").write_text(
        "import fixture_pkg\ndef test_value():\n    assert fixture_pkg.value() == 1\n", encoding="utf-8")
    return make_export(tree, atc_tmp)


def make_export(tree: Path, atc_tmp: Path) -> Export:
    """``tree`` committed and copied into the scratch ``m`` as its export."""
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
    lines = mutant_lines(run)
    assert run.returncode == 1, run.stdout + run.stderr  # a survivor exits 1
    assert lines[0].startswith("BASELINE-OK")
    assert lines[1].startswith("KILLED") and "K one" in lines[1] and "FAILED" in lines[1]
    assert lines[2].startswith("SURVIVED") and "S two" in lines[2]
    assert lines[3] == "restored-ok src/fixture_pkg/__init__.py"
    assert lines[-1] == "done: 2 mutants, 1 survived S two"
    assert b"return 1" in export.file.read_bytes()


@pytest.mark.integration
def test_mutant_runs_get_the_verifiers_environment(export: Export, tmp_path: Path) -> None:
    home = export.place / "home"
    caches = locations.cache_directories(dict(os.environ))
    (export.place / "tree/tests/test_value.py").write_text(
        "import os\nimport fixture_pkg\n\ndef test_value():\n    assert fixture_pkg.value() == 1\n"
        f"    assert os.environ['HOME'] == {str(home)!r} and os.environ['UV_CACHE_DIR'] == {caches['UV_CACHE_DIR']!r}\n"
        "    assert 'GIT_X' not in os.environ and os.environ['PYTHONIOENCODING'] == 'utf-8'\n", encoding="utf-8")
    run = export.run(tmp_path, [KILLED], GIT_X="1")
    lines = mutant_lines(run)
    assert lines[0].startswith("BASELINE-OK") and lines[1].startswith("KILLED"), run.stdout + run.stderr


ERRORING = {"label": "E three", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "return 1", "new": "return 1 / 0"}]}


def mutant_lines(run: subprocess.CompletedProcess[str] | str) -> list[str]:
    """The command's lines without the private-desktop note, the baseline's workers line and the stage times."""
    out = run if isinstance(run, str) else run.stdout
    return [line for line in out.splitlines()
            if line != testrun.EXCLUSION_NOTE and not line.startswith((f"{elapsed.PREFIX} ", "workers: "))]


def elapsed_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(f"{elapsed.PREFIX} ")]


def pool_records() -> list[dict]:
    """The stage-time records of the session's worker pool directory, where the conftest points every run."""
    return elapsed.records(Path(os.environ[workers.POOL_DIRECTORY]))


@pytest.mark.integration
def test_each_mutant_and_the_baseline_print_and_record_their_elapsed_time(export: Export, tmp_path: Path) -> None:
    before = len(pool_records())
    run = export.run(tmp_path, [KILLED, SURVIVING])
    shown = elapsed_lines(run.stdout)
    assert [line.split()[1] for line in shown] == ["baseline", "mutant:K", "mutant:S"], run.stdout
    assert [line.split()[-1] for line in shown] == ["PASS", "KILLED", "SURVIVED"]
    # the baseline runs the workers it was granted, and so does each mutant judged by its file-level tests
    baseline = re.fullmatch(r"ELAPSED baseline \d+\.\d+s workers (\d+) PASS", shown[0])
    assert baseline is not None, shown
    granted = int(baseline.group(1))
    assert all(re.fullmatch(rf"ELAPSED \S+ \S+ \d+\.\d+s workers {granted} \S+", line) for line in shown[1:]), shown
    added = pool_records()[before:]
    assert [(r["task"], r["sha"], r["command"], r["stage"], r["workers"], r["result"]) for r in added] == [
        (TASK, export.sha, "mutants", "baseline", granted, "PASS"),
        (TASK, export.sha, "mutants", "mutant:K one", granted, "KILLED"),
        (TASK, export.sha, "mutants", "mutant:S two", granted, "SURVIVED"),
    ]
    assert all(isinstance(r["seconds"], float) and r["seconds"] >= 0 for r in added)


@pytest.mark.integration
def test_the_assertion_rule_kills_only_by_assertion(export: Export, tmp_path: Path) -> None:
    run = export.run(tmp_path, [KILLED, ERRORING], "--kill-rule", "assertion")
    lines = mutant_lines(run)
    assert run.returncode == 1, run.stdout + run.stderr
    assert lines[1].startswith("KILLED") and "K one" in lines[1]
    assert lines[2].startswith("INCONCLUSIVE E three -> failed by") and "not by an assertion" in lines[2]
    assert lines[-1] == "done: 2 of 2 mutants, 0 survived"
    # the baseline runs under the failure rule, so only the mutants under the assertion rule leave a report
    assert sorted(path.name for path in (export.place / "junit").iterdir()) == ["001-K_one.xml", "002-E_three.xml"]


@pytest.mark.integration
def test_the_failure_rule_kills_on_any_failed_test(export: Export, tmp_path: Path) -> None:
    run = export.run(tmp_path, [ERRORING])
    assert run.returncode == 0 and mutant_lines(run)[1].startswith("KILLED"), run.stdout + run.stderr
    assert not (export.place / "junit").exists()


@pytest.mark.integration
def test_a_mutants_own_tests_judge_it_and_join_the_baseline(export: Export, tmp_path: Path) -> None:
    (export.place / "tree/tests/test_other.py").write_text("def test_other():\n    pass\n", encoding="utf-8")
    run = export.run(tmp_path, [{**KILLED, "tests": ["tests/test_other.py"]}])
    assert run.returncode == 1 and mutant_lines(run)[1].startswith("SURVIVED K one"), run.stdout + run.stderr
    (export.place / "tree/tests/test_other.py").write_text("def test_other():\n    assert 0\n", encoding="utf-8")
    (export.place / testrun.BASELINE_RECORD).unlink()  # a hand edit of the export is outside the reuse key
    run = export.run(tmp_path, [{**KILLED, "tests": ["tests/test_other.py"]}])
    assert run.returncode == 1 and mutant_lines(run)[0].startswith("BASELINE-RED")


LINES_MODULE = ("def value():\n    return 1\n\n\n"
                "def unused():\n    return 5\n\n\n"
                "def child_only():\n    return 7\n")
CHILD_TEST = ("import subprocess, sys\n\n\n"
              "def test_child():\n"
              "    code = 'import sys, fixture_pkg; sys.exit(0 if fixture_pkg.child_only() == 7 else 3)'\n"
              "    assert subprocess.run([sys.executable, '-c', code]).returncode == 0\n")
BY_LINES = [
    {"label": "K value", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "return 1", "new": "return 2"}]},
    {"label": "U unused", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "return 5", "new": "return 6"}]},
    {"label": "C child", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "return 7", "new": "return 8"}]},
    {"label": "S value", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "return 1", "new": "return 1 + 0"}]},
]


@pytest.fixture()
def lines_export(tree: Path, atc_tmp: Path) -> Export:
    (tree / "src/fixture_pkg/__init__.py").write_text(LINES_MODULE, encoding="utf-8")
    (tree / "tests/test_value.py").write_text(
        "import fixture_pkg\n\n\ndef test_value():\n    assert fixture_pkg.value() == 1\n", encoding="utf-8")
    (tree / "tests/test_child.py").write_text(CHILD_TEST, encoding="utf-8")
    (tree / "tests/test_other.py").write_text("def test_other():\n    pass\n", encoding="utf-8")
    return make_export(tree, atc_tmp)


@pytest.mark.integration
def test_with_lines_each_mutant_runs_only_the_tests_that_ran_its_lines_also_in_child_processes(
        lines_export: Export, tmp_path: Path) -> None:
    stale = lines_export.place / "lines" / "data"
    stale.mkdir(parents=True)
    (stale / "1-stale.tsv").write_text(f"tests/test_other.py::test_other\t"
                                       f"{os.path.normcase(os.path.realpath(lines_export.file))}\t6\n", encoding="utf-8")
    run = lines_export.run(tmp_path, BY_LINES, "--lines")
    lines = mutant_lines(run)
    assert run.returncode == 1, run.stdout + run.stderr
    assert lines[0].startswith("BASELINE-OK 3 passed")
    assert lines[1].startswith("KILLED   K value -> FAILED tests/test_value.py::test_value")
    assert lines[2] == "NOT_COVERED U unused -> no baseline test runs src/fixture_pkg/__init__.py:6"
    assert lines[3].startswith("KILLED   C child -> FAILED tests/test_child.py::test_child")
    assert lines[4].startswith("SURVIVED S value -> 1 passed in ")
    assert lines[-1] == "done: 4 mutants, 2 survived U unused S value"  # the survivor ran its one narrowed test
    covered = json.loads((lines_export.place / "mutants-covered.json").read_text(encoding="utf-8"))
    assert [(entry["label"], entry["judged_by"], entry["tests"]) for entry in covered] == [
        ("K value", "lines", ["tests/test_value.py::test_value"]),
        ("U unused", "not covered", []),
        ("C child", "lines", ["tests/test_child.py::test_child"]),  # recorded in the child the test started
        ("S value", "lines", ["tests/test_value.py::test_value"]),
    ]
    assert [line.split()[1] for line in elapsed_lines(run.stdout)] == ["baseline", "mutant:K", "mutant:C", "mutant:S"]
    assert b"return 5" in lines_export.file.read_bytes()


@pytest.mark.integration
def test_with_lines_a_file_without_a_record_runs_its_file_level_tests(lines_export: Export, tmp_path: Path) -> None:
    mutant = {"label": "F toml", "edits": [{"path": "pyproject.toml", "old": 'version = "1"', "new": 'version = "2"'}]}
    run = lines_export.run(tmp_path, [mutant], "--lines")
    lines = mutant_lines(run)
    assert lines[1] == ("NOTE F toml: pyproject.toml is not a Python file; "
                        "judged by its file-level tests"), run.stdout + run.stderr
    assert lines[2].startswith("SURVIVED F toml -> 3 passed")


@pytest.mark.integration
def test_a_nested_run_keeps_the_recorders_hook_first_on_its_pythonpath(tree: Path, atc_tmp: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(atc_tmp)
    hook, data = atc_tmp / "outer" / "hook", atc_tmp / "outer" / "data"
    environ = {**os.environ, lines.HOOK_VARIABLE: str(hook), lines.DIR_VARIABLE: str(data)}
    place = atc_tmp / "nested"
    place.mkdir()
    testrun.isolate(tree, place, sys.executable, environ)
    assert environ["PYTHONPATH"].split(os.pathsep) == [str(hook), str(tree / "src")]
    plain = {key: value for key, value in os.environ.items() if not key.startswith("ATC_LINES_")}
    testrun.isolate(tree, place, sys.executable, plain)
    assert plain["PYTHONPATH"] == str(tree / "src")


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


@pytest.mark.integration
def test_a_red_baseline_applies_no_mutant_names_its_failures_and_leaves_nothing_to_reuse(
        export: Export, tmp_path: Path) -> None:
    (export.place / "tree/tests/test_value.py").write_text(
        "import fixture_pkg\ndef test_value():\n    assert fixture_pkg.value() == 9\n", encoding="utf-8")
    (export.place / testrun.BASELINE_RECORD).write_text("{}", encoding="utf-8")
    run = export.run(tmp_path, [KILLED])
    lines = mutant_lines(run)
    assert run.returncode == 1 and "K one" not in run.stdout + run.stderr
    assert lines[:2] == ["NOTE baseline: not reused: inputs", lines[1]] and lines[1].startswith("BASELINE-RED ")
    assert lines[1].endswith("; failed: tests/test_value.py::test_value"), lines
    assert [line.split()[1] for line in elapsed_lines(run.stdout)] == ["baseline"]
    assert elapsed_lines(run.stdout)[0].endswith(" FAIL")
    assert not (export.place / testrun.BASELINE_RECORD).exists()


@pytest.mark.unit
def test_the_failed_tests_of_a_run_are_its_failed_and_errored_node_ids() -> None:
    result = testrun.Result(1, ["x", "FAILED tests/a.py::t - assert 0", "ERROR tests/b.py::u", "FAILED"])
    assert testrun.failed_tests(result) == ["tests/a.py::t", "tests/b.py::u"]


@pytest.mark.integration
def test_a_file_left_mutated_is_refused_before_any_baseline(export: Export, tmp_path: Path) -> None:
    export.file.write_text(export.file.read_text(encoding="utf-8") + "# drifted\n", encoding="utf-8")
    run = export.run(tmp_path, [KILLED])
    assert run.returncode == 9 and run.stderr.startswith(
        f"TREE_NOT_PRISTINE src/fixture_pkg/__init__.py differs from {export.sha[:7]}"), run.stdout + run.stderr
    assert "BASELINE" not in run.stdout and not (export.place / "lines").exists()


@pytest.mark.integration
def test_a_path_an_earlier_run_touched_is_checked_too(export: Export, tmp_path: Path) -> None:
    (export.place / testrun.TOUCHED).write_text("tests/test_value.py\n", encoding="utf-8")
    test_file = export.place / "tree/tests/test_value.py"
    test_file.write_text(test_file.read_text(encoding="utf-8").replace("== 1", "== 2"), encoding="utf-8")
    run = export.run(tmp_path, [KILLED])
    assert run.returncode == 9 and run.stderr.startswith("TREE_NOT_PRISTINE tests/test_value.py "), run.stderr


@pytest.mark.integration
def test_a_run_records_the_paths_it_mutates_before_it_mutates_them(export: Export, tmp_path: Path) -> None:
    run = export.run(tmp_path, [KILLED, SURVIVING])
    assert run.returncode == 1, run.stdout + run.stderr
    assert (export.place / testrun.TOUCHED).read_text(encoding="utf-8") == "src/fixture_pkg/__init__.py\n" * 2


@pytest.mark.integration
def test_a_second_run_on_a_held_scratch_is_refused_at_once(export: Export, tmp_path: Path) -> None:
    with scratch.mutants_lock(export.place):
        run = export.run(tmp_path, [KILLED])
        assert scratch.mutants_running(export.place) is not None
    assert run.returncode == 9, run.stdout + run.stderr
    assert re.match(rf"MUTANTS_RUNNING another atc test --mutants runs on m \(pid {os.getpid()}, since \S+Z\)",
                    run.stderr), run.stderr
    assert "BASELINE" not in run.stdout and not (export.place / "lines").exists()
    assert not (export.place / testrun.BASELINE_RECORD).exists()
    assert scratch.mutants_running(export.place) is None


@pytest.mark.integration
def test_a_run_on_the_same_key_reuses_the_baseline_and_judges_alike(lines_export: Export, tmp_path: Path) -> None:
    first = lines_export.run(tmp_path, BY_LINES, "--lines")
    covered = (lines_export.place / "mutants-covered.json").read_text(encoding="utf-8")
    stored = json.loads((lines_export.place / testrun.BASELINE_RECORD).read_text(encoding="utf-8"))
    assert stored["recorded"] is True and stored["files"] == ["src/fixture_pkg/__init__.py"]
    assert stored["sha"] == lines_export.sha and stored["testkit"] == testrun.package_digest()
    assert stored["inputs"] == f"commit {lines_export.sha}" and stored["python"] == sys.executable
    second = lines_export.run(tmp_path, BY_LINES[:1], "--lines")
    shown = mutant_lines(second)
    assert re.fullmatch(rf"BASELINE-OK reused \S+Z \({stored['seconds']}s, workers {stored['workers']}\) "
                        r"3 passed.*", shown[0]), second.stdout
    assert [line.split()[1] for line in elapsed_lines(second.stdout)] == ["mutant:K"]
    # a reused baseline still takes the grant its tests ask for: the mutants run on it
    assert shown[1] == mutant_lines(first)[1] and re.search(r"^workers: \d+", second.stdout, re.MULTILINE)
    assert json.loads(covered)[0] == json.loads(
        (lines_export.place / "mutants-covered.json").read_text(encoding="utf-8"))[0]


@pytest.mark.integration
def test_a_mutant_with_more_tests_than_one_worker_takes_runs_on_workers_of_the_held_grant(
        lines_export: Export, tmp_path: Path) -> None:
    (lines_export.place / "tree/tests/test_value.py").write_text(
        "import pytest\nimport fixture_pkg\n\n\n@pytest.mark.parametrize('case', range(9))\n"
        "def test_value(case):\n    assert fixture_pkg.value() == 1\n", encoding="utf-8")
    run = lines_export.run(tmp_path, BY_LINES[:2], "--lines")
    assert run.returncode == 1, run.stdout + run.stderr
    granted = int(re.search(r"^workers: (\d+)", run.stdout, re.MULTILINE).group(1))
    assert granted >= 2, run.stdout
    shown = elapsed_lines(run.stdout)
    # nine tests ran the edited line: two workers of the grant the baseline took, with no wait for tokens between
    assert re.fullmatch(r"ELAPSED mutant:K value \d+\.\d+s workers 2 KILLED", shown[1]), shown
    assert mutant_lines(run)[1].startswith("KILLED   K value -> FAILED tests/test_value.py::test_value[")
    assert list((lines_export.place / "lines" / "data").glob(f"{lines.DURATIONS}*"))


@pytest.mark.unit
@pytest.mark.parametrize(("tests", "kind", "granted", "count"), [
    ([f"t.py::test_{n}" for n in range(9)], lines.BY_LINES, 6, 2),
    ([f"t.py::test_{n}" for n in range(8)], lines.BY_LINES, 6, 1),
    ([f"t.py::test_{n}" for n in range(40)], lines.BY_LINES, 3, 3),
    ([f"t.py::test_{n}" for n in range(40)], lines.BY_LINES, 1, 1),
    (["tests/test_a.py", "tests/test_b.py"], lines.FALLBACK, 4, 4),
    ([*(f"t.py::test_{n}" for n in range(40)), "-n", "3"], lines.BY_LINES, 6, 1),
])
def test_a_mutant_takes_one_worker_per_eight_named_tests_within_the_grant(tests: list[str], kind: str, granted: int,
                                                                          count: int) -> None:
    assert testrun.mutant_workers(tests, lines.Choice(kind, tests), granted) == count


@pytest.mark.integration
def test_a_sharded_baseline_records_what_a_single_process_one_does(lines_export: Export, tmp_path: Path) -> None:
    # Under loadgroup pytest-xdist appends "@<group>" to a grouped test's node ID in each worker.
    (lines_export.place / "tree/tests/test_value.py").write_text(
        "import pytest\nimport fixture_pkg\n\n\n@pytest.mark.xdist_group('value')\n"
        "def test_value():\n    assert fixture_pkg.value() == 1\n", encoding="utf-8")
    single = lines_export.run(tmp_path, BY_LINES, "--lines", **{workers.HELD: "outer"})
    assert "workers: 1 (nested" in single.stdout and " workers 1 PASS" in elapsed_lines(single.stdout)[0], single.stdout
    alone = (lines_export.place / "mutants-covered.json").read_text(encoding="utf-8")
    (lines_export.place / testrun.BASELINE_RECORD).unlink()
    sharded = lines_export.run(tmp_path, BY_LINES, "--lines")
    count = int(re.search(r"^workers: (\d+)", sharded.stdout, re.MULTILINE).group(1))
    assert count >= 2, sharded.stdout
    data = lines_export.place / "lines" / "data"  # the controller and each worker loaded the plugin
    assert len(list(data.glob(f"{lines.LOADED}*"))) == count + 1
    assert len(list(data.glob(f"{lines.COLLECTED}*"))) == count
    assert (lines_export.place / "mutants-covered.json").read_text(encoding="utf-8") == alone
    def verdicts(run: subprocess.CompletedProcess[str]) -> list[str]:
        return [line.split(" -> ")[0] for line in mutant_lines(run)[1:]]
    assert verdicts(sharded) == verdicts(single), sharded.stdout + single.stdout


@pytest.mark.unit
def test_a_baseline_is_reused_only_on_its_whole_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key = {"inputs": "inputs a", "tests": ["tests"], "lines": False, "files": ["a.py", "b.py"], "recorder": "r",
           "testkit": "t", "python": "py", "private": True}
    (tmp_path / testrun.BASELINE_RECORD).write_text(json.dumps({**key, "recorded": False}), encoding="utf-8")
    assert testrun.reusable(tmp_path, {**key, "files": ["b.py"]}, None) is not None
    changes = {"inputs": "inputs b", "tests": ["tests", "x"], "lines": True, "files": ["c.py"], "recorder": "r2",
               "testkit": "t2", "python": "py2", "private": False}
    for name, value in changes.items():
        assert testrun.reusable(tmp_path, {**key, name: value}, None) is None
        assert capsys.readouterr().out == f"NOTE baseline: not reused: {name}\n"
    (tmp_path / testrun.BASELINE_RECORD).write_text(json.dumps({**key, "recorded": True}), encoding="utf-8")
    gone = lines.Prepared(tmp_path / "lines", {})
    assert testrun.reusable(tmp_path, key, gone) is None
    assert capsys.readouterr().out == "NOTE baseline: not reused: the line records are gone\n"
    (tmp_path / testrun.BASELINE_RECORD).unlink()
    assert testrun.reusable(tmp_path, key, None) is None and capsys.readouterr().out == ""


# A verifier that classifies every Markdown file of the commit ATC_INPUTS_OF names as outside its input set.
MARKDOWN_VERIFIER = ("import json, os, subprocess\n"
                     "commit = os.environ['ATC_INPUTS_OF']\n"
                     "paths = subprocess.run(['git', 'ls-tree', '-r', '--name-only', commit], capture_output=True,\n"
                     "                       text=True, check=True).stdout.split()\n"
                     "print(json.dumps({'commit': commit, 'excluded': [p for p in paths if p.endswith('.md')],\n"
                     "                  'dependency_files': []}))\n")


def markdown_verifier(directory: Path) -> Path:
    argv_file = directory / "verify-argv.json"
    argv_file.write_text(json.dumps([sys.executable, "-c", MARKDOWN_VERIFIER]), encoding="utf-8")
    return argv_file


def commit_in(worktree: Path, files: dict[str, str]) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def git(*args: str) -> str:
        return subprocess.run(["git", *GIT_IDENTITY, *args], cwd=worktree, env=env, capture_output=True, text=True,
                              check=True).stdout.strip()
    if not (worktree / ".git").exists():
        git("init", "-q", "-b", "main")
    for path, text in files.items():
        (worktree / path).parent.mkdir(parents=True, exist_ok=True)
        (worktree / path).write_text(text, encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "y")
    return git("rev-parse", "HEAD")


@pytest.mark.unit
def test_a_baseline_is_keyed_by_the_inputs_of_its_commit(tmp_path: Path) -> None:
    worktree = tmp_path / "w"
    worktree.mkdir()
    first = commit_in(worktree, {"a.py": "x = 1\n", "notes.md": "one\n"})
    docs = commit_in(worktree, {"notes.md": "two\n"})
    added = commit_in(worktree, {"more.md": "three\n"})
    code = commit_in(worktree, {"a.py": "x = 2\n"})
    argv_file = str(markdown_verifier(tmp_path))

    def tree(commit: str) -> list | None:
        return testrun.commit_tree(str(worktree), commit)

    def classified(argv: str | None = argv_file) -> dict | None:
        return testrun.classified_inputs(str(worktree), first, tree(first), argv, dict(os.environ))
    stored = classified()
    assert stored is not None and stored["excluded"] == ["notes.md"]
    assert testrun._same_inputs(stored, f"commit {docs}", tree(docs))
    # a path the record does not list as excluded counts as an input, so an added Markdown file reruns the baseline
    assert [testrun._same_inputs(stored, f"commit {c}", tree(c)) for c in (first, added, code)] == [True, False, False]
    assert not testrun._same_inputs(stored, f"commit {first}", None)
    assert not testrun._same_inputs({**stored, "excluded": "notes.md"}, f"commit {docs}", tree(docs))
    assert testrun._same_inputs(f"commit {first}", f"commit {first}", None)
    assert not testrun._same_inputs(f"commit {first}", f"commit {docs}", tree(docs))
    assert tree("0" * 40) is None and classified(None) is None
    (tmp_path / "silent.json").write_text(json.dumps([sys.executable, "-c", "pass"]), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert classified(str(tmp_path / "silent.json")) is None and classified(str(tmp_path / "broken.json")) is None
    place = tmp_path / "s"
    assert testrun.scratch_relative(str(place / "tree/.venv/python.exe"), place) == "<scratch>/tree/.venv/python.exe"
    assert testrun.scratch_relative(sys.executable, place) == sys.executable


def lent_baseline(root: Path, name: str, task: str, key: dict, record: dict | None = None) -> Path:
    """A registered scratch of ``task`` holding a passed, recorded baseline of ``key`` whose one line row names
    ``src/m.py`` of its own tree."""
    place = root / name
    (place / "tree").mkdir(parents=True)
    scratch.register(root, place, place / "tree", task=task)
    data = place / lines.DIRECTORY / lines.DATA
    data.mkdir(parents=True)
    (data / f"{lines.STARTED}1").write_text("", encoding="utf-8")
    (data / f"{lines.LOADED}1").write_text("", encoding="utf-8")
    (data / "1.tsv").write_text(f"tests/t.py::test\t{lines.real(place / 'tree/src/m.py')}\t3\n", encoding="utf-8")
    stored = record or {**key, "sha": "a" * 40, "recorded": True, "time": "2026-11-17T00:00:00Z", "summary": "1 passed",
                        "seconds": 1.0, "workers": 1}
    (place / testrun.BASELINE_RECORD).write_text(json.dumps(stored), encoding="utf-8")
    return place


@pytest.mark.unit
def test_another_scratch_of_the_task_lends_a_matching_baseline_with_its_line_paths_moved(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = {"inputs": "inputs d", "tests": ["tests"], "lines": True, "files": ["src/m.py"], "recorder": "r",
           "testkit": "t", "python": "<scratch>/tree/.venv/python", "private": False}
    lender = lent_baseline(tmp_path, "lender", TASK, key)
    lent_baseline(tmp_path, "foreign", "ATC-0001", key, {**key, "sha": "f" * 40, "recorded": False,
                                                         "time": "2026-11-18T00:00:00Z"})
    place = tmp_path / "own"
    (place / "tree").mkdir(parents=True)
    scratch.register(tmp_path, place, place / "tree", task=TASK)

    def adopt(wanted: dict) -> dict | None:
        return testrun.adopt_baseline(tmp_path, place, TASK, wanted, "b" * 40, lines.prepare(place, place / "tree", []),
                                      None)
    for name, value in {"inputs": "inputs e", "tests": ["x"], "testkit": "t2", "python": "py"}.items():
        assert adopt({**key, name: value}) is None and not (place / testrun.BASELINE_RECORD).exists()
    adopted = adopt(key)
    assert adopted is not None and adopted["sha"] == "b" * 40
    assert adopted["reused_from"] == {"scratch": "lender", "sha": "a" * 40}
    assert json.loads((place / testrun.BASELINE_RECORD).read_text(encoding="utf-8")) == adopted
    record = lines.read(place / lines.DIRECTORY / lines.DATA)
    assert record is not None and record.files == {lines.real(place / "tree/src/m.py")}
    (place / testrun.BASELINE_RECORD).unlink()
    # a lender whose line records no longer read back, or whose record changed during the copy, lends nothing
    (lender / lines.DIRECTORY / lines.DATA / f"{lines.STARTED}1").unlink()
    assert adopt(key) is None
    (lender / lines.DIRECTORY / lines.DATA / f"{lines.STARTED}1").write_text("", encoding="utf-8")
    copy = testrun._copy_lines

    def copy_while_rerun(source: Path, own: Path, recording: lines.Prepared) -> bool:
        (source / testrun.BASELINE_RECORD).write_text("{}", encoding="utf-8")
        return copy(source, own, recording)
    monkeypatch.setattr(testrun, "_copy_lines", copy_while_rerun)
    assert adopt(key) is None and not (place / testrun.BASELINE_RECORD).exists()


@pytest.mark.integration
def test_a_commit_that_differs_only_outside_its_inputs_reuses_another_scratchs_baseline(
        lines_export: Export, tmp_path: Path) -> None:
    argv_file = str(markdown_verifier(tmp_path))

    def export_of(name: str, notes: str) -> Export:
        commit = commit_in(lines_export.worktree, {"notes.md": notes})
        made = Export(lines_export.worktree, commit, lines_export.place.parent / name)
        shutil.copytree(lines_export.place / "tree", made.place / "tree")
        (made.place / "tree/notes.md").write_text(notes, encoding="utf-8")
        return made
    added, edited = export_of("m2", "added\n"), export_of("m3", "edited\n")
    lines_export.run(tmp_path, BY_LINES, "--lines", "--verifier-argv", argv_file)
    # the Markdown file the lender's commit lacks is no excluded path of its record, so it counts as an input
    first = added.run(tmp_path, BY_LINES, "--lines", "--verifier-argv", argv_file)
    assert mutant_lines(first)[0].startswith("BASELINE-OK ") and " reused" not in first.stdout, first.stdout
    stored = json.loads((added.place / testrun.BASELINE_RECORD).read_text(encoding="utf-8"))
    assert stored["inputs"]["excluded"] == ["notes.md"] and stored["sha"] == added.sha, stored
    second = edited.run(tmp_path, BY_LINES, "--lines", "--verifier-argv", argv_file)
    shown = mutant_lines(second)
    assert shown[0].startswith(f"BASELINE-OK reused from m2 {stored['time']} "), second.stdout + second.stderr
    # the lines the lender recorded under its own tree judge each mutant here alike
    def judged(run_lines: list[str]) -> list[str]:
        return [re.sub(r" in \d+\.\d+s", "", line) for line in run_lines[1:]]
    assert judged(shown) == judged(mutant_lines(first)), second.stdout
    adopted = json.loads((edited.place / testrun.BASELINE_RECORD).read_text(encoding="utf-8"))
    assert adopted["sha"] == edited.sha and adopted["reused_from"] == {"scratch": "m2", "sha": added.sha}


@pytest.mark.unit
def test_the_testkit_key_is_a_digest_of_the_running_package_sources(tmp_path: Path) -> None:
    """A copy of the package digests alike, a changed byte of one of its files differs, carriage returns do not."""
    package = Path(testrun.__file__).resolve().parent
    copy = tmp_path / "atc_testkit"
    shutil.copytree(package, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert re.fullmatch(r"[0-9a-f]{64}", testrun.package_digest()) and testrun.package_digest() != lines.RECORDER_DIGEST
    assert testrun.sources_digest(copy) == testrun.package_digest()
    source = copy / "lines.py"
    text = source.read_bytes().replace(b"\r", b"")
    source.write_bytes(text.replace(b"\n", b"\r\n"))
    assert testrun.sources_digest(copy) == testrun.package_digest()
    source.write_bytes(text + b"#\n")
    assert testrun.sources_digest(copy) != testrun.package_digest()


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
        code = testrun.mutants([export.place.name, export.sha, str(export.worktree), str(listing), "--task", TASK, "--python",
                                sys.executable, "--tests", "tests"], bytecode_written)
    lines = mutant_lines(capsys.readouterr().out)
    assert code == 1, lines  # the survivor
    assert lines[1].startswith("KILLED") and "K one" in lines[1]
    assert lines[2].startswith("SURVIVED") and "other file" in lines[2]


def test_an_import_guard_refusal_aborts_and_restores(export: Export, tmp_path: Path) -> None:
    edit = {"label": "guard", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "def value():", "new": "raise ImportError('x')\ndef value():"}]}
    run = export.run(tmp_path, [edit, KILLED])
    assert run.returncode == 8 and "COPY_IMPORT_MISMATCH" in run.stderr and "K one" not in run.stdout
    assert "restored-ok src/fixture_pkg/__init__.py" in run.stdout and b"raise" not in export.file.read_bytes()


def test_a_file_that_differs_from_its_object_is_a_restore_mismatch(export: Export, tmp_path: Path) -> None:
    drift = ("import pathlib\nimport fixture_pkg\ndef test_value():\n    assert fixture_pkg.value() == 1\n"
             "def test_drift():\n    source = pathlib.Path(fixture_pkg.__file__)\n"
             "    if '# drifted' not in source.read_text():\n        source.write_text(source.read_text() + '# drifted')\n")
    (export.place / "tree/tests/test_value.py").write_text(drift, encoding="utf-8")
    applies = {"label": "K one", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "return 1",
                                            "new": "return 1  # mutated"}], "tests": ["tests/test_value.py::test_drift"]}
    run = export.run(tmp_path, [applies])
    assert run.returncode == 1 and "RESTORE-MISMATCH src/fixture_pkg/__init__.py" in run.stdout, run.stdout


@pytest.mark.parametrize(("result", "verdict"), [
    (testrun.Result(0, ["1 passed"]), testrun.SURVIVED),
    (testrun.Result(1, ["FAILED tests/a.py::t", "ERROR tests/b.py::t"]), testrun.KILLED),
    (testrun.Result(1, ["ERROR tests/a.py::t - PermissionError"]), testrun.INCONCLUSIVE),
    (testrun.Result(1, ["no summary"]), testrun.INCONCLUSIVE),
    (testrun.Result(2, ["FAILED looks like a failure"]), testrun.INCONCLUSIVE),
    # pytest-xdist ends a run at -x's first failure as an interrupt (exit 2)
    (testrun.Result(2, ["!!! xdist.dsession.Interrupted: stopping after 1 failures !!!", "FAILED tests/a.py::t"]),
     testrun.KILLED),
    (testrun.Result(2, ["!!! xdist.dsession.Interrupted: stopping after 1 failures !!!"]), testrun.INCONCLUSIVE),
    (testrun.Result(2, ["!!! KeyboardInterrupt !!!", "FAILED tests/a.py::t"]), testrun.INCONCLUSIVE),
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
                          # 1 in process; on workers pytest-xdist stops at -x's first error as an interrupt, 2
                          "def test_value():", r"pytest exit [12]: ERROR"),
])
def test_a_run_no_test_failed_in_is_inconclusive_and_ends_the_list(export: Export, tmp_path: Path, old: str, new: str,
                                                                    expected: str) -> None:
    edit = {"label": "odd", "edits": [{"path": "tests/test_value.py", "old": old, "new": new}]}
    run = export.run(tmp_path, [edit, KILLED])
    assert run.returncode == 1, run.stdout + run.stderr
    assert re.search(f"INCONCLUSIVE odd -> {expected}", run.stdout) and "K one" not in run.stdout
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
    here = os.getcwd()
    try:  # the run enters its tree before the baseline, and the scratch's teardown deletes that tree
        code = testrun.mutants([export.place.name, export.sha, str(export.worktree), str(listing), "--task", TASK,
                                "--python", sys.executable, "--tests", "tests"])
    finally:
        os.chdir(here)
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


@pytest.mark.integration
def test_a_list_or_clean_reports_its_lines_and_a_refusal_raises(atc_tmp: Path, capsys: pytest.CaptureFixture[str],
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATC_TMP", "relative")
    with pytest.raises(Refusal) as refused:
        scratch.list_scratches()
    assert (refused.value.token, refused.value.code) == ("ATC_TMP_UNSUITABLE", 9)
    monkeypatch.setenv("ATC_TMP", str(atc_tmp))
    (atc_tmp / "s").mkdir()
    lines: list[str] = []
    assert scratch.clean(["s"], say=lines.append) == 0
    assert lines == [f"DELETED {atc_tmp / 's'}"] and capsys.readouterr().err == ""
    lines.clear()
    assert scratch.clean(["systmp"], say=lines.append) == 1
    assert lines and lines[0].startswith("REFUSED ")


def test_mutants_need_the_export(export: Export, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    listing = tmp_path / "m.json"
    listing.write_text(json.dumps([KILLED]), encoding="utf-8")
    other = export.place.parent / "other"
    code = mutants([other.name, export.sha, str(export.worktree), str(listing),
                    "--task", TASK, "--python", sys.executable, "--tests", "tests"])
    assert code == 9 and capsys.readouterr().err.startswith("TREE_NOT_FOUND ")


@pytest.mark.integration
@pytest.mark.parametrize("sha", ["0" * 40, "not-a-revision"])
def test_mutants_refuse_a_sha_that_names_no_commit_before_the_baseline(
        export: Export, tmp_path: Path, capsys: pytest.CaptureFixture[str], sha: str) -> None:
    listing = tmp_path / "m.json"
    listing.write_text(json.dumps([KILLED]), encoding="utf-8")
    other = export.place.parent / "other"
    code = mutants([other.name, sha, str(export.worktree), str(listing),
                    "--task", TASK, "--python", sys.executable, "--tests", "tests"])
    captured = capsys.readouterr()
    assert code == 9 and captured.err.startswith(f"SHA_NOT_FOUND {sha} names no commit in ")
    assert captured.out == "" and not other.exists()


@pytest.mark.integration
def test_mutants_refuse_an_invalid_private_desktop_value_before_the_baseline(
        export: Export, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    listing = tmp_path / "m.json"
    listing.write_text(json.dumps([KILLED]), encoding="utf-8")
    monkeypatch.setenv("ATC_PRIVATE_DESKTOP", "yes")
    code = mutants([export.place.name, export.sha, str(export.worktree), str(listing),
                    "--task", TASK, "--python", sys.executable, "--tests", "tests"])
    captured = capsys.readouterr()
    assert code == 9 and captured.err.startswith("PRIVATE_DESKTOP_INVALID ") and "BASELINE" not in captured.out


@pytest.mark.integration
def test_mutants_register_the_scratch_to_its_task_and_refuse_another(export: Export, tmp_path: Path) -> None:
    assert export.run(tmp_path, [KILLED]).returncode == 0
    assert [row.task for row in scratch.registered(export.place.parent)] == [TASK]
    listing = tmp_path / "m.json"
    listing.write_text(json.dumps([KILLED]), encoding="utf-8")
    run = run_command("mutants", export.place.name, export.sha, str(export.worktree), str(listing),
                      "--task", "ATC-0001","--python", sys.executable, "--tests", "tests")
    assert run.returncode == 9 and run.stderr.startswith("SCRATCH_TASK_MISMATCH ") and "BASELINE" not in run.stdout


@pytest.mark.integration
def test_mutants_without_a_task_are_a_usage_error(export: Export, tmp_path: Path) -> None:
    listing = tmp_path / "m.json"
    listing.write_text(json.dumps([KILLED]), encoding="utf-8")
    run = run_command("mutants", export.place.name, export.sha, str(export.worktree), str(listing),
                      "--python",sys.executable, "--tests", "tests")
    assert run.returncode == 2 and "--task" in run.stderr and scratch.registered(export.place.parent) == []


@pytest.mark.integration
def test_mutants_default_to_the_copys_own_environment(export: Export, tmp_path: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    listing = tmp_path / "m.json"
    listing.write_text(json.dumps([KILLED]), encoding="utf-8")
    code = mutants([export.place.name, export.sha, str(export.worktree), str(listing),
                    "--task", TASK, "--tests", "tests"])
    err = capsys.readouterr().err
    assert code == 9 and err.startswith("VENV_MISSING ") and str(export.place / "tree") in err


VENV_PYTHON = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"


@pytest.mark.unit
def test_the_copys_interpreter_is_in_the_registered_lock_dir_else_the_tree(atc_tmp: Path, tmp_path: Path) -> None:
    place = atc_tmp / "c"
    for directory in (place / "tree", place / "tree/adapters/python", tmp_path / "outside"):
        (directory / VENV_PYTHON).parent.mkdir(parents=True)
        (directory / VENV_PYTHON).write_bytes(b"")
    assert testrun.copy_interpreter(atc_tmp, place) == str(place / "tree" / VENV_PYTHON)
    scratch.register(atc_tmp, place, place / "tree/adapters/python", task=TASK)
    assert testrun.copy_interpreter(atc_tmp, place) == str(place / "tree/adapters/python" / VENV_PYTHON)
    other = atc_tmp / "d"
    (other / "tree").mkdir(parents=True)
    scratch.register(atc_tmp, other, tmp_path / "outside", task=TASK)
    with pytest.raises(Refusal) as refused:
        testrun.copy_interpreter(atc_tmp, other)
    assert refused.value.token == "VENV_MISSING"


PROBE = "atc0044_probe"


def guard(tree: Path, *pythonpath: Path) -> testrun.Result:
    environ = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    environ["PYTHONPATH"] = os.pathsep.join(map(str, pythonpath))
    return testrun._capture([sys.executable, "-c", testrun.IMPORT_GUARD, str(tree), str(testrun.PROJECT_DEPTH)],
                            tree, environ, False, None)


def probe_project(at: Path) -> Path:
    """A project at ``at`` whose ``src`` holds the probe package; its ``src``."""
    (at / "src" / PROBE).mkdir(parents=True)
    (at / "pyproject.toml").write_text('[project]\nname = "probe"\n', encoding="utf-8")
    (at / "src" / PROBE / "__init__.py").write_text("raise RuntimeError('a located package never runs')\n",
                                                    encoding="utf-8")
    return at / "src"


@pytest.mark.unit
def test_the_guard_refuses_a_tree_project_package_the_environment_takes_from_elsewhere(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    inside = probe_project(tree / "adapters/python")
    elsewhere = probe_project(tmp_path / "worktree/adapters/python")
    refused = guard(tree, elsewhere)
    assert refused.code == 1 and any(line.startswith(f"{PROBE}: ") and str(elsewhere) in line
                                     for line in refused.lines), refused.lines
    assert guard(tree, inside).code == 0
    assert guard(tree).code == 0


@pytest.mark.unit
def test_the_guard_looks_for_projects_three_levels_below_the_tree(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    elsewhere = probe_project(tmp_path / "elsewhere")
    probe_project(tree / "a/b/c")
    assert guard(tree, elsewhere).code == 1
    deeper = tmp_path / "deeper"
    probe_project(deeper / "a/b/c/d")
    assert guard(deeper, elsewhere).code == 0


@pytest.mark.integration
def test_a_refused_guard_names_the_copys_own_environment(tree: Path, atc_tmp: Path) -> None:
    (tree / "src/fixture_pkg/__init__.py").write_text("raise ImportError('broken')\n", encoding="utf-8")
    run = run_tests(str(tree), "s", sys.executable, "tests")
    assert run.returncode == 8 and testrun.GUARD_NEXT in run.stderr


def unregistered(root: Path) -> list[str]:
    """The scratch directories under ``root`` that no registry line names: what ATC-0044 decision (a) forbids."""
    rows = [os.path.normcase(row.scratch) for row in scratch.registered(root)]
    return sorted(child.name for child in root.iterdir()
                  if child.is_dir() and child.name.lower() not in scratch.RESERVED_NAMES
                  and os.path.normcase(child) not in rows)


@pytest.mark.integration
def test_a_mutants_refusal_before_the_registry_line_creates_no_directory(export: Export, tmp_path: Path,
                                                                         capsys: pytest.CaptureFixture[str],
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    listing = tmp_path / "m.json"
    listing.write_text(json.dumps([KILLED]), encoding="utf-8")
    root = export.place.parent
    common = [str(export.worktree), str(listing), "--task", TASK, "--python", sys.executable, "--tests", "tests"]
    assert mutants(["fresh", export.sha, *common]) == 9
    assert capsys.readouterr().err.startswith("TREE_NOT_FOUND ") and not (root / "fresh").exists()
    monkeypatch.setenv("ATC_PRIVATE_DESKTOP", "yes")
    assert mutants([export.place.name, export.sha, *common]) == 9
    assert capsys.readouterr().err.startswith("PRIVATE_DESKTOP_INVALID ") and scratch.registered(root) == []
    assert unregistered(root) == [export.place.name] and not (export.place / "qdr").exists()


@pytest.mark.integration
def test_mutants_refuse_a_bad_file_before_the_baseline(export: Export, tmp_path: Path,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    listing = tmp_path / "m.json"
    listing.write_text("[]", encoding="utf-8")
    code = mutants([export.place.name, export.sha, str(export.worktree), str(listing),
                    "--task", TASK, "--python", sys.executable, "--tests", "tests"])
    captured = capsys.readouterr()
    assert code == 9 and captured.err.startswith("MUTANTS_INVALID ") and "BASELINE" not in captured.out

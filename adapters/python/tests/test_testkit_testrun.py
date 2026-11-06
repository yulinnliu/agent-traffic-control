"""docs/TESTKIT.md § Commands: ``atc test run`` and ``atc test mutants``, and the ``atc`` dispatch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import atc_cli
from atc_testkit import commands, scratch, testrun
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
    environment.update(env)
    return subprocess.run([sys.executable, "-m", "atc_testkit.commands", *args], cwd=cwd, env=environment,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src/fixture_pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src/fixture_pkg/__init__.py").write_text(MODULE, encoding="utf-8")
    (root / "tests/test_value.py").write_text(REPORTING_TEST, encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "fixture-name"\nversion = "1"\n', encoding="utf-8")
    return root


def test_a_run_prints_the_note_the_label_and_the_tail(tree: Path, atc_tmp: Path) -> None:
    run = run_command("test", "run", str(tree), "s1", sys.executable, "the label", "-s", "tests", HAMI_X="set")
    assert run.returncode == 0, run.stdout + run.stderr
    lines = run.stdout.splitlines()
    assert lines[0].startswith("NOTE no .venv") and lines[1] == "== the label =="
    assert len(lines) <= 2 + testrun.TAIL_LINES
    scratch_dir = atc_tmp / "s1"
    values = dict(line.split(" ", 1) for line in lines if line.split(" ")[0] in ("TEMPDIR", "BASE", "DATA", "HAMI", "FILE"))
    assert values["TEMPDIR"] == str(scratch_dir)
    assert Path(values["BASE"]).is_relative_to(scratch_dir / "bt")
    assert values["DATA"] == f"{scratch_dir / 'qdr'} None"
    assert values["HAMI"] == "None"
    assert Path(values["FILE"]).is_relative_to(tree.resolve())


def test_the_exit_code_is_pytests(tree: Path, atc_tmp: Path) -> None:
    (tree / "tests/test_bad.py").write_text("def test_bad(): assert False\n", encoding="utf-8")
    assert run_command("test", "run", str(tree), "s", sys.executable, "l", "tests").returncode == 1


def test_only_the_last_fifteen_lines_are_printed(tree: Path, atc_tmp: Path) -> None:
    body = "def test_lines():\n    for i in range(40): print('LINE', i)\n"
    (tree / "tests/test_lines.py").write_text(body, encoding="utf-8")
    run = run_command("test", "run", str(tree), "s", sys.executable, "l", "-s", "tests/test_lines.py")
    assert "LINE 0\n" not in run.stdout and run.stdout.count("\n") <= 2 + 15


def test_the_tree_names_its_data_roots(tree: Path, atc_tmp: Path) -> None:
    (tree / "pyproject.toml").write_text('[tool.atc-testkit]\ndata_root_variables = ["OTHER_ROOT"]\n', encoding="utf-8")
    run = run_command("test", "run", str(tree), "s", sys.executable, "l", "-s", "tests", QUANT_DATA_ROOT="ambient")
    assert f"DATA ambient {atc_tmp / 's/qdr'}" in run.stdout


def test_xdist_is_turned_off_when_installed_and_the_cache_provider_always(tree: Path, atc_tmp: Path) -> None:
    (tree / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-n 2"\n', encoding="utf-8")
    run = run_command("test", "run", str(tree), "s", sys.executable, "l", "-s", "tests")
    assert run.returncode == 0 and "created:" not in run.stdout and "BASE" in run.stdout
    assert not (tree / ".pytest_cache").exists()


def test_a_relative_tree_and_python_resolve_against_the_caller(tree: Path, atc_tmp: Path) -> None:
    run = run_command("test", "run", "tree", "s", os.path.relpath(sys.executable, tree.parent), "l", "tests", cwd=tree.parent)
    assert run.returncode == 0, run.stdout + run.stderr


def test_a_bare_python_name_is_found_on_the_path(tree: Path, atc_tmp: Path) -> None:
    run = run_command("test", "run", str(tree), "s", "python", "l", "tests",
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
    run = run_command("test", "run", *(argument.format(**values) for argument in arguments), "l", "tests")
    assert run.returncode == 9, run.stdout + run.stderr
    assert run.stdout.startswith(token + " ")
    assert not (tmp_path / "out").exists() and not (atc_tmp / values["long"]).exists()


def test_the_scratch_bound_is_sixty_four_characters(tree: Path, atc_tmp: Path) -> None:
    name = "a" * (64 - len(str(atc_tmp)) - 1)
    assert run_command("test", "run", str(tree), name, sys.executable, "l", "tests").returncode == 0


def test_a_scratch_that_cannot_be_resolved_is_refused(atc_tmp: Path) -> None:
    with pytest.raises(Refusal) as refused:
        scratch.resolve_scratch("a\0b", atc_tmp)
    assert refused.value.token == "SCRATCH_NOT_RESOLVED" and refused.value.code == 9


def test_a_path_to_a_direct_child_is_accepted(atc_tmp: Path) -> None:
    assert scratch.resolve_scratch(str(atc_tmp / "s"), atc_tmp) == atc_tmp / "s"


def test_an_unsuitable_root_exits_nine(tree: Path, atc_tmp: Path) -> None:
    run = run_command("test", "run", str(tree), "s", sys.executable, "l", "tests", ATC_TMP="relative")
    assert run.returncode == 9 and run.stdout.startswith("ATC_TMP_UNSUITABLE ")


def test_a_pythonpath_directory_absent_after_entering_is_refused(tree: Path, atc_tmp: Path, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(testrun, "pythonpath_directories", lambda _tree: [str(tmp_path / "gone")])
    with pytest.raises(Refusal) as refused:
        testrun.execute(str(tree), "s", sys.executable, "l", ["tests"], dict(os.environ))
    assert refused.value.token == "PYTHONPATH_MISSING" and refused.value.code == 8


@pytest.mark.parametrize("body", ["raise ImportError('broken')\n", "import sys\nraise SystemExit(0) if False else None\nraise RuntimeError('x')\n"])
def test_a_package_that_cannot_load_is_refused(tree: Path, atc_tmp: Path, body: str) -> None:
    (tree / "src/fixture_pkg/__init__.py").write_text(body, encoding="utf-8")
    run = run_command("test", "run", str(tree), "s", sys.executable, "l", "tests")
    assert run.returncode == 8 and run.stdout.startswith("COPY_IMPORT_MISMATCH ")


def test_a_package_loaded_from_outside_the_tree_is_refused(tree: Path, atc_tmp: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside).mkdir()
    (outside / "fixture_pkg.py").write_text("X = 1\n", encoding="utf-8")
    body = f"import sys, importlib\nsys.modules.pop(__name__)\nsys.path.remove(sys.path[0]) if False else None\nsys.path.insert(0, {str(outside)!r})\nimport importlib.util\nspec = importlib.util.spec_from_file_location(__name__, {str(outside / 'fixture_pkg.py')!r})\nmodule = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\nsys.modules[__name__] = module\n"
    (tree / "src/fixture_pkg/__init__.py").write_text(body, encoding="utf-8")
    run = run_command("test", "run", str(tree), "s", sys.executable, "l", "tests")
    assert run.returncode == 8 and "fixture_pkg" in run.stdout


def test_the_guard_descends_through_namespace_directories(tree: Path, atc_tmp: Path) -> None:
    package = tree / "packages/one/src/name_space/inner"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise ImportError('deep')\n", encoding="utf-8")
    run = run_command("test", "run", str(tree), "s", sys.executable, "l", "tests")
    assert run.returncode == 8 and "name_space.inner" in run.stdout


def test_the_tree_has_no_venv_note_when_it_holds_one(tree: Path, atc_tmp: Path) -> None:
    (tree / ".venv").mkdir()
    assert "NOTE" not in run_command("test", "run", str(tree), "s", sys.executable, "l", "tests").stdout


def test_the_registry_gets_one_line_per_scratch(tree: Path, atc_tmp: Path) -> None:
    for _ in range(2):
        assert run_command("test", "run", str(tree), "s", sys.executable, "l", "tests").returncode == 0
    (atc_tmp / "s2").mkdir()
    assert run_command("test", "run", str(tree), "s2", sys.executable, "l", "tests").returncode == 0
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
    run = run_command("test", "run", str(tree), "s", sys.executable, "l", "tests/test_desktop.py", ATC_PRIVATE_DESKTOP="1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 passed, 1 deselected" in run.stdout


def test_the_exclusion_joins_a_given_marker_expression() -> None:
    assert testrun._with_exclusion(["tests"]) == ["-m", "not interactive_desktop", "tests"]
    assert testrun._with_exclusion(["-m", "slow", "tests"]) == ["-m", "(slow) and not interactive_desktop", "tests"]
    assert testrun._with_exclusion(["-munit", "-x"]) == ["-m", "(unit) and not interactive_desktop", "-x"]


def test_a_run_needs_its_four_arguments() -> None:
    assert run_command("test", "run", "a", "b", "c").returncode == 2


# ``atc test mutants``


GIT_IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false")


class Export:
    def __init__(self, worktree: Path, sha: str, place: Path):
        self.worktree, self.sha, self.place = worktree, sha, place
        self.file = place / "tree/src/fixture_pkg/__init__.py"

    def run(self, tmp_path: Path, mutants: list[dict], **env: str) -> subprocess.CompletedProcess[str]:
        listing = tmp_path / "mutants.json"
        listing.write_text(json.dumps(mutants), encoding="utf-8")
        return run_command("test", "mutants", self.place.name, self.sha, str(self.worktree), str(listing),
                           "--python", sys.executable, "--tests", "tests", **env)


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
    lines = run.stdout.splitlines()
    assert run.returncode == 0, run.stdout + run.stderr
    assert lines[0].startswith("BASELINE-OK")
    assert lines[1].startswith("KILLED") and "K one" in lines[1] and "FAILED" in lines[1]
    assert lines[2].startswith("SURVIVED") and "S two" in lines[2]
    assert lines[3] == "restored-ok src/fixture_pkg/__init__.py"
    assert lines[-1] == "done: 2 mutants, 1 survived S two"
    assert b"return 1" in export.file.read_bytes()


def test_a_red_baseline_applies_no_mutant(export: Export, tmp_path: Path) -> None:
    export.file.write_text("def value():\n    return 9\n", encoding="utf-8")
    run = export.run(tmp_path, [KILLED])
    assert run.returncode == 1 and run.stdout.startswith("BASELINE-RED") and "K one" not in run.stdout


@pytest.mark.parametrize("old", ["absent text", "\n"])
def test_an_old_that_does_not_occur_exactly_once_aborts(export: Export, tmp_path: Path, old: str) -> None:
    edit = {"label": "bad", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": old, "new": "x"}]}
    run = export.run(tmp_path, [edit, KILLED])
    assert run.returncode == 1 and "need exactly 1" in run.stdout and "K one" not in run.stdout


def test_edits_follow_the_files_line_endings(export: Export, tmp_path: Path) -> None:
    export.file.write_bytes(export.file.read_bytes().replace(b"\n", b"\r\n"))
    edit = {"label": "crlf", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "def value():\n    return 1",
                                        "new": "def value():\n    return 3"}]}
    run = export.run(tmp_path, [edit])
    assert run.returncode == 0 and "KILLED" in run.stdout and "restored-ok" in run.stdout
    assert b"\r\n" in export.file.read_bytes()


def test_an_import_guard_refusal_aborts_and_restores(export: Export, tmp_path: Path) -> None:
    edit = {"label": "guard", "edits": [{"path": "src/fixture_pkg/__init__.py", "old": "def value():", "new": "raise ImportError('x')\ndef value():"}]}
    run = export.run(tmp_path, [edit, KILLED])
    assert run.returncode == 8 and "COPY_IMPORT_MISMATCH" in run.stdout and "K one" not in run.stdout
    assert "restored-ok src/fixture_pkg/__init__.py" in run.stdout and b"raise" not in export.file.read_bytes()


def test_a_file_that_differs_from_its_object_is_a_restore_mismatch(export: Export, tmp_path: Path) -> None:
    export.file.write_text(export.file.read_text(encoding="utf-8") + "# drifted\n", encoding="utf-8")
    run = export.run(tmp_path, [KILLED])
    assert run.returncode == 1 and "RESTORE-MISMATCH src/fixture_pkg/__init__.py" in run.stdout


def test_a_timed_out_mutant_counts_as_killed_and_its_tree_ends(export: Export, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(testrun, "MUTANT_SECONDS", 0.001)
    assert testrun._summary(testrun.Result(None, []))[0] is False
    result = testrun._capture([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, os.environ, False, 1.0)
    assert result.code is None


def test_mutants_need_the_export(tree: Path, atc_tmp: Path, tmp_path: Path) -> None:
    listing = tmp_path / "m.json"
    listing.write_text("[]", encoding="utf-8")
    run = run_command("test", "mutants", "m", "0" * 40, str(tree), str(listing), "--python", sys.executable, "--tests", "tests")
    assert run.returncode == 9 and run.stdout.startswith("TREE_NOT_FOUND ")


# dispatch


@pytest.mark.parametrize("arguments", [("test",), ("test", "other"), ("tmp",), ("tmp", "list", "x"), ("tmp", "clean"),
                                       ("tmp", "clean", "--project"), ("other",)])
def test_a_wrong_command_exits_two(arguments: tuple[str, ...], atc_tmp: Path) -> None:
    assert commands.main(list(arguments)) == 2


@pytest.mark.parametrize(("arguments", "attribute", "module", "expected"), [
    (["test", "run", "a", "b"], "run", testrun, (["a", "b"],)),
    (["test", "mutants", "a"], "mutants", testrun, (["a"],)),
    (["tmp", "list"], "list_scratches", scratch, ()),
    (["tmp", "prune"], "prune", scratch, ()),
    (["tmp", "clean", "a", "b"], "clean", scratch, (["a", "b"],)),
    (["tmp", "clean", "--project", "p"], "clean", scratch, ((), "p")),
])
def test_atc_reaches_the_package_with_its_arguments_and_returns_its_exit_code(
        monkeypatch: pytest.MonkeyPatch, arguments: list[str], attribute: str, module, expected: tuple) -> None:
    seen = []
    monkeypatch.setattr(module, attribute, lambda *args: seen.append(args) or 41)
    assert atc_cli.main(arguments) == 41
    assert seen == [expected]

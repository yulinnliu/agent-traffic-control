"""`atc test ID` (src/atc_cli/scripts/task_tests.py, docs/TESTKIT.md § Commands) on tmp repositories: the related tests of a
task's worktree, an export of a commit, mutants, the candidate, and the scratch listing and cleaning.

The uv sync, the pytest runs, the mutant runs and the candidate verifier are stand-ins that record what the front
hands them; the workspace, the task records, the worktrees and the scratch registry are real.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import git_history
import git_integrate
import task_tests
from atc_testkit import checkout, elapsed, locations, scratch, testrun, workers

pytestmark = pytest.mark.integration

TASK = "ATC-0001"
BRANCH = "claude+ATC-0001"
HARNESS = {"CLAUDECODE": "1"}  # the harness the task records name: claude
TESTKIT = '[project]\nname = "demo"\nversion = "1"\n\n[tool.atc-testkit]\ntimeout = 60\n'
LOCK = 'version = 1\nsource = "example/governance"\nrevision = "' + "a" * 40 + '"\n'


def git_env() -> dict[str, str]:
    """The environment of the test's git calls: a fixed identity and no user or system configuration."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=git_env(),
                          check=True).stdout.strip()


def put(repo: Path, files: dict[str, str], message: str = "edit") -> str:
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        git(repo, "add", "--", relative)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def record(base: str) -> str:
    return yaml.safe_dump({
        "task_id": TASK, "repository": "demo", "base_sha": base, "scope": "the widget", "branch": BRANCH,
        "governance_source": "example/governance", "governance_revision": "a" * 40,
        "authority": {"edit": True, "commit": True, "push_topic": False, "integrate_main": False,
                      "push_main": False, "deploy": False},
    }, sort_keys=False, allow_unicode=True)


class Workspace:
    def __init__(self, root: Path, project: Path, worktree: Path, base: str) -> None:
        self.root, self.project, self.worktree, self.base = root, project, worktree, base

    def run(self, *argv: str) -> int:
        return task_tests.main(list(argv), root=self.root, environ=dict(HARNESS, ATC_TMP=os.environ["ATC_TMP"]))


def built_workspace(root: Path) -> str:
    """The workspace root repository, and project `demo`'s repository with its base commit on main; the base SHA.
    Every file is LF text, as `put` writes it."""
    env = git_env()
    git_history.new_repository(root, [git_history.Commit(
        {"AGENTS.md": b"# AGENTS.md\n", ".gitignore": b"workspace.yaml\nprojects/\nworktrees/\nstate/\n"}, "edit")],
        env=env)
    [base] = git_history.new_repository(root / "projects" / "demo", [git_history.Commit({
        "AGENTS.md": "# AGENTS.md\n\n## Task records\n\n- Task prefix: ATC\n\nProse after the line.\n".encode(),
        ".atc/atc.lock": LOCK.encode(), "pyproject.toml": TESTKIT.encode(), "src/demo.py": b"X = 1\n",
        "tests/test_value.py": b"def test_value():\n    assert True\n",
    }, "edit")], env=env)
    return base


@pytest.fixture()
def workspace(tmp_path: Path, atc_tmp: Path, tmp_path_factory: pytest.TempPathFactory) -> Workspace:
    """A workspace whose project `demo` declares the prefix ATC, with the task's worktree on its branch."""
    root = tmp_path / "workspace"
    base = git_history.copied(tmp_path_factory, "task-tests", root, built_workspace)
    (root / "workspace.yaml").write_text(
        f"version: 1\nworkspace_root: '{root}'\ngovernance_source: example/governance\nprojects:\n"
        "  demo:\n    repo: projects/demo\n    worktrees: worktrees/demo\n    state: state/demo-data\n",
        encoding="utf-8")
    project = root / "projects" / "demo"
    worktree = root / "worktrees" / "demo" / BRANCH
    git(project, "worktree", "add", "-q", "-b", BRANCH, str(worktree), "main")
    put(worktree, {".atc/tasks/ATC-0001.yaml": record(base), "src/demo.py": "X = 2\n"}, "open ATC-0001")
    return Workspace(root, project, worktree, base)


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """The stand-ins: each call's arguments in order; the mutant run also reads the mutant list it is given."""
    seen: dict[str, list] = {"run": [], "mutants": [], "candidate": [], "sync": [], "sync_env": []}

    def run(argv, environ=None) -> int:
        seen["run"].append(list(argv))
        return 0

    def mutants(argv, environ=None) -> int:
        seen["mutants"].append((list(argv), json.loads(Path(argv[3]).read_text(encoding="utf-8"))))
        return 0

    def candidate(task, sha, root=None, cwd=None, as_json=False) -> int:
        seen["candidate"].append((task, sha))
        return 0

    def sync(directory, environ, run=None) -> Path:
        seen["sync"].append(Path(directory))
        seen["sync_env"].append(dict(environ))
        venv = Path(directory) / ".venv"
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")  # the interpreter's place; no selector is declared, so nothing runs it
        return venv
    monkeypatch.setattr(testrun, "run", run)
    monkeypatch.setattr(testrun, "mutants", mutants)
    monkeypatch.setattr(git_integrate, "run_candidate", candidate)
    monkeypatch.setattr(checkout, "sync", sync)
    return seen


def option(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_a_run_tests_the_related_tests_of_the_worktree_as_the_task_scratch(workspace: Workspace,
                                                                          calls: dict[str, list]) -> None:
    assert workspace.run(TASK) == 0
    [argv] = calls["run"]
    assert (option(argv, "--scratch"), option(argv, "--task"), option(argv, "--label")) == (TASK, TASK, "related")
    assert Path(option(argv, "--tree")).resolve() == workspace.worktree.resolve()
    assert option(argv, "--related") == workspace.base
    assert option(argv, "--timeout") == "60" and "--" not in argv


def test_all_runs_the_full_suite(workspace: Workspace, calls: dict[str, list]) -> None:
    assert workspace.run(TASK, "--all") == 0
    [argv] = calls["run"]
    assert option(argv, "--label") == "all" and "--related" not in argv and "--" not in argv


@pytest.mark.parametrize("given", [[], ["--all"]], ids=["related", "all"])
def test_pytest_arguments_after_dashes_are_an_explicit_run_with_no_related_selection(
        workspace: Workspace, calls: dict[str, list], given: list[str]) -> None:
    """docs/TESTKIT.md § Commands, step 2: the arguments after -- are exactly what pytest gets, --all or not; the
    lint stage still runs over the whole tree."""
    assert workspace.run(TASK, *given, "--", "tests/test_value.py", "-q") == 0
    [argv] = calls["run"]
    assert option(argv, "--label") == "explicit" and "--related" not in argv
    assert "--lint" in argv
    assert argv[argv.index("--"):] == ["--", "tests/test_value.py", "-q"]


def test_an_explicit_run_runs_its_files_where_the_related_selection_selects_none(
        workspace: Workspace, calls: dict[str, list]) -> None:
    """The task changes no test file, so its related selection is empty: the related form stops with NO_TESTS and
    exit 0 without running anything, while the argv `atc test ID -- FILE` hands the runner runs FILE and records an
    explicit selection. Only the runner's interpreter is given; the run is the real one, in a child process."""
    assert workspace.run(TASK, "--", "-n", "0", "tests/test_value.py") == 0
    [argv] = calls["run"]
    at = argv.index("--")
    explicit = [*argv[:at], "--python", sys.executable, *argv[at:]]
    related = [*argv[:at], "--python", sys.executable, "--related", workspace.base]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    environment["PYTHONIOENCODING"] = "utf-8"
    entry = "import sys\nfrom atc_testkit import testrun\nsys.exit(testrun.run(sys.argv[1:]))\n"
    pool = Path(os.environ[workers.POOL_DIRECTORY])

    def run(runner_argv: list[str]) -> tuple[int, list[str], str]:
        done = subprocess.run([sys.executable, "-c", entry, *runner_argv], env=environment, capture_output=True,
                              text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
        return done.returncode, done.stdout.splitlines(), done.stdout + done.stderr
    before = len(elapsed.records(pool))
    code, lines, shown = run(related)
    assert code == 0 and lines[-1] == "NO_TESTS no test selected", shown
    code, lines, shown = run(explicit)
    assert code == 0 and lines[-1].startswith("PASS 1 passed"), shown
    added = elapsed.records(pool)[before:]
    assert [(r["task"], r["stage"], r["selection"], r["selection_reason"]) for r in added] == [
        (TASK, "explicit", "explicit", "pytest arguments")]


def test_a_sha_is_exported_into_the_task_and_commit_scratch_and_reused(workspace: Workspace, calls: dict[str, list],
                                                                       atc_tmp: Path) -> None:
    name = f"{TASK}-{workspace.base[:7]}"
    assert workspace.run(TASK, "--sha", workspace.base) == 0
    assert (atc_tmp / name / "tree" / "pyproject.toml").is_file()
    [row] = scratch.registered(atc_tmp)
    assert (row.task, row.commit, row.project) == (TASK, workspace.base, "demo")
    assert len(calls["sync"]) == 1
    [env] = calls["sync_env"]
    shared = locations.cache_directories(dict(HARNESS, ATC_TMP=str(atc_tmp)))
    assert {name: env.get(name) for name in shared} == shared, "the export builds with the verifier's caches"
    assert option(calls["run"][-1], "--scratch") == name

    assert workspace.run(TASK, "--sha", workspace.base) == 0
    assert len(calls["sync"]) == 1, "a second run reuses the scratch's environment"
    assert option(calls["run"][-1], "--scratch") == name
    assert len(scratch.registered(atc_tmp)) == 1


def write_mutants(tmp_path: Path, entries: list[dict]) -> Path:
    listing = tmp_path / "mutants.json"
    listing.write_text(json.dumps(entries), encoding="utf-8")
    return listing


DEMO_EDIT = [{"path": "src/demo.py", "old": "X = 2", "new": "X = 3"}]
TEST_EDIT = [{"path": "tests/test_value.py", "old": "2 == 2", "new": "2 == 3"}]


def test_mutants_judge_the_branch_tip_by_default_each_by_its_selected_and_own_tests(
        workspace: Workspace, calls: dict[str, list], tmp_path: Path) -> None:
    """Without tests after --, each mutant is judged by what the selector picks for its edited paths (the stand-in
    environment runs no selector, so a changed test file picks itself) and its own tests; a test file the branch
    changed that no mutant reaches stays out, so the baseline is never the task's related tests."""
    put(workspace.worktree, {"tests/test_value.py": "def test_value():\n    assert 2 == 2\n",
                             "tests/test_other.py": "def test_other():\n    assert True\n"}, "test the widget")
    listing = write_mutants(tmp_path, [
        {"label": "a", "edits": DEMO_EDIT, "tests": ["tests/test_own.py"], "kill_rule": "assertion"},
        {"label": "b", "edits": TEST_EDIT}])
    assert workspace.run(TASK, "--mutants", str(listing)) == 0
    [(argv, entries)] = calls["mutants"]
    tip = git(workspace.worktree, "rev-parse", "HEAD")
    assert argv[0] == f"{TASK}-{tip[:7]}" and argv[1] == tip
    assert Path(argv[3]).name == task_tests.SELECTED_MUTANTS and Path(argv[3]).parent.name == argv[0]
    assert [(entry["label"], entry["tests"], entry.get("kill_rule")) for entry in entries] == [
        ("a", ["tests/test_own.py"], "assertion"), ("b", ["tests/test_value.py"], None)]
    assert argv[argv.index("--tests") + 1:] == ["tests/test_own.py", "tests/test_value.py"]
    assert "--lines" in argv[:argv.index("--tests")]  # the baseline's line record narrows each selection
    assert json.loads(listing.read_text(encoding="utf-8"))[0]["tests"] == ["tests/test_own.py"]


def test_a_mutant_its_selection_and_own_tests_leave_without_tests_stops_before_the_run(
        workspace: Workspace, calls: dict[str, list], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    listing = write_mutants(tmp_path, [{"label": "b", "edits": TEST_EDIT}, {"label": "a", "edits": DEMO_EDIT}])
    assert workspace.run(TASK, "--mutants", str(listing)) == 1
    err = capsys.readouterr().err
    assert err.startswith("STOP NO_TESTS: no test judges the mutant a: the selection picks none for src/demo.py")
    assert calls["mutants"] == []


def test_without_a_selector_a_mutant_is_judged_by_the_test_files_that_import_its_module(
        workspace: Workspace, calls: dict[str, list], tmp_path: Path) -> None:
    """docs/TESTKIT.md § Default import edge: a tree with no ``selector`` gives a mutant of ``src/demo.py`` the
    test files that import ``demo``."""
    put(workspace.worktree, {"tests/test_demo.py": "import demo\n\n\ndef test_demo():\n    assert demo.X == 2\n"},
        "test demo")
    listing = write_mutants(tmp_path, [{"label": "a", "edits": DEMO_EDIT}])
    assert workspace.run(TASK, "--mutants", str(listing)) == 0
    [(_argv, entries)] = calls["mutants"]
    assert entries[0]["tests"] == ["tests/test_demo.py"]


def test_tests_after_dashes_judge_every_mutant_from_the_given_file(
        workspace: Workspace, calls: dict[str, list], tmp_path: Path) -> None:
    listing = write_mutants(tmp_path, [{"label": "a", "edits": DEMO_EDIT}])
    assert workspace.run(TASK, "--mutants", str(listing), "--", "tests/test_value.py") == 0
    [(argv, entries)] = calls["mutants"]
    assert Path(argv[3]) == listing.resolve() and "tests" not in entries[0]
    assert argv[argv.index("--tests") + 1:] == ["tests/test_value.py"] and "--lines" not in argv
    assert "--verifier-argv" not in argv  # the worktree's AGENTS.md names no verifier


def test_an_export_names_its_own_verifier_argv_file_never_the_worktrees(
        workspace: Workspace, calls: dict[str, list], tmp_path: Path) -> None:
    """The export's own verifier classifies its commit's input set, for the mutant baseline and for a --sha run's
    input evidence; the worktree's file is never named (docs/TESTKIT.md § Input evidence)."""
    put(workspace.worktree, {"AGENTS.md": "# AGENTS.md\n\n## Integration verification\n\n"
                                          "- Verifier argv file: verify.json\n- Verifier timeout: 60\n",
                             "verify.json": "[]\n"}, "name the verifier")
    listing = write_mutants(tmp_path, [{"label": "a", "edits": DEMO_EDIT}])
    assert workspace.run(TASK, "--mutants", str(listing), "--", "tests/test_value.py") == 0
    [(argv, _entries)] = calls["mutants"]
    named = Path(argv[argv.index("--verifier-argv") + 1])
    assert named.name == "verify.json" and named.parent.name == "tree" and named.parent.parent.name == argv[0]
    assert argv.index("--verifier-argv") < argv.index("--tests")
    tip = git(workspace.worktree, "rev-parse", "HEAD")
    assert workspace.run(TASK, "--sha", tip) == 0
    run_argv = calls["run"][-1]
    named = Path(run_argv[run_argv.index("--verifier-argv") + 1])
    assert named.parent.name == "tree" and named.parent.parent.name == run_argv[run_argv.index("--scratch") + 1]


def test_a_candidate_is_verified_at_the_branch_tip_or_the_given_sha(workspace: Workspace,
                                                                    calls: dict[str, list]) -> None:
    assert workspace.run(TASK, "--candidate") == 0
    assert workspace.run(TASK, "--candidate", "--sha", workspace.base) == 0
    assert calls["candidate"] == [(TASK, None), (TASK, workspace.base)]


def test_clean_deletes_only_the_scratches_of_the_task(workspace: Workspace, atc_tmp: Path) -> None:
    for name, task in (("ATC-0001-aaaaaaa", TASK), ("ATC-0001", TASK), ("ATC-0002-bbbbbbb", "ATC-0002")):
        place = atc_tmp / name
        (place / "tree").mkdir(parents=True)
        (place / "tree" / "f.txt").write_text("x", encoding="utf-8")
        scratch.register(atc_tmp, place, place / "tree", task=task)
    assert workspace.run(TASK, "--clean") == 0
    assert not (atc_tmp / "ATC-0001-aaaaaaa").exists() and not (atc_tmp / "ATC-0001").exists()
    assert (atc_tmp / "ATC-0002-bbbbbbb").is_dir()
    assert [row.task for row in scratch.registered(atc_tmp)] == ["ATC-0002"]


def test_list_names_the_task_scratches_and_list_alone_names_every_scratch(workspace: Workspace, atc_tmp: Path,
                                                                          capsys: pytest.CaptureFixture[str]) -> None:
    for name, task in (("ATC-0001-aaaaaaa", TASK), ("ATC-0002-bbbbbbb", "ATC-0002")):
        (atc_tmp / name).mkdir()
        scratch.register(atc_tmp, atc_tmp / name, atc_tmp / name, task=task)
    assert workspace.run("--list", TASK) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"1 scratches of {TASK}" and lines[1].split()[0] == "ATC-0001-aaaaaaa"
    assert task_tests.main(["--list"], root=workspace.root, environ=dict(HARNESS, ATC_TMP=str(atc_tmp))) == 0
    listed = capsys.readouterr().out
    assert "ATC-0001-aaaaaaa" in listed and "ATC-0002-bbbbbbb" in listed


@pytest.mark.parametrize("argv", [["--private"], ["--private", "--"], [TASK, "--private", "--", "x"]])
def test_private_takes_a_command_after_dashes_and_no_id(argv: list[str], tmp_path: Path,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        task_tests.main(argv, root=tmp_path, environ=dict(HARNESS))
    assert stopped.value.code == 2 and "--private takes no ID and a command after --" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], [TASK, "--clean", "--all"], [TASK, "--clean", "--", "-q"],
                                  [TASK, "--candidate", "--all"], [TASK, "--clean", "--sha", "HEAD"]])
def test_a_wrong_form_is_a_usage_error(argv: list[str], tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as stopped:
        task_tests.main(argv, root=tmp_path, environ=dict(HARNESS))
    assert stopped.value.code == 2


def test_a_task_without_a_worktree_is_refused_with_the_sha_hint(workspace: Workspace, calls: dict[str, list],
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    git(workspace.project, "worktree", "remove", "--force", str(workspace.worktree))
    assert workspace.run(TASK) == 1
    err = capsys.readouterr().err
    assert err.startswith("STOP NO_WORKTREE: ") and f"hint: atc test {TASK} --sha SHA tests a commit" in err
    assert calls["run"] == []


def test_a_tree_without_testkit_is_refused_with_the_tests_none_hint(workspace: Workspace, calls: dict[str, list],
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    (workspace.worktree / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1"\n', encoding="utf-8")
    assert workspace.run(TASK) == 1
    err = capsys.readouterr().err
    assert err.startswith("STOP NO_TESTKIT: ") and "hint: its tasks use tests: none" in err
    assert calls["run"] == []


def test_a_sha_that_names_no_commit_is_refused_before_any_run(workspace: Workspace, calls: dict[str, list],
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    assert workspace.run(TASK, "--sha", "0" * 40) == 9
    err = capsys.readouterr().err
    assert err.startswith("STOP SHA_NOT_FOUND: ") and "hint: name a commit of the task's repository" in err
    assert calls["run"] == [] and calls["sync"] == []


@pytest.mark.parametrize("content, problem", [
    ("{not json", "not JSON"),
    ("X = 2\n", "not JSON"),
    ('{"label": "a"}', "the top level is not a non-empty JSON list of mutants"),
    ("[]", "the top level is not a non-empty JSON list of mutants"),
    ('["a"]', "mutant 0 is not an object"),
    ('[{"edits": [{"path": "a", "old": "x", "new": "y"}]}]', "mutant 0 has no label"),
    ('[{"label": "a", "edits": []}]', "a: edits is not a non-empty list"),
    ('[{"label": "a", "edits": [{"path": "a", "old": "x"}]}]', "a: an edit is not {path, old, new} as text"),
    ('[{"label": "a", "edits": [{"path": "C:/x/demo.py", "old": "x", "new": "y"}]}]',
     "a: edit path C:/x/demo.py is not relative to the repository root"),
    ('[{"label": "a", "edits": [{"path": "/x/demo.py", "old": "x", "new": "y"}]}]',
     "a: edit path /x/demo.py is not relative to the repository root"),
    ('[{"label": "a", "edits": [{"path": "a", "old": "x", "new": "y"}], "tests": "t.py"}]',
     "a: tests is not a list of pytest arguments"),
    ('[{"label": "a", "edits": [{"path": "a", "old": "x", "new": "y"}], "kill_rule": "any"}]',
     "a: kill_rule 'any' is not failure or assertion"),
], ids=["broken-json", "python-source", "object", "empty", "not-object", "no-label", "no-edits", "edit-shape",
        "drive-path", "rooted-path", "tests-shape", "kill-rule"])
def test_a_bad_mutants_file_stops_before_any_export_or_scratch(
        workspace: Workspace, calls: dict[str, list], atc_tmp: Path, tmp_path: Path,
        capsys: pytest.CaptureFixture[str], content: str, problem: str) -> None:
    listing = tmp_path / "mutants.json"
    listing.write_text(content, encoding="utf-8")
    assert workspace.run(TASK, "--mutants", str(listing)) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"STOP MUTANTS_INVALID: {listing}: {problem}")
    assert 'hint: write a JSON list of {"label": TEXT, "edits": [{"path": PATH, "old": TEXT, "new": TEXT}]' in err
    assert "relative to the repository root" in err
    assert calls["mutants"] == [] and calls["sync"] == []
    assert scratch.registered(atc_tmp) == [] and not list(atc_tmp.glob(f"{TASK}*"))


def test_a_missing_mutants_file_stops_before_any_scratch(workspace: Workspace, calls: dict[str, list], atc_tmp: Path,
                                                         tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert workspace.run(TASK, "--mutants", str(tmp_path / "absent.json")) == 1
    assert capsys.readouterr().err.startswith(f"STOP MUTANTS_INVALID: {tmp_path / 'absent.json'}: cannot be read")
    assert calls["sync"] == [] and scratch.registered(atc_tmp) == []


def test_the_testkit_runner_reads_the_file_with_the_same_check(tmp_path: Path) -> None:
    listing = tmp_path / "m.json"
    listing.write_text('[{"label": "a", "edits": [{"path": "src/demo.py", "old": "x", "new": "y"}], '
                       '"tests": ["tests/t.py"], "kill_rule": "assertion"}]', encoding="utf-8")
    assert testrun.read_mutants(listing)[0]["label"] == "a"
    listing.write_text("{", encoding="utf-8")
    with pytest.raises(testrun.Refusal) as raised:
        testrun.read_mutants(listing)
    assert raised.value.token == "MUTANTS_INVALID"


def hold_mutants_lock(place: Path) -> subprocess.Popen[str]:
    """Another process holding ``place``'s mutants lock until its stdin closes."""
    holder = subprocess.Popen([sys.executable, "-c", "import sys\nfrom pathlib import Path\n"
                               "from atc_testkit import scratch\nwith scratch.mutants_lock(Path(sys.argv[1])):\n"
                               "    print('held', flush=True)\n    sys.stdin.read()\n", str(place)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert holder.stdout is not None and holder.stdout.readline() == "held\n"
    return holder


def test_a_held_scratch_refuses_before_the_mutant_selection_is_written(
        workspace: Workspace, calls: dict[str, list], tmp_path: Path, atc_tmp: Path,
        capsys: pytest.CaptureFixture[str]) -> None:
    listing = write_mutants(tmp_path, [{"label": "a", "edits": DEMO_EDIT, "tests": ["tests/test_value.py"]}])
    assert workspace.run(TASK, "--mutants", str(listing)) == 0
    [(argv, _entries)] = calls["mutants"]
    selected = atc_tmp / argv[0] / task_tests.SELECTED_MUTANTS
    before = selected.read_bytes()
    listing = write_mutants(tmp_path, [{"label": "b", "edits": DEMO_EDIT, "tests": ["tests/test_other.py"]}])
    capsys.readouterr()
    holder = hold_mutants_lock(atc_tmp / argv[0])
    try:
        code = workspace.run(TASK, "--mutants", str(listing))
    finally:
        holder.communicate("")
    err = capsys.readouterr().err
    assert code == 9 and "MUTANTS_RUNNING" in err, err
    assert selected.read_bytes() == before and len(calls["mutants"]) == 1


def test_a_real_mutants_run_takes_the_scratch_lock_it_already_holds(
        workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """run_mutants holds the scratch's lock and the real testrun.mutants takes it again in this process: the run
    goes on instead of refusing itself with MUTANTS_RUNNING. Only the environment build is a stand-in; the tests
    run on this interpreter."""
    def sync(directory, environ, run=None) -> Path:
        return Path(directory) / ".venv"
    monkeypatch.setattr(checkout, "sync", sync)
    monkeypatch.setattr(testrun, "copy_interpreter", lambda root, place: sys.executable)
    listing = write_mutants(tmp_path, [{"label": "a", "edits": DEMO_EDIT}])
    here = os.getcwd()
    try:  # the run enters its tree before the baseline, and the scratch's teardown deletes that tree
        code = task_tests.main([TASK, "--mutants", str(listing), "--", "tests/test_value.py"], root=workspace.root,
                               environ=dict(os.environ, **HARNESS))
    finally:
        os.chdir(here)
    out, err = capsys.readouterr()
    assert "MUTANTS_RUNNING" not in err, err
    assert code == 1 and "BASELINE-OK" in out and out.splitlines()[-1] == "done: 1 mutants, 1 survived a", out + err

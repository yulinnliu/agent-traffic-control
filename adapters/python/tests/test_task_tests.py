"""`atc test ID` (scripts/task_tests.py, docs/TESTKIT.md § Commands) on tmp repositories: the related tests of a
task's worktree, an export of a commit, mutants, the candidate, and the scratch listing and cleaning.

The uv sync, the pytest runs, the mutant runs and the candidate verifier are stand-ins that record what the front
hands them; the workspace, the task records, the worktrees and the scratch registry are real.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

import git_integrate
import task_tests
from atc_testkit import checkout, scratch, testrun

pytestmark = pytest.mark.integration

TASK = "ATC-0001"
BRANCH = "claude+ATC-0001"
HARNESS = {"CLAUDECODE": "1"}  # the harness the task records name: claude
TESTKIT = '[project]\nname = "demo"\nversion = "1"\n\n[tool.atc-testkit]\ntimeout = 60\n'
LOCK = 'version = 1\nsource = "example/governance"\nrevision = "' + "a" * 40 + '"\n'


def git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env,
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


@pytest.fixture()
def workspace(tmp_path: Path, atc_tmp: Path) -> Workspace:
    """A workspace whose project `demo` declares the prefix ATC, with the task's worktree on its branch."""
    root = tmp_path / "workspace"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    put(root, {"AGENTS.md": "# AGENTS.md\n", ".gitignore": "workspace.yaml\nprojects/\nworktrees/\nstate/\n"})
    (root / "workspace.yaml").write_text(
        f"version: 1\nworkspace_root: '{root}'\ngovernance_source: example/governance\nprojects:\n"
        "  demo:\n    repo: projects/demo\n    worktrees: worktrees/demo\n    state: state/demo-data\n",
        encoding="utf-8")
    project = root / "projects" / "demo"
    project.mkdir(parents=True)
    git(project, "init", "-q", "-b", "main")
    base = put(project, {
        "AGENTS.md": "# AGENTS.md\n\n## Task records\n\n- Task prefix: ATC\n\nProse after the line.\n",
        ".atc/atc.lock": LOCK, "pyproject.toml": TESTKIT, "src/demo.py": "X = 1\n",
        "tests/test_value.py": "def test_value():\n    assert True\n",
    })
    worktree = root / "worktrees" / "demo" / BRANCH
    git(project, "worktree", "add", "-q", "-b", BRANCH, str(worktree), "main")
    put(worktree, {".atc/tasks/ATC-0001.yaml": record(base), "src/demo.py": "X = 2\n"}, "open ATC-0001")
    return Workspace(root, project, worktree, base)


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """The stand-ins: each call's arguments in order; the mutant run also reads the mutant list it is given."""
    seen: dict[str, list] = {"run": [], "mutants": [], "candidate": [], "sync": []}

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


def test_all_runs_the_full_suite_and_passes_pytests_arguments_after_dashes(workspace: Workspace,
                                                                          calls: dict[str, list]) -> None:
    assert workspace.run(TASK, "--all", "--", "-q") == 0
    [argv] = calls["run"]
    assert option(argv, "--label") == "all" and "--related" not in argv and argv[-2:] == ["--", "-q"]


def test_a_sha_is_exported_into_the_task_and_commit_scratch_and_reused(workspace: Workspace, calls: dict[str, list],
                                                                       atc_tmp: Path) -> None:
    name = f"{TASK}-{workspace.base[:7]}"
    assert workspace.run(TASK, "--sha", workspace.base) == 0
    assert (atc_tmp / name / "tree" / "pyproject.toml").is_file()
    [row] = scratch.registered(atc_tmp)
    assert (row.task, row.commit, row.project) == (TASK, workspace.base, "demo")
    assert len(calls["sync"]) == 1
    assert option(calls["run"][-1], "--scratch") == name

    assert workspace.run(TASK, "--sha", workspace.base) == 0
    assert len(calls["sync"]) == 1, "a second run reuses the scratch's environment"
    assert option(calls["run"][-1], "--scratch") == name
    assert len(scratch.registered(atc_tmp)) == 1


def test_mutants_judge_the_branch_tip_by_default_with_each_mutants_kill_rule(
        workspace: Workspace, calls: dict[str, list], tmp_path: Path) -> None:
    """Without tests after --, the judging tests are those the branch changed since its base, read from the
    export's source commit (the export is no Git working tree)."""
    put(workspace.worktree, {"tests/test_value.py": "def test_value():\n    assert 2 == 2\n"}, "test the widget")
    listing = tmp_path / "mutants.json"
    edit = [{"path": "src/demo.py", "old": "X = 2", "new": "X = 3"}]
    listing.write_text(json.dumps([{"label": "a", "edits": edit, "kill_rule": "assertion"},
                                   {"label": "b", "edits": edit}]), encoding="utf-8")
    assert workspace.run(TASK, "--mutants", str(listing)) == 0
    [(argv, entries)] = calls["mutants"]
    tip = git(workspace.worktree, "rev-parse", "HEAD")
    assert argv[0] == f"{TASK}-{tip[:7]}" and argv[1] == tip
    assert argv[argv.index("--tests") + 1:] == ["tests/test_value.py"]
    assert [entry.get("kill_rule") for entry in entries] == ["assertion", None]


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

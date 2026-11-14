"""Task worktrees of contract 2: identity from the harness, create, resume, remove and audit (managed_worktree.py).

The library is driven through its functions; `atc task` reaches the same operations through task_records.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import managed_worktree as hw

pytestmark = pytest.mark.integration

PROJECT = "demo-project"
TASK = "DEMO-0001"
CLAUDE = {"CLAUDECODE": "1"}


def run_git(repo: Path, *args: str, check: bool = True) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env,
                          check=check).stdout.strip()


def commit(repo: Path, path: str, content: str) -> str:
    (repo / path).write_text(content, encoding="utf-8")
    run_git(repo, "add", "--", path)
    run_git(repo, "commit", "-q", "-m", f"edit {path}")
    return run_git(repo, "rev-parse", "HEAD")


def write_map(root: Path) -> None:
    (root / "workspace.yaml").write_text(
        "version: 1\n"
        f"workspace_root: '{root}'\n"
        "projects:\n"
        f"  {PROJECT}:\n"
        f"    repo: projects/{PROJECT}\n"
        f"    worktrees: worktrees/{PROJECT}\n"
        f"    state: state/{PROJECT}-data\n",
        encoding="utf-8")


class Workspace:
    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        self.home = home
        self.repo = root / "projects" / PROJECT
        self.worktrees = root / "worktrees" / PROJECT

    def ctx(self) -> hw.Context:
        return hw.context(self.root, {}, PROJECT, home=str(self.home))

    def locked(self, path: Path) -> str | None:
        for block in run_git(self.repo, "worktree", "list", "--porcelain").split("\n\n"):
            lines = dict(line.partition(" ")[::2] for line in block.splitlines())
            if os.path.normcase(os.path.normpath(lines.get("worktree", ""))) == os.path.normcase(str(path)):
                return lines.get("locked")
        return None

    def branch_exists(self, branch: str) -> bool:
        return hw.branch_present(str(self.repo), branch) is True


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    root = tmp_path / "workspace"
    repo = root / "projects" / PROJECT
    repo.mkdir(parents=True)
    run_git(repo, "init", "-q", "-b", "main")
    commit(repo, "a.txt", "a\n")
    write_map(root)
    home = tmp_path / "home"
    home.mkdir()
    return Workspace(root, home)


def make_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


# identity and harness

@pytest.mark.parametrize("environ, harness", [
    ({"CLAUDECODE": "1"}, "claude"),
    ({"CODEX_HOME": "/x"}, "codex"),
    ({"DSH_SESSION": "1"}, "dsh"),
    ({}, None),
    ({"CLAUDECODE": "1", "CODEX_HOME": "/x"}, None),
], ids=["claude", "codex", "dsh", "none", "several"])
def test_the_harness_comes_from_the_environment(environ: dict, harness: str | None) -> None:
    assert hw.detect_harness(environ) == harness


def test_identity_is_harness_plus_task_id() -> None:
    assert hw.identity("claude", TASK) == f"claude+{TASK}"


# create

def test_create_makes_a_locked_worktree_and_branch_from_main(ws: Workspace) -> None:
    result = hw.create(ws.ctx(), "claude", TASK)
    assert result.code == "OK", result
    target = ws.worktrees / f"claude+{TASK}"
    assert target.is_dir()
    assert ws.branch_exists(f"claude+{TASK}")
    assert ws.locked(target) == f"claude:{TASK}"
    assert result.evidence["base"] == run_git(ws.repo, "rev-parse", "main")


def test_create_of_an_existing_worktree_never_mutates(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    again = hw.create(ws.ctx(), "claude", TASK)
    assert again.code == "ALREADY_EXISTS"
    assert ws.locked(ws.worktrees / f"claude+{TASK}") == f"claude:{TASK}"


def test_create_over_a_branch_without_a_worktree_is_an_identity_mismatch(ws: Workspace) -> None:
    run_git(ws.repo, "branch", f"claude+{TASK}")
    assert hw.create(ws.ctx(), "claude", TASK).code == "IDENTITY_MISMATCH"


def test_create_into_an_unregistered_directory_is_path_unsafe(ws: Workspace) -> None:
    (ws.worktrees / f"claude+{TASK}").mkdir(parents=True)
    assert hw.create(ws.ctx(), "claude", TASK).code == "PATH_UNSAFE"


def stopped(call) -> str:
    """The code of the refusal ``call`` raises as Stop, or of the Result it returns."""
    try:
        return call().code
    except hw.Stop as stop:
        return stop.result.code


def test_create_refuses_a_reparse_point_at_the_target(ws: Workspace, tmp_path: Path) -> None:
    ws.worktrees.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    make_link(ws.worktrees / f"claude+{TASK}", elsewhere)
    assert stopped(lambda: hw.create(ws.ctx(), "claude", TASK)) == "PATH_UNSAFE"


def test_the_main_checkout_is_never_a_target(ws: Workspace) -> None:
    assert stopped(lambda: hw.remove(ws.ctx(), "main")) == "NOT_FOUND"
    assert ws.repo.is_dir()


# resume

def test_resume_reports_how_far_main_moved(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    commit(ws.repo, "b.txt", "b\n")
    result = hw.resume(ws.ctx(), f"claude+{TASK}")
    assert result.code == "OK"
    assert result.evidence["behind_main"] == 1
    assert result.target == str(ws.worktrees / f"claude+{TASK}")


def test_resume_of_a_missing_task_never_creates(ws: Workspace) -> None:
    assert stopped(lambda: hw.resume(ws.ctx(), f"claude+{TASK}")) == "NOT_FOUND"
    assert not ws.worktrees.exists()


def test_resume_finds_an_old_style_worktree_by_its_branch(ws: Workspace) -> None:
    """A worktree created under the earlier `worktree-` branch name is found from its record's branch."""
    old_target = ws.worktrees / "claude+cli-redesign"
    run_git(ws.repo, "worktree", "add", "--lock", "--reason", "claude:cli-redesign", "-b",
            "worktree-claude+cli-redesign", str(old_target), "main")
    result = hw.resume(ws.ctx(), "worktree-claude+cli-redesign")
    assert result.code == "OK", result
    assert result.target == str(old_target)


def test_resume_refuses_a_worktree_replaced_by_a_reparse_point(ws: Workspace, tmp_path: Path) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    target = ws.worktrees / f"claude+{TASK}"
    backing = tmp_path / "backing"
    backing.mkdir()
    shutil.move(str(target), str(backing / target.name))
    empty = tmp_path / "empty"
    empty.mkdir()
    make_link(target, empty)
    assert stopped(lambda: hw.resume(ws.ctx(), f"claude+{TASK}")) == "PATH_UNSAFE"


# remove

def test_remove_deletes_a_merged_clean_worktree_and_its_branch(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    target = ws.worktrees / f"claude+{TASK}"
    result = hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK)
    assert result.code == "OK", result
    assert not target.exists()
    assert not ws.branch_exists(f"claude+{TASK}")
    assert result.evidence["branch_kept"] is False


def test_remove_keeps_an_unmerged_worktree(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    target = ws.worktrees / f"claude+{TASK}"
    commit(target, "work.txt", "work\n")
    result = hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK)
    assert result.code == "UNMERGED"
    assert target.is_dir() and ws.branch_exists(f"claude+{TASK}")


def test_remove_keeps_a_dirty_worktree(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    target = ws.worktrees / f"claude+{TASK}"
    (target / "scratch.txt").write_text("unsaved\n", encoding="utf-8")
    result = hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK)
    assert result.code == "DIRTY"
    assert target.is_dir()


def test_remove_goes_when_the_lock_was_already_released(ws: Workspace) -> None:
    """A worktree whose lock is gone is removed the same way: the lock is only restored after a failure."""
    hw.create(ws.ctx(), "claude", TASK)
    target = ws.worktrees / f"claude+{TASK}"
    run_git(ws.repo, "worktree", "unlock", str(target))
    assert hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK).code == "OK"


def test_abandon_keeps_the_tip_under_the_archive_tag(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    target = ws.worktrees / f"claude+{TASK}"
    tip = commit(target, "work.txt", "work\n")
    result = hw.remove(ws.ctx(), f"claude+{TASK}", abandon=True, task_id=TASK)
    assert result.code == "OK", result
    assert not target.exists() and not ws.branch_exists(f"claude+{TASK}")
    assert run_git(ws.repo, "rev-parse", f"refs/tags/archive/{TASK}") == tip


def test_abandon_refuses_an_archive_tag_at_another_commit(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    target = ws.worktrees / f"claude+{TASK}"
    commit(target, "work.txt", "work\n")
    run_git(ws.repo, "tag", f"archive/{TASK}", run_git(ws.repo, "rev-parse", "main"))
    result = hw.remove(ws.ctx(), f"claude+{TASK}", abandon=True, task_id=TASK)
    assert result.code == "ARCHIVE_MISMATCH"
    assert target.is_dir() and ws.branch_exists(f"claude+{TASK}")


# audit

def test_audit_reports_removability_per_worktree(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    results = hw.audit(ws.ctx())
    rows = [r for r in results if r.code == "OK"]
    assert len(rows) == 1
    assert hw.removability(rows[0].evidence) == "removable"


def test_audit_flags_an_unregistered_directory(ws: Workspace) -> None:
    ws.worktrees.mkdir(parents=True)
    (ws.worktrees / "stray").mkdir()
    results = hw.audit(ws.ctx())
    assert any(r.code == "PATH_UNSAFE" and r.target.endswith("stray") for r in results)


def test_audit_with_no_worktrees_has_no_findings(ws: Workspace) -> None:
    assert hw.audit(ws.ctx()) == []

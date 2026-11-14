"""Task worktrees of contract 2: identity from the harness, create, resume, remove and audit (managed_worktree.py).

The library is driven through its functions; `atc task` reaches the same operations through task_records.
"""

from __future__ import annotations

import json
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
LOCK = f"claude:{TASK}"


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


def write_map(root: Path, *, worktrees: str = f"worktrees/{PROJECT}", other: dict[str, str] | None = None) -> None:
    lines = ["version: 1", f"workspace_root: '{root}'", "projects:",
             f"  {PROJECT}:", f"    repo: projects/{PROJECT}", f"    worktrees: {worktrees}",
             f"    state: state/{PROJECT}-data"]
    if other is not None:
        lines += ["  other:"] + [f"    {field}: {value}" for field, value in other.items()]
    (root / "workspace.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


class Workspace:
    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        self.home = home
        self.repo = root / "projects" / PROJECT
        self.worktrees = root / "worktrees" / PROJECT

    def ctx(self, environ: dict[str, str] | None = None) -> hw.Context:
        return hw.context(self.root, environ or {}, PROJECT, home=str(self.home))

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


def codex_home(tmp_path: Path, config: str) -> dict[str, str]:
    home = tmp_path / "codex"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(config, encoding="utf-8")
    return {"CODEX_HOME": str(home)}


def stopped(call) -> str:
    """The code of the refusal ``call`` raises as Stop, or of the Result it returns."""
    try:
        return call().code
    except hw.Stop as stop:
        return stop.result.code


def refusal(call) -> hw.Result:
    """The Result ``call`` returns, or the Result of the Stop it raises."""
    try:
        return call()
    except hw.Stop as stop:
        return stop.result


def stop_result(call) -> hw.Result:
    try:
        call()
    except hw.Stop as stop:
        return stop.result
    raise AssertionError("expected a refusal")


def intercept(monkeypatch: pytest.MonkeyPatch, verb: tuple[str, ...], respond) -> None:
    """Route `git <verb>` through ``respond(run, args)``, where ``run()`` performs the real command."""
    real_git = hw.git

    def patched(repo, *args, readonly=False, input=None):
        if args[:len(verb)] == verb:
            return respond(lambda: real_git(repo, *args, readonly=readonly, input=input), args)
        return real_git(repo, *args, readonly=readonly, input=input)

    monkeypatch.setattr(hw, "git", patched)


def failing(stderr: str):
    return lambda run, args: subprocess.CompletedProcess(args, 1, "", stderr)


def merge_into(repo: Path, path: Path) -> None:
    tip = commit(path, "work.txt", "work\n")
    run_git(repo, "merge", "-q", "--ff-only", tip)


def merged_task(ws: Workspace) -> Path:
    hw.create(ws.ctx(), "claude", TASK)
    path = ws.worktrees / f"claude+{TASK}"
    merge_into(ws.repo, path)
    return path


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


# contract, map and project resolution

def test_malformed_map_is_map_invalid(ws: Workspace) -> None:
    (ws.root / "workspace.yaml").write_text("version: 1\nworkspace_root: 3\n", encoding="utf-8")
    assert stop_result(lambda: hw.context(ws.root, {}, PROJECT)).code == "MAP_INVALID"


def test_map_root_that_is_not_this_checkout_is_map_invalid(ws: Workspace, tmp_path: Path) -> None:
    text = (ws.root / "workspace.yaml").read_text(encoding="utf-8").replace(str(ws.root), str(tmp_path / "elsewhere"))
    (ws.root / "workspace.yaml").write_text(text, encoding="utf-8")
    assert stop_result(lambda: hw.context(ws.root, {}, PROJECT)).code == "MAP_INVALID"


def test_atc_root_override_pointing_elsewhere_is_map_invalid(ws: Workspace, tmp_path: Path) -> None:
    assert stop_result(lambda: hw.context(ws.root, {"ATC_ROOT": str(tmp_path)}, PROJECT)).code == "MAP_INVALID"


def test_an_unknown_project_is_refused_with_the_registered_names(ws: Workspace) -> None:
    result = stop_result(lambda: hw.context(ws.root, {}, "missing"))
    assert (result.code, result.detail) == ("UNKNOWN_PROJECT", f"registered: {PROJECT}")


def two_projects(ws: Workspace) -> Path:
    other = ws.root / "projects" / "other-project"
    other.mkdir(parents=True)
    run_git(other, "init", "-q", "-b", "main")
    commit(other, "b.txt", "b\n")
    write_map(ws.root, other={"repo": "projects/other-project", "worktrees": "worktrees/other-project",
                              "state": "state/other-project-data"})
    return other


def test_project_is_inferred_from_the_repository_and_its_worktrees(ws: Workspace) -> None:
    other = two_projects(ws)
    workspace = hw.load_workspace(ws.root, {})
    assert hw.infer_project(workspace, str(ws.repo / "sub")).name == PROJECT
    assert hw.infer_project(workspace, str(ws.worktrees / f"claude+{TASK}")).name == PROJECT
    assert hw.infer_project(workspace, str(other)).name == "other"
    assert hw.infer_project(workspace, str(ws.root)) is None


def root_repo(ws: Workspace) -> Path:
    """Make the workspace root a Git repository whose topics live under worktrees/root."""
    run_git(ws.root, "init", "-q", "-b", "main")
    (ws.root / ".gitignore").write_text("/projects/\n/worktrees/\n/state/\n/workspace.yaml\n", encoding="utf-8")
    commit(ws.root, "AGENTS.md", "# root\n")
    return ws.root / "worktrees" / "root"


def test_root_worktrees_are_inferred_only_under_their_own_root(ws: Workspace) -> None:
    topics = root_repo(ws)
    workspace = hw.load_workspace(ws.root, {})
    assert hw.infer_project(workspace, str(topics / f"claude+{TASK}")).name == "root"
    assert hw.infer_project(workspace, str(ws.root)) is None


def test_the_workspace_root_is_a_project_with_its_own_lifecycle(ws: Workspace) -> None:
    topics = root_repo(ws)
    target = topics / f"claude+{TASK}"
    ctx = hw.context(ws.root, {}, "root", home=str(ws.home))
    result = hw.create(ctx, "claude", TASK)
    assert (result.code, os.path.normcase(result.target)) == ("OK", os.path.normcase(str(target)))
    assert run_git(target, "branch", "--show-current") == f"claude+{TASK}"
    assert hw.resume(ctx, f"claude+{TASK}").code == "OK"
    audited = hw.audit(ctx)
    assert [(r.code, os.path.normcase(r.target)) for r in audited] == [("OK", os.path.normcase(str(target)))]
    merge_into(ws.root, target)
    removed = hw.remove(ctx, f"claude+{TASK}", task_id=TASK)
    assert removed.code == "OK", removed
    assert not target.exists()
    assert run_git(ws.root, "branch", "--list", f"claude+{TASK}") == ""


# path safety

def test_worktrees_outside_the_workspace_root_are_refused(ws: Workspace) -> None:
    write_map(ws.root, worktrees="../outside")
    result = refusal(lambda: hw.create(ws.ctx(), "claude", TASK))
    assert (result.code, result.detail) == ("PATH_UNSAFE", "outside-root")
    assert not (ws.root.parent / "outside").exists()


def test_a_worktrees_root_equal_to_the_workspace_root_is_refused(ws: Workspace) -> None:
    """The map validator rejects this value, so the check is driven with the Project directly."""
    workspace = hw.load_workspace(ws.root, {})
    project = hw.Project(PROJECT, str(ws.repo), str(ws.root), None)
    with pytest.raises(hw.Stop) as stop:
        hw.check_target_path(workspace, project, str(ws.root / f"claude+{TASK}"), [])
    assert (stop.value.result.code, stop.value.result.detail) == ("PATH_UNSAFE", "outside-root")


def test_another_projects_state_directory_is_protected(ws: Workspace) -> None:
    """Another project's repository cannot be a target: its own .claude/worktrees root overlaps first."""
    target_rel = f"worktrees/{PROJECT}/claude+{TASK}"
    write_map(ws.root, other={"repo": "projects/other", "worktrees": "worktrees/other", "state": target_rel})
    result = refusal(lambda: hw.create(ws.ctx(), "claude", TASK))
    assert (result.code, result.detail) == ("PATH_UNSAFE", "protected")
    assert not (ws.root / target_rel).exists()


def test_the_project_repository_is_never_a_target_path(ws: Workspace) -> None:
    workspace = hw.load_workspace(ws.root, {})
    project = workspace.projects[PROJECT]
    with pytest.raises(hw.Stop) as stop:
        hw.check_target_path(workspace, project, str(ws.repo), [])
    assert stop.value.result.code == "PATH_UNSAFE"


@pytest.mark.parametrize("linked", [f"worktrees/{PROJECT}", f"worktrees/{PROJECT}/claude+{TASK}"])
def test_reparse_point_on_the_target_path_is_refused(ws: Workspace, tmp_path: Path, linked: str) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = ws.root / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    make_link(link, elsewhere)
    assert hw.is_reparse(str(link))
    result = refusal(lambda: hw.create(ws.ctx(), "claude", TASK))
    assert (result.code, result.detail) == ("PATH_UNSAFE", "reparse")
    assert hw.is_reparse(str(link)), "the reparse point was removed"
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("configured", [f"worktrees/{PROJECT}", "worktrees", f"worktrees/{PROJECT}/nested"])
def test_codex_app_root_overlapping_the_worktrees_is_refused(ws: Workspace, tmp_path: Path, configured: str) -> None:
    environ = codex_home(tmp_path, f"[desktop]\ngit-worktree-root = '{ws.root / configured}'\n")
    result = refusal(lambda: hw.create(ws.ctx(environ), "claude", TASK))
    assert (result.code, result.detail) == ("PATH_UNSAFE", "external-root-overlap")
    assert result.evidence["external_roots"][0]["evidence"] == "CONFIG-ONLY"


def test_claude_native_root_overlapping_the_worktrees_is_refused(ws: Workspace) -> None:
    write_map(ws.root, worktrees=f"projects/{PROJECT}/.claude/worktrees")
    result = refusal(lambda: hw.create(ws.ctx(), "claude", TASK))
    assert (result.code, result.detail) == ("PATH_UNSAFE", "external-root-overlap")


def test_external_roots_record_their_evidence_level(ws: Workspace, tmp_path: Path) -> None:
    workspace = hw.load_workspace(ws.root, {})
    missing = {"CODEX_HOME": str(tmp_path / "codex-missing")}
    roots = hw.external_roots(workspace, missing, str(ws.home))
    assert [(r.tool, r.evidence) for r in roots] == [("codex-app", "DOCUMENTED"), ("claude-code", "DOCUMENTED"),
                                                     ("claude-code", "DOCUMENTED")]
    assert [os.path.normcase(r.path) for r in roots[1:]] == [
        os.path.normcase(str(ws.root / ".claude" / "worktrees")), os.path.normcase(str(ws.repo / ".claude" / "worktrees"))]
    configured = codex_home(tmp_path, f"[desktop]\ngit-worktree-root = '{ws.root / 'codex-root'}'\n")
    assert hw.external_roots(workspace, configured, str(ws.home))[0].evidence == "CONFIG-ONLY"
    unreadable = codex_home(tmp_path, "[desktop\n")
    assert hw.external_roots(workspace, unreadable, str(ws.home))[0] == hw.ExternalRoot("codex-app", None, "UNVERIFIED")


def test_unverified_external_root_is_never_presumed_disjoint(ws: Workspace, tmp_path: Path) -> None:
    environ = codex_home(tmp_path, "[desktop\n")
    result = refusal(lambda: hw.create(ws.ctx(environ), "claude", TASK))
    assert (result.code, result.detail) == ("UNSUPPORTED_ENVIRONMENT", "external worktree root unverified: codex-app")
    assert not ws.worktrees.exists()
    audited = hw.audit(ws.ctx(environ))
    assert audited[0].code == "UNSUPPORTED_ENVIRONMENT"


def test_create_checks_the_resolved_destination(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw.os.path, "realpath", lambda p, **_: str(ws.root / "elsewhere"))
    result = refusal(lambda: hw.create(ws.ctx(), "claude", TASK))
    assert (result.code, result.detail) == ("PATH_UNSAFE", "resolved-mismatch")
    assert not ws.worktrees.exists()


def test_resume_checks_the_resolved_destination(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    monkeypatch.setattr(hw.os.path, "realpath", lambda p, **_: str(ws.root / "elsewhere"))
    result = refusal(lambda: hw.resume(ws.ctx(), f"claude+{TASK}"))
    assert (result.code, result.detail) == ("PATH_UNSAFE", "resolved-mismatch")


def test_create_locks_a_new_worktree_on_main(ws: Workspace) -> None:
    result = hw.create(ws.ctx(), "claude", TASK)
    assert result.code == "OK", result
    assert ws.locked(ws.worktrees / f"claude+{TASK}") == LOCK


def test_create_refuses_a_reparse_point_at_the_target(ws: Workspace, tmp_path: Path) -> None:
    ws.worktrees.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    make_link(ws.worktrees / f"claude+{TASK}", elsewhere)
    assert stopped(lambda: hw.create(ws.ctx(), "claude", TASK)) == "PATH_UNSAFE"


# permissions: Git metadata, the worktrees root and the removal path

def test_create_without_writable_metadata_is_permission_denied(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw, "metadata_writable", lambda repo: False)
    result = refusal(lambda: hw.create(ws.ctx(), "claude", TASK))
    assert (result.code, result.detail) == ("PERMISSION_DENIED", "Git metadata is not writable from this session")
    assert not (ws.worktrees / f"claude+{TASK}").exists()


def test_remove_without_writable_metadata_keeps_the_worktree(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    path = merged_task(ws)
    monkeypatch.setattr(hw, "metadata_writable", lambda repo: False)
    result = refusal(lambda: hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK))
    assert result.code == "PERMISSION_DENIED"
    assert path.is_dir() and ws.branch_exists(f"claude+{TASK}") and ws.locked(path) == LOCK


def test_metadata_writability_probes_the_common_dir_and_the_worktrees_dir(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    repo = str(ws.repo)
    assert hw.metadata_writable(repo) is True
    worktrees_dir = os.path.join(repo, ".git", "worktrees")
    monkeypatch.setattr(hw, "dir_writable", lambda d: hw.key(d) != hw.key(worktrees_dir))
    assert hw.metadata_writable(repo) is False
    monkeypatch.setattr(hw, "dir_writable", lambda d: False)
    assert hw.metadata_writable(repo) is False


def test_probe_that_cannot_be_removed_is_reported(tmp_path: Path) -> None:
    def refuse(path: str) -> None:
        raise PermissionError(13, "Access is denied", path)

    with pytest.raises(hw.Stop) as stop:
        hw.probe(str(tmp_path), os.mkdir, refuse)
    assert stop.value.result.code == "PERMISSION_DENIED"
    assert "probe left behind" in stop.value.result.detail


def refuse_add(monkeypatch: pytest.MonkeyPatch, stderr: str) -> None:
    real_git = hw.git

    def patched(repo, *args, readonly=False, input=None):
        if args[:2] == ("worktree", "add") and "-h" not in args:
            return subprocess.CompletedProcess(args, 128, "", stderr)
        return real_git(repo, *args, readonly=readonly, input=input)

    monkeypatch.setattr(hw, "git", patched)


@pytest.mark.parametrize("stderr, writable_after_failure, code", [
    ("fatal: could not create leading directories of 'x': Permission denied\n", True, "PERMISSION_DENIED"),
    ("fatal: simulated\n", False, "PERMISSION_DENIED"),
    ("fatal: simulated\n", True, "UNSUPPORTED_ENVIRONMENT"),
], ids=["git-permission-text", "unwritable-after-failure", "plain-failure"])
def test_a_failed_add_is_classified_from_git_text_and_metadata(ws: Workspace, monkeypatch: pytest.MonkeyPatch,
                                                               stderr: str, writable_after_failure: bool, code: str) -> None:
    calls = {"n": 0}

    def metadata(repo: str) -> bool:
        calls["n"] += 1
        return calls["n"] == 1 or writable_after_failure

    refuse_add(monkeypatch, stderr)
    monkeypatch.setattr(hw, "metadata_writable", metadata)
    result = refusal(lambda: hw.create(ws.ctx(), "claude", TASK))
    assert result.code == code
    assert result.evidence["git"] == stderr.strip()
    assert not (ws.worktrees / f"claude+{TASK}").exists()


def test_destination_without_subdirectory_rights_is_permission_denied(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    probed: list[str] = []
    monkeypatch.setattr(hw, "subdir_creatable", lambda d: probed.append(d) or False)
    result = refusal(lambda: hw.create(ws.ctx(), "claude", TASK))
    assert result.code == "PERMISSION_DENIED"
    assert not ws.worktrees.exists()
    assert hw.key(probed[0]) == hw.key(str(ws.root))


# removal: restoration of the lock and the refusal kinds

def test_failed_removal_restores_the_lock(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    path = merged_task(ws)
    intercept(monkeypatch, ("worktree", "remove"), failing("error: simulated busy directory\n"))
    result = refusal(lambda: hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK))
    assert (result.code, result.detail) == ("PERMISSION_DENIED", "remove failed")
    assert result.evidence["lock_restored"] is True
    assert ws.locked(path) == LOCK
    assert path.is_dir()


def test_git_refusing_a_dirty_removal_is_dirty_and_restores_the_lock(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    path = merged_task(ws)
    intercept(monkeypatch, ("worktree", "remove"),
              failing(f"fatal: '{path}' contains modified or untracked files, use --force to delete it\n"))
    result = refusal(lambda: hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK))
    assert (result.code, result.detail) == ("DIRTY", "git refused removal")
    assert result.evidence["lock_restored"] is True
    assert ws.locked(path) == LOCK


def test_failed_unlock_stops_before_removal_and_claims_no_restoration(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    path = merged_task(ws)
    intercept(monkeypatch, ("worktree", "unlock"), failing("error: simulated\n"))
    result = refusal(lambda: hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK))
    assert (result.code, result.detail) == ("PERMISSION_DENIED", "unlock failed")
    assert "lock_restored" not in result.evidence
    assert ws.locked(path) == LOCK and path.is_dir()


@pytest.mark.parametrize("restore", ["command-succeeds-without-effect", "command-fails-after-effect"])
def test_lock_restored_comes_from_the_read_back_not_the_command(ws: Workspace, monkeypatch: pytest.MonkeyPatch, restore: str) -> None:
    merged_task(ws)
    intercept(monkeypatch, ("worktree", "remove"), failing("error: simulated busy directory\n"))
    if restore == "command-succeeds-without-effect":
        intercept(monkeypatch, ("worktree", "lock"), lambda run, args: subprocess.CompletedProcess(args, 0, "", ""))
        expected = False
    else:
        intercept(monkeypatch, ("worktree", "lock"),
                  lambda run, args: (run(), subprocess.CompletedProcess(args, 1, "", "error: simulated\n"))[1])
        expected = True
    result = refusal(lambda: hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK))
    assert (result.code, result.evidence["lock_restored"]) == ("PERMISSION_DENIED", expected)


@pytest.mark.parametrize("stderr, code, detail", [
    ("error: simulated busy directory\n", "PERMISSION_DENIED", "remove failed"),
    ("fatal: 'x' contains modified or untracked files, use --force to delete it\n", "DIRTY", "git refused removal"),
], ids=["busy", "dirty"])
def test_unobservable_read_back_is_null_and_keeps_the_removal_result(ws: Workspace, monkeypatch: pytest.MonkeyPatch,
                                                                     stderr: str, code: str, detail: str) -> None:
    merged_task(ws)
    real_list = hw.list_worktrees
    state = {"removed": False}

    def blind_after_removal(repo: str):
        if state["removed"]:
            raise hw.Stop(hw.Result("UNSUPPORTED_ENVIRONMENT", repo, "git not found on PATH"))
        return real_list(repo)

    def removal_fails(run, args):
        state["removed"] = True
        return subprocess.CompletedProcess(args, 1, "", stderr)

    monkeypatch.setattr(hw, "list_worktrees", blind_after_removal)
    intercept(monkeypatch, ("worktree", "remove"), removal_fails)
    result = refusal(lambda: hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK))
    assert (result.code, result.detail) == (code, detail)
    assert result.evidence["lock_restored"] is None
    assert result.evidence["restoration_observation"] == "UNOBSERVABLE"


def test_probe_left_behind_after_a_failed_unlock_is_reported(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    path = merged_task(ws)
    real_remove = os.remove
    state = {"unlock_failed": False}

    def unlock_fails(run, args):
        state["unlock_failed"] = True
        return subprocess.CompletedProcess(args, 1, "", "error: simulated\n")

    def stuck_probe(target: str) -> None:
        if state["unlock_failed"] and "hami-worktree-probe-" in os.path.basename(target):
            raise PermissionError(13, "Access is denied", target)
        real_remove(target)

    intercept(monkeypatch, ("worktree", "unlock"), unlock_fails)
    monkeypatch.setattr(hw.os, "remove", stuck_probe)
    result = refusal(lambda: hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK))
    assert (result.code, result.detail) == (
        "PERMISSION_DENIED", "unlock failed: Git metadata is not writable from this session")
    probe = result.evidence["metadata_probe"]
    assert probe["result"] == "leftover"
    leftover = Path(probe["path"])
    assert leftover.name.startswith("hami-worktree-probe-") and leftover.exists()
    assert ws.locked(path) == LOCK


def test_removal_failure_with_unwritable_metadata_says_so(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    merged_task(ws)
    calls = {"n": 0}

    def writable_then_not(repo: str) -> bool:
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr(hw, "metadata_writable", writable_then_not)
    intercept(monkeypatch, ("worktree", "remove"), failing("error: simulated busy directory\n"))
    result = refusal(lambda: hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK))
    assert (result.code, result.detail) == (
        "PERMISSION_DENIED", "remove failed: Git metadata is not writable from this session")


def test_removal_refuses_a_tree_containing_a_reparse_point(ws: Workspace, tmp_path: Path) -> None:
    path = merged_task(ws)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    (path / "sub").mkdir()
    make_link(path / "sub" / "link", outside)
    # Ignore the link so the tree is clean and the reparse scan itself refuses.
    (ws.repo / ".git" / "info").mkdir(exist_ok=True)
    (ws.repo / ".git" / "info" / "exclude").write_text("sub/\n", encoding="utf-8")
    result = refusal(lambda: hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK))
    assert (result.code, result.detail) == ("PATH_UNSAFE", "reparse")
    assert (outside / "keep.txt").exists()
    assert path.is_dir()


def test_branch_is_kept_when_git_refuses_to_delete_it(ws: Workspace) -> None:
    run_git(ws.repo, "switch", "-q", "-c", "side")
    hw.create(ws.ctx(), "claude", TASK)
    path = ws.worktrees / f"claude+{TASK}"
    tip = commit(path, "work.txt", "work\n")
    run_git(ws.repo, "update-ref", "refs/heads/main", tip)
    result = hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK)
    assert (result.code, result.evidence["branch_kept"]) == ("OK", True)
    assert not path.exists() and ws.branch_exists(f"claude+{TASK}")


def test_removal_of_another_harness_identity_finds_nothing(ws: Workspace) -> None:
    path = merged_task(ws)
    result = refusal(lambda: hw.remove(ws.ctx(), f"codex+{TASK}", task_id=TASK))
    assert result.code == "NOT_FOUND"
    assert path.is_dir() and ws.locked(path) == LOCK


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


def test_resume_of_another_harness_identity_finds_nothing(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    assert stopped(lambda: hw.resume(ws.ctx(), f"dsh+{TASK}")) == "NOT_FOUND"
    assert ws.locked(ws.worktrees / f"claude+{TASK}") == LOCK


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


# remove (merged, unmerged, dirty, abandon)

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


def test_remove_refuses_a_worktree_whose_lock_is_missing(ws: Workspace) -> None:
    """Without its lock the worktree's holder is unknown: it stays, with its branch."""
    hw.create(ws.ctx(), "claude", TASK)
    target = ws.worktrees / f"claude+{TASK}"
    run_git(ws.repo, "worktree", "unlock", str(target))
    result = hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK)
    assert (result.code, result.detail) == ("IDENTITY_AMBIGUOUS", "lock missing")
    assert target.is_dir() and run_git(ws.repo, "branch", "--list", f"claude+{TASK}")


@pytest.mark.parametrize("reason", ["codex:DEMO-0001", "claude:DEMO-0002", "claude agent pid 4242", ""])
def test_remove_refuses_a_worktree_locked_by_another_holder(ws: Workspace, reason: str) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    target = ws.worktrees / f"claude+{TASK}"
    run_git(ws.repo, "worktree", "unlock", str(target))
    run_git(ws.repo, "worktree", "lock", *(["--reason", reason] if reason else []), str(target))
    result = hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK)
    assert result.code == "IDENTITY_MISMATCH", (result.code, result.detail)
    assert target.is_dir() and ws.locked(target) == reason


def test_create_refuses_a_branch_that_exists_without_its_worktree(ws: Workspace) -> None:
    run_git(ws.repo, "branch", f"claude+{TASK}", "main")
    result = hw.create(ws.ctx(), "claude", TASK)
    assert (result.code, result.detail) == ("IDENTITY_MISMATCH", f"branch claude+{TASK} exists without this worktree")
    assert not (ws.worktrees / f"claude+{TASK}").exists()


def test_create_names_a_branch_another_process_created_meanwhile(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    def racing(run, args):
        if "-b" not in args:
            return run()
        run_git(ws.repo, "branch", f"claude+{TASK}", "main")
        return subprocess.CompletedProcess(args, 255, "", f"fatal: a branch named 'claude+{TASK}' already exists\n")

    intercept(monkeypatch, ("worktree", "add"), racing)
    result = hw.create(ws.ctx(), "claude", TASK)
    assert (result.code, result.detail) == ("IDENTITY_MISMATCH", f"branch claude+{TASK} was created by another process")


def test_remove_accepts_an_earlier_contracts_lock_with_its_session(ws: Workspace) -> None:
    """`<owner>:<topic>:<session>` names the worktree `<owner>+<topic>` as `<harness>:<ID>` does."""
    hw.create(ws.ctx(), "claude", TASK)
    target = ws.worktrees / f"claude+{TASK}"
    run_git(ws.repo, "worktree", "unlock", str(target))
    run_git(ws.repo, "worktree", "lock", "--reason", f"claude:{TASK}:s1", str(target))
    assert hw.remove(ws.ctx(), f"claude+{TASK}", task_id=TASK).code == "OK"


def moving_after_removal(monkeypatch: pytest.MonkeyPatch, ref: str, to: str) -> None:
    """Move ``ref`` to ``to`` right after Git removes the worktree: between the check and the branch's deletion."""
    real_git = hw.git

    def racing(repo, *args, readonly=False, input=None):
        result = real_git(repo, *args, readonly=readonly, input=input)
        if args[:2] == ("worktree", "remove") and result.returncode == 0:
            run_git(Path(repo), "update-ref", ref, to)
        return result

    monkeypatch.setattr(hw, "git", racing)


@pytest.mark.parametrize("race", ["tag", "branch"])
def test_abandon_keeps_the_branch_when_it_or_the_archive_tag_moves_after_the_check(
        ws: Workspace, monkeypatch: pytest.MonkeyPatch, race: str) -> None:
    """The branch is compare-and-deleted against the checked tip, with the tag verified, in one transaction."""
    hw.create(ws.ctx(), "claude", TASK)
    path = ws.worktrees / f"claude+{TASK}"
    tip = commit(path, "work.txt", "work\n")
    main = run_git(ws.repo, "rev-parse", "main")
    moved = f"refs/tags/archive/{TASK}" if race == "tag" else f"refs/heads/claude+{TASK}"
    moving_after_removal(monkeypatch, moved, main)
    result = hw.remove(ws.ctx(), f"claude+{TASK}", abandon=True, task_id=TASK)
    assert (result.code, result.evidence["branch_kept"]) == ("OK", True), result.evidence
    assert not path.exists()
    assert run_git(ws.repo, "rev-parse", f"refs/heads/claude+{TASK}") == (tip if race == "tag" else main)


@pytest.mark.parametrize("annotated", [False, True])
def test_abandon_preserves_the_exact_tag_and_leaves_other_worktrees(ws: Workspace, annotated: bool) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    path = ws.worktrees / f"claude+{TASK}"
    tip = commit(path, "work.txt", "work\n")
    tag = f"archive/{TASK}"
    if annotated:
        run_git(ws.repo, "tag", "-a", tag, tip, "-m", "retained evidence")
    else:
        run_git(ws.repo, "tag", tag, tip)
    other_id = "DEMO-0002"
    hw.create(ws.ctx(), "codex", other_id)
    other = ws.worktrees / f"codex+{other_id}"
    result = hw.remove(ws.ctx(), f"claude+{TASK}", abandon=True, task_id=TASK)
    assert (result.code, result.evidence["branch_kept"]) == ("OK", False), result.evidence
    assert not path.exists() and other.exists()
    assert ws.locked(other) == f"codex:{other_id}"
    assert run_git(ws.repo, "rev-parse", f"refs/tags/{tag}^{{commit}}") == tip
    assert run_git(ws.repo, "branch", "--list", f"claude+{TASK}") == ""


def test_abandon_creates_the_archive_tag_at_the_tip_when_none_exists(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    path = ws.worktrees / f"claude+{TASK}"
    tip = commit(path, "work.txt", "work\n")
    result = hw.remove(ws.ctx(), f"claude+{TASK}", abandon=True, task_id=TASK)
    assert (result.code, result.evidence["archive"]) == ("OK", f"archive/{TASK}"), result.evidence
    assert run_git(ws.repo, "rev-parse", f"refs/tags/archive/{TASK}^{{commit}}") == tip
    assert not path.exists()


def test_abandon_refuses_an_archive_tag_at_another_commit(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    target = ws.worktrees / f"claude+{TASK}"
    commit(target, "work.txt", "work\n")
    run_git(ws.repo, "tag", f"archive/{TASK}", run_git(ws.repo, "rev-parse", "main"))
    result = hw.remove(ws.ctx(), f"claude+{TASK}", abandon=True, task_id=TASK)
    assert result.code == "ARCHIVE_MISMATCH"
    assert target.is_dir() and ws.branch_exists(f"claude+{TASK}")


def test_abandon_with_a_busy_directory_restores_the_lock_and_keeps_the_tag(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    path = ws.worktrees / f"claude+{TASK}"
    tip = commit(path, "work.txt", "work\n")
    intercept(monkeypatch, ("worktree", "remove"), failing("error: simulated busy directory\n"))
    result = refusal(lambda: hw.remove(ws.ctx(), f"claude+{TASK}", abandon=True, task_id=TASK))
    assert (result.code, result.detail, result.evidence["lock_restored"]) == ("PERMISSION_DENIED", "remove failed", True)
    assert ws.locked(path) == LOCK and path.is_dir()
    assert run_git(ws.repo, "rev-parse", f"refs/tags/archive/{TASK}^{{commit}}") == tip


@pytest.mark.parametrize("fault", [None, "recreated", "config_failure"])
def test_abandon_settles_branch_config_and_reports_what_it_kept(ws: Workspace, monkeypatch: pytest.MonkeyPatch, fault) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    path = ws.worktrees / f"claude+{TASK}"
    branch = f"claude+{TASK}"
    tip = commit(path, "work.txt", "work\n")
    run_git(ws.repo, "config", f"branch.{branch}.remote", "origin")
    run_git(ws.repo, "config", f"branch.{branch}.merge", f"refs/heads/{branch}")
    if fault == "config_failure":
        intercept(monkeypatch, ("config", "--local", "--remove-section"), failing("simulated config lock failure"))
    if fault == "recreated":
        def recreate(run, args):
            result = run()
            if result.returncode == 0:
                run_git(ws.repo, "update-ref", f"refs/heads/{branch}", tip)
            return result

        intercept(monkeypatch, ("update-ref", "--stdin"), recreate)
    result = hw.remove(ws.ctx(), branch, abandon=True, task_id=TASK)
    assert (result.code, result.evidence["branch_kept"]) == ("OK", False)
    assert not path.exists()
    assert run_git(ws.repo, "rev-parse", f"refs/tags/archive/{TASK}^{{commit}}") == tip
    settings = run_git(ws.repo, "config", "--local", "--get-regexp", rf"^branch\.claude\+{TASK}\.", check=False)
    listed = bool(run_git(ws.repo, "branch", "--list", branch))
    if fault is None:
        assert (settings, result.evidence["branch_config"], listed) == ("", "removed", False)
    else:
        assert f"branch.{branch}.remote origin" in settings
        assert result.evidence["branch_config"] == "kept" and result.evidence["warning"]
        assert listed == (fault == "recreated")


# audit

def snapshot(git_dir: Path) -> dict[str, tuple[int, int]]:
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in git_dir.rglob("*") if p.is_file()}


def test_audit_reports_removability_per_worktree(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    results = hw.audit(ws.ctx())
    rows = [r for r in results if r.code == "OK"]
    assert len(rows) == 1
    assert hw.removability(rows[0].evidence) == "removable"


def test_audit_says_what_keeps_a_worktree(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    path = ws.worktrees / f"claude+{TASK}"

    def details() -> list[str]:
        return [r.detail for r in hw.audit(ws.ctx()) if r.code == "OK"]

    (path / "extra.txt").write_text("x\n", encoding="utf-8")
    assert details() == ["not removable: uncommitted changes"]
    run_git(path, "add", "extra.txt")
    run_git(path, "commit", "-q", "-m", "extra")
    assert details() == ["not removable: unmerged"]
    (path / "more.txt").write_text("y\n", encoding="utf-8")
    assert details() == ["not removable: unmerged, uncommitted changes"]


@pytest.mark.parametrize("reason, code, detail", [
    (None, "IDENTITY_AMBIGUOUS", "lock missing"),
    ("codex:DEMO-0001", "IDENTITY_MISMATCH", "lock reason 'codex:DEMO-0001' does not name claude:DEMO-0001"),
])
def test_audit_flags_a_lock_that_does_not_name_the_worktree(ws: Workspace, reason: str | None, code: str,
                                                            detail: str) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    path = ws.worktrees / f"claude+{TASK}"
    run_git(ws.repo, "worktree", "unlock", str(path))
    if reason is not None:
        run_git(ws.repo, "worktree", "lock", "--reason", reason, str(path))
    [result] = hw.audit(ws.ctx())
    assert (result.code, result.detail) == (code, detail)
    assert result.code in hw.HINTS


def test_audit_writes_nothing(ws: Workspace) -> None:
    hw.create(ws.ctx(), "claude", TASK)
    before = snapshot(ws.repo / ".git")
    hw.audit(ws.ctx())
    assert snapshot(ws.repo / ".git") == before


def test_audit_flags_an_unregistered_directory(ws: Workspace) -> None:
    ws.worktrees.mkdir(parents=True)
    (ws.worktrees / "stray").mkdir()
    results = hw.audit(ws.ctx())
    assert any(r.code == "PATH_UNSAFE" and r.target.endswith("stray") for r in results)


def test_audit_flags_a_reparse_worktrees_root_even_when_empty(ws: Workspace, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    ws.worktrees.parent.mkdir(parents=True)
    make_link(ws.worktrees, elsewhere)
    results = hw.audit(ws.ctx())
    assert (results[0].code, results[0].detail) == ("PATH_UNSAFE", "reparse")


def test_audit_with_no_worktrees_has_no_findings(ws: Workspace) -> None:
    assert hw.audit(ws.ctx()) == []


def test_audit_names_candidates_in_use_and_left_over(ws: Workspace, atc_tmp: Path) -> None:
    from atc_testkit import owner as candidate_owner

    candidates = atc_tmp / "candidates"
    candidates.mkdir()
    for name, created in (("gone", "0"), ("live", candidate_owner.created(os.getpid()))):
        tree = candidates / name
        run_git(ws.repo, "worktree", "add", "-q", "--detach", str(tree), "main")
        candidate_owner.write_marker(tree, "integrate", ws.repo, TASK)
        data = json.loads(candidate_owner.marker(tree).read_text(encoding="utf-8"))
        candidate_owner.marker(tree).write_text(json.dumps({**data, "created": created}), encoding="utf-8")
    shutil.rmtree(candidates / "gone")
    results = hw.audit(ws.ctx(), {"ATC_TMP": str(atc_tmp)})
    by_code = {r.code: r for r in results}
    assert set(by_code) == {"CANDIDATE_LEFTOVER", "CANDIDATE_IN_USE"}
    assert f"pid {os.getpid()}" in by_code["CANDIDATE_IN_USE"].detail


# doctor line (contract 2)

def test_readiness_names_contract_2_and_refuses_an_unverified_root(ws: Workspace, tmp_path: Path) -> None:
    root_repo(ws)
    status, detail = hw.readiness(ws.root, {}, str(ws.home))
    assert status == "READY" and detail.startswith(f"contract {hw.CONTRACT};")
    status, detail = hw.readiness(ws.root, codex_home(tmp_path, "[desktop\n"), str(ws.home))
    assert status == "ERROR" and "codex-app root unverified" in detail

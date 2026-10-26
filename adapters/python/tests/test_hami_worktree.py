from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import hami_worktree as hw

PROJECT = "demo-project"


def run_git(repo: Path, *args: str, check: bool = True) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
    )
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=check)
    return result.stdout.strip()


def commit(repo: Path, path: str, content: str) -> str:
    (repo / path).write_text(content, encoding="utf-8")
    run_git(repo, "add", "--", path)
    run_git(repo, "commit", "-q", "-m", f"edit {path}")
    return run_git(repo, "rev-parse", "HEAD")


def write_map(root: Path, projects: dict[str, dict[str, str]]) -> None:
    lines = ["version: 1", f"workspace_root: '{root}'", "projects:"]
    for name, entry in projects.items():
        lines.append(f"  {name}:")
        lines += [f"    {field}: {value}" for field, value in entry.items()]
    (root / "workspace.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def demo_entry(**overrides: str) -> dict[str, str]:
    entry = {"repo": f"projects/{PROJECT}", "worktrees": f"worktrees/{PROJECT}", "state": f"state/{PROJECT}-data"}
    return {**entry, **overrides}


def make_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def replace_path_with_reparse(path: Path, backing_root: Path, link_target: Path) -> None:
    """Move ``path`` aside and leave a reparse point at the same lexical path.

    Frees whatever occupies ``path`` so the link can be created there (a registered
    worktree is moved, an empty parent directory is moved), and keeps Git's
    registration at the lexical path a command starts from. The link points at the
    still-empty ``link_target``, so nothing can be reached through it.
    """
    backing = backing_root / path.name
    assert not backing.exists(), f"backing already holds {backing}"
    backing_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(backing))
    make_link(path, link_target)


class Workspace:
    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        self.home = home
        self.repo = root / "projects" / PROJECT
        self.worktrees = root / "worktrees" / PROJECT
        self.environ = {"CODEX_HOME": str(home / "codex")}

    def path(self, owner: str, topic: str) -> Path:
        return self.worktrees / f"{owner}+{topic}"

    def cli(self, capsys: pytest.CaptureFixture[str], *argv: str, cwd: Path | None = None) -> tuple[int, list[dict]]:
        status = hw.main([*argv, "--json"], root=self.root, environ=self.environ, home=str(self.home),
                         cwd=str(cwd) if cwd is not None else None)
        payload = json.loads(capsys.readouterr().out)
        assert payload["contract"] == hw.CONTRACT
        return status, payload["results"]

    def run(self, capsys: pytest.CaptureFixture[str], command: str, owner: str | None = "claude", topic: str | None = "topic", *extra: str) -> tuple[int, list[dict]]:
        argv = [command, "--project", PROJECT, "--contract", "1"]
        if owner is not None:
            argv += ["--owner", owner]
        if topic is not None:
            argv += ["--topic", topic]
        return self.cli(capsys, *argv, *extra)

    def lock_reason(self, path: Path) -> str | None:
        for block in run_git(self.repo, "worktree", "list", "--porcelain").split("\n\n"):
            lines = dict(line.partition(" ")[::2] for line in block.splitlines())
            if os.path.normcase(os.path.normpath(lines.get("worktree", ""))) == os.path.normcase(str(path)):
                return lines.get("locked")
        return None


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    root = tmp_path / "workspace"
    repo = root / "projects" / PROJECT
    repo.mkdir(parents=True)
    run_git(repo, "init", "-q", "-b", "main")
    commit(repo, "a.txt", "a\n")
    write_map(root, {PROJECT: demo_entry()})
    home = tmp_path / "home"
    home.mkdir()
    return Workspace(root, home)


def single(results: list[dict]) -> dict:
    assert len(results) == 1, results
    return results[0]


# Contract, map, identity parsing and path safety


def test_contract_mismatch_is_reported_before_the_map_is_read(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    (ws.root / "workspace.yaml").write_text("not: [valid", encoding="utf-8")
    status, results = ws.cli(capsys, "audit", "--project", PROJECT, "--contract", "2")
    assert status == 2
    assert single(results)["reason"] == "CONTRACT_INCOMPATIBLE"


def test_malformed_map_is_map_invalid(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    (ws.root / "workspace.yaml").write_text("version: 1\nworkspace_root: 3\n", encoding="utf-8")
    status, results = ws.run(capsys, "audit", None, None)
    assert (status, single(results)["reason"]) == (2, "MAP_INVALID")


def test_map_root_that_is_not_this_checkout_is_map_invalid(ws: Workspace, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_map(tmp_path / "workspace", {PROJECT: demo_entry()})
    text = (ws.root / "workspace.yaml").read_text(encoding="utf-8").replace(str(ws.root), str(tmp_path / "elsewhere"))
    (ws.root / "workspace.yaml").write_text(text, encoding="utf-8")
    status, results = ws.run(capsys, "audit", None, None)
    assert (status, single(results)["reason"]) == (2, "MAP_INVALID")


def test_atc_root_override_pointing_elsewhere_is_map_invalid(ws: Workspace, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws.environ["ATC_ROOT"] = str(tmp_path)
    status, results = ws.run(capsys, "audit", None, None)
    assert (status, single(results)["reason"]) == (2, "MAP_INVALID")


def test_unknown_project(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status, results = ws.cli(capsys, "audit", "--project", "missing", "--contract", "1")
    assert (status, single(results)["reason"]) == (2, "UNKNOWN_PROJECT")


def two_projects(ws: Workspace) -> Path:
    other = ws.root / "projects" / "other-project"
    other.mkdir(parents=True)
    run_git(other, "init", "-q", "-b", "main")
    commit(other, "b.txt", "b\n")
    write_map(ws.root, {PROJECT: demo_entry(), "other-project": demo_entry(
        repo="projects/other-project", worktrees="worktrees/other-project", state="state/other-project-data")})
    return other


def test_project_is_inferred_from_the_repository_and_its_worktrees(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    two_projects(ws)
    (ws.repo / "sub").mkdir()
    status, results = ws.cli(capsys, "create", "--owner", "claude", "--topic", "topic", "--contract", "1", cwd=ws.repo / "sub")
    assert (status, single(results)["reason"]) == (0, "OK")
    assert os.path.normcase(single(results)["target"]) == os.path.normcase(str(ws.path("claude", "topic")))
    status, results = ws.cli(capsys, "audit", "--contract", "1", cwd=ws.path("claude", "topic"))
    assert status == 0
    assert [os.path.normcase(r["target"]) for r in results] == [os.path.normcase(str(ws.path("claude", "topic")))]


def test_explicit_project_wins_over_the_working_directory(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    other = two_projects(ws)
    status, results = ws.run(capsys, "create")
    assert status == 0
    status, results = ws.cli(capsys, "audit", "--project", "other-project", "--contract", "1", cwd=ws.path("claude", "topic"))
    assert status == 0
    assert os.path.normcase(single(results)["target"]) == os.path.normcase(str(other.parents[1] / "worktrees" / "other-project"))


def test_uninferable_project_lists_the_registered_names(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    two_projects(ws)
    status, results = ws.cli(capsys, "audit", "--contract", "1", cwd=ws.root)
    assert (status, single(results)["reason"]) == (2, "UNKNOWN_PROJECT")
    assert f"registered: {PROJECT}, other-project" in single(results)["detail"]


@pytest.mark.parametrize("topic", ["Upper", "-lead", "a+b", "a_b", "a" * 49, ""])
def test_invalid_topic_is_a_usage_error(ws: Workspace, topic: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        ws.run(capsys, "create", "claude", topic)
    assert exc.value.code == 2


def test_unknown_owner_is_a_usage_error(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        ws.run(capsys, "create", "deepseek")
    assert exc.value.code == 2


def test_lock_reason_session_is_opaque() -> None:
    assert hw.parse_lock("claude:topic:a:b:c") == ("claude", "topic", "a:b:c")
    assert hw.parse_lock("claude:topic") is None
    assert hw.parse_lock("claude:topic:") is None
    assert hw.parse_lock("claude:topic:a\nb") is None
    assert hw.parse_lock(None) is None


@pytest.mark.parametrize("command", ["create", "resume", "cleanup"])
def test_worktrees_root_outside_the_workspace_is_refused(ws: Workspace, command: str, capsys: pytest.CaptureFixture[str]) -> None:
    if command == "cleanup":
        assert ws.run(capsys, "create", "claude", "topic", "--session", "s1")[0] == 0
        worktree = ws.path("claude", "topic")
        before = snapshot(ws.repo / ".git")
    else:
        worktree = None
        before = None
    write_map(ws.root, {PROJECT: demo_entry(worktrees="../outside")})
    status, results = ws.run(capsys, command)
    assert (status, single(results)["reason"], results[0]["detail"]) == (1, "PATH_UNSAFE", "outside-root")
    if worktree is not None:
        # Cleanup must reach the path-safety step before it unlocks or removes anything.
        assert worktree.is_dir(), "the refusal did not precede the removal"
        assert snapshot(ws.repo / ".git") == before
    assert not (ws.root.parent / "outside").exists()


def test_target_that_is_another_project_state_is_protected(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    other = ws.worktrees / "claude+topic"
    write_map(ws.root, {PROJECT: demo_entry(), "other": {"repo": "projects/other", "worktrees": "worktrees/other", "state": f"worktrees/{PROJECT}/claude+topic"}})
    status, results = ws.run(capsys, "create")
    assert (single(results)["reason"], results[0]["detail"]) == ("PATH_UNSAFE", "protected")
    assert not other.exists()


@pytest.mark.parametrize("command", ["create", "resume", "cleanup"])
@pytest.mark.parametrize("linked", ["worktrees", f"worktrees/{PROJECT}", f"worktrees/{PROJECT}/claude+topic"])
def test_reparse_point_on_the_path_is_refused(ws: Workspace, tmp_path: Path, command: str, linked: str, capsys: pytest.CaptureFixture[str]) -> None:
    link = ws.root / linked
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if command == "cleanup":
        assert ws.run(capsys, "create", "claude", "topic", "--session", "s1")[0] == 0
        replace_path_with_reparse(link, tmp_path / "backing", elsewhere)
    else:
        link.parent.mkdir(parents=True, exist_ok=True)
        make_link(link, elsewhere)
    assert hw.is_reparse(str(link)), f"{linked} is not a reparse point"
    status, results = ws.run(capsys, command)
    assert (status, single(results)["reason"], results[0]["detail"]) == (1, "PATH_UNSAFE", "reparse")
    assert hw.is_reparse(str(link)), "the reparse point was removed"
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("configured", [f"worktrees/{PROJECT}", "worktrees", f"worktrees/{PROJECT}/nested"])
def test_codex_app_root_overlapping_the_map_root_is_refused(ws: Workspace, configured: str, capsys: pytest.CaptureFixture[str]) -> None:
    codex = Path(ws.environ["CODEX_HOME"])
    codex.mkdir()
    (codex / "config.toml").write_text(f"[desktop]\ngit-worktree-root = '{ws.root / configured}'\n", encoding="utf-8")
    status, results = ws.run(capsys, "create")
    result = single(results)
    assert (status, result["reason"], result["detail"]) == (1, "PATH_UNSAFE", "external-root-overlap")
    assert result["evidence"]["external_roots"][0]["evidence"] == "CONFIG-ONLY"


def test_claude_native_root_overlapping_the_map_root_is_refused(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    write_map(ws.root, {PROJECT: demo_entry(worktrees=f"projects/{PROJECT}/.claude/worktrees")})
    status, results = ws.run(capsys, "create")
    assert (single(results)["reason"], results[0]["detail"]) == ("PATH_UNSAFE", "external-root-overlap")


def test_external_roots_record_their_evidence_level(ws: Workspace) -> None:
    workspace = hw.load_workspace(ws.root, {})
    roots = hw.external_roots(workspace, ws.environ, str(ws.home))
    assert [(r.tool, r.evidence) for r in roots] == [("codex-app", "DOCUMENTED"), ("claude-code", "DOCUMENTED")]
    codex = Path(ws.environ["CODEX_HOME"])
    codex.mkdir()
    (codex / "config.toml").write_text("[desktop\n", encoding="utf-8")
    assert hw.external_roots(workspace, ws.environ, str(ws.home))[0] == hw.ExternalRoot("codex-app", None, "UNVERIFIED")


# Audit


def snapshot(git_dir: Path) -> dict[str, tuple[int, int]]:
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in git_dir.rglob("*") if p.is_file()}


def test_audit_reports_v1_and_legacy_without_writing(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    assert ws.run(capsys, "create", "claude", "topic", "--session", "s1")[0] == 0
    legacy = ws.repo / ".claude" / "worktrees" / "claude+old"
    run_git(ws.repo, "worktree", "add", "-q", "--lock", "-b", "worktree-claude+old", str(legacy), "main")
    before = snapshot(ws.repo / ".git")
    status, results = ws.run(capsys, "audit", None, None)
    assert snapshot(ws.repo / ".git") == before
    assert status == 0
    by_code = {r["reason"]: r for r in results}
    assert set(by_code) == {"OK", "LEGACY_TARGET"}
    assert by_code["OK"]["evidence"] | {"head": None} == {
        "branch": "worktree-claude+topic", "head": None, "locked": True,
        "lock_reason": "claude:topic:s1", "clean": True, "merged": True,
    }


def test_audit_finds_a_missing_lock(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    run_git(ws.repo, "worktree", "unlock", str(ws.path("claude", "topic")))
    status, results = ws.run(capsys, "audit", None, None)
    assert (status, single(results)["reason"], results[0]["detail"]) == (1, "IDENTITY_AMBIGUOUS", "lock missing")


@pytest.mark.parametrize("reason", ["codex:topic:s1", "claude agent pid 4242"])
def test_audit_finds_a_lock_that_names_someone_else(ws: Workspace, reason: str, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    path = str(ws.path("claude", "topic"))
    run_git(ws.repo, "worktree", "unlock", path)
    run_git(ws.repo, "worktree", "lock", "--reason", reason, path)
    status, results = ws.run(capsys, "audit", None, None)
    assert (status, single(results)["reason"]) == (1, "IDENTITY_MISMATCH")


def test_audit_finds_a_branch_that_disagrees_with_the_directory(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    path = ws.path("dsh", "topic")
    run_git(ws.repo, "worktree", "add", "-q", "--lock", "--reason", "dsh:topic:s", "-b", "worktree-claude+topic", str(path), "main")
    status, results = ws.run(capsys, "audit", None, None)
    assert (status, single(results)["reason"]) == (1, "IDENTITY_MISMATCH")


def test_audit_finds_an_unregistered_directory(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.path("claude", "stray").mkdir(parents=True)
    status, results = ws.run(capsys, "audit", None, None)
    assert (status, single(results)["reason"], results[0]["detail"]) == (1, "PATH_UNSAFE", "unregistered")


def test_audit_filter_limits_results_to_one_topic(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "one")
    ws.run(capsys, "create", "claude", "two")
    status, results = ws.run(capsys, "audit", "claude", "two")
    assert single(results)["target"].endswith("claude+two")


def test_audit_with_no_worktrees_is_ok(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status, results = ws.run(capsys, "audit", None, None)
    assert (status, single(results)["reason"]) == (0, "OK")


def test_human_output_is_one_ascii_line_per_target(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status = hw.main(["audit", "--project", PROJECT, "--contract", "1"], root=ws.root, environ=ws.environ, home=str(ws.home))
    out = capsys.readouterr().out
    assert status == 0
    assert out.strip().splitlines() == [f"OK {ws.worktrees} (no worktrees) - continue"]


# Create and resume


def test_create_locks_a_new_worktree_on_main(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    main = run_git(ws.repo, "rev-parse", "main")
    status, results = ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    result = single(results)
    path = ws.path("claude", "topic")
    assert (status, result["reason"], result["target"]) == (0, "OK", str(path))
    assert result["evidence"] == {"branch": "worktree-claude+topic", "base": main}
    assert ws.lock_reason(path) == "claude:topic:s1"
    assert run_git(path, "branch", "--show-current") == "worktree-claude+topic"


def test_create_without_a_session_records_a_dash(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    assert ws.lock_reason(ws.path("claude", "topic")) == "claude:topic:-"


@pytest.mark.parametrize("session", ["s1", "s2"])
def test_create_of_an_existing_identity_never_mutates(ws: Workspace, session: str, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    status, results = ws.run(capsys, "create", "claude", "topic", "--session", session)
    assert (status, single(results)["reason"]) == (1, "ALREADY_EXISTS")
    assert ws.lock_reason(ws.path("claude", "topic")) == "claude:topic:s1"


def test_create_of_a_topic_held_by_another_owner_is_a_mismatch(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic")
    status, results = ws.run(capsys, "create", "codex", "topic")
    assert (status, single(results)["reason"]) == (1, "IDENTITY_MISMATCH")
    assert not ws.path("codex", "topic").exists()


def test_create_with_an_orphan_branch_is_a_mismatch(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    run_git(ws.repo, "branch", "worktree-claude+topic")
    status, results = ws.run(capsys, "create")
    assert (status, single(results)["reason"]) == (1, "IDENTITY_MISMATCH")


def test_create_into_an_occupied_path_is_refused(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.path("claude", "topic").mkdir(parents=True)
    status, results = ws.run(capsys, "create")
    assert (single(results)["reason"], results[0]["detail"]) == ("PATH_UNSAFE", "unregistered")


def test_create_of_a_branch_checked_out_at_a_legacy_path_is_legacy(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    legacy = ws.repo / ".claude" / "worktrees" / "claude+topic"
    run_git(ws.repo, "worktree", "add", "-q", "-b", "worktree-claude+topic", str(legacy), "main")
    status, results = ws.run(capsys, "create")
    assert (status, single(results)["reason"]) == (1, "LEGACY_TARGET")


def test_create_without_writable_metadata_is_permission_denied(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(hw, "metadata_writable", lambda repo: False)
    status, results = ws.run(capsys, "create")
    assert (status, single(results)["reason"]) == (2, "PERMISSION_DENIED")
    assert not ws.path("claude", "topic").exists()


def test_resume_reports_how_far_main_moved(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    commit(ws.repo, "b.txt", "b\n")
    status, results = ws.run(capsys, "resume", "claude", "topic", "--session", "s1")
    assert (status, single(results)["reason"], results[0]["evidence"]["behind_main"]) == (0, "OK", 1)


def test_resume_of_a_missing_topic_never_creates(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status, results = ws.run(capsys, "resume")
    assert (status, single(results)["reason"]) == (1, "NOT_FOUND")
    assert not ws.worktrees.exists()


def test_resume_from_another_session_is_ambiguous_until_rebound(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    status, results = ws.run(capsys, "resume", "claude", "topic", "--session", "s2")
    assert (status, single(results)["reason"]) == (1, "IDENTITY_AMBIGUOUS")
    status, results = ws.run(capsys, "resume", "claude", "topic", "--session", "s2", "--rebind-session")
    assert (status, single(results)["reason"]) == (0, "OK")
    assert ws.lock_reason(ws.path("claude", "topic")) == "claude:topic:s2"


def test_rebind_restores_a_missing_lock(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    path = ws.path("claude", "topic")
    run_git(ws.repo, "worktree", "unlock", str(path))
    assert single(ws.run(capsys, "resume", "claude", "topic", "--session", "s3")[1])["reason"] == "IDENTITY_AMBIGUOUS"
    assert ws.run(capsys, "resume", "claude", "topic", "--session", "s3", "--rebind-session")[0] == 0
    assert ws.lock_reason(path) == "claude:topic:s3"


def test_resume_by_another_harness_is_a_mismatch_even_with_rebind(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    status, results = ws.run(capsys, "resume", "dsh", "topic", "--session", "s1", "--rebind-session")
    assert (status, single(results)["reason"]) == (1, "IDENTITY_MISMATCH")
    assert ws.lock_reason(ws.path("claude", "topic")) == "claude:topic:s1"


def test_resume_of_a_legacy_checkout_is_legacy(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    legacy = ws.repo / ".claude" / "worktrees" / "claude+topic"
    run_git(ws.repo, "worktree", "add", "-q", "-b", "worktree-claude+topic", str(legacy), "main")
    assert single(ws.run(capsys, "resume")[1])["reason"] == "LEGACY_TARGET"


def test_rebind_without_writable_metadata_keeps_the_old_lock(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    monkeypatch.setattr(hw, "metadata_writable", lambda repo: False)
    status, results = ws.run(capsys, "resume", "claude", "topic", "--session", "s2", "--rebind-session")
    assert (status, single(results)["reason"]) == (2, "PERMISSION_DENIED")
    assert ws.lock_reason(ws.path("claude", "topic")) == "claude:topic:s1"


def test_rebind_restores_the_old_lock_when_relocking_fails(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    real_git = hw.git

    def failing_lock(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "lock") and "claude:topic:s2" in args:
            return subprocess.CompletedProcess(args, 1, "", "error: simulated\n")
        return real_git(repo, *args, readonly=readonly)

    monkeypatch.setattr(hw, "git", failing_lock)
    status, results = ws.run(capsys, "resume", "claude", "topic", "--session", "s2", "--rebind-session")
    assert (single(results)["reason"], results[0]["evidence"]["lock_restored"]) == ("PERMISSION_DENIED", True)
    assert ws.lock_reason(ws.path("claude", "topic")) == "claude:topic:s1"


# Cleanup


def merge_into_main(ws: Workspace, path: Path) -> None:
    tip = commit(path, "work.txt", "work\n")
    run_git(ws.repo, "merge", "-q", "--ff-only", tip)


def test_cleanup_removes_a_merged_clean_worktree_and_its_branch(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    path = ws.path("claude", "topic")
    merge_into_main(ws, path)
    status, results = ws.run(capsys, "cleanup")
    assert (status, single(results)["reason"], results[0]["evidence"]["branch_kept"]) == (0, "OK", False)
    assert not path.exists()
    assert run_git(ws.repo, "branch", "--list", "worktree-claude+topic") == ""


def test_cleanup_keeps_an_unmerged_worktree(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    path = ws.path("claude", "topic")
    commit(path, "work.txt", "work\n")
    status, results = ws.run(capsys, "cleanup")
    assert (status, single(results)["reason"]) == (1, "UNMERGED")
    assert (path / "work.txt").exists()
    assert ws.lock_reason(path) == "claude:topic:s1"


def test_cleanup_keeps_a_dirty_worktree(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    path = ws.path("claude", "topic")
    (path / "scratch.txt").write_text("x", encoding="utf-8")
    status, results = ws.run(capsys, "cleanup")
    assert (status, single(results)["reason"]) == (1, "DIRTY")
    assert (path / "scratch.txt").exists()


def test_cleanup_refuses_a_tree_containing_a_reparse_point(ws: Workspace, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    path = ws.path("claude", "topic")
    merge_into_main(ws, path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    (path / "sub").mkdir()
    make_link(path / "sub" / "link", outside)
    # Ignore the link so the tree is clean and the reparse scan itself refuses.
    (ws.repo / ".git" / "info").mkdir(exist_ok=True)
    (ws.repo / ".git" / "info" / "exclude").write_text("sub/\n", encoding="utf-8")
    status, results = ws.run(capsys, "cleanup")
    assert (status, single(results)["reason"], results[0]["detail"]) == (1, "PATH_UNSAFE", "reparse")
    assert (outside / "keep.txt").exists()
    assert path.exists()


def test_cleanup_relocks_when_removal_fails(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    path = ws.path("claude", "topic")
    merge_into_main(ws, path)
    real_git = hw.git

    def failing_remove(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(args, 1, "", "error: simulated busy directory\n")
        return real_git(repo, *args, readonly=readonly)

    monkeypatch.setattr(hw, "git", failing_remove)
    status, results = ws.run(capsys, "cleanup")
    assert (single(results)["reason"], results[0]["evidence"]["lock_restored"]) == ("PERMISSION_DENIED", True)
    assert ws.lock_reason(path) == "claude:topic:s1"


def test_cleanup_reports_when_the_lock_could_not_be_restored(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    path = ws.path("claude", "topic")
    merge_into_main(ws, path)
    real_git = hw.git

    def failing_remove(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(args, 1, "", "error: simulated busy directory\n")
        return real_git(repo, *args, readonly=readonly)

    monkeypatch.setattr(hw, "git", failing_remove)
    # The restore below reports success but leaves a different lock behind, so the
    # read-back is what decides: an observed mismatch is false, not a success.
    monkeypatch.setattr(hw, "restore_lock", lambda repo, target, reason: True)
    monkeypatch.setattr(hw, "current_lock", lambda repo, target: "other:topic:s9")
    status, results = ws.run(capsys, "cleanup")
    assert (single(results)["reason"], results[0]["evidence"]["lock_restored"]) == ("PERMISSION_DENIED", False)


def test_lock_restored_is_true_from_the_read_back_not_the_restore_command(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    merge_into_main(ws, ws.path("claude", "topic"))
    real_git = hw.git

    def failing_remove(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(args, 1, "", "error: simulated busy directory\n")
        return real_git(repo, *args, readonly=readonly)

    monkeypatch.setattr(hw, "git", failing_remove)
    # The restore command reports failure, yet the lock is in fact still the old one,
    # so the read-back alone must decide that it was restored.
    monkeypatch.setattr(hw, "restore_lock", lambda repo, target, reason: False)
    monkeypatch.setattr(hw, "current_lock", lambda repo, target: "claude:topic:s1")
    status, results = ws.run(capsys, "cleanup")
    assert (single(results)["reason"], results[0]["evidence"]["lock_restored"]) == ("PERMISSION_DENIED", True)


def test_lock_restored_is_null_when_the_read_back_fails(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    path = ws.path("claude", "topic")
    merge_into_main(ws, path)
    real_git = hw.git

    def failing_remove(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(args, 1, "", "error: simulated busy directory\n")
        return real_git(repo, *args, readonly=readonly)

    def unreadable(repo: str, target: str) -> str | None:
        raise hw.Stop(hw.Result("UNSUPPORTED_ENVIRONMENT", repo, "git not found on PATH"))

    monkeypatch.setattr(hw, "git", failing_remove)
    monkeypatch.setattr(hw, "current_lock", unreadable)
    status, results = ws.run(capsys, "cleanup")
    result = single(results)
    # The remove failure stays primary; only the unobservable state is recorded.
    assert (result["reason"], result["detail"]) == ("PERMISSION_DENIED", "remove failed")
    assert result["evidence"]["lock_restored"] is None
    assert result["evidence"]["restoration_observation"] == "UNOBSERVABLE"


def test_lock_restored_is_absent_when_no_restoration_was_attempted_even_if_unobservable(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    real_git = hw.git

    def failing_unlock(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "unlock"):
            return subprocess.CompletedProcess(args, 1, "", "error: simulated\n")
        return real_git(repo, *args, readonly=readonly)

    def unreadable(repo: str, target: str) -> str | None:
        raise hw.Stop(hw.Result("UNSUPPORTED_ENVIRONMENT", repo, "git not found on PATH"))

    monkeypatch.setattr(hw, "git", failing_unlock)
    monkeypatch.setattr(hw, "current_lock", unreadable)
    status, results = ws.run(capsys, "cleanup")
    result = single(results)
    assert result["reason"] == "PERMISSION_DENIED"
    # Nothing was ever restored, so no restoration claim may be invented here.
    assert "lock_restored" not in result["evidence"]
    assert "restoration_observation" not in result["evidence"]


def test_dirty_removal_keeps_its_result_when_the_lock_cannot_be_observed(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    merge_into_main(ws, ws.path("claude", "topic"))
    real_git = hw.git

    def dirty_remove(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(args, 1, "", f"fatal: '{args[2]}' contains modified or untracked files, use --force to delete it\n")
        return real_git(repo, *args, readonly=readonly)
    def unreadable(repo: str, target: str) -> str | None:
        raise hw.Stop(hw.Result("UNSUPPORTED_ENVIRONMENT", repo, "git not found on PATH"))

    monkeypatch.setattr(hw, "git", dirty_remove)
    monkeypatch.setattr(hw, "current_lock", unreadable)
    status, results = ws.run(capsys, "cleanup")
    result = single(results)
    # The unobservable lock must not displace the primary DIRTY refusal.
    assert (result["reason"], result["detail"]) == ("DIRTY", "git refused removal")
    assert result["evidence"]["lock_restored"] is None


def test_lock_restored_is_absent_without_a_restoration_attempt(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    real_git = hw.git

    def failing_unlock(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "unlock"):
            return subprocess.CompletedProcess(args, 1, "", "error: simulated\n")
        return real_git(repo, *args, readonly=readonly)

    monkeypatch.setattr(hw, "git", failing_unlock)
    status, results = ws.run(capsys, "cleanup")
    result = single(results)
    assert (result["reason"], result["evidence"]["lock_reason"]) == ("PERMISSION_DENIED", "claude:topic:s1")
    # No lock was unlocked, so nothing was restored and no state is claimed.
    assert "lock_restored" not in result["evidence"]


def test_filesystem_refusal_is_permission_denied(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def refuse(path: str) -> bool:
        raise PermissionError(13, "Access is denied", path)

    monkeypatch.setattr(hw, "is_reparse", refuse)
    status, results = ws.run(capsys, "create")
    assert (status, single(results)["reason"]) == (2, "PERMISSION_DENIED")


def test_cleanup_keeps_the_branch_when_branch_delete_refuses(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    run_git(ws.repo, "switch", "-q", "-c", "side")
    ws.run(capsys, "create")
    path = ws.path("claude", "topic")
    tip = commit(path, "work.txt", "work\n")
    run_git(ws.repo, "update-ref", "refs/heads/main", tip)
    status, results = ws.run(capsys, "cleanup")
    result = single(results)
    assert (status, result["reason"], result["evidence"]["branch_kept"]) == (0, "OK", True)
    assert not path.exists()


def test_cleanup_by_another_harness_is_a_mismatch(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    merge_into_main(ws, ws.path("claude", "topic"))
    status, results = ws.run(capsys, "cleanup", "codex", "topic")
    assert (status, single(results)["reason"]) == (1, "IDENTITY_MISMATCH")
    assert ws.path("claude", "topic").exists()


def test_cleanup_of_a_legacy_checkout_is_legacy(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    legacy = ws.repo / ".claude" / "worktrees" / "claude+topic"
    run_git(ws.repo, "worktree", "add", "-q", "-b", "worktree-claude+topic", str(legacy), "main")
    status, results = ws.run(capsys, "cleanup")
    assert (status, single(results)["reason"]) == (1, "LEGACY_TARGET")
    assert legacy.exists()


def test_cleanup_requires_the_lock(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    path = ws.path("claude", "topic")
    merge_into_main(ws, path)
    run_git(ws.repo, "worktree", "unlock", str(path))
    status, results = ws.run(capsys, "cleanup")
    assert (status, single(results)["reason"]) == (1, "IDENTITY_AMBIGUOUS")
    assert path.exists()


# Path-safety, probe and restoration evidence


def test_json_keeps_the_verdict_reason_evidence_shape(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status, results = ws.run(capsys, "resume")
    assert set(single(results)) == {"verdict", "reason", "target", "detail", "next", "evidence"}
    assert (results[0]["verdict"], results[0]["reason"]) == ("STOP", "NOT_FOUND")
    status, results = ws.cli(capsys, "audit", "--project", "missing", "--contract", "1")
    assert single(results)["verdict"] == "ERROR"


def test_create_over_an_unlocked_registration_is_ambiguous(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    run_git(ws.repo, "worktree", "unlock", str(ws.path("claude", "topic")))
    status, results = ws.run(capsys, "create")
    assert (status, single(results)["reason"]) == (1, "IDENTITY_AMBIGUOUS")


def test_permission_message_runs_the_probes_before_it_decides(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    real_git = hw.git
    root_probes: list[int] = []

    def refused_add(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "add") and "-h" not in args:
            return subprocess.CompletedProcess(args, 128, "", "fatal: could not create leading directories of 'x': Permission denied\n")
        return real_git(repo, *args, readonly=readonly)

    def root_probe(directory: str) -> bool:
        # The pre-add writability gate calls this first; the post-failure evidence
        # probe is the second call, and that one leaves a probe behind.
        root_probes.append(1)
        if len(root_probes) == 1:
            return True
        raise hw.ProbeLeftBehind(hw.Result("PERMISSION_DENIED", "/stuck-probe", "probe left behind"))

    monkeypatch.setattr(hw, "git", refused_add)
    monkeypatch.setattr(hw, "metadata_writable", lambda repo: True)
    monkeypatch.setattr(hw, "subdir_creatable", root_probe)
    status, results = ws.run(capsys, "create")
    result = single(results)
    assert (status, result["reason"]) == (2, "PERMISSION_DENIED")
    # Git's own permission message already decides the refusal; the evidence probes
    # must still run, so the leftover probe is reported even then.
    assert result["evidence"]["worktrees_root_probe"] == {"result": "leftover", "path": "/stuck-probe"}
    assert result["evidence"]["metadata_probe"] == {"result": "ok"}
    assert len(root_probes) == 2


def test_both_probe_leftovers_are_kept(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    real_git = hw.git
    calls = {"metadata": 0, "root": 0}

    def refused_add(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "add") and "-h" not in args:
            return subprocess.CompletedProcess(args, 128, "", "fatal: could not create leading directories of 'x': Permission denied\n")
        return real_git(repo, *args, readonly=readonly)

    def metadata(repo: str) -> bool:
        # The pre-add gate is the first call; the evidence probe is the second.
        calls["metadata"] += 1
        if calls["metadata"] == 1:
            return True
        raise hw.ProbeLeftBehind(hw.Result("PERMISSION_DENIED", "/stuck-metadata", "probe left behind"))

    def root_probe(directory: str) -> bool:
        calls["root"] += 1
        if calls["root"] == 1:
            return True
        raise hw.ProbeLeftBehind(hw.Result("PERMISSION_DENIED", "/stuck-root", "probe left behind"))

    monkeypatch.setattr(hw, "git", refused_add)
    monkeypatch.setattr(hw, "metadata_writable", metadata)
    monkeypatch.setattr(hw, "subdir_creatable", root_probe)
    status, results = ws.run(capsys, "create")
    result = single(results)
    assert (status, result["reason"]) == (2, "PERMISSION_DENIED")
    # Each probe keeps its own leftover, so the second cannot overwrite the first.
    assert result["evidence"]["metadata_probe"] == {"result": "leftover", "path": "/stuck-metadata"}
    assert result["evidence"]["worktrees_root_probe"] == {"result": "leftover", "path": "/stuck-root"}


def test_other_stop_is_not_reported_as_a_leftover_probe() -> None:
    def missing_git() -> bool:
        raise hw.Stop(hw.Result("UNSUPPORTED_ENVIRONMENT", "/repo", "git not found on PATH"))

    with pytest.raises(hw.Stop) as stop:
        hw.probe_result(missing_git)
    assert stop.value.result.code == "UNSUPPORTED_ENVIRONMENT"


def test_probe_states_distinguish_unwritable_from_ok_and_leftover(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    real_git = hw.git
    calls = {"metadata": 0}

    def refused_add(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "add") and "-h" not in args:
            return subprocess.CompletedProcess(args, 128, "", "fatal: could not create leading directories of 'x': Permission denied\n")
        return real_git(repo, *args, readonly=readonly)

    def metadata(repo: str) -> bool:
        calls["metadata"] += 1
        return calls["metadata"] == 1

    monkeypatch.setattr(hw, "git", refused_add)
    monkeypatch.setattr(hw, "metadata_writable", metadata)
    monkeypatch.setattr(hw, "subdir_creatable", lambda directory: True)
    status, results = ws.run(capsys, "create")
    result = single(results)
    assert (status, result["reason"]) == (2, "PERMISSION_DENIED")
    # A plain refusal is its own state, distinct from ok and from a leftover probe.
    assert result["evidence"]["metadata_probe"] == {"result": "unwritable"}
    assert result["evidence"]["worktrees_root_probe"] == {"result": "ok"}


def test_failed_add_is_reclassified_from_the_new_state(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    real_git = hw.git

    def racing_add(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "add") and "-h" not in args:
            run_git(ws.repo, "branch", "worktree-claude+topic")
            return subprocess.CompletedProcess(args, 128, "", "fatal: a branch named 'worktree-claude+topic' already exists\n")
        return real_git(repo, *args, readonly=readonly)

    monkeypatch.setattr(hw, "git", racing_add)
    status, results = ws.run(capsys, "create")
    assert (status, single(results)["reason"]) == (1, "IDENTITY_MISMATCH")
    assert "already exists" in results[0]["evidence"]["git"]


def test_destination_without_subdirectory_rights_is_permission_denied(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    probed: list[str] = []
    monkeypatch.setattr(hw, "subdir_creatable", lambda d: probed.append(d) or False)
    status, results = ws.run(capsys, "create")
    assert (status, single(results)["reason"]) == (2, "PERMISSION_DENIED")
    assert not ws.worktrees.exists()
    assert hw.key(probed[0]) == hw.key(str(ws.root))


def test_create_leaves_no_directory_behind_when_add_fails(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    real_git = hw.git

    def failing_add(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "add") and "-h" not in args:
            return subprocess.CompletedProcess(args, 128, "", "fatal: simulated\n")
        return real_git(repo, *args, readonly=readonly)

    monkeypatch.setattr(hw, "git", failing_add)
    status, results = ws.run(capsys, "create")
    assert (status, single(results)["reason"]) == (2, "UNSUPPORTED_ENVIRONMENT")
    assert not (ws.root / "worktrees").exists()


def test_probe_that_cannot_be_removed_is_reported(tmp_path: Path) -> None:
    def refuse(path: str) -> None:
        raise PermissionError(13, "Access is denied", path)

    with pytest.raises(hw.Stop) as stop:
        hw.probe(str(tmp_path), os.mkdir, refuse)
    assert stop.value.result.code == "PERMISSION_DENIED"
    assert "probe left behind" in stop.value.result.detail


def test_leftover_probe_after_a_failed_unlock_keeps_the_lock_evidence(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    merge_into_main(ws, ws.path("claude", "topic"))
    real_git, real_remove = hw.git, os.remove
    unlock_failed = []

    def failing_unlock(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "unlock"):
            unlock_failed.append(True)
            return subprocess.CompletedProcess(args, 1, "", "error: simulated\n")
        return real_git(repo, *args, readonly=readonly)

    def stuck_probe(path: str) -> None:
        # The probe file is created for real; only its removal is refused, and only after the failed unlock.
        if unlock_failed and "hami-worktree-probe-" in os.path.basename(path):
            raise PermissionError(13, "Access is denied", path)
        real_remove(path)

    monkeypatch.setattr(hw, "git", failing_unlock)
    monkeypatch.setattr(hw.os, "remove", stuck_probe)
    status, results = ws.run(capsys, "cleanup")
    monkeypatch.undo()
    result = single(results)
    assert (result["reason"], result["target"]) == ("PERMISSION_DENIED", str(ws.path("claude", "topic")))
    assert result["evidence"]["lock_reason"] == "claude:topic:s1"
    probe = result["evidence"]["metadata_probe"]
    assert probe["result"] == "leftover"
    leftover = Path(probe["path"])
    assert leftover.name.startswith("hami-worktree-probe-") and leftover.exists()


def test_git_permission_message_after_failed_add_is_permission_denied(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    real_git = hw.git

    def refused_add(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "add") and "-h" not in args:
            return subprocess.CompletedProcess(args, 128, "", "fatal: could not create leading directories of 'x': Permission denied\n")
        return real_git(repo, *args, readonly=readonly)

    monkeypatch.setattr(hw, "git", refused_add)
    status, results = ws.run(capsys, "create")
    assert (status, single(results)["reason"]) == (2, "PERMISSION_DENIED")


def test_lock_with_an_empty_session_is_a_mismatch(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    path = str(ws.path("claude", "topic"))
    run_git(ws.repo, "worktree", "unlock", path)
    run_git(ws.repo, "worktree", "lock", "--reason", "claude:topic:", path)
    status, results = ws.run(capsys, "audit", None, None)
    assert (status, single(results)["reason"]) == (1, "IDENTITY_MISMATCH")


def test_create_checks_the_resolved_destination(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(hw.os.path, "realpath", lambda p, **_: str(ws.root / "elsewhere"))
    status, results = ws.run(capsys, "create")
    assert (single(results)["reason"], results[0]["detail"]) == ("PATH_UNSAFE", "resolved-mismatch")
    assert not ws.worktrees.exists()


def unreadable_codex_config(ws: Workspace) -> None:
    codex = Path(ws.environ["CODEX_HOME"])
    codex.mkdir()
    (codex / "config.toml").write_text("[desktop\n", encoding="utf-8")


def test_unverified_external_root_is_never_presumed_disjoint(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    unreadable_codex_config(ws)
    status, results = ws.run(capsys, "create")
    assert (status, single(results)["reason"]) == (2, "UNSUPPORTED_ENVIRONMENT")
    assert not ws.worktrees.exists()
    status, results = ws.run(capsys, "audit", None, None)
    assert status == 2
    assert results[0]["reason"] == "UNSUPPORTED_ENVIRONMENT"


def test_audit_flags_a_reparse_worktrees_root_even_when_empty(ws: Workspace, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    ws.worktrees.parent.mkdir(parents=True)
    make_link(ws.worktrees, elsewhere)
    status, results = ws.run(capsys, "audit", None, None)
    assert (status, single(results)["reason"], results[0]["detail"]) == (1, "PATH_UNSAFE", "reparse")


def test_failed_unlock_reports_the_current_lock(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create", "claude", "topic", "--session", "s1")
    merge_into_main(ws, ws.path("claude", "topic"))
    real_git = hw.git

    def failing_unlock(repo: str, *args: str, readonly: bool = False) -> subprocess.CompletedProcess:
        if args[:2] == ("worktree", "unlock"):
            return subprocess.CompletedProcess(args, 1, "", "error: simulated\n")
        return real_git(repo, *args, readonly=readonly)

    monkeypatch.setattr(hw, "git", failing_unlock)
    status, results = ws.run(capsys, "cleanup")
    assert (single(results)["reason"], results[0]["evidence"]["lock_reason"]) == ("PERMISSION_DENIED", "claude:topic:s1")


def test_resolved_destination_must_equal_the_lexical_path(ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws.run(capsys, "create")
    monkeypatch.setattr(hw.os.path, "realpath", lambda p, **_: str(ws.root / "elsewhere"))
    status, results = ws.run(capsys, "resume")
    assert (single(results)["reason"], results[0]["detail"]) == ("PATH_UNSAFE", "resolved-mismatch")


def test_main_checkout_is_never_a_target(ws: Workspace) -> None:
    workspace = hw.load_workspace(ws.root, {})
    project = workspace.projects[PROJECT]
    with pytest.raises(hw.Stop) as stop:
        hw.check_target_path(workspace, project, str(ws.repo), [])
    assert stop.value.result.code == "PATH_UNSAFE"


# Explicit abandonment preserves an exact local archive ref.


def archived_topic(ws, capsys, *, annotated=False):
    ws.run(capsys, "create", "codex", "discard", "--session", "s1")
    path = ws.path("codex", "discard")
    tip = commit(path, "work.txt", "archived work\n")
    tag = "archive/discard"
    if annotated:
        run_git(ws.repo, "tag", "-a", tag, tip, "-m", "retained evidence")
    else:
        run_git(ws.repo, "tag", tag, tip)
    return path, tip, tag


def abandon_args(tip, tag):
    return ("--abandon", "--archive-tag", tag, "--expected-head", tip)


@pytest.mark.parametrize("annotated", [False, True])
def test_abandoned_cleanup_preserves_exact_tag_and_other_worktree(ws, capsys, annotated):
    path, tip, tag = archived_topic(ws, capsys, annotated=annotated)
    ws.run(capsys, "create", "claude", "other", "--session", "other-session")
    other = ws.path("claude", "other")
    status, results = ws.run(capsys, "cleanup", "codex", "discard", *abandon_args(tip, tag))
    result = single(results)
    assert (status, result["reason"], result["evidence"]["branch_kept"]) == (0, "OK", False), result["evidence"].get("git")
    assert result["evidence"]["abandoned"] and result["evidence"]["preserved_head"] == tip
    assert not path.exists() and other.exists()
    assert ws.lock_reason(other) == "claude:other:other-session"
    assert run_git(ws.repo, "rev-parse", f"refs/tags/{tag}^{{commit}}") == tip
    assert run_git(ws.repo, "branch", "--list", "worktree-codex+discard") == ""
    assert hw.git(str(ws.repo), "merge-base", "--is-ancestor", tip, "main", readonly=True).returncode == 1
    assert run_git(ws.repo, "rev-parse", "main") != tip


@pytest.mark.parametrize("fault", ["missing_tag", "wrong_tag", "wrong_head", "dirty", "other_owner", "missing_lock"])
def test_abandoned_cleanup_refuses_without_complete_preservation(ws, capsys, fault):
    path, tip, tag = archived_topic(ws, capsys)
    expected_reason = "ARCHIVE_MISMATCH"
    owner = "codex"
    if fault == "missing_tag":
        tag = "archive/missing"
    elif fault == "wrong_tag":
        run_git(ws.repo, "tag", "-f", tag, "main")
    elif fault == "wrong_head":
        tip = run_git(ws.repo, "rev-parse", "main")
        expected_reason = "HEAD_MISMATCH"
    elif fault == "dirty":
        (path / "uncommitted.txt").write_text("keep", encoding="utf-8")
        expected_reason = "DIRTY"
    elif fault == "other_owner":
        owner = "claude"
        expected_reason = "IDENTITY_MISMATCH"
    elif fault == "missing_lock":
        run_git(ws.repo, "worktree", "unlock", str(path))
        expected_reason = "IDENTITY_AMBIGUOUS"
    status, results = ws.run(capsys, "cleanup", owner, "discard", *abandon_args(tip, tag))
    assert (status, single(results)["reason"]) == (1, expected_reason)
    assert path.exists() and (path / "work.txt").exists()
    assert run_git(ws.repo, "branch", "--list", "worktree-codex+discard")


@pytest.mark.parametrize("extra", [("--abandon",), ("--archive-tag", "archive/example"), ("--expected-head", "a" * 40)])
def test_abandoned_cleanup_requires_all_authority_fields(ws, capsys, extra):
    with pytest.raises(SystemExit) as exc:
        ws.run(capsys, "cleanup", "codex", "discard", *extra)
    assert exc.value.code == 2


@pytest.mark.parametrize("race", ["tag", "branch"])
def test_abandoned_cleanup_keeps_branch_if_retained_refs_change(ws, capsys, monkeypatch, race):
    path, tip, tag = archived_topic(ws, capsys)
    real_git = hw.git
    changed_tip = run_git(ws.repo, "rev-parse", "main")
    def racing_git(repo, *args, readonly=False, input=None):
        result = real_git(repo, *args, readonly=readonly, input=input)
        if args[:2] == ("worktree", "remove") and result.returncode == 0:
            if race == "tag":
                run_git(ws.repo, "tag", "-f", tag, "main")
            else:
                run_git(ws.repo, "update-ref", "refs/heads/worktree-codex+discard", changed_tip)
        return result
    monkeypatch.setattr(hw, "git", racing_git)
    status, results = ws.run(capsys, "cleanup", "codex", "discard", *abandon_args(tip, tag))
    assert status == 0 and single(results)["evidence"]["branch_kept"]
    assert not path.exists()
    assert run_git(ws.repo, "rev-parse", "worktree-codex+discard") == (tip if race == "tag" else changed_tip)


def test_abandoned_cleanup_refuses_reparse_and_restores_lock_on_busy_remove(ws, tmp_path, capsys, monkeypatch):
    path, tip, tag = archived_topic(ws, capsys)
    outside = tmp_path / "outside-archive"; outside.mkdir()
    make_link(path / "ignored-link", outside)
    # A trailing slash would ignore Windows junctions but not POSIX symlinks.
    (ws.repo / ".git" / "info" / "exclude").write_text("ignored-link\n", encoding="utf-8")
    assert run_git(path, "status", "--porcelain") == ""
    status, results = ws.run(capsys, "cleanup", "codex", "discard", *abandon_args(tip, tag))
    assert (status, single(results)["reason"], results[0]["detail"]) == (1, "PATH_UNSAFE", "reparse")
    assert path.exists() and outside.exists()
    if sys.platform == "win32":
        os.rmdir(path / "ignored-link")
    else:
        (path / "ignored-link").unlink()
    real_git = hw.git
    def busy(repo, *args, readonly=False, input=None):
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(args, 1, "", "simulated busy directory")
        return real_git(repo, *args, readonly=readonly, input=input)
    monkeypatch.setattr(hw, "git", busy)
    status, results = ws.run(capsys, "cleanup", "codex", "discard", *abandon_args(tip, tag))
    assert (status, single(results)["reason"]) == (2, "PERMISSION_DENIED")
    assert ws.lock_reason(path) == "codex:discard:s1"
    assert run_git(ws.repo, "rev-parse", f"refs/tags/{tag}^{{commit}}") == tip


@pytest.mark.parametrize("fault", [None, "recreated", "config_failure"])
def test_abandoned_cleanup_handles_upstream_settings(ws, capsys, monkeypatch, fault):
    path, tip, tag = archived_topic(ws, capsys)
    branch = "worktree-codex+discard"
    run_git(ws.repo, "config", f"branch.{branch}.remote", "origin")
    run_git(ws.repo, "config", f"branch.{branch}.merge", "refs/heads/" + branch)
    real_git = hw.git
    def mutate(repo, *args, readonly=False, input=None):
        if fault == "config_failure" and args[:3] == ("config", "--local", "--remove-section"):
            return subprocess.CompletedProcess(args, 1, "", "simulated config lock failure")
        result = real_git(repo, *args, readonly=readonly, input=input)
        if fault == "recreated" and args == ("update-ref", "--stdin") and result.returncode == 0:
            run_git(ws.repo, "update-ref", "refs/heads/" + branch, tip)
        return result
    monkeypatch.setattr(hw, "git", mutate)
    status, results = ws.run(capsys, "cleanup", "codex", "discard", *abandon_args(tip, tag))
    result = single(results)
    assert (status, result["reason"], result["evidence"]["branch_kept"]) == (0, "OK", False)
    assert not path.exists() and run_git(ws.repo, "rev-parse", f"refs/tags/{tag}^{{commit}}") == tip
    settings = run_git(ws.repo, "config", "--local", "--get-regexp", r"^branch\.worktree-codex\+discard\.", check=False)
    if fault is None:
        assert not settings and result["evidence"]["branch_config"] == "removed"
        assert not run_git(ws.repo, "branch", "--list", branch)
    else:
        assert f"branch.{branch}.remote origin" in settings
        assert result["evidence"]["branch_config"] == "kept" and result["evidence"]["warning"]
        assert bool(run_git(ws.repo, "branch", "--list", branch)) == (fault == "recreated")


# A same-tree squash integration is cleaned up against an exact recorded squash commit and a retained ref.

SQUASH_BRANCH = "worktree-codex+sq"


def squashed_topic(ws, capsys):
    ws.run(capsys, "create", "codex", "sq", "--session", "s1")
    path = ws.path("codex", "sq")
    tip = commit(path, "work.txt", "squashed work\n")
    base = run_git(ws.repo, "rev-parse", "main")
    integration = run_git(ws.repo, "commit-tree", f"{tip}^{{tree}}", "-p", base, "-m", "squash")
    run_git(ws.repo, "update-ref", "refs/heads/main", integration)
    run_git(ws.repo, "tag", "retain/sq", tip)
    return path, {"tip": tip, "base": base, "integration": integration, "ref": "refs/tags/retain/sq"}


def squash_args(facts, **override):
    values = {"expected_head": facts["tip"], "reviewed_sha": facts["tip"], "expected_main": facts["base"],
              "integrated_as": facts["integration"], "retained_ref": facts["ref"], **override}
    return ("--squash-integrated", "--expected-head", values["expected_head"], "--reviewed-sha", values["reviewed_sha"],
            "--expected-main", values["expected_main"], "--integrated-as", values["integrated_as"],
            "--retained-ref", values["retained_ref"])


def squash_cleanup(ws, capsys, facts, **override):
    return ws.run(capsys, "cleanup", "codex", "sq", *squash_args(facts, **override))


def assert_kept(ws, path, facts):
    assert path.exists() and (path / "work.txt").exists()
    assert run_git(ws.repo, "rev-parse", SQUASH_BRANCH) == facts["tip"]
    assert ws.lock_reason(path) == "codex:sq:s1"


def test_squash_cleanup_removes_the_worktree_and_keeps_the_retained_ref(ws, capsys):
    path, facts = squashed_topic(ws, capsys)
    ws.run(capsys, "create", "claude", "other", "--session", "other-session")
    status, results = squash_cleanup(ws, capsys, facts)
    result = single(results)
    evidence = result["evidence"]
    assert (status, result["reason"], evidence["branch_kept"]) == (0, "OK", False), evidence.get("git")
    assert (evidence["squash_integrated"], evidence["worktree_removed"], evidence["cleanup_complete"]) == (True, True, True)
    assert evidence["retained_ref"] == facts["ref"] and evidence["preserved_head"] == facts["tip"]
    assert "abandoned" not in evidence
    assert not path.exists() and ws.path("claude", "other").exists()
    assert run_git(ws.repo, "branch", "--list", SQUASH_BRANCH) == ""
    assert run_git(ws.repo, "rev-parse", "retain/sq^{commit}") == facts["tip"]
    assert hw.git(str(ws.repo), "merge-base", "--is-ancestor", facts["tip"], "main", readonly=True).returncode == 1


def test_squash_cleanup_accepts_a_main_that_moved_on_and_a_branch_retainer(ws, capsys):
    path, facts = squashed_topic(ws, capsys)
    later = run_git(ws.repo, "commit-tree", f"{facts['integration']}^{{tree}}", "-p", facts["integration"], "-m", "later")
    run_git(ws.repo, "update-ref", "refs/heads/main", later)
    run_git(ws.repo, "branch", "keeper", facts["tip"])
    status, results = squash_cleanup(ws, capsys, facts, retained_ref="refs/heads/keeper")
    assert (status, single(results)["reason"], results[0]["evidence"]["cleanup_complete"]) == (0, "OK", True)
    assert not path.exists() and run_git(ws.repo, "rev-parse", "keeper") == facts["tip"]


def break_squash(ws, facts, fault):
    """Apply one fault and return the expected reason plus argument overrides."""
    other = "f" * 40
    if fault == "reviewed_differs":
        return "HEAD_MISMATCH", {"reviewed_sha": facts["base"]}
    if fault == "head_differs":
        return "HEAD_MISMATCH", {"expected_head": facts["base"], "reviewed_sha": facts["base"]}
    if fault == "wrong_expected_main":
        return "UNMERGED", {"expected_main": facts["tip"]}
    if fault == "unknown_integration":
        return "UNMERGED", {"integrated_as": other}
    if fault == "merge_commit":
        side = run_git(ws.repo, "commit-tree", f"{facts['base']}^{{tree}}", "-p", facts["base"], "-m", "side")
        merge = run_git(ws.repo, "commit-tree", f"{facts['tip']}^{{tree}}", "-p", facts["base"], "-p", side, "-m", "merge")
        run_git(ws.repo, "update-ref", "refs/heads/main", merge)
        return "UNMERGED", {"integrated_as": merge}
    if fault == "not_on_main":
        run_git(ws.repo, "update-ref", "refs/heads/main", facts["base"])
        return "UNMERGED", {}
    if fault == "tree_differs":
        wrong = run_git(ws.repo, "commit-tree", f"{facts['base']}^{{tree}}", "-p", facts["base"], "-m", "other")
        run_git(ws.repo, "update-ref", "refs/heads/main", wrong)
        return "UNMERGED", {"integrated_as": wrong}
    if fault == "retained_missing":
        return "ARCHIVE_MISMATCH", {"retained_ref": "refs/tags/retain/missing"}
    if fault == "retained_short_name":
        return "ARCHIVE_MISMATCH", {"retained_ref": "retain/sq"}
    if fault == "retained_own_branch":
        return "ARCHIVE_MISMATCH", {"retained_ref": "refs/heads/" + SQUASH_BRANCH}
    if fault == "retained_managed_branch":
        run_git(ws.repo, "branch", "worktree-claude+keeper", facts["tip"])
        return "ARCHIVE_MISMATCH", {"retained_ref": "refs/heads/worktree-claude+keeper"}
    if fault == "retained_remote":
        run_git(ws.repo, "update-ref", "refs/remotes/origin/keeper", facts["tip"])
        return "ARCHIVE_MISMATCH", {"retained_ref": "refs/remotes/origin/keeper"}
    if fault == "retained_not_covering":
        run_git(ws.repo, "tag", "-f", "retain/sq", facts["base"])
        return "ARCHIVE_MISMATCH", {}
    raise AssertionError(fault)


@pytest.mark.parametrize("fault", [
    "reviewed_differs", "head_differs", "wrong_expected_main", "unknown_integration", "merge_commit", "not_on_main",
    "tree_differs", "retained_missing", "retained_short_name", "retained_own_branch", "retained_managed_branch",
    "retained_remote", "retained_not_covering"])
def test_squash_cleanup_refuses_without_the_exact_recorded_integration(ws, capsys, fault):
    path, facts = squashed_topic(ws, capsys)
    expected, override = break_squash(ws, facts, fault)
    status, results = squash_cleanup(ws, capsys, facts, **override)
    assert (status, single(results)["reason"]) == (1, expected)
    assert_kept(ws, path, facts)


def test_squash_cleanup_refuses_a_dirty_worktree_before_checking_the_integration(ws, capsys):
    path, facts = squashed_topic(ws, capsys)
    (path / "uncommitted.txt").write_text("keep", encoding="utf-8")
    status, results = squash_cleanup(ws, capsys, facts, integrated_as="f" * 40)
    assert (status, single(results)["reason"]) == (1, "DIRTY")
    assert results[0]["evidence"]["dirty_paths"] == ["uncommitted.txt"]
    assert_kept(ws, path, facts)


def test_squash_cleanup_of_a_topic_already_in_main_points_to_plain_cleanup(ws, capsys):
    ws.run(capsys, "create", "codex", "sq", "--session", "s1")
    path = ws.path("codex", "sq")
    tip = commit(path, "work.txt", "work\n")
    run_git(ws.repo, "merge", "-q", "--ff-only", tip)
    run_git(ws.repo, "tag", "retain/sq", tip)
    facts = {"tip": tip, "base": tip, "integration": tip, "ref": "refs/tags/retain/sq"}
    status, results = squash_cleanup(ws, capsys, facts)
    assert (status, single(results)["reason"], single(results)["next"]) == (1, "ALREADY_INTEGRATED", hw.NEXT["ALREADY_INTEGRATED"])
    assert_kept(ws, path, facts)


def test_squash_cleanup_keeps_the_identity_checks(ws, capsys):
    path, facts = squashed_topic(ws, capsys)
    status, results = ws.run(capsys, "cleanup", "claude", "sq", *squash_args(facts))
    assert (status, single(results)["reason"]) == (1, "IDENTITY_MISMATCH")
    run_git(ws.repo, "worktree", "unlock", str(path))
    status, results = squash_cleanup(ws, capsys, facts)
    assert (status, single(results)["reason"]) == (1, "IDENTITY_AMBIGUOUS")
    assert path.exists()


def test_squash_cleanup_rechecks_main_ancestry_before_unlocking(ws, capsys, monkeypatch):
    path, facts = squashed_topic(ws, capsys)
    real_git = hw.git
    def rewinding_git(repo, *args, readonly=False, input=None):
        if args[:1] == ("rev-parse",) and "--git-common-dir" in args:
            run_git(ws.repo, "update-ref", "refs/heads/main", facts["base"])
        return real_git(repo, *args, readonly=readonly, input=input)
    monkeypatch.setattr(hw, "git", rewinding_git)
    status, results = squash_cleanup(ws, capsys, facts)
    assert (status, single(results)["reason"]) == (1, "UNMERGED")
    assert_kept(ws, path, facts)


@pytest.mark.parametrize("race", ["retained", "branch"])
def test_squash_cleanup_keeps_the_branch_if_a_ref_changes_after_removal(ws, capsys, monkeypatch, race):
    path, facts = squashed_topic(ws, capsys)
    real_git = hw.git
    def racing_git(repo, *args, readonly=False, input=None):
        result = real_git(repo, *args, readonly=readonly, input=input)
        if args[:2] == ("worktree", "remove") and result.returncode == 0:
            if race == "retained":
                run_git(ws.repo, "tag", "-f", "retain/sq", facts["base"])
            else:
                run_git(ws.repo, "update-ref", "refs/heads/" + SQUASH_BRANCH, facts["base"])
        return result
    monkeypatch.setattr(hw, "git", racing_git)
    status, results = squash_cleanup(ws, capsys, facts)
    evidence = single(results)["evidence"]
    assert (status, single(results)["reason"], evidence["branch_kept"]) == (0, "OK", True)
    assert (evidence["worktree_removed"], evidence["cleanup_complete"]) == (True, False)
    assert not path.exists()
    assert run_git(ws.repo, "rev-parse", SQUASH_BRANCH) == (facts["tip"] if race == "retained" else facts["base"])
    monkeypatch.setattr(hw, "git", real_git)
    status, results = squash_cleanup(ws, capsys, facts)
    assert (status, single(results)["reason"]) == (1, "NOT_FOUND")
    assert results[0]["evidence"]["branch_present"] is True
    assert run_git(ws.repo, "branch", "--list", SQUASH_BRANCH)


def test_cleanup_complete_is_omitted_when_the_branch_state_cannot_be_observed(ws, capsys, monkeypatch):
    path, facts = squashed_topic(ws, capsys)
    real_git = hw.git
    def blind_git(repo, *args, readonly=False, input=None):
        if args[:1] == ("show-ref",) and args[-1] == "refs/heads/" + SQUASH_BRANCH:
            return subprocess.CompletedProcess(args, 2, "", "simulated failure")
        return real_git(repo, *args, readonly=readonly, input=input)
    monkeypatch.setattr(hw, "git", blind_git)
    status, results = squash_cleanup(ws, capsys, facts)
    evidence = single(results)["evidence"]
    assert (status, single(results)["reason"], evidence["worktree_removed"]) == (0, "OK", True)
    assert "cleanup_complete" not in evidence


@pytest.mark.parametrize("extra", [
    ("--squash-integrated",),
    ("--reviewed-sha", "a" * 40),
    ("--retained-ref", "refs/tags/x"),
    ("--abandon", "--archive-tag", "archive/x"),
])
def test_squash_cleanup_options_are_a_mutually_exclusive_mode(ws, capsys, extra):
    base = ("--expected-head", "a" * 40, "--reviewed-sha", "a" * 40, "--expected-main", "b" * 40,
            "--integrated-as", "c" * 40, "--retained-ref", "refs/tags/x")
    argv = (*extra, *base, "--squash-integrated") if extra[0] == "--abandon" else extra
    with pytest.raises(SystemExit) as exc:
        ws.run(capsys, "cleanup", "codex", "sq", *argv)
    assert exc.value.code == 2


def test_plain_cleanup_reports_completion_from_observed_state(ws, capsys):
    ws.run(capsys, "create")
    merge_into_main(ws, ws.path("claude", "topic"))
    status, results = ws.run(capsys, "cleanup")
    evidence = single(results)["evidence"]
    assert (status, evidence["worktree_removed"], evidence["cleanup_complete"]) == (0, True, True)


def test_plain_cleanup_with_a_kept_branch_is_not_complete(ws, capsys):
    run_git(ws.repo, "switch", "-q", "-c", "side")
    ws.run(capsys, "create")
    tip = commit(ws.path("claude", "topic"), "work.txt", "work\n")
    run_git(ws.repo, "update-ref", "refs/heads/main", tip)
    status, results = ws.run(capsys, "cleanup")
    evidence = single(results)["evidence"]
    assert (status, evidence["branch_kept"], evidence["worktree_removed"], evidence["cleanup_complete"]) == (0, True, True, False)

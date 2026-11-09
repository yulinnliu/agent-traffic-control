from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import onboard_project
from integration_slot import IntegrationSlot
from test_tooling import commit_workspace, make_workspace, run_git, write

pytestmark = pytest.mark.integration

COMMIT = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m")


def git_out(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def committed_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """The ATC checkout committed on main and the project as its own repository on main."""
    root, project = make_workspace(tmp_path)
    commit_workspace(root, project)
    return root, project


def records(project: Path) -> list[dict]:
    path = project / ".git" / "atc" / "integrations.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_sync_commits_on_main_and_records_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = committed_workspace(tmp_path)
    before = git_out(project, "rev-parse", "HEAD")
    assert onboard_project.main(["--sync", "demo"], root=root) == 0
    head = git_out(project, "rev-parse", "HEAD")
    revision = git_out(root, "rev-parse", "HEAD")
    assert head != before and git_out(project, "status", "--porcelain") == ""
    assert f"  commit: {head}" in capsys.readouterr().out
    assert git_out(project, "log", "-1", "--format=%s") == f"chore(governance): sync from unknown {revision[:7]}"
    assert (project / ".atc/governance.yaml").read_text(encoding="utf-8").endswith(f"revision: {revision}\n")
    [record] = records(project)
    assert (record["kind"], record["candidate_sha"], record["main_before"], record["source_revision"]) == ("sync", head, before, revision)
    assert onboard_project.main(["--sync", "demo"], root=root) == 0
    assert git_out(project, "rev-parse", "HEAD") == head
    assert "  commit: none (no tracked file changed)" in capsys.readouterr().out
    assert len(records(project)) == 1


@pytest.mark.parametrize("case", ["dirty", "branch", "busy", "behind"])
def test_project_precondition_stops_before_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str) -> None:
    root, project = committed_workspace(tmp_path)
    expected = {"dirty": "CHECKOUT_DIRTY", "branch": "NOT_MAIN", "busy": "INTEGRATION_BUSY", "behind": "BEHIND_ORIGIN"}[case]
    slot = None
    if case == "dirty":
        write(project / "notes.txt", "work in progress\n")
    elif case == "branch":
        run_git(project, "checkout", "-q", "-b", "topic")
    elif case == "busy":
        slot = IntegrationSlot(project)
        assert slot.acquire(blocking=False)
    else:
        run_git(project, *COMMIT, "ahead", "--allow-empty")
        run_git(project, "update-ref", "refs/remotes/origin/main", "HEAD")
        run_git(project, "reset", "-q", "--hard", "HEAD~1")
    head = git_out(project, "rev-parse", "HEAD")
    try:
        assert onboard_project.main(["--sync", "demo"], root=root) == 2
    finally:
        if slot is not None:
            slot.release()
    assert f"  stop: {expected} " in capsys.readouterr().out
    assert git_out(project, "rev-parse", "HEAD") == head
    assert not (project / ".atc/governance.yaml").exists()
    assert records(project) == []


@pytest.mark.parametrize("case", ["topic", "dirty", "adapter"])
def test_atc_checkout_precondition_stops_every_project(tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str) -> None:
    root, project = committed_workspace(tmp_path)
    expected = {"topic": "ROOT_NOT_MAIN", "dirty": "ROOT_DIRTY", "adapter": "ROOT_ADAPTER_DRIFT"}[case]
    if case == "topic":
        run_git(root, "checkout", "-q", "-b", "topic")
    elif case == "dirty":
        write(root / "CLAUDE.md", "Read AGENTS.md first.\n")
    else:
        (root / ".agents/skills/demo-skill/SKILL.md").unlink()
    assert onboard_project.main(["--sync", "--all"], root=root) == 2
    assert f"  stop: {expected} " in capsys.readouterr().out
    assert not (project / ".atc/governance.yaml").exists()
    assert case != "adapter" or not (root / ".agents/skills/demo-skill/SKILL.md").exists()


def test_ignored_synced_files_make_no_commit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = committed_workspace(tmp_path)
    run_git(project, "rm", "-q", "--cached", "AGENTS.md")
    write(project / ".git/info/exclude", "/.atc/\n/.agents/\n/.claude/\n/.dsh/\n/CLAUDE.md\n/AGENTS.md\n")
    run_git(project, *COMMIT, "local-only governance")
    head = git_out(project, "rev-parse", "HEAD")
    assert onboard_project.main(["--sync", "demo"], root=root) == 0
    assert "  commit: none (no tracked file changed)" in capsys.readouterr().out
    assert git_out(project, "rev-parse", "HEAD") == head
    assert (project / ".atc/governance.yaml").exists()
    assert records(project) == []


def test_change_outside_the_synced_files_is_left_uncommitted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, project = committed_workspace(tmp_path)
    apply_plan = onboard_project.apply_plan

    def apply_and_stray(plan, mode, prune=False):
        apply_plan(plan, mode, prune)
        write(project / "stray.txt", "written meanwhile\n")

    monkeypatch.setattr(onboard_project, "apply_plan", apply_and_stray)
    head = git_out(project, "rev-parse", "HEAD")
    assert onboard_project.main(["--sync", "demo"], root=root) == 2
    assert "  stop: UNMANAGED_CHANGE changed outside the synced files, left uncommitted: stray.txt" in capsys.readouterr().out
    assert git_out(project, "rev-parse", "HEAD") == head
    assert records(project) == []


def test_root_only_sync_renders_the_topic_checkout_and_commits_nothing(tmp_path: Path) -> None:
    root, _ = committed_workspace(tmp_path)
    run_git(root, "checkout", "-q", "-b", "topic")
    adapter = root / ".agents/skills/demo-skill/SKILL.md"
    adapter.unlink()
    head = git_out(root, "rev-parse", "HEAD")
    assert onboard_project.main(["--sync", "--root-only"], root=root) == 0
    assert adapter.exists()
    assert git_out(root, "rev-parse", "HEAD") == head

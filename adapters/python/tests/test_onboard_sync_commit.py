from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import git_preflight
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
    assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 0
    head = git_out(project, "rev-parse", "HEAD")
    revision = git_out(root, "rev-parse", "HEAD")
    assert head != before and git_out(project, "status", "--porcelain") == ""
    assert f"  commit: {head}" in capsys.readouterr().out
    assert git_out(project, "log", "-1", "--format=%s") == f"chore(governance): sync from unknown {revision[:7]}"
    assert (project / ".atc/atc.lock").read_text(encoding="utf-8").endswith(f'revision = "{revision}"\n')
    [record] = records(project)
    assert (record["kind"], record["candidate_sha"], record["main_before"], record["source_revision"]) == ("sync", head, before, revision)
    assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 0
    assert git_out(project, "rev-parse", "HEAD") == head
    assert "  commit: none (no tracked file changed)" in capsys.readouterr().out
    assert len(records(project)) == 1


@pytest.mark.parametrize("case", ["at tag", "after tag", "no tag"])
def test_the_sync_subject_names_the_release_tag(tmp_path: Path, case: str) -> None:
    root, project = committed_workspace(tmp_path)
    if case != "no tag":
        subprocess.run(["git", "-C", str(root), "tag", "v0.3.0"], check=True)
        subprocess.run(["git", "-C", str(root), "tag", "notes-1"], check=True)
    if case == "after tag":
        (root / "notes.txt").write_text("n\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "notes.txt"], check=True)
        subprocess.run(["git", "-C", str(root), *COMMIT, "notes"], check=True)
    assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 0
    short = git_out(root, "rev-parse", "HEAD")[:7]
    label, tag, after = {"at tag": (f"v0.3.0 ({short})", "v0.3.0", None),
                         "after tag": (f"{short} (after v0.3.0)", None, "v0.3.0"),
                         "no tag": (short, None, None)}[case]
    assert git_out(project, "log", "-1", "--format=%s") == f"chore(governance): sync from unknown {label}"
    [record] = records(project)
    assert (record["source_tag"], record["source_after"]) == (tag, after)


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
        assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 1
    finally:
        if slot is not None:
            slot.release()
    assert f"  stop: {expected} " in capsys.readouterr().out
    assert git_out(project, "rev-parse", "HEAD") == head
    assert not (project / ".atc/atc.lock").exists()
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
    assert onboard_project.main(["--sync", "--all"], root=root) == 1
    assert f"  stop: {expected} " in capsys.readouterr().out
    assert not (project / ".atc/atc.lock").exists()
    assert case != "adapter" or not (root / ".agents/skills/demo-skill/SKILL.md").exists()


def test_ignored_synced_files_stop_before_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = committed_workspace(tmp_path)
    write(project / ".git/info/exclude", "/.atc/\n/CLAUDE.md\n")
    head = git_out(project, "rev-parse", "HEAD")
    assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 1
    out = capsys.readouterr().out
    assert "  stop: IGNORED_OUTPUT every file sync writes is committed; Git ignores or excludes: .atc/README.md, .atc/atc.lock" in out
    assert "CLAUDE.md" in out.split("IGNORED_OUTPUT")[1]
    assert git_out(project, "rev-parse", "HEAD") == head
    assert not (project / ".atc").exists() and git_out(project, "status", "--porcelain") == ""
    assert records(project) == []


@pytest.mark.integration
def test_a_short_windows_path_still_counts_as_the_top_of_its_checkout(tmp_path: Path) -> None:
    """Git prints the long form; an 8.3 path must not switch off the IGNORED_OUTPUT and tracked checks."""
    ctypes = pytest.importorskip("ctypes")
    if not hasattr(ctypes, "windll"):
        pytest.skip("8.3 short paths exist on Windows only")
    repo = tmp_path / "long name repo"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    buffer = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(repo), buffer, 1024) or buffer.value == str(repo):
        pytest.skip("this volume makes no 8.3 short names")
    validator = onboard_project.validator
    assert validator.git_toplevel(Path(buffer.value)) and validator.git_toplevel(repo)
    (repo / "sub").mkdir()
    assert not validator.git_toplevel(repo / "sub")


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
    assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 1
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


def test_the_integration_slot_is_held_while_sync_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = committed_workspace(tmp_path)
    apply_plan = onboard_project.apply_plan
    held: list[bool] = []

    def apply_while_probing(plan, mode, prune=False):
        probe = IntegrationSlot(project)
        got = probe.acquire(blocking=False)
        if got:
            probe.release()
        held.append(not got)
        apply_plan(plan, mode, prune)

    monkeypatch.setattr(onboard_project, "apply_plan", apply_while_probing)
    assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 0
    assert held == [True]


def test_checkout_dirtied_before_the_slot_is_taken_stops_inside_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, project = committed_workspace(tmp_path)

    class DirtyingSlot(IntegrationSlot):
        def acquire(self, blocking: bool = True) -> bool:
            write(project / "notes.txt", "written between the checks\n")
            return super().acquire(blocking)

    monkeypatch.setattr(onboard_project, "IntegrationSlot", DirtyingSlot)
    head = git_out(project, "rev-parse", "HEAD")
    assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 1
    assert "  stop: CHECKOUT_DIRTY " in capsys.readouterr().out
    assert git_out(project, "rev-parse", "HEAD") == head
    assert not (project / ".atc/atc.lock").exists()


def test_project_that_is_not_its_own_repository_stops(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = make_workspace(tmp_path)
    write(root / ".gitignore", "projects/\n")
    run_git(root, "add", "-A")
    run_git(root, *COMMIT, "release")
    head = git_out(root, "rev-parse", "HEAD")
    assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 1
    assert "  stop: NOT_A_REPOSITORY " in capsys.readouterr().out
    assert git_out(root, "rev-parse", "HEAD") == head
    assert not (project / ".atc/atc.lock").exists()


def test_root_adapter_collision_stops_every_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = committed_workspace(tmp_path)
    write(root / ".agents/skills/demo-skill/SKILL.md", "---\nname: demo-skill\ndescription: local\n---\n\n# Local\n")
    run_git(root, "add", "-A")
    run_git(root, *COMMIT, "unowned adapter")
    assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 1
    assert "  stop: ROOT_ADAPTER_DRIFT " in capsys.readouterr().out
    assert not (project / ".atc/atc.lock").exists()


def test_ownership_stops_at_the_directory_boundary(tmp_path: Path) -> None:
    owner = tmp_path / "skills" / "old-skill"
    assert onboard_project._within(owner, owner)
    assert onboard_project._within(owner / "SKILL.md", owner)
    assert not onboard_project._within(tmp_path / "skills" / "old-skill-2" / "SKILL.md", owner)


def test_push_listing_names_the_sync_source(tmp_path: Path) -> None:
    root, project = committed_workspace(tmp_path)
    run_git(project, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 0
    head = git_out(project, "rev-parse", "HEAD")
    revision = git_out(root, "rev-parse", "HEAD")
    lines = git_preflight.render_push(git_preflight.push_listing(project))
    assert lines[1] == f'  integration {head[:12]} task="{revision[:12]}" authority=sync'
    record = git_preflight.read_integrations(project)[-1]
    assert (record["task"], record["authority"], record["records_only"]) == (revision, {"kind": "sync"}, False)
    assert "authority_reference" not in record


def test_validate_fails_on_an_ignored_synced_file_and_notes_an_uncommitted_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import validate_workspace as validator
    root, project = committed_workspace(tmp_path)
    assert onboard_project.main(["--sync", "--project", "demo"], root=root) == 0
    monkeypatch.setattr(validator, "ROOT", root)
    canonical = onboard_project.canonical_skills(root)
    validator.FAILURES.clear()
    validator.check_tracked(project, "projects.demo", canonical)
    assert validator.FAILURES == []
    run_git(project, "rm", "-q", "--cached", ".atc/atc.lock", ".atc/README.md")
    write(project / ".git/info/exclude", "/.atc/atc.lock\n")
    capsys.readouterr()
    validator.check_tracked(project, "projects.demo", canonical)
    assert validator.FAILURES == ["projects.demo: .atc/atc.lock is written by sync but Git ignores or excludes it; every file sync writes is committed"]
    assert "projects.demo: .atc/README.md is written by sync and not committed yet" in capsys.readouterr().out
    validator.FAILURES.clear()


def short_name(path: Path) -> str | None:
    """The Windows 8.3 form of ``path``; ``None`` where the volume keeps no short names."""
    if os.name != "nt":
        return None
    import ctypes
    buffer = ctypes.create_unicode_buffer(1024)
    size = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    return buffer.value if size and buffer.value.lower() != str(path).lower() else None


def test_a_project_named_by_its_short_path_is_its_checkout(tmp_path: Path) -> None:
    long_project = tmp_path / "project with a long name"
    long_project.mkdir()
    run_git(long_project, "init", "-q", "-b", "main")
    run_git(long_project, *COMMIT, "start", "--allow-empty")
    short = short_name(long_project)
    if short is None:
        pytest.skip("this volume keeps no 8.3 names")
    onboard_project.check_project_checkout(Path(short))

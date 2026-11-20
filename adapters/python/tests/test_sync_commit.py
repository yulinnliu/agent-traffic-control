from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import git_preflight
import sync_project
from integration_slot import IntegrationSlot
from test_tooling import RELEASE, make_workspace, run_git, user_home, write  # noqa: F401  (user_home: autouse)

pytestmark = pytest.mark.integration

COMMIT = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m")


def git_out(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def committed_workspace(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """The ATC checkout committed on main and the project as its own repository on main."""
    return make_workspace(tmp_path, tmp_path_factory, committed=True)


def records(project: Path) -> list[dict]:
    path = project / ".git" / "atc" / "integrations.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_sync_commits_on_main_and_records_it(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    before = git_out(project, "rev-parse", "HEAD")
    assert sync_project.main(["demo"], root=root) == 0
    head = git_out(project, "rev-parse", "HEAD")
    revision = git_out(root, "rev-parse", "HEAD")
    assert head != before and git_out(project, "status", "--porcelain") == ""
    assert f"commit {head[:7]}" in capsys.readouterr().out
    assert git_out(project, "log", "-1", "--format=%s") == f"chore(governance): sync from unknown {RELEASE} ({revision[:7]})"
    assert (project / ".atc/atc.lock").read_text(encoding="utf-8").endswith(f'revision = "{revision}"\n')
    [record] = records(project)
    assert (record["kind"], record["candidate_sha"], record["main_before"], record["source_revision"]) == ("sync", head, before, revision)
    assert record["commits"] == [head]
    assert sync_project.main(["demo"], root=root) == 0
    assert git_out(project, "rev-parse", "HEAD") == head
    assert capsys.readouterr().out == "demo current\n"
    assert len(records(project)) == 1


def commit_root(root: Path, name: str) -> str:
    """A new commit on the ATC checkout's main; its SHA."""
    write(root / f"{name}.txt", f"{name}\n")
    run_git(root, "add", "-A")
    run_git(root, *COMMIT, name)
    return git_out(root, "rev-parse", "HEAD")


@pytest.mark.parametrize("case", ["at tag", "after tag", "no tag"])
def test_the_sync_subject_and_record_name_the_target(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, case: str) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    released = git_out(root, "rev-parse", "HEAD")
    run_git(root, "tag", "notes-1")
    if case == "no tag":
        run_git(root, "tag", "-d", RELEASE)
    head = commit_root(root, "notes") if case == "after tag" else released
    run_git(root, "tag", "v1-test")
    run_git(root, "tag", "v9.0")
    named = [] if case == "at tag" else ["--revision", head]
    assert sync_project.main(["demo", *named], root=root) == 0
    label, tag, after = {"at tag": (f"{RELEASE} ({released[:7]})", RELEASE, None),
                         "after tag": (f"{head[:7]} (after {RELEASE})", None, RELEASE),
                         "no tag": (head[:7], None, None)}[case]
    assert git_out(project, "log", "-1", "--format=%s") == f"chore(governance): sync from unknown {label}"
    [record] = records(project)
    assert (record["source_revision"], record["source_tag"], record["source_after"]) == (head, tag, after)
    assert (project / ".atc/atc.lock").read_text(encoding="utf-8").endswith(f'revision = "{head}"\n')


def test_sync_renders_the_latest_release_not_the_checkout(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    write(root / "shared/governance/CORE.md", "# CORE.md\n\nv0.0.2\n")
    newer = commit_root(root, "second")
    run_git(root, "tag", "v0.0.9", RELEASE)
    run_git(root, "tag", "v0.0.10")
    write(root / "shared/governance/CORE.md", "# CORE.md\n\nunreleased\n")
    unreleased = commit_root(root, "third")
    run_git(root, "tag", "v0.1.0-rc")
    assert sync_project.main(["demo", "--check", "--verbose"], root=root) == 1
    assert capsys.readouterr().out.startswith(f"target v0.0.10 ({newer[:7]})\n")
    assert sync_project.main(["demo"], root=root) == 0
    assert (project / ".atc/governance/CORE.md").read_text(encoding="utf-8") == "# CORE.md\n\nv0.0.2\n"
    assert (project / ".atc/atc.lock").read_text(encoding="utf-8").endswith(f'revision = "{newer}"\n')
    assert git_out(project, "log", "-1", "--format=%s") == f"chore(governance): sync from unknown v0.0.10 ({newer[:7]})"
    assert records(project)[-1]["source_revision"] == newer
    capsys.readouterr()
    assert sync_project.main(["demo", "--revision", unreleased, "--verbose"], root=root) == 0
    assert capsys.readouterr().out.startswith(f"target {unreleased[:7]} (named)\n")
    assert (project / ".atc/governance/CORE.md").read_text(encoding="utf-8") == "# CORE.md\n\nunreleased\n"


def test_sync_targets_the_installed_release(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str],
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed release older than the latest tag: sync renders the installed one, which clears the release-pin
    mismatch, so a project never moves ahead of the tool (docs/WORKFLOW.md § Installing a release)."""
    import release_pin
    from types import SimpleNamespace
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    released = git_out(root, "rev-parse", f"{RELEASE}^{{commit}}")
    write(root / "shared/governance/CORE.md", "# CORE.md\n\nnewer\n")
    commit_root(root, "newer")
    run_git(root, "tag", "v0.0.2")
    release = release_pin.Release(released, RELEASE, "file:///C:/hosts/governance")
    monkeypatch.setattr(sync_project, "installed_release", lambda: release)
    demo = SimpleNamespace(name="demo", repo=str(project))
    assert release_pin.mismatch(demo, release, root).token == "RELEASE_MISMATCH"
    assert sync_project.main(["demo", "--check", "--verbose"], root=root) == 1
    assert capsys.readouterr().out.startswith(f"target {RELEASE} ({released[:7]})\n")
    assert sync_project.main(["demo"], root=root) == 0
    assert (project / ".atc/atc.lock").read_text(encoding="utf-8").endswith(f'revision = "{released}"\n')
    assert (project / ".atc/governance/CORE.md").read_text(encoding="utf-8") == "# CORE.md\n"
    assert release_pin.mismatch(demo, release, root) is None


def test_a_release_that_changes_no_synced_file_moves_the_lock(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory,
                                                              capsys: pytest.CaptureFixture[str],
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The atc 0.10.0 case: the installed release renders every synced file as the previous one did, so only the
    lock moves; `--check` reports it, sync commits it alone and the release pin then matches."""
    import release_pin
    from types import SimpleNamespace
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    monkeypatch.setattr(sync_project, "installed_release", lambda: None)
    assert sync_project.main(["demo"], root=root) == 0
    previous = git_out(root, "rev-parse", f"{RELEASE}^{{commit}}")
    released = commit_root(root, "tool-only")
    run_git(root, "tag", "v0.0.2")
    release = release_pin.Release(released, "v0.0.2", "file:///C:/hosts/governance")
    monkeypatch.setattr(sync_project, "installed_release", lambda: release)
    demo = SimpleNamespace(name="demo", repo=str(project))
    assert release_pin.pin(demo) == previous
    assert release_pin.mismatch(demo, release, root).token == "RELEASE_MISMATCH"
    before = git_out(project, "rev-parse", "HEAD")
    capsys.readouterr()
    assert sync_project.main(["demo", "--check", "--verbose"], root=root) == 1
    out = capsys.readouterr().out
    assert "demo ~1\n  ~ .atc/atc.lock\n" in out
    assert git_out(project, "rev-parse", "HEAD") == before
    assert sync_project.main(["demo"], root=root) == 0
    head = git_out(project, "rev-parse", "HEAD")
    assert git_out(project, "diff", "--name-only", before, head) == ".atc/atc.lock"
    assert git_out(project, "log", "-1", "--format=%s") == f"chore(governance): sync from unknown v0.0.2 ({released[:7]})"
    assert release_pin.mismatch(demo, release, root) is None
    assert sync_project.main(["demo", "--check"], root=root) == 0
    assert capsys.readouterr().out.startswith(f"demo ~1 commit {head[:7]}\ndemo current\n")


def test_the_checkout_as_it_stands_moves_the_lock_only_with_a_file(tmp_path: Path,
                                                                   tmp_path_factory: pytest.TempPathFactory) -> None:
    """doctor compares a project with the checkout's HEAD, which runs ahead of the release the lock pins: a lock that
    differs from it with every file current is no drift."""
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    assert sync_project.main(["demo"], root=root) == 0
    commit_root(root, "tool-only")
    [lock] = [item for item in sync_project.build_plan(project, root) if item.path == project / ".atc/atc.lock"]
    assert (lock.action, lock.detail) == ("skip", "ATC lock is current")


def test_the_installed_release_is_the_one_atc_records(monkeypatch: pytest.MonkeyPatch) -> None:
    import release_pin
    monkeypatch.setattr(release_pin, "direct_url", lambda: {
        "url": "file:///C:/hosts/governance", "vcs_info": {"vcs": "git", "commit_id": "c" * 40,
                                                          "requested_revision": "v0.0.2"}})
    assert sync_project.installed_release() == release_pin.Release("c" * 40, "v0.0.2", "file:///C:/hosts/governance")
    monkeypatch.setattr(release_pin, "direct_url", lambda: {"url": "file:///C:/w", "dir_info": {"editable": True}})
    assert sync_project.installed_release() is None


def test_sync_with_no_installed_release_targets_the_latest_tag(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str],
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    write(root / "shared/governance/CORE.md", "# CORE.md\n\nnewer\n")
    newer = commit_root(root, "newer")
    run_git(root, "tag", "v0.0.2")
    monkeypatch.setattr(sync_project, "installed_release", lambda: None)
    assert sync_project.main(["demo"], root=root) == 0
    assert (project / ".atc/atc.lock").read_text(encoding="utf-8").endswith(f'revision = "{newer}"\n')
    assert "v0.0.2" in git_out(project, "log", "-1", "--format=%s")


def test_an_installed_release_off_main_stops_before_any_project(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str],
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    import release_pin
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    run_git(root, "checkout", "-q", "-b", "topic")
    side = commit_root(root, "topic")
    run_git(root, "checkout", "-q", "main")
    monkeypatch.setattr(sync_project, "installed_release",
                        lambda: release_pin.Release(side, "v0.0.2", "file:///C:/hosts/governance"))
    assert sync_project.main(["demo"], root=root) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"STOP RELEASE_NOT_ON_MAIN: the installed release v0.0.2 ({side[:12]}) is not in the history")
    assert "hint: the Owner installs a release tagged on main with uv tool install" in err
    assert not (project / ".atc").exists()


def test_a_project_ahead_of_the_latest_release_is_left_alone(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    write(root / "shared/governance/CORE.md", "# CORE.md\n\nahead\n")
    ahead = commit_root(root, "ahead")
    assert sync_project.main(["demo", "--revision", ahead], root=root) == 0
    head = git_out(project, "rev-parse", "HEAD")
    capsys.readouterr()
    for mode in (["--check"], []):
        assert sync_project.main(["demo", *mode], root=root) == 0
        assert f"locked at {ahead[:7]}, ahead of {RELEASE}; sync never moves a project back without --revision" \
            in capsys.readouterr().out
    assert git_out(project, "rev-parse", "HEAD") == head
    assert (project / ".atc/governance/CORE.md").read_text(encoding="utf-8") == "# CORE.md\n\nahead\n"
    released = git_out(root, "rev-parse", f"{RELEASE}^{{commit}}")
    assert sync_project.main(["demo", "--revision", released], root=root) == 0
    assert (project / ".atc/governance/CORE.md").read_text(encoding="utf-8") == "# CORE.md\n"
    assert (project / ".atc/atc.lock").read_text(encoding="utf-8").endswith(f'revision = "{released}"\n')


@pytest.mark.parametrize("case", ["no release", "off main", "unknown"])
def test_an_unresolvable_target_stops_before_any_project(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str], case: str) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    named: list[str] = []
    if case == "no release":
        run_git(root, "tag", "-d", RELEASE)
        run_git(root, "tag", "v1.0")
        expected = "NO_RELEASE_TAG"
    elif case == "off main":
        run_git(root, "checkout", "-q", "-b", "topic")
        named = ["--revision", commit_root(root, "topic")]
        run_git(root, "checkout", "-q", "main")
        expected = "REVISION_NOT_ON_MAIN"
    elif case == "unknown":
        named = ["--revision", "0" * 40]
        expected = "REVISION_NOT_ON_MAIN"
    else:
        named = ["--revision", git_out(root, "rev-parse", RELEASE)]
        expected = "REVISION_NOT_ON_MAIN"
    assert sync_project.main(["demo", *named], root=root) == 1
    captured = capsys.readouterr()
    assert f"STOP {expected}:" in captured.err and "===" not in captured.out
    assert not (project / ".atc").exists()


def test_the_verification_gate_applies_to_every_target(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> None:
    """Every revision sync renders carries a manifest and the verifier rule, so the gate is unconditional."""
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    write(project / "AGENTS.md", "# AGENTS.md\n\n## What this is\n\nDemo.\n")
    run_git(project, "commit", "-q", "-am", "no verification section")
    assert sync_project.main(["demo"], root=root) == 1
    assert "STOP VERIFICATION_SECTION_MISSING:" in capsys.readouterr().out
    assert not (project / ".atc").exists()


def test_json_names_the_target(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> None:
    root, _ = committed_workspace(tmp_path, tmp_path_factory)
    assert sync_project.main(["demo", "--check", "--json"], root=root) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["target"] == {"revision": git_out(root, "rev-parse", f"{RELEASE}^{{commit}}"), "tag": RELEASE, "named": False}
    run_git(root, "tag", "-d", RELEASE)
    assert sync_project.main(["demo", "--check", "--json"], root=root) == 1
    assert json.loads(capsys.readouterr().out)["target"]["stops"][0]["reason"] == "NO_RELEASE_TAG"


@pytest.mark.parametrize("argv", [["--revision", "not-a-sha"], ["--root", "--revision", "0" * 40],
                                  ["--root", "demo"]])
def test_revision_takes_a_full_sha_and_root_takes_neither_revision_nor_project(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, argv: list[str]) -> None:
    root, _ = committed_workspace(tmp_path, tmp_path_factory)
    target = [] if "--root" in argv else ["demo"]
    with pytest.raises(SystemExit) as exit_:
        sync_project.main(["--check", *target, *argv], root=root)
    assert exit_.value.code == 2


def test_the_v0_4_0_release_tree_has_no_manifest_so_it_is_not_a_sync_target(tmp_path: Path) -> None:
    """D5 (docs/design/ATC-0049.md § 4): a revision before the manifest is refused, never rendered."""
    import manifest
    import validate_workspace as validator
    source = sync_project.ROOT
    if subprocess.run(["git", "-C", str(source), "rev-parse", "-q", "--verify", "refs/tags/v0.4.0^{commit}"],
                      capture_output=True).returncode:
        pytest.skip("this checkout has no v0.4.0 tag")
    tree = tmp_path / "v0.4.0"
    validator.export_revision(source, git_out(source, "rev-parse", "v0.4.0^{commit}"), tree)
    with pytest.raises(manifest.ManifestError) as refused:
        manifest.load(tree)
    assert refused.value.token == "NO_MANIFEST"


@pytest.mark.parametrize("case", ["dirty", "branch", "busy", "behind"])
def test_project_precondition_stops_before_writing(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str], case: str) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
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
        assert sync_project.main(["demo"], root=root) == 1
    finally:
        if slot is not None:
            slot.release()
    assert f"STOP {expected}:" in capsys.readouterr().out
    assert git_out(project, "rev-parse", "HEAD") == head
    assert not (project / ".atc/atc.lock").exists()
    assert records(project) == []


@pytest.mark.parametrize("case", ["topic", "dirty", "adapter"])
def test_atc_checkout_precondition_stops_every_project(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str], case: str) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    expected = {"topic": "ROOT_NOT_MAIN", "dirty": "ROOT_DIRTY", "adapter": "ROOT_ADAPTER_DRIFT"}[case]
    if case == "topic":
        run_git(root, "checkout", "-q", "-b", "topic")
    elif case == "dirty":
        write(root / "CLAUDE.md", "Read AGENTS.md first.\n")
    else:
        (root / ".agents/skills/demo-skill/SKILL.md").unlink()
    assert sync_project.main([], root=root) == 1
    assert f"STOP {expected}:" in capsys.readouterr().out
    assert not (project / ".atc/atc.lock").exists()
    assert case != "adapter" or not (root / ".agents/skills/demo-skill/SKILL.md").exists()


def test_ignored_synced_files_stop_before_writing(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    write(project / ".git/info/exclude", "/.atc/\n/CLAUDE.md\n")
    head = git_out(project, "rev-parse", "HEAD")
    assert sync_project.main(["demo"], root=root) == 1
    out = capsys.readouterr().out
    assert "STOP IGNORED_OUTPUT: Git ignores or excludes: .atc/README.md, .atc/atc.lock" in out
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
    validator = sync_project.validator
    assert validator.git_toplevel(Path(buffer.value)) and validator.git_toplevel(repo)
    (repo / "sub").mkdir()
    assert not validator.git_toplevel(repo / "sub")


def test_change_outside_the_synced_files_is_left_uncommitted(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    apply_plan = sync_project.apply_plan

    def apply_and_stray(plan, mode):
        apply_plan(plan, mode)
        write(project / "stray.txt", "written meanwhile\n")

    monkeypatch.setattr(sync_project, "apply_plan", apply_and_stray)
    head = git_out(project, "rev-parse", "HEAD")
    assert sync_project.main(["demo"], root=root) == 1
    assert "STOP UNMANAGED_CHANGE: changed outside the synced files, left uncommitted: stray.txt" in capsys.readouterr().out
    assert git_out(project, "rev-parse", "HEAD") == head
    assert records(project) == []


def test_sync_root_renders_the_topic_checkout_and_commits_nothing(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    root, _ = committed_workspace(tmp_path, tmp_path_factory)
    run_git(root, "checkout", "-q", "-b", "topic")
    adapter = root / ".agents/skills/demo-skill/SKILL.md"
    adapter.unlink()
    head = git_out(root, "rev-parse", "HEAD")
    assert sync_project.root_main(["--root"], root=root) == 0
    assert adapter.exists()
    assert git_out(root, "rev-parse", "HEAD") == head


def test_the_integration_slot_is_held_while_sync_writes(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    apply_plan = sync_project.apply_plan
    held: list[bool] = []

    def apply_while_probing(plan, mode):
        probe = IntegrationSlot(project)
        got = probe.acquire(blocking=False)
        if got:
            probe.release()
        held.append(not got)
        apply_plan(plan, mode)

    monkeypatch.setattr(sync_project, "apply_plan", apply_while_probing)
    assert sync_project.main(["demo"], root=root) == 0
    assert held == [True]


def test_checkout_dirtied_before_the_slot_is_taken_stops_inside_it(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)

    class DirtyingSlot(IntegrationSlot):
        def acquire(self, blocking: bool = True) -> bool:
            write(project / "notes.txt", "written between the checks\n")
            return super().acquire(blocking)

    monkeypatch.setattr(sync_project, "IntegrationSlot", DirtyingSlot)
    head = git_out(project, "rev-parse", "HEAD")
    assert sync_project.main(["demo"], root=root) == 1
    assert "STOP CHECKOUT_DIRTY:" in capsys.readouterr().out
    assert git_out(project, "rev-parse", "HEAD") == head
    assert not (project / ".atc/atc.lock").exists()


def test_project_that_is_not_its_own_repository_stops(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = make_workspace(tmp_path, tmp_path_factory)
    write(root / ".gitignore", "projects/\n")
    run_git(root, "add", "-A")
    run_git(root, *COMMIT, "release")
    run_git(root, "tag", RELEASE)
    head = git_out(root, "rev-parse", "HEAD")
    assert sync_project.main(["demo"], root=root) == 1
    assert "STOP NOT_A_REPOSITORY:" in capsys.readouterr().out
    assert git_out(root, "rev-parse", "HEAD") == head
    assert not (project / ".atc/atc.lock").exists()


def test_root_adapter_collision_stops_every_project(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    write(root / ".agents/skills/demo-skill/SKILL.md", "---\nname: demo-skill\ndescription: local\n---\n\n# Local\n")
    run_git(root, "add", "-A")
    run_git(root, *COMMIT, "unowned adapter")
    assert sync_project.main(["demo"], root=root) == 1
    assert "STOP ROOT_ADAPTER_DRIFT:" in capsys.readouterr().out
    assert not (project / ".atc/atc.lock").exists()


def test_ownership_stops_at_the_directory_boundary(tmp_path: Path) -> None:
    owner = tmp_path / "skills" / "old-skill"
    assert sync_project._within(owner, owner)
    assert sync_project._within(owner / "SKILL.md", owner)
    assert not sync_project._within(tmp_path / "skills" / "old-skill-2" / "SKILL.md", owner)


def test_push_listing_names_the_sync_source(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    run_git(project, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert sync_project.main(["demo"], root=root) == 0
    head = git_out(project, "rev-parse", "HEAD")
    revision = git_out(root, "rev-parse", "HEAD")
    lines = git_preflight.render_push(git_preflight.push_listing(project))
    assert lines[1] == f'  integration {head[:12]} task="{revision[:12]}" authority=sync'
    record = git_preflight.read_integrations(project)[-1]
    assert (record["task"], record["authority"], record["records_only"]) == (revision, {"kind": "sync"}, False)
    assert "authority_reference" not in record


def test_validate_fails_on_an_ignored_synced_file_and_notes_an_uncommitted_one(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import validate_workspace as validator
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    assert sync_project.main(["demo"], root=root) == 0
    monkeypatch.setattr(validator, "ROOT", root)
    canonical = sync_project.canonical_skills(root)
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


def test_validate_compares_a_project_with_its_locked_release(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_tooling import check_registered_projects
    root, project = committed_workspace(tmp_path, tmp_path_factory)
    assert sync_project.main(["demo"], root=root) == 0
    write(root / "shared/governance/CORE.md", "# CORE.md\n\nnewer\n")
    write(root / "shared/skills/demo-skill/SKILL.md",
          "---\nname: demo-skill\ndescription: test skill\nadoption: baseline\n---\n\n# Demo, newer\n")
    write(root / "shared/skills/new-skill/SKILL.md", "---\nname: new-skill\ndescription: later\nadoption: baseline\n---\n\n# New\n")
    sync_project.apply_plan(sync_project.root_adapter_plan(root), "sync")
    commit_root(root, "unreleased")
    assert check_registered_projects(root, monkeypatch) == []
    write(project / ".atc/governance/CORE.md", "# CORE.md\n\nnewer\n")
    write(project / ".claude/skills/demo-skill/SKILL.md", (root / ".claude/skills/demo-skill/SKILL.md").read_text(encoding="utf-8"))
    failures = check_registered_projects(root, monkeypatch)
    assert "projects.demo: .atc/governance/CORE.md differs from shared/governance/CORE.md — run sync" in failures
    assert any(".claude" in failure and "demo-skill" in failure and "drifted" in failure for failure in failures)
    assert not any("new-skill" in failure for failure in failures)


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
    sync_project.check_project_checkout(Path(short))

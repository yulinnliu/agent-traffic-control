"""The manifest, the content repository and the D5 refusal (docs/design/ATC-0049.md § 2.2, § 3, § 4)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import content
import manifest as mf
import sync_project
import validate_workspace as validator
from test_tooling import MANIFEST, RELEASE, commit_workspace, make_workspace, run_git, user_home, write  # noqa: F401

COMMIT = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m")


def git_out(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.mark.unit
def test_the_repository_manifest_names_what_sync_delivers() -> None:
    found = mf.load(sync_project.ROOT)
    assert found.governance_modules()[0] == "INDEX.md"
    assert found.skills_source() == "shared/skills"
    assert found.files(mf.CREATE_ONLY) == [("CLAUDE.md", "CLAUDE.md")]
    assert [entry.id for entry in found.entries("workspace")] == ["workspace-policy", "workspace-skills"]


@pytest.mark.unit
@pytest.mark.parametrize("text, detail", [
    ("manifest: 2\nmin_atc: 0.7.0\nproject: []\n", "manifest must be 1"),
    ("manifest: 1\nmin_atc: seven\nproject: []\n", "min_atc must be MAJOR.MINOR.PATCH"),
    ("manifest: 1\nmin_atc: 0.7.0\nproject: []\nextra: 1\n", "unknown keys extra"),
    (MANIFEST.replace("mode: owned}", "mode: rewrite}", 1), "mode must be one of"),
    (MANIFEST.replace("source: CLAUDE.md", "source: ../CLAUDE.md"), "without '..'"),
    (MANIFEST.replace("target: .atc/governance/", "target: .atc/governance"), "must end with '/'"),
    (MANIFEST.replace("  - {id: pointer, source: shared/templates/PROJECT_AGENTS.md, target: AGENTS.md, "
                      "mode: pointer-line}\n", ""), "exactly one pointer-line item"),
    (MANIFEST.replace('"{harness}/skills/"', "skills/"), "target must be '{harness}/skills/'"),
    (MANIFEST + "workspace:\n  - {id: p, source: a.md, target: AGENTS.md, mode: pointer-line}\n",
     "workspace holds no pointer-line item"),
])
def test_a_manifest_that_breaks_the_format_is_invalid(text: str, detail: str) -> None:
    with pytest.raises(mf.ManifestError) as refused:
        mf.parse(text)
    assert refused.value.token == "MANIFEST_INVALID" and detail in refused.value.detail


@pytest.mark.unit
def test_load_refuses_a_tree_without_a_manifest_or_with_a_missing_source(tmp_path: Path) -> None:
    with pytest.raises(mf.ManifestError) as refused:
        mf.load(tmp_path)
    assert refused.value.token == "NO_MANIFEST"
    root, _ = make_workspace(tmp_path)
    (root / "shared/governance/CORE.md").unlink()
    with pytest.raises(mf.ManifestError) as refused:
        mf.load(root)
    assert refused.value.token == "MANIFEST_SOURCE_MISSING" and "shared/governance/CORE.md" in refused.value.detail


@pytest.mark.unit
def test_min_atc_refuses_an_older_atc() -> None:
    found = mf.parse(MANIFEST.replace("min_atc: 0.0.1", "min_atc: 0.7.0"))
    mf.check_version(found, "0.7.0")
    mf.check_version(found, "unknown")
    with pytest.raises(mf.ManifestError) as refused:
        mf.check_version(found, "0.6.9")
    assert refused.value.token == "MANIFEST_TOO_NEW"


@pytest.mark.unit
def test_a_manifest_moves_what_sync_delivers(tmp_path: Path) -> None:
    """The sources and targets are the manifest's, not the tool's."""
    root, project = make_workspace(tmp_path)
    write(root / "docs/BOOT.md", "moved bootloader\n")
    write(root / "atc-manifest.yaml", MANIFEST.replace("source: shared/templates/ATC_README.md", "source: docs/BOOT.md")
          .replace("source: CLAUDE.md, target: CLAUDE.md", "source: CLAUDE.md, target: GUIDE.md"))
    sync_project.apply_plan(sync_project.build_plan(project, root), "sync")
    assert (project / ".atc/README.md").read_text(encoding="utf-8") == "moved bootloader\n"
    assert (project / "GUIDE.md").is_file() and not (project / "CLAUDE.md").exists()


@pytest.mark.unit
def test_governance_checkout_resolves_against_the_workspace_directory(tmp_path: Path) -> None:
    write(tmp_path / "workspace.yaml", "version: 1\ngovernance_checkout: projects/atc\n")
    assert content.governance_checkout(tmp_path) == (tmp_path / "projects/atc").resolve()
    assert not content.is_workspace_checkout(tmp_path)
    write(tmp_path / "workspace.yaml", "version: 1\ngovernance_checkout: .\n")
    assert content.is_workspace_checkout(tmp_path)
    write(tmp_path / "workspace.yaml", "version: 1\n")
    assert content.governance_checkout(tmp_path) is None


@pytest.mark.unit
@pytest.mark.parametrize("value", ["/abs/atc", "''", "[a]"])
def test_workspace_yaml_rejects_a_governance_checkout_that_is_not_a_relative_path(tmp_path: Path, value: str) -> None:
    write(tmp_path / "workspace.yaml", f"version: 1\nworkspace_root: {tmp_path.as_posix()}\n"
                                       f"governance_checkout: {value}\nprojects: {{}}\n")
    validator.FAILURES.clear()
    validator.load_and_check_workspace_yaml(tmp_path / "workspace.yaml")
    assert any("'governance_checkout' must be a path relative" in failure for failure in validator.FAILURES)
    validator.FAILURES.clear()


@pytest.mark.integration
def test_the_content_repository_is_the_checkout_or_the_cache(tmp_path: Path) -> None:
    write(tmp_path / "w/workspace.yaml", "version: 1\n")
    with pytest.raises(content.ContentError) as refused:
        content.repository(tmp_path / "w")
    assert refused.value.token == "CONTENT_SOURCE_UNKNOWN"
    write(tmp_path / "w/workspace.yaml", "version: 1\ngovernance_checkout: ../nowhere\n")
    with pytest.raises(content.ContentError) as refused:
        content.repository(tmp_path / "w")
    assert refused.value.token == "GOVERNANCE_CHECKOUT_INVALID"
    write(tmp_path / "w/workspace.yaml", "version: 1\ngovernance_source: owner/repo\n")
    with pytest.raises(content.ContentError) as refused:
        content.repository(tmp_path / "w")
    assert refused.value.token == "CONTENT_FETCH_FAILED"


def remote_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """A released ATC repository as the remote of ``governance_source``, and a workspace directory that is not a
    checkout of it: ``(workspace, project, remote)``."""
    remote, project = make_workspace(tmp_path)
    commit_workspace(remote, project)
    workspace = tmp_path / "plain"
    shutil_project = workspace / "projects" / "demo"
    shutil_project.parent.mkdir(parents=True)
    project.rename(shutil_project)
    write(workspace / "workspace.yaml", f"version: 1\ngovernance_source: owner/atc\nworkspace_root: "
                                        f"{workspace.as_posix()}\nprojects:\n  demo:\n    repo: projects/demo\n"
                                        "    worktrees: worktrees/demo\n    state: state/demo-data\n")
    monkeypatch.setattr(content, "remote_url", lambda name: str(remote))
    return workspace, shutil_project, remote


@pytest.mark.integration
def test_a_workspace_without_a_checkout_syncs_from_the_cache_and_gets_its_workspace_items(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    workspace, project, remote = remote_workspace(tmp_path, monkeypatch)
    write(remote / "policies/hard-deny.yaml", "version: 1\nrules: []\n")
    write(remote / "atc-manifest.yaml", MANIFEST + "workspace:\n"
          "  - {id: workspace-policy, source: policies/hard-deny.yaml, target: policies/hard-deny.yaml, mode: create-only}\n"
          '  - {id: workspace-skills, source: shared/skills/, target: "{harness}/skills/", mode: skill-render}\n')
    run_git(remote, "add", "-A")
    run_git(remote, *COMMIT, "workspace items")
    run_git(remote, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "tag", "-a", "-m", "v0.0.2", "v0.0.2")
    assert sync_project.main(["demo"], root=workspace) == 0
    cache = content.cache_dir("owner/atc")
    assert (cache / "HEAD").is_file()
    assert validator.read_lock(project)["revision"] == git_out(remote, "rev-parse", "HEAD")
    assert (workspace / "policies/hard-deny.yaml").read_text(encoding="utf-8") == "version: 1\nrules: []\n"
    assert (workspace / ".claude/skills/demo-skill/SKILL.md").is_file()
    capsys.readouterr()
    assert sync_project.main(["demo", "--check"], root=workspace) == 0, capsys.readouterr()


@pytest.mark.integration
def test_a_revision_without_a_manifest_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """D5: sync names the refusal and writes nothing."""
    root, project = make_workspace(tmp_path)
    commit_workspace(root, project)
    run_git(root, "rm", "-q", "atc-manifest.yaml")
    run_git(root, *COMMIT, "no manifest")
    bare = git_out(root, "rev-parse", "HEAD")
    run_git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "tag", "-a", "-m", "v0.0.3", "v0.0.3")
    for argv in (["demo"], ["demo", "--revision", bare], ["demo", "--check"]):
        assert sync_project.main(argv, root=root) == 1
        assert "STOP NO_MANIFEST:" in capsys.readouterr().err
    assert not (project / ".atc").exists()
    assert sync_project.main(["demo", "--revision", git_out(root, "rev-parse", RELEASE + "^{commit}")], root=root) == 0


@pytest.mark.integration
def test_the_root_adapter_check_renders_from_mains_git_objects_not_the_working_tree(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Doctor and sync share one check: actual copies from the working tree, expected ones from `main`'s tip."""
    import doctor
    root, project = make_workspace(tmp_path)
    commit_workspace(root, project)
    plan, checkout = sync_project.workspace_adapter_plan(root)
    assert checkout and not sync_project.changes(plan)
    assert doctor.root_adapter_check(root).status == "READY"
    skill = root / "shared/skills/demo-skill/SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8").replace("test skill", "edited skill"), encoding="utf-8")
    assert doctor.root_adapter_check(root).status == "READY", "an uncommitted canonical edit is not the expectation"
    sync_project.apply_plan(sync_project.root_adapter_plan(root), "sync")
    check = doctor.root_adapter_check(root)
    assert check.status == "ERROR" and "differ from the tip of main" in check.detail
    assert sync_project.main(["demo"], root=root) == 1
    assert "STOP ROOT_ADAPTER_DRIFT:" in capsys.readouterr().out
    run_git(root, "add", "-A")
    run_git(root, *COMMIT, "skill and its copies")
    assert doctor.root_adapter_check(root).status == "READY"


@pytest.mark.integration
def test_doctor_warns_when_a_governance_checkout_does_not_say_so(tmp_path: Path) -> None:
    import doctor
    root, _ = make_workspace(tmp_path)
    assert doctor.governance_checkout_hint(root) is None
    path = root / "workspace.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("governance_checkout: .\n", ""), encoding="utf-8")
    hint = doctor.governance_checkout_hint(root)
    assert hint is not None and hint.status == "WARN" and "governance_checkout: ." in hint.detail


@pytest.mark.integration
def test_a_lock_before_the_manifest_is_a_problem_until_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    (root / "atc-manifest.yaml").rename(tmp_path / "held.yaml")
    write(root / ".gitignore", "projects/\n")
    run_git(root, "add", "-A")
    run_git(root, *COMMIT, "before the manifest")
    old = git_out(root, "rev-parse", "HEAD")
    write(project / ".atc/atc.lock", validator.render_lock("unknown", old))
    (tmp_path / "held.yaml").rename(root / "atc-manifest.yaml")
    run_git(root, "add", "-A")
    run_git(root, *COMMIT, "the manifest")
    found = validator.project_problems(root, ["demo"])
    assert any("revision predates atc-manifest.yaml" in problem for problem in found)

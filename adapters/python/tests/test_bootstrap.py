from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import shutil

import bootstrap
import sync_project
import validate_workspace as validator
from test_tooling import VERIFICATION_SECTION, make_workspace, release_workspace, run_git, write


def test_init_writes_map_and_directories(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert bootstrap.init(root, "owner/repo", force=False, environ={}) == 0
    data = yaml.safe_load((root / "workspace.yaml").read_text(encoding="utf-8"))
    assert data == {"version": 1, "governance_source": "owner/repo", "workspace_root": root.as_posix(), "projects": {}}
    for name in ("projects", "worktrees", "state"):
        assert (root / name).is_dir()
    assert capsys.readouterr().out.startswith("INITIALIZED workspace.yaml (governance_source owner/repo)")


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert bootstrap.init(root, "owner/repo", force=False, environ={}) == 0
    assert bootstrap.init(root, "owner/other", force=False, environ={}) == 1
    assert bootstrap.init(root, "owner/other", force=True, environ={}) == 0
    assert "owner/other" in (root / "workspace.yaml").read_text(encoding="utf-8")


def test_init_without_remote_needs_explicit_source(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert bootstrap.init(root, None, force=False, environ={}) == 2
    assert not (root / "workspace.yaml").exists()


def test_add_registers_onboards_and_validates(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path, create_project=False)
    shutil.copyfile(bootstrap.ROOT / "workspace.yaml.example", root / "workspace.yaml.example")
    bootstrap.init(root, "owner/repo", force=True, environ={})
    project = root / "projects" / "second"
    project.mkdir(parents=True)
    run_git(project, "init", "-q", "-b", "main")
    write(project / "AGENTS.md", "# AGENTS.md\n\nIntro.\n\n## What this is\n\nSecond.\n\n" + VERIFICATION_SECTION)
    write(project / "scripts/integration-verify.json", '["python", "-c", "pass"]\n')
    release_workspace(root)

    assert bootstrap.add(root, "second", None, []) == 0

    data = yaml.safe_load((root / "workspace.yaml").read_text(encoding="utf-8"))
    assert data["projects"]["second"] == {"repo": "projects/second", "worktrees": "worktrees/second", "state": "state/second-data"}
    assert (root / "worktrees" / "second").is_dir()
    assert (root / "state" / "second-data").is_dir()
    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert validator.POINTER_MARKER in (project / "AGENTS.md").read_text(encoding="utf-8")
    assert (project / ".atc/atc.lock").is_file() and (project / ".atc/README.md").is_file()
    assert (project / ".atc/governance/INDEX.md").is_file()
    for tool in (".claude", ".agents"):
        assert (project / tool / "skills" / "demo-skill" / "SKILL.md").is_file()


def test_add_rejects_non_repository_and_bad_names(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path, create_project=False)
    shutil.copyfile(bootstrap.ROOT / "workspace.yaml.example", root / "workspace.yaml.example")
    bootstrap.init(root, "owner/repo", force=True, environ={})
    (root / "projects" / "plain").mkdir(parents=True)
    assert bootstrap.add(root, "plain", None, []) == 1
    assert bootstrap.add(root, "Bad_Name", None, []) == 2
    assert bootstrap.add(root, "plain", str(root / "elsewhere"), []) == 2


@pytest.mark.integration
def test_add_refuses_the_reserved_root_name_before_writing(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path, create_project=False)
    shutil.copyfile(bootstrap.ROOT / "workspace.yaml.example", root / "workspace.yaml.example")
    bootstrap.init(root, "owner/repo", force=True, environ={})
    project = root / "projects" / "root"
    project.mkdir(parents=True)
    run_git(project, "init", "-q", "-b", "main")
    before = (root / "workspace.yaml").read_text(encoding="utf-8")
    assert bootstrap.add(root, "root", None, []) == 2
    assert (root / "workspace.yaml").read_text(encoding="utf-8") == before


def test_add_installs_requested_opt_in_adapter(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path, create_project=False)
    shutil.copyfile(bootstrap.ROOT / "workspace.yaml.example", root / "workspace.yaml.example")
    bootstrap.init(root, "owner/repo", force=True, environ={})
    write(root / "shared/skills/optional-skill/SKILL.md", "---\nname: optional-skill\ndescription: test skill\n---\n\n# Optional\n")
    sync_project.apply_plan(sync_project.root_adapter_plan(root), "sync")
    adapter = (root / ".claude/skills/optional-skill/SKILL.md").read_text(encoding="utf-8")
    project = root / "projects" / "third"
    project.mkdir(parents=True)
    run_git(project, "init", "-q", "-b", "main")
    write(project / "AGENTS.md", "# AGENTS.md\n\n## What this is\n\nThird.\n\n" + VERIFICATION_SECTION)
    write(project / "scripts/integration-verify.json", '["python", "-c", "pass"]\n')
    release_workspace(root)

    assert bootstrap.add(root, "third", None, ["optional-skill"]) == 0
    assert (project / ".claude/skills/optional-skill/SKILL.md").read_text(encoding="utf-8") == adapter
    with pytest.raises(SystemExit):
        bootstrap.install_opt_in(root, project, ["no-such-skill"])


def test_opt_in_install_carries_supporting_files_and_leaves_an_unowned_directory(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    write(root / "shared/skills/optional-skill/SKILL.md", "---\nname: optional-skill\ndescription: test skill\n---\n\n# Optional\n")
    write(root / "shared/skills/optional-skill/references/guide.md", "# Guide\n")
    write(project / ".agents/skills/optional-skill/notes/mine.md", "mine\n")
    with pytest.raises(SystemExit):
        bootstrap.install_opt_in(root, project, ["optional-skill"])
    assert (project / ".claude/skills/optional-skill/references/guide.md").read_text(encoding="utf-8") == "# Guide\n"
    assert (project / ".agents/skills/optional-skill/notes/mine.md").is_file()
    assert not (project / ".agents/skills/optional-skill/SKILL.md").exists()


def _registered(tmp_path: Path, skill_harness: bool = False) -> tuple[Path, Path]:
    root, project = make_workspace(tmp_path)
    if skill_harness:
        path = root / "workspace.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "skill_harnesses:\n  demo: [claude]\n", encoding="utf-8")
    (root / "worktrees" / "demo").mkdir(parents=True)
    return root, project


@pytest.mark.unit
def test_remove_drops_the_entry_and_keeps_the_project_files(tmp_path: Path) -> None:
    root, project = _registered(tmp_path, skill_harness=True)
    assert bootstrap.remove(root, "demo", check=False, answer=lambda prompt: "y") == 0
    data = yaml.safe_load((root / "workspace.yaml").read_text(encoding="utf-8"))
    assert "demo" not in data["projects"] and "demo" not in (data.get("skill_harnesses") or {})
    assert not (root / "worktrees" / "demo").exists()
    assert (project / "AGENTS.md").is_file()


@pytest.mark.unit
def test_remove_refuses_while_a_worktree_is_present(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _ = _registered(tmp_path)
    (root / "worktrees" / "demo" / "claude+ATC-0001").mkdir()
    before = (root / "workspace.yaml").read_text(encoding="utf-8")
    assert bootstrap.remove(root, "demo", check=False, answer=lambda prompt: "y") == 1
    err = capsys.readouterr().err
    assert err.startswith("STOP WORKTREES_PRESENT: ") and "task close" in err
    assert (root / "workspace.yaml").read_text(encoding="utf-8") == before


@pytest.mark.unit
def test_remove_check_and_a_declined_answer_change_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _ = _registered(tmp_path)
    before = (root / "workspace.yaml").read_text(encoding="utf-8")
    assert bootstrap.remove(root, "demo", check=True) == 0
    assert capsys.readouterr().out.startswith("CHECK remove demo")
    assert bootstrap.remove(root, "demo", check=False, answer=lambda prompt: "n") == 1
    assert capsys.readouterr().err.startswith("STOP DECLINED: ")
    assert (root / "workspace.yaml").read_text(encoding="utf-8") == before


@pytest.mark.unit
def test_remove_names_an_unregistered_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _ = _registered(tmp_path)
    assert bootstrap.remove(root, "absent", check=True) == 2
    assert capsys.readouterr().err.startswith("ERROR PROJECT_UNKNOWN ")

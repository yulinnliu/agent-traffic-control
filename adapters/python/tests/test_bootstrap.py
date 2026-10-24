from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import shutil

import bootstrap
import onboard_project
import validate_workspace as validator
from test_tooling import make_workspace, run_git, write


def test_init_writes_map_and_directories(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert bootstrap.init(root, "owner/repo", force=False, environ={}) == 0
    data = yaml.safe_load((root / "workspace.yaml").read_text(encoding="utf-8"))
    assert data == {"version": 1, "governance_source": "owner/repo", "workspace_root": root.as_posix(), "projects": {}}
    for name in ("projects", "worktrees", "state"):
        assert (root / name).is_dir()
    assert "atc" in capsys.readouterr().out


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
    assert bootstrap.init(root, None, force=False, environ={}) == 1
    assert not (root / "workspace.yaml").exists()


def test_add_registers_onboards_and_validates(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path, create_project=False)
    shutil.copyfile(bootstrap.ROOT / "workspace.yaml.example", root / "workspace.yaml.example")
    bootstrap.init(root, "owner/repo", force=True, environ={})
    project = root / "projects" / "second"
    project.mkdir(parents=True)
    run_git(project, "init", "-q", "-b", "main")
    write(project / "AGENTS.md", "# AGENTS.md\n\nIntro.\n\n## What this is\n\nSecond.\n")

    assert bootstrap.add(root, "second", None, []) == 0

    data = yaml.safe_load((root / "workspace.yaml").read_text(encoding="utf-8"))
    assert data["projects"]["second"] == {"repo": "projects/second", "worktrees": "worktrees/second", "state": "state/second-data"}
    assert (root / "worktrees" / "second").is_dir()
    assert (root / "state" / "second-data").is_dir()
    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert validator.POLICY_BEGIN in (project / "AGENTS.md").read_text(encoding="utf-8")
    assert (project / ".atc/governance/INDEX.md").is_file()
    for tool in (".claude", ".agents"):
        assert (project / tool / "skills" / "demo-skill" / "SKILL.md").is_file()


def test_add_rejects_non_repository_and_bad_names(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path, create_project=False)
    shutil.copyfile(bootstrap.ROOT / "workspace.yaml.example", root / "workspace.yaml.example")
    bootstrap.init(root, "owner/repo", force=True, environ={})
    (root / "projects" / "plain").mkdir(parents=True)
    assert bootstrap.add(root, "plain", None, []) == 1
    assert bootstrap.add(root, "Bad_Name", None, []) == 1
    assert bootstrap.add(root, "plain", str(root / "elsewhere"), []) == 1


def test_add_installs_requested_opt_in_adapter(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path, create_project=False)
    shutil.copyfile(bootstrap.ROOT / "workspace.yaml.example", root / "workspace.yaml.example")
    bootstrap.init(root, "owner/repo", force=True, environ={})
    write(root / "shared/skills/optional-skill/SKILL.md", "---\nname: optional-skill\ndescription: test skill\n---\n\n# Optional\n")
    onboard_project.apply_plan(onboard_project.root_adapter_plan(root), "sync")
    adapter = (root / ".claude/skills/optional-skill/SKILL.md").read_text(encoding="utf-8")
    project = root / "projects" / "third"
    project.mkdir(parents=True)
    run_git(project, "init", "-q", "-b", "main")
    write(project / "AGENTS.md", "# AGENTS.md\n\n## What this is\n\nThird.\n")

    assert bootstrap.add(root, "third", None, ["optional-skill"]) == 0
    assert (project / ".claude/skills/optional-skill/SKILL.md").read_text(encoding="utf-8") == adapter
    with pytest.raises(SystemExit):
        bootstrap.install_opt_in(root, project, ["no-such-skill"])

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import bootstrap
import onboard_project
from test_tooling import make_workspace, managed_adapter, write

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _orphaned_project(tmp_path: Path) -> tuple[Path, Path]:
    """A project carrying a managed adapter whose canonical skill no longer exists,
    plus a project-owned skill directory without any ownership marker."""
    root, project = make_workspace(tmp_path)
    shutil.copyfile(bootstrap.ROOT / "workspace.yaml.example", root / "workspace.yaml.example")
    for tool in (".agents", ".claude"):
        write(project / tool / "skills/old-skill/SKILL.md", managed_adapter("old-skill"))
    write(project / ".claude/skills/local-skill/SKILL.md", "---\nname: local-skill\ndescription: mine\n---\n\n# Local\n")
    return root, project


def _plan_actions(root: Path, project: Path) -> dict[str, list[str]]:
    plan = onboard_project.build_plan(project, root)
    out: dict[str, list[str]] = {}
    for item in plan:
        out.setdefault(item.action, []).append(item.path.relative_to(project).as_posix())
    return out


def test_plan_reports_orphaned_managed_adapters_only(tmp_path: Path) -> None:
    root, project = _orphaned_project(tmp_path)
    actions = _plan_actions(root, project)
    assert sorted(actions["prune"]) == [".agents/skills/old-skill", ".claude/skills/old-skill"]
    assert not any("local-skill" in p for paths in actions.values() for p in paths)


def test_check_only_reports_and_sync_without_prune_keeps_orphans(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = _orphaned_project(tmp_path)
    assert onboard_project.main(["--check", "demo"], root=root) == 1
    assert "would prune (2)" in capsys.readouterr().out
    assert onboard_project.main(["--sync", "demo"], root=root) == 1
    assert (project / ".claude/skills/old-skill/SKILL.md").is_file()


def test_sync_prune_removes_orphans_and_validator_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = _orphaned_project(tmp_path)
    assert onboard_project.main(["--sync", "--prune", "demo"], root=root) == 0
    assert "prune (2)" in capsys.readouterr().out
    assert not (project / ".claude/skills/old-skill").exists()
    assert not (project / ".agents/skills/old-skill").exists()
    assert (project / ".claude/skills/local-skill/SKILL.md").is_file()
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_workspace.py"), "--projects", "--root", str(root)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_prune_requires_sync_mode(tmp_path: Path) -> None:
    root, _ = _orphaned_project(tmp_path)
    with pytest.raises(SystemExit):
        onboard_project.main(["--check", "--prune", "demo"], root=root)


def test_bootstrap_sync_converges_every_project(tmp_path: Path) -> None:
    root, project = _orphaned_project(tmp_path)
    (project / ".claude/skills/demo-skill").mkdir(parents=True, exist_ok=True)
    (project / ".claude/skills/demo-skill/SKILL.md").unlink(missing_ok=True)
    assert bootstrap.sync(root, None, prune=True) == 0
    assert (project / ".claude/skills/demo-skill/SKILL.md").is_file()
    assert not (project / ".agents/skills/old-skill").exists()

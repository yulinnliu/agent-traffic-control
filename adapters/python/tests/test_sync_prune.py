from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import sync_project
import validate_workspace as validator
from test_tooling import commit_workspace, make_workspace, managed_adapter, run_git, write

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _orphaned_project(tmp_path: Path) -> tuple[Path, Path]:
    """A project carrying a managed adapter whose canonical skill no longer exists,
    plus a project-owned skill directory without any ownership marker, committed
    as sync commits need."""
    root, project = make_workspace(tmp_path)
    shutil.copyfile(sync_project.ROOT / "workspace.yaml.example", root / "workspace.yaml.example")
    for tool in (".agents", ".claude"):
        write(project / tool / "skills/old-skill/SKILL.md", managed_adapter("old-skill"))
    write(project / ".claude/skills/local-skill/SKILL.md", "---\nname: local-skill\ndescription: mine\n---\n\n# Local\n")
    commit_workspace(root, project)
    return root, project


def _plan_actions(root: Path, project: Path, tool_dirs: tuple[str, ...] = validator.TOOL_DIRS) -> dict[str, list[str]]:
    plan = sync_project.build_plan(project, root, tool_dirs=tool_dirs)
    out: dict[str, list[str]] = {}
    for item in plan:
        out.setdefault(item.action, []).append(item.path.relative_to(project).as_posix())
    return out


def _validate(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_workspace.py"), "--projects", "--root", str(root)],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True, text=True, encoding="utf-8",
    )


def _select(root: Path, tools: str) -> None:
    """Give project demo a skill_tools entry in workspace.yaml and commit it, as sync needs a clean checkout."""
    path = root / "workspace.yaml"
    path.write_text(path.read_text(encoding="utf-8") + f"skill_tools:\n  demo: {tools}\n", encoding="utf-8")
    run_git(root, "add", "-A")
    run_git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "select")


def test_plan_reports_orphaned_managed_adapters_only(tmp_path: Path) -> None:
    root, project = _orphaned_project(tmp_path)
    actions = _plan_actions(root, project)
    assert sorted(actions["prune"]) == [".agents/skills/old-skill", ".claude/skills/old-skill"]
    assert not any("local-skill" in p for paths in actions.values() for p in paths)


@pytest.mark.unit
def test_plan_prunes_every_managed_adapter_in_an_unselected_tool_dir(tmp_path: Path) -> None:
    root, project = _orphaned_project(tmp_path)
    write(project / ".dsh/skills/demo-skill/SKILL.md", managed_adapter("demo-skill"))
    write(project / ".dsh/skills/local-skill/SKILL.md", "---\nname: local-skill\ndescription: mine\n---\n\n# Local\n")
    actions = _plan_actions(root, project, (".claude",))
    assert sorted(actions["prune"]) == [".agents/skills/old-skill", ".claude/skills/old-skill", ".dsh/skills/demo-skill"]
    assert ".claude/skills/demo-skill/SKILL.md" in actions["create"]
    assert not any(path.startswith((".agents/", ".dsh/")) for path in actions["create"])
    assert not any("local-skill" in p for paths in actions.values() for p in paths)


@pytest.mark.integration
def test_check_lists_the_prunes_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = _orphaned_project(tmp_path)
    assert sync_project.main(["demo", "--check"], root=root) == 1
    assert "would prune (2)" in capsys.readouterr().out
    assert (project / ".claude/skills/old-skill/SKILL.md").is_file()


@pytest.mark.integration
def test_sync_prunes_by_default_in_its_commit_and_validator_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = _orphaned_project(tmp_path)
    assert sync_project.main(["demo"], root=root) == 0
    assert "prune (2)" in capsys.readouterr().out
    assert not (project / ".claude/skills/old-skill").exists()
    assert not (project / ".agents/skills/old-skill").exists()
    assert (project / ".claude/skills/local-skill/SKILL.md").is_file()
    deleted = subprocess.check_output(["git", "-C", str(project), "show", "--name-only", "--diff-filter=D", "--format=",
                                       "HEAD"], text=True).split()
    assert sorted(deleted) == [".agents/skills/old-skill/SKILL.md", ".claude/skills/old-skill/SKILL.md"]
    run = _validate(root)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.integration
def test_skill_tools_selects_the_tool_dirs_and_validate_agrees(tmp_path: Path) -> None:
    root, project = _orphaned_project(tmp_path)
    assert sync_project.main(["demo"], root=root) == 0
    assert (project / ".dsh/skills/demo-skill/SKILL.md").is_file()
    _select(root, "[claude, codex]")
    assert sync_project.main(["demo"], root=root) == 0
    assert not (project / ".dsh/skills/demo-skill").exists()
    for tool_dir in (".claude", ".agents"):
        assert (project / tool_dir / "skills/demo-skill/SKILL.md").is_file()
    run = _validate(root)
    assert run.returncode == 0, run.stdout + run.stderr
    write(project / ".dsh/skills/demo-skill/SKILL.md", validator.render_adapter(root, "demo-skill", ".dsh"))
    run = _validate(root)
    assert run.returncode == 1 and ".dsh/skills/demo-skill/ is a managed adapter in a tool directory skill_tools does " \
        "not select" in run.stdout


@pytest.mark.unit
@pytest.mark.parametrize(("value", "dirs"), [
    (None, validator.TOOL_DIRS), ("all", validator.TOOL_DIRS), (["dsh", "claude"], (".claude", ".dsh")),
    (["codex"], (".agents",)),
], ids=["absent", "all", "dsh claude", "codex"])
def test_a_skill_tools_value_names_tool_dirs_in_their_fixed_order(value: object, dirs: tuple[str, ...]) -> None:
    assert validator.tool_dirs_for(value) == dirs


@pytest.mark.unit
@pytest.mark.parametrize(("block", "problem"), [
    ("skill_tools: [claude]\n", "'skill_tools' must be a mapping"),
    ("skill_tools:\n  other: all\n", "skill_tools.other: not a registered project"),
    ("skill_tools:\n  demo: []\n", "skill_tools.demo must be 'all' or a non-empty list"),
    ("skill_tools:\n  demo: claude\n", "skill_tools.demo must be 'all' or a non-empty list"),
    ("skill_tools:\n  demo: [cursor]\n", "skill_tools.demo: unknown tool 'cursor'"),
    ("skill_tools:\n  demo: [claude, claude]\n", "skill_tools.demo lists a tool twice"),
], ids=["list", "unregistered", "empty", "string", "unknown", "twice"])
def test_a_malformed_skill_tools_stops_sync_before_it_writes(tmp_path: Path, block: str, problem: str,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    root, project = make_workspace(tmp_path)
    path = root / "workspace.yaml"
    path.write_text(path.read_text(encoding="utf-8") + block, encoding="utf-8")
    assert sync_project.main(["demo"], root=root) == 2
    assert problem in capsys.readouterr().err
    assert not (project / ".atc").exists()


@pytest.mark.unit
def test_skill_tools_is_top_level_so_project_entries_keep_their_three_keys(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path)
    path = root / "workspace.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "skill_tools:\n  demo: [claude]\n", encoding="utf-8")
    assert sync_project.load_projects(root)["demo"].keys() == {"repo", "worktrees", "state"}
    assert validator.load_skill_tools(path) == {"demo": (".claude",)}


@pytest.mark.unit
def test_a_project_without_agents_md_gets_a_minimal_one(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    (project / "AGENTS.md").unlink()
    item = next(i for i in sync_project.build_plan(project, root) if i.path == project / "AGENTS.md")
    assert item.action == "create"
    sync_project.apply_plan([item], "apply")
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert text.splitlines() == ["# AGENTS.md", "", validator.pointer_line(root), "",
                                 "This project's own rules go in this file, below the line above."]
    assert next(i for i in sync_project.build_plan(project, root) if i.path == project / "AGENTS.md").action == "skip"


@pytest.mark.unit
@pytest.mark.parametrize("original", ["# AGENTS.md\n\nRules without sections.\n", "Rules.", ""], ids=["title", "no newline", "empty"])
def test_the_pointer_line_is_appended_to_an_agents_md_with_no_section(tmp_path: Path, original: str,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    write(project / "AGENTS.md", original)
    item = next(i for i in sync_project.build_plan(project, root) if i.path == project / "AGENTS.md")
    assert item.action == "create"
    sync_project.apply_plan([item], "sync")
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith(original.rstrip("\n")) and text.endswith(f"\n{validator.pointer_line(root)}\n" if original else
                                                                    f"{validator.pointer_line(root)}\n")
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_project_pointer(project, "projects.demo")
    assert validator.FAILURES == []


@pytest.mark.integration
def test_sync_with_no_project_converges_every_project(tmp_path: Path) -> None:
    root, project = _orphaned_project(tmp_path)
    (project / ".claude/skills/demo-skill").mkdir(parents=True, exist_ok=True)
    (project / ".claude/skills/demo-skill/SKILL.md").unlink(missing_ok=True)
    assert sync_project.main([], root=root) == 0
    assert (project / ".claude/skills/demo-skill/SKILL.md").is_file()
    assert not (project / ".agents/skills/old-skill").exists()


@pytest.mark.integration
def test_default_output_lists_changes_and_counts_skips_verbose_lists_skips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _ = _orphaned_project(tmp_path)
    sync_project.main(["demo"], root=root)
    capsys.readouterr()
    assert sync_project.main(["demo", "--check"], root=root) == 0
    quiet = capsys.readouterr().out
    assert "is current" not in quiet and "skip (" not in quiet
    counts = [line for line in quiet.splitlines() if line.startswith("  skip ")]
    assert len(counts) == 2 and all(line.endswith(", update 0") and int(line.split()[1].rstrip(",")) > 0 for line in counts)
    assert sync_project.main(["demo", "--check", "--verbose"], root=root) == 0
    loud = capsys.readouterr().out
    assert "skip (" in loud and "is current" in loud and "  skip " not in loud.replace("  skip (", "")


@pytest.mark.integration
def test_default_output_still_prints_every_change(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _ = _orphaned_project(tmp_path)
    assert sync_project.main(["demo", "--check"], root=root) == 1
    out = capsys.readouterr().out
    assert "would prune (2)" in out and "would create" in out
    assert any(line.startswith("  skip ") for line in out.splitlines())


@pytest.mark.unit
def test_sync_root_prunes_an_orphaned_managed_adapter_of_the_checkout(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path)
    write(root / ".dsh/skills/old-skill/SKILL.md", managed_adapter("old-skill"))
    plan = sync_project.root_adapter_plan(root)
    assert [item.path for item in plan if item.action == "prune"] == [root / ".dsh/skills/old-skill"]
    sync_project.apply_plan(plan, "sync")
    assert not (root / ".dsh/skills/old-skill").exists()


@pytest.mark.integration
def test_doctor_reads_each_project_against_its_skill_tools(tmp_path: Path) -> None:
    import doctor
    root, project = make_workspace(tmp_path)
    path = root / "workspace.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "skill_tools:\n  demo: [claude]\n", encoding="utf-8")
    sync_project.apply_plan(sync_project.build_plan(project, root, tool_dirs=(".claude",)), "sync")
    assert {c.name: c for c in doctor.diagnose(root, {})}["projects.demo.integration"].status == "READY"
    write(project / ".agents/skills/demo-skill/SKILL.md", validator.render_adapter(root, "demo-skill", ".agents"))
    assert {c.name: c for c in doctor.diagnose(root, {})}["projects.demo.integration"].status == "ERROR"


@pytest.mark.integration
def test_bootstrap_add_installs_opt_in_skills_in_the_selected_tool_dirs_only(tmp_path: Path) -> None:
    import bootstrap
    from test_tooling import VERIFICATION_SECTION, release_workspace
    root, _ = make_workspace(tmp_path, create_project=False)
    shutil.copyfile(bootstrap.ROOT / "workspace.yaml.example", root / "workspace.yaml.example")
    bootstrap.init(root, "owner/repo", force=True, environ={})
    path = root / "workspace.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("projects: {}\n", "projects: {}\nskill_tools:\n  third: [codex]\n"),
                    encoding="utf-8")
    write(root / "shared/skills/optional-skill/SKILL.md", "---\nname: optional-skill\ndescription: test skill\n---\n\n# Optional\n")
    sync_project.apply_plan(sync_project.root_adapter_plan(root), "sync")
    project = root / "projects" / "third"
    project.mkdir(parents=True)
    run_git(project, "init", "-q", "-b", "main")
    write(project / "AGENTS.md", "# AGENTS.md\n\n## What this is\n\nThird.\n\n" + VERIFICATION_SECTION)
    write(project / "scripts/integration-verify.json", '["python", "-c", "pass"]\n')
    release_workspace(root)
    assert bootstrap.add(root, "third", None, ["optional-skill"]) == 0
    assert (project / ".agents/skills/optional-skill/SKILL.md").is_file()
    assert not (project / ".claude/skills").exists() and not (project / ".dsh/skills").exists()

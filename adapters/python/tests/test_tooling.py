from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import check_workspace
import doctor
import friction
import onboard_project
import validate_workspace as validator


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def managed_adapter(name: str, description: str = "test skill") -> str:
    """A managed adapter whose body is not the canonical render (an older release's output)."""
    return f"""---
name: {name}
description: {description}
managed-by: agent-traffic-control
canonical: {name}
---

# {name} - hand-written stand-in
"""


def make_workspace(tmp_path: Path, *, create_project: bool = True) -> tuple[Path, Path]:
    root = tmp_path / "workspace"
    project = root / "projects" / "demo"
    root.mkdir(parents=True)
    # A real workspace root is a Git repository, and doctor probes it for lock-reason
    # support; without this the probe cannot succeed.
    run_git(root, "init", "-q", "-b", "main")
    if create_project:
        project.mkdir(parents=True)
        write(project / "AGENTS.md", "# AGENTS.md\n\nProject intro.\n\n## What this is\n\nDemo.\n")
    write(root / "CLAUDE.md", "Read AGENTS.md.\n")
    for name in ("CORE.md", "GIT_WORKFLOW.md", "VERIFICATION.md", "DELIVERY.md", "FAILURE_ESCALATION.md", "GOVERNANCE_CHANGES.md", "PROJECT_RUNTIME.md"):
        write(root / "shared/governance" / name, f"# {name}\n")
    write(root / "shared/governance/INDEX.md", "# INDEX.md\n" + "\n".join(("CORE.md", "GIT_WORKFLOW.md", "VERIFICATION.md", "DELIVERY.md", "FAILURE_ESCALATION.md", "GOVERNANCE_CHANGES.md", "PROJECT_RUNTIME.md")) + "\n")
    write(root / "shared/conventions/ALERT_MESSAGES.md", "# Alerts\n")
    write(
        root / "workspace.yaml",
        f"version: 1\nworkspace_root: {root.as_posix()}\nprojects:\n  demo:\n    repo: projects/demo\n    worktrees: worktrees/demo\n    state: state/demo-data\n",
    )
    write(
        root / "shared/templates/PROJECT_AGENTS.md",
        "<!-- atc:project-policy-reference begin -->\n"
        "Pinned governance: `.atc/governance/` at the exact governance revision in `.atc/governance.yaml`. Read `.atc/governance/INDEX.md` and CORE.md.\n"
        "<!-- atc:project-policy-reference end -->\n",
    )
    write(
        root / "shared/skills/demo-skill/SKILL.md",
        "---\nname: demo-skill\ndescription: test skill\nadoption: baseline\n---\n\n# Demo\n",
    )
    onboard_project.apply_plan(onboard_project.root_adapter_plan(root), "sync")
    return root, project


def changed(plan: list[onboard_project.PlanItem]) -> list[onboard_project.PlanItem]:
    return [item for item in plan if item.action in ("create", "update", "collision")]


def test_onboarding_is_idempotent(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    first = onboard_project.build_plan(project, root)
    onboard_project.apply_plan(first, "sync")
    assert not changed(onboard_project.build_plan(project, root))


def test_missing_canonical_template_fails_without_project_reference(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    (root / "shared/templates/PROJECT_AGENTS.md").unlink()
    with pytest.raises(ValueError, match="canonical project policy template"):
        onboard_project.build_plan(project, root)


def test_malformed_workspace_yaml_is_rejected(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path)
    write(root / "workspace.yaml", "projects: [not, a, mapping\n")
    with pytest.raises(ValueError, match="not valid YAML"):
        onboard_project.load_projects(root)


def test_workspace_root_mismatch_is_contract_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = make_workspace(tmp_path)
    text = (root / "workspace.yaml").read_text(encoding="utf-8")
    write(root / "workspace.yaml", text.replace(root.as_posix(), (tmp_path / "wrong").as_posix()))
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_projects({})
    assert any("does not match this repo's actual location" in failure for failure in validator.FAILURES)


def test_unmanaged_collision_is_never_modified(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    target = project / ".agents/skills/demo-skill/SKILL.md"
    original = "---\nname: demo-skill\ndescription: local\n---\n\n# Local\n"
    write(target, original)
    plan = onboard_project.build_plan(project, root)
    assert any(item.action == "collision" and item.path == target for item in plan)
    onboard_project.apply_plan(plan, "sync")
    assert target.read_text(encoding="utf-8") == original


def test_managed_adapter_sync_and_second_sync_zero_diff(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    onboard_project.apply_plan(onboard_project.build_plan(project, root), "sync")
    target = project / ".agents/skills/demo-skill/SKILL.md"
    write(target, managed_adapter("demo-skill") + "\ndrift\n")
    plan = onboard_project.build_plan(project, root)
    assert any(item.action == "update" and item.path == target for item in plan)
    onboard_project.apply_plan(plan, "sync")
    assert target.read_text(encoding="utf-8") == (root / ".agents/skills/demo-skill/SKILL.md").read_text(encoding="utf-8")
    assert not changed(onboard_project.build_plan(project, root))


def claude_item(plan: list[onboard_project.PlanItem], project: Path) -> onboard_project.PlanItem:
    return next(item for item in plan if item.path == project / "CLAUDE.md")


def test_missing_claude_entrypoint_is_created_from_workspace_root(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    plan = onboard_project.build_plan(project, root)
    assert claude_item(plan, project).action == "create"
    onboard_project.apply_plan(plan, "apply")
    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == (root / "CLAUDE.md").read_text(encoding="utf-8")


def test_current_claude_entrypoint_is_reported_current(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    (project / "CLAUDE.md").write_bytes(b"Read AGENTS.md.\r\n")
    item = claude_item(onboard_project.build_plan(project, root), project)
    assert (item.action, item.detail) == ("skip", "workspace entrypoint is current")


@pytest.mark.parametrize("content", ["Read AGENTS.md first.\n", "# Project-owned\n\n@AGENTS.md\n"])
def test_differing_claude_entrypoint_is_reported_and_never_modified(tmp_path: Path, content: str) -> None:
    root, project = make_workspace(tmp_path)
    write(project / "CLAUDE.md", content)
    plan = onboard_project.build_plan(project, root)
    item = claude_item(plan, project)
    assert item.action == "skip"
    assert item.detail.startswith("differs from workspace entrypoint")
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == content


def test_validator_rejects_forbidden_fallback_in_canonical_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = make_workspace(tmp_path)
    write(root / "shared/skills/demo-skill/SKILL.md", "---\nname: demo-skill\ndescription: test skill\n---\n\nRead ~/Hami/shared/skills/demo-skill/SKILL.md.\n")
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.load_canonical_skills()
    assert any("hardcodes a '~/Hami'-style path" in failure for failure in validator.FAILURES)
    validator.FAILURES.clear()


@pytest.mark.parametrize("reference", ["$ATC_ROOT/shared/skills/x/SKILL.md", "${ATC_ROOT}/shared", "$env:ATC_ROOT\\adapters"])
def test_canonical_skill_must_not_point_back_at_the_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference: str) -> None:
    root, _ = make_workspace(tmp_path)
    write(root / "shared/skills/demo-skill/SKILL.md", f"---\nname: demo-skill\ndescription: test skill\n---\n\nRead {reference}.\n")
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.load_canonical_skills()
    assert any("through an environment variable" in failure for failure in validator.FAILURES)
    validator.FAILURES.clear()


def test_tool_overlay_is_appended_only_to_its_tool(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    write(root / "shared/skills/demo-skill/adapters/codex.md", "## Codex constants\n\n- owner: codex\n")
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    codex = (project / ".agents/skills/demo-skill/SKILL.md").read_text(encoding="utf-8")
    claude = (project / ".claude/skills/demo-skill/SKILL.md").read_text(encoding="utf-8")
    assert codex.endswith("# Demo\n\n## Codex constants\n\n- owner: codex\n")
    assert "Codex constants" not in claude
    write(root / "shared/skills/demo-skill/adapters/gemini.md", "nope\n")
    with pytest.raises(ValueError, match="overlay name must be one of"):
        onboard_project.canonical_skills(root)


def test_friction_append_and_resolution_preserve_incident(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    incident = friction.append_incident(
        kind="VALIDATOR_DRIFT", project="demo", tool="pytest", expected="green",
        observed="red", evidence="fixture", recurrence_key="validator", events_file=events,
    )
    original = events.read_text(encoding="utf-8")
    resolution = friction.resolve_incident(incident["event_id"], "green now", events)
    lines = events.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] + "\n" == original
    assert json.loads(lines[1])["resolves_event_id"] == incident["event_id"]
    assert resolution["event_type"] == "RESOLUTION"


def test_resolution_of_missing_incident_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        friction.resolve_incident("evt_missing", "none", tmp_path / "events.jsonl")


def test_friction_summary_projects_resolution_and_threshold(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    events = tmp_path / "events.jsonl"
    first = friction.append_incident(
        kind="RULE_NOT_FOUND", project="a", tool="codex", expected="found", observed="missing",
        evidence="one", recurrence_key="routing", session_id="s1", events_file=events,
    )
    friction.append_incident(
        kind="RULE_NOT_FOUND", project="b", tool="claude", expected="found", observed="missing",
        evidence="two", recurrence_key="routing", session_id="s2", events_file=events,
    )
    friction.resolve_incident(first["event_id"], "fixed", events)
    friction.print_summary(events, "recurrence_key")
    output = capsys.readouterr().out
    assert "routing  2  1  2  2  GOVERNANCE_CHANGE_CANDIDATE" in output


def test_validator_failure_records_evidence(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    validator_script = root / "fail.py"
    write(validator_script, "raise SystemExit(1)\n")
    events = root / "state/workspace-friction/events.jsonl"
    result = check_workspace.run_check(
        root=root, validator_script=validator_script, events_file=events,
        python_executable=sys.executable,
    )
    assert result == 1
    records = friction.load_records(events)
    assert records[0]["kind"] == "VALIDATOR_DRIFT"
    assert list((events.parent / "validator").glob("*.log"))


def test_validator_success_creates_no_incident(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    validator_script = root / "pass.py"
    write(validator_script, "raise SystemExit(0)\n")
    events = root / "state/workspace-friction/events.jsonl"
    result = check_workspace.run_check(
        root=root, validator_script=validator_script, events_file=events,
        python_executable=sys.executable,
    )
    assert result == 0
    assert not events.exists()


def test_missing_registered_project_is_readiness_not_contract_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = make_workspace(tmp_path, create_project=False)
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_projects({})
    assert validator.FAILURES == []


def test_explicit_candidate_checkout_is_validated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    onboard_project.apply_plan(onboard_project.build_plan(project, root), "sync")
    candidate = tmp_path / "candidate-demo"
    shutil.copytree(project, candidate)
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_projects(validator.load_canonical_skills(), {"demo": candidate})
    assert validator.FAILURES == []

def test_new_project_bootstrap_requires_vendored_index_and_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    write(project / "AGENTS.md", f"# AGENTS.md\n\n{validator.POLICY_BEGIN}\nread governance\n{validator.POLICY_END}\n")
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_project_policy_reference(project, "projects.demo")
    assert any("governance revision" in failure for failure in validator.FAILURES)
    assert any("does not read .atc/governance/INDEX.md" in failure for failure in validator.FAILURES)
    validator.FAILURES.clear()


def test_policy_block_from_an_older_release_is_stale_and_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    write(project / "AGENTS.md", f"# AGENTS.md\n\n<!-- atc:project-policy-reference begin 2025 -->\nold\n{validator.POLICY_END}\n\n## What\n")
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_project_policy_reference(project, "projects.demo")
    assert validator.FAILURES == ["projects.demo: AGENTS.md policy block is from an older release — run sync to replace it"]
    validator.FAILURES.clear()
    plan = onboard_project.build_plan(project, root)
    item = next(i for i in plan if i.path == project / "AGENTS.md")
    assert item.action == "update"
    onboard_project.apply_plan(plan, "sync")
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert validator.POLICY_BEGIN in text and "old" not in text and text.endswith("## What\n")
    validator.check_project_policy_reference(project, "projects.demo")
    assert validator.FAILURES == []


def test_vendored_governance_is_installed_pinned_and_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    for module in validator.vendored_governance_modules():
        assert (project / ".atc/governance" / module).read_text(encoding="utf-8") == (root / "shared/governance" / module).read_text(encoding="utf-8")
    pin = (project / ".atc/governance.yaml").read_text(encoding="utf-8")
    assert "managed-by: agent-traffic-control" in pin and "source: " in pin and "revision: " in pin
    assert check_registered_projects(root, monkeypatch) == []
    write(project / ".atc/governance/CORE.md", "# edited locally\n")
    assert any(".atc/governance/CORE.md differs" in f for f in check_registered_projects(root, monkeypatch))
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    assert check_registered_projects(root, monkeypatch) == []
    write(project / ".atc/governance.yaml", "source: mine\nrevision: abc\n")
    assert onboard_project.run("sync", [("demo", project)], root) == 2
    assert (project / ".atc/governance.yaml").read_text(encoding="utf-8") == "source: mine\nrevision: abc\n"


def test_governance_pin_outside_main_history_is_stale_and_resynced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    commit = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m")

    def head() -> str:
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()

    def pin_item() -> onboard_project.PlanItem:
        return next(i for i in onboard_project.governance_plan(project, root) if i.path == pin)

    run_git(root, "add", "-A")
    run_git(root, *commit, "release")
    old = head()
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    assert check_registered_projects(root, monkeypatch) == []
    pin = project / ".atc/governance.yaml"
    # Squash: the old commit survives on a backup branch but leaves main's history.
    run_git(root, "branch", "backup", old)
    run_git(root, "checkout", "-q", "--orphan", "squashed")
    run_git(root, *commit, "init commit")
    run_git(root, "branch", "-M", "squashed", "main")
    assert check_registered_projects(root, monkeypatch) == [
        f"projects.demo: .atc/governance.yaml revision {old} is not in the history of main — run sync"
    ]
    run_git(root, "checkout", "-q", "-b", "topic")
    assert check_registered_projects(root, monkeypatch) == []
    assert (pin_item().action, pin_item().detail) == ("skip", "governance pin not checked: the ATC checkout is not on main")
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    assert f"revision: {old}" in pin.read_text(encoding="utf-8")
    run_git(root, "checkout", "-q", "main")
    assert (pin_item().action, pin_item().detail) == ("update", "governance pin revision is not in the history of main")
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    assert f"revision: {head()}" in pin.read_text(encoding="utf-8")
    assert check_registered_projects(root, monkeypatch) == []


def test_project_enforcement_must_match_its_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    assert check_registered_projects(root, monkeypatch) == []
    write(project / "policies/hard-deny.yaml", "version: 1\ncapabilities:\n  deny: []\nfilesystem:\n  deny:\n    - \"**/.env\"\n")
    assert any("projects.demo.enforcement: generated/enforcement/ differs" in f for f in check_registered_projects(root, monkeypatch))
    import generate_enforcement
    assert generate_enforcement.main(["--policies", str(project / "policies"), "--out", str(project / "generated/enforcement")]) == 0
    assert check_registered_projects(root, monkeypatch) == []
    write(project / "policies/hard-deny.yaml", "version: 1\ncapabilities:\n  deny: []\nfilesystem:\n  deny:\n    - \"${DATA}/**\"\n")
    assert any("unresolved-token" in f for f in check_registered_projects(root, monkeypatch))


def test_hardcoded_governance_source_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    block = f"{validator.POLICY_BEGIN}\n.atc/governance/INDEX.md governance revision .atc/governance.yaml acme/agent-traffic-control\n{validator.POLICY_END}\n"
    write(project / "AGENTS.md", "# AGENTS.md\n\n" + block)
    write(root / "AGENTS.md", "# AGENTS.md\n\nacme/agent-traffic-control\n")
    write(root / "shared/templates/PROJECT_AGENTS.md", block)
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_project_policy_reference(project, "projects.demo")
    validator.check_policy_template()
    validator.check_no_hardcoded_governance_source()
    assert sum("hard-codes a governance source" in failure for failure in validator.FAILURES) == 3
    validator.FAILURES.clear()


def test_legacy_only_project_bootstrap_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    write(project / "AGENTS.md", "# AGENTS.md\n\nshared/rules/AGENT_DECISION_POLICY.md\n")
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_project_policy_reference(project, "projects.demo")
    assert any("missing canonical governance bootstrap" in failure for failure in validator.FAILURES)
    validator.FAILURES.clear()

def test_canonical_governance_validation_does_not_require_legacy_redirects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = make_workspace(tmp_path)
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_rules_referenced_in_agents_md()
    assert validator.FAILURES == []

def test_explicit_candidate_checkout_must_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = make_workspace(tmp_path)
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_projects(validator.load_canonical_skills(), {"demo": tmp_path / "missing"})
    assert any("explicit candidate checkout" in failure for failure in validator.FAILURES)
    validator.FAILURES.clear()


def test_unregistered_candidate_checkout_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_projects(validator.load_canonical_skills(), {"other": project})
    assert any("unregistered project" in failure for failure in validator.FAILURES)
    validator.FAILURES.clear()


def test_doctor_distinguishes_not_initialized_from_error(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    onboard_project.apply_plan(onboard_project.build_plan(project, root), "sync")
    checks = doctor.diagnose(root, {})
    by_name = {check.name: check.status for check in checks}
    assert by_name["projects.demo.repo"] == "READY"
    assert by_name["projects.demo.worktrees"] == "NOT_INITIALIZED"
    assert by_name["projects.demo.state"] == "NOT_INITIALIZED"


def make_env_workspace(tmp_path: Path) -> Path:
    root, _ = make_workspace(tmp_path, create_project=False)
    write(root / validator.POLICY_RELATIVE, "# INDEX.md\n")
    return root


def test_atc_root_override_is_optional_and_checked_when_set(tmp_path: Path) -> None:
    root = make_env_workspace(tmp_path)
    assert validator.workspace_env_problems(root, {}) == {}
    assert validator.workspace_env_problems(root, {"ATC_ROOT": str(root)}) == {"ATC_ROOT": None}
    by_name = {check.name: check for check in doctor.diagnose(root, {})}
    assert by_name["ATC_ROOT"].status == "READY" and "unset" in by_name["ATC_ROOT"].detail
    by_name = {check.name: check for check in doctor.diagnose(root, {"ATC_ROOT": str(root)})}
    assert by_name["ATC_ROOT"].status == "READY" and "same checkout" in by_name["ATC_ROOT"].detail


def test_atc_root_must_be_the_checkout_directory(tmp_path: Path) -> None:
    root = make_env_workspace(tmp_path)
    problems = validator.workspace_env_problems(root, {"ATC_ROOT": str(root / "workspace.yaml")})
    assert "must be the checkout directory" in problems["ATC_ROOT"]
    other = make_env_workspace(tmp_path / "other")
    assert validator.workspace_env_problems(root, {"ATC_ROOT": str(other)})["ATC_ROOT"].startswith("resolves to")


def test_atc_root_must_hold_the_canonical_policy(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path, create_project=False)
    (root / validator.GOVERNANCE_INDEX_RELATIVE).unlink()
    problems = validator.workspace_env_problems(root, {"ATC_ROOT": str(root)})
    assert "canonical governance index missing" in problems["ATC_ROOT"]


def test_doctor_requires_the_atc_command(tmp_path: Path) -> None:
    root = make_env_workspace(tmp_path)
    by_name = {check.name: check for check in doctor.diagnose(root, {"PATH": str(tmp_path / "empty")})}
    assert by_name["atc command"].status == "ERROR" and "uv tool install" in by_name["atc command"].detail


def test_validator_fails_on_mismatched_atc_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_env_workspace(tmp_path)
    other = make_env_workspace(tmp_path / "other")
    monkeypatch.setattr(validator, "ROOT", root)
    monkeypatch.setenv("ATC_ROOT", str(other))
    validator.FAILURES.clear()
    validator.check_workspace_env()
    assert len(validator.FAILURES) == 1 and validator.FAILURES[0].startswith("ATC_ROOT: resolves to")
    validator.FAILURES.clear()


def check_registered_projects(root: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_projects(validator.load_canonical_skills())
    failures = list(validator.FAILURES)
    validator.FAILURES.clear()
    return failures


def test_canonical_skill_requires_a_description(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = make_workspace(tmp_path)
    write(root / "shared/skills/demo-skill/SKILL.md", "---\nname: demo-skill\nadoption: baseline\n---\n\n# Demo\n")
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.load_canonical_skills()
    assert any("missing description" in failure for failure in validator.FAILURES)
    validator.FAILURES.clear()


def test_removed_canonical_skill_leaves_an_orphaned_workspace_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = make_workspace(tmp_path)
    (root / "shared/skills/demo-skill/SKILL.md").unlink()
    (root / "shared/skills/demo-skill").rmdir()
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.check_adapter_tree(root, validator.load_canonical_skills(), label="workspace root", required=set())
    assert any("orphaned adapter" in failure for failure in validator.FAILURES)
    validator.FAILURES.clear()


def test_adapter_from_an_older_release_is_stale_but_still_owned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    onboard_project.apply_plan(onboard_project.build_plan(project, root), "sync")
    target = project / ".claude/skills/demo-skill/SKILL.md"
    write(target, managed_adapter("demo-skill"))
    failures = check_registered_projects(root, monkeypatch)
    assert any("content drifted from canonical" in failure for failure in failures)
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    assert target.read_text(encoding="utf-8") == validator.render_adapter(root, "demo-skill", ".claude")
    assert check_registered_projects(root, monkeypatch) == []


def test_registered_project_must_carry_every_baseline_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    onboard_project.apply_plan(onboard_project.build_plan(project, root), "sync")
    assert check_registered_projects(root, monkeypatch) == []
    (project / ".claude/skills/demo-skill/SKILL.md").unlink()
    (project / ".claude/skills/demo-skill").rmdir()
    failures = check_registered_projects(root, monkeypatch)
    assert failures == ["projects.demo: .claude/skills/demo-skill/ missing"]


def test_registered_project_may_omit_an_optional_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    onboard_project.apply_plan(onboard_project.build_plan(project, root), "sync")
    write(root / "shared/skills/optional-skill/SKILL.md", "---\nname: optional-skill\ndescription: optional\n---\n\n# Optional\n")
    for tool in (".agents", ".claude"):
        write(root / tool / "skills/optional-skill/SKILL.md", managed_adapter("optional-skill", "optional"))
    assert check_registered_projects(root, monkeypatch) == []


def test_registered_project_adapter_must_match_canonical_description(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project = make_workspace(tmp_path)
    onboard_project.apply_plan(onboard_project.build_plan(project, root), "sync")
    write(project / ".agents/skills/demo-skill/SKILL.md", managed_adapter("demo-skill", "drifted"))
    failures = check_registered_projects(root, monkeypatch)
    assert any("description drifted from canonical" in failure for failure in failures)


def test_user_level_duplicate_of_a_canonical_skill_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    write(home / ".claude/skills/demo-skill/SKILL.md", "duplicate\n")
    write(home / ".claude/skills/unrelated/SKILL.md", "user skill\n")
    validator.FAILURES.clear()
    validator.check_user_level_duplicates({"demo-skill": {}}, home)
    assert len(validator.FAILURES) == 1
    assert "user-level duplicate of canonical shared skill 'demo-skill'" in validator.FAILURES[0]
    validator.FAILURES.clear()


def contract_adapter(root: Path, tool: str = ".dsh", requires: int | None = 1) -> str:
    """The rendered bound-skill adapter, optionally with a wrong or missing requires-contract line."""
    adapter = validator.render_adapter(root, "bound-skill", tool)
    if requires == 1:
        return adapter
    return adapter.replace("requires-contract: 1\n", "" if requires is None else f"requires-contract: {requires}\n")


def add_contract_skill(root: Path, tools: tuple[str, ...] = (".claude", ".agents", ".dsh"), requires: int | None = 1) -> None:
    write(root / "shared/skills/bound-skill/SKILL.md", "---\nname: bound-skill\ndescription: contract skill\ncontract: 1\n---\n\n# Bound\n")
    for tool in tools:
        write(root / tool / "skills/bound-skill/SKILL.md", contract_adapter(root, tool, requires))


def workspace_root_failures(root: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    canonical = validator.load_canonical_skills()
    validator.check_adapter_tree(root, canonical, label="workspace root", required=set(canonical))
    failures = list(validator.FAILURES)
    validator.FAILURES.clear()
    return failures


def test_contract_skill_needs_a_dsh_adapter_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = make_workspace(tmp_path)
    add_contract_skill(root)
    assert workspace_root_failures(root, monkeypatch) == []
    (root / ".dsh/skills/bound-skill/SKILL.md").unlink()
    (root / ".dsh/skills/bound-skill").rmdir()
    assert workspace_root_failures(root, monkeypatch) == ["workspace root: .dsh/skills/bound-skill/ missing"]


def test_skill_without_a_contract_needs_no_dsh_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = make_workspace(tmp_path)
    assert not (root / ".dsh").exists()
    assert workspace_root_failures(root, monkeypatch) == []


@pytest.mark.parametrize("requires", [2, None])
def test_adapter_requires_contract_must_match_canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requires: int | None) -> None:
    root, _ = make_workspace(tmp_path)
    add_contract_skill(root, requires=requires)
    failures = workspace_root_failures(root, monkeypatch)
    assert len(failures) == 3
    assert all("content drifted from canonical" in failure for failure in failures)


def test_bare_contract_key_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = make_workspace(tmp_path)
    add_contract_skill(root)
    canonical_file = root / "shared/skills/bound-skill/SKILL.md"
    canonical_file.write_text(
        canonical_file.read_text(encoding="utf-8").replace("contract: 1\n", "contract:\n"), encoding="utf-8"
    )
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.load_canonical_skills()
    failures = list(validator.FAILURES)
    validator.FAILURES.clear()
    assert failures == [f"{canonical_file}: contract must be a positive integer, got None"]
    # A bare key is not a declaration, so it must not pull in the DSH adapter either.
    assert validator.adapter_dirs({"contract": None}) == validator.TOOL_DIRS[:2]
    assert validator.adapter_dirs({"contract": 1}) == validator.TOOL_DIRS
    # Anything the contract check rejects is equally not a declaration.
    for rejected in (None, True, False, 0, -1, "1", 1.0):
        assert not validator.declares_contract({"contract": rejected}), rejected


def test_orphaned_dsh_adapter_is_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = make_workspace(tmp_path)
    write(root / ".dsh/skills/gone/SKILL.md", managed_adapter("gone"))
    assert any(".dsh/skills/gone/ is a managed adapter" in failure for failure in workspace_root_failures(root, monkeypatch))


def test_canonical_description_over_the_catalog_limit_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = make_workspace(tmp_path)
    long = "x" * (validator.MAX_DESCRIPTION + 1)
    write(root / "shared/skills/demo-skill/SKILL.md", f"---\nname: demo-skill\ndescription: {long}\nadoption: baseline\n---\n\n# Demo\n")
    monkeypatch.setattr(validator, "ROOT", root)
    validator.FAILURES.clear()
    validator.load_canonical_skills()
    assert any("description is 501 characters, limit 500" in failure for failure in validator.FAILURES)
    validator.FAILURES.clear()


def test_onboarding_plans_a_dsh_adapter_for_a_baseline_contract_skill(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    add_contract_skill(root)
    text = (root / "shared/skills/bound-skill/SKILL.md").read_text(encoding="utf-8")
    write(root / "shared/skills/bound-skill/SKILL.md", text.replace("contract: 1\n", "contract: 1\nadoption: baseline\n"))
    targets = {item.path for item in onboard_project.build_plan(project, root) if item.action == "create"}
    assert project / ".dsh/skills/bound-skill/SKILL.md" in targets
    assert project / ".dsh/skills/demo-skill/SKILL.md" not in targets


def canonical_cli(root: Path) -> Path:
    return root / "adapters/python/scripts/hami_worktree.py"


def write_cli_stub(root: Path) -> None:
    canonical_cli(root).parent.mkdir(parents=True, exist_ok=True)
    canonical_cli(root).write_text("import sys\n\nsys.exit(0 if '--help' in sys.argv else 1)\n", encoding="utf-8")


def test_doctor_reports_hami_worktree_readiness(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path)
    write_cli_stub(root)
    environ = {"CODEX_HOME": str(tmp_path / "codex")}
    by_name = {check.name: check for check in doctor.diagnose(root, environ)}
    assert by_name["hami-worktree"].status == "READY"
    assert "contract 1" in by_name["hami-worktree"].detail
    write(tmp_path / "codex/config.toml", f"[desktop]\ngit-worktree-root = '{(root / 'worktrees').as_posix()}'\n")
    check = {c.name: c for c in doctor.diagnose(root, environ)}["hami-worktree"]
    assert check.status == "ERROR"
    assert "codex-app" in check.detail and "CONFIG-ONLY" in check.detail
    canonical_cli(root).unlink()
    assert {c.name: c for c in doctor.diagnose(root, environ)}["hami-worktree"].status == "ERROR"
    canonical_cli(root).write_text("this is not python\n", encoding="utf-8")
    assert {c.name: c for c in doctor.diagnose(root, environ)}["hami-worktree"].status == "ERROR"


def run_doctor(scripts: Path, workspace_root: Path) -> subprocess.CompletedProcess:
    environ = {**os.environ, "PYTHONPATH": str(scripts)}
    return subprocess.run(
        [sys.executable, str(scripts / "doctor.py")],
        cwd=scripts.parent,
        capture_output=True,
        text=True,
        env=environ,
    )


def hami_worktree_line(stdout: str) -> str:
    return next(line for line in stdout.splitlines() if "hami-worktree" in line)


def write_minimal_workspace(root: Path) -> None:
    """Just enough workspace that doctor has no unrelated error to report."""
    run_git(root, "init", "-q", "-b", "main")
    write(root / "workspace.yaml", f"version: 1\nworkspace_root: {root.as_posix()}\nprojects: {{}}\n")
    write(root / validator.POLICY_RELATIVE, "# Policy\n")


def test_doctor_exposes_the_hami_worktree_capability_when_it_loads() -> None:
    """The guard must leave a working module usable, not shadow it."""
    assert doctor.hami_worktree is not None
    assert doctor.HAMI_WORKTREE_IMPORT_ERROR == ""


def test_doctor_starts_and_reports_a_missing_hami_worktree_module(tmp_path: Path) -> None:
    """Only a real startup exercises the import guard; diagnose() on the live module cannot."""
    scripts = tmp_path / "scripts"
    shutil.copytree(Path(doctor.__file__).resolve().parent, scripts)
    real_module = scripts / "hami_worktree.py"
    write_minimal_workspace(tmp_path)
    real_module.unlink()

    missing = run_doctor(scripts, tmp_path)
    assert "Traceback" not in missing.stderr and "Traceback" not in missing.stdout
    line = hami_worktree_line(missing.stdout)
    assert "ERROR" in line
    assert "could not be loaded" in line

    # A module that cannot be compiled must be reported the same way.
    real_module.write_text("def broken(:\n", encoding="utf-8")
    broken = run_doctor(scripts, tmp_path)
    assert "Traceback" not in broken.stderr and "Traceback" not in broken.stdout
    assert "could not be loaded" in hami_worktree_line(broken.stdout)

    # With the real module present, doctor must load it rather than die or disable it.
    shutil.copy(Path(doctor.hami_worktree.__file__), real_module)
    present = run_doctor(scripts, tmp_path)
    assert "Traceback" not in present.stderr and "Traceback" not in present.stdout
    assert "could not be loaded" not in present.stdout
    assert "contract 1" in hami_worktree_line(present.stdout)



@pytest.mark.parametrize("tool", [".agents", ".claude", ".dsh"])
def test_adopted_opt_in_drift_check_sync_and_idempotence(tmp_path: Path, tool: str, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = make_workspace(tmp_path)
    add_contract_skill(root)
    onboard_project.run("sync", [("demo", project)], root)
    target = project / tool / "skills/bound-skill/SKILL.md"
    expected = (root / tool / "skills/bound-skill/SKILL.md").read_text(encoding="utf-8")
    original = expected + "\ncontent drift\n"
    write(target, original)
    assert onboard_project.run("check", [("demo", project)], root) == 1
    assert "would update" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == original
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    assert target.read_text(encoding="utf-8") == expected
    for other in set(validator.TOOL_DIRS) - {tool}:
        assert not (project / other / "skills/bound-skill/SKILL.md").exists()
    before = target.read_bytes()
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    assert target.read_bytes() == before
    assert not changed(onboard_project.build_plan(project, root))


def test_unadopted_opt_in_contract_skill_is_not_installed(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    add_contract_skill(root)
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    for tool in validator.TOOL_DIRS:
        assert not (project / tool / "skills/bound-skill").exists()


@pytest.mark.parametrize("baseline", [False, True])
@pytest.mark.parametrize("field,value", [
    ("managed-by", None), ("managed-by", "project"),
    ("canonical", None), ("canonical", "other-skill"),
    ("name", "other-skill"),
])
def test_adapter_identity_collision_blocks_sync(tmp_path: Path, baseline: bool, field: str, value: str | None) -> None:
    root, project = make_workspace(tmp_path)
    add_contract_skill(root)
    canonical = root / "shared/skills/bound-skill/SKILL.md"
    if baseline:
        write(canonical, canonical.read_text(encoding="utf-8").replace("contract: 1\n", "contract: 1\nadoption: baseline\n"))
    target = project / ".dsh/skills/bound-skill/SKILL.md"
    text = contract_adapter(root)
    old_value = {"managed-by": "agent-traffic-control", "canonical": "bound-skill", "name": "bound-skill"}[field]
    original = text.replace(f"{field}: {old_value}\n", "" if value is None else f"{field}: {value}\n")
    write(target, original)
    assert onboard_project.run("check", [("demo", project)], root) == 2
    assert onboard_project.run("sync", [("demo", project)], root) == 2
    assert target.read_text(encoding="utf-8") == original
    assert not (project / "CLAUDE.md").exists()


@pytest.mark.parametrize("baseline", [False, True])
def test_adapter_line_endings_only_skip_but_content_drift_updates(tmp_path: Path, baseline: bool) -> None:
    root, project = make_workspace(tmp_path)
    add_contract_skill(root)
    canonical = root / "shared/skills/bound-skill/SKILL.md"
    if baseline:
        write(canonical, canonical.read_text(encoding="utf-8").replace("contract: 1\n", "contract: 1\nadoption: baseline\n"))
    target = project / ".dsh/skills/bound-skill/SKILL.md"
    source = root / ".dsh/skills/bound-skill/SKILL.md"
    expected = source.read_text(encoding="utf-8")
    target.parent.mkdir(parents=True)
    target.write_bytes(expected.replace("\n", "\r\n").encode("utf-8"))
    before = target.read_bytes()
    plan = onboard_project.build_plan(project, root)
    assert next(item.action for item in plan if item.path == target) == "skip"
    onboard_project.apply_plan(plan, "sync")
    assert target.read_bytes() == before
    target.write_bytes((expected + "\nreal drift\n").replace("\n", "\r\n").encode("utf-8"))
    plan = onboard_project.build_plan(project, root)
    assert next(item.action for item in plan if item.path == target) == "update"


def test_opt_in_dsh_sync_carries_the_contract_and_the_dsh_constants(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    workspace = Path(__file__).resolve().parents[3]
    relative = ".dsh/skills/hami-worktree/SKILL.md"
    expected = (workspace / relative).read_text(encoding="utf-8")
    write(root / relative, expected)
    shutil.copytree(workspace / "shared/skills/hami-worktree", root / "shared/skills/hami-worktree")
    target = project / relative
    write(target, expected.replace("DSH_SESSION_ID", "STALE_SESSION_ID"))
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    actual = target.read_text(encoding="utf-8")
    assert actual == expected
    for constant in ("**owner:** `dsh`", "DSH_SESSION_ID", "Windows PowerShell 5.1", "--no-cache", "--squash-integrated", "atc worktree"):
        assert constant in actual
    assert "$ATC_ROOT" not in actual
    for tool in (".agents", ".claude"):
        assert not (project / tool / "skills/hami-worktree").exists()


@pytest.mark.parametrize("original", ["# Local skill\n", "---\n[invalid\n---\n", "---\n- local\n---\n"])
def test_uncertain_opt_in_metadata_is_collision(tmp_path: Path, original: str) -> None:
    root, project = make_workspace(tmp_path)
    add_contract_skill(root)
    target = project / ".dsh/skills/bound-skill/SKILL.md"
    write(target, original)
    assert onboard_project.run("sync", [("demo", project)], root) == 2
    assert target.read_text(encoding="utf-8") == original


def test_root_adapter_with_foreign_identity_is_a_collision(tmp_path: Path) -> None:
    root, project = make_workspace(tmp_path)
    add_contract_skill(root)
    source = root / ".dsh/skills/bound-skill/SKILL.md"
    foreign = contract_adapter(root).replace("canonical: bound-skill", "canonical: other-skill")
    write(source, foreign)
    assert onboard_project.run("sync", [("demo", project)], root) == 2
    assert source.read_text(encoding="utf-8") == foreign


def test_root_adapter_sync_renders_invocation_flag_for_claude_only(tmp_path: Path) -> None:
    root, _ = make_workspace(tmp_path)
    write(
        root / "shared/skills/demo-skill/SKILL.md",
        "---\nname: demo-skill\ndescription: sharper trigger\nadoption: baseline\ninvocation: user\n---\n\n# Demo\n",
    )
    claude_adapter = root / ".claude/skills/demo-skill/SKILL.md"
    plan = onboard_project.root_adapter_plan(root)
    updates = [item for item in plan if item.action == "update"]
    assert {item.path for item in updates} == {
        claude_adapter,
        root / ".agents/skills/demo-skill/SKILL.md",
    }
    onboard_project.apply_plan(plan, "sync")
    synced = claude_adapter.read_text(encoding="utf-8")
    assert "description: sharper trigger" in synced
    assert "disable-model-invocation: true" in synced
    assert "\nadoption:" not in synced and "\ninvocation:" not in synced
    assert "disable-model-invocation" not in (root / ".agents/skills/demo-skill/SKILL.md").read_text(encoding="utf-8")
    assert not [item for item in onboard_project.root_adapter_plan(root) if item.action != "skip"]


def test_run_syncs_root_adapters_before_projects(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = make_workspace(tmp_path)
    onboard_project.apply_plan(onboard_project.build_plan(project, root), "sync")
    write(
        root / "shared/skills/demo-skill/SKILL.md",
        "---\nname: demo-skill\ndescription: sharper trigger\nadoption: baseline\n---\n\n# Demo\n",
    )
    assert onboard_project.run("sync", [("demo", project)], root) == 0
    out = capsys.readouterr().out
    assert "update (" in out
    assert "would update" not in out
    for tool in (".agents", ".claude"):
        text = (project / tool / "skills/demo-skill/SKILL.md").read_text(encoding="utf-8")
        assert "description: sharper trigger" in text


def test_check_mode_reports_root_drift_without_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, project = make_workspace(tmp_path)
    write(
        root / "shared/skills/demo-skill/SKILL.md",
        "---\nname: demo-skill\ndescription: sharper trigger\nadoption: baseline\n---\n\n# Demo\n",
    )
    assert onboard_project.run("check", [("demo", project)], root) == 1
    assert "would update" in capsys.readouterr().out
    assert "description: test skill" in (root / ".claude/skills/demo-skill/SKILL.md").read_text(encoding="utf-8")

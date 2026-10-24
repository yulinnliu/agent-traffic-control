"""Generator behaviour outside the conformance cases: hook mode, option rules and the machine-local segment."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import generate_enforcement

CASE = Path(__file__).resolve().parents[3] / "shared/conformance/generate-enforcement/filesystem-modes/input"


@pytest.mark.skipif(os.name == "nt", reason="Windows has no executable bit")
def test_pre_commit_is_written_executable(tmp_path):
    assert generate_enforcement.main(["--policies", str(CASE), "--out", str(tmp_path)]) == 0
    assert (tmp_path / "git-hooks/pre-commit").stat().st_mode & stat.S_IXUSR


def test_live_options_require_check(tmp_path):
    with pytest.raises(SystemExit) as exc:
        generate_enforcement.main(["--policies", str(CASE), "--out", str(tmp_path), "--live-hook", str(tmp_path / "h")])
    assert exc.value.code == 2


def _workspace(tmp_path, projects: str) -> Path:
    path = tmp_path / "workspace.yaml"
    path.write_text(f"version: 1\nworkspace_root: C:/Work Space\nprojects:\n{projects}", encoding="utf-8")
    return path


WORKSPACE = (
    "  a:\n    repo: projects/a\n    worktrees: worktrees/a\n    state: state/a-data\n"
    "  b:\n    repo: projects/b\n    worktrees: worktrees/b\n    state: /srv/b-data/\n"
    "  c:\n    repo: projects/c\n    worktrees: worktrees/c\n"
)
LOCAL_DENY = ["Edit(//c/Work Space/state/a-data/**)", "Edit(//srv/b-data/**)"]


def _settings(path: Path, deny: list[str]) -> Path:
    path.write_text(json.dumps({"permissions": {"deny": deny}}), encoding="utf-8")
    return path


def _policy_deny(tmp_path) -> list[str]:
    out = tmp_path / "plain"
    assert generate_enforcement.main(["--policies", str(CASE), "--out", str(out)]) == 0
    return json.loads((out / "claude-code/settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]


def test_state_roots_render_as_absolute_edit_denies(tmp_path):
    roots = generate_enforcement.state_roots(_workspace(tmp_path, WORKSPACE))
    assert roots == ["C:/Work Space/state/a-data", "/srv/b-data"]
    assert json.loads(generate_enforcement.local_settings(roots))["permissions"]["deny"] == LOCAL_DENY


def test_workspace_writes_local_out_and_leaves_out_unchanged(tmp_path, capsys):
    plain, mapped, local = tmp_path / "plain", tmp_path / "mapped", tmp_path / "local"
    assert generate_enforcement.main(["--policies", str(CASE), "--out", str(plain)]) == 0
    workspace = _workspace(tmp_path, WORKSPACE)
    assert generate_enforcement.main(["--policies", str(CASE), "--out", str(mapped), "--workspace", str(workspace), "--local-out", str(local)]) == 0
    assert "machine-local:" in capsys.readouterr().out
    assert sorted(p.relative_to(mapped) for p in mapped.rglob("*")) == sorted(p.relative_to(plain) for p in plain.rglob("*"))
    for path in plain.rglob("*"):
        if path.is_file():
            assert (mapped / path.relative_to(plain)).read_bytes() == path.read_bytes()
    written = json.loads((local / "claude-code/settings.local.json").read_text(encoding="utf-8"))
    assert written["permissions"]["deny"] == LOCAL_DENY
    assert generate_enforcement.main(["--policies", str(CASE), "--out", str(mapped), "--workspace", str(workspace), "--local-out", str(local), "--check"]) == 0


def test_local_out_never_lands_under_out(tmp_path):
    with pytest.raises(SystemExit) as exc:
        generate_enforcement.main(["--policies", str(CASE), "--out", str(tmp_path), "--workspace", str(_workspace(tmp_path, WORKSPACE)), "--local-out", str(tmp_path / "local")])
    assert exc.value.code == 2


def test_check_compares_policy_and_machine_local_segments_separately(tmp_path, capsys):
    out = tmp_path / "out"
    generate_enforcement.main(["--policies", str(CASE), "--out", str(out)])
    policy = _policy_deny(tmp_path)
    workspace = _workspace(tmp_path, WORKSPACE)
    both = _settings(tmp_path / "both.json", policy + LOCAL_DENY)
    check = ["--policies", str(CASE), "--out", str(out), "--workspace", str(workspace), "--check"]
    assert generate_enforcement.main([*check, "--live-claude", str(both), "--live-claude-local", str(both)]) == 0
    stale = _settings(tmp_path / "stale.json", policy + LOCAL_DENY[:1])
    capsys.readouterr()
    assert generate_enforcement.main([*check, "--live-claude", str(stale), "--live-claude-local", str(stale)]) == 1
    output = capsys.readouterr().out
    assert "live-claude-local (" in output and "live-claude (" not in output
    assert generate_enforcement.main([*check, "--live-claude-local", str(tmp_path / "missing.json")]) == 1


@pytest.mark.parametrize("projects", [None, "  c:\n    repo: projects/c\n    worktrees: worktrees/c\n"])
def test_without_state_roots_live_claude_is_unchanged(tmp_path, capsys, projects):
    out = tmp_path / "out"
    generate_enforcement.main(["--policies", str(CASE), "--out", str(out)])
    check = ["--policies", str(CASE), "--out", str(out), "--check"]
    if projects is not None:
        check += ["--workspace", str(_workspace(tmp_path, projects))]
    policy = _policy_deny(tmp_path)
    assert generate_enforcement.main([*check, "--live-claude", str(_settings(tmp_path / "exact.json", policy))]) == 0
    assert generate_enforcement.main([*check, "--live-claude", str(_settings(tmp_path / "extra.json", policy + LOCAL_DENY))]) == 1
    capsys.readouterr()
    assert generate_enforcement.main(["--policies", str(CASE), "--out", str(out)]) == 0
    assert "machine-local" not in capsys.readouterr().out


def test_atc_enforce_passes_the_root_workspace_map(tmp_path, monkeypatch):
    import atc_cli
    seen = {}
    monkeypatch.setattr(generate_enforcement, "main", lambda rest: seen.setdefault("rest", rest) and 0)
    (tmp_path / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    atc_cli.main(["--root", str(tmp_path), "enforce", "--policies", "p", "--out", "o"])
    assert seen["rest"][-2:] == ["--workspace", str(tmp_path / "workspace.yaml")]
    seen.clear()
    atc_cli.main(["--root", str(tmp_path), "enforce", "--workspace=other.yaml"])
    assert seen["rest"] == ["--workspace=other.yaml"]


def test_validate_reports_missing_and_drifted_machine_local(tmp_path, capsys):
    import validate_workspace
    workspace = _workspace(tmp_path, WORKSPACE)
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    failures = list(validate_workspace.FAILURES)
    validate_workspace.report_machine_local(project, "projects.a.machine-local", workspace)
    assert "machine-local state deny missing" in capsys.readouterr().out
    _settings(project / ".claude/settings.local.json", ["Bash(ls:*)", *LOCAL_DENY[:1]])
    validate_workspace.report_machine_local(project, "projects.a.machine-local", workspace)
    assert "differs from workspace.yaml" in capsys.readouterr().out
    _settings(project / ".claude/settings.local.json", ["Bash(ls:*)", *LOCAL_DENY])
    validate_workspace.report_machine_local(project, "projects.a.machine-local", workspace)
    assert capsys.readouterr().out == ""
    assert validate_workspace.FAILURES == failures


def test_claude_renders_only_read_and_edit_for_every_mode_combination():
    hard = {"filesystem": {"deny": [
        {"glob": "all/**", "modes": ["read", "edit", "write"]},
        {"glob": "wr/**", "modes": ["write", "edit"]},
        {"glob": "w/**", "modes": ["write"]},
        {"glob": "r/**", "modes": ["read"]},
    ]}}
    deny = json.loads(generate_enforcement.claude_settings(hard))["permissions"]["deny"]
    assert deny == ["Read(all/**)", "Edit(all/**)", "Edit(wr/**)", "Edit(w/**)", "Read(r/**)"]


def _enforce(out: Path) -> None:
    assert generate_enforcement.main(["--policies", str(CASE), "--out", str(out)]) == 0


def test_rerun_leaves_a_crlf_file_with_the_same_content_untouched(tmp_path):
    _enforce(tmp_path)
    target = tmp_path / "claude-code/settings.json"
    crlf = target.read_bytes().replace(b"\n", b"\r\n")
    target.write_bytes(crlf)
    _enforce(tmp_path)
    assert target.read_bytes() == crlf


def test_rerun_rewrites_a_file_whose_content_differs_as_lf(tmp_path):
    _enforce(tmp_path)
    target = tmp_path / "claude-code/settings.json"
    expected = target.read_bytes()
    target.write_bytes(expected.replace(b"Read(**/.env)", b"Read(**/.stale)").replace(b"\n", b"\r\n"))
    _enforce(tmp_path)
    assert target.read_bytes() == expected

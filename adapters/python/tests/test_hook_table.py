"""`atc enforce` composes the PreToolUse hook's table and installs its entries (docs/ENFORCEMENT.md § PreToolUse hook)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import doctor
import generate_enforcement as ge
import guard
import pretooluse_hook as hook

pytestmark = pytest.mark.integration

ROOT_POLICY = """version: 1
capabilities:
  deny: []
filesystem:
  deny:
    - glob: "**/.env"
    - glob: "~/.ssh/**"
commands:
  deny:
    - id: atc-integrate
      argv_prefix: [atc, integrate]
      unless_args: [--check]
    - id: git-reset-hard
      argv_prefix: [git, reset, --hard]
"""

# The retired cd guard's file name, joined at run time so the test selector reads no edge to that module.
CD_GUARD = "_".join(("worktree", "cd", "guard.py"))

PROJECT_POLICY = """version: 1
capabilities:
  deny: []
filesystem:
  deny:
    - glob: "authorizations/**"
      reason: Owner-signed.
    - glob: "~/.ssh/**"
commands:
  deny:
    - id: git-reset-hard
      argv_prefix: [git, reset, --hard]
    - id: gh-secret
      argv_prefix: [gh, secret]
"""


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("USERPROFILE", str(path))
    monkeypatch.setenv("HOME", str(path))
    return path


@pytest.fixture
def workspace(tmp_path, home) -> Path:
    root = tmp_path / "ws"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "hard-deny.yaml").write_text(ROOT_POLICY, encoding="utf-8")
    (root / "projects" / "p" / "policies").mkdir(parents=True)
    (root / "projects" / "p" / "policies" / "hard-deny.yaml").write_text(PROJECT_POLICY, encoding="utf-8")
    (root / "projects" / "q").mkdir(parents=True)
    path = root / "workspace.yaml"
    path.write_text(
        f"version: 1\nworkspace_root: {root.as_posix()}\n"
        "agent_write_roots:\n  - ~/.atc\n  - \"%SCRATCH_ROOT%\"\n"
        "projects:\n"
        "  p:\n    repo: projects/p\n    worktrees: worktrees/p\n    state: state/p-data\n"
        "  q:\n    repo: projects/q\n    worktrees: worktrees/q\n    state: state/q-data\n",
        encoding="utf-8")
    return path


def compose(workspace: Path, home: Path, monkeypatch) -> dict:
    monkeypatch.setenv("SCRATCH_ROOT", str(home.parent / "scratch"))
    return json.loads(ge.hook_table(workspace, home / ".atc" / "enforcement"))


def norm(path: Path) -> str:
    return ge._norm(path, path)


def test_the_table_composes_the_workspace_policy_first_then_each_project(workspace, home, monkeypatch):
    table = compose(workspace, home, monkeypatch)
    root = workspace.parent
    assert [(c["id"], c["source"]) for c in table["commands"]] == [
        ("atc-integrate", "policies/hard-deny.yaml"),
        ("git-reset-hard", "policies/hard-deny.yaml"),
        ("gh-secret", "projects.p"),
    ]
    assert table["commands"][0]["unless_args"] == ["--check"]
    rules = {(r["source"], r["glob"]): r for r in table["filesystem"]}
    env = rules[("policies/hard-deny.yaml", "**/.env")]
    assert env["scope"] == [{"base": norm(root), "depth": 0}, {"base": norm(root / "worktrees" / "root"), "depth": 1}]
    auth = rules[("projects.p", "authorizations/**")]
    assert auth["scope"] == [{"base": norm(root / "projects" / "p"), "depth": 0},
                             {"base": norm(root / "worktrees" / "p"), "depth": 1}]
    assert auth["reason"] == "Owner-signed." and auth["modes"] == ["read", "edit", "write"]
    ssh = [r for r in table["filesystem"] if r["glob"].endswith("/.ssh/**")]
    assert len(ssh) == 1 and ssh[0]["scope"] is None and ssh[0]["glob"] == norm(home / ".ssh").replace("\\", "/") + "/**"
    states = [r for r in table["filesystem"] if r["id"].startswith("state.")]
    assert [(r["id"], r["modes"]) for r in states] == [("state.p", ["edit", "write"]), ("state.q", ["edit", "write"])]
    assert table["worktrees"] == [norm(root / "worktrees")]
    assert table["write_roots"] == [norm(root), norm(home / ".atc"), norm(home.parent / "scratch")]
    assert table["protected"][0] == norm(home / ".atc" / "enforcement")
    assert norm(root / "adapters" / "python" / "scripts" / "pretooluse_hook.py") in table["protected"]


def test_the_table_is_deterministic_and_refuses_a_bad_policy(workspace, home, monkeypatch):
    first = ge.hook_table(workspace, home / "t")
    assert ge.hook_table(workspace, home / "t") == first and first.endswith("\n")
    (workspace.parent / "projects" / "p" / "policies" / "hard-deny.yaml").write_text(
        PROJECT_POLICY.replace("argv_prefix: [gh, secret]", "argv_prefix: [gh, secret]\n      background: false"),
        encoding="utf-8")
    with pytest.raises(ge.policies.PolicyError) as raised:
        ge.hook_table(workspace, home / "t")
    assert raised.value.problems[0].startswith("projects.p: schema-violation")


def test_install_writes_the_table_and_replaces_the_retired_guards(workspace, home, monkeypatch):
    claude = home / ".claude" / "settings.json"
    claude.parent.mkdir()
    claude.write_text("\ufeff" + json.dumps({"model": "x", "hooks": {"PreToolUse": [
        {"matcher": "Bash|PowerShell", "hooks": [{"type": "command", "command": f'python "C:/x/{CD_GUARD}"'}]},
        {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "python \"C:/h/write_scope_guard.py\""},
                                            {"type": "command", "command": "bash keep.sh"}]},
    ], "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python ctx.py"}]}]}}), encoding="utf-8")
    assert guard.main(["--install"], root=workspace.parent, home=home) == 0
    table = home / ".atc" / "enforcement" / "pretooluse.json"
    assert table.is_file()
    settings = json.loads(claude.read_text(encoding="utf-8"))
    engine = (workspace.parent / "adapters" / "python" / "scripts" / "pretooluse_hook.py").as_posix()
    assert settings["model"] == "x" and settings["hooks"]["UserPromptSubmit"]
    assert settings["hooks"]["PreToolUse"] == [
        {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "bash keep.sh"}]},
        {"matcher": "Bash|PowerShell|Edit|Write|NotebookEdit", "hooks": [{
            "type": "command", "command": f'python "{engine}" --harness claude', "timeout": 10, "onFailure": "block"}]},
    ]
    codex = json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert codex == {"hooks": {"PreToolUse": [{"matcher": "Bash|apply_patch", "hooks": [{
        "type": "command", "command": f'python "{engine}" --harness codex', "timeout": 10}]}]}}
    live = ["--workspace", str(workspace), "--check", "--live-hook-table", str(table),
            "--live-claude-hooks", str(claude), "--live-codex-hooks", str(home / ".codex" / "hooks.json")]
    assert ge.main(live) == 0
    assert guard.main(["--install"], root=workspace.parent, home=home) == 0
    assert json.loads(claude.read_text(encoding="utf-8")) == settings
    assert guard.main([], root=workspace.parent, home=home) == 0
    assert doctor.hook_check(workspace, home).status == "READY"


def test_a_table_outside_the_default_directory_is_named_in_the_command(workspace, home):
    command = ge.engine_command(workspace, "codex", home / "elsewhere")
    assert command.endswith(f' --table "{(home / "elsewhere" / "pretooluse.json").as_posix()}"')


def test_drift_names_each_stale_part(workspace, home, capsys):
    claude = home / ".claude" / "settings.json"
    assert guard.main(["--install"], root=workspace.parent, home=home) == 0
    table = home / ".atc" / "enforcement" / "pretooluse.json"
    (workspace.parent / "policies" / "hard-deny.yaml").write_text(
        ROOT_POLICY.replace("[git, reset, --hard]", "[git, reset, --hard]\n    - id: gh-auth-token\n"
                            "      argv_prefix: [gh, auth, token]"), encoding="utf-8")
    settings = json.loads(claude.read_text(encoding="utf-8"))
    settings["hooks"]["PreToolUse"].append({"hooks": [{"type": "command", "command": "python write_scope_guard.py"}]})
    claude.write_text(json.dumps(settings), encoding="utf-8")
    capsys.readouterr()
    code = ge.main(["--workspace", str(workspace), "--check", "--live-hook-table", str(table),
                    "--live-claude-hooks", str(claude), "--live-codex-hooks", str(home / "missing.json")])
    out = capsys.readouterr().out
    assert code == 1
    for part in ("live-hook-table", "live-claude-hooks", "live-codex-hooks"):
        assert part in out
    check = doctor.hook_check(workspace, home)
    assert check.status == "ERROR" and "table" in check.detail and "claude hook entry" in check.detail


def test_doctor_reports_a_missing_table(workspace, home):
    assert doctor.hook_check(workspace, home).status == "NOT_INITIALIZED"


@pytest.mark.parametrize("args, message", [
    (["--install-hooks"], "unrecognized arguments: --install-hooks"),
    (["--workspace", "w.yaml", "--install-hooks", "--check"], "unrecognized arguments: --install-hooks"),
    (["--workspace", "w.yaml", "--live-hook-table", "t.json"], "require --check"),
    (["--policies", "p"], "go together"),
    ([], "are required unless"),
    (["--workspace", "w.yaml", "--check", "--live-claude", "s.json", "--live-hook-table", "t"], "require --policies"),
])
def test_usage_errors(args, message, capsys):
    with pytest.raises(SystemExit) as raised:
        ge.main(args)
    assert raised.value.code == 2 and message in capsys.readouterr().err


# The engine as the harness runs it

TABLE = {"version": 1, "commands": [{"id": "git-reset-hard", "argv_prefix": ["git", "reset", "--hard"]}],
         "filesystem": [], "worktrees": [], "write_roots": ["c:\\hami"], "protected": []}
CWD = "C:\\hami\\projects\\x"


def run(args: list[str], stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, hook.__file__, *args], input=stdin, capture_output=True, text=True,
                          encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=60)


def call(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": CWD})


def test_the_engine_prints_a_deny_and_allows_silently(tmp_path):
    path = tmp_path / "table.json"
    path.write_text(json.dumps(TABLE), encoding="utf-8")
    denied = run(["--table", str(path)], call("git reset --hard"))
    assert denied.returncode == 0
    assert json.loads(denied.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    allowed = run(["--harness=codex", "--table", str(path)], call("git status"))
    assert (allowed.returncode, allowed.stdout) == (0, "")


@pytest.mark.parametrize("table_text, stdin", [
    (None, call("git status")),
    ("{not json", call("git status")),
    (json.dumps({"version": 1, "commands": []}), call("git status")),
    (json.dumps(TABLE), "not json"),
    (json.dumps(TABLE), "[1, 2]"),
])
def test_the_engine_fails_closed(tmp_path, table_text, stdin):
    path = tmp_path / "table.json"
    if table_text is not None:
        path.write_text(table_text, encoding="utf-8")
    ran = run(["--table", str(path)], stdin)
    assert ran.returncode == 0
    reason = json.loads(ran.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "could not decide" in reason and "atc guard --install" in reason


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_helper_that_cannot_load_fails_closed(tmp_path, harness):
    scripts = Path(hook.__file__).parent
    for name in ge.ENGINE_FILES:
        (tmp_path / name).write_bytes((scripts / name).read_bytes())
    (tmp_path / "shell_words.py").write_text("raise ImportError('broken helper')\n", encoding="utf-8")
    ran = subprocess.run([sys.executable, str(tmp_path / ge.ENGINE), "--harness", harness], input=call("git status"),
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert ran.returncode == 0
    reason = json.loads(ran.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "could not decide" in reason and "broken helper" in reason


def test_an_unknown_engine_argument_fails_closed():
    ran = run(["--bogus"], "{}")
    assert "could not decide" in json.loads(ran.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def test_guard_refuses_a_missing_workspace_and_an_uninstalled_table(tmp_path, home, capsys):
    assert guard.main([], root=tmp_path, home=home) == 1
    assert capsys.readouterr().err.startswith("STOP WORKSPACE_MISSING: ")
    (tmp_path / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    assert guard.main([], root=tmp_path, home=home) == 1
    assert capsys.readouterr().err.startswith("STOP GUARD_NOT_INSTALLED: ")
    assert guard.main(["--json"], root=tmp_path, home=home) == 1
    assert json.loads(capsys.readouterr().out)["refusal"]["reason"] == "GUARD_NOT_INSTALLED"


def test_guard_install_writes_the_table_and_then_reports_current(workspace, home, capsys):
    assert guard.main(["--install", "--json"], root=workspace.parent, home=home) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verdict"] == "INSTALLED" and result["hook_table"].endswith("pretooluse.json")
    assert (home / ".atc" / "enforcement" / "pretooluse.json").is_file()
    assert guard.main([], root=workspace.parent, home=home) == 0
    assert capsys.readouterr().out == "CURRENT guard\n"

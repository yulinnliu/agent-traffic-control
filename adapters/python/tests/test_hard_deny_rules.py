"""The workspace's hard-deny command rules, composed from the real policies/hard-deny.yaml as the hook runs them.

Each rule is pinned by a refused form and by the forms its unless_args or with_args let through. A rule deleted from
the policy, or an unless_args loosened, fails the cases that name it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import generate_enforcement as ge
import pretooluse_hook as hook

pytestmark = pytest.mark.integration

POLICY = Path(__file__).resolve().parents[3] / "policies" / "hard-deny.yaml"
CWD = "C:\\hami\\projects\\x"


@pytest.fixture
def table(tmp_path, monkeypatch) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "ws"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "hard-deny.yaml").write_text(POLICY.read_text(encoding="utf-8"), encoding="utf-8")
    workspace = root / "workspace.yaml"
    workspace.write_text(f"version: 1\nworkspace_root: {root.as_posix()}\nprojects: {{}}\n", encoding="utf-8")
    return json.loads(ge.hook_table(workspace, home / ".atc" / "enforcement"))


def verdict(table: dict, command: str, tool: str = "Bash", background: bool = False) -> str | None:
    payload = {"tool_name": tool, "tool_input": {"command": command, "run_in_background": background}, "cwd": CWD}
    return hook.decide(payload, table, "claude")


REFUSED = [
    ("atc-integrate", "Bash", "atc integrate ATC-0001", False),
    ("atc-integrate", "PowerShell", "atc integrate ATC-0001 --sha abc", False),
    ("atc-integrate", "Bash", "uv run --locked --directory adapters/python atc integrate ATC-0001", False),
    ("atc-push", "Bash", "atc push root", False),
    ("atc-push", "PowerShell", "atc push --all", False),
    ("atc-push", "Bash", "atc push", False),
    ("atc-push", "Bash", "atc push root --json", False),
    ("atc-remove", "Bash", "atc remove sandbox", False),
    ("atc-remove", "PowerShell", "atc remove sandbox --json", False),
    ("atc-guard-install", "Bash", "atc guard --install", False),
    ("atc-guard-install", "PowerShell", "atc guard --install --json", False),
    ("atc-test-background", "Bash", "atc test ATC-0001 &", False),
    ("atc-test-background", "PowerShell", "atc test ATC-0001 --all", True),
    ("atc-maintain-guard-hook-out", "Bash", "uv run --locked atc-maintain guard --hook-out", False),
]

ALLOWED = [
    ("Bash", "atc integrate --help", False),
    ("Bash", "atc integrate -h", False),
    ("Bash", "atc integrate ATC-0001 --check", False),
    ("Bash", "atc integrate ATC-0001 --check --json", False),
    ("Bash", "atc push root --check", False),
    ("PowerShell", "atc push --all --check", False),
    ("Bash", "atc push --help", False),
    ("Bash", "atc push -h", False),
    ("Bash", "atc remove sandbox --check", False),
    ("Bash", "atc remove --help", False),
    ("Bash", "atc guard", False),
    ("Bash", "atc guard --json", False),
    ("Bash", "atc guard --help", False),
    ("Bash", "atc test ATC-0001", False),
    ("Bash", "atc test ATC-0001 --all", False),
    ("PowerShell", "atc test ATC-0001 -- -q", False),
    ("PowerShell", "atc test ATC-0001 --all", False),
    ("Bash", "atc-maintain guard --check", False),
    ("Bash", "uv run --locked atc-maintain guard --check --workspace ws.yaml", False),
]


@pytest.mark.parametrize("rule, tool, command, background", REFUSED)
def test_a_hard_deny_form_names_its_rule(table, rule, tool, command, background):
    reason = verdict(table, command, tool, background)
    assert reason is not None and f"`{rule}`" in reason


@pytest.mark.parametrize("tool, command, background", ALLOWED)
def test_an_allowed_form_passes(table, tool, command, background):
    assert verdict(table, command, tool, background) is None


@pytest.mark.parametrize("command", [
    "atc --root . push root",
    "atc --root=. push root",
    "atc --root . remove sandbox",
    "atc --root . guard --install",
    "atc --root . integrate ATC-0001",
    "atc push --root . root",
    "atc push root --root --check",
    "atc integrate ATC-0001 --root=--check",
    "atc remove --root --check sandbox",
])
def test_a_global_root_option_does_not_hide_the_subcommand(table, command):
    """atc removes every `--root DIR` before `--` (atc_cli.take_root): neither the subcommand nor a `--check` that
    is only the directory's value may slip past the rule."""
    reason = verdict(table, command)
    assert reason is not None and "hard-deny rule" in reason


def test_a_global_root_option_keeps_the_check_form_allowed(table):
    assert verdict(table, "atc --root . push root --check") is None

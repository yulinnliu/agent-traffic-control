"""Generator behaviour outside the conformance cases: the Claude Code mapping of filesystem modes and state globs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "atc_cli" / "scripts"))
import generate_enforcement

pytestmark = pytest.mark.unit


def test_claude_renders_only_read_and_edit_for_every_mode_combination():
    hard = {"filesystem": {"deny": [
        {"glob": "all/**", "modes": ["read", "edit", "write"]},
        {"glob": "wr/**", "modes": ["write", "edit"]},
        {"glob": "w/**", "modes": ["write"]},
        {"glob": "r/**", "modes": ["read"]},
    ]}}
    deny = json.loads(generate_enforcement.claude_settings(hard))["permissions"]["deny"]
    assert deny == ["Read(all/**)", "Edit(all/**)", "Edit(wr/**)", "Edit(w/**)", "Read(r/**)"]


def test_a_state_glob_reaches_no_committed_output_but_the_hooks_comment():
    hard = {"filesystem": {"deny": [{"glob": "${state}/demo/**", "modes": ["read", "write"]}, "**/.env"]}}
    deny = json.loads(generate_enforcement.claude_settings(hard))["permissions"]["deny"]
    assert deny == ["Read(**/.env)", "Edit(**/.env)"]
    hook = generate_enforcement.pre_commit_hook(hard)
    assert "#   ${state}/demo/**  [read, write]" in hook and "state" not in hook.split("hit=")[1]

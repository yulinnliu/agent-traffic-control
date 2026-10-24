"""The generated Codex rules load in the installed Codex CLI and forbid each denied prefix."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CASE = Path(__file__).resolve().parents[3] / "shared/conformance/generate-enforcement/example-policies"
CODEX = shutil.which("codex")

pytestmark = pytest.mark.skipif(CODEX is None, reason="Codex CLI not installed")


def _check(argv: list[str]) -> subprocess.CompletedProcess:
    rules = CASE / "expected/codex/hard-deny.rules"
    return subprocess.run([CODEX, "execpolicy", "check", "--rules", str(rules), *argv],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_every_denied_prefix_is_forbidden():
    policy = yaml.safe_load((CASE / "input/hard-deny.yaml").read_text(encoding="utf-8"))
    for command in policy["commands"]["deny"]:
        result = _check([*command["argv_prefix"], "extra-arg"])
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["decision"] == "forbidden", command["id"]


def test_unlisted_command_parses_without_a_decision():
    result = _check(["git", "status"])
    assert result.returncode == 0, result.stderr
    assert "decision" not in json.loads(result.stdout)

#!/usr/bin/env python3
"""Claude Code PreToolUse hook: refuse a top-level directory change into the worktrees root.

The installed hook until `atc enforce --install-hooks` replaces it with the composed PreToolUse hook, which
applies the same rule from its table. The lexers and the refusal text live in `shell_words.py` and are
re-exported here for the callers that load this file by path. Anything it cannot parse is allowed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shell_words import *  # noqa: E402,F401,F403  (re-exported for callers loading this file by path)
from shell_words import bash_targets, native, pwsh_targets, within  # noqa: E402
from shell_words import cd_reason as reason  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
WORKTREES = ROOT / "worktrees"


def blocked(tool: str, command: str, cwd: str, worktrees: Path = WORKTREES) -> str | None:
    """The first location-change target under `worktrees`, or None."""
    targets = bash_targets(command) if tool == "Bash" else pwsh_targets(command) if tool == "PowerShell" else []
    root = native(str(worktrees), cwd)
    for target in targets:
        if within(native(target, cwd), root):
            return target
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_name", "")
        command = payload.get("tool_input", {}).get("command", "")
        cwd = payload.get("cwd") or os.getcwd()
        target = blocked(tool, command, cwd)
    except Exception:
        return 0
    if target is not None:
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason(tool, target),
            }
        }, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())

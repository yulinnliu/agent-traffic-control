#!/usr/bin/env python3
"""`atc guard`: check, or with `--install` write, the PreToolUse hook's table and its entries in the Claude Code
and Codex settings (docs/ENFORCEMENT.md § PreToolUse hook).

The table composes the workspace policy, every registered project's policy and the workspace map. The
generator's other outputs are `atc-maintain guard`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

import cli_conventions
import generate_enforcement as ge
import policies

ROOT = Path(__file__).resolve().parents[3]
INSTALL_HINT = "the Owner runs atc guard --install from their own terminal"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc guard", allow_abbrev=False, **cli_conventions.form(
        "Check that the installed PreToolUse hook table and its harness entries match the policies.",
        "--install writes the table to ~/.atc/enforcement and the hook entries of ~/.claude/settings.json and\n"
        "~/.codex/hooks.json, replacing the retired guards' entries.",
        exit="0 current, or installed; 1 STOP (not installed, drift, a policy problem); 2 ERROR",
        see="docs/ENFORCEMENT.md § PreToolUse hook"))
    parser.add_argument("--install", action="store_true", help="write the table and the hook entries (the Owner's)")
    cli_conventions.add_json(parser)
    return parser


def main(argv: list[str] | None = None, root: Path = ROOT, home: Path | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = home or Path.home()
    workspace = root / "workspace.yaml"
    if not workspace.is_file():
        return cli_conventions.refuse(cli_conventions.Refusal("WORKSPACE_MISSING", f"{workspace} is missing",
                                                              "atc init"), args.json)
    if args.install:
        return install(workspace, home, args.json)
    found = ge.hook_drift(workspace, home)
    if found is None:
        return cli_conventions.refuse(cli_conventions.Refusal(
            "GUARD_NOT_INSTALLED", f"no hook table at {home / '.atc' / 'enforcement' / ge.HOOK_TABLE}",
            INSTALL_HINT), args.json)
    if found:
        return cli_conventions.refuse(cli_conventions.Refusal("GUARD_DRIFT", "; ".join(found), INSTALL_HINT),
                                      args.json, drift=found)
    print(json.dumps({"verdict": "CURRENT", "drift": []}, indent=2) if args.json else "CURRENT guard")
    return cli_conventions.OK


def install(workspace: Path, home: Path, as_json: bool) -> int:
    table_dir = home / ".atc" / "enforcement"
    try:
        table = ge.hook_table(workspace, table_dir)
        groups = {h: ge.hook_group(h, ge.engine_command(workspace, h, table_dir)) for h in ("claude", "codex")}
    except policies.PolicyError as exc:
        return cli_conventions.refuse(cli_conventions.Refusal(
            "POLICY_INVALID", "; ".join(exc.problems), "fix the policy files it names"), as_json)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return cli_conventions.refuse(cli_conventions.Refusal("WORKSPACE_INVALID", str(exc), "fix workspace.yaml"),
                                      as_json)
    target = table_dir / ge.HOOK_TABLE
    target.parent.mkdir(parents=True, exist_ok=True)
    ge.write_if_changed(target, table)
    installed = []
    for harness, path in (("claude", home / ".claude" / "settings.json"), ("codex", home / ".codex" / "hooks.json")):
        try:
            ge.install_hook(path, groups[harness])
        except (OSError, ValueError) as exc:
            return cli_conventions.refuse(cli_conventions.Refusal(
                "INSTALL_FAILED", f"{path}: {exc}", "fix the settings file, then run it again",
                cli_conventions.ERROR), as_json)
        installed.append(str(path))
    if as_json:
        print(json.dumps({"verdict": "INSTALLED", "hook_table": str(target), "installed": installed}, indent=2))
    else:
        print(f"INSTALLED guard (table and {len(installed)} hook entries; Codex asks to trust the hook once in /hooks)")
    return cli_conventions.OK


if __name__ == "__main__":
    sys.exit(main())

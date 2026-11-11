"""Exit codes, the `--json` envelope and the shared options of every `atc` command (shared/conformance/CLI.md)."""

from __future__ import annotations

import argparse
import sys

OK = 0
STOP = 1
ERROR = 2
PARTIAL = 3
EXIT_NAMES = {OK: "OK", STOP: "STOP", ERROR: "ERROR", PARTIAL: "PARTIAL", 8: "GUARD", 9: "REFUSED",
              124: "TIMEOUT", 130: "INTERRUPTED"}

PROJECT_HELP = "registered project name from workspace.yaml; root names the workspace root"
JSON_HELP = "print the result as one JSON object (shared/conformance/CLI.md § Output)"
NEXT_RELEASE = "0.4.0"


def form(summary: str, detail: str | None = None, exit: str | None = None, see: str | None = None) -> dict:
    """The keyword arguments of one help page: summary, optional detail, then `Exit:` and `See:` lines (CLI.md § Help)."""
    tail = "\n".join(line for line in (exit and f"Exit: {exit}", see and f"See: {see}") if line)
    return {"description": summary + (f"\n\n{detail}" if detail else ""), "epilog": tail or None,
            "formatter_class": argparse.RawDescriptionHelpFormatter}


def add_project(parser, extra: str = "", help: str | None = None, **kwargs) -> argparse.Action:
    return parser.add_argument("--project", metavar="NAME", help=(help or PROJECT_HELP) + extra, **kwargs)


def add_json(parser: argparse.ArgumentParser) -> argparse.Action:
    return parser.add_argument("--json", action="store_true", help=JSON_HELP)


def deprecated(old: str, new: str) -> None:
    """One stderr warning per use of a deprecated form; the exit code and `--json` stay unchanged."""
    print(f"WARNING DEPRECATED {old}: use {new}; removed in atc {NEXT_RELEASE}", file=sys.stderr)


def envelope(command: str, exit_code: int, payload: object, list_key: str = "items") -> dict:
    """The `--json` object: `command`, `verdict`, `exit_code`, `next`, then the command's own fields."""
    fields = dict(payload) if isinstance(payload, dict) else {list_key: payload}
    refusal = fields.get("refusal") if isinstance(fields.get("refusal"), dict) else {}
    verdict = fields.get("verdict") if isinstance(fields.get("verdict"), str) else refusal.get("verdict")
    following = fields.get("next") if isinstance(fields.get("next"), str) else refusal.get("next")
    for key in ("command", "verdict", "exit_code", "next"):
        fields.pop(key, None)
    return {"command": command, "verdict": verdict or EXIT_NAMES.get(exit_code, str(exit_code)),
            "exit_code": exit_code, "next": following, **fields}

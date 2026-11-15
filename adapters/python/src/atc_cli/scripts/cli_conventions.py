"""Exit codes, the output rule, the `--json` envelope and the shared options of every `atc` command
(shared/conformance/CLI.md)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

OK = 0
STOP = 1
ERROR = 2
PARTIAL = 3
EXIT_NAMES = {OK: "OK", STOP: "STOP", ERROR: "ERROR", PARTIAL: "PARTIAL", 8: "GUARD", 9: "REFUSED",
              124: "TIMEOUT", 130: "INTERRUPTED"}

PROJECT_HELP = "registered project name from workspace.yaml; root names the workspace root"
JSON_HELP = "print the result as one JSON object (shared/conformance/CLI.md § Output)"
VERBOSE_HELP = "add detail lines after the verdict"


class Refusal(Exception):
    """A STOP or ERROR with its reason, detail and the concrete fix (CLI.md § Output)."""

    def __init__(self, token: str, detail: str, hint: str | None = None, code: int = STOP) -> None:
        super().__init__(f"{token} {detail}")
        self.token, self.detail, self.hint, self.code = token, detail, hint, code

    def lines(self) -> list[str]:
        head = f"ERROR {self.token} {self.detail}" if self.code == ERROR else f"STOP {self.token}: {self.detail}"
        return [head, *([f"hint: {self.hint}"] if self.hint else [])]

    def as_dict(self) -> dict:
        return {"verdict": EXIT_NAMES.get(self.code, str(self.code)), "reason": self.token, "detail": self.detail,
                "hint": self.hint}


def form(summary: str, detail: str | None = None, *, exit: str, see: str) -> dict:
    """The keyword arguments of one help page: summary, optional detail, then `Exit:` and `See:` lines (CLI.md § Help)."""
    return {"description": summary + (f"\n\n{detail}" if detail else ""), "epilog": f"Exit: {exit}\nSee: {see}",
            "formatter_class": argparse.RawDescriptionHelpFormatter}


def add_project(parser, extra: str = "", help: str | None = None, **kwargs) -> argparse.Action:
    return parser.add_argument("--project", metavar="NAME", help=(help or PROJECT_HELP) + extra, **kwargs)


def add_json(parser: argparse.ArgumentParser) -> argparse.Action:
    return parser.add_argument("--json", action="store_true", help=JSON_HELP)


def add_verbose(parser: argparse.ArgumentParser) -> argparse.Action:
    return parser.add_argument("-v", "--verbose", action="store_true", help=VERBOSE_HELP)


def refuse(refusal: Refusal, as_json: bool = False, **fields) -> int:
    """Print a refusal: its reason line and `hint:` on stderr, and under ``as_json`` the result object on stdout."""
    print("\n".join(refusal.lines()), file=sys.stderr)
    if as_json:
        print(json.dumps({"verdict": EXIT_NAMES.get(refusal.code, str(refusal.code)), "hint": refusal.hint,
                          **fields, "refusal": refusal.as_dict()}, indent=2))
    return refusal.code


def envelope(command: str, exit_code: int, payload: object, list_key: str = "items") -> dict:
    """The `--json` object: `command`, `verdict`, `exit_code`, `hint`, then the command's own fields."""
    fields = dict(payload) if isinstance(payload, dict) else {list_key: payload}
    refusal = fields.get("refusal") if isinstance(fields.get("refusal"), dict) else {}
    verdict = fields.get("verdict") if isinstance(fields.get("verdict"), str) else refusal.get("verdict")
    hint = fields.get("hint") if isinstance(fields.get("hint"), str) else refusal.get("hint")
    for key in ("command", "verdict", "exit_code", "hint"):
        fields.pop(key, None)
    return {"command": command, "verdict": verdict or EXIT_NAMES.get(exit_code, str(exit_code)),
            "exit_code": exit_code, "hint": hint, **fields}


def relative(path: str | os.PathLike[str], base: str | os.PathLike[str]) -> str:
    """``path`` relative to ``base`` with forward slashes when it lies under it, else as given."""
    try:
        return Path(os.path.realpath(path)).relative_to(os.path.realpath(base)).as_posix() or "."
    except ValueError:
        return str(path)

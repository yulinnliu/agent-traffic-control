"""usage: uv run --locked atc-maintain <command> [<args>]

Maintainer commands of this ATC checkout, run only from a worktree of it (never the installed atc):

  sync --root     render the managed skill copies of this checkout from shared/skills
  validate        check this checkout's contract: workspace.yaml.example, skills, records
  policies check  check a policy directory against shared/policies/schemas
  task check      check the task records and declaration of a working tree
  guard           generate the harness deny configs and hook table from policies

Exit codes and output: shared/conformance/CLI.md. Run `atc-maintain <command> --help` for one command.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from atc_cli import (CHECKOUT, SCRIPTS, command_name, enveloped, escape_unencodable_output, scripts_on_path,
                     wants_json)

COMMANDS = {"sync": "sync_project", "validate": "validate_workspace", "policies": "policies",
            "task": "task_records", "guard": "generate_enforcement"}
GROUPED = ("policies", "task")


def usage() -> str:
    return __doc__.strip()


def checkout_of(cwd: Path) -> Path:
    """The ATC checkout holding ``cwd``, else the one serving this program."""
    for candidate in (cwd.resolve(), *cwd.resolve().parents):
        if (candidate / "adapters" / "python" / "scripts" / "validate_workspace.py").is_file():
            return candidate
    return CHECKOUT


def refused(checkout: Path) -> str | None:
    """Why this program may not run here: the scripts are absent (a published package), or ``checkout`` is a
    canonical one, whose installed `atc` serves every session."""
    if not SCRIPTS.is_dir():
        return "this installation carries no maintainer scripts"
    scripts_on_path()
    import checkout_guard
    if checkout_guard.state(checkout).canonical or checkout_guard.state(CHECKOUT).canonical:
        return f"{checkout} is the canonical checkout"
    return None


def main(argv: list[str] | None = None) -> int:
    escape_unencodable_output()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    command, rest = args[0], args[1:]
    if command not in COMMANDS:
        print(f"usage: atc-maintain <command> [<args>]\natc-maintain: error: unknown command: {command}",
              file=sys.stderr)
        return 2
    checkout = checkout_of(Path.cwd())
    if not any(arg in ("-h", "--help") for arg in rest) and (why := refused(checkout)):
        print(f"ERROR CHECKOUT_CANONICAL {why}", file=sys.stderr)
        print("hint: run it as `uv run --locked atc-maintain` from adapters/python of an ATC worktree "
              "(root AGENTS.md § Changing this repo)", file=sys.stderr)
        return 2
    program = sys.argv[0]
    sys.argv[0] = f"atc-maintain {command}"
    try:
        if wants_json(rest):
            return enveloped(command_name("atc-maintain", command, rest, GROUPED),
                             lambda: dispatch(command, rest, checkout))
        return dispatch(command, rest, checkout)
    finally:
        sys.argv[0] = program


def dispatch(command: str, rest: list[str], checkout: Path) -> int:
    scripts_on_path()
    module = __import__(COMMANDS[command])
    if command == "sync":
        return module.root_main(rest, root=checkout)
    if command == "validate":
        return module.main([*rest, "--root", str(checkout)])
    if command == "task":
        if rest[:1] != ["check"]:
            print("usage: atc-maintain task check [DIR] [--json]", file=sys.stderr)
            return 2
        return module.check_main(rest[1:])
    if command == "guard":
        workspace = checkout / "workspace.yaml"
        if workspace.is_file() and not any(a.split("=", 1)[0] == "--workspace" for a in rest):
            rest = [*rest, "--workspace", str(workspace)]
        return module.main(rest)
    return module.main(rest)


if __name__ == "__main__":
    sys.exit(main())

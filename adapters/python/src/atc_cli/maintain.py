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

from atc_cli import command_name, enveloped, escape_unencodable_output, scripts_on_path, wants_json

COMMANDS = {"sync": "sync_project", "validate": "validate_workspace", "policies": "policies",
            "task": "task_records", "guard": "generate_enforcement"}
GROUPED = ("policies", "task")


def usage() -> str:
    return __doc__.strip()


def code_checkout() -> Path | None:
    """The ATC checkout this program runs from: the checkout of its editable install's `adapters/python`, else
    None (an install from a tag or a wheel)."""
    scripts_on_path()
    import release_pin
    source = release_pin.editable()
    return source.parents[1] if source is not None else None


def checkout_of(cwd: Path, code: Path | None) -> Path | None:
    """The ATC checkout holding ``cwd``, else the one serving this program."""
    for candidate in (cwd.resolve(), *cwd.resolve().parents):
        if (candidate / "adapters" / "python" / "src" / "atc_cli" / "scripts" / "validate_workspace.py").is_file():
            return candidate
    return code


def refused(checkout: Path | None, code: Path | None) -> str | None:
    """Why this program may not run here: it is not an editable install of an ATC checkout (the installed
    `atc`), or ``checkout`` or the checkout it runs from is a canonical one, which every session reads as the
    workspace."""
    if code is None or checkout is None:
        return "this atc-maintain is not an editable install of an ATC worktree"
    scripts_on_path()
    import checkout_guard
    for candidate in (checkout, code):
        if checkout_guard.state(candidate).canonical:
            return f"{candidate} is the canonical checkout"
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
    code = code_checkout()
    checkout = checkout_of(Path.cwd(), code)
    if not any(arg in ("-h", "--help") for arg in rest) and (why := refused(checkout, code)):
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

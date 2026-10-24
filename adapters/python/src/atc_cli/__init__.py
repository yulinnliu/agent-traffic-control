"""`atc`: one command in front of the Python reference tools.

    atc [--root DIR] <command> [args...]

Commands map onto the scripts under `adapters/python/scripts/`:

    worktree   hami_worktree.py   create / resume / audit / cleanup
    preflight  git_preflight.py   merge / cleanup verdicts
    validate   validate_workspace.py
    onboard    onboard_project.py
    bootstrap  bootstrap.py       init / add / sync / status
    doctor     doctor.py
    policies   policies.py        check DIR
    enforce    generate_enforcement.py
    classify   classify_change.py
    friction   friction.py

The checkout root is, in order: `--root`, `$ATC_ROOT`, the nearest parent of
the working directory holding `workspace.yaml`, else the checkout this
package was installed from. Skills call `atc` by name, so a repository that
uses them needs nothing but this command on PATH.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
CHECKOUT = SCRIPTS.parents[1]
COMMANDS = {
    "worktree": "hami_worktree",
    "preflight": "git_preflight",
    "validate": "validate_workspace",
    "onboard": "onboard_project",
    "bootstrap": "bootstrap",
    "doctor": "doctor",
    "policies": "policies",
    "enforce": "generate_enforcement",
    "classify": "classify_change",
    "friction": "friction",
}


def resolve_root(explicit: str | None, environ: dict[str, str], cwd: Path) -> Path:
    if explicit:
        return Path(explicit).resolve()
    if environ.get("ATC_ROOT"):
        return Path(environ["ATC_ROOT"]).resolve()
    for candidate in (cwd.resolve(), *cwd.resolve().parents):
        if (candidate / "workspace.yaml").is_file() and (candidate / "shared" / "governance").is_dir():
            return candidate
    return CHECKOUT


def usage() -> str:
    return __doc__.strip()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    explicit_root = None
    if args[:1] == ["--root"]:
        if len(args) < 2:
            print("--root needs a directory", file=sys.stderr)
            return 2
        explicit_root, args = args[1], args[2:]
    elif args and args[0].startswith("--root="):
        explicit_root, args = args[0].split("=", 1)[1], args[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    command, rest = args[0], args[1:]
    if command not in COMMANDS:
        print(f"unknown command: {command}\n\n{usage()}", file=sys.stderr)
        return 2
    root = resolve_root(explicit_root, dict(os.environ), Path.cwd())
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    module = __import__(COMMANDS[command])
    if command in ("worktree", "onboard", "bootstrap"):
        return module.main(rest, root=root)
    if command == "doctor":
        return module.main(root)
    if command == "validate" and "--root" not in rest and not any(a.startswith("--root=") for a in rest):
        rest = [*rest, "--root", str(root)]
    return module.main(rest)

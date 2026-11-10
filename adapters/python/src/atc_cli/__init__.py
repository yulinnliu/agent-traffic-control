"""`atc`: one command in front of the Python reference tools.

    atc [--root DIR] <command> [args...]

Commands map onto the scripts under `adapters/python/scripts/`:

    worktree   hami_worktree.py   create / resume / audit / cleanup
    preflight  git_preflight.py   merge / cleanup / push verdicts
    integrate  git_integrate.py   advance local main to a reviewed topic
    task       task_records.py    new / list / check task records
    close-out  close_out.py       remove an integrated task's worktree, scratches, reports
    validate   validate_workspace.py
    onboard    onboard_project.py
    bootstrap  bootstrap.py       init / add / sync / status
    doctor     doctor.py
    policies   policies.py        check DIR
    enforce    generate_enforcement.py   (+ --workspace <root>/workspace.yaml when present)
    classify   classify_change.py
    friction   friction.py
    test       atc_testkit            run / mutants
    tmp        atc_testkit            checkout / list / clean / prune

The checkout root is, in order: `--root`, `$ATC_ROOT`, the nearest parent of
the working directory holding `workspace.yaml`, else the checkout this
package was installed from. `onboard --root-only` renders the ATC checkout
holding the working directory, which in a linked worktree has no
`workspace.yaml`; `--root` and `$ATC_ROOT` still win. `integrate` resolves
`--project` and the working directory's project in that root's workspace.yaml and refuses an
explicit `--root` beside `--repo`. Skills call `atc` by name, so a repository that
uses them needs nothing but this command on PATH.

When this package runs from the canonical checkout (it holds `workspace.yaml`
and is no linked worktree), every command but `doctor` and `--help` stops with
`ERROR CHECKOUT_NOT_MAIN` while that checkout is off `main`, and warns with
`WARNING CHECKOUT_DIRTY` while it has tracked modifications;
`onboard --root-only` stops with `ERROR CHECKOUT_CANONICAL` when the checkout
it would render is the canonical one (root `AGENTS.md` § Changing this repo).

`atc <command> --help` describes one command. A module the installed `atc`
lacks stops the command with `ERROR MISSING_DEPENDENCY` and the reinstall
command of the root's AGENTS.md.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
CHECKOUT = SCRIPTS.parents[2]
COMMANDS = {
    "worktree": "hami_worktree",
    "preflight": "git_preflight",
    "integrate": "git_integrate",
    "task": "task_records",
    "close-out": "close_out",
    "validate": "validate_workspace",
    "onboard": "onboard_project",
    "bootstrap": "bootstrap",
    "doctor": "doctor",
    "policies": "policies",
    "enforce": "generate_enforcement",
    "classify": "classify_change",
    "friction": "friction",
}
TESTKIT_COMMANDS = ("test", "tmp")
GUARD_EXEMPT = ("doctor",)
GUARDED_CHECKOUT = CHECKOUT


def resolve_root(explicit: str | None, environ: dict[str, str], cwd: Path) -> Path:
    if explicit:
        return Path(explicit).resolve()
    if environ.get("ATC_ROOT"):
        return Path(environ["ATC_ROOT"]).resolve()
    for candidate in (cwd.resolve(), *cwd.resolve().parents):
        if (candidate / "workspace.yaml").is_file() and (candidate / "shared" / "governance").is_dir():
            return candidate
    return CHECKOUT


def resolve_checkout(explicit: str | None, environ: dict[str, str], cwd: Path) -> Path:
    """The ATC checkout holding ``cwd``, with or without ``workspace.yaml``: what `onboard --root-only` renders."""
    if explicit or environ.get("ATC_ROOT"):
        return resolve_root(explicit, environ, cwd)
    for candidate in (cwd.resolve(), *cwd.resolve().parents):
        if (candidate / "adapters" / "python" / "scripts" / "onboard_project.py").is_file():
            return candidate
    return resolve_root(explicit, environ, cwd)


def usage() -> str:
    return __doc__.strip()


def reinstall_command() -> str:
    return f'uv tool install -e "{SCRIPTS.parent}" --force'


def _escape_unencodable_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")


def _child_encoding() -> None:
    """Every child `atc` starts writes UTF-8 to its pipes (docs/TASK_CONTRACT_PLAN.md D.9); tests pass `argv`."""
    from atc_testkit.isolation import CHILD_ENCODING
    os.environ[CHILD_ENCODING[0]] = CHILD_ENCODING[1]


def _scripts_on_path() -> None:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))


def _guard(command: str, rest: list[str], checkout: Path) -> int | None:
    """Exit 2 when the canonical checkout serving this `atc` is off `main`; warn when it is dirty."""
    if command in GUARD_EXEMPT or any(arg in ("-h", "--help") for arg in rest):
        return None
    _scripts_on_path()
    import checkout_guard
    current = checkout_guard.state(checkout)
    if current.unreadable:
        print(f"ERROR CHECKOUT_UNREADABLE {checkout} holds workspace.yaml but its Git state cannot be read "
              f"({current.unreadable}); atc cannot confirm it is on main", file=sys.stderr)
        return 2
    if current.off_main:
        print(f"ERROR CHECKOUT_NOT_MAIN {checkout_guard.describe(checkout, current)}; every atc command would run "
              f"that branch's code. Switch it back to main and keep topics in worktrees/root "
              f"(root AGENTS.md § Changing this repo)", file=sys.stderr)
        return 2
    if current.dirty:
        print(f"WARNING CHECKOUT_DIRTY {checkout} has tracked modifications; atc runs them", file=sys.stderr)
    return None


def main(argv: list[str] | None = None, checkout: Path | None = None) -> int:
    """``checkout`` is the checkout serving this `atc` for the guard; tests name one."""
    _escape_unencodable_output()
    if argv is None:
        _child_encoding()
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
    if command not in COMMANDS and command not in TESTKIT_COMMANDS:
        print(f"unknown command: {command}\n\n{usage()}", file=sys.stderr)
        return 2
    refused = _guard(command, rest, GUARDED_CHECKOUT if checkout is None else checkout)
    if refused is not None:
        return refused
    program = sys.argv[0]
    sys.argv[0] = f"atc {command}"
    try:
        return _dispatch(command, rest, explicit_root)
    except ModuleNotFoundError as error:
        print(f"ERROR MISSING_DEPENDENCY atc {command} needs the module {error.name!r}, which this "
              f"installation lacks; reinstall it with: {reinstall_command()}", file=sys.stderr)
        return 2
    finally:
        sys.argv[0] = program


def _dispatch(command: str, rest: list[str], explicit_root: str | None) -> int:
    """Run one command; ``sys.argv[0]`` is ``atc <command>``, the program name argparse shows."""
    if command in TESTKIT_COMMANDS:
        from atc_testkit import commands
        return commands.main([command, *rest])
    _scripts_on_path()
    if command == "onboard" and "--root-only" in rest:
        root = resolve_checkout(explicit_root, dict(os.environ), Path.cwd())
        import checkout_guard
        if checkout_guard.state(root).canonical:
            print(f"ERROR CHECKOUT_CANONICAL {root} is the canonical checkout; render managed copies in the topic's "
                  f"worktree under worktrees/root and commit them there (root AGENTS.md § Changing this repo)",
                  file=sys.stderr)
            return 2
    else:
        root = resolve_root(explicit_root, dict(os.environ), Path.cwd())
    module = __import__(COMMANDS[command])
    if command in ("worktree", "task", "onboard", "bootstrap", "close-out"):
        return module.main(rest, root=root)
    if command == "integrate":
        return module.main(rest, root=root, root_given=explicit_root is not None)
    if command == "doctor":
        if rest[:1] in (["-h"], ["--help"]):
            print(f"usage: atc doctor\n\n{module.__doc__.strip()}")
            return 0
        return module.main(root)
    if command == "validate" and "--root" not in rest and not any(a.startswith("--root=") for a in rest):
        rest = [*rest, "--root", str(root)]
    if command == "enforce" and (root / "workspace.yaml").is_file() and not any(a.split("=", 1)[0] == "--workspace" for a in rest):
        rest = [*rest, "--workspace", str(root / "workspace.yaml")]
    return module.main(rest)

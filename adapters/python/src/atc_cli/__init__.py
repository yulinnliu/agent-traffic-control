"""usage: atc [--root DIR] <command> [<args>]
       atc --version

Agent Traffic Control: governed task lifecycle for agent-written changes.

Task lifecycle:
  task        new / list / check task records
  worktree    create / resume / audit / clean a topic worktree
  verify      run the verifier on the candidate integrate would build
  integrate   fast-forward main to a reviewed topic
  close-out   remove an integrated task's worktree, scratches and reports
  preflight   list what a push of main would carry

Testing:
  test        run / mutants / private: isolated pytest runs
  tmp         checkout / list / clean / prune scratch checkouts

Workspace:
  bootstrap   init / add / status: set up a workspace root and its projects
  onboard     check or sync the files ATC owns in projects
  doctor      diagnose runtime readiness; changes nothing
  validate    check this checkout's contract (and its projects')

Policy:
  policies    check a policy directory
  enforce     generate harness deny configs from hard-deny.yaml
  classify    classify a change LOW or HIGH

Log:
  friction    add / list / summary / resolve friction incidents

--root DIR    the ATC checkout to act on, before or after the command
              (shared/conformance/CLI.md § Root resolution)
Exit codes, --json and naming: shared/conformance/CLI.md
Run `atc <command> --help` for one command.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
CHECKOUT = SCRIPTS.parents[2]
COMMANDS = {
    "worktree": "managed_worktree",
    "preflight": "git_preflight",
    "integrate": "git_integrate",
    "verify": "git_integrate",
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
GROUPED = ("worktree", "preflight", "task", "bootstrap", "policies", "friction", "test", "tmp")
LIST_KEYS = {"atc tmp list": "scratches"}
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
    """Every child `atc` starts writes UTF-8 to its pipes (docs/TESTKIT.md § Isolated environment); tests pass `argv`."""
    from atc_testkit.isolation import CHILD_ENCODING
    os.environ[CHILD_ENCODING[0]] = CHILD_ENCODING[1]


def _usage_error(parser: argparse.ArgumentParser, message: str):
    """argparse's usage line and error, then the command that prints the whole help (GIT_WORKFLOW.md § Command results)."""
    parser.print_usage(sys.stderr)
    print(f"{parser.prog}: error: {message}", file=sys.stderr)
    print(f"next: {parser.prog} --help", file=sys.stderr)
    raise SystemExit(2)


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


def version() -> str:
    from importlib import metadata

    from atc_testkit import __version__ as testkit
    try:
        atc = metadata.version("agent-traffic-control")
    except metadata.PackageNotFoundError:
        atc = "unknown"
    return f"atc {atc} (atc-testkit {testkit})"


def _take_root(args: list[str]) -> tuple[str | None, list[str], str | None]:
    """Remove every `--root DIR` / `--root=DIR` before a `--`; the last one wins. Returns (root, rest, error)."""
    explicit, rest, index = None, [], 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            rest.extend(args[index:])
            break
        if arg == "--root":
            if index + 1 >= len(args):
                return None, args, "--root needs a directory"
            explicit, index = args[index + 1], index + 2
            continue
        if arg.startswith("--root="):
            explicit = arg.split("=", 1)[1]
        else:
            rest.append(arg)
        index += 1
    return explicit, rest, None


def _command_name(command: str, rest: list[str]) -> str:
    if command in GROUPED and rest and not rest[0].startswith("-"):
        return f"atc {command} {'clean' if (command, rest[0]) == ('worktree', 'cleanup') else rest[0]}"
    return f"atc {command}"


def _wants_json(rest: list[str]) -> bool:
    head = rest[:rest.index("--")] if "--" in rest else rest
    return "--json" in head and not any(arg in ("-h", "--help") for arg in head)


def main(argv: list[str] | None = None, checkout: Path | None = None) -> int:
    """``checkout`` is the checkout serving this `atc` for the guard; tests name one."""
    _escape_unencodable_output()
    if argv is None:
        _child_encoding()
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["--version"], ["-V"]):
        print(version())
        return 0
    command_at = next((i for i, arg in enumerate(args) if not arg.startswith("-") and (i == 0 or args[i - 1] != "--root")), None)
    if command_at is not None and args[command_at] == "test":
        explicit_root, head, error = _take_root(args[:command_at])
        args = head + args[command_at:]
    else:
        explicit_root, args, error = _take_root(args)
    if error:
        print(error, file=sys.stderr)
        return 2
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
    program, parser_error = sys.argv[0], argparse.ArgumentParser.error
    sys.argv[0] = f"atc {command}"
    argparse.ArgumentParser.error = _usage_error
    try:
        if command != "test" and _wants_json(rest):
            return _enveloped(_command_name(command, rest), lambda: _dispatch(command, rest, explicit_root))
        return _dispatch(command, rest, explicit_root)
    except ModuleNotFoundError as error:
        print(f"ERROR MISSING_DEPENDENCY atc {command} needs the module {error.name!r}, which this "
              f"installation lacks; reinstall it with: {reinstall_command()}", file=sys.stderr)
        return 2
    finally:
        sys.argv[0] = program
        argparse.ArgumentParser.error = parser_error


def _enveloped(name: str, run) -> int:
    """Run one `--json` command and print its result inside the common envelope (shared/conformance/CLI.md § Output)."""
    _scripts_on_path()
    import cli_conventions
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = run()
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 2)
    text = buffer.getvalue()
    if not text.strip():
        return code
    try:
        payload = json.loads(text)
    except ValueError:
        sys.stdout.write(text)
        return code
    print(json.dumps(cli_conventions.envelope(name, code, payload, LIST_KEYS.get(name, "items")), indent=2))
    return code


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
    if command in ("worktree", "task", "onboard", "bootstrap", "close-out", "integrate", "preflight", "doctor"):
        return module.main(rest, root=root)
    if command == "verify":
        return module.verify_main(rest, root=root)
    if command == "validate":
        rest = [*rest, "--root", str(root)]
    if command == "enforce" and (root / "workspace.yaml").is_file() and not any(a.split("=", 1)[0] == "--workspace" for a in rest):
        rest = [*rest, "--workspace", str(root / "workspace.yaml")]
    return module.main(rest)

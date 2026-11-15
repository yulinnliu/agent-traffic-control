"""usage: atc [--root DIR] <command> [<args>]
       atc --version

Agent Traffic Control: governed task lifecycle for agent-written changes.

Task lifecycle:
  task        create / update / list / resume / close a task
  test        run a task's tests, mutants or integration candidate
  integrate   fast-forward main to a task's reviewed tip
  push        push integrated main to origin after checking every commit

Workspace:
  init        set up this directory as a workspace
  add         register a project and create its ATC files
  remove      unregister a project
  sync        write and commit the files ATC owns in projects, or --check them
  doctor      diagnose runtime readiness; changes nothing
  guard       check, or --install, the PreToolUse hook and its permission rules
  install     install a release tag as this host's atc, or --check it
  checkout    make a clean tree of a project at a commit on its main, for a build
  hook        decide one PreToolUse hook call (the command the hook entry runs)

--root DIR    the workspace directory to act on, before or after the command
              (shared/conformance/CLI.md § Root resolution)
Workflow: docs/WORKFLOW.md. Exit codes, output and --json: shared/conformance/CLI.md.
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
    "task": "task_records",
    "test": "task_tests",
    "integrate": "git_integrate",
    "push": "push",
    "init": "bootstrap",
    "add": "bootstrap",
    "remove": "bootstrap",
    "sync": "sync_project",
    "doctor": "doctor",
    "guard": "guard",
    "install": "release_install",
    "checkout": "build_checkout",
}
HOOK = "hook"
HOOK_USAGE = """usage: atc hook [--harness {claude,codex}] [--table FILE] [--payload FILE]

Decide one PreToolUse hook call: read the payload on stdin, or from --payload, and print a deny decision or
nothing. It reads no workspace, so it runs before root resolution and the checkout guard.

Exit: 0 decided (a deny decision on stdout, or nothing); 2 the decision could not be written
See: docs/ENFORCEMENT.md § PreToolUse hook"""
GUARD_EXEMPT = ("doctor",)
# Commands that act on the working directory when no workspace is found: `init` creates one there, `doctor`
# reports that it is missing.
ROOTLESS = ("init", "doctor")
GROUPED = ("task",)
# The checkout the guard reads; None guards the resolved root. Tests name one.
GUARDED_CHECKOUT: Path | None = None


class WorkspaceNotFound(LookupError):
    """No `--root`, no `ATC_ROOT` and no parent of the working directory holding `workspace.yaml`."""


def resolve_root(explicit: str | None, environ: dict[str, str], cwd: Path) -> Path:
    """The workspace directory a command acts on: `--root`, else `ATC_ROOT`, else the nearest parent of ``cwd``
    holding `workspace.yaml` (shared/conformance/CLI.md § Root resolution); WorkspaceNotFound otherwise."""
    if explicit:
        return Path(explicit).resolve()
    if environ.get("ATC_ROOT"):
        return Path(environ["ATC_ROOT"]).resolve()
    for candidate in (cwd.resolve(), *cwd.resolve().parents):
        if (candidate / "workspace.yaml").is_file():
            return candidate
    raise WorkspaceNotFound(str(cwd.resolve()))


def usage() -> str:
    return __doc__.strip()


def reinstall_command() -> str:
    return f'uv tool install -e "{SCRIPTS.parent}" --force'


def escape_unencodable_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")


def _child_encoding() -> None:
    """Every child `atc` starts writes UTF-8 to its pipes (docs/TESTKIT.md § Isolated environment); tests pass `argv`."""
    from atc_testkit.isolation import CHILD_ENCODING
    os.environ[CHILD_ENCODING[0]] = CHILD_ENCODING[1]


def scripts_on_path() -> None:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))


def _guard(command: str, rest: list[str], checkout: Path) -> int | None:
    """Exit 2 when the canonical checkout `atc` acts on is off `main`; warn when it is dirty. Applies only when that
    workspace directory is itself the governance checkout: `governance_checkout: .`, or a Git top level holding
    `atc-manifest.yaml` whether or not `workspace.yaml` says so."""
    if command in GUARD_EXEMPT or any(arg in ("-h", "--help") for arg in rest):
        return None
    scripts_on_path()
    import checkout_guard
    import content
    if not (content.is_workspace_checkout(checkout) or content.holds_governance(checkout)):
        return None
    current = checkout_guard.state(checkout)
    if current.unreadable:
        print(f"ERROR CHECKOUT_UNREADABLE {checkout} holds workspace.yaml but its Git state cannot be read "
              f"({current.unreadable})", file=sys.stderr)
        print("hint: repair the checkout's Git state; atc cannot confirm it is on main", file=sys.stderr)
        return 2
    if current.off_main:
        print(f"ERROR CHECKOUT_NOT_MAIN {checkout_guard.describe(checkout, current)}", file=sys.stderr)
        print("hint: switch it back to main; topics live in task worktrees (root AGENTS.md § Changing this repo)",
              file=sys.stderr)
        return 2
    if current.dirty:
        print(f"WARNING CHECKOUT_DIRTY {checkout} has tracked modifications; atc and sessions read them", file=sys.stderr)
    return None


def _release_pin(command: str, rest: list[str], root: Path) -> int | None:
    """STOP when a release copy acts on a project pinned to another release (`release_pin`)."""
    scripts_on_path()
    import cli_conventions
    import release_pin
    environ = dict(os.environ)
    refusal = release_pin.check(command, rest, root, CHECKOUT, os.getcwd(), environ)
    return None if refusal is None else cli_conventions.refuse(refusal, wants_json(rest))


def package_version() -> str:
    """This atc's version, from its installed distribution, else `unknown`."""
    from importlib import metadata
    try:
        return metadata.version("agent-traffic-control")
    except metadata.PackageNotFoundError:
        return "unknown"


def version() -> str:
    from atc_testkit import __version__ as testkit
    return f"atc {package_version()} (atc-testkit {testkit})"


def hook(rest: list[str]) -> int:
    """`atc hook`: the PreToolUse engine alone, importing nothing but its standard-library modules."""
    if any(arg in ("-h", "--help") for arg in rest):
        escape_unencodable_output()
        print(HOOK_USAGE)
        return 0
    scripts_on_path()
    import pretooluse_hook
    return pretooluse_hook.main(rest)


def take_root(args: list[str]) -> tuple[str | None, list[str], str | None]:
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


def command_name(prefix: str, command: str, rest: list[str], grouped: tuple[str, ...]) -> str:
    if command in grouped and rest and not rest[0].startswith("-"):
        return f"{prefix} {command} {rest[0]}"
    return f"{prefix} {command}"


def wants_json(rest: list[str]) -> bool:
    head = rest[:rest.index("--")] if "--" in rest else rest
    return "--json" in head and not any(arg in ("-h", "--help") for arg in head)


def main(argv: list[str] | None = None, checkout: Path | None = None) -> int:
    """``checkout`` is the checkout the guard reads, by default the resolved root; tests name one."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == [HOOK]:
        return hook(args[1:])
    escape_unencodable_output()
    if argv is None:
        _child_encoding()
    if args[:1] in (["--version"], ["-V"]):
        print(version())
        return 0
    explicit_root, args, error = take_root(args)
    if error:
        print(error, file=sys.stderr)
        return 2
    if not args or args[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    command, rest = args[0], args[1:]
    if command not in COMMANDS:
        print(f"usage: atc [--root DIR] <command> [<args>]\natc: error: unknown command: {command}", file=sys.stderr)
        return 2
    try:
        root = resolve_root(explicit_root, dict(os.environ), Path.cwd())
    except WorkspaceNotFound as missing:
        if command not in ROOTLESS and not any(arg in ("-h", "--help") for arg in rest):
            scripts_on_path()
            import cli_conventions
            return cli_conventions.refuse(cli_conventions.Refusal(
                "WORKSPACE_NOT_FOUND", f"no workspace.yaml in {missing} or any parent",
                "run atc inside a workspace directory, pass --root DIR or set ATC_ROOT; atc init creates one"),
                wants_json(rest))
        root = Path.cwd().resolve()
    refused = _guard(command, rest, checkout or GUARDED_CHECKOUT or root)
    if refused is not None:
        return refused
    refused = _release_pin(command, rest, root)
    if refused is not None:
        return refused
    program = sys.argv[0]
    sys.argv[0] = f"atc {command}"
    try:
        if wants_json(rest):
            return enveloped(command_name("atc", command, rest, GROUPED),
                             lambda: _dispatch(command, rest, root))
        return _dispatch(command, rest, root)
    except ModuleNotFoundError as error:
        print(f"ERROR MISSING_DEPENDENCY atc {command} needs the module {error.name!r}, which this installation lacks",
              file=sys.stderr)
        print(f"hint: {reinstall_command()}", file=sys.stderr)
        return 2
    finally:
        sys.argv[0] = program


def enveloped(name: str, run) -> int:
    """Run one `--json` command and print its result inside the common envelope (shared/conformance/CLI.md § Output)."""
    scripts_on_path()
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
    print(json.dumps(cli_conventions.envelope(name, code, payload), indent=2))
    return code


def _dispatch(command: str, rest: list[str], root: Path) -> int:
    """Run one command on the workspace directory ``root``; ``sys.argv[0]`` is ``atc <command>``, the program name
    argparse shows."""
    scripts_on_path()
    module = __import__(COMMANDS[command])
    if command in ("init", "add", "remove"):
        return module.main([command, *rest], root=root)
    return module.main(rest, root=root)

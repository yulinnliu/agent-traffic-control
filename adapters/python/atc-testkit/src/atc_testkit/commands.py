"""The ``atc test`` and ``atc tmp`` subcommands (docs/TESTKIT.md § Commands); ``atc`` dispatches to ``main``.

``<command> <subcommand> -h`` prints that subcommand's help and exits 0; a wrong form prints its usage to
stderr and exits 2.
"""
from __future__ import annotations

from collections.abc import Sequence
import os
import sys

from . import checkout, desktop, scratch, testrun
from .locations import Refusal

USAGE_EXIT = 2
HELP_OPTIONS = ("-h", "--help")
WITH_RUNS_OPTION = "--with-runs"

USAGES = {
    ("test", "run"): "usage: atc test run --scratch <name> [--tree <tree>] [--python <py>] [--select <base>] "
                     "[--label <label>] [--timeout <s>] [--progress] [-- <pytest args...>]",
    ("test", "mutants"): "usage: atc test mutants <scratch> <sha> <worktree> <mutants.json> --python <py> "
                         "--tests <path>...",
    ("test", "private"): "usage: atc test private [--] <command> [args...]",
    ("tmp", "checkout"): "usage: atc tmp checkout <sha> --task <ID> [--worktree <path>] [--scratch <name>] "
                         "[--lock-dir <dir>]",
    ("tmp", "list"): "usage: atc tmp list [--json]",
    ("tmp", "clean"): f"usage: atc tmp clean [{WITH_RUNS_OPTION}] <dir>...",
    ("tmp", "prune"): "usage: atc tmp prune",
}
DESCRIPTIONS = {
    ("test", "private"): """Run one command on a private desktop from the current directory with the current
environment; print `desktop: private` (or `desktop: interactive` where none can be
created), stream its merged output and exit with its code (docs/TESTKIT.md § Commands).""",
    ("tmp", "list"): """Print one line per registered scratch: its name, its tree relative to the scratch
(the full path when the tree lies outside it), its age, `missing` when the directory is
gone, and its task; delete nothing. A last line counts the missing ones and names
`atc tmp prune`. `atc tmp clean` takes the name as printed.

  --json          every registry field of each scratch plus `size` in bytes (null when missing)

(docs/ATC_HOME.md § Scratch registry).""",
    ("tmp", "clean"): f"""Delete scratches (docs/ATC_HOME.md § Deleting).

  <dir>...                scratches under the ATC temporary root
  {WITH_RUNS_OPTION}             also delete each scratch's dependent runs, the scratches
                          whose registered tree is inside it; without it they are listed

Exit: 0, 1 for a refused directory, 2 before anything is deleted.""",
    ("tmp", "prune"): """Delete entries of <tmp>/systmp and <tmp>/candidates older than seven days
(docs/ATC_HOME.md § Deleting).""",
}
GROUPS = {"test": ("run", "mutants", "private"), "tmp": ("checkout", "list", "clean", "prune")}


def _group_usage(group: str) -> str:
    return "\n".join(USAGES[(group, name)] for name in GROUPS[group])


def _asks_help(arguments: Sequence[str]) -> bool:
    return bool(arguments) and arguments[0] in HELP_OPTIONS


def _help(text: str) -> int:
    print(text)
    return 0


def _usage(text: str) -> int:
    print(text, file=sys.stderr)
    return USAGE_EXIT


def _clean(arguments: Sequence[str]) -> int:
    usage = USAGES[("tmp", "clean")]
    with_runs = arguments[:1] == [WITH_RUNS_OPTION]
    arguments = arguments[1:] if with_runs else arguments
    if not arguments or any(argument.startswith("--") for argument in arguments):
        return _usage(usage)
    return scratch.clean(arguments, with_runs=with_runs)


def _dispatch(arguments: Sequence[str]) -> int:
    group = arguments[0] if arguments else ""
    if group not in GROUPS:
        return _usage("\n".join(_group_usage(name) for name in GROUPS))
    if _asks_help(arguments[1:]):
        return _help(_group_usage(group))
    command, rest = tuple(arguments[:2]), arguments[2:]
    if command not in USAGES:
        return _usage(_group_usage(group))
    if command == ("test", "run"):
        return testrun.run(rest)
    if command == ("test", "mutants"):
        return testrun.mutants(rest)
    if command == ("tmp", "checkout"):
        return checkout.checkout(rest)
    if _asks_help(rest):
        return _help(f"{USAGES[command]}\n\n{DESCRIPTIONS[command]}")
    if command == ("test", "private"):
        rest = rest[1:] if rest[:1] == ["--"] else rest
        if not rest:
            return _usage(USAGES[command])
        return desktop.run(rest, cwd=os.getcwd(), env=os.environ)
    if command == ("tmp", "clean"):
        return _clean(rest)
    if command == ("tmp", "list") and rest in ([], ["--json"]):
        return scratch.list_scratches(as_json=True) if rest else scratch.list_scratches()
    if rest:
        return _usage(USAGES[command])
    return scratch.prune()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    try:
        return _dispatch(arguments)
    except Refusal as refusal:
        print(f"{refusal.token} {refusal.detail}", file=sys.stderr, flush=True)
        return refusal.code


if __name__ == "__main__":
    raise SystemExit(main())

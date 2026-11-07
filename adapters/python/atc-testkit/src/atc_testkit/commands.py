"""The ``atc test`` and ``atc tmp`` subcommands (docs/TESTKIT.md § Commands); ``atc`` dispatches to ``main``.

``<command> <subcommand> -h`` prints that subcommand's help and exits 0; a wrong form prints its usage to
stderr and exits 2.
"""
from __future__ import annotations

from collections.abc import Sequence
import sys

from . import scratch, testrun
from .locations import Refusal

USAGE_EXIT = 2
HELP_OPTIONS = ("-h", "--help")
TREE_NAME_OPTION = "--tree-name"
EARLIER_TREE_NAME_OPTION = "--project"

USAGES = {
    ("test", "run"): "usage: atc test run <tree> <scratch> <python> <label> <pytest args...>",
    ("test", "mutants"): "usage: atc test mutants <scratch> <sha> <worktree> <mutants.json> --python <py> "
                         "--tests <path>...",
    ("tmp", "list"): "usage: atc tmp list",
    ("tmp", "clean"): f"usage: atc tmp clean (<dir>... | {TREE_NAME_OPTION} <name>)",
    ("tmp", "prune"): "usage: atc tmp prune",
}
DESCRIPTIONS = {
    ("test", "run"): """One isolated pytest run inside <tree> (docs/TESTKIT.md § Commands).

  <tree>          worktree or exported copy the tests run in
  <scratch>       scratch name, a direct child of the ATC temporary root
  <python>        interpreter that runs pytest: a path, or a name on PATH
  <label>         name printed as `== <label> ==` before the run
  <pytest args>   passed to pytest after the isolation options

Exit: pytest's code, 8 for a guard refusal, 9 for a location refusal.""",
    ("tmp", "list"): """Print the scratch registry with each scratch's size; delete nothing
(docs/ATC_HOME.md § Scratch registry).""",
    ("tmp", "clean"): f"""Delete scratches (docs/ATC_HOME.md § Deleting).

  <dir>...                scratches under the ATC temporary root
  {TREE_NAME_OPTION} <name>       every scratch whose registry `project` column is <name>:
                          the tree's [project].name, else its directory name
  {EARLIER_TREE_NAME_OPTION} <name>         accepted in place of {TREE_NAME_OPTION} through atc-testkit 0.7.x

Exit: 0, 1 for a refused directory, 2 before anything is deleted.""",
    ("tmp", "prune"): """Delete entries of <tmp>/systmp and <tmp>/candidates older than seven days
(docs/ATC_HOME.md § Deleting).""",
}
GROUPS = {"test": ("run", "mutants"), "tmp": ("list", "clean", "prune")}


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
    if arguments[:1] in ([TREE_NAME_OPTION], [EARLIER_TREE_NAME_OPTION]):
        if len(arguments) != 2:
            return _usage(usage)
        if arguments[0] == EARLIER_TREE_NAME_OPTION:
            print(f"NOTE {EARLIER_TREE_NAME_OPTION} is accepted through atc-testkit 0.7.x; use {TREE_NAME_OPTION}",
                  file=sys.stderr, flush=True)
        return scratch.clean((), arguments[1])
    if not arguments or any(argument.startswith("--") for argument in arguments):
        return _usage(usage)
    return scratch.clean(arguments)


def _dispatch(arguments: Sequence[str]) -> int:
    group = arguments[0] if arguments else ""
    if group not in GROUPS:
        return _usage("\n".join(_group_usage(name) for name in GROUPS))
    if _asks_help(arguments[1:]):
        return _help(_group_usage(group))
    command, rest = tuple(arguments[:2]), arguments[2:]
    if command not in USAGES:
        return _usage(_group_usage(group))
    if command == ("test", "mutants"):
        return testrun.mutants(rest)
    if _asks_help(rest):
        return _help(f"{USAGES[command]}\n\n{DESCRIPTIONS[command]}")
    if command == ("test", "run"):
        return testrun.run(rest)
    if command == ("tmp", "clean"):
        return _clean(rest)
    if rest:
        return _usage(USAGES[command])
    if command == ("tmp", "list"):
        return scratch.list_scratches()
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

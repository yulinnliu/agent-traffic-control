"""The ``atc test`` and ``atc tmp`` subcommands (docs/TESTKIT.md § Commands); ``atc`` dispatches to ``main``.

    python -m atc_testkit.commands test run <tree> <scratch> <python> <label> <pytest args...>
    python -m atc_testkit.commands test mutants <scratch> <sha> <worktree> <mutants.json> --python <py> --tests <path>...
    python -m atc_testkit.commands tmp list
    python -m atc_testkit.commands tmp clean (<dir>... | --project <name>)
    python -m atc_testkit.commands tmp prune
"""
from __future__ import annotations

from collections.abc import Sequence
import sys

from . import scratch, testrun
from .locations import Refusal

USAGE_EXIT = 2


def _usage() -> int:
    print(__doc__.strip(), file=sys.stderr)
    return USAGE_EXIT


def _clean(arguments: Sequence[str]) -> int:
    if arguments[:1] == ["--project"]:
        if len(arguments) != 2:
            return _usage()
        return scratch.clean((), arguments[1])
    if not arguments or any(argument.startswith("--") for argument in arguments):
        return _usage()
    return scratch.clean(arguments)


def _dispatch(arguments: Sequence[str]) -> int:
    command, rest = tuple(arguments[:2]), arguments[2:]
    if command == ("test", "run"):
        return testrun.run(rest)
    if command == ("test", "mutants"):
        return testrun.mutants(rest)
    if command == ("tmp", "clean"):
        return _clean(rest)
    if command == ("tmp", "list") and not rest:
        return scratch.list_scratches()
    if command == ("tmp", "prune") and not rest:
        return scratch.prune()
    return _usage()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    try:
        return _dispatch(arguments)
    except Refusal as refusal:
        print(f"{refusal.token} {refusal.detail}", file=sys.stderr, flush=True)
        return refusal.code


if __name__ == "__main__":
    raise SystemExit(main())

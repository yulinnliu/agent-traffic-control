"""``atc test run`` and ``atc test mutants`` (docs/TESTKIT.md § Commands)."""
from __future__ import annotations

import argparse
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

from . import config, desktop, locations, scratch, stages
from .git import git_env
from .isolation import drop_hami_variables
from .locations import Refusal

TAIL_LINES = 15
GUARD_EXIT = 8
LOCATION_EXIT = 9
MUTANT_SECONDS = 900.0
SUMMARY_WIDTH = 150
PRIVATE_DESKTOP_VARIABLE = "ATC_PRIVATE_DESKTOP"
INTERACTIVE_EXCLUSION = "not interactive_desktop"
KILLED, SURVIVED, INCONCLUSIVE = "KILLED", "SURVIVED", "INCONCLUSIVE"
IMPORT_GUARD = r"""
import glob, importlib, os, sys
tree = os.path.normcase(os.path.realpath(sys.argv[1])) + os.sep
bad = []
def visit(src, rel):
    here = os.path.join(src, *rel)
    if os.path.isfile(os.path.join(here, "__init__.py")):
        name = ".".join(rel)
        try:
            where = importlib.import_module(name).__file__
        except Exception as exc:
            bad.append(f"{name}: {exc!r}")
            return
        if not os.path.normcase(os.path.realpath(where)).startswith(tree):
            bad.append(f"{name}: {where} (tree {tree})")
        return
    for child in sorted(os.listdir(here)):
        if os.path.isdir(os.path.join(here, child)) and not child.startswith((".", "_")):
            visit(src, rel + [child])
for src in glob.glob(os.path.join(sys.argv[1], "packages", "*", "src")) + glob.glob(os.path.join(sys.argv[1], "src")):
    for child in sorted(os.listdir(src)):
        if os.path.isdir(os.path.join(src, child)) and not child.startswith((".", "_")):
            visit(src, [child])
print("\n".join(bad))
sys.exit(1 if bad else 0)
"""


@dataclass(frozen=True)
class Result:
    """A pytest run: its exit code, ``None`` after a timeout, and its output lines."""
    code: int | None
    lines: list[str]


def resolve_python(argument: str, cwd: str | None = None) -> str:
    """A path-form ``<python>`` against the caller's directory with its last component kept; a bare name on PATH."""
    if "/" in argument or "\\" in argument:
        path = os.path.abspath(os.path.join(cwd or os.getcwd(), argument))
        real = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
        if not os.path.isfile(real):
            raise Refusal("PYTHON_NOT_FOUND", argument)
        return real
    found = shutil.which(argument)
    if found is None:
        raise Refusal("PYTHON_NOT_FOUND", argument)
    return found


def resolve_tree(argument: str) -> Path:
    try:
        tree = Path(os.path.realpath(argument))
        if tree.is_dir():
            return tree
    except (OSError, ValueError):
        pass
    raise Refusal("TREE_NOT_FOUND", argument)


def prepare_scratch(argument: str, environ: MutableMapping[str, str]) -> tuple[Path, Path]:
    """``<tmp>`` and the scratch, checked and with its data root created; refusals exit 9."""
    root = locations.locate(environ).tmp
    place = scratch.resolve_scratch(argument, root)
    try:
        (place / "qdr").mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise Refusal("SCRATCH_NOT_CREATED", f"{argument}: {error}") from error
    return root, place


def pythonpath_directories(tree: Path) -> list[str]:
    return [*sorted(path for path in glob.glob(str(tree / "packages" / "*" / "src")) if os.path.isdir(path)),
            *([str(tree / "src")] if (tree / "src").is_dir() else [])]


def _capture(command: Sequence[str], cwd: Path, env: Mapping[str, str], private: bool,
             timeout: float | None) -> Result:
    """Run ``command`` with its output collected; a timeout ends its tree and returns code ``None``."""
    process, _kind = desktop.start(command, cwd=cwd, env=env, private=private, new_session=timeout is not None)
    chunks: list[bytes] = []
    reader = threading.Thread(target=lambda: chunks.extend(process.stdout), daemon=True)
    reader.start()
    reader.join(timeout)
    timed_out = reader.is_alive()
    if timed_out:
        desktop.kill_tree(process.pid)
        reader.join(10.0)
    code = process.wait()
    lines = b"".join(chunks).decode("utf-8", errors="replace").splitlines()
    return Result(None if timed_out else code, lines)


def _with_exclusion(arguments: Sequence[str]) -> list[str]:
    """``arguments`` with ``interactive_desktop`` tests excluded from the marker expression pytest applies."""
    result, expression, index = [], None, 0
    arguments = list(arguments)
    while index < len(arguments):
        argument = arguments[index]
        if argument == "-m" and index + 1 < len(arguments):
            expression = arguments[index + 1]
            index += 2
            continue
        if argument.startswith("-m") and not argument.startswith("--") and len(argument) > 2:
            expression = argument[2:].removeprefix("=")
        else:
            result.append(argument)
        index += 1
    combined = f"({expression}) and {INTERACTIVE_EXCLUSION}" if expression else INTERACTIVE_EXCLUSION
    return ["-m", combined, *result]


def isolate(tree: Path, place: Path, python: str, environ: MutableMapping[str, str]) -> bool:
    """Steps 2 to 4 of the section; whether the run goes to a private desktop."""
    drop_hami_variables(environ)
    for name in config.data_root_variables(tree) or (config.DEFAULT_DATA_ROOT,):
        environ[name] = str(place / "qdr")
    locations.enter(place, environ)

    directories = pythonpath_directories(tree)
    environ["PYTHONPATH"] = os.pathsep.join(directories)
    os.chdir(tree)
    for directory in directories:
        if not os.path.isdir(directory):
            raise Refusal("PYTHONPATH_MISSING", directory, GUARD_EXIT)

    guard = _capture([python, "-c", IMPORT_GUARD, str(tree)], tree, environ, False, None)
    if guard.code != 0:
        raise Refusal("COPY_IMPORT_MISMATCH", "\n".join(guard.lines), GUARD_EXIT)
    return environ.get(PRIVATE_DESKTOP_VARIABLE) == "1"


def _pytest_command(python: str, tree: Path, place: Path, environ: Mapping[str, str], private: bool,
                    pytest_arguments: Sequence[str]) -> list[str]:
    has_xdist = _capture([python, "-c", "import xdist"], tree, environ, False, None).code == 0
    arguments = _with_exclusion(pytest_arguments) if private else list(pytest_arguments)
    return [python, "-m", "pytest", "-p", "no:cacheprovider", *(["-n", "0"] if has_xdist else []), "-q",
            "--basetemp", str(place / "bt"), *arguments]


def execute(tree_argument: str, scratch_argument: str, python_argument: str, label: str,
            pytest_arguments: Sequence[str], environ: MutableMapping[str, str], *, quiet: bool = False,
            timeout: float | None = None) -> Result:
    """Steps 1 to 5 of the section; a refusal raises ``Refusal`` with its token and exit code."""
    tree = resolve_tree(tree_argument)
    python = resolve_python(python_argument)
    root, place = prepare_scratch(scratch_argument, environ)
    scratch.register(root, place, tree)
    private = isolate(tree, place, python, environ)

    if not quiet:
        if not (tree / ".venv").exists():
            print(f"NOTE no .venv in {tree}: tests that launch source children need one; "
                  "prepare it as the project's AGENTS.md says.", flush=True)
        print(f"== {label} ==", flush=True)
    command = _pytest_command(python, tree, place, environ, private, pytest_arguments)
    result = _capture(command, tree, environ, private, timeout)
    if not quiet:
        for line in result.lines[-TAIL_LINES:]:
            print(line, flush=True)
    return result


def run(argv: Sequence[str], environ: MutableMapping[str, str] | None = None) -> int:
    """``atc test run``: the flag form when the first argument is an option, else the positional form."""
    if argv and argv[0].startswith("--"):
        return run_flags(argv, environ)
    if len(argv) < 4:
        print("usage: atc test run <tree> <scratch> <python> <label> <pytest args...>", file=sys.stderr)
        return 2
    environ = os.environ if environ is None else environ
    tree, place, python, label, *arguments = argv
    return execute(tree, place, python, label, arguments, environ).code


def verify_commit(worktree: str, sha: str) -> None:
    """Refuse a ``<sha>`` that names no commit in ``worktree`` with ``SHA_NOT_FOUND`` (exit 9)."""
    try:
        found = subprocess.run(["git", "-C", worktree, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
                               capture_output=True, text=True, env=git_env(), stdin=subprocess.DEVNULL,
                               creationflags=scratch.WINDOW_HIDDEN)
    except OSError as error:
        raise Refusal("SHA_NOT_FOUND", f"{sha} in {worktree}: {error}") from error
    if found.returncode != 0:
        raise Refusal("SHA_NOT_FOUND", f"{sha} names no commit in {worktree}")


def _blob(worktree: str, sha: str, path: str) -> bytes:
    return subprocess.run(["git", "-C", worktree, "show", f"{sha}:{path}"], capture_output=True, check=True,
                          env=git_env(), stdin=subprocess.DEVNULL, creationflags=scratch.WINDOW_HIDDEN).stdout


def _digest(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r", b"")).hexdigest()


def _verdict(result: Result) -> tuple[str, str]:
    """``SURVIVED`` on a pass, ``KILLED`` on a failed test, and ``INCONCLUSIVE`` on any other ending of the run."""
    if result.code is None:
        return INCONCLUSIVE, f"timed out after {MUTANT_SECONDS:.0f} seconds"
    failed = next((line for line in result.lines if line.startswith("FAILED")), None)
    if result.code == 1 and failed:
        return KILLED, failed[:SUMMARY_WIDTH]
    last = next((line for line in reversed(result.lines) if line.strip()), "")
    if result.code == 0:
        return SURVIVED, last[:SUMMARY_WIDTH]
    errored = next((line for line in result.lines if line.startswith("ERROR")), last)
    return INCONCLUSIVE, f"pytest exit {result.code}: {errored}"[:SUMMARY_WIDTH]


class _Aborted(Exception):
    """A run that stops before its last mutant: the message and exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def _rewrite(target: Path, data: bytes) -> None:
    """Replace a file's bytes and move it past its last whole second, which a bytecode cache records with the size."""
    before = int(target.stat().st_mtime)
    target.write_bytes(data)
    if int(target.stat().st_mtime) <= before:
        os.utime(target, (before + 1, before + 1))


def _apply(tree: Path, mutant: Mapping, touched: set[str], originals: dict[Path, bytes]) -> None:
    for edit in mutant["edits"]:
        target = tree / edit["path"]
        data = target.read_bytes()
        originals.setdefault(target, data)
        old, new = edit["old"].encode(), edit["new"].encode()
        if b"\r\n" in data:
            old, new = old.replace(b"\n", b"\r\n"), new.replace(b"\n", b"\r\n")
        count = data.count(old)
        if count != 1:
            raise _Aborted(f"{mutant['label']}: `old` occurs {count} times in {edit['path']}, need exactly 1")
        _rewrite(target, data.replace(old, new))
        touched.add(edit["path"])


def mutants(argv: Sequence[str], environ: MutableMapping[str, str] | None = None) -> int:
    """``atc test mutants <scratch> <sha> <worktree> <mutants.json> --python <py> --tests <path>...``."""
    parser = argparse.ArgumentParser(
        prog="atc test mutants", allow_abbrev=False,
        description="Apply each mutant alone to <scratch>/tree and run the tests on it (docs/TESTKIT.md § Commands).",
        epilog="Exit: 0 with no survivor and every file restored; 1 for a red baseline, a survivor, an aborted run "
               "or a restore mismatch; 9 for a refusal before the baseline, 8 for an import-guard refusal.")
    parser.add_argument("scratch", help="scratch name; <scratch>/tree holds the export of <sha>")
    parser.add_argument("sha", help="commit the export was made from; touched files are compared with it at the end")
    parser.add_argument("worktree", help="repository that holds <sha>")
    parser.add_argument("mutants", metavar="mutants.json",
                        help="JSON list of mutants: {label, edits: [{path, old, new}]}, paths relative to the tree")
    parser.add_argument("--python", required=True, help="interpreter that runs pytest: a path, or a name on PATH")
    parser.add_argument("--tests", nargs="+", required=True, help="pytest arguments naming the tests that judge a mutant")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    worktree = os.path.abspath(args.worktree)
    verify_commit(worktree, args.sha)
    python = resolve_python(args.python)
    entries = json.loads(Path(args.mutants).read_text(encoding="utf-8-sig"))
    _root, place = prepare_scratch(args.scratch, environ)
    tree = place / "tree"
    pytest_arguments = ["-x", *args.tests]

    def attempt(label: str) -> Result:
        return execute(str(tree), str(place), python, label, pytest_arguments, environ, quiet=True,
                       timeout=MUTANT_SECONDS)

    verdict, summary = _verdict(attempt("baseline"))
    passed = verdict == SURVIVED
    print(f"{'BASELINE-OK' if passed else 'BASELINE-RED'} {summary}", flush=True)
    if not passed:
        return 1

    touched: set[str] = set()
    survivors: list[str] = []
    aborted = finished = 0
    try:
        for mutant in entries:
            originals: dict[Path, bytes] = {}
            try:
                _apply(tree, mutant, touched, originals)
                verdict, summary = _verdict(attempt(mutant["label"]))
                print(f"{verdict:<8} {mutant['label']} -> {summary}", flush=True)
                finished += 1
                if verdict == SURVIVED:
                    survivors.append(mutant["label"])
                elif verdict == INCONCLUSIVE:
                    aborted = 1
                    break
            finally:
                for target, data in originals.items():
                    _rewrite(target, data)
    except _Aborted as abort:
        print(str(abort), file=sys.stderr, flush=True)
        aborted = abort.code
    except Refusal as refusal:
        print(f"{refusal.token} {refusal.detail}", file=sys.stderr, flush=True)
        aborted = refusal.code
    except Exception as error:  # noqa: BLE001 - the restore check and the last line report every ending of the list
        print(f"{type(error).__name__}: {error}", file=sys.stderr, flush=True)
        aborted = 1

    mismatched = False
    for path in sorted(touched):
        same = _digest((tree / path).read_bytes()) == _digest(_blob(worktree, args.sha, path))
        mismatched |= not same
        print("restored-ok" if same else "RESTORE-MISMATCH", path, flush=True)
    counted = f"{finished} of {len(entries)}" if aborted else finished
    print(f"done: {counted} mutants, {len(survivors)} survived", *survivors, flush=True)
    return aborted or (1 if mismatched else 0)


# The flag form (docs/TESTKIT.md § `atc test run --tree`)

TIMEOUT_EXIT = 124
INTERRUPTED_EXIT = 130
PYTEST_INTERRUPTED = 2
PYTEST_NO_TESTS = 5
RUNS = "runs"
LABEL_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")
COUNTS = re.compile(r"\b\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)\b")
SELECTOR_KEY = "selector"


def venv_python(tree: Path) -> str:
    """The tree's ``.venv`` interpreter; ``VENV_MISSING`` names how to make one."""
    python = tree / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise Refusal("VENV_MISSING", f"{tree} has no .venv; make the scratch with `atc tmp checkout`, prepare the "
                                      "environment as the project's AGENTS.md says, or name one with --python")
    return str(python)


def _registered_row(root: Path, place: Path) -> scratch.Row | None:
    return next((row for row in scratch.registered(root) if locations.same_path(row.scratch, place)), None)


def _git_lines(directory: Path | str, *args: str) -> list[str] | None:
    done = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, encoding="utf-8",
                          env=git_env(), stdin=subprocess.DEVNULL, creationflags=scratch.WINDOW_HIDDEN)
    return None if done.returncode else [line for line in done.stdout.splitlines() if line]


def changed_paths(tree: Path, base: str, row: scratch.Row | None) -> list[str]:
    """Paths relative to the repository top that differ from ``base``: the tree's own, or its export's source."""
    if _git_lines(tree, "rev-parse", "--show-toplevel") is not None:
        tracked = _git_lines(tree, "diff", "--name-only", base)
        if tracked is None:
            raise Refusal("SELECTION_BASE_UNKNOWN", f"{base} names no commit in {tree}")
        untracked = _git_lines(tree, "ls-files", "--others", "--exclude-standard", "--full-name") or []
        return sorted({*tracked, *untracked})
    if row is None or not row.source or not row.commit:
        raise Refusal("SELECTION_SOURCE_UNKNOWN", f"{tree} is no Git working tree and its scratch names no source "
                                                  "commit; make it with `atc tmp checkout`")
    paths = _git_lines(row.source, "diff", "--name-only", base, row.commit)
    if paths is None:
        raise Refusal("SELECTION_BASE_UNKNOWN", f"{base} or {row.commit} names no commit in {row.source}")
    return paths


def select(tree: Path, python: str, paths: Sequence[str], environ: Mapping[str, str]) -> list[str]:
    """The test paths the tree's declared ``selector`` prints as JSON for ``paths``."""
    selector = config.tool_table(tree).get(SELECTOR_KEY)
    if not (isinstance(selector, list) and selector and all(isinstance(item, str) for item in selector)):
        raise Refusal("SELECTION_UNDECLARED", f"{tree}/pyproject.toml declares no [tool.atc-testkit] {SELECTOR_KEY}, "
                                              "a list of strings; run without --select")
    if not paths:
        return []
    done = _capture([python, *selector, *paths], tree, environ, False, None)
    try:
        chosen = json.loads(done.lines[-1]) if done.code == 0 and done.lines else None
    except ValueError:
        chosen = None
    if not (isinstance(chosen, list) and all(isinstance(item, str) for item in chosen)):
        raise Refusal("SELECTOR_FAILED", "\n".join([f"exit {done.code}", *done.lines[-TAIL_LINES:]]))
    return chosen


def _logged(command: Sequence[str], cwd: Path, env: Mapping[str, str], private: bool, timeout: float | None,
            log: Path, progress: bool) -> Result:
    """Run ``command`` with every line written to ``log`` as it arrives; code ``None`` after a timeout."""
    process, kind = desktop.start(command, cwd=cwd, env=env, private=private, new_session=True)
    lines: list[str] = []

    def pump() -> None:
        with log.open("ab") as sink:
            for chunk in iter(process.stdout.readline, b""):
                sink.write(chunk)
                sink.flush()
                lines.append(chunk.decode("utf-8", errors="replace").rstrip("\r\n"))
                if progress:
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()

    with log.open("wb") as sink:
        sink.write(stages.log_header(command, cwd, kind).encode("utf-8"))
    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        reader.join(timeout)
    except KeyboardInterrupt:
        desktop.kill_tree(process.pid)
        reader.join(10.0)
        process.wait()
        raise
    timed_out = reader.is_alive()
    if timed_out:
        desktop.kill_tree(process.pid)
        reader.join(10.0)
    code = process.wait()
    return Result(None if timed_out else code, lines)


def summary(result: Result | None, log: Path) -> tuple[str, int]:
    """The one console line after a run and the command's exit code; ``None`` is an interrupted wait."""
    if result is None:
        return f"INTERRUPTED log: {log}", INTERRUPTED_EXIT
    if result.code is None:
        return f"TIMEOUT log: {log}", TIMEOUT_EXIT
    status = {0: "PASS", 1: "FAIL", PYTEST_INTERRUPTED: "INTERRUPTED", PYTEST_NO_TESTS: "NO_TESTS"}.get(
        result.code, f"ERROR exit {result.code}")
    counts = next((", ".join(COUNTS.findall(line)) for line in reversed(result.lines) if COUNTS.search(line)), "")
    failed = [line.split(" ", 1)[1].split(" - ", 1)[0] for line in result.lines
              if line.startswith(("FAILED ", "ERROR ")) and " " in line]
    parts = [status, *([counts] if counts else [])]
    tail = [*([f"failed: {', '.join(failed)}"] if failed else []), f"log: {log}"]
    return f"{' '.join(parts)}; {'; '.join(tail)}", result.code


def run_flags(argv: Sequence[str], environ: MutableMapping[str, str] | None = None) -> int:
    """``atc test run --scratch <name> [--tree <tree>] [--python <py>] [--select <base>] [-- <pytest args>]``."""
    argv = list(argv)
    pytest_arguments: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, pytest_arguments = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(
        prog="atc test run", allow_abbrev=False,
        description="One isolated pytest run in a scratch, its output kept in a log (docs/TESTKIT.md § Commands).",
        epilog="Exit: pytest's code; 124 after --timeout, 130 when interrupted; 8 for a guard refusal, "
               "9 for a refusal before the run.")
    parser.add_argument("--scratch", required=True, help="scratch name, a direct child of the ATC temporary root")
    parser.add_argument("--tree", help="the tree the tests run in (default: the tree the scratch's registry line "
                                       "names, as `atc tmp checkout` records it)")
    parser.add_argument("--python", help="interpreter that runs pytest (default: the tree's .venv)")
    parser.add_argument("--select", metavar="BASE",
                        help="run only the tests the tree's declared selector picks for the paths changed since BASE")
    parser.add_argument("--label", default="run", help="name of the run, in its log's file name")
    parser.add_argument("--timeout", type=float, help="seconds before the run is ended and reported as TIMEOUT")
    parser.add_argument("--progress", action="store_true", help="also stream pytest's output to the console")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    root, place = prepare_scratch(args.scratch, environ)
    row = _registered_row(root, place)
    if args.tree is not None:
        tree = resolve_tree(args.tree)
    elif row is not None and row.tree and os.path.isdir(row.tree):
        tree = Path(os.path.realpath(row.tree))
    else:
        raise Refusal("TREE_NOT_FOUND", f"the scratch {place.name} names no tree; pass --tree")
    python = resolve_python(args.python) if args.python else venv_python(tree)
    scratch.register(root, place, tree)
    paths = changed_paths(tree, args.select, row) if args.select else None
    environ["PYTHONUNBUFFERED"] = "1"
    private = isolate(tree, place, python, environ)
    if paths is not None:
        chosen = select(tree, python, paths, environ)
        print(f"SELECT {args.select}: {len(paths)} changed paths, {len(chosen)} test paths", flush=True)
        if not chosen:
            print("NO_TESTS no test selected", flush=True)
            return 0
        pytest_arguments = [*chosen, *pytest_arguments]
    (place / RUNS).mkdir(exist_ok=True)
    label = LABEL_CHARACTERS.sub("_", args.label)[:40] or "run"
    log = place / RUNS / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{label}.log"
    print(f"LOG {log}", flush=True)
    command = _pytest_command(python, tree, place, environ, private, pytest_arguments)
    try:
        result: Result | None = _logged(command, tree, environ, private, args.timeout, log, args.progress)
    except KeyboardInterrupt:
        result = None
    if result is None or result.code is None or result.code == PYTEST_INTERRUPTED:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-TAIL_LINES:]:
            print(line, flush=True)
    line, code = summary(result, log)
    print(line, flush=True)
    return code

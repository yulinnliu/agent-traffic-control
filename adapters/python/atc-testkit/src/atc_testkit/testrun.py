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
import shutil
import subprocess
import sys
import threading

from . import config, desktop, locations, scratch
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


def execute(tree_argument: str, scratch_argument: str, python_argument: str, label: str,
            pytest_arguments: Sequence[str], environ: MutableMapping[str, str], *, quiet: bool = False,
            timeout: float | None = None) -> Result:
    """Steps 1 to 5 of the section; a refusal raises ``Refusal`` with its token and exit code."""
    tree = resolve_tree(tree_argument)
    python = resolve_python(python_argument)
    root, place = prepare_scratch(scratch_argument, environ)
    scratch.register(root, place, tree)

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

    private = environ.get(PRIVATE_DESKTOP_VARIABLE) == "1"
    guard = _capture([python, "-c", IMPORT_GUARD, str(tree)], tree, environ, False, None)
    if guard.code != 0:
        raise Refusal("COPY_IMPORT_MISMATCH", "\n".join(guard.lines), GUARD_EXIT)

    if not quiet:
        if not (tree / ".venv").exists():
            print(f"NOTE no .venv in {tree}: tests that launch source children need one; "
                  "prepare it as the project's AGENTS.md says.", flush=True)
        print(f"== {label} ==", flush=True)
    has_xdist = _capture([python, "-c", "import xdist"], tree, environ, False, None).code == 0
    arguments = _with_exclusion(pytest_arguments) if private else list(pytest_arguments)
    command = [python, "-m", "pytest", "-p", "no:cacheprovider", *(["-n", "0"] if has_xdist else []), "-q",
               "--basetemp", str(place / "bt"), *arguments]
    result = _capture(command, tree, environ, private, timeout)
    if not quiet:
        for line in result.lines[-TAIL_LINES:]:
            print(line, flush=True)
    return result


def run(argv: Sequence[str], environ: MutableMapping[str, str] | None = None) -> int:
    """``atc test run <tree> <scratch> <python> <label> <pytest args...>``: pytest's exit code, or the refusal's."""
    if len(argv) < 4:
        print("usage: atc test run <tree> <scratch> <python> <label> <pytest args...>", file=sys.stderr)
        return 2
    environ = os.environ if environ is None else environ
    tree, place, python, label, *arguments = argv
    return execute(tree, place, python, label, arguments, environ).code


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
        target.write_bytes(data.replace(old, new))
        touched.add(edit["path"])


def mutants(argv: Sequence[str], environ: MutableMapping[str, str] | None = None) -> int:
    """``atc test mutants <scratch> <sha> <worktree> <mutants.json> --python <py> --tests <path>...``."""
    parser = argparse.ArgumentParser(prog="atc test mutants")
    parser.add_argument("scratch")
    parser.add_argument("sha")
    parser.add_argument("worktree")
    parser.add_argument("mutants")
    parser.add_argument("--python", required=True)
    parser.add_argument("--tests", nargs="+", required=True)
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    worktree, python = os.path.abspath(args.worktree), resolve_python(args.python)
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
                    target.write_bytes(data)
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
    counted = finished if finished == len(entries) else f"{finished} of {len(entries)}"
    print(f"done: {counted} mutants, {len(survivors)} survived", *survivors, flush=True)
    return aborted or (1 if mismatched else 0)

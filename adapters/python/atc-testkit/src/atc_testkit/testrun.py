"""The runs behind ``atc test ID`` and ``atc test ID --mutants`` (docs/TESTKIT.md § Commands)."""
from __future__ import annotations

import argparse
import contextlib
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
import functools
import glob
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ElementTree

from . import checkout, config, desktop, elapsed, evidence, inputs, isolation, lines, lint, locations, \
    python_toolchain, scratch, stages
from . import timing
from . import toolchain as toolchains
from . import workers as host_workers
from .git import git_env
from .isolation import drop_hami_variables
from .locations import Refusal

TAIL_LINES = 15
GUARD_EXIT = 8
LOCATION_EXIT = 9
MUTANT_SECONDS = 900.0
SUMMARY_WIDTH = 150
PRIVATE_DESKTOP_VARIABLE = "ATC_PRIVATE_DESKTOP"
INTERACTIVE_EXCLUSION = stages.PRIVATE_EXCLUSION
EXCLUSION_NOTE = (f"NOTE private desktop: interactive_desktop tests are excluded; {PRIVATE_DESKTOP_VARIABLE}=0 "
                  "runs on the interactive desktop with them")
KILLED, SURVIVED, INCONCLUSIVE, NOT_COVERED = "KILLED", "SURVIVED", "INCONCLUSIVE", "NOT_COVERED"
BASELINE_STAGE, MUTANT_STAGE = "baseline", "mutant:"
KILL_FAILURE, KILL_ASSERTION = "failure", "assertion"
# The start of a junit failure message that kills a mutant under the assertion rule.
ASSERTION_FAILURE = re.compile(r"(?:AssertionError|assert |Failed: DID NOT RAISE)")
JUNIT = "junit"
PROJECT_DEPTH = 3
# Imports each package of the PYTHONPATH directories, which must load from the tree; then locates, without running
# it, each package of every other project `src` up to PROJECT_DEPTH levels below the tree, which must resolve to the
# tree or not at all: an environment built from another checkout installs that checkout's copy.
IMPORT_GUARD = r"""
import glob, importlib, importlib.util, os, sys
top, depth = sys.argv[1], int(sys.argv[2])
real = lambda path: os.path.normcase(os.path.realpath(path))
tree = real(top) + os.sep
bad = []
def where(name, listed):
    if listed:
        return importlib.import_module(name).__file__
    spec = importlib.util.find_spec(name)
    return None if spec is None else spec.origin or next(iter(spec.submodule_search_locations or ()), None)
def visit(src, rel, listed):
    here = os.path.join(src, *rel)
    if os.path.isfile(os.path.join(here, "__init__.py")):
        name = ".".join(rel)
        try:
            found = where(name, listed)
        except Exception as exc:
            if listed:
                bad.append(f"{name}: {exc!r}")
            return
        if found is not None and not real(found).startswith(tree):
            bad.append(f"{name}: {found} (tree {tree})")
        return
    for child in sorted(os.listdir(here)):
        if os.path.isdir(os.path.join(here, child)) and not child.startswith((".", "_")) and child.isidentifier():
            visit(src, rel + [child], listed)
listed = glob.glob(os.path.join(top, "packages", "*", "src")) + glob.glob(os.path.join(top, "src"))
seen = {real(src) for src in listed}
others = []
for here, dirs, files in os.walk(top):
    rel = os.path.relpath(here, top)
    level = 0 if rel == os.curdir else rel.count(os.sep) + 1
    dirs[:] = [] if level >= depth else sorted(
        name for name in dirs if not name.startswith((".", "_")) and name not in ("src", "node_modules", "tests", "test"))
    src = os.path.join(here, "src")
    if "pyproject.toml" in files and os.path.isdir(src) and real(src) not in seen:
        others.append(src)
for srcs, flag in ((listed, True), (others, False)):
    for src in srcs:
        for child in sorted(os.listdir(src)):
            if os.path.isdir(os.path.join(src, child)) and not child.startswith((".", "_")) and child.isidentifier():
                visit(src, [child], flag)
print("\n".join(bad))
sys.exit(1 if bad else 0)
"""
GUARD_NEXT = ("hint: run with the copy's own environment, built by `atc test ID --sha` or the project's AGENTS.md "
              "preparation in the copy, and its interpreter")


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


def check_task(task: str) -> None:
    """Refuse a ``--task`` that is not of the task form with ``TASK_INVALID`` (exit 9)."""
    if not scratch.TASK_ID.fullmatch(task):
        raise Refusal("TASK_INVALID", f"{task!r} is not a task ID such as ATC-0008")


def copy_interpreter(root: Path, place: Path) -> str:
    """The interpreter of the copy's own environment: the ``.venv`` of the tree the scratch's registry line names
    inside it, as the export records it, else of ``<scratch>/tree``; ``VENV_MISSING`` without."""
    row = next((row for row in scratch.registered(root) if locations.same_path(row.scratch, place)), None)
    tree = place / "tree"
    if row is not None and row.tree and Path(os.path.realpath(row.tree)).is_relative_to(os.path.realpath(place)):
        tree = Path(row.tree)
    return toolchains.for_tree(tree).interpreter(tree)


def locate_scratch(argument: str, environ: MutableMapping[str, str]) -> tuple[Path, Path]:
    """``<tmp>`` and the scratch, checked and nothing created; a scratch path that holds a file is
    ``SCRATCH_NOT_CREATED``. Refusals exit 9."""
    root = locations.locate(environ).tmp
    place = scratch.resolve_scratch(argument, root)
    if place.exists() and not place.is_dir():
        raise Refusal("SCRATCH_NOT_CREATED", f"{argument}: {place} exists and is no directory")
    return root, place


def claim_scratch(root: Path, place: Path, tree: Path, task: str) -> None:
    """Register the scratch with ``task``, then create it and its data root: every check that can refuse runs
    before this, so no refusal leaves an unregistered directory, and one after it leaves a registered one."""
    scratch.register(root, place, tree, task=task)
    try:
        (place / "qdr").mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise Refusal("SCRATCH_NOT_CREATED", f"{place.name}: {error}") from error


def pythonpath_directories(tree: Path) -> list[str]:
    return [*sorted(path for path in glob.glob(str(tree / "packages" / "*" / "src")) if os.path.isdir(path)),
            *([str(tree / "src")] if (tree / "src").is_dir() else [])]


def _capture(command: Sequence[str], cwd: Path, env: Mapping[str, str], private: bool,
             timeout: float | None) -> Result:
    """Run ``command`` with its output collected; a timeout ends its tree and returns code ``None``."""
    process, _kind = desktop.start(command, cwd=cwd, env=env, private=private, new_session=timeout is not None)
    job = host_workers.StageJob(process)
    try:
        chunks: list[bytes] = []
        reader = threading.Thread(target=lambda: chunks.extend(process.stdout), daemon=True)
        reader.start()
        reader.join(timeout)
        timed_out = reader.is_alive()
        if timed_out:
            desktop.kill_tree(process.pid)
            reader.join(10.0)
        code = process.wait()
    finally:
        job.close()
    lines = b"".join(chunks).decode("utf-8", errors="replace").splitlines()
    return Result(None if timed_out else code, lines)


def isolate(tree: Path, place: Path, python: str, environ: MutableMapping[str, str]) -> bool:
    """Steps 2 to 4 of the section; whether the run goes to a private desktop."""
    timing.resolve(environ)
    drop_hami_variables(environ)
    for name in config.data_root_variables(tree) or (config.DEFAULT_DATA_ROOT,):
        environ[name] = str(place / "qdr")
    for name, path in toolchains.for_tree(tree).pointed_variables(place).items():
        environ[name] = str(path)
    locations.enter(place, environ)

    directories = pythonpath_directories(tree)
    environ["PYTHONPATH"] = lines.keep_hook(environ, os.pathsep.join(directories))
    os.chdir(tree)
    for directory in directories:
        if not os.path.isdir(directory):
            raise Refusal("PYTHONPATH_MISSING", directory, GUARD_EXIT)

    guard = _capture([python, "-c", IMPORT_GUARD, str(tree), str(PROJECT_DEPTH)], tree, environ, False, None)
    if guard.code != 0:
        raise Refusal("COPY_IMPORT_MISMATCH", "\n".join([*guard.lines, GUARD_NEXT]), GUARD_EXIT)
    return private_desktop(environ)


def child_environment(tree: Path, place: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """The environment of a process the run starts: the verifier's (docs/TESTKIT.md § Isolated environment), the
    scratch as its working directory and the run's data roots kept."""
    return isolation.IsolatedRun(place, environ=environ, credentials=config.credential_variables(tree),
                                 toolchain=toolchains.for_tree(tree)).environment(CHILD_STAGE)


def private_desktop(environ: Mapping[str, str]) -> bool:
    """On Windows a run is private unless ``ATC_PRIVATE_DESKTOP=0``; another value than 0 or 1 is refused."""
    value = environ.get(PRIVATE_DESKTOP_VARIABLE, "")
    if value not in ("", "0", "1"):
        raise Refusal("PRIVATE_DESKTOP_INVALID", f"{PRIVATE_DESKTOP_VARIABLE}={value!r}: use 0 for the interactive "
                                                 "desktop, 1 or nothing for a private one")
    return os.name == "nt" and value != "0"


def xdist_installed(python: str, tree: Path, environ: Mapping[str, str]) -> bool:
    return _capture([python, "-c", "import xdist"], tree, environ, False, None).code == 0


def _pytest_command(python: str, tree: Path, place: Path, environ: Mapping[str, str], private: bool,
                    pytest_arguments: Sequence[str], junit: Path | None = None,
                    xdist: bool | None = None) -> list[str]:
    """The run's pytest command; a private run adds ``not interactive_desktop`` to the caller's ``-m``."""
    has_xdist = xdist_installed(python, tree, environ) if xdist is None else xdist
    expression, rest = python_toolchain.split_marker(pytest_arguments)
    if private:
        expression = python_toolchain.combine(expression, INTERACTIVE_EXCLUSION)
    options = python_toolchain.pytest_arguments(select=expression, xdist=has_xdist, no_cache=True,
                                                basetemp=place / "bt", junit=junit)
    return [python, "-m", "pytest", *options, *rest]


def execute(tree_argument: str, scratch_argument: str, python_argument: str, pytest_arguments: Sequence[str],
            environ: MutableMapping[str, str], *, task: str, timeout: float | None = None,
            junit: Path | None = None, recording: lines.Prepared | None = None) -> Result:
    """One captured pytest run of a mutant; a refusal raises ``Refusal`` with its token and exit code. With
    ``recording`` the run records the lines each test runs (docs/TESTKIT.md § ``atc test ID --mutants FILE``)."""
    tree = resolve_tree(tree_argument)
    python = resolve_python(python_argument)
    root, place = locate_scratch(scratch_argument, environ)
    claim_scratch(root, place, tree, task)
    private = isolate(tree, place, python, environ)
    child = child_environment(tree, place, environ)
    if recording is not None:
        child = lines.environment(recording, tree, child)
        pytest_arguments = ["-p", lines.PLUGIN, *pytest_arguments]
    command = _pytest_command(python, tree, place, child, private, pytest_arguments, junit)
    return _capture(command, tree, child, private, timeout)


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


def _unasserted(junit: Path) -> str | None:
    """Why the junit report shows a run that did not fail by assertion alone; ``None`` when it did."""
    try:
        cases = list(ElementTree.parse(junit).getroot().iter("testcase"))
    except (OSError, ElementTree.ParseError) as error:
        return f"no junit report ({type(error).__name__})"
    errored = [case for case in cases if case.find("error") is not None]
    if errored:
        return f"errored: {errored[0].find('error').get('message', '')}"
    for case in cases:
        failure = case.find("failure")
        if failure is not None and not ASSERTION_FAILURE.match(failure.get("message", "")):
            return f"failed by {failure.get('message', '')!r}, not by an assertion"
    return None


XDIST_STOPPED = re.compile(r"xdist\.dsession\.Interrupted: stopping after \d+ failures?\b")


def _verdict(result: Result, junit: Path | None = None) -> tuple[str, str]:
    """``SURVIVED`` on a pass, ``KILLED`` on a failed test, and ``INCONCLUSIVE`` on any other ending of the run.

    With ``junit``, the assertion rule: a failed run kills only when every failure in that report is an assertion
    and no test errored.
    """
    if result.code is None:
        return INCONCLUSIVE, f"timed out after {MUTANT_SECONDS:.0f} seconds"
    failed = next((line for line in result.lines if line.startswith("FAILED")), None)
    code = result.code
    if code == PYTEST_INTERRUPTED and any(XDIST_STOPPED.search(line) for line in result.lines):
        code = 1  # pytest-xdist ends a run that reached -x's one failure as an interrupt
    if code == 1 and failed:
        why = _unasserted(junit) if junit is not None else None
        if why is not None:
            return INCONCLUSIVE, why[:SUMMARY_WIDTH]
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


MUTANTS_SHAPE = ('a JSON list of {"label": TEXT, "edits": [{"path": PATH, "old": TEXT, "new": TEXT}], '
                 '"tests"?: [ARG], "kill_rule"?: "failure" | "assertion"}; edit paths and tests are relative to the '
                 'repository root')


def _relative(path: str) -> bool:
    return not (PurePosixPath(path).is_absolute() or re.match(r"^[A-Za-z]:", path) or path.startswith("\\"))


def mutants_problem(entries: object) -> str | None:
    """The first way ``entries`` breaks the mutants file shape, or None."""
    if not isinstance(entries, list) or not entries:
        return "the top level is not a non-empty JSON list of mutants"
    for index, mutant in enumerate(entries):
        if not isinstance(mutant, dict):
            return f"mutant {index} is not an object"
        label = mutant.get("label")
        if not isinstance(label, str) or not label.strip():
            return f"mutant {index} has no label"
        edits = mutant.get("edits")
        if not isinstance(edits, list) or not edits:
            return f"{label}: edits is not a non-empty list"
        for edit in edits:
            if not isinstance(edit, dict) or not all(isinstance(edit.get(key), str) for key in ("path", "old", "new")):
                return f"{label}: an edit is not {{path, old, new}} as text"
            if not _relative(edit["path"]):
                return f"{label}: edit path {edit['path']} is not relative to the repository root"
        tests = mutant.get("tests", [])
        if not isinstance(tests, list) or not all(isinstance(test, str) for test in tests):
            return f"{label}: tests is not a list of pytest arguments"
        if mutant.get("kill_rule", KILL_FAILURE) not in (KILL_FAILURE, KILL_ASSERTION):
            return f"{label}: kill_rule {mutant['kill_rule']!r} is not {KILL_FAILURE} or {KILL_ASSERTION}"
    return None


def read_mutants(path: str | os.PathLike[str]) -> list[dict]:
    """The mutants of ``path``; Refusal ``MUTANTS_INVALID`` naming the first problem (§ ``MUTANTS_SHAPE``)."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise Refusal("MUTANTS_INVALID", f"{path}: cannot be read ({getattr(exc, 'strerror', None) or exc})") from None
    try:
        entries = json.loads(text)
    except ValueError as exc:
        raise Refusal("MUTANTS_INVALID", f"{path}: not JSON ({exc})") from None
    problem = mutants_problem(entries)
    if problem:
        raise Refusal("MUTANTS_INVALID", f"{path}: {problem}")
    return entries


def mutants(argv: Sequence[str], environ: MutableMapping[str, str] | None = None) -> int:
    """``<scratch> <sha> <worktree> <mutants.json> --python <py> --tests <path>...``.

    The command holds the scratch's mutants lock from before the pristine check to its end, the baseline's host
    worker tokens for the baseline and one token from then to its end (docs/TESTKIT.md § Host worker budget).
    """
    held: list[tuple[host_workers.Budget, host_workers.Grant]] = []
    with contextlib.ExitStack() as stack:
        try:
            return _mutants(argv, environ, held, stack)
        finally:
            for budget, grant in held:
                budget.release(grant)


BASELINE_RECORD = "baseline.json"
TOUCHED = "mutants-touched.txt"
# The fields of a reused baseline's key (docs/TESTKIT.md § ``atc test ID --mutants FILE``); ``files`` need only
# hold the files this run records. ``inputs`` holds the input digest and excluded paths of the commit the baseline
# ran on, so a commit with the same inputs, in any scratch of the task, reuses it.
KEY_FIELDS = ("inputs", "tests", "lines", "files", "recorder", "testkit", "python", "private")
NOT_REUSED = "NOTE baseline: not reused:"
SCRATCH_PREFIX = "<scratch>/"


def commit_tree(repository: str, commit: str) -> list[inputs.Entry] | None:
    """The ``(mode, blob, path)`` entries of ``commit``'s tree; ``None`` when Git cannot list them."""
    try:
        return inputs.entries(inputs.git_reader(Path(repository)), commit)
    except (OSError, subprocess.CalledProcessError):
        return None


def classified_inputs(export: Path, repository: str, commit: str, tree: Sequence[inputs.Entry] | None,
                      argv_file: str | None, environ: Mapping[str, str]) -> dict | None:
    """A baseline's ``inputs``: the input digest of ``commit`` and its excluded paths, as the verifier of its export
    classifies them (``classify_export``); ``None`` without a classification."""
    if tree is None or not argv_file:
        return None
    try:
        argv = json.loads(Path(argv_file).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        return None
    classified = classify_export(argv, export, Path(repository), environ, commit)
    if classified is None:
        return None
    excluded = sorted(classified[0])
    return {"digest": inputs.digest(tree, excluded), "excluded": excluded}


def scratch_relative(path: str, place: Path) -> str:
    """An interpreter inside the scratch by its path from the scratch, so another scratch's equal one matches."""
    try:
        return SCRATCH_PREFIX + Path(path).resolve().relative_to(place.resolve()).as_posix()
    except (OSError, ValueError):
        return path


def _same_inputs(stored: object, key: object, tree: Sequence[inputs.Entry] | None) -> bool:
    """Whether the commit of ``tree`` has the stored baseline's inputs: a classified record's digest equals the
    tree's with that record's excluded paths left out (shared/conformance/verification-evidence/README.md § Input
    set); an unclassified record matches its own commit only."""
    if isinstance(stored, dict):
        digest, excluded = stored.get("digest"), stored.get("excluded")
        return (tree is not None and isinstance(digest, str) and isinstance(excluded, list)
                and all(isinstance(path, str) for path in excluded) and inputs.digest(tree, excluded) == digest)
    return stored == key


def _mismatch(stored: Mapping, key: Mapping[str, object], tree: Sequence[inputs.Entry] | None) -> str | None:
    """The first key field ``stored`` does not match, ``None`` when it matches."""
    for name in KEY_FIELDS:
        if name == "files":
            have = stored.get(name)
            matches = isinstance(have, list) and set(key[name]) <= set(have)
        elif name == "inputs":
            matches = _same_inputs(stored.get(name), key[name], tree)
        else:
            matches = stored.get(name) == key[name]
        if not matches:
            return name
    return None


def _copy_lines(source: Path, place: Path, recording: lines.Prepared) -> bool:
    """Copy another scratch's line records into the freshly prepared ``recording``, each recorded file's path moved
    from that scratch's tree to this one's; whether the copy reads back as a record."""
    data = source / lines.DIRECTORY / lines.DATA
    old, new = lines.real(source / "tree"), lines.real(place / "tree")
    for path in recording.data.iterdir():
        path.unlink()
    for path in data.iterdir():
        if not path.is_file():
            continue
        text = path.read_bytes()
        if path.suffix == ".tsv":
            rows = []
            for row in text.decode("utf-8", errors="replace").split("\n"):
                parts = row.split("\t")
                if len(parts) == 3 and parts[1].startswith(old):
                    parts[1] = new + parts[1][len(old):]
                rows.append("\t".join(parts))
            text = "\n".join(rows).encode("utf-8")
        (recording.data / path.name).write_bytes(text)
    return lines.read(recording.data) is not None


def adopt_baseline(root: Path, place: Path, task: str, key: Mapping[str, object], commit: str,
                   recording: lines.Prepared | None, tree: Sequence[inputs.Entry] | None) -> dict | None:
    """The newest passed baseline of another scratch of ``task`` whose key matches, kept as this scratch's record with
    its line records copied here; ``None`` when there is none."""
    found = []
    for row in scratch.registered(root):
        other = Path(row.scratch)
        if row.task != task or lines.real(other) == lines.real(place):
            continue
        try:
            stored = json.loads((other / BASELINE_RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(stored, dict) and _mismatch(stored, key, tree) is None:
            found.append((str(stored.get("time", "")), str(other), stored))
    for _time, name, stored in sorted(found, reverse=True):
        other = Path(name)
        try:
            if stored.get("recorded") and (recording is None or not _copy_lines(other, place, recording)):
                continue
            # A baseline of that scratch that started meanwhile deleted its record before touching its lines.
            if json.loads((other / BASELINE_RECORD).read_text(encoding="utf-8")) != stored:
                continue
        except (OSError, ValueError):
            continue
        adopted = {**stored, "sha": commit, "reused_from": {"scratch": other.name, "sha": stored.get("sha")}}
        _keep_record(place, adopted)
        return adopted
    return None


@functools.cache
def package_digest() -> str:
    """The sources digest of the running atc_testkit package."""
    return sources_digest(Path(__file__).resolve().parent)


def sources_digest(base: Path) -> str:
    """The sha256 of every ``.py`` file under ``base``, each with its relative path, carriage returns dropped."""
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*.py"), key=lambda path: path.relative_to(base).as_posix()):
        digest.update(path.relative_to(base).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r", b"") + b"\0")
    return digest.hexdigest()


def _touched_before(place: Path) -> list[str]:
    try:
        return [line for line in (place / TOUCHED).read_text(encoding="utf-8").splitlines() if line]
    except FileNotFoundError:
        return []


def check_pristine(tree: Path, worktree: str, sha: str, paths: Sequence[str]) -> None:
    """Refuse with ``TREE_NOT_PRISTINE`` (exit 9) when a file of ``paths`` differs from its object at ``sha``,
    carriage returns ignored; a path ``sha`` lacks, or a directory, is left to the mutant that names it."""
    for path in sorted(set(paths)):
        if (tree / path).is_dir():
            continue
        try:
            blob = _blob(worktree, sha, path)
        except subprocess.CalledProcessError:
            continue
        try:
            same = _digest((tree / path).read_bytes()) == _digest(blob)
        except OSError:
            same = False
        if not same:
            raise Refusal("TREE_NOT_PRISTINE", f"{path} differs from {sha[:7]}")


def reusable(place: Path, key: Mapping[str, object], recording: lines.Prepared | None,
             tree: Sequence[inputs.Entry] | None = None) -> dict | None:
    """The record of the scratch's last passed baseline when it matches ``key``; ``None`` otherwise, after a
    ``NOTE baseline: not reused:`` line naming the first mismatch when a record exists."""
    try:
        stored = json.loads((place / BASELINE_RECORD).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        stored = None
    if not isinstance(stored, dict):
        print(f"{NOT_REUSED} {BASELINE_RECORD} cannot be read", flush=True)
        return None
    name = _mismatch(stored, key, tree)
    if name is not None:
        print(f"{NOT_REUSED} {name}", flush=True)
        return None
    if stored.get("recorded"):
        try:
            found = recording is not None and lines.read(recording.data) is not None
        except OSError:
            found = False
        if not found:
            print(f"{NOT_REUSED} the line records are gone", flush=True)
            return None
    return stored


def _keep_record(place: Path, record: Mapping[str, object]) -> None:
    """Write ``baseline.json`` whole or not at all."""
    staged = place / f"{BASELINE_RECORD}.tmp"
    staged.write_text(json.dumps(record, indent=1), encoding="utf-8")
    os.replace(staged, place / BASELINE_RECORD)


def failed_tests(result: Result) -> list[str]:
    """The node IDs of the ``FAILED`` and ``ERROR`` lines of a run."""
    return [line.split(" ", 1)[1].split(" - ", 1)[0] for line in result.lines
            if line.startswith(("FAILED ", "ERROR ")) and " " in line]


def _mutants(argv: Sequence[str], environ: MutableMapping[str, str] | None,
             held: list[tuple[host_workers.Budget, host_workers.Grant]], stack: contextlib.ExitStack) -> int:
    parser = argparse.ArgumentParser(
        prog="atc test --mutants", allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Apply each mutant alone to <scratch>/tree and run the tests on it, after a baseline run in\n"
                    "parallel on the host's workers, or reused from the last passed baseline of the same inputs,\n"
                    "tests, recorder and testkit, in this scratch or another of the task.",
        epilog="Exit: 0 when every mutant ran and was killed and every file is restored; 1 for a survivor (named\n"
               "on the last line), a red baseline, an aborted run or a restore mismatch; 9 for a refusal before the\n"
               "baseline (MUTANTS_RUNNING while another run holds the scratch, TREE_NOT_PRISTINE for a file left\n"
               "mutated), 8 for an import-guard refusal; 124 when the wait for host workers passes 900 seconds,\n"
               "130 when that wait is interrupted.\n"
               "See: docs/TESTKIT.md § Commands")
    parser.add_argument("scratch", metavar="NAME", help="scratch name; NAME/tree holds the export of SHA")
    parser.add_argument("sha", metavar="SHA",
                        help="commit the export was made from; touched files are compared with it at the end")
    parser.add_argument("worktree", metavar="DIR", help="repository that holds SHA")
    parser.add_argument("mutants", metavar="FILE",
                        help="JSON list of mutants: {label, edits: [{path, old, new}], tests?: [...], kill_rule?}, paths "
                             "relative to the tree; a mutant's own tests and kill_rule replace --tests and --kill-rule "
                             "for it")
    parser.add_argument("--task", required=True, metavar="ID",
                        help="the task the scratch belongs to, for example ATC-0008; a scratch registered to another "
                             "task is refused")
    parser.add_argument("--python", metavar="PATH",
                        help="interpreter that runs pytest: a path, or a name on PATH (default: the copy's own .venv, "
                             "in the directory the scratch's registry line names, else in NAME/tree)")
    parser.add_argument("--tests", nargs="+", required=True, metavar="ARG",
                        help="pytest arguments naming the tests that judge a mutant")
    parser.add_argument("--lines", action="store_true",
                        help="record the lines each baseline test runs and judge each mutant by the tests that ran "
                             "the lines it edits; a mutant no baseline test runs is NOT_COVERED")
    parser.add_argument("--kill-rule", choices=(KILL_FAILURE, KILL_ASSERTION), default=KILL_FAILURE,
                        help="failure (default): any failed test kills; assertion: only a run whose every failure "
                             "is an assertion, with no test errored, kills")
    parser.add_argument("--verifier-argv", metavar="FILE",
                        help="the project's verifier argv file, which classifies the commit's input set: the baseline "
                             "is then keyed by the input digest, else by the commit")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    check_task(args.task)
    worktree = os.path.abspath(args.worktree)
    verify_commit(worktree, args.sha)
    commit = (_git_lines(worktree, "rev-parse", f"{args.sha}^{{commit}}") or [args.sha])[0]
    python = resolve_python(args.python) if args.python else None
    entries = read_mutants(args.mutants)
    root, place = locate_scratch(args.scratch, environ)
    tree = resolve_tree(str(place / "tree"))
    python = python or copy_interpreter(root, place)
    timing.resolve(environ)
    private = private_desktop(environ)
    claim_scratch(root, place, tree, args.task)
    tree = place / "tree"
    stack.enter_context(scratch.mutants_lock(place))
    check_pristine(tree, worktree, commit,
                   [*_touched_before(place), *(edit["path"] for entry in entries for edit in entry["edits"])])
    if private:
        print(EXCLUSION_NOTE, flush=True)
    baseline_tests = list(dict.fromkeys([*args.tests, *(test for entry in entries for test in entry.get("tests", ()))]))
    recording = lines.pristine(place, tree, entries) if args.lines else None
    isolate(tree, place, python, environ)
    child = child_environment(tree, place, environ)
    commit_entries = commit_tree(worktree, commit)
    key = {"inputs": f"commit {commit}", "tests": baseline_tests,
           "lines": bool(args.lines), "files": lines.watched(recording) if recording is not None else [],
           "recorder": lines.RECORDER_DIGEST, "testkit": package_digest(), "python": scratch_relative(python, place),
           "private": private}
    stored = reusable(place, key, recording, commit_entries)
    budget = stages.open_budget(f"atc test --mutants {args.scratch}", environ, root=Path(worktree),
                                max_wait=MUTANT_SECONDS)
    want = floor = 1
    if stored is None:
        (place / BASELINE_RECORD).unlink(missing_ok=True)
        if recording is not None:
            recording = lines.prepare(place, tree, entries)
        stored = adopt_baseline(root, place, args.task, key, commit, recording, commit_entries)
        if stored is None and recording is not None:
            recording = lines.prepare(place, tree, entries)
    # One grant, sized for the baseline's tests, serves the baseline and every mutant: a mutant's tests are among them.
    xdist = xdist_installed(python, tree, child)
    workers, arguments = _worker_request(baseline_tests, tree, environ, xdist)
    if workers is not None and workers.source == stages.SOURCE_DEFAULT and budget.active:
        workers = workers.capped(_count(python, tree, place, child, private, arguments))
    if workers is not None:
        want, floor = max(1, workers.count), max(1, workers.floor())
    try:
        grant = budget.acquire("mutants", want, floor)
    except KeyboardInterrupt:
        print(QUEUE_INTERRUPTED, flush=True)
        return INTERRUPTED_EXIT
    held.append((budget, grant))
    if budget.active:
        environ[host_workers.HELD] = budget.held_id()
    reports = place / JUNIT
    attempts = 0
    clock = elapsed.Clock(args.task, args.sha, MUTANTS_COMMAND, stages.pool_directory(environ))
    ran: dict[str, object] = {}

    def attempt(label: str, tests: Sequence[str], rule: str = KILL_FAILURE, stage: str = BASELINE_STAGE,
                recorded: lines.Prepared | None = None, options: Sequence[str] = ()) -> tuple[str, str]:
        nonlocal attempts
        junit = None
        if rule == KILL_ASSERTION:
            reports.mkdir(exist_ok=True)
            junit = reports / f"{attempts:03d}-{LABEL_CHARACTERS.sub('_', label)[:40]}.xml"
            junit.unlink(missing_ok=True)
        attempts += 1
        started = time.monotonic()
        result = execute(str(tree), str(place), python,
                         ["-x", *options, *lines.arguments(tests, place, f"{attempts:03d}")],
                         environ, task=args.task, timeout=MUTANT_SECONDS, junit=junit, recording=recorded)
        verdict = _verdict(result, junit)
        word = verdict[0] if stage != BASELINE_STAGE else ("PASS" if verdict[0] == SURVIVED else "FAIL")
        ran.update(result=result, seconds=time.monotonic() - started)
        clock.stage(stage, ran["seconds"], _run_workers([*options, *tests]), word)
        return verdict

    arguments, workers_line = _granted_arguments(arguments, grant, want, workers)
    if workers_line:
        print(workers_line, flush=True)
        budget.annotate(limit=workers_line)
    if stored is not None:
        source = stored.get("reused_from")
        origin = f" from {source.get('scratch')}" if isinstance(source, dict) else ""
        print(f"BASELINE-OK reused{origin} {stored.get('time', '')} ({stored.get('seconds', 0):.1f}s, workers "
              f"{stored.get('workers', 1)}) {stored.get('summary', '')}", flush=True)
    else:
        if _run_workers(arguments) > 1 and not any(item.startswith("--dist") for item in arguments):
            arguments = [*arguments, "--dist", "loadgroup"]
        verdict, summary = attempt("baseline", arguments, recorded=recording)
        if verdict != SURVIVED:
            failed = failed_tests(ran["result"])
            print(f"BASELINE-RED {summary}" + (f"; failed: {', '.join(failed)}" if failed else ""), flush=True)
            return 1
        print(f"BASELINE-OK {summary}", flush=True)
    choices = _choices(entries, args.tests, tree, place, recording)
    if stored is None:
        classified = classified_inputs(tree, worktree, commit, commit_entries, args.verifier_argv, child)
        _keep_record(place, {**key, "inputs": classified or key["inputs"], "sha": commit, "recorded": bool(choices),
                             "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "summary": summary,
                             "seconds": round(float(ran["seconds"]), 1), "workers": _run_workers(arguments)})

    touched: set[str] = set()
    survivors: list[str] = []
    aborted = finished = 0
    try:
        for mutant in entries:
            originals: dict[Path, bytes] = {}
            try:
                choice = choices.get(mutant["label"])
                if choice is not None and choice.kind == lines.UNCOVERED:
                    print(f"{NOT_COVERED} {mutant['label']} -> {lines.uncovered(choice)}", flush=True)
                    finished += 1
                    survivors.append(mutant["label"])
                    continue
                if choice is not None and choice.note:
                    print(f"NOTE {mutant['label']}: {choice.note}; judged by its file-level tests", flush=True)
                with (place / TOUCHED).open("a", encoding="utf-8") as listing:
                    listing.write("".join(edit["path"] + "\n" for edit in mutant["edits"]))
                _apply(tree, mutant, touched, originals)
                rule = mutant.get("kill_rule", args.kill_rule)
                if rule not in (KILL_FAILURE, KILL_ASSERTION):
                    raise Refusal("MUTANT_INVALID", f"{mutant['label']}: kill_rule {rule!r} is not "
                                                    f"{KILL_FAILURE} or {KILL_ASSERTION}")
                tests = choice.tests if choice is not None else mutant.get("tests") or args.tests
                count = mutant_workers(tests, choice, grant.count) if xdist else 1
                verdict, summary = attempt(mutant["label"], tests, rule, f"{MUTANT_STAGE}{mutant['label']}",
                                           options=["-n", str(count), "--dist", "loadgroup"] if count > 1 else ())
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
    return aborted or (1 if mismatched or survivors else 0)


def mutant_workers(tests: Sequence[str], choice: lines.Choice | None, granted: int) -> int:
    """The workers a mutant runs on, within the command's grant: one per ``stages.ITEMS_PER_WORKER`` tests its line
    record names, the whole grant for file-level tests; 1 runs in process, as does a mutant whose own tests carry
    ``-n``, which then decides its workers."""
    if _worker_option(tests) is not None:
        return 1
    if choice is not None and choice.kind == lines.BY_LINES:
        named = sum(1 for test in tests if "::" in test and not test.startswith("-"))
        return max(1, min(granted, -(-named // stages.ITEMS_PER_WORKER)))
    return max(1, granted)


def _choices(entries: Sequence[Mapping], tests: Sequence[str], tree: Path, place: Path,
             recording: lines.Prepared | None) -> dict[str, lines.Choice]:
    """Each mutant's tests by the baseline's line record, written to ``<scratch>/mutants-covered.json``; none
    without a recording, and only a note when the recorder did not start."""
    if recording is None:
        return {}
    record = lines.read(recording.data)
    if record is None:
        print(lines.NOT_RECORDED, flush=True)
        return {}
    choices = {entry["label"]: lines.choose(entry, entry.get("tests") or tests, tree, recording, record)
               for entry in entries}
    (place / lines.COVERED).write_text(json.dumps(
        [{"label": label, "judged_by": choice.kind, "lines": choice.lines, "tests": choice.tests,
          **({"note": choice.note} if choice.note else {})} for label, choice in choices.items()], indent=1),
        encoding="utf-8")
    return choices


# One run (docs/TESTKIT.md § Commands)

TIMEOUT_EXIT = 124
INTERRUPTED_EXIT = 130
QUEUE_INTERRUPTED = "INTERRUPTED while waiting for host workers; no test ran"
PYTEST_INTERRUPTED = 2
PYTEST_NO_TESTS = 5
RUNS = "runs"
LABEL_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")
COUNTS = re.compile(r"\b\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)\b")
SELECTOR_KEY = "selector"
DIRECT_OPTION = "--direct"


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
                                                  "commit; make it with `atc test ID --sha`")
    paths = _git_lines(row.source, "diff", "--name-only", base, row.commit)
    if paths is None:
        raise Refusal("SELECTION_BASE_UNKNOWN", f"{base} or {row.commit} names no commit in {row.source}")
    return paths


def _logged(command: Sequence[str], cwd: Path, env: Mapping[str, str], private: bool, timeout: float | None,
            log: Path, progress: bool) -> Result:
    """Run ``command`` with every line written to ``log`` as it arrives; code ``None`` after a timeout."""
    process, kind = desktop.start(command, cwd=cwd, env=env, private=private, new_session=True)
    job = host_workers.StageJob(process)
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
        sink.write(stages.log_header(command, cwd, kind, env).encode("utf-8"))
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
    job.close()
    return Result(None if timed_out else code, lines)


WORKER_OPTION = re.compile(r"^(?:-n|--numprocesses)(?:=?(.+))?$")


def _worker_option(arguments: Sequence[str]) -> tuple[int, int, str] | None:
    """The last ``-n``/``--numprocesses`` among ``arguments``: its first index, its last index and its value."""
    found = None
    for at, argument in enumerate(arguments):
        match = WORKER_OPTION.match(argument)
        if match is None:
            continue
        if match.group(1):
            found = (at, at, match.group(1))
        elif at + 1 < len(arguments):
            found = (at, at + 1, arguments[at + 1])
    return found


DEFAULT_VALUES = ("auto", "logical")


def _without_workers(arguments: Sequence[str]) -> list[str]:
    """``arguments`` without any ``-n``/``--numprocesses`` option, for a collection."""
    arguments = list(arguments)
    while (option := _worker_option(arguments)) is not None:
        del arguments[option[0]:option[1] + 1]
    return arguments


def _worker_request(arguments: Sequence[str], tree: Path, environ: Mapping[str, str],
                    xdist: bool = True) -> tuple[stages.Workers | None, list[str]]:
    """The workers a run asks for and its arguments: no ``-n``, ``-n auto`` or ``-n logical`` is the default count
    (``stages.resolve_workers``), written as ``-n auto`` until the grant; ``-n K`` a flag cut to the workers memory
    holds; ``-n 0``, another value, or a Python without pytest-xdist and no ``-n``, ``None`` for one serial token."""
    arguments = list(arguments)
    option = _worker_option(arguments)
    if option is None:
        if not xdist:
            return None, arguments
        arguments = [*arguments, "-n", DEFAULT_VALUES[0]]
        option = _worker_option(arguments)
    first, last, value = option
    if value in DEFAULT_VALUES:
        return stages.resolve_workers(None, environ, root=tree), arguments
    if not value.isdigit() or not int(value):
        return None, arguments
    workers = stages.resolve_workers(int(value), environ, root=tree)
    if workers.clamp:
        arguments[first:last + 1] = ["-n", str(workers.count)]
    return workers, arguments


def _granted_arguments(arguments: list[str], grant: host_workers.Grant, want: int,
                       workers: stages.Workers | None) -> tuple[list[str], str]:
    """The arguments with the count the run got, and the ``workers:`` line naming what cut or delayed it (empty for
    a serial run): ``-n auto``, ``-n logical`` and an ``-n K`` above the grant become the granted count."""
    option = _worker_option(arguments)
    if option is None or workers is None:
        return arguments, ""
    first, last, value = option
    if value in DEFAULT_VALUES or grant.count < want:
        count = 0 if value in DEFAULT_VALUES and grant.count == 1 else grant.count
        arguments = [*arguments[:first], "-n", str(count), *arguments[last + 1:]]
    return arguments, workers.granted(grant).line()


def _count(python: str, tree: Path, place: Path, environ: Mapping[str, str], private: bool,
           arguments: Sequence[str]) -> int | None:
    """The tests pytest collects for ``arguments`` (worker options dropped); ``None`` when it cannot tell."""
    command = _pytest_command(python, tree, place, environ, private, [*_without_workers(arguments), "--collect-only"],
                              xdist=False)
    return stages.collected_items(command, tree, environ)


def touched(tree: Path, python: str, paths: Sequence[str], environ: Mapping[str, str]) -> list[str]:
    """The test paths the declared selector prints for ``--direct`` (changed test files and the test files that
    import a changed module directly); a selector without ``--direct`` gives the changed test files of the tree."""
    selector = config.tool_table(tree).get(SELECTOR_KEY)
    if isinstance(selector, list) and selector and all(isinstance(item, str) for item in selector) and paths:
        done = _capture([python, *selector, *paths, DIRECT_OPTION], tree, environ, False, None)
        try:
            chosen = json.loads(done.lines[-1]) if done.code == 0 and done.lines else None
        except ValueError:
            chosen = None
        if isinstance(chosen, list) and all(isinstance(item, str) for item in chosen):
            return chosen
    prefix = (_git_lines(tree, "rev-parse", "--show-prefix") or [""])[0]
    tests = []
    for path in paths:
        relative = path[len(prefix):] if path.startswith(prefix) else None
        name = PurePosixPath(path).name
        if relative and name.startswith("test_") and name.endswith(".py") and (tree / relative).is_file():
            tests.append(relative)
    return tests


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


def run(argv: Sequence[str], environ: MutableMapping[str, str] | None = None) -> int:
    """``--scratch <name> --task <ID> [--tree <tree>] [--python <py>] [<option>...] [-- <pytest args>]``."""
    argv = list(argv)
    pytest_arguments: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, pytest_arguments = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(
        prog="atc test", allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="One isolated pytest run in a scratch, its output kept in a log.",
        epilog="Exit: pytest's code; 124 after --timeout or when the wait for host workers passes its limit,\n"
               "130 when interrupted; 8 for a guard refusal, 9 for a refusal before the run.\n"
               "See: docs/TESTKIT.md § Commands")
    parser.add_argument("--scratch", required=True, metavar="NAME",
                        help="scratch name, a direct child of the ATC temporary root")
    parser.add_argument("--tree", metavar="DIR", help="the tree the tests run in (default: the tree the scratch's registry line "
                                       "names, as the export records it)")
    parser.add_argument("--python", metavar="PATH", help="interpreter that runs pytest (default: the tree's .venv)")
    parser.add_argument("--task", required=True, metavar="ID",
                        help="the task the scratch belongs to, for example ATC-0008; a scratch registered to another "
                             "task is refused")
    parser.add_argument("--related", metavar="SHA",
                        help="run only the changed test files and the test files that import a changed module "
                             "directly, for the paths changed since SHA")
    parser.add_argument("--lint", metavar="SHA",
                        help="first run the lint stage over the files changed since SHA; a failure ends the run")
    parser.add_argument("--lint-only", action="store_true", help="run the lint stage of --lint and no test")
    parser.add_argument("--verifier-argv", metavar="FILE",
                        help="the project's verifier argv file, which classifies the input set; with it a clean run "
                             "of whole test files records each passed file (docs/TESTKIT.md § Input evidence)")
    parser.add_argument("--label", default="run", metavar="NAME", help="name of the run, in its log's file name")
    parser.add_argument("--timeout", type=float, metavar="SECONDS", help="seconds before the run is ended and reported as TIMEOUT")
    parser.add_argument("--progress", action="store_true", help="also stream pytest's output to the console")
    args = parser.parse_args(argv)
    if args.lint_only and (args.lint is None or pytest_arguments or args.related):
        parser.error("--lint-only takes --lint and no tests")
    check_task(args.task)
    environ = os.environ if environ is None else environ
    root, place = locate_scratch(args.scratch, environ)
    row = _registered_row(root, place)
    if args.tree is not None:
        tree = resolve_tree(args.tree)
    elif row is not None and row.tree and os.path.isdir(row.tree):
        tree = Path(os.path.realpath(row.tree))
    else:
        raise Refusal("TREE_NOT_FOUND", f"the scratch {place.name} names no tree; pass --tree")
    python = resolve_python(args.python) if args.python else toolchains.for_tree(tree).interpreter(tree)
    claim_scratch(root, place, tree, args.task)
    paths = changed_paths(tree, args.related, row) if args.related else None
    environ["PYTHONUNBUFFERED"] = "1"
    private = isolate(tree, place, python, environ)
    child = child_environment(tree, place, environ)
    if args.lint:
        code = run_lint(tree, place, python, changed_paths(tree, args.lint, row), child)
        if code or args.lint_only:
            return code
    items = None
    source = RunSource.of(tree, place, row) if args.verifier_argv and not pytest_arguments else None
    if paths is not None:
        chosen = touched(tree, python, paths, child)
        head = f"RELATED {args.related}: {len(paths)} changed paths, {len(chosen)} test paths"
        if not chosen:
            print(head, "NO_TESTS no test selected", sep="\n", flush=True)
            return 0
        print(head, flush=True)
        pytest_arguments = [*chosen, *pytest_arguments]
    (place / RUNS).mkdir(exist_ok=True)
    label = LABEL_CHARACTERS.sub("_", args.label)[:40] or "run"
    log = place / RUNS / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{label}.log"
    print(f"LOG {log}", flush=True)
    if private:
        print(EXCLUSION_NOTE, flush=True)
    budget = stages.open_budget(f"atc test {place.name}", environ, root=tree, max_wait=args.timeout)
    xdist = xdist_installed(python, tree, child)
    workers, pytest_arguments = _worker_request(pytest_arguments, tree, environ, xdist)
    if workers is not None and workers.source == stages.SOURCE_DEFAULT and budget.active:
        if items is None:
            items = _count(python, tree, place, child, private, pytest_arguments)
        workers = workers.capped(items)
    want, floor = (workers.count, workers.floor()) if workers is not None else (1, 1)
    want, floor = max(1, want), max(1, floor)
    try:
        grant = budget.acquire("tests", want, floor)
    except KeyboardInterrupt:
        print(QUEUE_INTERRUPTED, flush=True)
        return INTERRUPTED_EXIT
    try:
        if budget.active:
            environ[host_workers.HELD] = budget.held_id()
        pytest_arguments, workers_line = _granted_arguments(pytest_arguments, grant, want, workers)
        if workers_line:
            print(workers_line, flush=True)
            environ[stages.WORKERS_LINE] = workers_line
        budget.annotate(log=str(log), limit=workers_line)
        child = child_environment(tree, place, environ)
        outcomes = log.with_suffix(".outcomes.json")
        if source is not None:
            child[inputs.OUTCOMES] = str(outcomes)
        command = _pytest_command(python, tree, place, child, private, pytest_arguments, xdist=xdist)
        started = time.monotonic()
        try:
            result: Result | None = _logged(command, tree, child, private, args.timeout, log, args.progress)
        except KeyboardInterrupt:
            result = None
        seconds = time.monotonic() - started
    finally:
        budget.release(grant)
    if result is None or result.code is None or result.code == PYTEST_INTERRUPTED:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-TAIL_LINES:]:
            print(line, flush=True)
    line, code = summary(result, log)
    if source is not None and result is not None and result.code in (0, 1):
        print(record_inputs(source, Path(args.verifier_argv), outcomes, private,
                            {key: value for key, value in child.items() if key != inputs.OUTCOMES}), flush=True)
    if result is not None:
        clock = elapsed.Clock(args.task, tree_commit(tree, row), RUN_COMMAND, stages.pool_directory(environ))
        clock.stage(label, seconds, _run_workers(pytest_arguments), line.split(" ", 1)[0].rstrip(";"))
    print(line, flush=True)
    return code


@dataclass(frozen=True)
class RunSource:
    """Where a run's commit lives: the repository's top in the tree, the commit, a Git reader that holds it and
    whether the tree equals it, read before the run (docs/TESTKIT.md § Input evidence)."""
    top: Path
    commit: str
    repository: Path
    clean: str | None
    worktree: bool

    @classmethod
    def of(cls, tree: Path, place: Path, row: scratch.Row | None) -> RunSource | None:
        top = _git_lines(tree, "rev-parse", "--show-toplevel")
        if top is not None:
            commit = (_git_lines(tree, "rev-parse", "HEAD") or [""])[0]
            return cls(Path(top[0]), commit, tree, worktree_unclean(tree), True)
        if row is None or not row.source or not row.commit:
            return None
        export = repository_top(tree, place)
        touched = [path for path in _touched_before(place) if (export / path).is_file()]
        try:
            check_pristine(export, row.source, row.commit, touched)
            unclean = None
        except Refusal as refusal:
            unclean = refusal.detail
        return cls(export, row.commit, Path(row.source), unclean, False)

    def unclean_after(self) -> str | None:
        """A worktree's state after the run; an export stays its commit while the scratch is held."""
        return worktree_unclean(self.top) if self.worktree else None

    def classify(self, argv: Sequence[str], env: Mapping[str, str]) -> tuple[list[str], list[str]] | None:
        """The commit's input set as its own verifier classifies it: in the worktree, or in the export with Git
        reads served from the source repository's objects (``classify_export``)."""
        if self.worktree:
            return inputs.classify(argv, self.top, env, self.commit)
        return classify_export(argv, self.top, self.repository, env, self.commit)


CLASSIFY_GIT = "classify.git"


def _export_git(top: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(top), *args], check=True, capture_output=True, env=git_env(),
                   stdin=subprocess.DEVNULL, creationflags=scratch.WINDOW_HIDDEN)


def classify_export(argv: Sequence[str], top: Path, repository: Path, env: Mapping[str, str], commit: str
                    ) -> tuple[list[str], list[str]] | None:
    """``commit``'s input set as the verifier of its export ``top`` classifies it, run in the export: an export is
    no Git checkout, so it gets a Git directory beside it (``<scratch>/classify.git``) that reads the source
    repository's objects through ``objects/info/alternates``, with ``HEAD`` and the index at ``commit``, removed
    with its ``.git`` pointer file afterwards. Nothing runs in, or writes to, the source checkout. ``None`` when
    the export already has a ``.git`` or the Git directory cannot be made."""
    marker, directory = top / ".git", top.parent / CLASSIFY_GIT
    if os.path.lexists(marker):
        return None

    def clean() -> None:
        marker.unlink(missing_ok=True)
        if os.path.isdir(directory):
            scratch.remove_tree(directory, lambda _link: False, say=lambda _line: None)
    try:
        clean()
        objects = inputs.common_directory(repository) / "objects"
        _export_git(top, "init", "-q", f"--separate-git-dir={directory}")
        # Git reads a carriage return as part of the path, so the line ends in a bare LF on every platform.
        (directory / "objects" / "info" / "alternates").write_bytes(f"{objects.as_posix()}\n".encode())
        _export_git(top, "update-ref", "HEAD", commit)
        _export_git(top, "read-tree", commit)
    except (OSError, subprocess.CalledProcessError):
        clean()
        return None
    try:
        return inputs.classify(argv, top, {name: value for name, value in env.items()
                                           if not name.upper().startswith("GIT_")}, commit)
    finally:
        clean()


def worktree_unclean(tree: Path) -> str | None:
    """Why a Git working tree is not its ``HEAD``: its first changed or untracked path; ``None`` when it is."""
    status = _git_lines(tree, "status", "--porcelain", "--untracked-files=all")
    if status is None:
        return "git status failed"
    return f"changed: {status[0].strip()}" if status else None


def record_inputs(source: RunSource, argv_file: Path, outcomes: Path, private: bool, env: Mapping[str, str]) -> str:
    """Write an input record for each test file the run passed, when the tree equalled its commit before and after
    the run; the line that says what was recorded or why nothing was."""
    unclean = source.clean or source.unclean_after()
    if unclean:
        return f"{INPUTS_NOT_RECORDED} the tree is not {source.commit[:12]} ({unclean})"
    passed = inputs.repository_files(outcomes, source.top, "passed")
    if not passed:
        return f"{INPUTS_NOT_RECORDED} no test file passed whole"
    try:
        argv = json.loads(argv_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return f"{INPUTS_NOT_RECORDED} the verifier argv file {argv_file} cannot be read"
    classified = source.classify(argv, env)
    if classified is None:
        return f"{INPUTS_NOT_RECORDED} the verifier did not classify {source.commit[:12]}'s input set"
    excluded, dependency_files = classified
    try:
        common = inputs.common_directory(source.repository)
        tree = inputs.entries(inputs.git_reader(source.repository), source.commit)
        dependencies = evidence.dependency_digests(source.top, dependency_files)
        marker = stages.PRIVATE_EXCLUSION if private else ""
        for file in passed:
            inputs.write(common, evidence.test_file_identity(file, marker, dependencies, timing.slack(env)), tree,
                         excluded, source.commit)
        inputs.prune(common)
    except (OSError, subprocess.CalledProcessError) as error:
        return f"{INPUTS_NOT_RECORDED} {type(error).__name__}: {error}"
    return f"EVIDENCE {len(passed)} test files recorded for {source.commit[:12]} (inputs {inputs.digest(tree, excluded)[:12]})"


INPUTS_NOT_RECORDED = "NOTE input evidence: not recorded:"
RUN_COMMAND = "test"
LINT_LABEL = "lint"
CHILD_STAGE = "tests"
MUTANTS_COMMAND = "mutants"


def repository_top(tree: Path, place: Path) -> Path:
    """The top of the repository ``tree`` belongs to: its Git working tree, or the export ``<place>/tree``."""
    found = _git_lines(tree, "rev-parse", "--show-toplevel")
    return Path(found[0]) if found else place / checkout.TREE


def run_lint(tree: Path, place: Path, python: str, paths: Sequence[str], env: Mapping[str, str]) -> int:
    """The lint stage over ``paths`` (docs/TESTKIT.md § Lint stage): 0 when it passes, has no file or is not
    adopted, else 1."""
    top = repository_top(tree, place)
    if not lint.configured(top):
        print(lint.not_configured_line(top), flush=True)
        return 0
    files = lint.existing(top, paths)
    if not files:
        print("LINT no changed file", flush=True)
        return 0
    (place / RUNS).mkdir(exist_ok=True)
    log = place / RUNS / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{LINT_LABEL}.log"
    code = lint.run_paths(top, place, python, files, env, log)
    if code == 0:
        print(f"LINT PASS {len(files)} files; log {log}", flush=True)
        return 0
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL_LINES:]:
        print(line, flush=True)
    print(f"STOP {lint.TOKEN}: pre-commit exited {code} on {len(files)} files; log {log}", flush=True)
    return 1


def tree_commit(tree: Path, row: scratch.Row | None) -> str:
    """The commit a run tests: the source commit an export's registry line names, else the tree's ``HEAD``; empty
    when neither is known."""
    if row is not None and row.commit:
        return row.commit
    return (_git_lines(tree, "rev-parse", "HEAD") or [""])[0]


def _run_workers(arguments: Sequence[str]) -> int:
    """The worker processes a pytest command line runs: its last ``-n K``, else 1 for an in-process run."""
    option = _worker_option(arguments)
    return int(option[2]) if option is not None and option[2].isdigit() and int(option[2]) > 0 else 1

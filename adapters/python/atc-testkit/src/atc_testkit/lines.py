"""The lines each baseline test runs, and the tests that judge a mutant by them (docs/TESTKIT.md § ``atc test ID
--mutants FILE``).

The recorder runs inside the tested tree's interpreters from files this module writes into the scratch; it imports
nothing of the tree, so it records an export of any commit.
"""
from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path

from . import scratch
from .locations import Refusal

DIRECTORY = "lines"
DATA, HOOK = "data", "hook"
COVERED = "mutants-covered.json"
PLUGIN = "atc_lines_plugin"
DIR_VARIABLE, FILES_VARIABLE = "ATC_LINES_DIR", "ATC_LINES_FILES"
CONTEXT_VARIABLE, HOOK_VARIABLE = "ATC_LINES_CONTEXT", "ATC_LINES_HOOK"
STARTED, LOADED, COLLECTED = "started-", "loaded-", "collected-"
# Past this many characters of test arguments a run passes them in a pytest argfile.
ARGFILE_THRESHOLD = 30_000
FALLBACK, BY_LINES, UNCOVERED = "file-level", "lines", "not covered"
NOT_RECORDED = "NOTE lines: not recorded; every mutant is judged by its file-level tests"

RECORDER = r'''"""The line recorder of atc-testkit (docs/TESTKIT.md § atc test ID --mutants FILE); written into the scratch."""
import os
import sys

_state = None


def _real(path):
    return os.path.normcase(os.path.realpath(path))


class _State:
    def __init__(self, data, tool, watched):
        self.data, self.tool, self.watched = data, tool, watched
        self.context = os.environ.get("ATC_LINES_CONTEXT", "")
        self.known = {}
        self.fd = None

    def write(self, text):
        if self.fd is None:
            name = f"{os.getpid()}-{os.urandom(4).hex()}.tsv"
            self.fd = os.open(os.path.join(self.data, name),
                              os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0))
        os.write(self.fd, text.encode("utf-8", "backslashreplace"))

    def path(self, code):
        name = code.co_filename
        found = self.known.get(name)
        if found is None:
            try:
                found = _real(name)
            except (OSError, ValueError):
                found = name
            self.known[name] = found
        return found


def _py_start(code, offset):
    state = _state
    if state.path(code) in state.watched:
        sys.monitoring.set_local_events(state.tool, code, sys.monitoring.events.LINE)
    return sys.monitoring.DISABLE


def _line(code, line):
    state = _state
    path = state.path(code)
    if path in state.watched:
        state.write(f"{state.context}\t{path}\t{line}\n")
    return sys.monitoring.DISABLE


def start():
    global _state
    data, files = os.environ.get("ATC_LINES_DIR"), os.environ.get("ATC_LINES_FILES")
    monitoring = getattr(sys, "monitoring", None)
    if _state is not None or not data or files is None or monitoring is None:
        return
    tool = next((tool for tool in (monitoring.COVERAGE_ID, 4, 3) if monitoring.get_tool(tool) is None), None)
    if tool is None:
        return
    monitoring.use_tool_id(tool, "atc-lines")
    _state = _State(data, tool, frozenset(_real(path) for path in files.split(os.pathsep) if path))
    monitoring.register_callback(tool, monitoring.events.PY_START, _py_start)
    monitoring.register_callback(tool, monitoring.events.LINE, _line)
    monitoring.set_events(tool, monitoring.events.PY_START)


def active():
    return _state is not None


def mark_started():
    if _state is not None:
        open(os.path.join(_state.data, f"started-{os.getpid()}"), "w").close()


def enter(name):
    state = _state
    if state is None:
        return
    state.context = name
    os.environ["ATC_LINES_CONTEXT"] = name
    sys.monitoring.restart_events()


def leave():
    state = _state
    if state is not None:
        state.context = ""
        os.environ["ATC_LINES_CONTEXT"] = ""
'''

SITECUSTOMIZE = r'''"""Starts the atc-testkit line recorder, then hands over to the sitecustomize this one shadows."""
import os
import sys

try:
    import atc_lines_recorder
    atc_lines_recorder.start()
except Exception:
    pass


def _hand_over():
    here = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
    saved, ours = sys.path[:], sys.modules.pop("sitecustomize", None)
    sys.path[:] = [entry for entry in saved if os.path.normcase(os.path.abspath(entry or os.curdir)) != here]
    try:
        import sitecustomize  # noqa: F401 - the shadowed module, run for its effect
    except ImportError as error:
        if error.name != "sitecustomize":
            raise
        sys.modules["sitecustomize"] = ours
    finally:
        sys.path[:] = saved


_hand_over()
'''

PLUGIN_SOURCE = r'''"""Gives each test its own line-recorder context, also in the processes it starts."""
import os

import pytest

import atc_lines_recorder as recorder


def _write(name, text=""):
    data = os.environ.get("ATC_LINES_DIR")
    if data:
        with open(os.path.join(data, f"{name}{os.getpid()}"), "w", encoding="utf-8") as sink:
            sink.write(text)


_NODEID = pytest.StashKey[str]()


def pytest_configure(config):
    _write("loaded-")
    recorder.mark_started()


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    # The node ID as collected, before pytest-xdist's loadgroup appends "@<group>", which no pytest argument names.
    for item in items:
        item.stash[_NODEID] = item.nodeid


def _name(item):
    nodeid = item.stash.get(_NODEID, item.nodeid)
    rest = nodeid.split("::", 1)
    try:
        path = os.path.relpath(str(item.path), str(item.config.invocation_params.dir)).replace(os.sep, "/")
    except ValueError:
        return nodeid
    return path + ("::" + rest[1] if len(rest) == 2 else "")


def pytest_collection_finish(session):
    if session.items:
        _write("collected-", "".join(_name(item) + "\n" for item in session.items))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    recorder.enter(_name(item))
    try:
        yield
    finally:
        recorder.leave()
'''

HOOK_FILES = {"sitecustomize.py": SITECUSTOMIZE, "atc_lines_recorder.py": RECORDER, f"{PLUGIN}.py": PLUGIN_SOURCE}


def recorder_digest(files: Mapping[str, str]) -> str:
    """The sha256 of the recorder's hook files, each name with its text."""
    return hashlib.sha256("\0".join(f"{name}\0{text}" for name, text in files.items()).encode("utf-8")).hexdigest()


# The recorder's version in a reused baseline's key: any change to its files makes an earlier record unusable.
RECORDER_DIGEST = recorder_digest(HOOK_FILES)


def real(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.realpath(path))


@dataclass
class Prepared:
    """The recording a baseline runs with: its directories, the variables of its processes, the pristine texts."""
    root: Path
    texts: dict[str, str | None]

    @property
    def data(self) -> Path:
        return self.root / DATA

    @property
    def hook(self) -> Path:
        return self.root / HOOK


def prepare(place: Path, tree: Path, entries: Sequence[Mapping]) -> Prepared:
    """A fresh ``<scratch>/lines`` with the recorder's files, and the pristine text of every edited ``.py`` file."""
    root = place / DIRECTORY
    if os.path.lexists(root):
        why = scratch.remove_tree(root, lambda _link: False, say=lambda _line: None)
        if why is not None:
            raise Refusal("LINES_NOT_CLEARED", f"{root}: {why}")
    (root / DATA).mkdir(parents=True)
    (root / HOOK).mkdir()
    for name, source in HOOK_FILES.items():
        (root / HOOK / name).write_text(source, encoding="utf-8")
    return pristine(place, tree, entries)


def pristine(place: Path, tree: Path, entries: Sequence[Mapping]) -> Prepared:
    """The recording of ``<scratch>/lines`` as it stands, with the pristine text of every edited ``.py`` file: a
    reused baseline's (docs/TESTKIT.md § ``atc test ID --mutants FILE``)."""
    root = place / DIRECTORY
    texts: dict[str, str | None] = {}
    for entry in entries:
        for edit in entry["edits"]:
            path = edit["path"]
            if path.endswith(".py") and path not in texts:
                try:
                    texts[path] = (tree / path).read_bytes().decode("utf-8").replace("\r\n", "\n")
                except (OSError, UnicodeError):
                    texts[path] = None
    return Prepared(root, texts)


def watched(prepared: Prepared) -> list[str]:
    """The edited ``.py`` files the recorder watches, relative to the tree."""
    return sorted(path for path, text in prepared.texts.items() if text is not None)


def environment(prepared: Prepared, tree: Path, child: Mapping[str, str]) -> dict[str, str]:
    """``child`` with the hook first on ``PYTHONPATH`` and the recorder's variables."""
    hook = str(prepared.hook)
    files = os.pathsep.join(str(tree / path) for path, text in prepared.texts.items() if text is not None)
    return {**child, "PYTHONPATH": os.pathsep.join(filter(None, [hook, child.get("PYTHONPATH", "")])),
            DIR_VARIABLE: str(prepared.data), FILES_VARIABLE: files, HOOK_VARIABLE: hook, CONTEXT_VARIABLE: ""}


def keep_hook(environ: Mapping[str, str], pythonpath: str) -> str:
    """``pythonpath`` with the recorder's hook first while a recording runs, so a nested run's processes record too."""
    hook = environ.get(HOOK_VARIABLE)
    if not hook or not environ.get(DIR_VARIABLE):
        return pythonpath
    return os.pathsep.join(filter(None, [hook, pythonpath]))


@dataclass
class Record:
    """What a baseline recorded: per (file, line) the tests that ran it, the lines run outside a test, the files
    seen at all and the tests in collection order, the order a single-process baseline runs them."""
    tests: dict[tuple[str, int], set[str]] = field(default_factory=dict)
    outside: set[tuple[str, int]] = field(default_factory=set)
    files: set[str] = field(default_factory=set)
    order: list[str] = field(default_factory=list)


def _rows(text: str) -> Iterable[tuple[str, str, int]]:
    """The complete rows of a recorder file: a row cut short by a process that ended mid-write is not one."""
    for row in text.split("\n")[:-1]:
        parts = row.split("\t")
        if len(parts) == 3 and parts[2].isdigit():
            yield parts[0], parts[1], int(parts[2])


def _pids(names: Iterable[str], prefix: str) -> set[str]:
    return {name[len(prefix):] for name in names if name.startswith(prefix)}


def read(data: Path) -> Record | None:
    """The baseline's record, merged from every process's files; ``None`` when the recorder started in no pytest
    process, or not in every pytest process that loaded the plugin (an xdist worker among them)."""
    names = [path.name for path in data.iterdir()]
    started = _pids(names, STARTED)
    if not started or not _pids(names, LOADED) <= started:
        return None
    record = Record()
    for path in sorted(data.glob("*.tsv")):
        for context, file, line in _rows(path.read_text(encoding="utf-8", errors="replace")):
            record.files.add(file)
            if context:
                record.tests.setdefault((file, line), set()).add(context)
            else:
                record.outside.add((file, line))
    collected = sorted(data.glob(f"{COLLECTED}*"))
    if collected:  # every xdist worker collects the same tests in the same order
        record.order = list(dict.fromkeys(
            name for name in collected[0].read_text(encoding="utf-8").splitlines() if name))
    return record


# The edited lines of a mutant (design § 3.1)

def _children(node: ast.AST) -> list[ast.AST]:
    return [child for name in ("body", "orelse", "handlers", "finalbody", "cases")
            for child in getattr(node, name, ()) or () if isinstance(child, (ast.stmt, ast.excepthandler, ast.match_case))]


def _start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", ())
    return min([node.lineno, *(decorator.lineno for decorator in decorators)]) if hasattr(node, "lineno") else \
        node.pattern.lineno


def _nodes(module: ast.Module) -> list[tuple[int, int, int | None, tuple[int, ...]]]:
    """Every statement, handler and case: (start, end, end of its header, ``None`` for a simple statement, the starts
    of its child statements)."""
    found = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.stmt, ast.excepthandler, ast.match_case)):
            continue
        start = _start(node)
        end = getattr(node, "end_lineno", None) or max(
            (getattr(child, "end_lineno", start) for child in _children(node)), default=start)
        children = tuple(sorted(_start(child) for child in _children(node)))
        header = max(start, children[0] - 1) if children else None
        found.append((start, end, header, children))
    return found


def statement_lines(text: str, first: int, last: int) -> set[int]:
    """Lines ``first`` to ``last`` widened to their statements: the innermost statement holding each line, a compound
    statement by its header alone. A line of a compound statement in none of its children (an ``else:`` or
    ``finally:``, which run no line event, or a comment) also stands for the next child statement, the first of its
    clause; a line in no statement stays itself."""
    try:
        nodes = _nodes(ast.parse(text))
    except (SyntaxError, ValueError):
        nodes = []
    lines: set[int] = set()

    def widen(line: int) -> None:
        holding = [node for node in nodes if node[0] <= line <= node[1]]
        lines.add(line)
        if not holding:
            return
        start, end, header, children = min(holding, key=lambda node: node[1] - node[0])
        if header is None:
            lines.update(range(start, end + 1))
        elif line <= header:
            lines.update(range(start, header + 1))
        else:
            following = [child for child in children if child > line]
            if following:
                widen(following[0])

    for line in range(first, last + 1):
        widen(line)
    return lines


def edited_lines(text: str, old: str, new: str) -> tuple[int, int] | int:
    """The first and last line of ``text`` that replacing ``old`` by ``new`` changes, the lines both share at their
    start and end left out; or how often ``old`` occurs when that is not exactly once."""
    count = text.count(old) if old else 0
    if count != 1:
        return count
    first = text[:text.index(old)].count("\n") + 1
    before, after = old.split("\n"), new.split("\n")
    shared = min(len(before), len(after))
    head = next((index for index in range(shared) if before[index] != after[index]), shared)
    tail = next((index for index in range(shared - head) if before[-1 - index] != after[-1 - index]), shared - head)
    low, high = head, len(before) - 1 - tail
    if low > high:  # whole lines added or removed: the lines on both sides of the cut
        low, high = max(low - 1, 0), min(low, len(before) - 1)
    return first + low, first + high


# The tests of a mutant (design § 3.2)

@dataclass
class Choice:
    """How a mutant is judged: ``lines``, ``file-level`` or ``not covered``, its tests and the why of a fallback."""
    kind: str
    tests: list[str]
    note: str = ""
    lines: dict[str, list[list[int]]] = field(default_factory=dict)


def _ranges(lines: Iterable[int]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for line in sorted(lines):
        if ranges and line == ranges[-1][1] + 1:
            ranges[-1][1] = line
        else:
            ranges.append([line, line])
    return ranges


def _narrowable(tree: Path, tests: Sequence[str]) -> list[bool]:
    """Which items of ``tests`` are a file or directory of the tree, not the value of an option before them."""
    flags = []
    for index, item in enumerate(tests):
        after_option = index > 0 and tests[index - 1].startswith("-") and "=" not in tests[index - 1]
        flags.append(not item.startswith("-") and "::" not in item and not after_option
                     and (tree / item).exists())
    return flags


def _within(name: str, item: str) -> bool:
    file = name.split("::", 1)[0]
    prefix = item.rstrip("/")
    return file == prefix or file.startswith(prefix + "/")


def choose(mutant: Mapping, tests: Sequence[str], tree: Path, prepared: Prepared, record: Record) -> Choice:
    """The tests that judge ``mutant``, whose file-level tests are ``tests``."""
    fallback = list(tests)
    wanted: dict[str, set[int]] = {}
    for edit in mutant["edits"]:
        path = edit["path"]
        text = prepared.texts.get(path)
        if not path.endswith(".py"):
            return Choice(FALLBACK, fallback, f"{path} is not a Python file")
        if text is None:
            return Choice(FALLBACK, fallback, f"{path} cannot be read as UTF-8 before the run")
        if real(tree / path) not in record.files:
            return Choice(FALLBACK, fallback, f"no line record of {path}")
        span = edited_lines(text, edit["old"], edit["new"])
        if isinstance(span, int):
            return Choice(FALLBACK, fallback, f"`old` occurs {span} times in {path} before the run")
        wanted.setdefault(path, set()).update(statement_lines(text, *span))
    shown = {path: _ranges(lines) for path, lines in wanted.items()}
    keys = [(real(tree / path), line) for path, lines in wanted.items() for line in lines]
    outside = sorted({path for path, lines in wanted.items() for line in lines if (real(tree / path), line) in record.outside})
    if outside:
        return Choice(FALLBACK, fallback, f"lines of {', '.join(outside)} run outside a test", shown)
    running = set().union(*(record.tests.get(key, set()) for key in keys))
    flags = _narrowable(tree, tests)
    places = [item for item, flag in zip(tests, flags) if flag]
    kept = [item for item, flag in zip(tests, flags) if not flag]
    narrowed = [name for name in record.order if name in running and any(_within(name, place) for place in places)]
    if not narrowed and not any("::" in item for item in kept):
        return Choice(UNCOVERED, [], "", shown)
    return Choice(BY_LINES, [*narrowed, *kept], "", shown)


def uncovered(choice: Choice) -> str:
    """The summary of a ``NOT_COVERED`` line: the edited lines no baseline test runs."""
    return "no baseline test runs " + ", ".join(
        f"{path}:{first}" + (f"-{last}" if last != first else "") for path, ranges in choice.lines.items()
        for first, last in ranges)


def arguments(tests: Sequence[str], place: Path, label: str) -> list[str]:
    """``tests`` as given, or past the threshold one ``@argfile`` (one argument a line, read by pytest's argparse)."""
    if sum(len(item) + 1 for item in tests) <= ARGFILE_THRESHOLD:
        return list(tests)
    path = place / DIRECTORY / f"{label}.args"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(tests) + "\n", encoding="utf-8")
    return [f"@{path}"]

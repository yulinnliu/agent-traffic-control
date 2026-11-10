"""The guard of declared readers: a test file that reads a guarded path of the checkout undeclared fails the session.

`scripts/select_tests.py` selects for a guarded path only the test files
`DIRECTORY_READERS` declares, so an undeclared reader would be missed; this
plugin records, through one audit hook per process and by test file, every
file open, directory listing, glob and copy under a guarded path, and counts a
child command that runs the checkout's `scripts/*.sh` as a read of every
guarded path. Under pytest-xdist each worker hands its failures to the
controller. Existence checks and what a child Python process opens are not
traced.
"""
from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

import select_tests

TOKEN = "ROOT_READ_UNDECLARED"
WORKER_KEY = "root_reads_failure"
PATH_EVENTS = frozenset({"open", "os.listdir", "os.scandir", "glob.glob", "shutil.copytree"})
_ACTIVE: list[RootReads] = []
_HOOKED = False


def _audit(event: str, args: tuple) -> None:
    if _ACTIVE:
        _ACTIVE[-1].audit(event, args)


def _key(path: str) -> str:
    """A workspace-relative path in the form compared here: forward slashes, case folded where the platform folds it."""
    return os.path.normcase(path.replace("/", os.sep)).replace(os.sep, "/")


def _arguments(argv, cwd) -> list[str]:
    """A child's arguments as absolute paths where they name one, relative ones taken from its working directory."""
    if isinstance(argv, (str, bytes, os.PathLike)):
        text = os.fsdecode(argv)
        parts = shlex.split(text, posix=False) if os.name == "nt" else shlex.split(text)
    else:
        parts = [os.fsdecode(arg) for arg in argv or ()]
    base = os.fsdecode(cwd) if cwd is not None else os.getcwd()
    return [os.path.join(base, part.strip('"')) for part in parts]


class RootReads:
    """Records which guarded paths of ``root`` each test file reads, and fails the session on an undeclared one."""

    def __init__(self, root: Path, guarded: Iterable[str], readers: Mapping[str, Iterable[str]]):
        global _HOOKED
        self.root = Path(os.path.realpath(root))
        self._prefix = os.path.normcase(str(self.root)) + os.sep
        self._scripts = os.path.normcase(str(self.root / "scripts"))
        self.guarded = tuple(_key(entry) for entry in guarded)
        self.readers = {_key(test): tuple(_key(entry) for entry in entries) for test, entries in readers.items()}
        self.current: str | None = None
        self.reads: dict[str, set[str]] = {}
        self.failure: list[str] = []
        self._busy = False
        if not _HOOKED:
            sys.addaudithook(_audit)
            _HOOKED = True

    @classmethod
    def for_checkout(cls) -> RootReads:
        return cls(select_tests.WORKSPACE, select_tests.GUARDED, select_tests.DIRECTORY_READERS)

    def audit(self, event: str, args: tuple) -> None:
        if self.current is None or self._busy:
            return
        self._busy = True
        try:
            if event in PATH_EVENTS and args and isinstance(args[0], (str, bytes, os.PathLike)):
                self._read(os.fsdecode(args[0]))
            elif event == "subprocess.Popen":
                self._child(args[1], args[2])
        except (TypeError, ValueError, OSError):
            pass
        finally:
            self._busy = False

    def _read(self, raw: str) -> None:
        path = os.path.normcase(os.path.realpath(raw))
        if not path.startswith(self._prefix):
            return
        rel = path[len(self._prefix):].replace(os.sep, "/")
        if any(select_tests.covers(entry, rel) for entry in self.guarded):
            self.reads.setdefault(self.current, set()).add(rel)

    def _child(self, argv, cwd) -> None:
        for argument in _arguments(argv, cwd):
            if argument.endswith(".sh") and \
                    os.path.normcase(os.path.dirname(os.path.realpath(argument))) == self._scripts:
                self.reads.setdefault(self.current, set()).update(self.guarded)
                return

    def _test_file(self, path: Path) -> str | None:
        try:
            return _key(Path(os.path.realpath(path)).relative_to(self.root).as_posix())
        except ValueError:
            return None

    def undeclared(self) -> dict[str, list[str]]:
        found = {}
        for test, paths in sorted(self.reads.items()):
            declared = self.readers.get(test, ())
            missing = sorted(p for p in paths if not any(select_tests.covers(entry, p) for entry in declared))
            if missing:
                found[test] = missing
        return found

    def pytest_configure(self, config: pytest.Config) -> None:
        _ACTIVE.append(self)

    def pytest_unconfigure(self, config: pytest.Config) -> None:
        if self in _ACTIVE:
            _ACTIVE.remove(self)

    @pytest.hookimpl(wrapper=True)
    def pytest_make_collect_report(self, collector):
        if isinstance(collector, pytest.Module):
            self.current = self._test_file(collector.path)
        try:
            return (yield)
        finally:
            self.current = None

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        self.current = self._test_file(item.path)
        try:
            return (yield)
        finally:
            self.current = None

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:
        self.failure.extend(getattr(node, "workeroutput", {}).get(WORKER_KEY, ()))

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        self.failure.extend(f"{TOKEN}: {test} reads {', '.join(paths)}; declare it in select_tests.DIRECTORY_READERS"
                            for test, paths in self.undeclared().items())
        if hasattr(session.config, "workerinput"):
            session.config.workeroutput[WORKER_KEY] = list(self.failure)
        if self.failure and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if self.failure:
            terminalreporter.write_sep("=", "undeclared readers of guarded paths", red=True)
            for line in self.failure:
                terminalreporter.write_line(line)

"""Opt-in pytest plugin that records every git child process and every test's phase times
(docs/design/ATC-0071.md § 1.2, docs/design/ATC-0072.md § 1.1).

Load it with `-p tests.measure_git_calls --git-calls-out DIR`; without `-p` nothing here runs. Each xdist worker
appends JSON lines to `DIR/git-<pid>.jsonl` (one per git process: repository, argv, product call stack, the
`atc integrate` or `atc test --candidate` invocation it belongs to, test, exit code, seconds) and
`DIR/tests-<pid>.jsonl` (one per test phase: test, phase, seconds).
"""
from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PRODUCT = ("/src/atc_cli/", "/atc-testkit/src/", "/adapters/python/scripts/")
ENTRIES = {("git_integrate.py", "_main"), ("git_integrate.py", "run_candidate")}
FRAMES = 8
_Original = subprocess.Popen
_state: dict = {"out": None, "handles": {}, "entries": {}, "counter": itertools.count(1)}


def _write(kind: str, record: dict) -> None:
    if _state["out"] is None:
        return
    handle = _state["handles"].get(kind)
    if handle is None:
        _state["out"].mkdir(parents=True, exist_ok=True)
        handle = _state["handles"][kind] = (_state["out"] / f"{kind}-{os.getpid()}.jsonl").open("a", encoding="utf-8")
    handle.write(json.dumps(record) + "\n")


def _context() -> tuple[list[str], object | None]:
    """The product frames outside the test, innermost first, and the outermost invocation entry frame."""
    frames, entry = [], None
    frame = sys._getframe(2)
    while frame is not None:
        path = frame.f_code.co_filename.replace("\\", "/")
        name = path.rsplit("/", 1)[-1]
        if any(part in path for part in PRODUCT):
            frames.append(f"{name}:{frame.f_code.co_name}:{frame.f_lineno}")
            if (name, frame.f_code.co_name) in ENTRIES:
                entry = frame
        elif "/tests/" in path and name != "measure_git_calls.py":
            break
        frame = frame.f_back
    return frames, entry


def _invocation(entry) -> str | None:
    """One label per entry frame object: in-process tests call an entry many times in one process."""
    if entry is None:
        return None
    known = _state["entries"].get(id(entry))
    if known is None or known[0] is not entry:
        known = _state["entries"][id(entry)] = (entry, f"{os.getpid()}-{next(_state['counter'])}")
    return known[1]


class _Popen(_Original):
    def __init__(self, args, *rest, **kwargs):
        self._measured = None
        argv = [str(a) for a in args] if isinstance(args, (list, tuple)) else [str(args)]
        if argv and Path(argv[0]).stem.lower() == "git":
            frames, entry = _context()
            repo = str(kwargs["cwd"]) if kwargs.get("cwd") else None
            if argv[1:2] == ["-C"]:
                repo, argv = argv[2], [argv[0], *argv[3:]]
            self._measured = {"argv": argv[1:], "repo": repo, "frames": frames[:FRAMES],
                              "entry": getattr(getattr(entry, "f_code", None), "co_name", None),
                              "inv": _invocation(entry), "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
                              "t0": time.perf_counter()}
        super().__init__(args, *rest, **kwargs)

    def _done(self) -> None:
        if self._measured is not None and self.returncode is not None:
            record, self._measured = self._measured, None
            record["seconds"] = round(time.perf_counter() - record.pop("t0"), 4)
            record["rc"] = self.returncode
            _write("git", record)

    def wait(self, timeout=None):
        result = super().wait(timeout)
        self._done()
        return result

    def poll(self):
        result = super().poll()
        self._done()
        return result


def pytest_addoption(parser) -> None:
    parser.addoption("--git-calls-out", metavar="DIR", help="where measure_git_calls writes its JSON lines")


def pytest_configure(config) -> None:
    out = config.getoption("--git-calls-out")
    # Under xdist the controller sees every worker's reports again; only the process running a test records it.
    workers = getattr(config.option, "numprocesses", None)
    if out and (os.environ.get("PYTEST_XDIST_WORKER") or not workers):
        _state["out"] = Path(out)
        subprocess.Popen = _Popen


def pytest_runtest_setup(item) -> None:
    _state["entries"].clear()


def pytest_runtest_logreport(report) -> None:
    _write("tests", {"test": report.nodeid, "phase": report.when, "seconds": round(report.duration, 4),
                     "outcome": report.outcome})


def pytest_unconfigure(config) -> None:
    subprocess.Popen = _Original
    for handle in _state["handles"].values():
        handle.close()
    _state["handles"].clear()

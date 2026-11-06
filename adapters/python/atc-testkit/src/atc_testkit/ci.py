"""The CI runner: pytest with failure context and shutdown thread evidence (docs/TESTKIT.md § CI runner, § Stages).

    python -m atc_testkit.ci [pytest args]                 one invocation, for a targeted run
    python -m atc_testkit.ci --stages [--workers N] [paths]  the three stages
    python -m atc_testkit.ci --slow [paths]                the ``slow`` invocation

A project's own runner script calls ``main`` with its additions. ``ci_init`` in ``[tool.atc-testkit]`` names its
initialization function as ``module:function``, imported with the working directory on the search path.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
import faulthandler
import importlib
import json
import os
from pathlib import Path
import sys
import threading

import pytest

from . import config, stages
from .evidence import Stage

REPORT_LIMIT = 4000  # characters of a failing report kept, counted from its end where the exception line sits
CAUSE_LIMIT = 20  # chain elements listed, root cause first
CAUSE_MESSAGE_LIMIT = 300  # characters of one element's crash message
LOG_DIRECTORY = ".tmp"

Context = Callable[[object, object], dict]
Hints = Callable[[object], list[str]]


def pending_threads() -> list[dict]:
    return [dict(name=thread.name, ident=thread.ident) for thread in threading.enumerate()
            if thread is not threading.main_thread() and not thread.daemon]


def failure_causes(longrepr) -> list[str]:
    """One line per exception of a chained report, the root cause first; none for a plain-text report."""
    lines = []
    for _, crash, _ in list(getattr(longrepr, "chain", ()))[:CAUSE_LIMIT]:
        if crash is None:
            continue
        message = " ".join(str(crash.message).split())[:CAUSE_MESSAGE_LIMIT]
        lines.append(f"CI_FAILURE_CAUSE {crash.path}:{crash.lineno} {message}")
    return lines


def print_failure(nodeid: str, report, context: dict) -> None:
    print("CI_FAILURE_CONTEXT " + json.dumps({"test": nodeid, "phase": report.when, "context": context},
                                             sort_keys=True), file=sys.stderr, flush=True)
    for line in failure_causes(report.longrepr):
        print(line, file=sys.stderr, flush=True)
    print(f"CI_FAILURE_REPORT {nodeid} {report.when}\n{report.longreprtext[-REPORT_LIMIT:]}",
          file=sys.stderr, flush=True)


class FailureDiagnostics:
    """The pytest plugin of the runner; ``context`` adds fields to the context line, ``hints`` lines to the summary."""

    def __init__(self, context: Context | None = None, hints: Hints | None = None):
        self.context, self.hints = context, hints

    def pytest_terminal_summary(self, terminalreporter, exitstatus):
        if self.hints and (terminalreporter.stats.get("failed") or terminalreporter.stats.get("error")):
            for line in self.hints(terminalreporter):
                terminalreporter.write_line(line)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        if not report.failed:
            return
        context = {}
        if self.context:
            try:
                context = self.context(item, call)
            except Exception as error:  # noqa: BLE001 - diagnostics never change a test's result
                context = {"diagnostic_error": type(error).__name__}
        print_failure(item.nodeid, report, context)
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)

    def pytest_runtest_logreport(self, report):
        """Evidence for a report a worker sent: a worker runs no hooks of this plugin."""
        if report.failed and getattr(report, "node", None) is not None:
            print_failure(report.nodeid, report, {})


def named_initialization(root: Path) -> Callable[[], object] | None:
    """The function ``ci_init`` names."""
    value = config.tool_table(root).get("ci_init")
    if not value:
        return None
    module, _, function = value.partition(":")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return getattr(importlib.import_module(module), function)


def run_pytest(args: Sequence[str], *, context: Context | None = None, hints: Hints | None = None,
               init: Callable[[], object] | None = None) -> int:
    """One pytest invocation under the runner; the initialization function may return a function that undoes it."""
    print("CI_PYTHON " + sys.version, flush=True)
    undo = (init or named_initialization(Path.cwd()) or (lambda: None))()
    try:
        result = pytest.main(list(args), plugins=[FailureDiagnostics(context, hints)])
    finally:
        if callable(undo):
            undo()
    workers = pending_threads()
    print("CI_PENDING_THREADS " + json.dumps(workers), file=sys.stderr, flush=True)
    if workers:
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        faulthandler.dump_traceback_later(60, repeat=True, file=sys.stderr)
    return int(result)


def _self_command() -> list[str]:
    """The argv that runs this runner again: the module, or the project's runner script."""
    main_module = sys.modules.get("__main__")
    spec = getattr(main_module, "__spec__", None)
    if spec is not None:
        return [sys.executable, "-m", spec.name]
    return [sys.executable, os.path.abspath(sys.argv[0])]


def run_all_stages(paths: Sequence[str], workers: int | None, run: stages.Run = stages.run_logged) -> int:
    """The three stages, each its own process; a run in which every stage collects no test is refused."""
    logs = Path(LOG_DIRECTORY)
    outcome = stages.run_stages(_self_command(), paths, cwd=Path.cwd(),
                                stage_list=stages.stages(config.ignored_paths(Path.cwd())),
                                env_for=lambda stage: dict(os.environ), options_for=lambda stage: (),
                                log_for=lambda stage: logs / f"ci-{stage.name}.log", workers=workers, run=run)
    if outcome.token:
        print(outcome.token, file=sys.stderr, flush=True)
    return outcome.code


def main(args: Sequence[str] | None = None, *, context: Context | None = None, hints: Hints | None = None,
         init: Callable[[], object] | None = None) -> int:
    arguments = list(sys.argv[1:] if args is None else args)
    if arguments[:1] == ["--stages"]:
        rest = arguments[1:]
        workers = None
        if rest[:1] == ["--workers"]:
            workers, rest = int(rest[1]), rest[2:]
        return run_all_stages(rest, workers)
    if arguments[:1] == ["--slow"]:
        slow = Stage(stages.SLOW.name, stages.SLOW.marker, config.ignored_paths(Path.cwd()))
        code = run_pytest([*stages.pytest_arguments(slow), *arguments[1:]], context=context, hints=hints, init=init)
        return 0 if code == stages.PYTEST_NO_TESTS_COLLECTED else code
    return run_pytest(arguments, context=context, hints=hints, init=init)


if __name__ == "__main__":
    raise SystemExit(main())

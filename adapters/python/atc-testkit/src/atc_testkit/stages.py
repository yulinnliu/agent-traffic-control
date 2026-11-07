"""The three stages and the runner with logging (docs/TESTKIT.md § Stages, § Running and logging)."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import sys
import time

from . import desktop, locations
from .evidence import Stage

PARALLEL = Stage("parallel", "not slow and not serial and not interactive_desktop")
SERIAL = Stage("serial", "serial and not slow and not interactive_desktop")
INTERACTIVE = Stage("interactive", "interactive_desktop and not slow")
SLOW = Stage("slow", "slow")
STAGE_ORDER = (PARALLEL, SERIAL, INTERACTIVE)
TEST_COMMAND_STAGE = "tests"
BASE_DIRECTORY_STAGES = frozenset({TEST_COMMAND_STAGE, *(stage.name for stage in STAGE_ORDER)})
MAX_WORKERS = 8
PYTEST_NO_TESTS_COLLECTED = 5
TESTS_FAILED = "TESTS_FAILED"
NO_TESTS_COLLECTED = "NO_TESTS_COLLECTED"
FAILURE_LINE = re.compile(r"^(?:FAILED|ERROR)\b")
REPORTED_LINES = 20
LOG_DIRECTORY = ".tmp"
LOG_DIRECTORY_UNSUITABLE = "LOG_DIRECTORY_UNSUITABLE"
LOG_DIRECTORY_EXIT = 9

Run = Callable[..., int]


def refuse_log_directory(logs: Path) -> int:
    """The exit code of a log directory that is a link or not a directory, with its token on stderr; 0 for one in order."""
    not_real = locations.not_a_real_directory(logs)
    if not not_real:
        return 0
    print(f"{LOG_DIRECTORY_UNSUITABLE} {not_real}", file=sys.stderr, flush=True)
    return LOG_DIRECTORY_EXIT


def stages(ignore: Sequence[str] = ()) -> tuple[Stage, ...]:
    """The stages in order, each with the project's ignored paths."""
    return tuple(Stage(stage.name, stage.marker, tuple(ignore)) for stage in STAGE_ORDER)


def default_workers() -> int:
    return min(MAX_WORKERS, os.cpu_count() or 1)


def pytest_arguments(stage: Stage, workers: int | None = None) -> list[str]:
    """The options of one invocation; each stage passes its worker option explicitly."""
    if stage.name == PARALLEL.name:
        selected = ["-n", str(default_workers() if workers is None else workers), "--dist", "loadscope"]
    else:
        selected = ["-n", "0"]
    return ["-q", "-m", stage.marker, *selected, *(f"--ignore={path}" for path in stage.ignore)]


def private_desktop(stage: Stage) -> bool:
    return stage.name in (PARALLEL.name, SERIAL.name)


def log_header(command: Sequence[str], cwd: Path | str, kind: str) -> str:
    return "\n".join((f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {platform.platform()}",
                      f"python: {sys.executable} {sys.version.split()[0]}", f"cwd: {cwd}", f"desktop: {kind}",
                      f"command: {' '.join(map(str, command))}", "", ""))


def run_logged(command: Sequence[str], *, cwd: Path | str, env: Mapping[str, str], log: Path,
               private: bool = False) -> int:
    """Run ``command`` with its output shown live and kept in ``log``; return its exit code."""
    log.parent.mkdir(parents=True, exist_ok=True)
    process, kind = desktop.start(command, cwd=cwd, env=env, private=private)
    with log.open("wb") as sink:
        sink.write(log_header(command, cwd, kind).encode("utf-8"))
        for chunk in iter(process.stdout.readline, b""):
            sink.write(chunk)
            sink.flush()
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    return process.wait()


def failure_lines(log: Path) -> list[str]:
    """The last twenty ``FAILED`` or ``ERROR`` lines; the log's last twenty lines when it holds none."""
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return ([line for line in lines if FAILURE_LINE.match(line)] or lines)[-REPORTED_LINES:]


@dataclass(frozen=True)
class Outcome:
    """A refused run: its token, the stage and exit code, and the log; ``token`` is ``None`` for a pass."""
    token: str | None = None
    stage: Stage | None = None
    code: int = 0
    log: Path | None = None


def run_stages(runner: Sequence[str], paths: Sequence[str], *, cwd: Path | str, stage_list: Sequence[Stage],
               env_for: Callable[[Stage], Mapping[str, str]], options_for: Callable[[Stage], Sequence[str]],
               log_for: Callable[[Stage], Path], workers: int | None = None, run: Run = run_logged) -> Outcome:
    """Run each stage in order until one fails; a stage that collects no test passes, a run of only those is refused."""
    collected = False
    for stage in stage_list:
        log = log_for(stage)
        command = [*runner, *pytest_arguments(stage, workers), *options_for(stage), *paths]
        code = run(command, cwd=cwd, env=env_for(stage), log=log, private=private_desktop(stage))
        if code not in (0, PYTEST_NO_TESTS_COLLECTED):
            return Outcome(TESTS_FAILED, stage, code, log)
        collected = collected or code == 0
    if not collected:
        return Outcome(NO_TESTS_COLLECTED, code=PYTEST_NO_TESTS_COLLECTED)
    return Outcome()

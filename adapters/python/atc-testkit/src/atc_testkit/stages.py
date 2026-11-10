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
import tomllib

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
HOST_CONFIG_NAME = "config.toml"
WORKERS_KEY = "test_workers"
CONFIG_INVALID = "ATC_CONFIG_INVALID"
CONFIG_EXIT = 9
SOURCE_FLAG = "flag"
SOURCE_HOST = "~/.atc/config.toml"
SOURCE_DEFAULT = "default"

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


@dataclass(frozen=True)
class Workers:
    """The parallel stage's worker count and where it came from: the flag, the host file or the default."""
    count: int
    source: str

    def line(self) -> str:
        return f"workers: {self.count} ({self.source})"


def host_config() -> Path:
    """``~/.atc/config.toml`` of the user the process runs as (docs/ATC_HOME.md § Layout)."""
    return Path.home() / ".atc" / HOST_CONFIG_NAME


def positive_count(value: object) -> int | None:
    """``value`` as a worker count, an integer of 1 or more; ``None`` for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = int(value) if value.isdecimal() else None
    return value if isinstance(value, int) and value >= 1 else None


def resolve_workers(flag: int | None = None, environ: Mapping[str, str] | None = None,
                    config: Path | None = None) -> Workers:
    """The flag, then ``test_workers`` in the host file unless ``CI`` is set, then ``default_workers()``.

    A host file that cannot be read or parsed, or a key that is not an integer of 1 or more, raises
    ``locations.Refusal`` with ``ATC_CONFIG_INVALID``.
    """
    if flag is not None:
        return Workers(flag, SOURCE_FLAG)
    environ = os.environ if environ is None else environ
    if not environ.get("CI"):
        path = host_config() if config is None else config
        try:
            with path.open("rb") as handle:
                table = tomllib.load(handle)
        except FileNotFoundError:
            table = {}
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise locations.Refusal(CONFIG_INVALID, f"{path}: {error}", CONFIG_EXIT) from error
        if WORKERS_KEY in table:
            count = positive_count(table[WORKERS_KEY])
            if count is None:
                raise locations.Refusal(CONFIG_INVALID, f"{path}: {WORKERS_KEY} = {table[WORKERS_KEY]!r} is not an "
                                                        "integer of 1 or more", CONFIG_EXIT)
            return Workers(count, SOURCE_HOST)
    return Workers(default_workers(), SOURCE_DEFAULT)


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
               log_for: Callable[[Stage], Path], workers: Workers | int | None = None,
               run: Run = run_logged) -> Outcome:
    """Run each stage in order until one fails; a stage that collects no test passes, a run of only those is refused.

    ``workers`` is the caller's resolved count, an explicit integer, or ``None`` for ``resolve_workers()``; the
    parallel stage prints it before it starts.
    """
    if not isinstance(workers, Workers):
        workers = resolve_workers(workers)
    collected = False
    for stage in stage_list:
        log = log_for(stage)
        if stage.name == PARALLEL.name:
            print(workers.line(), flush=True)
        command = [*runner, *pytest_arguments(stage, workers.count), *options_for(stage), *paths]
        code = run(command, cwd=cwd, env=env_for(stage), log=log, private=private_desktop(stage))
        if code not in (0, PYTEST_NO_TESTS_COLLECTED):
            return Outcome(TESTS_FAILED, stage, code, log)
        collected = collected or code == 0
    if not collected:
        return Outcome(NO_TESTS_COLLECTED, code=PYTEST_NO_TESTS_COLLECTED)
    return Outcome()

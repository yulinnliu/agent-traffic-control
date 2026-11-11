"""The three stages and the runner with logging (docs/TESTKIT.md § Stages, § Running and logging).

A stage's ``marker`` is a category expression: category names joined by ``and``, ``or`` and ``not``. The toolchain
turns it into its own test filter; the Python toolchain passes it to ``pytest -m``.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import sys
import time
import tomllib

from . import desktop, locations, timing, toolchain as toolchains
from .evidence import Stage
from .toolchain import Toolchain

PARALLEL = Stage("parallel", "not slow and not serial and not interactive_desktop")
SERIAL = Stage("serial", "serial and not slow and not interactive_desktop")
INTERACTIVE = Stage("interactive", "interactive_desktop and not slow")
SLOW = Stage("slow", "slow")
STAGE_ORDER = (PARALLEL, SERIAL, INTERACTIVE)
TEST_COMMAND_STAGE = "tests"
BASE_DIRECTORY_STAGES = frozenset({TEST_COMMAND_STAGE, *(stage.name for stage in STAGE_ORDER)})
MAX_WORKERS = 8
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


CATEGORY_TOKEN = re.compile(r"\s*(\(|\)|[A-Za-z_][A-Za-z0-9_]*)")


def selects(expression: str, categories: Iterable[str]) -> bool:
    """Whether a test carrying ``categories`` is in a stage whose category expression is ``expression``.

    The expression holds category names, ``and``, ``or``, ``not`` and parentheses, with pytest's precedence.
    """
    held, tokens, at = frozenset(categories), [], 0
    while at < len(expression.rstrip()):
        match = CATEGORY_TOKEN.match(expression, at)
        if match is None:
            raise ValueError(f"not a category expression: {expression!r}")
        tokens.append(match.group(1))
        at = match.end()
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> str:
        nonlocal position
        if position >= len(tokens):
            raise ValueError(f"not a category expression: {expression!r}")
        position += 1
        return tokens[position - 1]

    def either() -> bool:
        value = both()
        while peek() == "or":
            take()
            value = both() or value
        return value

    def both() -> bool:
        value = negated()
        while peek() == "and":
            take()
            value = negated() and value
        return value

    def negated() -> bool:
        if peek() == "not":
            take()
            return not negated()
        token = take()
        if token == "(":
            value = either()
            if take() != ")":
                raise ValueError(f"not a category expression: {expression!r}")
            return value
        if token in ("and", "or", ")"):
            raise ValueError(f"not a category expression: {expression!r}")
        return token in held

    value = either()
    if position != len(tokens):
        raise ValueError(f"not a category expression: {expression!r}")
    return value


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


def most_host_workers() -> int:
    """The largest ``test_workers`` the host file may give: the logical CPU count."""
    return os.cpu_count() or 1


def most_flag_workers() -> int:
    """The largest ``--workers`` count: ``MAX_WORKERS``, or the logical CPU count when that is larger.

    A flag may oversubscribe a small runner up to ``MAX_WORKERS``; the host file may not.
    """
    return max(MAX_WORKERS, most_host_workers())


def flag_count(text: str) -> int | None:
    """A ``--workers`` value: ASCII digits naming 1 to ``most_flag_workers()``; ``None`` for anything else.

    Leading zeros go before the length check, so a value never reaches ``int`` longer than the bound's digits.
    """
    if not (text.isascii() and text.isdigit()):
        return None
    digits = text.lstrip("0") or "0"
    if len(digits) > len(str(most_flag_workers())):
        return None
    count = int(digits)
    return count if 1 <= count <= most_flag_workers() else None


def host_count(value: object) -> int | None:
    """A ``test_workers`` value: a TOML integer from 1 to ``most_host_workers()``; ``None`` for anything else."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value if 1 <= value <= most_host_workers() else None


def flag_rule() -> str:
    return f"an integer from 1 to {most_flag_workers()}, the larger of {MAX_WORKERS} and the logical CPU count"


def host_rule() -> str:
    return f"an integer from 1 to {most_host_workers()}, the logical CPU count"


def resolve_workers(flag: int | None = None, environ: Mapping[str, str] | None = None,
                    config: Path | None = None) -> Workers:
    """The flag, then ``test_workers`` in the host file unless ``CI`` is set, then ``default_workers()``.

    A host file that cannot be read, decoded or parsed, or a key that is not a TOML integer from 1 to
    ``most_host_workers()``, raises ``locations.Refusal`` with ``ATC_CONFIG_INVALID``. The flag is checked where it is
    parsed (``flag_count``).
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
        except (OSError, ValueError) as error:  # ValueError: TOMLDecodeError and UnicodeDecodeError
            raise locations.Refusal(CONFIG_INVALID, f"{path}: {error}", CONFIG_EXIT) from error
        if WORKERS_KEY in table:
            count = host_count(table[WORKERS_KEY])
            if count is None:
                raise locations.Refusal(CONFIG_INVALID, f"{path}: {WORKERS_KEY} = {table[WORKERS_KEY]!r} is not "
                                                        f"{host_rule()}", CONFIG_EXIT)
            return Workers(count, SOURCE_HOST)
    return Workers(default_workers(), SOURCE_DEFAULT)


def stage_workers(stage: Stage, workers: int | None = None) -> int | None:
    """The parallel stage's worker count; ``None`` for every other stage, which runs without workers."""
    if stage.name != PARALLEL.name:
        return None
    return default_workers() if workers is None else workers


def private_desktop(stage: Stage) -> bool:
    return stage.name in (PARALLEL.name, SERIAL.name)


def slack_line(env: Mapping[str, str] | None) -> str:
    """The time slack ``env`` gives a run, or the refusal an entry point makes of it."""
    try:
        return timing.resolve(env).line()
    except locations.Refusal as refusal:
        return f"time slack: {refusal.token} {refusal.detail}"


def log_header(command: Sequence[str], cwd: Path | str, kind: str, env: Mapping[str, str] | None = None) -> str:
    return "\n".join((f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {platform.platform()}",
                      f"python: {sys.executable} {sys.version.split()[0]}", f"cwd: {cwd}", f"desktop: {kind}",
                      f"command: {' '.join(map(str, command))}", slack_line(env), "", ""))


def run_logged(command: Sequence[str], *, cwd: Path | str, env: Mapping[str, str], log: Path,
               private: bool = False) -> int:
    """Run ``command`` with its output shown live and kept in ``log``; return its exit code."""
    log.parent.mkdir(parents=True, exist_ok=True)
    process, kind = desktop.start(command, cwd=cwd, env=env, private=private)
    with log.open("wb") as sink:
        sink.write(log_header(command, cwd, kind, env).encode("utf-8"))
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
               run: Run = run_logged, toolchain: Toolchain | None = None) -> Outcome:
    """Run each stage in order until one fails; a stage that collects no test passes, a run of only those is refused.

    ``workers`` is the caller's resolved count, an explicit integer, or ``None`` for ``resolve_workers()``; the
    parallel stage prints it before it starts. ``toolchain`` builds each command, the Python one by default.
    """
    toolchain = toolchains.resolve() if toolchain is None else toolchain
    if not isinstance(workers, Workers):
        workers = resolve_workers(workers)
    collected = False
    for stage in stage_list:
        log = log_for(stage)
        if stage.name == PARALLEL.name:
            print(workers.line(), slack_line(env_for(stage)), sep="\n", flush=True)
        command = toolchain.stage_command(runner, stage, stage_workers(stage, workers.count), options_for(stage), paths)
        code = run(command, cwd=cwd, env=env_for(stage), log=log, private=private_desktop(stage))
        if code not in (0, toolchain.no_tests_exit):
            return Outcome(TESTS_FAILED, stage, code, log)
        collected = collected or code == 0
    if not collected:
        return Outcome(NO_TESTS_COLLECTED, code=toolchain.no_tests_exit)
    return Outcome()

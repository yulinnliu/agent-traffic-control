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

from . import config as project_config, desktop, locations, timing, toolchain as toolchains, workers as host_workers
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
POOL_KEY = "test_worker_pool"
MAX_WAIT_KEY = "test_worker_max_wait"
MOST_MAX_WAIT = 86400
MEMORY_KEY = "worker_memory_mib"
MOST_WORKER_MEMORY_MIB = 65536
CONFIG_INVALID = "ATC_CONFIG_INVALID"
CONFIG_EXIT = 9
SOURCE_FLAG = "flag"
SOURCE_HOST = "~/.atc/config.toml"
SOURCE_DEFAULT = "default"
SOURCE_NESTED = "nested in a budgeted run"

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
    """The parallel stage's worker count and where it came from: the flag, the host file, the default or a nesting
    run; ``requested`` and ``clamp`` when memory cut the count, ``budget`` when the host pool did."""
    count: int
    source: str
    requested: int | None = None
    clamp: str = ""
    budget: str = ""

    def line(self) -> str:
        text = self.source + (f" {self.requested}" if self.requested not in (None, self.count) else "")
        text += f", {self.clamp}" if self.clamp else ""
        text += f"; {self.budget}" if self.budget else ""
        return f"workers: {self.count} ({text})"

    def granted(self, grant: host_workers.Grant) -> Workers:
        """This count as the host pool granted it; a flag above the pool size runs the pool size and names it."""
        if self.source == SOURCE_FLAG and grant.pooled and grant.count < self.count:
            clamp = ", ".join(part for part in (self.clamp, f"host pool: {grant.size}") if part)
            note = grant.note(grant.count)
            return Workers(grant.count, self.source, self.requested or self.count, clamp, note)
        note = grant.note(self.count)
        if not note:
            return self
        return Workers(grant.count, self.source, self.requested or self.count, self.clamp, note)

    def floor(self) -> int:
        """The fewest workers the parallel stage starts with: a flag's whole count (the pool caps it at its size),
        otherwise ``FLOOR``."""
        return self.count if self.source == SOURCE_FLAG else min(host_workers.FLOOR, self.count)


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


def host_table(environ: Mapping[str, str], config: Path | None = None) -> dict:
    """The host file's table; empty with ``CI`` set or no file. One that cannot be read, decoded or parsed raises
    ``locations.Refusal`` with ``ATC_CONFIG_INVALID``."""
    if environ.get("CI"):
        return {}
    path = host_config() if config is None else config
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:  # ValueError: TOMLDecodeError and UnicodeDecodeError
        raise locations.Refusal(CONFIG_INVALID, f"{path}: {error}", CONFIG_EXIT) from error


def _host_integer(table: dict, key: str, least: int, most: int, config: Path | None) -> int | None:
    if key not in table:
        return None
    value = table[key]
    if isinstance(value, int) and not isinstance(value, bool) and least <= value <= most:
        return value
    path = host_config() if config is None else config
    raise locations.Refusal(CONFIG_INVALID, f"{path}: {key} = {value!r} is not an integer from {least} to {most}",
                            CONFIG_EXIT)


def worker_memory(root: Path | None) -> int:
    """Bytes one worker is counted to need: the project's ``worker_memory_mib``, 1024 MiB by default.

    A value that is not an integer from 1 to 65536 raises ``locations.Refusal`` with ``ATC_CONFIG_INVALID``.
    """
    value = project_config.tool_table(root).get(MEMORY_KEY) if root is not None else None
    if value is None:
        return host_workers.PER_WORKER_MIB * host_workers.MIB
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MOST_WORKER_MEMORY_MIB:
        return value * host_workers.MIB
    raise locations.Refusal(CONFIG_INVALID, f"{Path(root) / 'pyproject.toml'}: [tool.atc-testkit] {MEMORY_KEY} = "
                                            f"{value!r} is not an integer from 1 to {MOST_WORKER_MEMORY_MIB}",
                            CONFIG_EXIT)


def resolve_workers(flag: int | None = None, environ: Mapping[str, str] | None = None,
                    config: Path | None = None, root: Path | None = None) -> Workers:
    """The flag, then a nesting run's 1, then ``test_workers`` in the host file unless ``CI`` is set, then
    ``default_workers()``.

    The flag and the default are cut to the workers available memory holds (``worker_memory(root)`` each, after
    ``workers.RESERVE``); the CPU count never cuts a flag. A host file that cannot be read, decoded or parsed, or a
    key that is not a TOML integer from 1 to ``most_host_workers()``, raises ``locations.Refusal`` with
    ``ATC_CONFIG_INVALID``. The flag is checked where it is parsed (``flag_count``).
    """
    environ = os.environ if environ is None else environ

    def within_memory(count: int, source: str) -> Workers:
        available = host_workers.available_memory()
        cap = host_workers.memory_cap(available, worker_memory(root))
        if cap is not None and cap < count:
            return Workers(cap, source, count, host_workers.memory_note(available))
        return Workers(count, source)

    if flag is not None:
        return within_memory(flag, SOURCE_FLAG)
    if environ.get(host_workers.HELD):
        return Workers(1, SOURCE_NESTED)
    table = host_table(environ, config)
    if WORKERS_KEY in table:
        count = host_count(table[WORKERS_KEY])
        if count is None:
            raise locations.Refusal(CONFIG_INVALID, f"{host_config() if config is None else config}: {WORKERS_KEY} = "
                                                    f"{table[WORKERS_KEY]!r} is not {host_rule()}", CONFIG_EXIT)
        return Workers(count, SOURCE_HOST)
    return within_memory(default_workers(), SOURCE_DEFAULT)


def pool_directory(environ: Mapping[str, str]) -> Path:
    """``~/.atc/run`` of the user the process runs as, or the absolute ``ATC_WORKER_POOL_DIR``."""
    named = environ.get(host_workers.POOL_DIRECTORY)
    return Path(named) if named and os.path.isabs(named) else Path.home() / ".atc" / "run"


def pool_size(table: dict, config: Path | None = None) -> int:
    """``test_worker_pool``, 0 to the logical CPU count; three quarters of the logical CPUs by default."""
    size = _host_integer(table, POOL_KEY, 0, most_host_workers(), config)
    return host_workers.default_pool_size(most_host_workers()) if size is None else size


def open_budget(label: str, environ: Mapping[str, str] | None = None, config: Path | None = None,
                root: Path | None = None, max_wait: float | None = None) -> host_workers.Budget:
    """The host budget of one entry point (docs/TESTKIT.md § Host worker budget).

    Outside the pool: with ``CI`` set, inside a run that holds tokens (``ATC_TEST_WORKERS_HELD``), and with
    ``test_worker_pool = 0``. The wait limit is ``ATC_WORKER_MAX_WAIT``, else ``max_wait`` (the run's own timeout),
    else ``test_worker_max_wait``, else ``workers.MAX_WAIT_SECONDS``.
    """
    environ = os.environ if environ is None else environ
    per_worker = worker_memory(root)
    if environ.get("CI") or environ.get(host_workers.HELD):
        return host_workers.Budget(None, label=label, per_worker=per_worker)
    table = host_table(environ, config)
    size = pool_size(table, config)
    limit = _host_integer(table, MAX_WAIT_KEY, 1, MOST_MAX_WAIT, config)
    named = environ.get(host_workers.MAX_WAIT, "")
    try:
        named_limit = float(named) if named else None
    except ValueError:
        named_limit = None
    if named_limit is not None and named_limit > 0:
        limit = named_limit
    elif max_wait is not None:
        limit = max_wait
    if size == 0:
        return host_workers.Budget(None, label=label, per_worker=per_worker)
    wait_file = environ.get(host_workers.WAIT_FILE)
    return host_workers.Budget(host_workers.Pool(pool_directory(environ), size), label=label,
                               max_wait=host_workers.MAX_WAIT_SECONDS if limit is None else limit,
                               wait_file=Path(wait_file) if wait_file else None, per_worker=per_worker)


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
    job = host_workers.StageJob(process)
    try:
        with log.open("wb") as sink:
            sink.write(log_header(command, cwd, kind, env).encode("utf-8"))
            for chunk in iter(process.stdout.readline, b""):
                sink.write(chunk)
                sink.flush()
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        return process.wait()
    finally:
        job.close()


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
               run: Run = run_logged, toolchain: Toolchain | None = None,
               budget: host_workers.Budget | None = None) -> Outcome:
    """Run each stage in order until one fails; a stage that collects no test passes, a run of only those is refused.

    ``workers`` is the caller's resolved count, an explicit integer, or ``None`` for ``resolve_workers()``; the
    parallel stage prints it before it starts. ``toolchain`` builds each command, the Python one by default.
    ``budget`` takes each stage's tokens from the host pool before it starts and gives them back when it ends: the
    parallel stage's count, down to ``Workers.floor()``; one for every other stage. ``WaitTimeout`` passes through.
    """
    toolchain = toolchains.resolve() if toolchain is None else toolchain
    if not isinstance(workers, Workers):
        workers = resolve_workers(workers)
    budget = host_workers.Budget(None, label="") if budget is None else budget
    collected = False
    for stage in stage_list:
        log = log_for(stage)
        parallel = stage.name == PARALLEL.name
        grant = budget.acquire(stage.name, workers.count if parallel else 1, workers.floor() if parallel else 1)
        try:
            env = dict(env_for(stage))
            if budget.active:
                env[host_workers.HELD] = budget.held_id()
            granted = workers.granted(grant)
            if parallel:
                print(granted.line(), slack_line(env), sep="\n", flush=True)
            command = toolchain.stage_command(runner, stage, stage_workers(stage, granted.count), options_for(stage),
                                              paths)
            code = run(command, cwd=cwd, env=env, log=log, private=private_desktop(stage))
        finally:
            budget.release(grant)
        if code not in (0, toolchain.no_tests_exit):
            return Outcome(TESTS_FAILED, stage, code, log)
        collected = collected or code == 0
    if not collected:
        return Outcome(NO_TESTS_COLLECTED, code=toolchain.no_tests_exit)
    return Outcome()

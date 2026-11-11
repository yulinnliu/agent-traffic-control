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
ITEMS_PER_WORKER = 8
WORKERS_LINE = "ATC_TEST_WORKERS_LINE"
COLLECTED = re.compile(r"(\d+)(?:/\d+)? tests? collected")
NO_TESTS = re.compile(r"no tests (?:collected|ran)")
COLLECT_TIMEOUT = 300
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
    """The default count outside the host pool, before the memory ceiling: the logical CPU count."""
    return most_host_workers()


def jobserver_note(environ: Mapping[str, str] | None = None) -> str:
    """A note when a GNU make jobserver is in ``MAKEFLAGS``: its tokens and the host pool's are separate."""
    flags = (os.environ if environ is None else environ).get("MAKEFLAGS", "")
    return "make jobserver present, not joined" if "--jobserver-auth" in flags else ""


@dataclass(frozen=True)
class Workers:
    """The parallel stage's worker count and where it came from: the flag, the host file, the default or a nesting
    run; ``requested`` and ``clamp`` when memory or the collected tests cut the count, ``budget`` when the host
    pool did, ``pool`` the pool's size when the count came from it."""
    count: int
    source: str
    requested: int | None = None
    clamp: str = ""
    budget: str = ""
    pool: int = 0

    def line(self) -> str:
        """``workers: <n>[ of <pool>] (<source>[ <asked>][, <clamp>][; <budget>])`` (docs/TESTKIT.md § Stages)."""
        text = self.source + (f" {self.requested}" if self.requested not in (None, self.count) else "")
        text += f", {self.clamp}" if self.clamp else ""
        text += f"; {self.budget}" if self.budget else ""
        jobserver = jobserver_note()
        text += f"; {jobserver}" if jobserver else ""
        return f"workers: {self.count}{f' of {self.pool}' if self.pool else ''} ({text})"

    def capped(self, items: int | None) -> Workers:
        """At most one worker per ``ITEMS_PER_WORKER`` collected tests, at least ``FLOOR``; only the default is cut."""
        if items is None or self.source != SOURCE_DEFAULT:
            return self
        most = max(host_workers.FLOOR, -(-items // ITEMS_PER_WORKER))
        if most >= self.count:
            return self
        clamp = ", ".join(part for part in (self.clamp, f"{items} tests collected, {ITEMS_PER_WORKER} per worker")
                          if part)
        return Workers(most, self.source, self.requested or self.count, clamp, self.budget, self.pool)

    def granted(self, grant: host_workers.Grant) -> Workers:
        """This count as the host pool granted it; a flag above the pool size runs the pool size and names it."""
        pool = grant.size if grant.pooled else 0
        if self.source == SOURCE_FLAG and grant.pooled and grant.count < self.count:
            clamp = ", ".join(part for part in (self.clamp, f"host pool: {grant.size}") if part)
            note = grant.note(grant.count)
            return Workers(grant.count, self.source, self.requested or self.count, clamp, note, pool)
        return Workers(grant.count, self.source, self.requested or self.count, self.clamp, grant.note(self.count),
                       pool)

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


MEMORY_DECLARED = "pyproject"
MEMORY_RECORDED = "recorded"
MEMORY_DEFAULT = "default"


def memory_key(root: Path | None) -> str | None:
    """The name a project's recorded worker memory is kept under: its ``[project] name``, else its directory's."""
    if root is None:
        return None
    name = project_config.read_pyproject(root).get("project", {}).get("name")
    return name if isinstance(name, str) and name else Path(root).name


def worker_memory_source(root: Path | None, environ: Mapping[str, str] | None = None) -> tuple[int, str]:
    """Bytes one worker is counted to need and where the figure came from: the project's ``worker_memory_mib``,
    else its recorded runs in the host pool's directory (not on CI), else ``PER_WORKER_MIB``.

    A declared value that is not an integer from 1 to 65536 raises ``locations.Refusal`` with ``ATC_CONFIG_INVALID``.
    """
    environ = os.environ if environ is None else environ
    value = project_config.tool_table(root).get(MEMORY_KEY) if root is not None else None
    if value is not None:
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MOST_WORKER_MEMORY_MIB:
            return value * host_workers.MIB, MEMORY_DECLARED
        raise locations.Refusal(CONFIG_INVALID, f"{Path(root) / 'pyproject.toml'}: [tool.atc-testkit] {MEMORY_KEY} "
                                                f"= {value!r} is not an integer from 1 to {MOST_WORKER_MEMORY_MIB}",
                                CONFIG_EXIT)
    key = memory_key(root)
    if key is not None and not environ.get("CI"):
        recorded = host_workers.recorded_memory(pool_directory(environ), key)
        if recorded is not None:
            return recorded, MEMORY_RECORDED
    return host_workers.PER_WORKER_MIB * host_workers.MIB, MEMORY_DEFAULT


def worker_memory(root: Path | None, environ: Mapping[str, str] | None = None) -> int:
    """Bytes one worker is counted to need (``worker_memory_source``)."""
    return worker_memory_source(root, environ)[0]


def resolve_workers(flag: int | None = None, environ: Mapping[str, str] | None = None,
                    config: Path | None = None, root: Path | None = None) -> Workers:
    """The flag, then a nesting run's 1, then ``test_workers`` in the host file unless ``CI`` is set, then the
    default: the host pool's size when the run is in the pool, else ``default_workers()``.

    The flag and the default outside the pool are cut to the workers available memory holds
    (``worker_memory_source(root)`` each, after ``workers.RESERVE``); a default in the pool is cut by memory only
    when it takes its tokens (``Budget.acquire``). The CPU count never cuts a flag. A host file that cannot be read,
    decoded or parsed, or a key that is not a TOML integer in its range, raises ``locations.Refusal`` with
    ``ATC_CONFIG_INVALID``. The flag is checked where it is parsed (``flag_count``).
    """
    environ = os.environ if environ is None else environ

    def within_memory(count: int, source: str) -> Workers:
        available = host_workers.available_memory()
        per_worker, origin = worker_memory_source(root, environ)
        cap = host_workers.memory_cap(available, per_worker)
        if cap is not None and cap < count:
            return Workers(cap, source, count, host_workers.memory_note(available, per_worker, origin))
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
    size = 0 if environ.get("CI") else pool_size(table, config)
    if size:
        return Workers(size, SOURCE_DEFAULT)
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
    per_worker, origin = worker_memory_source(root, environ)
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
                               wait_file=Path(wait_file) if wait_file else None, per_worker=per_worker,
                               per_worker_source=origin, memory_key=memory_key(root))


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
    """The log's first lines; the ``workers:`` line the run printed, when ``env`` carries it in ``WORKERS_LINE``."""
    workers_line = (env or {}).get(WORKERS_LINE, "")
    return "\n".join((f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {platform.platform()}",
                      f"python: {sys.executable} {sys.version.split()[0]}", f"cwd: {cwd}", f"desktop: {kind}",
                      f"command: {' '.join(map(str, command))}", *([workers_line] if workers_line else []),
                      slack_line(env), "", ""))


def collected_items(command: Sequence[str], cwd: Path | str, env: Mapping[str, str]) -> int | None:
    """The tests a ``--collect-only`` command reports; ``None`` when it fails or prints no count."""
    import subprocess
    try:
        done = subprocess.run(list(command), cwd=cwd, env=dict(env), capture_output=True, timeout=COLLECT_TIMEOUT,
                              stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = done.stdout.decode("utf-8", errors="replace")
    found = COLLECTED.findall(text)
    if found:
        return int(found[-1])
    return 0 if NO_TESTS.search(text) else None


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
               budget: host_workers.Budget | None = None,
               count: Callable[[Sequence[str], Path | str, Mapping[str, str]], int | None] | None = None) -> Outcome:
    """Run each stage in order until one fails; a stage that collects no test passes, a run of only those is refused.

    ``workers`` is the caller's resolved count, an explicit integer, or ``None`` for ``resolve_workers()``; the
    parallel stage prints it before it starts and writes it into its log's header. ``toolchain`` builds each
    command, the Python one by default. ``budget`` takes each stage's tokens from the host pool before it starts and
    gives them back when it ends: the parallel stage's count, down to ``Workers.floor()``; one for every other
    stage. ``WaitTimeout`` passes through. ``count`` (``collected_items``) collects a pooled parallel stage of the
    Python toolchain first, so a default count asks for no more than ``Workers.capped`` allows.
    """
    toolchain = toolchains.resolve() if toolchain is None else toolchain
    if not isinstance(workers, Workers):
        workers = resolve_workers(workers)
    budget = host_workers.Budget(None, label="") if budget is None else budget
    collected = False
    for stage in stage_list:
        log = log_for(stage)
        parallel = stage.name == PARALLEL.name
        wanted = workers
        if (parallel and count is not None and budget.active and workers.source == SOURCE_DEFAULT
                and toolchain.name == "python"):
            command = toolchain.stage_command(runner, stage, None, [*options_for(stage), "--collect-only"], paths)
            wanted = workers.capped(count(command, cwd, env_for(stage)))
        grant = budget.acquire(stage.name, wanted.count if parallel else 1, wanted.floor() if parallel else 1)
        try:
            env = dict(env_for(stage))
            if budget.active:
                env[host_workers.HELD] = budget.held_id()
            granted = wanted.granted(grant)
            budget.annotate(log=str(log))
            if parallel:
                env[WORKERS_LINE] = granted.line()
                budget.annotate(limit=env[WORKERS_LINE])
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

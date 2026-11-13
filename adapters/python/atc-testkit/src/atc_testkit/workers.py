"""The host's test worker budget: memory sizing and the pool of worker tokens (docs/TESTKIT.md § Host worker budget).

A token is a one-byte lock in ``<run>/workers.lock``; the operating system releases it when the process that holds
it ends, however it ends. Byte 0 is the turnstile a waiting run holds until its floor is free. Each process keeps one
ledger row, ``<run>/workers/<pid>-<id>.json``, locked at ``ROW_LOCK_OFFSET`` for its life; a row nobody holds is
stale. The rows feed the lines a run prints and ``atc doctor``; the locks alone decide who holds a token.
"""
from __future__ import annotations

import atexit
import errno
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import sys
import threading
import time
import uuid

from . import locations

if os.name == "nt":
    import msvcrt
else:
    import fcntl

GIB = 1024 ** 3
MIB = 1024 ** 2
RESERVE = GIB
PER_WORKER_MIB = 256
FLOOR = 2
MEMORY = "memory"
RECORDED_RUNS = 10
RECORD_MARGIN = 1.25
RECORD_LEAST_MIB = 128
RECORD_LEAST_WORKERS = 4
NONPAGED_WARN_GIB = 4
RAMP_SECONDS = 60
MAX_WAIT_SECONDS = 1800
POLL_SECONDS = 1.0
REPORT_SECONDS = 30
HELD = "ATC_TEST_WORKERS_HELD"
POOL_DIRECTORY = "ATC_WORKER_POOL_DIR"
WAIT_FILE = "ATC_WORKER_WAIT_FILE"
MAX_WAIT = "ATC_WORKER_MAX_WAIT"
POOL_FILE = "workers.lock"
ROWS = "workers"
ROW_BYTES = 4096
ROW_LOCK_OFFSET = 1 << 20
WAIT_TIMEOUT = "WORKER_WAIT_TIMEOUT"
WAIT_TIMEOUT_EXIT = 124
POOL_UNSUITABLE = "WORKER_POOL_UNSUITABLE"
POOL_UNAVAILABLE = "WORKER_POOL_UNAVAILABLE"
POOL_EXIT = 9
_BUSY = (errno.EACCES, errno.EAGAIN, errno.EDEADLK)


class WaitTimeout(locations.Refusal):
    """A run whose total wait for host workers passed its limit; ``detail`` names what it waited behind."""

    def __init__(self, detail: str):
        super().__init__(WAIT_TIMEOUT, detail, WAIT_TIMEOUT_EXIT)


def available_memory() -> int | None:
    """Bytes of physical memory available now: Windows and Linux (cgroup v2 limit included); ``None`` elsewhere."""
    try:
        if os.name == "nt":
            return _windows_available()
        if sys.platform.startswith("linux"):
            return _linux_available()
    except (OSError, ValueError):
        return None
    return None


def _windows_available() -> int | None:
    import ctypes
    from ctypes import wintypes

    class Status(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    *((name, ctypes.c_ulonglong) for name in ("total", "available", "page_total", "page_available",
                                                               "virtual_total", "virtual_available", "extended"))]

    status = Status()
    status.dwLength = ctypes.sizeof(Status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.available)


def _linux_available() -> int | None:
    available = None
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        if line.startswith("MemAvailable:"):
            available = int(line.split()[1]) * 1024
    group = Path("/sys/fs/cgroup")
    try:
        limit = (group / "memory.max").read_text(encoding="ascii").strip()
        if limit != "max":
            left = int(limit) - int((group / "memory.current").read_text(encoding="ascii"))
            available = left if available is None else min(available, left)
    except OSError:
        pass
    return available


def memory_cap(available: int | None, per_worker: int) -> int | None:
    """The workers ``available`` bytes hold after ``RESERVE``, at least 1; ``None`` when memory is unknown."""
    if available is None:
        return None
    return max(1, (available - RESERVE) // per_worker)


def memory_note(available: int, per_worker: int | None = None, source: str = "") -> str:
    each = f", {per_worker // MIB} MiB/worker {source}".rstrip() if per_worker else ""
    return f"memory ceiling: {available / GIB:.1f} GiB available{each}"


@dataclass(frozen=True)
class Performance:
    """Windows memory figures in bytes: physical total and available, and the kernel's nonpaged pool."""
    total: int
    available: int
    nonpaged: int


def performance() -> Performance | None:
    """``GetPerformanceInfo`` on Windows; ``None`` elsewhere or when the call fails."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD),
                    *((name, ctypes.c_size_t) for name in (
                        "CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal", "PhysicalAvailable",
                        "SystemCache", "KernelTotal", "KernelPaged", "KernelNonpaged", "PageSize")),
                    ("HandleCount", wintypes.DWORD), ("ProcessCount", wintypes.DWORD),
                    ("ThreadCount", wintypes.DWORD)]

    info = Info()
    info.cb = ctypes.sizeof(Info)
    try:
        if not ctypes.WinDLL("psapi").GetPerformanceInfo(ctypes.byref(info), info.cb):
            return None
    except OSError:
        return None
    page = info.PageSize
    return Performance(info.PhysicalTotal * page, info.PhysicalAvailable * page, info.KernelNonpaged * page)


def nonpaged_threshold(total: int, override_gib: int | None = None) -> int:
    """Bytes of kernel nonpaged pool above which ``atc doctor`` warns: the override, else max(4 GiB, total / 8)."""
    return override_gib * GIB if override_gib else max(NONPAGED_WARN_GIB * GIB, total // 8)


def _memory_file(directory: Path, key: str) -> Path:
    return directory / MEMORY / f"{''.join(c if c.isalnum() or c in '-_.' else '_' for c in key)[:80]}.json"


def recorded_memory(directory: Path, key: str) -> int | None:
    """Bytes a worker of project ``key`` is counted at from its recorded runs: their largest value times
    ``RECORD_MARGIN``, at least ``RECORD_LEAST_MIB``; ``None`` with no readable record."""
    try:
        values = json.loads(_memory_file(directory, key).read_text(encoding="utf-8"))["per_worker"]
        largest = max(int(value) for value in values)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return max(RECORD_LEAST_MIB * MIB, int(largest * RECORD_MARGIN))


def record_memory(directory: Path, key: str, per_worker: int) -> None:
    """Add one run's bytes per worker to project ``key``'s record, keeping the last ``RECORDED_RUNS``."""
    path = _memory_file(directory, key)
    try:
        values = json.loads(path.read_text(encoding="utf-8"))["per_worker"]
        values = [int(value) for value in values]
    except (OSError, ValueError, KeyError, TypeError):
        values = []
    values = [*values, int(per_worker)][-RECORDED_RUNS:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps({"per_worker": values}), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


_LAST_PEAK: list[int] = []
PEAK_IS_TREE = os.name == "nt"
"""Whether ``take_peak`` measures a stage's whole process tree (the Windows Job) rather than its largest process."""


def take_peak() -> int | None:
    """The memory peak of the last stage process tree this process ran: on Windows its Job's committed peak;
    elsewhere the largest resident size of any child so far. ``None`` when unknown."""
    if os.name == "nt":
        return _LAST_PEAK.pop() if _LAST_PEAK else None
    try:
        import resource
        largest = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    except (ImportError, OSError):
        return None
    return largest if sys.platform == "darwin" else largest * 1024


def default_pool_size(cpus: int) -> int:
    """Three quarters of the logical CPUs, rounded up: the rest stays for sessions, editors, Git and the OS."""
    return max(1, -(-cpus * 3 // 4))


def _try_lock(fd: int, offset: int) -> bool:
    try:
        if os.name == "nt":
            os.lseek(fd, offset, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, offset, os.SEEK_SET)
    except OSError as error:
        if error.errno in _BUSY:
            return False
        raise
    return True


def _unlock(fd: int, offset: int) -> None:
    if os.name == "nt":
        os.lseek(fd, offset, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.lockf(fd, fcntl.LOCK_UN, 1, offset, os.SEEK_SET)


def _open(path: Path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)


@dataclass
class _Shared:
    """The one descriptor a process keeps on a pool file, and the token bytes it holds: POSIX drops every lock a
    process holds on a file when it closes any descriptor of it, so the descriptor is never closed."""
    fd: int
    held: set[int] = field(default_factory=set)
    guard: threading.Lock = field(default_factory=threading.Lock)


_SHARED: dict[str, _Shared] = {}
_SHARED_GUARD = threading.Lock()


def _shared(path: Path) -> _Shared:
    with _SHARED_GUARD:
        key = os.path.normcase(str(path))
        if key not in _SHARED:
            _SHARED[key] = _Shared(_open(path))
        return _SHARED[key]


def holding() -> bool:
    """Whether this process holds a token of any pool."""
    return any(shared.held for shared in _SHARED.values())


@dataclass(frozen=True)
class Row:
    """One process's ledger row as another process reads it."""
    pid: int
    label: str
    stage: str
    tokens: int
    want: int
    floor: int
    granted_at: float | None
    waiting_since: float | None
    reserved: int
    path: Path
    log: str = ""
    limit: str = ""

    def held_for(self, now: float) -> str:
        return _minutes(now - (self.granted_at or now))

    def describe(self, now: float) -> str:
        if self.waiting_since is not None:
            return f"{self.label} pid {self.pid} waiting {_minutes(now - self.waiting_since)} for {self.floor}"
        return f"{self.label} pid {self.pid} {self.tokens} tokens for {self.held_for(now)}"


def _minutes(seconds: float) -> str:
    return f"{int(seconds // 60)}m" if seconds >= 60 else f"{int(seconds)}s"


def _read_row(path: Path) -> Row | None:
    try:
        with path.open("rb") as handle:
            data = json.loads(handle.read(ROW_BYTES).decode("utf-8").strip() or "null")
        return Row(int(data["pid"]), str(data["label"]), str(data["stage"]), len(data["tokens"]), int(data["want"]),
                   int(data["floor"]), data.get("granted_at"), data.get("waiting_since"), int(data["reserved"]), path,
                   str(data.get("log") or ""), str(data.get("limit") or ""))
    except (OSError, ValueError, KeyError, TypeError):
        return None


_OWN_ROWS: set[str] = set()


def _row_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _row_alive(path: Path, prune: bool = True) -> bool:
    """Whether a process holds the row's lock; with ``prune`` a row nobody holds is removed.

    A row of this process is alive without a probe: a POSIX record lock never conflicts with its own process, and
    unlocking or closing a probe descriptor would drop the process's lock on the row."""
    if _row_key(path) in _OWN_ROWS:
        return True
    try:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except OSError:
        return False
    try:
        if not _try_lock(fd, ROW_LOCK_OFFSET):
            return True
        _unlock(fd, ROW_LOCK_OFFSET)
    finally:
        os.close(fd)
    if prune:
        try:
            path.unlink()
        except OSError:
            pass
    return False


class _OwnRow:
    """This process's ledger row: locked for the process's life, rewritten in place, removed at exit."""

    def __init__(self, rows: Path, label: str):
        self.path = rows / f"{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
        self.fd = _open(self.path)
        if not _try_lock(self.fd, ROW_LOCK_OFFSET):
            raise OSError(f"{self.path} is locked by another process")
        _OWN_ROWS.add(_row_key(self.path))
        self.data: dict = {"pid": os.getpid(), "label": label, "stage": "", "tokens": [], "want": 0, "floor": 0,
                           "granted_at": None, "waiting_since": None, "reserved": 0, "log": "", "limit": ""}
        atexit.register(self.remove)

    @property
    def id(self) -> str:
        return self.path.stem

    def update(self, **values) -> None:
        self.data.update(values)
        text = json.dumps(self.data).encode("utf-8")[:ROW_BYTES]
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.write(self.fd, text + b" " * (ROW_BYTES - len(text)))

    def remove(self) -> None:
        _OWN_ROWS.discard(_row_key(self.path))
        try:
            _unlock(self.fd, ROW_LOCK_OFFSET)
            os.close(self.fd)
            self.path.unlink()
        except OSError:
            pass


@dataclass(frozen=True)
class Status:
    """What the pool looks like now: its size, the tokens free, and the other live rows."""
    size: int
    free: int
    runs: tuple[Row, ...]
    waiters: tuple[Row, ...]


class Pool:
    """``size`` tokens in ``directory``; one instance per process and directory is enough, more are safe."""

    def __init__(self, directory: Path, size: int):
        not_real = locations.not_a_real_directory(directory)
        if not_real:
            raise locations.Refusal(POOL_UNSUITABLE, not_real, POOL_EXIT)
        try:
            (directory / ROWS).mkdir(parents=True, exist_ok=True)
            self.directory, self.size = directory, size
            self.shared = _shared(directory / POOL_FILE)
        except OSError as error:
            raise unavailable(directory, error) from error

    def try_take(self, index: int) -> bool:
        shared = self.shared
        with shared.guard:
            if index in shared.held or not _try_lock(shared.fd, index):
                return False
            shared.held.add(index)
            return True

    def give(self, indices) -> None:
        shared = self.shared
        with shared.guard:
            for index in list(indices):
                if index in shared.held:
                    _unlock(shared.fd, index)
                    shared.held.discard(index)

    def free(self) -> int:
        """Tokens no process holds, found by locking and unlocking each one this process does not hold."""
        shared, count = self.shared, 0
        with shared.guard:
            for index in range(1, self.size + 1):
                if index not in shared.held and _try_lock(shared.fd, index):
                    _unlock(shared.fd, index)
                    count += 1
        return count

    def rows(self, own: Path | None = None, prune: bool = True) -> list[Row]:
        """Every live row but ``own``, oldest first; with ``prune`` stale rows are removed on the way."""
        found = []
        for path in sorted((self.directory / ROWS).glob("*.json")):
            if own is not None and path == own:
                continue
            if _row_alive(path, prune):
                row = _read_row(path)
                if row is not None:
                    found.append(row)
        return sorted(found, key=lambda row: row.granted_at or row.waiting_since or 0)

    def status(self, own: Path | None = None, prune: bool = True) -> Status:
        rows = self.rows(own, prune)
        return Status(self.size, self.free(), tuple(row for row in rows if row.tokens),
                      tuple(row for row in rows if row.waiting_since is not None))


def unavailable(directory: Path, error: OSError) -> locations.Refusal:
    return locations.Refusal(POOL_UNAVAILABLE, f"{directory}: {error}; test_worker_pool = 0 in ~/.atc/config.toml "
                                               "turns the host budget off", POOL_EXIT)


@dataclass(frozen=True)
class Grant:
    """The workers a stage got: ``count``, the token bytes behind it, and what the budget line says about it."""
    count: int
    tokens: tuple[int, ...] = ()
    waited: float = 0.0
    free: int = 0
    size: int = 0
    active: int = 0
    pooled: bool = False
    memory: str = ""

    def note(self, wanted: int) -> str:
        """What cut or delayed the grant: ``memory ceiling: ...`` when memory held fewer than ``wanted``, and
        ``host budget: ...`` when the pool cut the count, made the run wait, or another run holds tokens."""
        if not self.pooled:
            return ""
        notes = [self.memory] if self.memory and self.count < wanted else []
        pool_cut = self.count < wanted and not notes
        if pool_cut or self.waited or self.active:
            parts = []
            if self.waited:
                parts.append(f"waited {self.waited:.0f}s")
            if pool_cut:
                parts.append(f"{self.free} of {self.size} free")
            parts.append(f"{self.active} run{'' if self.active == 1 else 's'} active")
            notes.append("host budget: " + ", ".join(parts))
        return "; ".join(notes)


class Budget:
    """One entry point's share of the pool: ``pool`` ``None`` is a run outside it (CI, nested, turned off)."""

    def __init__(self, pool: Pool | None, *, label: str, max_wait: float = MAX_WAIT_SECONDS,
                 wait_file: Path | None = None, per_worker: int = PER_WORKER_MIB * MIB,
                 per_worker_source: str = "default", memory_key: str | None = None):
        self.pool, self.label, self.max_wait = pool, label, max_wait
        self.wait_file, self.per_worker = wait_file, per_worker
        self.per_worker_source, self.memory_key = per_worker_source, memory_key
        self.waited = 0.0
        self.row: _OwnRow | None = None

    @property
    def active(self) -> bool:
        return self.pool is not None

    def held_id(self) -> str:
        """The value of ``ATC_TEST_WORKERS_HELD`` a stage of this run gets."""
        return self.row.id if self.row is not None else f"{os.getpid()}"

    def _own_row(self) -> _OwnRow:
        if self.row is None:
            try:
                self.row = _OwnRow(self.pool.directory / ROWS, self.label)
            except OSError as error:
                raise unavailable(self.pool.directory, error) from error
        return self.row

    def _memory_room(self, own: Path) -> tuple[int | None, int]:
        """Workers memory holds now, after the reservations of runs granted within ``RAMP_SECONDS``, and the bytes
        that count as available; ``(None, 0)`` when memory is unknown."""
        available = available_memory()
        if available is None:
            return None, 0
        now = time.time()
        ramping = sum(row.reserved for row in self.pool.rows(own)
                      if row.granted_at is not None and now - row.granted_at < RAMP_SECONDS)
        return memory_cap(available - ramping, self.per_worker), available - ramping

    def annotate(self, **values) -> None:
        """Add ``log`` or ``limit`` to this run's ledger row, for ``atc test status``."""
        if self.row is not None:
            self.row.update(**values)

    def acquire(self, stage: str, want: int, floor: int) -> Grant:
        """At least ``floor`` and at most ``want`` tokens, waiting in turn when fewer are free.

        A run holds no token while it waits, and the turnstile it holds while it waits keeps later runs from taking
        the tokens released meanwhile. A total wait over ``max_wait`` raises ``WaitTimeout``.
        """
        if self.pool is None:
            return Grant(want)
        pool, row = self.pool, self._own_row()
        floor = max(1, min(floor, pool.size))
        want = max(floor, min(want, pool.size))
        row.update(stage=stage, want=want, floor=floor, tokens=[], waiting_since=None, granted_at=None, reserved=0)
        started, reported, turnstile, waited = time.monotonic(), None, False, 0.0
        taken: list[int] = []
        try:
            while True:
                if not turnstile:
                    turnstile = pool.try_take(0)
                if turnstile:
                    room, available = self._memory_room(row.path)
                    wanted = want if room is None else min(want, max(floor, room))
                    memory = memory_note(available, self.per_worker, self.per_worker_source) \
                        if room is not None and room < want else ""
                    free = pool.free()
                    for index in range(1, pool.size + 1):
                        if len(taken) == wanted:
                            break
                        if pool.try_take(index):
                            taken.append(index)
                    if len(taken) >= floor:
                        if reported is not None:
                            waited = time.monotonic() - started
                            self._record_wait(waited)
                        active = len(pool.status(row.path).runs)
                        row.update(tokens=taken, granted_at=time.time(), waiting_since=None,
                                   reserved=len(taken) * self.per_worker)
                        granted, taken = tuple(taken), []
                        return Grant(len(granted), granted, waited, free, pool.size, active, True, memory)
                    pool.give(taken)
                    taken = []
                waited = time.monotonic() - started
                if self.waited + waited > self.max_wait:
                    raise WaitTimeout(self._behind(stage, floor, waited, row.path))
                now = time.monotonic()
                if reported is None:
                    row.update(waiting_since=time.time())
                if reported is None or now - reported >= REPORT_SECONDS:
                    print(self._wait_line(stage, want, floor, row.path), flush=True)
                    reported = now
                self._write_wait(self.waited + waited)
                time.sleep(POLL_SECONDS)
        finally:
            if taken:
                pool.give(taken)
                row.update(tokens=[], waiting_since=None)
            if turnstile:
                pool.give([0])

    def release(self, grant: Grant) -> None:
        if self.pool is not None and grant.tokens:
            self.pool.give(grant.tokens)
            if self.row is not None:
                self.row.update(tokens=[], reserved=0, log="", limit="")
            self._record(grant.count)

    def _record(self, count: int) -> None:
        """Record the per-worker memory of the stage that just ended, when it ran at least
        ``RECORD_LEAST_WORKERS`` workers so the fixed cost of its main process stays small beside them."""
        peak = take_peak()
        if self.memory_key is None or peak is None or count < RECORD_LEAST_WORKERS:
            return
        record_memory(self.pool.directory, self.memory_key, peak // count if PEAK_IS_TREE else peak)

    def _record_wait(self, waited: float) -> None:
        self.waited += waited
        self._write_wait(self.waited)

    def _write_wait(self, total: float) -> None:
        """The run's total wait so far, for the caller that times this process (``atc integrate``)."""
        if self.wait_file is None:
            return
        try:
            self.wait_file.write_text(f"{total:.1f}\n", encoding="ascii")
        except OSError:
            pass

    def _wait_line(self, stage: str, want: int, floor: int, own: Path) -> str:
        status, now = self.pool.status(own), time.time()
        oldest = f", oldest {status.runs[0].label} pid {status.runs[0].pid} for {status.runs[0].held_for(now)}" \
            if status.runs else ""
        return (f"WAIT host workers: {stage} needs {floor} (wants {want}), {status.free} of {status.size} free; "
                f"{len(status.runs)} run{'' if len(status.runs) == 1 else 's'} active{oldest}")

    def _behind(self, stage: str, floor: int, waited: float, own: Path) -> str:
        status, now = self.pool.status(own), time.time()
        ahead = [row.describe(now) for row in (*status.runs, *status.waiters)]
        return (f"waited {self.waited + waited:.0f}s for {floor} host workers ({stage}), the limit is "
                f"{self.max_wait:g}s; behind: {', '.join(ahead) or 'no live run'}\nnext: atc doctor")


def wait_total(path: Path) -> float:
    """The total wait a run wrote to its wait file; 0 when it wrote none or is mid-write."""
    try:
        return max(0.0, float(path.read_text(encoding="ascii").strip() or 0))
    except (OSError, ValueError):
        return 0.0


class StageJob:
    """On Windows, a kill-on-close Job holding a stage's process tree, so the tree ends with the process holding
    its tokens; ``close`` ends what is left of it. Elsewhere, and when the Job cannot be made, it does nothing."""

    def __init__(self, process) -> None:
        self.handle = None
        if os.name != "nt" or not holding():
            return
        try:
            self.handle = _kill_on_close_job(_process_handle(process))
        except OSError:
            self.handle = None

    def close(self) -> None:
        """End what is left of the tree, keeping the Job's committed memory peak for ``take_peak``."""
        if self.handle is not None:
            import ctypes
            peak = _job_peak(self.handle)
            _LAST_PEAK.clear()
            if peak:
                _LAST_PEAK.append(peak)
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def _process_handle(process) -> int:
    handle = getattr(process, "_handle", None)
    if handle is None:
        handle = process._process.hProcess
    return int(handle)


def _limit_structure():
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    return Extended


def _job_peak(job: int) -> int | None:
    """The committed memory peak of every process the Job has held (``PeakJobMemoryUsed``)."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.c_void_p]
    limits = _limit_structure()()
    if not kernel.QueryInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits), None):
        return None
    return int(limits.PeakJobMemoryUsed)


def _kill_on_close_job(process_handle: int) -> int:
    import ctypes
    from ctypes import wintypes

    Extended = _limit_structure()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Extended()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not (kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            and kernel.AssignProcessToJobObject(job, process_handle)):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(job)
        raise error
    return job

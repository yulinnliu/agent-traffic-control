"""The scratch registry and deletion (docs/ATC_HOME.md § Test scratch, § Scratch registry, § Deleting)."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import tomllib
from typing import NamedTuple

from . import isolation, locations, owner, stages, toolchain
from .git import git_env
from .locations import CANDIDATES, Refusal, SYSTMP, is_link as _is_link

SCRATCH_NAME = re.compile(r"[A-Za-z0-9._-]+")
TASK_ID = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+")
RESERVED_NAMES = frozenset({SYSTMP, CANDIDATES})
SCRATCH_NAME_BOUND = 64
REGISTRY_NAME = "scratch-dirs.tsv"
LOCK_NAME = "scratch-dirs.lock"
REGISTRY_HEADER = "project\tscratch\ttree\tcreated_utc\ttask\tsource\tcommit"
HEADER_START = "project\tscratch\t"
OLD_FIELDS = 4
PRUNE_AGE = timedelta(days=7)
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LOCK_SECONDS = 60.0
WINDOW_HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class Row(NamedTuple):
    """A registry line; a line of the four-column format has an empty task, source and commit."""
    project: str
    scratch: str
    tree: str
    created_utc: str
    task: str = ""
    source: str = ""
    commit: str = ""


def _is_reserved(name: str) -> bool:
    return name.lower() in RESERVED_NAMES


def resolve_scratch(argument: str, root: Path) -> Path:
    """``<tmp>/<scratch>`` for a name or a path that resolves to a direct child of ``root``; refusals exit 9."""
    try:
        chosen = os.path.join(root, argument) if SCRATCH_NAME.fullmatch(argument) else os.path.abspath(argument)
        real = locations.long_name(chosen)
    except (OSError, ValueError) as error:
        raise Refusal("SCRATCH_NOT_RESOLVED", f"{argument!r}: {error}") from error
    parent, name = os.path.split(real)
    if not locations.same_path(parent, root) or not SCRATCH_NAME.fullmatch(name) or _is_reserved(name):
        raise Refusal("SCRATCH_OUTSIDE_TMP", f"{argument}: a scratch is a direct child of {root}, "
                                             f"not named {' or '.join(sorted(RESERVED_NAMES))}")
    if len(real) > SCRATCH_NAME_BOUND:
        raise Refusal("SCRATCH_PATH_TOO_LONG", f"{real} ({len(real)} characters, limit {SCRATCH_NAME_BOUND})")
    return Path(real)


def project_name(tree: Path) -> str:
    """``[project].name`` of the tree's ``pyproject.toml``; the tree's directory name when absent."""
    try:
        name = tomllib.loads((tree / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"]
    except (OSError, ValueError, KeyError, TypeError):
        return tree.name
    return name if isinstance(name, str) and name else tree.name


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    """Hold ``<tmp>/scratch-dirs.lock``; the operating system releases it when this process ends."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / LOCK_NAME).open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + LOCK_SECONDS
            while True:
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield


def _registry_lines(root: Path) -> list[str]:
    """The lines below the header."""
    try:
        text = (root / REGISTRY_NAME).read_bytes().decode("utf-8")
    except FileNotFoundError:
        return []
    return [line for line in text.split("\n") if line and not line.startswith(HEADER_START)]


def _parse(line: str) -> Row | None:
    fields = line.split("\t")
    return Row(*fields) if len(fields) in (OLD_FIELDS, len(Row._fields)) else None


def _write_registry(root: Path, lines: Iterable[str]) -> None:
    """Replace the file in one step."""
    target = root / REGISTRY_NAME
    staged = root / f"{REGISTRY_NAME}.{os.getpid()}"
    staged.write_bytes(("\n".join([REGISTRY_HEADER, *lines]) + "\n").encode("utf-8"))
    os.replace(staged, target)


def registered(root: Path) -> list[Row]:
    return [row for row in map(_parse, _registry_lines(root)) if row is not None]


def register(root: Path, scratch: Path, tree: Path, now: datetime | None = None, *, task: str = "",
             source: str = "", commit: str = "", project: str | None = None) -> None:
    """Add the scratch's line when it has none, or fill an empty task of its line; ``project`` defaults to the
    tree's ``project_name``. A line that names another task refuses ``task`` with ``SCRATCH_TASK_MISMATCH``."""
    field = lambda value: re.sub(r"[\t\r\n]", " ", str(value))  # noqa: E731 - the format has no quoting
    created = (now or datetime.now(UTC)).strftime(TIME_FORMAT)
    with _locked(root):
        lines = _registry_lines(root)
        for index, row in enumerate(map(_parse, lines)):
            if row and locations.same_path(row.scratch, scratch):
                if task and row.task and row.task != task:
                    raise Refusal("SCRATCH_TASK_MISMATCH", f"{scratch} belongs to {row.task}, not {task}; name "
                                                           "another scratch")
                if task and not row.task:
                    lines[index] = "\t".join(map(field, row._replace(task=task)))
                    _write_registry(root, lines)
                return
        _write_registry(root, [*lines, "\t".join(map(field, (project or project_name(tree), scratch, tree, created, task, source, commit)))])


def _unregister(root: Path, directory: str) -> None:
    if not (root / REGISTRY_NAME).exists():
        return
    with _locked(root):
        lines = _registry_lines(root)
        keep = [line for line in lines
                if not ((row := _parse(line)) and locations.same_path(row.scratch, directory))]
        if len(keep) != len(lines):
            _write_registry(root, keep)


def _extended(path: str) -> str:
    r"""The form Windows accepts beyond its path limit, ``\\?\`` plus the absolute path."""
    if os.name != "nt" or path.startswith("\\\\?\\"):
        return path
    return "\\\\?\\" + os.path.abspath(path)


def _links(directory: str) -> list[str]:
    """Every link below ``directory``, sorted; a link is reported and never entered."""
    found, pending = [], [directory]
    while pending:
        current = pending.pop()
        with os.scandir(_extended(current)) as entries:
            for entry in entries:
                path = os.path.join(current, entry.name)
                if _is_link(entry.stat(follow_symlinks=False)):
                    found.append(path)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    return sorted(found)


def _remove_link(path: str) -> None:
    """Remove the link itself; a link that stays is what the rescan reports."""
    try:
        status = os.lstat(_extended(path))
        directory = (bool(status.st_file_attributes & stat.FILE_ATTRIBUTE_DIRECTORY) if os.name == "nt"
                     else False)
        (os.rmdir if directory else os.unlink)(_extended(path))
    except OSError:
        pass


def _writable_retry(function: Callable[[str], object], target: str, _error: BaseException) -> None:
    """Git writes its objects read-only."""
    os.chmod(target, stat.S_IWRITE)
    function(target)


def remove_tree(directory: str | os.PathLike[str], expected: Callable[[str], bool],
                say: Callable[[str], None] = print) -> str | None:
    """The single recursive delete of ATC's temporary tooling: steps 4 and 5 of docs/ATC_HOME.md § Deleting.

    Returns the reason the tree stays, or ``None`` once it is gone.
    """
    directory = os.fspath(directory)
    if _is_link(os.lstat(_extended(directory))):
        return "is itself a link"
    links = _links(directory)
    anomalies = [link for link in links if not expected(link)]
    if anomalies:
        for link in anomalies:
            say(f"ANOMALY {link}")
        return f"{len(anomalies)} unexpected links"
    for link in links:
        _remove_link(link)
    left = _links(directory)
    if left:
        for link in left:
            say(f"LEFT {link}")
        return f"{len(left)} links remain after link removal"
    shutil.rmtree(_extended(directory), onexc=_writable_retry)
    return None


def scratch_links(directory: str) -> Callable[[str], bool]:
    """The links a scratch may hold: a toolchain's environment directory (``.venv`` for Python) in the scratch, in
    ``tree`` or beside a ``pyproject.toml`` below ``tree``, and any inside ``bt``."""
    names = {os.path.normcase(name) for name in toolchain.environment_directories()}
    tree = os.path.normcase(os.path.join(directory, "tree"))
    tops = {os.path.normcase(os.path.join(place, name)) for place in (directory, tree) for name in names}
    bt = os.path.normcase(os.path.join(directory, "bt")) + os.sep

    def expected(link: str) -> bool:
        name = os.path.normcase(link)
        return (name in tops or name.startswith(bt)
                or (name.startswith(tree + os.sep) and os.path.basename(name) in names
                    and _project_file(os.path.join(os.path.dirname(link), "pyproject.toml"))))
    return expected


def _project_file(path: str) -> bool:
    """``path`` is a regular file, not a link."""
    try:
        return stat.S_ISREG(os.lstat(_extended(path)).st_mode)
    except OSError:
        return False


def working_directory_links(work: str | os.PathLike[str]) -> Callable[[str], bool]:
    """The links a run's working directory may hold: any inside the base directory of a stage or the test command."""
    prefix = os.path.normcase(os.fspath(work)) + os.sep

    def expected(link: str) -> bool:
        parts = os.path.normcase(link).removeprefix(prefix).split(os.sep)
        return len(parts) > 2 and parts[1] == "bt" and parts[0] in stages.BASE_DIRECTORY_STAGES
    return expected


def no_link(_link: str) -> bool:
    return False


def _expanded(path: str) -> str:
    """``path`` with 8.3 names expanded and links left as they are; a path that does not exist stays as typed."""
    if os.name != "nt":
        return path
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetLongPathNameW(path, buffer, len(buffer))
    return buffer.value if 0 < size < len(buffer) else path


def _resolve_for_clean(argument: str, root: Path) -> tuple[str | None, str]:
    """Step 1 of § Deleting: the directory, or ``None`` and the reason it is refused."""
    try:
        chosen = os.path.join(root, argument) if SCRATCH_NAME.fullmatch(argument) else argument
        lexical = os.path.abspath(chosen)
    except (OSError, ValueError) as error:
        return None, f"{argument!r}: {error}"
    parent = _expanded(os.path.dirname(lexical))
    name = os.path.basename(_expanded(lexical))
    if not locations.same_path(parent, root):
        return None, f"{lexical} (not a direct child of {root} through real directories)"
    if not SCRATCH_NAME.fullmatch(name):
        return None, f"{lexical} (not a scratch name)"
    if _is_reserved(name):
        return None, f"{lexical} (reserved name)"
    return os.path.join(root, name), ""


def _clean_one(directory: str, root: Path, say: Callable[[str], None], check: str | None = None) -> int:
    """Steps 2 to 6 of § Deleting; each directory ends on one line. ``check`` is the reason a rehearsal gives
    a directory that would be deleted, and then nothing is removed or unregistered."""
    try:
        try:
            status = os.lstat(_extended(directory))
        except FileNotFoundError:
            say(f"SKIPPED {directory} (not found)")
            if check is None:
                _unregister(root, directory)
            return 0
        if _is_link(status):
            say(f"REFUSED {directory} (is itself a link)")
            return 1
        if not stat.S_ISDIR(status.st_mode):
            say(f"REFUSED {directory} (not a directory)")
            return 1
        if check is not None:
            say(f"DELETED {directory} ({check})")
            return 0
        why = remove_tree(directory, scratch_links(directory), say)
        if why:
            say(f"REFUSED {directory} ({why})")
            return 1
        say(f"DELETED {directory}")
        _unregister(root, directory)
        return 0
    except Exception as error:  # noqa: BLE001 - an error while handling a directory is its refusal
        say(f"REFUSED {directory} (error: {error})")
        return 1


def _within(path: str, directory: str) -> bool:
    try:
        inner, outer = (os.path.normcase(os.path.realpath(value)) for value in (path, directory))
        return Path(inner).is_relative_to(outer)
    except (OSError, ValueError):
        return False


def dependent_runs(root: Path, directory: str) -> list[str]:
    """The other scratches whose registered tree lies inside ``directory``: runs made on its export."""
    return [row.scratch for row in registered(root)
            if row.tree and _within(row.tree, directory) and not locations.same_path(row.scratch, directory)]


def clean(arguments: Sequence[str], *, say: Callable[[str], None] = print, environ=None,
          with_runs: bool = False, check: bool = False) -> int:
    """``atc test ID --clean``: the highest exit code across the directories.

    A scratch's dependent runs are listed; ``with_runs`` deletes them too, before the scratch. ``check``
    rehearses: each line says what the run would do, and nothing is deleted, unregistered or locked.
    """
    root = locations.locate(environ).tmp
    chosen = list(arguments)
    directories = []
    for argument in chosen:
        directory, why = _resolve_for_clean(argument, root)
        if directory is None:
            say(f"REFUSED {why}")
        else:
            directories.append(directory)
    if len(directories) != len(chosen):
        return 1
    ordered: list[tuple[str, str]] = []
    for directory in directories:
        for run in dependent_runs(root, directory):
            if any(locations.same_path(run, seen) for seen in [*(path for path, _ in ordered), *directories]):
                continue
            if with_runs:
                ordered.append((run, f"dependent run of {directory}"))
            else:
                say(f"DEPENDENT {run} (its tree is inside {directory}; --with-runs deletes it)")
        ordered.append((directory, "registered scratch"))
    code = 0
    for directory, reason in ordered:
        code = max(code, _clean_one(directory, root, say, reason if check else None))
    return code


def _size(path: str) -> int:
    total, pending = 0, [path]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(_extended(current)) as entries:
                for entry in entries:
                    status = entry.stat(follow_symlinks=False)
                    if entry.is_dir(follow_symlinks=False) and not _is_link(status):
                        pending.append(os.path.join(current, entry.name))
                    else:
                        total += status.st_size
        except OSError:
            continue
    return total


def _age(created_utc: str, now: datetime) -> str:
    try:
        seconds = (now - datetime.strptime(created_utc, TIME_FORMAT).replace(tzinfo=UTC)).total_seconds()
    except ValueError:
        return "?"
    for unit, length in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= length:
            return f"{int(seconds // length)}{unit}"
    return "0m"


def _relative_tree(row: Row) -> str:
    """The row's tree relative to its scratch; the full path when the tree lies outside the scratch."""
    if locations.same_path(row.tree, row.scratch) or not row.tree.strip():
        return "."
    inside = os.path.relpath(row.tree, row.scratch)
    return row.tree if inside.startswith("..") or os.path.isabs(inside) else inside


def list_scratches(*, say: Callable[[str], None] = print, environ=None, as_json: bool = False,
                   now: datetime | None = None) -> int:
    """``atc test --list``: one line per scratch (name, tree, age, ``missing``, task); ``as_json`` prints every row with its size."""
    root = locations.locate(environ).tmp
    rows = registered(root)
    sizes = [_size(row.scratch) if os.path.isdir(row.scratch) else None for row in rows]
    if as_json:
        say(json.dumps([{**row._asdict(), "size": size} for row, size in zip(rows, sizes)], indent=2))
        return 0
    now = now or datetime.now(UTC)
    for row, size in zip(rows, sizes):
        fields = [os.path.basename(row.scratch), _relative_tree(row), _age(row.created_utc, now)]
        if size is None:
            fields.append("missing")
        if row.task:
            fields.append(row.task)
        say("  ".join(fields))
    missing = [os.path.basename(row.scratch) for row, size in zip(rows, sizes) if size is None]
    if missing:
        say(f"{len(missing)} missing; atc test ID --clean drops their rows")
    leftovers = leftover_candidates(root / CANDIDATES, now)
    for path, data in leftovers:
        repo = data.get("repo") if data else _owning_repository(str(path))
        try:
            created = datetime.fromtimestamp(os.lstat(path).st_mtime, UTC).strftime(TIME_FORMAT)
        except OSError:
            created = ""
        say("  ".join([f"{CANDIDATES}/{path.name}", _age(created, now), "leftover", owner.describe(data),
                       repo or "repository gone"]))
    if leftovers:
        say(f"{len(leftovers)} leftover candidates; the next atc test prunes them")
    return 0


def leftover_candidates(parent: Path, now: datetime | None = None) -> list[tuple[Path, dict | None]]:
    """The candidate directories under ``parent`` whose owner process is gone, or that have no owner marker and
    are older than an hour (docs/ATC_HOME.md § Candidates)."""
    try:
        entries = sorted(os.scandir(parent), key=lambda entry: entry.name)
    except OSError:
        return []
    found = []
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        verdict, data = owner.state(Path(entry.path), now)
        if verdict == "leftover":
            found.append((Path(entry.path), data))
    return found


def _old(status: os.stat_result, now: datetime) -> bool:
    return datetime.fromtimestamp(status.st_mtime, UTC) < now - PRUNE_AGE


def _prune_entry(path: str, status: os.stat_result, say: Callable[[str], None],
                 expected: Callable[[str], bool] = no_link) -> int:
    try:
        if _is_link(status):
            say(f"REFUSED {path} (is itself a link)")
            return 1
        if stat.S_ISDIR(status.st_mode):
            why = remove_tree(path, expected, say)
            if why:
                say(f"REFUSED {path} ({why})")
                return 1
        else:
            os.unlink(_extended(path))
        say(f"DELETED {path}")
        return 0
    except Exception as error:  # noqa: BLE001 - an error while handling an entry is its refusal
        say(f"REFUSED {path} (error: {error})")
        return 1


def _prune_system_entry(path: str, status: os.stat_result, say: Callable[[str], None]) -> int:
    """An entry of ``systmp``: a working directory holds its run's links, every other directory none."""
    working = isolation.WORK_NAME.fullmatch(os.path.basename(path))
    return _prune_entry(path, status, say, working_directory_links(path) if working else no_link)


def _owning_repository(tree: str) -> str | None:
    """The git common directory that knows ``tree`` as a worktree; ``None`` when Git no longer knows it."""
    try:
        text = Path(tree, ".git").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if not text.startswith("gitdir:"):
        return None
    git_dir = Path(tree, text.removeprefix("gitdir:").strip())
    try:
        common = (git_dir / (git_dir / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        return None
    listed = subprocess.run(["git", "--git-dir", str(common), "worktree", "list", "--porcelain"], capture_output=True,
                            text=True, env=git_env(), stdin=subprocess.DEVNULL, creationflags=WINDOW_HIDDEN)
    if listed.returncode:
        return None
    known = (line.removeprefix("worktree ") for line in listed.stdout.splitlines() if line.startswith("worktree "))
    real = locations.long_name(tree)
    return str(common) if any(locations.same_path(locations.long_name(path), real) for path in known) else None


def _prune_candidate(path: str, status: os.stat_result, say: Callable[[str], None]) -> int:
    """A candidate directory goes through ``git worktree remove`` of the repository that knows it, forced; any other
    entry is deleted, except a candidate whose owner is confirmed to run. The candidate's owner marker goes
    with it."""
    data = owner.read_marker(Path(path)) if stat.S_ISDIR(status.st_mode) else None
    if data is not None and owner.confirmed(data["pid"], data.get("created")):
        return 0
    common = None if _is_link(status) or not stat.S_ISDIR(status.st_mode) else _owning_repository(path)
    if common is None:
        code = _prune_entry(path, status, say)
    else:
        removed = subprocess.run(["git", "--git-dir", common, "worktree", "remove", "-f", "-f", path],
                                 capture_output=True, text=True, env=git_env(), stdin=subprocess.DEVNULL,
                                 creationflags=WINDOW_HIDDEN)
        if removed.returncode:
            say(f"REFUSED {path} ({' '.join(removed.stderr.split())})")
            return 1
        say(f"DELETED {path}")
        code = 0
    if not code and stat.S_ISDIR(status.st_mode):
        owner.marker(Path(path)).unlink(missing_ok=True)
    return code


def forget_worktree(common: Path, candidate: Path) -> bool:
    """Remove the one administrative directory under ``<common>/worktrees`` whose ``gitdir`` names ``candidate``,
    compared without case; whether there was one. Other worktrees' records are left as they are."""
    want = os.path.normcase(os.path.abspath(candidate))
    try:
        entries = list((common / "worktrees").iterdir())
    except OSError:
        return False
    for entry in entries:
        try:
            gitdir = (entry / "gitdir").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if os.path.normcase(os.path.abspath(os.path.dirname(gitdir))) == want:
            why = remove_tree(str(entry), no_link, lambda _line: None)
            if why:
                raise OSError(f"{entry}: {why}")
            return True
    return False


def _prune_orphan_marker(path: str, say: Callable[[str], None]) -> int:
    """A marker whose candidate directory is gone: the owner's repository forgets the candidate, then the marker
    goes, unless its owner still runs."""
    candidate = Path(path[:-len(owner.MARKER_SUFFIX)])
    data = owner.read_marker(candidate)
    if data is not None and owner.alive(data["pid"], data.get("created")):
        return 0
    try:
        if data is not None and data.get("repo"):
            listed = subprocess.run(["git", "-C", data["repo"], "rev-parse", "--path-format=absolute",
                                     "--git-common-dir"], capture_output=True, text=True, env=git_env(),
                                    stdin=subprocess.DEVNULL, creationflags=WINDOW_HIDDEN)
            if listed.returncode == 0 and forget_worktree(Path(listed.stdout.strip()), candidate):
                say(f"DELETED {candidate} (worktree record)")
        os.unlink(_extended(path))
        say(f"DELETED {path}")
        return 0
    except Exception as error:  # noqa: BLE001 - an error while handling an entry is its refusal
        say(f"REFUSED {path} (error: {error})")
        return 1


def _prune_leftover_candidates(parent: Path, say: Callable[[str], None], now: datetime,
                               handled: set[str]) -> int:
    """Candidates whose owner is gone, at any age, and markers left without their candidate; ``handled`` gets
    each candidate taken here."""
    code = 0
    for path, _data in leftover_candidates(parent, now):
        handled.add(str(path))
        try:
            code = max(code, _prune_candidate(str(path), os.lstat(path), say))
        except OSError as error:
            say(f"REFUSED {path} (error: {error})")
            code = 1
    try:
        markers = sorted(entry.path for entry in os.scandir(parent) if entry.name.endswith(owner.MARKER_SUFFIX))
    except OSError:
        markers = []
    for path in markers:
        if not os.path.lexists(path[:-len(owner.MARKER_SUFFIX)]):
            code = max(code, _prune_orphan_marker(path, say))
    return code


def prune(*, say: Callable[[str], None] = print, environ=None, now: datetime | None = None) -> int:
    """The prune every ``atc test`` run starts with: entries of ``systmp`` and ``candidates`` older than seven days, and candidates whose owner
    is gone; 1 on any refusal."""
    places = locations.locate(environ)
    now = now or datetime.now(UTC)
    code = 0
    for parent, handle, skip in ((places.systmp, _prune_system_entry, toolchain.system_temp_runs()),
                                 (places.tmp / CANDIDATES, _prune_candidate, (f"*{owner.MARKER_SUFFIX}",))):
        try:
            parent_status = os.lstat(parent)
        except FileNotFoundError:
            continue
        except OSError as error:
            say(f"REFUSED {parent} (error: {error})")
            code = 1
            continue
        if _is_link(parent_status):
            say(f"REFUSED {parent} (is itself a link)")
            code = 1
            continue
        if not stat.S_ISDIR(parent_status.st_mode):
            say(f"REFUSED {parent} (not a directory)")
            code = 1
            continue
        handled: set[str] = set()
        if handle is _prune_candidate:
            code = max(code, _prune_leftover_candidates(parent, say, now, handled))
        try:
            entries = sorted(os.scandir(parent), key=lambda entry: entry.name)
        except OSError as error:
            say(f"REFUSED {parent} (error: {error})")
            code = 1
            continue
        for entry in entries:
            if entry.path in handled or any(fnmatch.fnmatch(entry.name, pattern) for pattern in skip):
                continue
            try:
                status = entry.stat(follow_symlinks=False)
                if _old(status, now):
                    code = max(code, handle(entry.path, status, say))
            except Exception as error:  # noqa: BLE001 - an error while handling an entry is its refusal
                say(f"REFUSED {entry.path} (error: {error})")
                code = 1
    for row in registered(places.tmp):
        try:
            created = datetime.strptime(row.created_utc, TIME_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            continue
        if created < now - PRUNE_AGE:
            say(f"OLD {row.scratch} ({row.project}, created {row.created_utc})")
    return code

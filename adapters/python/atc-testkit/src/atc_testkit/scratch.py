"""The scratch registry and deletion (docs/ATC_HOME.md § Test scratch, § Scratch registry, § Deleting)."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import fnmatch
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import tomllib
from typing import NamedTuple

from . import locations
from .git import git_env
from .locations import CANDIDATES, Refusal, SYSTMP

SCRATCH_NAME = re.compile(r"[A-Za-z0-9._-]+")
RESERVED_NAMES = frozenset({SYSTMP, CANDIDATES})
SCRATCH_NAME_BOUND = 64
REGISTRY_NAME = "scratch-dirs.tsv"
LOCK_NAME = "scratch-dirs.lock"
REGISTRY_HEADER = "project\tscratch\ttree\tcreated_utc"
PRUNE_AGE = timedelta(days=7)
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LOCK_SECONDS = 60.0
WINDOW_HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class Row(NamedTuple):
    project: str
    scratch: str
    tree: str
    created_utc: str


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
    return [line for line in text.split("\n") if line and line != REGISTRY_HEADER]


def _parse(line: str) -> Row | None:
    fields = line.split("\t")
    return Row(*fields) if len(fields) == len(Row._fields) else None


def _write_registry(root: Path, lines: Iterable[str]) -> None:
    """Replace the file in one step."""
    target = root / REGISTRY_NAME
    staged = root / f"{REGISTRY_NAME}.{os.getpid()}"
    staged.write_bytes(("\n".join([REGISTRY_HEADER, *lines]) + "\n").encode("utf-8"))
    os.replace(staged, target)


def registered(root: Path) -> list[Row]:
    return [row for row in map(_parse, _registry_lines(root)) if row is not None]


def register(root: Path, scratch: Path, tree: Path, now: datetime | None = None) -> None:
    """Add the scratch's line when it has none."""
    field = lambda value: re.sub(r"[\t\r\n]", " ", str(value))  # noqa: E731 - the format has no quoting
    created = (now or datetime.now(UTC)).strftime(TIME_FORMAT)
    with _locked(root):
        lines = _registry_lines(root)
        if any(row and locations.same_path(row.scratch, scratch) for row in map(_parse, lines)):
            return
        _write_registry(root, [*lines, "\t".join(map(field, (project_name(tree), scratch, tree, created)))])


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


def _is_link(status: os.stat_result) -> bool:
    if os.name == "nt":
        return bool(status.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return stat.S_ISLNK(status.st_mode)


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
    """The links a scratch may hold: ``.venv``, ``tree/.venv`` and any inside ``bt``."""
    venv = os.path.normcase(os.path.join(directory, ".venv"))
    tree_venv = os.path.normcase(os.path.join(directory, "tree", ".venv"))
    bt = os.path.normcase(os.path.join(directory, "bt")) + os.sep

    def expected(link: str) -> bool:
        name = os.path.normcase(link)
        return name in (venv, tree_venv) or name.startswith(bt)
    return expected


def working_directory_links(work: str | os.PathLike[str]) -> Callable[[str], bool]:
    """The links a run's working directory may hold: any inside a stage's base directory."""
    prefix = os.path.normcase(os.fspath(work)) + os.sep

    def expected(link: str) -> bool:
        parts = os.path.normcase(link).removeprefix(prefix).split(os.sep)
        return len(parts) > 2 and parts[1] == "bt"
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


def _clean_one(directory: str, root: Path, say: Callable[[str], None]) -> int:
    """Steps 2 to 6 of § Deleting; each directory ends on one line."""
    try:
        try:
            status = os.lstat(_extended(directory))
        except FileNotFoundError:
            say(f"SKIPPED {directory} (not found)")
            _unregister(root, directory)
            return 0
        if _is_link(status):
            say(f"REFUSED {directory} (is itself a link)")
            return 1
        if not stat.S_ISDIR(status.st_mode):
            say(f"REFUSED {directory} (not a directory)")
            return 1
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


def clean(arguments: Sequence[str], project: str | None = None, *, say: Callable[[str], None] = print,
          environ=None) -> int:
    """``atc tmp clean``: the highest exit code across the directories."""
    root = locations.locate(environ).tmp
    chosen = [row.scratch for row in registered(root) if row.project == project] if project is not None else arguments
    directories = []
    for argument in chosen:
        directory, why = _resolve_for_clean(argument, root)
        if directory is None:
            say(f"REFUSED {why}")
        else:
            directories.append(directory)
    if len(directories) != len(chosen):
        return 2
    code = 0
    for directory in directories:
        code = max(code, _clean_one(directory, root, say))
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


def list_scratches(*, say: Callable[[str], None] = print, environ=None) -> int:
    """``atc tmp list``: the registry with each scratch's size in bytes; a missing scratch shows ``missing``."""
    root = locations.locate(environ).tmp
    say(f"{REGISTRY_HEADER}\tsize")
    for row in registered(root):
        size = str(_size(row.scratch)) if os.path.isdir(row.scratch) else "missing"
        say("\t".join((*row, size)))
    return 0


def _old(status: os.stat_result, now: datetime) -> bool:
    return datetime.fromtimestamp(status.st_mtime, UTC) < now - PRUNE_AGE


def _prune_entry(path: str, status: os.stat_result, say: Callable[[str], None]) -> int:
    try:
        if _is_link(status):
            say(f"REFUSED {path} (is itself a link)")
            return 1
        if stat.S_ISDIR(status.st_mode):
            why = remove_tree(path, no_link, say)
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
    common = None if _is_link(status) or not stat.S_ISDIR(status.st_mode) else _owning_repository(path)
    if common is None:
        return _prune_entry(path, status, say)
    removed = subprocess.run(["git", "--git-dir", common, "worktree", "remove", path], capture_output=True, text=True,
                             env=git_env(), stdin=subprocess.DEVNULL, creationflags=WINDOW_HIDDEN)
    if removed.returncode:
        say(f"REFUSED {path} ({' '.join(removed.stderr.split())})")
        return 1
    say(f"DELETED {path}")
    return 0


def prune(*, say: Callable[[str], None] = print, environ=None, now: datetime | None = None) -> int:
    """``atc tmp prune``: entries of ``systmp`` and ``candidates`` older than seven days; 1 on any refusal."""
    places = locations.locate(environ)
    now = now or datetime.now(UTC)
    code = 0
    for parent, handle, skip in ((places.systmp, _prune_entry, "pytest-of-*"),
                                 (places.tmp / CANDIDATES, _prune_candidate, None)):
        try:
            parent_status = os.lstat(parent)
        except FileNotFoundError:
            continue
        if _is_link(parent_status):
            say(f"REFUSED {parent} (is itself a link)")
            code = 1
            continue
        if not stat.S_ISDIR(parent_status.st_mode):
            say(f"REFUSED {parent} (not a directory)")
            code = 1
            continue
        for entry in sorted(os.scandir(parent), key=lambda entry: entry.name):
            if skip and fnmatch.fnmatch(entry.name, skip):
                continue
            status = entry.stat(follow_symlinks=False)
            if _old(status, now):
                code = max(code, handle(entry.path, status, say))
    for row in registered(places.tmp):
        try:
            created = datetime.strptime(row.created_utc, TIME_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            continue
        if created < now - PRUNE_AGE:
            say(f"OLD {row.scratch} ({row.project}, created {row.created_utc})")
    return code

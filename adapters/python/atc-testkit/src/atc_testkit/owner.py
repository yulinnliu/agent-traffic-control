"""The process that owns a candidate worktree under ``<tmp>/candidates`` (docs/ATC_HOME.md § Candidates).

A PID with the process's creation time names it; a reused PID has another creation time and counts as gone.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

MARKER_SUFFIX = ".owner.json"
UNMARKED_AGE = timedelta(hours=1)
DENIED = "denied"
_STILL_ACTIVE = 259
_ACCESS_DENIED = 5
_QUERY_LIMITED = 0x1000


def _windows_created(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(_QUERY_LIMITED, False, pid)
    if not handle:
        return DENIED if ctypes.get_last_error() == _ACCESS_DENIED else None
    try:
        code = wintypes.DWORD()
        if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *map(ctypes.byref, times)):
            return DENIED
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def _posix_created(pid: int) -> str | None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return DENIED
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        shown = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL)
        return " ".join(shown.stdout.split()) or None


def created(pid: int) -> str | None:
    """The creation time of a running ``pid`` as an opaque string, ``DENIED`` when it cannot be read, ``None`` when
    no such process runs."""
    if pid <= 0:
        return None
    return _windows_created(pid) if sys.platform == "win32" else _posix_created(pid)


def alive(pid: int, when: str | None) -> bool:
    """Whether the process recorded as ``pid`` created at ``when`` still runs; access denied counts as alive."""
    now = created(pid)
    return now == DENIED or (now is not None and now == when)


def confirmed(pid: int, when: str | None) -> bool:
    """Whether the process recorded as ``pid`` created at ``when`` is known to run; access denied is not known."""
    now = created(pid)
    return now not in (None, DENIED) and now == when


def marker(candidate: Path) -> Path:
    return candidate.with_name(candidate.name + MARKER_SUFFIX)


def write_marker(candidate: Path, command: str, repo: Path, task: str | None) -> Path:
    """Record this process as the owner of ``candidate`` before the candidate exists."""
    path = marker(candidate)
    path.write_text(json.dumps({
        "pid": os.getpid(), "created": created(os.getpid()), "command": command, "repo": str(repo), "task": task,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds")}, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_marker(candidate: Path) -> dict | None:
    try:
        data = json.loads(marker(candidate).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("pid"), int) else None


def state(candidate: Path, now: datetime | None = None) -> tuple[str, dict | None]:
    """``in use``, ``leftover`` or ``recent`` (no marker and younger than an hour), with the marker read."""
    data = read_marker(candidate)
    if data is not None:
        return ("in use" if alive(data["pid"], data.get("created")) else "leftover"), data
    try:
        changed = datetime.fromtimestamp(os.lstat(candidate).st_mtime, UTC)
    except OSError:
        return "leftover", None
    return ("leftover" if changed < (now or datetime.now(UTC)) - UNMARKED_AGE else "recent"), None


def describe(data: Mapping | None) -> str:
    """The marker's command and task for a listing line."""
    if data is None:
        return "no owner marker"
    task = f" {data['task']}" if data.get("task") else ""
    return f"{data.get('command', '?')}{task}, pid {data['pid']}"

"""Child processes a test starts, and the list of the ones that outlive it (docs/TESTKIT.md § Child processes)."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from typing import NamedTuple

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0
REAP_SECONDS = 10.0
ALIVE_TOKEN = "CHILD_PROCESSES_ALIVE"


class HostProcess(NamedTuple):
    pid: int
    parent: int
    command: str


if os.name == "nt":
    from ctypes import wintypes

    _MEMBER_ROOM = 64
    _SYNCHRONIZE = 0x00100000
    _WAIT_OBJECT_0 = 0

    class _Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in ("user", "kernel", "period_user", "period_kernel")] + [
            (name, wintypes.DWORD) for name in ("faults", "total", "active", "terminated")]

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    class _UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]

    def _process_id_list(room: int) -> ctypes.Structure:
        class ProcessIdList(ctypes.Structure):
            _fields_ = [("assigned", wintypes.DWORD), ("listed", wintypes.DWORD), ("ids", ctypes.c_size_t * room)]
        return ProcessIdList()

    class _Tree:
        """A non-breakaway Windows Job that holds one child's whole tree."""

        def __init__(self, process: subprocess.Popen):
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
            self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                              wintypes.DWORD, ctypes.c_void_p]
            self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.held: dict[int, int] = {}
            self.handle = self.kernel.CreateJobObjectW(None, None)
            if not self.handle:
                raise OSError("CHILD_JOB_CREATE_FAILED")
            if not self.kernel.AssignProcessToJobObject(self.handle, process._handle):
                self.close()
                raise OSError("CHILD_JOB_ASSIGN_FAILED")

        def active(self) -> int:
            value = _Accounting()
            if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(value), ctypes.sizeof(value),
                                                         None):
                raise OSError("CHILD_JOB_QUERY_FAILED")
            return int(value.active)

        def terminate(self) -> None:
            if not self.kernel.TerminateJobObject(self.handle, 1):
                raise OSError("CHILD_JOB_TERMINATE_FAILED")

        def members(self) -> list[int]:
            """The process IDs the Job lists. A process in teardown leaves this list before it leaves the assigned count,
            so a list with room for every assigned process is complete; it leaves both before it has finished."""
            room = _MEMBER_ROOM
            while True:
                value = _process_id_list(room)
                succeeded = self.kernel.QueryInformationJobObject(self.handle, 3, ctypes.byref(value),
                                                                  ctypes.sizeof(value), None)
                if succeeded and value.assigned <= room:
                    return [int(pid) for pid in value.ids[:value.listed]]
                if value.assigned <= room:
                    raise OSError("CHILD_JOB_QUERY_FAILED")
                room = value.assigned + _MEMBER_ROOM

        def hold_members(self) -> None:
            """Open a handle to every member, which is what stays waitable through its teardown."""
            for pid in self.members():
                if pid not in self.held:
                    handle = self.kernel.OpenProcess(_SYNCHRONIZE, False, pid)
                    if handle:
                        self.held[pid] = handle

        def wait_members(self, seconds: float) -> bool:
            """Whether every held process has finished within ``seconds``."""
            deadline = time.monotonic() + seconds
            for handle in self.held.values():
                remaining = max(deadline - time.monotonic(), 0.0)
                if self.kernel.WaitForSingleObject(handle, int(remaining * 1000)) != _WAIT_OBJECT_0:
                    return False
            return True

        def close(self) -> None:
            for handle in self.held.values():
                self.kernel.CloseHandle(handle)
            self.held = {}
            self.kernel.CloseHandle(self.handle)
            self.handle = None
else:
    class _Tree:
        """The process group of a child started in its own session."""

        def __init__(self, process: subprocess.Popen):
            self.group = process.pid

        def active(self) -> int:
            try:
                os.killpg(self.group, 0)
            except ProcessLookupError:
                return 0
            except PermissionError:
                return 1
            return 1

        def terminate(self) -> None:
            try:
                os.killpg(self.group, signal.SIGKILL)
            except ProcessLookupError:
                pass

        def hold_members(self) -> None:
            """The group's existence is what ``active`` reads until its last member is reaped."""

        def wait_members(self, _seconds: float) -> bool:
            return True

        def close(self) -> None:
            self.group = None


_started: list[tuple[subprocess.Popen, _Tree]] = []


def _resume(process: subprocess.Popen) -> None:
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    if ntdll.NtResumeProcess(process._handle) != 0:
        raise OSError("CHILD_RESUME_FAILED")


def _windows_options(options: dict) -> dict:
    """A windowless console unless the test passes its own console flags; a new console starts hidden."""
    flags = options.get("creationflags", 0)
    if not flags & (CREATE_NEW_CONSOLE | DETACHED_PROCESS | CREATE_NO_WINDOW):
        flags |= CREATE_NO_WINDOW
    if flags & CREATE_NEW_CONSOLE:
        startup = options.get("startupinfo") or subprocess.STARTUPINFO()
        startup.dwFlags |= STARTF_USESHOWWINDOW
        startup.wShowWindow = SW_HIDE
        options["startupinfo"] = startup
    options["creationflags"] = flags | CREATE_SUSPENDED
    return options


def start(args, **options) -> subprocess.Popen:
    """``subprocess.Popen`` whose whole tree the current test's teardown ends and waits for."""
    if os.name == "nt":
        options = _windows_options(options)
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(args, **options)
    try:
        tree = _Tree(process)
    except BaseException:
        process.kill()
        process.wait()
        raise
    _started.append((process, tree))
    if os.name == "nt":
        _resume(process)
    return process


def _end(process: subprocess.Popen, tree: _Tree, seconds: float = REAP_SECONDS) -> None:
    """End every process of the child's tree and return once the child and every member have finished.

    The tree is told to end and its handles close on every path, also when the wait raises.
    """
    try:
        try:
            tree.hold_members()
        finally:
            tree.terminate()
        tree.hold_members()
        process.wait(seconds)
        deadline = time.monotonic() + seconds
        while tree.active():
            if time.monotonic() > deadline:
                raise TimeoutError("CHILD_TREE_STILL_RUNNING")
            time.sleep(0.05)
        if not tree.wait_members(seconds):
            raise TimeoutError("CHILD_TREE_STILL_RUNNING")
    finally:
        tree.close()


def run(args, *, input=None, capture_output=False, timeout=None, check=False, **options) -> subprocess.CompletedProcess:
    """``subprocess.run`` through ``start``: the tree ends when the call returns, also on a timeout."""
    if capture_output:
        options["stdout"] = options["stderr"] = subprocess.PIPE
    if input is not None:
        options["stdin"] = subprocess.PIPE
    process = start(args, **options)
    entry = next(item for item in _started if item[0] is process)
    try:
        stdout, stderr = process.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        entry[1].terminate()
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(process.args, expired.timeout, output=stdout, stderr=stderr) from None
    finally:
        _started.remove(entry)
        _end(*entry)
    if check and process.returncode:
        raise subprocess.CalledProcessError(process.returncode, process.args, stdout, stderr)
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def reap() -> None:
    """End every tree a test started and has not ended."""
    while _started:
        _end(*_started.pop())


def _windows_command_line(ntdll, kernel, pid: int) -> str:
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.ULONG(0)
        ntdll.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(size))  # ProcessCommandLineInformation
        if not size.value:
            return ""
        buffer = ctypes.create_string_buffer(size.value)
        if ntdll.NtQueryInformationProcess(handle, 60, buffer, size, ctypes.byref(size)) != 0:
            return ""
        text = _UnicodeString.from_buffer(buffer)
        return ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Buffer else ""
    finally:
        kernel.CloseHandle(handle)


def _windows_processes() -> list[HostProcess]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
                                                ctypes.POINTER(wintypes.ULONG)]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    processes = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            pid = int(entry.th32ProcessID)
            processes.append(HostProcess(pid, int(entry.th32ParentProcessID),
                                         _windows_command_line(ntdll, kernel, pid)))
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return processes


def _posix_processes() -> list[HostProcess]:
    listed = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,args="], capture_output=True, text=True, check=True,
                            errors="replace", stdin=subprocess.DEVNULL)
    processes = []
    for line in listed.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) >= 2:
            processes.append(HostProcess(int(fields[0]), int(fields[1]), fields[2] if len(fields) == 3 else ""))
    return processes


def list_processes() -> list[HostProcess]:
    """Every live process with its parent and command line."""
    return _windows_processes() if os.name == "nt" else _posix_processes()


def _short_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    return buffer.value if 0 < size < len(buffer) else str(path)


def survivors(root: Path, processes: list[HostProcess] | None = None) -> list[HostProcess]:
    """Live processes, this process and its ancestors excepted, whose command line names ``root``."""
    processes = list_processes() if processes is None else processes
    forms = {form.replace("/", "\\").rstrip("\\").lower() for form in (str(root), _short_path(Path(root)))}
    pattern = re.compile("|".join(re.escape(form) + r'(?=[\\"\s]|$)' for form in sorted(forms)))
    parents = {process.pid: process.parent for process in processes}
    excluded, pid = set(), os.getpid()
    while pid and pid not in excluded:
        excluded.add(pid)
        pid = parents.get(pid, 0)
    return [process for process in processes
            if process.pid not in excluded and pattern.search(process.command.replace("/", "\\").lower())]


def lasting_survivors(root: Path, seconds: float = REAP_SECONDS) -> list[HostProcess]:
    """The ``survivors`` of ``root`` still listed after up to ``seconds``.

    A process ended outside the Job's hold, such as by a test's own ``terminate`` cascading through a kill-on-close
    Job, stays listed for the rest of its teardown after its Job no longer counts it.
    """
    deadline = time.monotonic() + seconds
    while True:
        found = survivors(root)
        if not found or time.monotonic() >= deadline:
            return found
        time.sleep(0.05)

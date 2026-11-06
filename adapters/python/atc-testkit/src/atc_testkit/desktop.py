"""Start a command on a private Windows desktop or on the current one (docs/TESTKIT.md § Private desktop).

Either way the child's stdout and stderr arrive merged on ``stdout``, stdin is empty and its console is windowless.
"""
from __future__ import annotations

import ctypes
from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import subprocess
import uuid

PRIVATE = "private"
INTERACTIVE = "interactive"
GENERIC_ALL = 0x10000000
GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
FILE_SHARE_READ_WRITE = 0x3
STARTF_USESTDHANDLES = 0x100
CREATE_UNICODE_ENVIRONMENT = 0x400
CREATE_NO_WINDOW = 0x08000000
HANDLE_FLAG_INHERIT = 0x1
INFINITE = 0xFFFFFFFF

if os.name == "nt":
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    class StartupInfo(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def _bind():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE),
                                    ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                        ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInformation)]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return user32, kernel32


def _environment_block(env: Mapping[str, str]):
    items = sorted(env.items(), key=lambda item: item[0].upper())
    return ctypes.create_unicode_buffer("".join(f"{key}={value}\0" for key, value in items) + "\0")


class PrivateProcess:
    """A running child on its own desktop."""

    def __init__(self, user32, kernel32, desktop, process, stdout):
        self._user32, self._kernel32 = user32, kernel32
        self._desktop, self._process = desktop, process
        self.pid = process.dwProcessId
        self.stdout = stdout
        self.returncode: int | None = None

    def wait(self) -> int:
        if self.returncode is None:
            self._kernel32.WaitForSingleObject(self._process.hProcess, INFINITE)
            code = wintypes.DWORD()
            self._kernel32.GetExitCodeProcess(self._process.hProcess, ctypes.byref(code))
            self.returncode = code.value
            self._kernel32.CloseHandle(self._process.hProcess)
            self._kernel32.CloseHandle(self._process.hThread)
            self._user32.CloseDesktop(self._desktop)
            self.stdout.close()
        return self.returncode


def popen(command: Sequence[str], *, cwd: Path | str, env: Mapping[str, str]) -> PrivateProcess:
    """Start ``command`` on a new desktop; raise OSError when the desktop or process cannot be created."""
    if os.name != "nt":
        raise OSError("a private desktop exists on Windows only")
    import msvcrt

    user32, kernel32 = _bind()
    desktop_name = "atc_" + uuid.uuid4().hex[:12]
    desktop = user32.CreateDesktopW(desktop_name, None, None, 0, GENERIC_ALL, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    inheritable = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, True)
    read_end, write_end = wintypes.HANDLE(), wintypes.HANDLE()
    stdin = None
    try:
        if not kernel32.CreatePipe(ctypes.byref(read_end), ctypes.byref(write_end), ctypes.byref(inheritable), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.SetHandleInformation(read_end, HANDLE_FLAG_INHERIT, 0)
        stdin = kernel32.CreateFileW("NUL", GENERIC_READ, FILE_SHARE_READ_WRITE, ctypes.byref(inheritable),
                                     OPEN_EXISTING, 0, None)
        startup = StartupInfo()
        startup.cb = ctypes.sizeof(StartupInfo)
        startup.lpDesktop = "WinSta0\\" + desktop_name
        startup.dwFlags = STARTF_USESTDHANDLES
        startup.hStdInput, startup.hStdOutput, startup.hStdError = stdin, write_end, write_end
        process = ProcessInformation()
        line = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(command)))
        if not kernel32.CreateProcessW(None, line, None, None, True, CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
                                       _environment_block(env), str(cwd), ctypes.byref(startup),
                                       ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        descriptor = msvcrt.open_osfhandle(read_end.value, os.O_RDONLY)
        read_end = None
        return PrivateProcess(user32, kernel32, desktop, process, os.fdopen(descriptor, "rb"))
    except BaseException:
        user32.CloseDesktop(desktop)
        if read_end:
            kernel32.CloseHandle(read_end)
        raise
    finally:
        kernel32.CloseHandle(write_end)
        if stdin:
            kernel32.CloseHandle(stdin)


def _interactive(command: Sequence[str], cwd: Path | str, env: Mapping[str, str], new_session: bool):
    options = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": new_session}
    return subprocess.Popen(list(command), cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, **options)


def start(command: Sequence[str], *, cwd: Path | str, env: Mapping[str, str], private: bool,
          new_session: bool = False):
    """``(process, desktop)``, on a private desktop when asked and available; one stdout line says when it is not."""
    if private and os.name == "nt":
        try:
            return popen(command, cwd=cwd, env=env), PRIVATE
        except OSError as error:
            print(f"private desktop unavailable ({error}); windows show on the interactive desktop", flush=True)
    return _interactive(command, cwd, env, new_session), INTERACTIVE


def kill_tree(pid: int) -> None:
    """End ``pid`` and what it started, with the tool the operating system ships."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdin=subprocess.DEVNULL, capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    else:
        import signal
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass

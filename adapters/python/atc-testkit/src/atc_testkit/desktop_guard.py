"""The plugin's guard against Tk windows on the interactive desktop (docs/TESTKIT.md § Private desktop).

On a session whose own desktop is ``WinSta0\\Default`` a Tk root created for an item not marked
``interactive_desktop``, or before any item, is refused before Tcl opens the window.
"""
from __future__ import annotations

from collections.abc import Mapping
import ctypes
import os

import pytest

GUARD_VARIABLE = "ATC_DESKTOP_GUARD"
GUARD_INVALID = "DESKTOP_GUARD_INVALID"
WINDOW_TOKEN = "INTERACTIVE_DESKTOP_WINDOW"
INTERACTIVE_MARKER = "interactive_desktop"
INTERACTIVE_DESKTOP = "WinSta0\\Default"
UOI_NAME = 2


class InteractiveDesktopWindow(RuntimeError):
    """A Tk root the guard refused."""


def _object_name(user32, handle) -> str:
    from ctypes import wintypes
    needed = wintypes.DWORD()
    buffer = ctypes.create_unicode_buffer(256)
    if not user32.GetUserObjectInformationW(handle, UOI_NAME, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


def desktop_name() -> str | None:
    """``<window station>\\<desktop>`` of the calling thread; ``None`` outside Windows."""
    if os.name != "nt":
        return None
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD)]
    desktop = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    return f"{_object_name(user32, user32.GetProcessWindowStation())}\\{_object_name(user32, desktop)}"


def enabled(environ: Mapping[str, str]) -> bool:
    """``ATC_DESKTOP_GUARD``: ``0`` off, ``1`` on, unset automatic; any other value is a usage error (exit 4)."""
    value = environ.get(GUARD_VARIABLE, "")
    if value == "0":
        return False
    if value == "1":
        return True
    if value:
        raise pytest.UsageError(f"{GUARD_INVALID} {GUARD_VARIABLE}={value!r}: use 0, 1 or leave it unset")
    try:
        return desktop_name() == INTERACTIVE_DESKTOP
    except OSError:
        return False


class Guard:
    """Patches ``tkinter.Tk`` while registered; tracks the item being set up, run or torn down."""

    def __init__(self) -> None:
        self.item = None
        self.refused: dict[str, str] = {}
        self.failed: set[str] = set()
        self._restore = None

    def install(self) -> Guard:
        try:
            import tkinter
        except ImportError:
            return self
        original_init, original_loadtk = tkinter.Tk.__init__, tkinter.Tk.loadtk
        guard = self

        def init(tk, screenName=None, baseName=None, className="Tk", useTk=True, sync=False, use=None):
            if useTk:
                guard.check()
            return original_init(tk, screenName, baseName, className, useTk, sync, use)

        def loadtk(tk):
            guard.check()
            return original_loadtk(tk)

        tkinter.Tk.__init__, tkinter.Tk.loadtk = init, loadtk

        def restore() -> None:
            tkinter.Tk.__init__, tkinter.Tk.loadtk = original_init, original_loadtk
        self._restore = restore
        return self

    def check(self) -> None:
        item = self.item
        if item is not None and item.get_closest_marker(INTERACTIVE_MARKER) is not None:
            return
        where = item.nodeid if item is not None else "collection or session start, before any test"
        message = (f"{WINDOW_TOKEN} {where} opened a Tk window on the interactive desktop; run it through "
                   f"`atc test ID` (a private desktop) or mark the test {INTERACTIVE_MARKER}")
        if item is not None:
            self.refused[item.nodeid] = message
        raise InteractiveDesktopWindow(message)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        self.item = item
        try:
            yield
        finally:
            self.item = None

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        if report.failed:
            self.failed.add(item.nodeid)
        message = self.refused.get(item.nodeid)
        if report.when == "teardown" and message is not None:
            del self.refused[item.nodeid]
            if item.nodeid not in self.failed:
                report.outcome = "failed"
                report.longrepr = message

    def pytest_unconfigure(self, config):
        if self._restore is not None:
            self._restore()
            self._restore = None

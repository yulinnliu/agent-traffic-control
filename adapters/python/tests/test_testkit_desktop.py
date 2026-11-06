"""docs/TESTKIT.md § Private desktop."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from atc_testkit import desktop

windows = pytest.mark.skipif(os.name != "nt", reason="a private desktop exists on Windows only")
DESKTOP_NAME = """
import ctypes
from ctypes import wintypes
user32 = ctypes.WinDLL("user32")
user32.GetThreadDesktop.restype = wintypes.HANDLE
user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
kernel = ctypes.WinDLL("kernel32")
buffer = ctypes.create_unicode_buffer(256)
user32.GetUserObjectInformationW(user32.GetThreadDesktop(kernel.GetCurrentThreadId()), 2, buffer, 512, None)
kernel.GetConsoleWindow.restype = wintypes.HWND
print(buffer.value, bool(user32.IsWindowVisible(kernel.GetConsoleWindow())))
"""


@windows
def test_a_command_runs_on_a_new_desktop_with_no_visible_console(tmp_path: Path) -> None:
    process, kind = desktop.start([sys.executable, "-c", DESKTOP_NAME], cwd=tmp_path, env=os.environ, private=True)
    output = process.stdout.read().decode()
    assert kind == desktop.PRIVATE and process.wait() == 0
    name, visible = output.split()
    assert name.startswith("atc_") and visible == "False"


@windows
def test_its_children_inherit_the_desktop(tmp_path: Path) -> None:
    (tmp_path / "name.py").write_text(DESKTOP_NAME, encoding="utf-8")
    parent = f"import subprocess,sys; print(subprocess.run([sys.executable, {str(tmp_path / 'name.py')!r}], capture_output=True, text=True).stdout)"
    process, _ = desktop.start([sys.executable, "-c", parent], cwd=tmp_path, env=os.environ, private=True)
    output = process.stdout.read().decode()
    assert process.wait() == 0 and output.split()[0].startswith("atc_") and output.split()[1] == "False"


@windows
def test_stdout_and_stderr_merge_stdin_is_empty_and_wait_returns_the_code(tmp_path: Path) -> None:
    code = "import sys; print('o'); print('e', file=sys.stderr); print(repr(sys.stdin.read())); sys.exit(7)"
    process, _ = desktop.start([sys.executable, "-c", code], cwd=tmp_path, env=os.environ, private=True)
    output = process.stdout.read().decode()
    assert "o" in output and "e" in output and "''" in output
    assert process.wait() == 7 and process.wait() == 7


def test_an_unavailable_desktop_falls_back_to_the_interactive_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    def refuse(*_args, **_kwargs):
        raise OSError("no desktop")
    monkeypatch.setattr(desktop, "popen", refuse)
    process, kind = desktop.start([sys.executable, "-c", "print('ran')"], cwd=tmp_path, env=os.environ, private=True)
    assert kind == desktop.INTERACTIVE and b"ran" in process.stdout.read()
    process.stdout.close()
    assert process.wait() == 0
    out = capsys.readouterr().out
    assert os.name != "nt" or out.count("\n") == 1 and "private desktop unavailable" in out


def test_a_stage_on_the_current_desktop_shows_no_console(tmp_path: Path) -> None:
    process, kind = desktop.start([sys.executable, "-c", "print('ran')"], cwd=tmp_path, env=os.environ, private=False)
    assert kind == desktop.INTERACTIVE and b"ran" in process.stdout.read()
    process.stdout.close()
    assert process.wait() == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX path")
def test_popen_is_refused_outside_windows(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        desktop.popen(["true"], cwd=tmp_path, env={})


@windows
def test_popen_raises_when_the_process_cannot_start(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        desktop.popen([str(tmp_path / "absent.exe")], cwd=tmp_path, env=os.environ)


def test_kill_tree_ends_a_process_and_what_it_started(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    code = ("import subprocess,sys,time; "
            "c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            f"open({str(pid_file)!r},'w').write(str(c.pid)); time.sleep(60)")
    process, _ = desktop.start([sys.executable, "-c", code], cwd=tmp_path, env=os.environ, private=False, new_session=True)
    import time
    deadline = time.monotonic() + 30
    while not (pid_file.exists() and pid_file.read_text()) and time.monotonic() < deadline:
        time.sleep(0.05)
    grandchild = int(pid_file.read_text())
    desktop.kill_tree(process.pid)
    assert process.wait() != 0
    process.stdout.close()
    from atc_testkit import children
    deadline = time.monotonic() + 15
    while any(p.pid == grandchild for p in children.list_processes()) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not any(p.pid == grandchild for p in children.list_processes())

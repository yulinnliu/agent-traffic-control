"""docs/TESTKIT.md § Child processes."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from atc_testkit import children

windows = pytest.mark.skipif(os.name != "nt", reason="Windows creation flags")
SLEEP = "import time; time.sleep(120)"
GRANDCHILD = ("import subprocess,sys,time; "
              "c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
              "open(sys.argv[1],'w').write(str(c.pid)); time.sleep(120)")
CONSOLE_VISIBLE = ("import ctypes,sys; from ctypes import wintypes; k=ctypes.WinDLL('kernel32'); "
                   "k.GetConsoleWindow.restype=wintypes.HWND; "
                   "print(bool(ctypes.WinDLL('user32').IsWindowVisible(k.GetConsoleWindow())))")


def alive(pid: int) -> bool:
    return any(process.pid == pid for process in children.list_processes())


def gone(pid: int) -> bool:
    """The system's process list drops an ended process shortly after its Job counts it out."""
    deadline = time.monotonic() + 15
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not alive(pid)


def wait_for_text(path: Path) -> str:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{path} stayed empty")


def test_reap_ends_the_whole_tree_and_waits(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    process = children.start([sys.executable, "-c", GRANDCHILD, str(pid_file)])
    grandchild = int(wait_for_text(pid_file))
    children.reap()
    assert process.poll() is not None
    assert gone(process.pid) and gone(grandchild)


def test_a_test_teardown_reaps_what_it_started(tmp_path: Path, pytester_unused=None) -> None:
    process = children.start([sys.executable, "-c", SLEEP])
    assert process.poll() is None
    children.reap()
    assert process.poll() is not None


def test_run_returns_the_result_and_ends_the_tree(tmp_path: Path) -> None:
    result = children.run([sys.executable, "-c", "print('out')"], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "out"
    assert children._started == []


def test_run_ends_what_the_child_left_running(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    code = ("import subprocess,sys; "
            "c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'], stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL); "
            "open(sys.argv[1],'w').write(str(c.pid))")
    assert children.run([sys.executable, "-c", code, str(pid_file)]).returncode == 0
    assert gone(int(pid_file.read_text()))


class FakeProcess:
    def poll(self) -> int:
        return 0

    def wait(self, _seconds: float) -> int:
        return 0


class FakeTree:
    def __init__(self, active: int):
        self.remaining, self.closed, self.terminated = active, False, False

    def active(self) -> int:
        self.remaining = max(self.remaining - 1, 0) if self.terminated else self.remaining
        return self.remaining

    def terminate(self) -> None:
        self.terminated = True

    def close(self) -> None:
        self.closed = True


def test_ending_waits_until_the_tree_counts_no_process() -> None:
    tree = FakeTree(3)
    children._end(FakeProcess(), tree)
    assert tree.remaining == 0 and tree.closed


def test_a_tree_that_keeps_running_is_reported_with_its_token() -> None:
    class Stuck(FakeTree):
        def active(self) -> int:
            return 1
    tree = Stuck(1)
    with pytest.raises(TimeoutError, match="CHILD_TREE_STILL_RUNNING"):
        children._end(FakeProcess(), tree, seconds=0.1)
    assert not tree.closed


def test_run_passes_input_and_check(tmp_path: Path) -> None:
    code = "import sys; sys.stdout.write(sys.stdin.read()); sys.exit(4)"
    with pytest.raises(subprocess.CalledProcessError) as failed:
        children.run([sys.executable, "-c", code], input="in", capture_output=True, text=True, check=True)
    assert failed.value.returncode == 4 and failed.value.stdout == "in"


def test_run_ends_the_tree_on_a_timeout(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    with pytest.raises(subprocess.TimeoutExpired):
        children.run([sys.executable, "-c", GRANDCHILD, str(pid_file)], timeout=3, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    assert children._started == []
    if pid_file.exists() and pid_file.read_text():
        assert not alive(int(pid_file.read_text()))


@windows
def test_a_child_gets_a_windowless_console_its_children_inherit(tmp_path: Path) -> None:
    (tmp_path / "visible.py").write_text(CONSOLE_VISIBLE, encoding="utf-8")
    parent = f"import subprocess,sys; print(subprocess.run([sys.executable, {str(tmp_path / 'visible.py')!r}], capture_output=True, text=True).stdout)"
    result = children.run([sys.executable, "-c", parent], capture_output=True, text=True)
    assert result.stdout.strip() == "False", result.stderr


@windows
def test_own_creation_flags_pass_through_and_a_new_console_starts_hidden() -> None:
    options = children._windows_options({"creationflags": children.CREATE_NEW_CONSOLE})
    assert options["creationflags"] == children.CREATE_NEW_CONSOLE | children.CREATE_SUSPENDED
    assert options["startupinfo"].dwFlags & children.STARTF_USESHOWWINDOW and options["startupinfo"].wShowWindow == children.SW_HIDE
    assert children._windows_options({"creationflags": children.DETACHED_PROCESS})["creationflags"] == (
        children.DETACHED_PROCESS | children.CREATE_SUSPENDED)
    assert children._windows_options({})["creationflags"] == children.CREATE_NO_WINDOW | children.CREATE_SUSPENDED
    assert children._windows_options({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP})["creationflags"] == (
        subprocess.CREATE_NEW_PROCESS_GROUP | children.CREATE_NO_WINDOW | children.CREATE_SUSPENDED)


@windows
def test_a_new_console_launch_shows_no_window() -> None:
    result = children.run([sys.executable, "-c", CONSOLE_VISIBLE + "; open(sys.argv[1],'w').write('x')", os.devnull],
                          creationflags=children.CREATE_NEW_CONSOLE)
    assert result.returncode == 0


def test_the_process_list_reads_this_process_and_a_child_command_line(tmp_path: Path) -> None:
    marker = str(tmp_path / "marker")
    process = children.start([sys.executable, "-c", SLEEP, marker])
    try:
        listed = {entry.pid: entry for entry in children.list_processes()}
        assert os.getpid() in listed and marker in listed[process.pid].command
        assert listed[process.pid].parent == os.getpid()
    finally:
        children.reap()


def host(pid: int, parent: int, command: str) -> children.HostProcess:
    return children.HostProcess(pid, parent, command)


def test_survivors_name_the_base_directory_excluding_self_and_ancestors(tmp_path: Path) -> None:
    base = tmp_path / "base"
    me, parent = os.getpid(), 999001
    processes = [host(me, parent, f"python {base}"), host(parent, 0, f"atc {base}\\x"),
                 host(999002, me, f"python {base}/w"), host(999003, me, f'python "{base}"'),
                 host(999004, me, f"python {base}-other"), host(999005, me, "python elsewhere")]
    assert [process.pid for process in children.survivors(base, processes)] == [999002, 999003]


def test_survivors_compare_case_insensitively_and_across_slashes(tmp_path: Path) -> None:
    base = tmp_path / "Base"
    command = f"python {str(base).upper().replace(os.sep, '/')}/x"
    assert children.survivors(base, [host(999002, 1, command)])


@windows
def test_survivors_recognise_the_short_path_form(atc_tmp: Path) -> None:
    long_name = atc_tmp / "a long directory name"
    long_name.mkdir()
    short = children._short_path(long_name)
    assert children.survivors(long_name, [host(999002, 1, f"python {short}\\x")]) or short == str(long_name)

from __future__ import annotations

import multiprocessing as mp
import os
import subprocess
import time
from pathlib import Path

import pytest

from integration_slot import IntegrationSlot, common_dir


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(
        ["git", "-C", str(repo), *args], env=env, text=True,
        capture_output=True, encoding="utf-8",
    )
    if check and result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "ATC test")
    git(root, "config", "user.email", "atc@example.invalid")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    return root


def _child_hold(repo: str, ready) -> None:
    slot = IntegrationSlot(Path(repo))
    slot.acquire()
    ready.set()
    # Keep the OS lock held without waiting on a multiprocessing Event.
    # Terminating this child must not leave a synchronization primitive locked.
    time.sleep(15)
    slot.release()


def _child_contend(repo: str, start, active, guard, overlap, results, index: int) -> None:
    start.wait(10)
    with IntegrationSlot(Path(repo)):
        with guard:
            active.value += 1
            if active.value > 1:
                overlap.set()
        results.put(index)
        time.sleep(0.05)
        with guard:
            active.value -= 1


def test_same_process_lock_is_exclusive(repo: Path) -> None:
    first, second = IntegrationSlot(repo), IntegrationSlot(repo)
    assert first.acquire()
    try:
        assert not second.acquire(blocking=False)
    finally:
        first.release()
    assert second.acquire(blocking=False)
    second.release()


def test_linked_worktrees_share_lock_identity(repo: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "-b", "side", str(worktree))
    first, second = IntegrationSlot(repo), IntegrationSlot(worktree)
    assert common_dir(repo) == common_dir(worktree)
    assert first.path == second.path
    assert first.acquire()
    try:
        assert not second.acquire(blocking=False)
    finally:
        first.release()


def test_multiprocess_exclusion_and_process_exit_releases_lock(repo: Path) -> None:
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    process = ctx.Process(target=_child_hold, args=(str(repo), ready))
    process.start()
    try:
        assert ready.wait(10)
        assert not IntegrationSlot(repo).acquire(blocking=False)
        process.terminate()
        process.join(10)
        assert process.exitcode is not None
        assert IntegrationSlot(repo).acquire(blocking=False)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_repeated_multiprocess_contention_never_overlaps(repo: Path) -> None:
    ctx = mp.get_context("spawn")
    start, overlap = ctx.Event(), ctx.Event()
    active, guard, results = ctx.Value("i", 0), ctx.Lock(), ctx.Queue()
    workers = [
        ctx.Process(target=_child_contend, args=(str(repo), start, active, guard, overlap, results, i))
        for i in range(6)
    ]
    try:
        for worker in workers:
            worker.start()
        start.set()
        acquired = [results.get(timeout=30) for _ in workers]
        for worker in workers:
            worker.join(10)
            assert worker.exitcode == 0
        assert sorted(acquired) == list(range(len(workers)))
        assert not overlap.is_set()
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)


def test_context_manager_releases_after_exception(repo: Path) -> None:
    slot = IntegrationSlot(repo)
    with pytest.raises(ValueError):
        with slot:
            raise ValueError("test")
    assert IntegrationSlot(repo).acquire(blocking=False)

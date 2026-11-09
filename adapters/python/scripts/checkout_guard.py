"""The canonical checkout guard of the root `AGENTS.md` § Changing this repo.

The installed `atc` is an editable install, so the canonical checkout's content
is the code every session runs: it stays on `main` with no tracked
modification. A checkout is canonical when it holds `workspace.yaml` and its
git dir is the common dir; a linked worktree, a CI checkout and an
integration candidate are not. A checkout holding `workspace.yaml` and `.git`
whose Git state cannot be read is treated as canonical and unreadable, so the
guard fails closed.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

MAIN = "main"
# Variables that would point Git at another repository than the checkout named by -C.
REPOSITORY_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


@dataclass(frozen=True)
class CheckoutState:
    canonical: bool
    branch: str | None = None
    dirty: bool = False
    unreadable: str | None = None

    @property
    def off_main(self) -> bool:
        return self.canonical and self.unreadable is None and self.branch != MAIN


def _git(checkout: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in REPOSITORY_ENV}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, check=False)


def _failure(result: subprocess.CompletedProcess) -> str:
    lines = (result.stderr or "").strip().splitlines()
    return lines[0] if lines else f"git exited {result.returncode}"


def state(checkout: Path) -> CheckoutState:
    if not (checkout / "workspace.yaml").is_file() or not (checkout / ".git").exists():
        return CheckoutState(False)
    dirs = _git(checkout, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir")
    lines = dirs.stdout.splitlines()
    if dirs.returncode != 0 or len(lines) != 2:
        return CheckoutState(True, unreadable=_failure(dirs))
    if os.path.normcase(os.path.normpath(lines[0])) != os.path.normcase(os.path.normpath(lines[1])):
        return CheckoutState(False)
    status = _git(checkout, "status", "--porcelain=v2", "--branch", "--untracked-files=no")
    if status.returncode != 0:
        return CheckoutState(True, unreadable=_failure(status))
    branch = None
    dirty = False
    for line in status.stdout.splitlines():
        if line.startswith("# branch.head "):
            head = line.removeprefix("# branch.head ").strip()
            branch = None if head == "(detached)" else head
        elif not line.startswith("#"):
            dirty = True
    return CheckoutState(True, branch, dirty)


def describe(checkout: Path, current: CheckoutState) -> str:
    return f"{checkout} is on {current.branch or 'a detached HEAD'}"

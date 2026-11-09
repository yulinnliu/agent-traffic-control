"""The canonical checkout guard of the root `AGENTS.md` § Changing this repo.

The installed `atc` is an editable install, so the canonical checkout's content
is the code every session runs: it stays on `main` with no tracked
modification. A checkout is canonical when it holds `workspace.yaml` and its
git dir is the common dir; a linked worktree, a CI checkout and an
integration candidate are not.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

MAIN = "main"


@dataclass(frozen=True)
class CheckoutState:
    canonical: bool
    branch: str | None = None
    dirty: bool = False

    @property
    def off_main(self) -> bool:
        return self.canonical and self.branch != MAIN


def _git(checkout: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, check=False)


def state(checkout: Path) -> CheckoutState:
    if not (checkout / "workspace.yaml").is_file():
        return CheckoutState(False)
    dirs = _git(checkout, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir")
    lines = dirs.stdout.splitlines()
    if dirs.returncode != 0 or len(lines) != 2 or os.path.normcase(os.path.normpath(lines[0])) != os.path.normcase(os.path.normpath(lines[1])):
        return CheckoutState(False)
    status = _git(checkout, "status", "--porcelain=v2", "--branch", "--untracked-files=no")
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

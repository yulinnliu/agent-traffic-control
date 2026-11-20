"""The lint stage (docs/TESTKIT.md § Lint stage): the repository's `.pre-commit-config.yaml` over the whole tree."""
from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import shutil
import subprocess

from . import scratch
from .git import git_env

TOKEN = "LINT_FAILED"
# A repository without CONFIG has not adopted the lint stage: each entry prints this token and goes on.
NOT_CONFIGURED = "LINT_NOT_CONFIGURED"
CONFIG = ".pre-commit-config.yaml"
INDEX = "lint.git"
TREE_INDEX = "lint.index"
RUN = ("-m", "pre_commit", "run", "--show-diff-on-failure", "--color", "never")


def configured(top: Path) -> bool:
    """Whether the repository at ``top`` adopted the lint stage: it holds ``.pre-commit-config.yaml``."""
    return (top / CONFIG).is_file()


def not_configured_line(top: Path) -> str:
    return f"{NOT_CONFIGURED}: no {CONFIG} at {top}; the lint stage is not adopted and does not run"


def tree_argv(python: str) -> list[str]:
    """pre-commit over every file the index of the checkout names."""
    return [python, *RUN, "--all-files"]


def _git(top: Path, *args: str, index: Path | None = None) -> str:
    env = git_env() if index is None else {**git_env(), "GIT_INDEX_FILE": str(index)}
    return subprocess.run(["git", "-C", str(top), *args], check=True, capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, creationflags=scratch.WINDOW_HIDDEN).stdout


def _seed(top: Path, index: Path) -> None:
    """Fill ``index`` with every file of the working tree ``top``, untracked ones included.

    It starts as a copy of the tree's own index, so ``git add -A`` hashes only the files that differ from it;
    the tree's own index is never written.
    """
    own = top / _git(top, "rev-parse", "--git-path", "index").strip()
    if own.is_file():
        shutil.copyfile(own, index)
    _git(top, "add", "-A", index=index)


def run_tree(top: Path, place: Path, python: str, env: Mapping[str, str], log: Path) -> int:
    """pre-commit over every file of the repository tree ``top``, its output in ``log``; pre-commit's exit code.

    In a Git working tree, pre-commit and every hook read the scratch index ``<place>/lint.index`` through
    ``GIT_INDEX_FILE``, so untracked files are linted too. An export is no Git working tree, and pre-commit runs
    only in one: the export gets a Git directory in the scratch (``<place>/lint.git``) holding an index of every
    file it has. Either is removed when pre-commit ends, with the export's ``.git`` pointer file, so the tree is
    again what it was.
    """
    temporary = not (top / ".git").exists()
    index = None if temporary else place / TREE_INDEX
    run_env = dict(env)
    try:
        if index is None:
            _git(top, "init", "-q", f"--separate-git-dir={place / INDEX}")
            _git(top, "add", "-A")
        else:
            _seed(top, index)
            run_env["GIT_INDEX_FILE"] = str(index)
        with log.open("w", encoding="utf-8") as out:
            return subprocess.run(tree_argv(python), cwd=top, env=run_env, stdout=out,
                                  stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  creationflags=scratch.WINDOW_HIDDEN).returncode
    finally:
        if index is not None:
            index.unlink(missing_ok=True)
            index.with_name(index.name + ".lock").unlink(missing_ok=True)
        if temporary:
            (top / ".git").unlink(missing_ok=True)
            if os.path.isdir(place / INDEX):
                scratch.remove_tree(place / INDEX, lambda _link: False, say=lambda _line: None)

"""The lint stage (docs/TESTKIT.md § Lint stage): the repository's `.pre-commit-config.yaml` over a range or paths."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import subprocess

from . import scratch
from .git import git_env

TOKEN = "LINT_FAILED"
# A repository without CONFIG has not adopted the lint stage: each entry prints this token and goes on.
NOT_CONFIGURED = "LINT_NOT_CONFIGURED"
CONFIG = ".pre-commit-config.yaml"
INDEX = "lint.git"
RUN = ("-m", "pre_commit", "run", "--show-diff-on-failure", "--color", "never")


def configured(top: Path) -> bool:
    """Whether the repository at ``top`` adopted the lint stage: it holds ``.pre-commit-config.yaml``."""
    return (top / CONFIG).is_file()


def not_configured_line(top: Path) -> str:
    return f"{NOT_CONFIGURED}: no {CONFIG} at {top}; the lint stage is not adopted and does not run"


def range_argv(python: str, base: str, sha: str) -> list[str]:
    """pre-commit over the files the commits ``base..sha`` add or change, as the checkout holds them."""
    return [python, *RUN, "--from-ref", base, "--to-ref", sha]


def files_argv(python: str, paths: Sequence[str]) -> list[str]:
    return [python, *RUN, "--files", *paths]


def existing(top: Path, paths: Sequence[str]) -> list[str]:
    """The paths that name a file under ``top``: a deleted path has nothing to lint."""
    return [path for path in paths if (top / path).is_file()]


def _git(top: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(top), *args], check=True, capture_output=True, env=git_env(),
                   stdin=subprocess.DEVNULL, creationflags=scratch.WINDOW_HIDDEN)


def run_paths(top: Path, place: Path, python: str, paths: Sequence[str], env: Mapping[str, str], log: Path) -> int:
    """pre-commit over ``paths`` in the repository tree ``top``, its output in ``log``; pre-commit's exit code.

    An export is no Git working tree, and pre-commit runs only in one: the export gets a Git directory in the
    scratch (``<place>/lint.git``) holding an index of every file it has, removed with its ``.git`` pointer file
    when pre-commit ends, so the tree is again the export of its commit.
    """
    temporary = not (top / ".git").exists()
    if temporary:
        _git(top, "init", "-q", f"--separate-git-dir={place / INDEX}")
    try:
        if temporary:
            _git(top, "add", "-A")
        with log.open("w", encoding="utf-8") as out:
            return subprocess.run(files_argv(python, paths), cwd=top, env=dict(env), stdout=out,
                                  stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  creationflags=scratch.WINDOW_HIDDEN).returncode
    finally:
        if temporary:
            (top / ".git").unlink(missing_ok=True)
            if os.path.isdir(place / INDEX):
                scratch.remove_tree(place / INDEX, lambda _link: False, say=lambda _line: None)

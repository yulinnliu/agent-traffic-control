"""The files ATC manages inside a project, read from what sync writes there (docs/TESTKIT.md § ATC-managed paths).

A file under ``.atc/`` outside ``.atc/tasks/`` is sync's by path (docs/LAYOUT.md § Inside a consuming repository),
a ``.atc/tasks/<name>.yaml`` is a task record, and a file of ``<dir>/skills/<name>/`` is a skill copy sync renders when
that directory's ``SKILL.md`` frontmatter carries ``managed-by: agent-traffic-control``.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

from .git import git_env

ATC = ".atc/"
TASKS = ".atc/tasks/"
TASK_RECORD = re.compile(r"\.atc/tasks/[^/]+\.yaml")
SKILL_COPY = re.compile(r"[^/]+/skills/[^/]+/")
MARKER = re.compile(r"(?m)^managed-by:\s*agent-traffic-control\s*$")
_WINDOW_HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def by_path(path: str) -> bool:
    """A task record or a file sync owns under ``.atc/``: what the path alone settles."""
    path = path.replace("\\", "/")
    return TASK_RECORD.fullmatch(path) is not None or (path.startswith(ATC) and not path.startswith(TASKS))


def marked(text: str) -> bool:
    """Whether a ``SKILL.md`` text's frontmatter carries the marker of a copy sync renders."""
    if not text.startswith("---"):
        return False
    return MARKER.search(text[3:].split("\n---", 1)[0]) is not None


def is_managed(path: str, root: Path | None, base: str | None = None) -> bool:
    """Whether ATC manages ``path`` in the repository at ``root``.

    A skill copy's ``SKILL.md`` is read in the tree, else at ``base``, else at the parent of the commit that deleted
    it. With no ``root`` only the path rule applies.
    """
    path = path.replace("\\", "/")
    if by_path(path):
        return True
    match = SKILL_COPY.match(path)
    if root is None or match is None:
        return False
    return marked(_read(root, f"{match.group(0)}SKILL.md", base))


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=git_env(), stdin=subprocess.DEVNULL, creationflags=_WINDOW_HIDDEN)


def _read(root: Path, skill: str, base: str | None) -> str:
    try:
        return (root / skill).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        pass
    if base is None:
        deleting = _git(root, "rev-list", "-1", "HEAD", "--", skill)
        if deleting.returncode != 0 or not deleting.stdout.strip():
            return ""
        base = f"{deleting.stdout.strip()}^"
    shown = _git(root, "show", f"{base}:{skill}")
    return shown.stdout if shown.returncode == 0 else ""

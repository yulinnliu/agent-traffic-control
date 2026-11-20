"""A test repository's history in one `git fast-import`, and a session's built repositories copied instead of rebuilt.

Each git process costs about 30 ms on Windows, and building history one `add` and `commit` at a time was the largest
share of the suite's time (docs/design/ATC-0071.md § 2.3, § 4.1).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import pytest

T = TypeVar("T")
IDENTITY = ("t", "t@example.com")
# A built repository per pytest session and key, with what its builder returned.
_BUILT: dict[tuple[str, str], tuple[Path, object]] = {}


@dataclass(frozen=True)
class Commit:
    """One commit: each path written with its content, or removed when the content is ``None``."""
    files: Mapping[str, str | bytes | None]
    message: str


def blob(content: str | bytes) -> bytes:
    """The bytes `git add` stores for ``content`` written by ``Path.write_text``, in a repository with no content
    filter: text gets the platform's line ends, as ``write_text`` gives it."""
    if isinstance(content, bytes):
        return content
    return content.replace("\n", os.linesep).encode("utf-8")


def _person(kind: str, when: str, env: Mapping[str, str]) -> bytes:
    name = env.get(f"GIT_{kind}_NAME", IDENTITY[0])
    email = env.get(f"GIT_{kind}_EMAIL", IDENTITY[1])
    return f"{name} <{email}> {when}".encode("utf-8")


def _data(payload: bytes) -> bytes:
    return b"data %d\n" % len(payload) + payload + b"\n"


def _path(path: str) -> bytes:
    if not path or path.startswith('"') or "\n" in path:
        raise ValueError(f"a fast-import path must be non-empty, unquoted and on one line: {path!r}")
    return path.encode("utf-8")


def import_commits(repo: Path, branch: str, commits: Sequence[Commit], *, start: str | None = None,
                   env: Mapping[str, str] | None = None) -> list[str]:
    """Commit ``commits`` in order on ``branch`` in one `git fast-import` and return their SHAs.

    The first commit's parent is the commit ``start`` names, and with no ``start`` it has none; with no commits the
    branch is set to ``start``. The working tree, the index and ``HEAD`` stay as they were; the repository applies no
    content filter, so its files hold what ``blob`` gives. Git runs in ``env`` (default: this process's), whose
    ``GIT_AUTHOR_*`` and ``GIT_COMMITTER_*`` give the identity, else ``IDENTITY``."""
    env = os.environ if env is None else env
    ref = f"refs/heads/{branch}".encode("utf-8")
    when = f"{int(time.time())} +0000"
    stream = bytearray()
    parent = f"from {start}^0\n".encode("utf-8") if start is not None else b""
    if not commits:
        if start is None:
            raise ValueError("a branch with no commits needs a start")
        stream += b"reset " + ref + b"\n" + parent + b"\n"
    for mark, commit in enumerate(commits, start=1):
        stream += b"commit " + ref + b"\nmark :%d\n" % mark
        stream += b"author " + _person("AUTHOR", when, env) + b"\n"
        stream += b"committer " + _person("COMMITTER", when, env) + b"\n"
        stream += _data(commit.message.encode("utf-8") + b"\n")
        stream += parent if mark == 1 else b""
        for path, content in commit.files.items():
            if content is None:
                stream += b"D " + _path(path) + b"\n"
            else:
                stream += b"M 100644 inline " + _path(path) + b"\n" + _data(blob(content))
        stream += b"\n"
    for mark in range(1, len(commits) + 1):
        stream += b"get-mark :%d\n" % mark
    stream += b"done\n"
    result = subprocess.run(["git", "-C", str(repo), "fast-import", "--quiet", "--done"], input=bytes(stream),
                            capture_output=True, env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git fast-import failed in {repo}: {result.stderr.decode('utf-8', 'replace')}")
    return result.stdout.decode("ascii").split()


def new_repository(root: Path, commits: Sequence[Commit], *, branch: str = "main",
                   env: Mapping[str, str] | None = None) -> list[str]:
    """``root`` as a new repository whose ``branch`` holds ``commits``, checked out; their SHAs. Git runs in
    ``env`` as for ``import_commits``."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", branch], capture_output=True, env=env, check=True)
    shas = import_commits(root, branch, commits, env=env)
    subprocess.run(["git", "-C", str(root), "reset", "-q", "--hard"], capture_output=True, env=env, check=True)
    return shas


def copied(factory: pytest.TempPathFactory, key: str, target: Path, build: Callable[[Path], T]) -> T:
    """``target`` as a copy of the directory ``build`` filled once in this session for ``key``; what ``build``
    returned. ``target`` must not exist; nothing in the copy may name the built directory's path."""
    session = (str(factory.getbasetemp()), key)
    if session not in _BUILT:
        source = factory.mktemp("git-template") / "built"
        source.mkdir()
        _BUILT[session] = (source, build(source))
    source, value = _BUILT[session]
    shutil.copytree(source, target, symlinks=True)
    return value  # type: ignore[return-value]

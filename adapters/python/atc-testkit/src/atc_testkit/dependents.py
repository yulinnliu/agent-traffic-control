"""The test files a project declares as reading paths no import reaches, ``[tool.atc-testkit.dependents]``
(docs/TESTKIT.md § Declared dependents)."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import functools
import os
from pathlib import Path, PurePosixPath
import re

from . import config

KEY = "dependents"
WILDCARDS = frozenset("*?")
FORBIDDEN = frozenset("\\[]")


class DependentsInvalid(ValueError):
    """A declaration that breaks § Declared dependents; its message names the key, the entry and the problem."""


def pattern_problem(pattern: object) -> str | None:
    """Why ``pattern`` is no path pattern, or ``None``."""
    if not isinstance(pattern, str) or not pattern:
        return "invalid pattern"
    if pattern.startswith("/") or FORBIDDEN & set(pattern) or ":" in pattern.split("/", 1)[0]:
        return "invalid pattern"
    if any(part in ("", ".", "..") or ("**" in part and part != "**") for part in pattern.split("/")):
        return "invalid pattern"
    return None


@functools.lru_cache(maxsize=512)
def _regex(pattern: str) -> re.Pattern[str]:
    parts = pattern.split("/")
    pieces = []
    for at, part in enumerate(parts):
        last = at == len(parts) - 1
        if part == "**":
            pieces.append(".*" if last else "(?:[^/]+/)*")
            continue
        segment = "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in part)
        pieces.append(segment if last else segment + "/")
    return re.compile("".join(pieces))


def matches(pattern: str, path: str) -> bool:
    """Whether the repository-relative ``path`` is one ``pattern`` names: a pattern with ``*`` or ``?`` matches the
    whole path (``**`` is any number of segments), any other names itself and everything below it."""
    if WILDCARDS & set(pattern):
        return _regex(pattern).fullmatch(path) is not None
    return path == pattern or path.startswith(f"{pattern}/")


def _test_target(tree: Path, key: str) -> bool:
    """Whether ``key`` names a ``test_*.py`` file or a directory holding one, inside ``tree``."""
    if pattern_problem(key) or WILDCARDS & set(key):
        return False
    path = (tree / key).resolve()
    if not path.is_relative_to(tree.resolve()):
        return False
    if path.is_file():
        return path.name.startswith("test_") and path.suffix == ".py"
    return path.is_dir() and any(path.rglob("test_*.py"))


def _exists(top: Path, pattern: str) -> bool:
    """Whether ``pattern`` names at least one file or directory under ``top``."""
    if not WILDCARDS & set(pattern):
        return os.path.lexists(top / pattern)
    return next(iter(top.glob(pattern)), None) is not None


@dataclass(frozen=True)
class Dependents:
    """Each declared test path, relative to the test directory, and the patterns, relative to the repository top,
    of what it reads."""
    entries: Mapping[str, tuple[str, ...]]

    def select(self, paths: Iterable[str]) -> dict[str, list[str]]:
        """Each declared test path one of whose patterns matches one of ``paths``, with the paths it matched."""
        paths = [path.replace("\\", "/") for path in paths]
        chosen = {}
        for test, patterns in self.entries.items():
            hits = [path for path in paths if any(matches(pattern, path) for pattern in patterns)]
            if hits:
                chosen[test] = hits
        return chosen


def declared(table: Mapping[str, object]) -> Mapping[str, object]:
    """The ``dependents`` subtable of ``[tool.atc-testkit]``, empty when absent."""
    value = table.get(KEY, {})
    if not isinstance(value, dict):
        raise DependentsInvalid(f"{KEY}: not a table of test paths")
    return value


def problems(tree: Path, top: Path) -> list[str]:
    """Every problem of ``tree``'s declaration, ``top`` being the repository top the patterns are relative to."""
    try:
        table = declared(config.tool_table(tree))
    except DependentsInvalid as error:
        return [str(error)]
    found = []
    for test, patterns in table.items():
        if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) for p in patterns):
            found.append(f"{KEY}: {test}: not a list of path patterns")
            continue
        if not _test_target(tree, test):
            found.append(f"{KEY}: {test}: no such test file or directory")
        for pattern in patterns:
            if pattern_problem(pattern):
                found.append(f"{KEY}: {test}: {pattern}: invalid pattern")
            elif not _exists(top, pattern):
                found.append(f"{KEY}: {test}: {pattern}: matches no file")
    return found


def load(tree: Path, top: Path) -> Dependents:
    """``tree``'s declaration; the first problem raises ``DependentsInvalid``."""
    found = problems(tree, top)
    if found:
        raise DependentsInvalid(found[0] + (f" (+{len(found) - 1} more)" if len(found) > 1 else ""))
    table = declared(config.tool_table(tree))
    return Dependents({str(PurePosixPath(test)): tuple(patterns) for test, patterns in table.items()})


def merge(chosen: Iterable[str], extra: Iterable[str]) -> tuple[list[str], list[str]]:
    """``chosen`` then ``extra``, once each, less every path below another of them; and the kept paths of ``extra``
    that ``chosen`` lacked."""
    chosen = list(dict.fromkeys(path.rstrip("/") for path in chosen))
    every = list(dict.fromkeys([*chosen, *(path.rstrip("/") for path in extra)]))
    kept = [path for path in every if not any(path.startswith(f"{other}/") for other in every)]
    return kept, [path for path in kept if path not in chosen]

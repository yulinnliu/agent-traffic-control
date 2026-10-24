"""Glob matching shared by the policy tools.

Policy files use shell-style globs with `**` for any number of path
segments. Paths are compared as POSIX strings relative to the repository.
"""

from __future__ import annotations

import re

TOKEN_PREFIXES = ("${", "~")


def path_regex(glob: str) -> re.Pattern[str]:
    """Regex matching a whole repo-relative path against a policy glob."""
    out: list[str] = []
    i = 0
    while i < len(glob):
        ch = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(path: str, glob: str) -> bool:
    return path_regex(glob).match(path.replace("\\", "/")) is not None


def basename_regex(pattern: str) -> str:
    """Regex fragment for a single path segment glob (no slashes)."""
    out: list[str] = []
    for ch in pattern:
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def is_tokenised(glob: str) -> bool:
    """True for globs rooted at a resolver token or the home directory."""
    return glob.startswith(TOKEN_PREFIXES)

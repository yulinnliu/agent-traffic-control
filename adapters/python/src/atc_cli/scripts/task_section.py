"""The `## Task records` section of a repository's AGENTS.md.

`docs/TASKS.md` § Declaration defines its line; `task_records.py` and
`validate_workspace.py` read the prefix from it.
"""
from __future__ import annotations

import re

HEADING = "## Task records"
PREFIX_LINE = re.compile(r"^\s*(?:[-*]\s+)?Task prefix:\s*`?([^`\s]+)`?\s*$", re.MULTILINE)
PREFIX_FORM = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")


def section(text: str) -> str | None:
    """The body of the section, up to the next `## ` heading."""
    match = re.search(rf"^{re.escape(HEADING)}\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else None


def prefix(text: str) -> str | None:
    """The declared prefix of AGENTS.md ``text``; ``""`` for a section without a prefix line, ``None`` for no section."""
    body = section(text)
    if body is None:
        return None
    line = PREFIX_LINE.search(body)
    return line.group(1) if line else ""

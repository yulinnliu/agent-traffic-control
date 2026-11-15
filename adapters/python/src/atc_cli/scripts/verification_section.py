"""The `## Integration verification` section of a repository's AGENTS.md.

Governance `GIT_WORKFLOW.md` § Project verification section defines its lines;
`validate_workspace.py` checks them and `git_integrate.py` reads its verifier
argv file and timeout from them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

HEADING = "## Integration verification"
ARGV_FILE_LINE = re.compile(r"^\s*(?:[-*]\s+)?Verifier argv file:\s*`?([^`\s]+)`?\s*$", re.MULTILINE)
TIMEOUT_LINE = re.compile(r"^\s*(?:[-*]\s+)?Verifier timeout:\s*`?(\d+(?:\.\d+)?)`?(?:\s*s(?:econds)?)?\s*$", re.MULTILINE)
DEFAULT_TIMEOUT = 300.0


@dataclass(frozen=True)
class Declaration:
    """The section's verifier lines; ``None`` for a line the section lacks."""

    argv_file: str | None
    timeout: float | None


def section(text: str) -> str | None:
    """The body of the section, up to the next `## ` heading."""
    match = re.search(rf"^{re.escape(HEADING)}\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else None


def declaration(text: str) -> Declaration | None:
    """The verifier lines of AGENTS.md ``text``, or ``None`` when it has no section."""
    body = section(text)
    if body is None:
        return None
    argv_file = ARGV_FILE_LINE.search(body)
    timeout = TIMEOUT_LINE.search(body)
    return Declaration(
        argv_file.group(1) if argv_file else None,
        float(timeout.group(1)) if timeout else None,
    )

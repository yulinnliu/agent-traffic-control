"""Pure validation for the repository-wide pytest marker contract."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol


SEMANTIC_MARKERS = frozenset({"unit", "integration", "render", "statistical"})


class MarkedItem(Protocol):
    nodeid: str

    def get_closest_marker(self, name: str) -> object | None: ...


def definition_nodeid(item: MarkedItem) -> str:
    """Return a stable nodeid shared by every parameter of one test definition."""
    nodeid = item.nodeid.replace("\\", "/")
    originalname = getattr(item, "originalname", None)
    if originalname:
        marker = f"::{originalname}"
        prefix, separator, _ = nodeid.partition(marker)
        if separator:
            return prefix + marker
    return re.sub(r"\[.*\]$", "", nodeid)


def load_unmarked_baseline(path: Path) -> frozenset[str]:
    return frozenset(
        line.strip().replace("\\", "/")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def marker_contract_violations(
    items: Iterable[MarkedItem], *, allowed_unmarked: frozenset[str] = frozenset()
) -> list[str]:
    violations: list[str] = []
    for item in items:
        semantic = sorted(name for name in SEMANTIC_MARKERS if item.get_closest_marker(name))
        if len(semantic) > 1:
            violations.append(f"{item.nodeid}: multiple semantic markers: {', '.join(semantic)}")
        if item.get_closest_marker("slow") and not semantic:
            violations.append(f"{item.nodeid}: slow requires one semantic marker")
        elif not semantic:
            definition = definition_nodeid(item)
            if definition not in allowed_unmarked:
                violations.append(f"{item.nodeid}: unmarked test is not in the legacy baseline")
    return violations

"""The ``[tool.atc-testkit]`` data of a project's ``pyproject.toml`` (docs/TESTKIT.md § What a project supplies)."""
from __future__ import annotations

import os
from pathlib import Path
import tomllib
from typing import Any

TABLE = "atc-testkit"
DEFAULT_DATA_ROOT = "QUANT_DATA_ROOT"


def read_pyproject(root: Path) -> dict[str, Any]:
    """The parsed file; an absent or unreadable one is empty."""
    try:
        return tomllib.loads((Path(root) / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def tool_table(root: Path) -> dict[str, Any]:
    table = read_pyproject(root).get("tool", {}).get(TABLE, {})
    return table if isinstance(table, dict) else {}


def ignored_paths(root: Path, platform: str = os.name) -> tuple[str, ...]:
    """The ``--ignore`` paths of this platform: ``[tool.atc-testkit.ignore]`` keys ``nt`` and ``posix``."""
    return tuple(tool_table(root).get("ignore", {}).get(platform, ()))


def data_root_variables(root: Path) -> tuple[str, ...]:
    return tuple(tool_table(root).get("data_root_variables", ()))

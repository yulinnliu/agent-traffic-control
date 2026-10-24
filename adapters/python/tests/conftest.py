from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def pytest_configure(config: pytest.Config) -> None:
    # A sandboxed %TEMP% can differ between calls; keep temporary repos under
    # the gitignored tests/.tmp-pytest/ instead.
    if config.option.basetemp is None:
        config.option.basetemp = Path(__file__).resolve().parent / ".tmp-pytest"

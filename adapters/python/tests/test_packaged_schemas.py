"""The policy schemas packaged with `atc` (`src/atc_cli/schemas/`) are byte-for-byte the canonical
`shared/policies/schemas/`, so an installed wheel validates what the workspace specifies."""
from __future__ import annotations

from pathlib import Path

import pytest

import policies
import task_records

pytestmark = pytest.mark.unit

CANONICAL = Path(__file__).resolve().parents[3] / "shared" / "policies" / "schemas"


def test_the_packaged_schemas_equal_the_canonical_ones() -> None:
    packaged = {path.name: path.read_bytes() for path in policies.SCHEMAS.glob("*.json")}
    canonical = {path.name: path.read_bytes() for path in CANONICAL.glob("*.json")}
    assert canonical, CANONICAL
    assert packaged == canonical, "copy shared/policies/schemas/*.json into adapters/python/src/atc_cli/schemas/"


def test_the_readers_use_the_packaged_copy() -> None:
    assert task_records.SCHEMA.parent == policies.SCHEMAS
    assert policies.SCHEMAS.parent.name == "atc_cli"

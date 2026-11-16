"""The readers of policy and task-record shapes use the schemas packaged with `atc` (`src/atc_cli/schemas/`);
the lint stage's drift hook holds them equal to `shared/policies/schemas/`."""
from __future__ import annotations


import pytest

import policies
import task_records

pytestmark = pytest.mark.unit


def test_the_readers_use_the_packaged_copy() -> None:
    assert task_records.SCHEMA.parent == policies.SCHEMAS
    assert policies.SCHEMAS.parent.name == "atc_cli"

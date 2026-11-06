"""The suite's own ``<tmp>`` stays a root the location rule accepts under a caller's root at the bound."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

import pytest
from atc_testkit import locations

pytestmark = pytest.mark.integration


@pytest.fixture()
def root_at_the_bound(restored_process, monkeypatch: pytest.MonkeyPatch):
    base = Path(os.path.realpath(os.environ.get("ATC_TMP") or Path.home() / ".atc" / "tmp"))
    room = locations.TMP_NAME_BOUND - len(str(base)) - 1
    if room < 1:
        pytest.skip("the caller's root leaves no room for a name")
    root = base / ("p" * room)
    root.mkdir(parents=True)
    monkeypatch.setenv("ATC_TMP", str(root))
    yield root
    shutil.rmtree(root)


def test_a_caller_root_at_the_bound_still_gives_an_accepted_root(root_at_the_bound: Path, request) -> None:
    assert len(str(root_at_the_bound)) == locations.TMP_NAME_BOUND
    root = request.getfixturevalue("atc_tmp")
    assert root != root_at_the_bound and len(str(root)) <= locations.TMP_NAME_BOUND
    assert locations.locate(os.environ).tmp == root

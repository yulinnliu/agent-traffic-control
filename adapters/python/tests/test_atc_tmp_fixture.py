"""The suite's own ``<tmp>`` stays a root the location rule accepts under a caller's root at the bound."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

import pytest
from atc_testkit import locations

pytestmark = pytest.mark.integration


@pytest.fixture(params=[30, locations.TMP_NAME_BOUND])
def caller_root(request, restored_process, monkeypatch: pytest.MonkeyPatch):
    base = Path(os.path.realpath(os.environ.get("ATC_TMP") or Path.home() / ".atc" / "tmp"))
    room = request.param - len(str(base)) - 1
    if room < 1:
        pytest.skip(f"{base} leaves no room for a name in a root of {request.param} characters")
    root = base / ("p" * room)
    root.mkdir(parents=True)
    monkeypatch.setenv("ATC_TMP", str(root))
    yield root
    shutil.rmtree(root)


def test_a_caller_root_gives_an_accepted_root_with_room_for_a_test_s_own_root(caller_root: Path, request) -> None:
    root = request.getfixturevalue("atc_tmp")
    assert root != caller_root and locations.locate(os.environ).tmp == root
    assert locations.long_name(locations.tmp_root({"ATC_TMP": str(root / "root-link")})) == str(root / "root-link")

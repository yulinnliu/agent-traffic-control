"""The suite's own ``<tmp>`` lies inside the caller's root and stays a root the location rule accepts."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

import pytest
from atc_testkit import locations

pytestmark = pytest.mark.integration


def _caller_root(length: int, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = Path(os.path.realpath(os.environ.get("ATC_TMP") or Path.home() / ".atc" / "tmp"))
    room = length - len(str(base)) - 1
    if room < 1:
        pytest.skip(f"{base} leaves no room for a name in a root of {length} characters")
    root = base / ("p" * room)
    root.mkdir(parents=True)
    monkeypatch.setenv("ATC_TMP", str(root))
    return root


@pytest.fixture()
def caller_root(restored_process, monkeypatch: pytest.MonkeyPatch):
    root = _caller_root(30, monkeypatch)
    yield root
    shutil.rmtree(root)


@pytest.fixture()
def caller_root_at_the_bound(restored_process, monkeypatch: pytest.MonkeyPatch):
    root = _caller_root(locations.TMP_NAME_BOUND, monkeypatch)
    yield root
    shutil.rmtree(root)


def test_a_caller_root_gives_an_accepted_root_inside_it_with_room_for_a_test_s_own_root(caller_root: Path, request) -> None:
    root = request.getfixturevalue("atc_tmp")
    assert root.parent == caller_root and locations.locate(os.environ).tmp == root
    assert locations.long_name(locations.tmp_root({"ATC_TMP": str(root / "new")})) == str(root / "new")


def test_a_caller_root_at_the_bound_skips_and_creates_nothing_beside_it(caller_root_at_the_bound: Path) -> None:
    import conftest
    beside = sorted(caller_root_at_the_bound.parent.iterdir())
    with pytest.raises(pytest.skip.Exception, match="leaves no room"):
        conftest._new_root(conftest.ROOT_MARGIN)
    assert sorted(caller_root_at_the_bound.parent.iterdir()) == beside
    assert list(caller_root_at_the_bound.iterdir()) == []

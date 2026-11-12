"""The suite's own ``<tmp>`` lies inside the caller's root and stays a root the location rule accepts."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil

import pytest
from atc_testkit import locations

pytestmark = pytest.mark.integration


def _caller_root(length: int, monkeypatch: pytest.MonkeyPatch, *, own_parent: bool = False) -> Path:
    """A caller's root of ``length`` characters below the host's ``<tmp>``; with ``own_parent`` it lies in a
    directory of its own, so a listing beside it sees no other run's directories."""
    import conftest
    base = conftest.HOST_TMP
    if own_parent:
        base.mkdir(parents=True, exist_ok=True)
        base = _short_directory(base)
    room = length - len(str(base)) - 1
    if room < 1:
        pytest.skip(f"{base} leaves no room for a name in a root of {length} characters")
    root = base / ("p" * room)
    root.mkdir(parents=True)
    monkeypatch.setenv("ATC_TMP", str(root))
    return root


def _short_directory(base: Path) -> Path:
    while True:
        directory = base / secrets.token_hex(2)
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        return directory


@pytest.fixture()
def caller_root(restored_process, monkeypatch: pytest.MonkeyPatch):
    root = _caller_root(30, monkeypatch)
    yield root
    shutil.rmtree(root)


@pytest.fixture()
def caller_root_at_the_bound(restored_process, monkeypatch: pytest.MonkeyPatch):
    root = _caller_root(locations.TMP_NAME_BOUND, monkeypatch, own_parent=True)
    yield root
    shutil.rmtree(root.parent)


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


def test_a_test_runs_in_the_session_s_tmp_and_atc_tmp_gives_a_usable_root_inside_it(request) -> None:
    import conftest
    session = request.getfixturevalue("_session_tmp")
    if session is None:
        pytest.skip(f"{conftest.HOST_TMP} leaves no room for a session root")
    assert os.environ["ATC_TMP"] == str(session) and session.parent == conftest.HOST_TMP
    assert locations.locate(os.environ).tmp == session
    root = request.getfixturevalue("atc_tmp")
    assert root.parent == session and locations.locate(os.environ).tmp == root
    assert locations.long_name(locations.tmp_root({"ATC_TMP": str(root / "new")})) == str(root / "new")


@pytest.fixture(scope="module")
def _module_tmp() -> str | None:
    return os.environ.get("ATC_TMP")


def test_a_module_fixture_runs_in_the_session_s_tmp(_module_tmp: str | None, request) -> None:
    session = request.getfixturevalue("_session_tmp")
    if session is None:
        pytest.skip("the host's <tmp> leaves no room for a session root")
    assert _module_tmp == str(session)

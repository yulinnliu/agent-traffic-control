"""docs/ATC_HOME.md § Location and § Resolving the locations."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from atc_testkit import locations
from atc_testkit.locations import Refusal


def environment(**values: str) -> dict[str, str]:
    return dict(values)


def test_the_default_root_is_tmp_in_the_atc_home() -> None:
    home = Path.home()
    env = environment(USERPROFILE=str(home), HOME=str(home))
    assert locations.atc_home(env) == home / ".atc"
    assert locations.tmp_root(env) == home / ".atc" / "tmp"


def test_atc_tmp_replaces_the_default(atc_tmp: Path) -> None:
    assert locations.tmp_root() == atc_tmp


@pytest.mark.parametrize("kind", ["relative", "appdata", "appdata_variable", "git", "long"])
def test_an_unsuitable_root_is_refused_before_anything_is_created(atc_tmp: Path, tmp_path: Path, kind: str) -> None:
    (atc_tmp / "repo/.git").mkdir(parents=True)
    value = {"relative": "relative/root", "appdata": str(atc_tmp / "AppData/x"), "appdata_variable": str(atc_tmp / "l/x"),
             "git": str(atc_tmp / "repo/x"), "long": str(atc_tmp / ("a" * 40))}[kind]
    env = environment(ATC_TMP=value, **({"LOCALAPPDATA": str(atc_tmp / "l")} if kind == "appdata_variable" else {}))
    with pytest.raises(Refusal) as refused:
        locations.locate(env)
    assert refused.value.token == "ATC_TMP_UNSUITABLE" and refused.value.code == 9
    expected = {"relative": "not absolute", "appdata": "inside AppData", "appdata_variable": "inside AppData",
                "git": "holds .git", "long": "limit 40"}[kind]
    assert expected in refused.value.detail
    assert kind == "relative" or not Path(value).exists()


def test_the_bound_is_forty_characters(atc_tmp: Path) -> None:
    name = "a" * (40 - len(str(atc_tmp)) - 1)
    assert len(locations.long_name(locations.tmp_root(environment(ATC_TMP=str(atc_tmp / name))))) == 40
    with pytest.raises(Refusal):
        locations.tmp_root(environment(ATC_TMP=str(atc_tmp / (name + "a"))))


def test_ci_keeps_the_platform_location_and_skips_the_properties(atc_tmp: Path) -> None:
    assert locations.tmp_root(environment(CI="1")) == Path(os.path.realpath(tempfile.gettempdir()))
    before = tempfile.gettempdir()
    env = environment(CI="1", TEMP="unchanged")
    assert locations.enter(environ=env) == Path(before)
    assert env == {"CI": "1", "TEMP": "unchanged"}
    assert locations.tmp_root(environment(CI="1", ATC_TMP=str(atc_tmp / ("a" * 40)))).name == "a" * 40


def test_locate_returns_and_creates_both_paths(atc_tmp: Path) -> None:
    places = locations.locate()
    assert places.tmp == atc_tmp and places.systmp == atc_tmp / "systmp" and places.systmp.is_dir()


def test_a_root_that_cannot_be_created_is_refused(atc_tmp: Path) -> None:
    (atc_tmp / "f").write_text("x")
    with pytest.raises(Refusal) as refused:
        locations.locate(environment(ATC_TMP=str(atc_tmp / "f")))
    assert refused.value.token == "ATC_TMP_UNSUITABLE" and "cannot be created" in refused.value.detail


def test_enter_moves_this_process_and_every_child(atc_tmp: Path) -> None:
    assert locations.enter() == atc_tmp / "systmp"
    assert tempfile.gettempdir() == str(atc_tmp / "systmp")
    assert os.environ["ATC_TMP"] == str(atc_tmp)
    child = subprocess.run([sys.executable, "-c", "import tempfile;print(tempfile.gettempdir())"],
                           capture_output=True, text=True, check=True)
    assert child.stdout.strip() == str(atc_tmp / "systmp")
    for name in ("TEMP", "TMP", "TMPDIR"):
        assert os.environ[name] == str(atc_tmp / "systmp")


def test_enter_takes_a_named_directory_below_the_root_only(atc_tmp: Path, tmp_path: Path) -> None:
    assert locations.enter(atc_tmp / "s") == atc_tmp / "s" and tempfile.gettempdir() == str(atc_tmp / "s")
    with pytest.raises(ValueError):
        locations.enter(tmp_path / "s")


def test_a_redirected_home_resolves_the_same_root_through_the_export(atc_tmp: Path) -> None:
    locations.enter()
    env = {**os.environ, "USERPROFILE": str(atc_tmp / "elsewhere"), "HOME": str(atc_tmp / "elsewhere")}
    assert locations.tmp_root(env) == atc_tmp


def test_below_the_root_is_strict(atc_tmp: Path) -> None:
    locations.enter()
    assert locations.temporary_directory_below_tmp()
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(atc_tmp)
    tempfile.tempdir = None
    assert not locations.temporary_directory_below_tmp()


def test_cache_directories_are_under_the_atc_home(atc_tmp: Path) -> None:
    env = environment(USERPROFILE=str(atc_tmp), HOME=str(atc_tmp))
    assert locations.cache_directories(env) == {
        "UV_CACHE_DIR": str(atc_tmp / ".atc/cache/uv"), "UV_PYTHON_INSTALL_DIR": str(atc_tmp / ".atc/cache/python"),
        "RUFF_CACHE_DIR": str(atc_tmp / ".atc/cache/ruff")}

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


def test_a_root_that_cannot_be_created_is_refused(atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args, **_kwargs):
        raise PermissionError("no access")
    with monkeypatch.context() as patch, pytest.raises(Refusal) as refused:
        patch.setattr(Path, "mkdir", refuse)
        locations.locate(environment(ATC_TMP=str(atc_tmp / "new")))
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


PLACES = ("tmp", "systmp", "candidates")
KINDS = ("junction", "symlink") if os.name == "nt" else ("symlink",)
PROJECT = """[project]
name = "fixture-project"
version = "1"
"""
PLUGIN = 'pytest_plugins = ("atc_testkit.plugin",)\n'


def make_link(link: Path, target: Path, kind: str) -> None:
    if kind == "junction":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"this account cannot create a symbolic link: {error}")


@pytest.fixture(params=[(place, kind) for place in PLACES for kind in KINDS], ids="-".join)
def linked(request: pytest.FixtureRequest, atc_tmp: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A link at one of the three places, whose target must stay empty, with ``ATC_TMP`` naming the root in use."""
    place, kind = request.param
    victim = tmp_path / "victim"
    victim.mkdir()
    link = atc_tmp / ("root-link" if place == "tmp" else place)
    make_link(link, victim, kind)
    root = link if place == "tmp" else atc_tmp
    monkeypatch.setenv("ATC_TMP", str(root))
    yield victim, root
    os.rmdir(link) if os.name == "nt" else link.unlink()


def assert_refused(run: subprocess.CompletedProcess[str], victim: Path) -> None:
    assert run.returncode == 9, run.stdout + run.stderr
    first = run.stderr.splitlines()[0]
    assert "ATC_TMP_UNSUITABLE" in first and "is a link" in first and "Traceback" not in run.stderr
    assert list(victim.iterdir()) == []


def outside_a_pytest(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    environ = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    return subprocess.run([sys.executable, *args], cwd=cwd, env=environ, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


@pytest.mark.parametrize("command", ["list", "prune"])
def test_a_link_stops_atc_tmp(linked: tuple[Path, Path], command: str) -> None:
    assert_refused(outside_a_pytest("-m", "atc_testkit.commands", "tmp", command), linked[0])


def test_a_link_stops_atc_test_run_before_its_scratch_exists(linked: tuple[Path, Path], tmp_path: Path, atc_tmp: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    run = outside_a_pytest("-m", "atc_testkit.commands", "test", "run", str(tree), "scratch", sys.executable, "label", "tests")
    assert_refused(run, linked[0])
    assert not (atc_tmp / "scratch").exists() and not (atc_tmp / "scratch-dirs.tsv").exists()


def test_a_link_stops_the_plugin_at_configure_time(linked: tuple[Path, Path], tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(PROJECT, encoding="utf-8")
    (project / "conftest.py").write_text(PLUGIN, encoding="utf-8")
    assert_refused(outside_a_pytest("-m", "pytest", "-p", "no:cacheprovider", cwd=project), linked[0])


def test_a_link_stops_enter_and_leaves_the_temporary_variables(linked: tuple[Path, Path]) -> None:
    victim = linked[0]
    env = environment(ATC_TMP=os.environ["ATC_TMP"])
    with pytest.raises(Refusal, match="is a link"):
        locations.enter(environ=env)
    assert set(env) == {"ATC_TMP"} and list(victim.iterdir()) == []


def test_a_link_with_a_trailing_separator_is_still_a_link(atc_tmp: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    link = atc_tmp / "root-link"
    make_link(link, victim, KINDS[0])
    with pytest.raises(Refusal, match="is a link"):
        locations.tmp_root(environment(ATC_TMP=str(link) + os.sep))
    os.rmdir(link) if os.name == "nt" else link.unlink()


def test_a_link_whose_target_is_gone_is_refused(atc_tmp: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    link = atc_tmp / "systmp"
    make_link(link, victim, KINDS[0])
    victim.rmdir()
    with pytest.raises(Refusal, match="is a link"):
        locations.locate()
    os.rmdir(link) if os.name == "nt" else link.unlink()


@pytest.mark.parametrize("place", ["systmp", "candidates"])
def test_a_file_at_a_place_is_refused(atc_tmp: Path, place: str) -> None:
    (atc_tmp / place).write_text("x", encoding="utf-8")
    with pytest.raises(Refusal, match="is not a directory"):
        locations.locate()


def test_a_file_as_the_root_is_refused(atc_tmp: Path) -> None:
    (atc_tmp / "f").write_text("x", encoding="utf-8")
    with pytest.raises(Refusal, match="is not a directory"):
        locations.tmp_root(environment(ATC_TMP=str(atc_tmp / "f")))


def test_a_place_that_cannot_be_read_is_refused(atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.lstat

    def refuse(path, **kwargs):
        if str(path) == str(atc_tmp / "candidates"):
            raise PermissionError("no access")
        return real(path, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(locations.os, "lstat", refuse)
        with pytest.raises(Refusal, match="cannot be read") as refused:
            locations.locate()
    assert refused.value.token == "ATC_TMP_UNSUITABLE"


def test_real_directories_stay_and_missing_ones_are_created(atc_tmp: Path) -> None:
    (atc_tmp / "candidates").mkdir()
    assert locations.locate().systmp.is_dir()
    assert locations.locate().systmp == atc_tmp / "systmp"


def test_ci_with_no_named_directory_changes_nothing(atc_tmp: Path) -> None:
    env = environment(CI="1", ATC_TMP=str(atc_tmp / "unused"))
    before = dict(env)
    assert locations.enter(environ=env) == Path(tempfile.gettempdir())
    assert env == before and not (atc_tmp / "unused").exists()


def test_ci_with_a_named_directory_makes_it_the_temporary_directory(atc_tmp: Path) -> None:
    env = environment(CI="1", ATC_TMP=str(atc_tmp))
    target = atc_tmp / "named"
    assert locations.enter(target, env) == target and target.is_dir()
    assert env == {"CI": "1", "ATC_TMP": str(atc_tmp), "TEMP": str(target), "TMP": str(target), "TMPDIR": str(target)}


def test_ci_leaves_a_linked_root_alone_and_checks_the_places_below_it(atc_tmp: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    link = atc_tmp / "root-link"
    make_link(link, victim, KINDS[0])
    env = environment(CI="1", ATC_TMP=str(link))
    assert locations.tmp_root(env) == victim.resolve()
    assert locations.locate(env).systmp.is_dir()
    os.rmdir(link) if os.name == "nt" else link.unlink()
    make_link(atc_tmp / "candidates", victim, KINDS[0])
    with pytest.raises(Refusal, match="is a link"):
        locations.locate(environment(CI="1", ATC_TMP=str(atc_tmp)))
    assert locations.enter(environ=environment(CI="1", ATC_TMP=str(atc_tmp))) == Path(tempfile.gettempdir())
    os.rmdir(atc_tmp / "candidates") if os.name == "nt" else (atc_tmp / "candidates").unlink()

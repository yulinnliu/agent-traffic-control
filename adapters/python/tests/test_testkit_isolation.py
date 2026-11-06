"""docs/TESTKIT.md § Isolated environment."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from atc_testkit import isolation, locations


def caller(**values: str) -> dict[str, str]:
    base = {name: os.environ[name] for name in ("PATH", "SystemRoot", "SYSTEMROOT") if name in os.environ}
    return {**base, **values}


def run_of(work: Path, environ: dict[str, str], **options) -> isolation.IsolatedRun:
    return isolation.IsolatedRun(work, environ=environ, **options)


@pytest.mark.parametrize("name", ["GIT_DIR", "git_work_tree", "HAMI_X", "hami_y", "BINANCE_API_KEY", "x_api_secret",
                                  "MY_SECRET", "BOT_TOKEN", "DB_PASSWORD", "NAMED_BY_PROJECT"])
def test_removed_variables(tmp_path: Path, name: str) -> None:
    env = run_of(tmp_path, caller(**{name: "v"}), credentials=("named_by_project",)).environment("parallel")
    assert not any(key.upper() == name.upper() for key in env)


def test_other_variables_stay(tmp_path: Path) -> None:
    assert run_of(tmp_path, caller(KEEP="v")).environment("parallel")["KEEP"] == "v"


def test_home_and_userprofile_are_one_directory(tmp_path: Path) -> None:
    env = run_of(tmp_path, caller(HOME="a", USERPROFILE="b")).environment("parallel")
    assert env["HOME"] == env["USERPROFILE"] == str(tmp_path / "home")


def test_the_profile_points_into_the_working_directory(tmp_path: Path) -> None:
    env = run_of(tmp_path, caller()).environment("parallel")
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                 "PYTHONPYCACHEPREFIX"):
        assert Path(env[name]).is_relative_to(tmp_path) and Path(env[name]).is_dir(), name


def test_each_stage_has_its_own_data_root(tmp_path: Path) -> None:
    run = run_of(tmp_path, caller(), data_roots=("QUANT_DATA_ROOT", "OTHER_ROOT"))
    parallel, serial = run.environment("parallel"), run.environment("serial")
    assert parallel["QUANT_DATA_ROOT"] == parallel["OTHER_ROOT"] == str(tmp_path / "parallel/data")
    assert serial["QUANT_DATA_ROOT"] == str(tmp_path / "serial/data") and Path(serial["OTHER_ROOT"]).is_dir()


def test_caches_are_shared_under_the_callers_atc_home(tmp_path: Path) -> None:
    home = tmp_path / "real"
    env = run_of(tmp_path, caller(USERPROFILE=str(home), HOME=str(home), UV_CACHE_DIR="given")).environment("parallel")
    assert env["UV_CACHE_DIR"] == str(home / ".atc/cache/uv")
    assert env["UV_PYTHON_INSTALL_DIR"] == str(home / ".atc/cache/python")
    assert env["RUFF_CACHE_DIR"] == str(home / ".atc/cache/ruff")


def test_temporary_variables_carry_what_the_entry_point_resolved(tmp_path: Path) -> None:
    env = run_of(tmp_path, caller(TEMP="t", TMP="t", TMPDIR="t", ATC_TMP="root")).environment("parallel")
    assert (env["TEMP"], env["TMP"], env["TMPDIR"], env["ATC_TMP"]) == ("t", "t", "t", "root")


def test_pytest_options_place_the_base_and_cache_in_the_working_directory(tmp_path: Path) -> None:
    assert run_of(tmp_path, caller()).pytest_options("serial") == [
        "--basetemp", str(tmp_path / "serial/bt"), "-o", f"cache_dir={tmp_path / 'serial/cache'}"]


def test_git_reads_the_test_identity_with_every_git_variable_removed(tmp_path: Path) -> None:
    env = run_of(tmp_path, caller(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")).environment("parallel")

    def config(key: str) -> str:
        return subprocess.run(["git", "config", "--global", "--get", key], env=env, cwd=tmp_path, capture_output=True,
                              text=True, check=True).stdout.strip()
    assert config("user.name") and config("user.email") and config("core.longpaths") == "true"


def test_the_process_drops_its_own_hami_variables() -> None:
    environ = {"HAMI_A": "1", "hami_b": "2", "KEEP": "3"}
    isolation.drop_hami_variables(environ)
    assert environ == {"KEEP": "3"}


def test_the_working_directory_is_short_and_below_systmp(atc_tmp: Path) -> None:
    work = isolation.create_working_directory()
    assert work.parent == atc_tmp / "systmp" and work.is_dir() and len(work.name) <= isolation.WORK_NAME_BOUND
    assert work != isolation.create_working_directory()


def test_the_bound_of_a_working_directory_is_fifty_six_characters(atc_tmp: Path) -> None:
    name = "a" * (locations.TMP_NAME_BOUND - len(str(atc_tmp)) - 1)
    os.environ["ATC_TMP"] = str(atc_tmp / name)
    assert len(str(isolation.create_working_directory())) <= 56

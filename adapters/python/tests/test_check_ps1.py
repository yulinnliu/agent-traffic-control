"""``check.ps1``: the entry-point row of docs/ATC_HOME.md § Resolving the locations, § Cache, § pytest without a scratch."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from atc_testkit import children, locations

pytestmark = pytest.mark.integration

SCRIPT = Path(__file__).resolve().parents[1] / "check.ps1"
SHELL = shutil.which("pwsh") or shutil.which("powershell")
if SHELL is None:
    pytest.skip("no PowerShell on this machine", allow_module_level=True)
FAKE_UV = """import json, os, sys
with open(os.environ["FAKE_UV_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"args": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd()}) + "\\n")
"""
VALUES = ("TEMP", "TMP", "TMPDIR")


class Check:
    """``check.ps1`` with a ``uv`` that records its arguments and environment, and a home directory of its own."""

    def __init__(self, directory: Path):
        self.log = directory / "uv.log"
        tools = directory / "tools"
        tools.mkdir()
        home = directory / "home"
        home.mkdir()
        (tools / "fake_uv.py").write_text(FAKE_UV, encoding="utf-8")
        if os.name == "nt":
            (tools / "uv.cmd").write_text(f'@"{sys.executable}" "{tools / "fake_uv.py"}" %*\r\n', encoding="utf-8")
        else:
            (tools / "uv").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tools / "fake_uv.py"}" "$@"\n', encoding="utf-8")
            (tools / "uv").chmod(0o755)
        self.env = {name: value for name, value in os.environ.items() if name.upper() not in {"CI", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"}}
        self.env.update(FAKE_UV_LOG=str(self.log), PATH=str(tools) + os.pathsep + os.environ["PATH"],
                        HOME=str(home), USERPROFILE=str(home))

    def run(self, *arguments: str, **env: str) -> subprocess.CompletedProcess[str]:
        return children.run([SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *arguments],
                            env={**self.env, **env}, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", stdin=subprocess.DEVNULL)

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture()
def check(tmp_path: Path, atc_tmp: Path) -> Check:
    return Check(tmp_path)


def make_junction(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_the_processes_it_starts_get_the_locations_and_the_cache_values(check: Check, atc_tmp: Path) -> None:
    run = check.run("-Tests", ATC_TMP=str(atc_tmp), USERPROFILE=str(atc_tmp), HOME=str(atc_tmp))
    assert run.returncode == 0, run.stdout + run.stderr
    validate, tests = check.calls()
    systmp = atc_tmp / "systmp"
    assert systmp.is_dir()
    for call in (validate, tests):
        assert [call["env"][name] for name in VALUES] == [str(systmp)] * 3
        assert call["env"]["ATC_TMP"] == str(atc_tmp)
        assert call["env"]["UV_CACHE_DIR"] == str(atc_tmp / ".atc" / "cache" / "uv")
        assert call["env"]["UV_PYTHON_INSTALL_DIR"] == str(atc_tmp / ".atc" / "cache" / "python")
    assert validate["args"] == ["run", "--locked", "python", "scripts/validate_workspace.py"]
    assert tests["args"] == ["run", "--locked", "pytest"]


def test_the_callers_cache_values_stay(check: Check, atc_tmp: Path) -> None:
    run = check.run(ATC_TMP=str(atc_tmp), UV_CACHE_DIR="own-cache", UV_PYTHON_INSTALL_DIR="own-python")
    assert run.returncode == 0, run.stdout + run.stderr
    env = check.calls()[0]["env"]
    assert (env["UV_CACHE_DIR"], env["UV_PYTHON_INSTALL_DIR"]) == ("own-cache", "own-python")


def test_ci_keeps_the_platform_directory_and_sets_no_location(check: Check, atc_tmp: Path, tmp_path: Path) -> None:
    platform_directory = str(tmp_path)
    run = check.run(CI="1", ATC_TMP="relative", TEMP=platform_directory, TMP=platform_directory, TMPDIR=platform_directory)
    assert run.returncode == 0, run.stdout + run.stderr
    env = check.calls()[0]["env"]
    assert [env[name] for name in VALUES] == [platform_directory] * 3 and env["ATC_TMP"] == "relative"
    assert "UV_CACHE_DIR" in env and not (atc_tmp / "systmp").exists()


def test_the_default_root_lies_in_the_home_directory(fake_home: Path, check: Check) -> None:
    default = fake_home / ".atc" / "tmp"
    del check.env["ATC_TMP"]
    run = check.run(USERPROFILE=str(fake_home), HOME=str(fake_home))
    assert run.returncode == 0, run.stdout + run.stderr
    env = check.calls()[0]["env"]
    assert (env["ATC_TMP"], env["TEMP"]) == (str(default), str(default / "systmp"))
    assert (default / "systmp").is_dir()


def test_a_default_root_past_the_bound_is_refused(check: Check, atc_tmp: Path) -> None:
    del check.env["ATC_TMP"]
    home = atc_tmp / ("h" * 20)
    home.mkdir()
    run = check.run(USERPROFILE=str(home), HOME=str(home))
    assert run.returncode == 9 and run.stderr.startswith(locations.UNSUITABLE) and "limit 40" in run.stderr
    assert check.calls() == []


def unsuitable_root(kind: str, base: Path) -> str:
    """A ``<tmp>`` that breaks one property of § Location, built below ``base``."""
    if kind == "relative":
        return "relative"
    if kind == "file":
        (base / "f").write_text("x", encoding="utf-8")
        return str(base / "f")
    if kind == "link":
        make_junction(base / "j", base / "elsewhere")
        return str(base / "j")
    if kind == "app data":
        return str(base / "AppData")
    if kind == "git":
        (base / "g" / ".git").mkdir(parents=True)
        return str(base / "g" / "t")
    if kind == "too long":
        return str(base / ("n" * (locations.TMP_NAME_BOUND + 1 - len(str(base)) - 1)))
    if kind == "systmp file":
        (base / "s").mkdir()
        (base / "s" / "systmp").write_text("x", encoding="utf-8")
        return str(base / "s")
    if kind == "dangling ancestor":
        make_junction(base / "d", base / "gone")
        (base / "gone").rmdir()
        return str(base / "d" / "t")
    if kind == "candidates link":
        (base / "c").mkdir()
        make_junction(base / "c" / "candidates", base / "elsewhere")
        return str(base / "c")
    raise AssertionError(kind)


@pytest.mark.parametrize(("kind", "reason"), [
    ("relative", "is not absolute"), ("file", "is not a directory"), ("link", "is a link"),
    ("app data", "lies inside AppData"), ("git", "holds .git"), ("too long", "limit 40"),
    ("systmp file", "is not a directory"), ("candidates link", "is a link"),
    ("dangling ancestor", "is a link to a missing target")])
def test_an_unsuitable_root_exits_nine_as_the_package_refuses_it(check: Check, atc_tmp: Path, kind: str, reason: str) -> None:
    root = unsuitable_root(kind, atc_tmp)
    before = sorted(path.name for path in atc_tmp.iterdir())
    run = check.run(ATC_TMP=root)
    assert run.returncode == 9, run.stdout + run.stderr
    assert run.stderr.startswith(locations.UNSUITABLE + " ") and reason in run.stderr
    assert check.calls() == [] and sorted(path.name for path in atc_tmp.iterdir()) == before
    with pytest.raises(locations.Refusal) as refused:
        locations.locate({**os.environ, "ATC_TMP": root})
    assert reason in refused.value.detail
    assert sorted(path.name for path in atc_tmp.iterdir()) == before


@pytest.mark.skipif(os.name != "nt", reason="8.3 names")
def test_the_bound_applies_to_the_long_name_of_a_short_form(check: Check, atc_tmp: Path) -> None:
    long_form = atc_tmp / ("a long directory name" + "x" * 20)
    long_form.mkdir()
    short = children._short_path(long_form)
    if short == str(long_form):
        pytest.skip("this volume keeps no 8.3 names")
    run = check.run(ATC_TMP=short)
    assert run.returncode == 9 and "limit 40" in run.stderr


@pytest.mark.skipif(os.name != "nt", reason="8.3 names")
def test_a_short_form_root_reaches_the_children_as_its_long_name(check: Check, atc_tmp: Path) -> None:
    short = children._short_path(atc_tmp)
    if short == str(atc_tmp):
        pytest.skip("this path has no 8.3 form")
    run = check.run(ATC_TMP=short)
    assert run.returncode == 0, run.stdout + run.stderr
    env = check.calls()[0]["env"]
    assert (env["ATC_TMP"], env["TEMP"]) == (str(atc_tmp), str(atc_tmp / "systmp"))


def test_a_root_below_a_link_reaches_the_children_with_the_link_resolved(check: Check, atc_tmp: Path) -> None:
    if len(str(atc_tmp)) + len("\\j\\x") > locations.TMP_NAME_BOUND:
        pytest.skip(f"{atc_tmp} leaves no room below the bound")
    make_junction(atc_tmp / "j", atc_tmp / "e")
    (atc_tmp / "e" / "x").mkdir()
    root = atc_tmp / "j" / "x"
    run = check.run(ATC_TMP=str(root))
    assert run.returncode == 0, run.stdout + run.stderr
    assert check.calls()[0]["env"]["ATC_TMP"] == str(atc_tmp / "e" / "x") == str(locations.locate({**os.environ, "ATC_TMP": str(root)}).tmp)


def test_a_root_at_the_bound_is_accepted_and_the_values_carry_its_long_name(check: Check, atc_tmp: Path) -> None:
    room = locations.TMP_NAME_BOUND - len(str(atc_tmp)) - 1
    if room < 1:
        pytest.skip(f"{atc_tmp} leaves no room below the bound")
    root = atc_tmp / ("r" * room)
    run = check.run(ATC_TMP=str(root))
    assert run.returncode == 0, run.stdout + run.stderr
    assert check.calls()[0]["env"]["TEMP"] == str(root / "systmp")

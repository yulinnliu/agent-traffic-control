"""hami-review-relay's clean_scratch.ps1 and the LEDGER.txt line run_tests.sh appends."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PS = shutil.which("powershell")
BASH = shutil.which("bash")
REFS = Path(__file__).resolve().parents[3] / "shared/skills/hami-review-relay/references"
CLEAN = REFS / "clean_scratch.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows PowerShell only")


def env_for(temp: Path) -> dict[str, str]:
    return {**os.environ, "TEMP": str(temp), "TMP": str(temp)}


def clean(temp: Path, *dirs: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(CLEAN), *map(str, dirs)],
        capture_output=True, text=True, env=env_for(temp),
    )


def junction(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def write(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def target(tmp_path: Path, name: str) -> Path:
    write(tmp_path / "outer" / name / "keep.txt", name)
    return tmp_path / "outer" / name


def scratch(q: Path, name: str) -> Path:
    write(q / name / "tree/src/pkg/__init__.py")
    write(q / name / "qdr/data.txt")
    return q / name


def lines(run: subprocess.CompletedProcess[str]) -> list[str]:
    return [line.lower() for line in run.stdout.splitlines()]


@pytest.fixture()
def temp(tmp_path: Path) -> Path:
    (tmp_path / "temp/q").mkdir(parents=True)
    return tmp_path / "temp"


def test_expected_links_are_unlinked_and_their_targets_survive(tmp_path: Path, temp: Path) -> None:
    q = temp / "q"
    real = scratch(q, "real")
    write(real / ".venv/Lib/site.py")
    venv = scratch(q, "venv")
    junction(venv / ".venv", target(tmp_path, "venv"))
    bt = scratch(q, "bt")
    junction(bt / "bt/test_x0/link", target(tmp_path, "bt"))
    write(bt / "bt/test_x0/file.txt")
    ledger = q / "LEDGER.txt"
    ledger.write_text(
        f"{real}\t{real / 'tree'}\t2026-11-04T00:00:00Z\n{real / 'tree'}\tnested\tt\n{venv}\t{venv / 'tree'}\tt\n"
        f"{venv}\t{venv / 'tree'}\tt2\n{bt}\t{bt / 'tree'}\tt\n{q / 'other'}\t{q / 'other/tree'}\tt\n",
        encoding="utf-8",
    )
    run = clean(temp, real, venv, bt)
    assert run.returncode == 0, run.stdout + run.stderr
    assert lines(run) == [f"deleted {d}".lower() for d in (real, venv, bt)]
    assert not real.exists() and not venv.exists() and not bt.exists()
    for name in ("venv", "bt"):
        assert (tmp_path / "outer" / name / "keep.txt").read_text(encoding="utf-8") == name
    assert ledger.read_text(encoding="utf-8") == f"{q / 'other'}\t{q / 'other/tree'}\tt\n"


def test_a_link_elsewhere_refuses_that_tree_untouched_and_the_rest_continue(tmp_path: Path, temp: Path) -> None:
    q = temp / "q"
    odd = scratch(q, "odd")
    junction(odd / ".venv", target(tmp_path, "venv"))
    junction(odd / "bt/test_x0/link", target(tmp_path, "bt"))
    junction(odd / "tree/src/hidden", target(tmp_path, "hidden"))
    inner = scratch(q, "inner")
    write(inner / ".venv/Lib/site.py")
    junction(inner / ".venv/Lib/link", target(tmp_path, "inner"))
    plain = scratch(q, "plain")
    ledger = write(q / "LEDGER.txt", f"{odd}\t{odd / 'tree'}\tt\n{plain}\t{plain / 'tree'}\tt\n")
    run = clean(temp, odd, inner, plain)
    assert run.returncode == 1
    assert lines(run) == [f"anomaly {odd / 'tree/src/hidden'}".lower(),
                          f"refused {odd} (1 unexpected reparse points)".lower(),
                          f"anomaly {inner / '.venv/Lib/link'}".lower(),
                          f"refused {inner} (1 unexpected reparse points)".lower(), f"deleted {plain}".lower()]
    assert (inner / ".venv/Lib/link/keep.txt").is_file() and (inner / ".venv/Lib/site.py").is_file()
    os.rmdir(inner / ".venv/Lib/link")
    for link in (odd / ".venv", odd / "bt/test_x0/link", odd / "tree/src/hidden"):
        assert (link / "keep.txt").is_file()
    assert (odd / "qdr/data.txt").is_file() and not plain.exists()
    assert ledger.read_text(encoding="utf-8") == f"{odd}\t{odd / 'tree'}\tt\n"
    for link in (odd / ".venv", odd / "bt/test_x0/link", odd / "tree/src/hidden"):
        os.rmdir(link)


@pytest.mark.parametrize("where", ["outside", "root", "dotdot", "under_link"])
def test_a_path_not_inside_the_root_refuses_the_whole_run(tmp_path: Path, temp: Path, where: str) -> None:
    q = temp / "q"
    inside = scratch(q, "inside")
    outside = scratch(tmp_path, "outside")
    junction(q / "link", outside)
    bad = {"outside": outside, "root": q, "dotdot": f"{q}\\..\\..\\outside", "under_link": q / "link" / "tree"}[where]
    run = clean(temp, inside, bad)
    assert run.returncode == 2
    assert len(lines(run)) == 1 and lines(run)[0].startswith("refused ")
    assert (inside / "qdr/data.txt").is_file() and (outside / "tree/src/pkg/__init__.py").is_file()
    os.rmdir(q / "link")


def test_a_link_that_survives_rmdir_blocks_the_recursive_delete(tmp_path: Path, temp: Path) -> None:
    stuck = scratch(temp / "q", "stuck")
    link = stuck / "bt/test_x0/link"
    junction(link, target(tmp_path, "bt"))
    subprocess.run(["cmd", "/c", "attrib", "+r", str(link), "/L"], check=True, capture_output=True)
    try:
        run = clean(temp, stuck)
        assert run.returncode == 1
        assert lines(run)[-1] == f"refused {stuck} (1 reparse points remain after link removal)".lower()
        assert (link / "keep.txt").is_file() and (stuck / "qdr/data.txt").is_file()
    finally:
        subprocess.run(["cmd", "/c", "attrib", "-r", str(link), "/L"], capture_output=True)
        os.rmdir(link)


def test_a_dir_that_is_itself_a_link_is_refused(tmp_path: Path, temp: Path) -> None:
    link = temp / "q/link"
    junction(link, target(tmp_path, "whole"))
    run = clean(temp, link)
    assert run.returncode == 1 and lines(run) == [f"refused {link} (is itself a reparse point)".lower()]
    assert (link / "keep.txt").is_file()
    os.rmdir(link)


def test_a_missing_dir_is_skipped_and_drops_its_ledger_lines(temp: Path) -> None:
    gone = temp / "q/gone"
    ledger = write(temp / "q/LEDGER.txt", f"{gone}\t{gone / 'tree'}\tt\n")
    run = clean(temp, gone)
    assert run.returncode == 0 and lines(run) == [f"skipped {gone} (not found)".lower()]
    assert ledger.read_text(encoding="utf-8") == ""


def test_the_only_recursive_delete_rescans_for_links_first() -> None:
    text = CLEAN.read_text(encoding="utf-8")
    assert text.count("Remove-Item") == 1
    body = text[text.index("function Remove-Tree"):text.index("Remove-Item")]
    assert "$left = Get-Links $dir" in body and "if ($left.Count -gt 0)" in body and "return " in body


@pytest.mark.skipif(BASH is None, reason="no bash on PATH")
def test_run_tests_ledger_line_is_what_clean_scratch_deletes(tmp_path: Path, atc_tmp: Path) -> None:
    # The scratch must stay within run_tests.sh's 64-character limit, so its
    # %TEMP% is a root that stays within the location rule's bound.
    temp = atc_tmp
    tree = tmp_path / "tree"
    tree.mkdir()
    outside = temp / "outside"

    def run_tests(where: Path) -> None:
        subprocess.run([BASH, str(REFS / "run_tests.sh"), str(tree), str(where), sys.executable, "L", "--co"],
                       capture_output=True, text=True, env=env_for(temp), check=False)

    run_tests(outside)
    assert outside.is_dir() and not (temp / "q").exists()
    run_tests(temp / "q" / "s")
    ledger = (temp / "q/LEDGER.txt").read_text(encoding="utf-8")
    scratch_win = os.path.realpath(temp / "q" / "s")
    assert re.fullmatch(re.escape(f"{scratch_win}\t{tree}\t") + r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n", ledger), ledger
    run = clean(temp, scratch_win)
    assert run.returncode == 0 and lines(run) == [f"deleted {scratch_win}".lower()]
    assert (temp / "q/LEDGER.txt").read_text(encoding="utf-8") == ""

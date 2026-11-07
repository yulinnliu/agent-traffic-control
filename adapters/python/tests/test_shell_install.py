"""scripts/link-skills.sh and scripts/check-skills.sh run under a POSIX sh."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_tooling import run_git, write

SH = shutil.which("sh")
SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
pytestmark = pytest.mark.skipif(SH is None, reason="no POSIX sh on PATH")


def sh(script: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([SH, (SCRIPTS / script).as_posix(), *args], capture_output=True, text=True, cwd=cwd)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    return repo


def test_directory_without_skill_md_is_a_collision(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    mine = repo / ".claude/skills/hami-review-relay/notes/mine.md"
    write(mine, "mine\n")
    run = sh("link-skills.sh", repo.as_posix(), "hami-review-relay")
    assert run.returncode == 1
    assert "collision  .claude/skills/hami-review-relay (files but no SKILL.md, left alone)" in run.stdout
    assert mine.read_text(encoding="utf-8") == "mine\n" and not (mine.parents[1] / "SKILL.md").exists()
    assert sh("link-skills.sh", repo.as_posix(), "hami-review-relay").returncode == 1
    assert mine.is_file()


def test_managed_directory_is_repaired_whatever_its_file_names(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert sh("link-skills.sh", repo.as_posix(), "hami-handoff-reminder").returncode == 0
    check = sh("check-skills.sh", repo.as_posix())
    assert check.returncode == 0 and "current    .claude/skills/hami-handoff-reminder" in check.stdout
    adapter = repo / ".claude/skills/hami-handoff-reminder"
    shipped = adapter / "references/handoff-document.md"
    original = shipped.read_bytes()
    shipped.write_text("edited\n", encoding="utf-8")
    assert "drifted    .claude/skills/hami-handoff-reminder" in sh("check-skills.sh", repo.as_posix()).stdout
    shipped.write_bytes(original)
    # A space splits a name under the default IFS; with globbing on, "[ab].md" expands against
    # the caller's directory, here the adapter itself, to "a.md".
    extras = [adapter / "my notes.md", adapter / "[ab].md", adapter / "a.md", adapter / "references/keep/k"]
    for extra in extras:
        write(extra, "x\n")
    check = sh("check-skills.sh", repo.as_posix())
    assert check.returncode == 1 and "drifted    .claude/skills/hami-handoff-reminder" in check.stdout
    link = sh("link-skills.sh", repo.as_posix(), "hami-handoff-reminder", cwd=adapter)
    assert link.returncode == 0 and "updated    .claude/skills/hami-handoff-reminder" in link.stdout
    assert not [extra for extra in extras if extra.exists()]
    assert shipped.read_bytes().replace(b"\r", b"") == original.replace(b"\r", b"")
    check = sh("check-skills.sh", repo.as_posix())
    assert check.returncode == 0 and "current    .claude/skills/hami-handoff-reminder" in check.stdout


def test_dangling_skill_md_link_is_relinked_only_when_alone(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    adapter = repo / ".claude/skills/hami-review-relay"
    adapter.mkdir(parents=True)
    try:
        os.symlink(tmp_path / "moved/SKILL.md", adapter / "SKILL.md")
    except OSError:
        pytest.skip("this host cannot create symlinks")
    write(adapter / "mine.md", "mine\n")
    run = sh("link-skills.sh", repo.as_posix(), "hami-review-relay")
    assert "collision  .claude/skills/hami-review-relay (files but no SKILL.md, left alone)" in run.stdout
    assert (adapter / "mine.md").is_file() and (adapter / "SKILL.md").is_symlink()
    (adapter / "mine.md").unlink()
    assert sh("link-skills.sh", repo.as_posix(), "hami-review-relay").returncode == 0
    assert (adapter / "SKILL.md").is_file() and sh("check-skills.sh", repo.as_posix()).returncode == 0

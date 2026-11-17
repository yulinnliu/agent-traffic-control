"""The files ATC manages in a project, read from what sync writes there (docs/TESTKIT.md § ATC-managed paths)."""
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "atc-testkit/src"))

from atc_testkit import managed  # noqa: E402
from atc_testkit.git import git_env  # noqa: E402

MANAGED_SKILL = "---\nname: s\nmanaged-by: agent-traffic-control\ncanonical: s\n---\n\n# S\n"
OWN_SKILL = "---\nname: s\n---\n\n# S\n"
MARKER_IN_BODY = "---\nname: s\n---\n\nmanaged-by: agent-traffic-control\n"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false",
                           *args], cwd=root, env=git_env(), capture_output=True, text=True, check=True).stdout.strip()


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


@pytest.mark.unit
@pytest.mark.parametrize(("path", "expected"), [
    (".atc/README.md", True), (".atc/atc.lock", True), (".atc/governance/CORE.md", True),
    (".atc/lint/ruff.toml", True), (".atc/lint/vale/ATC/RoleTerms.yml", True), (".atc\\lint\\vale.ini", True),
    (".atc/a-file-a-later-release-adds.json", True),
    (".atc/tasks/T-0001.yaml", True), (".atc/tasks/sub/T.yaml", False), (".atc/tasks/notes.md", False),
    (".atcx/README.md", False), ("AGENTS.md", False), ("docs/.atc/x.md", False)])
def test_the_path_rule(path: str, expected: bool) -> None:
    assert managed.by_path(path) is expected
    assert managed.is_managed(path, None) is expected


@pytest.mark.integration
@pytest.mark.parametrize(("path", "files", "expected"), [
    (".agents/skills/s/references/x.md", {".agents/skills/s/SKILL.md": MANAGED_SKILL}, True),
    (".dsh/skills/hami-retro/SKILL.md", {".dsh/skills/hami-retro/SKILL.md": MANAGED_SKILL}, True),
    (".new-harness/skills/s/SKILL.md", {".new-harness/skills/s/SKILL.md": MANAGED_SKILL}, True),
    (".claude/skills/s/SKILL.md", {".claude/skills/s/SKILL.md": OWN_SKILL}, False),
    (".claude/skills/s/SKILL.md", {".claude/skills/s/SKILL.md": MARKER_IN_BODY}, False),
    (".claude/skills/s/SKILL.md", {".claude/skills/s/SKILL.md": "# S\nmanaged-by: agent-traffic-control\n"}, False),
    (".claude\\skills\\s\\SKILL.md", {".claude/skills/s/SKILL.md": MANAGED_SKILL}, True),
    (".claude/skills/s/scripts/run.py", {".claude/skills/s/SKILL.md": OWN_SKILL}, False),
    (".claude/settings.json", {}, False),
    ("packages/a/skills/s/SKILL.md", {"packages/a/skills/s/SKILL.md": MANAGED_SKILL}, False)])
def test_a_skill_copy_is_managed_by_its_marker(tmp_path: Path, path: str, files: dict[str, str],
                                               expected: bool) -> None:
    write(tmp_path, files)
    assert managed.is_managed(path, tmp_path) is expected


@pytest.mark.unit
def test_without_a_root_a_skill_copy_is_not_claimed(tmp_path: Path) -> None:
    write(tmp_path, {".dsh/skills/s/SKILL.md": MANAGED_SKILL})
    assert not managed.is_managed(".dsh/skills/s/SKILL.md", None)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    write(tmp_path, {".claude/skills/s/SKILL.md": MANAGED_SKILL, ".claude/skills/own/SKILL.md": OWN_SKILL})
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "skills")
    return tmp_path


@pytest.mark.integration
def test_a_deleted_skill_copy_is_read_at_the_base(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "rm", "-q", "-r", ".claude")
    git(repo, "commit", "-q", "-m", "drop")
    assert managed.is_managed(".claude/skills/s/SKILL.md", repo, base)
    assert not managed.is_managed(".claude/skills/own/SKILL.md", repo, base)


@pytest.mark.integration
def test_a_deleted_skill_copy_is_read_before_the_commit_that_deleted_it(repo: Path) -> None:
    git(repo, "rm", "-q", "-r", ".claude")
    git(repo, "commit", "-q", "-m", "drop")
    write(repo, {"later.md": "x\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "later")
    assert managed.is_managed(".claude/skills/s/references/x.md", repo)
    assert not managed.is_managed(".claude/skills/own/SKILL.md", repo)
    assert not managed.is_managed(".claude/skills/never/SKILL.md", repo)

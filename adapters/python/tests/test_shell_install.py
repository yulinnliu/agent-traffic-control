"""scripts/link-skills.sh and scripts/check-skills.sh run under a POSIX sh."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import onboard_project
from test_tooling import run_git, write

SH = shutil.which("sh")
SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
pytestmark = pytest.mark.skipif(SH is None, reason="no POSIX sh on PATH")
# check-skills.sh judges the lock against this ATC tree's own history (`git -C "$ROOT" rev-parse HEAD`):
# a Git checkout, as the integration candidate and CI are, has one; an `atc tmp checkout` export has none.
needs_atc_history = pytest.mark.skipif(
    not (SCRIPTS.parent / ".git").exists(),
    reason="check-skills.sh reads the ATC tree's Git history; this tree is an export without .git",
)


def sh(script: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([SH, (SCRIPTS / script).as_posix(), *args], capture_output=True, text=True, cwd=cwd)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    # link-skills stops before writing without this section (GIT_WORKFLOW.md § Lock advancement).
    write(repo / "AGENTS.md", "# AGENTS.md\n\n## Integration verification\n\n- Verifier argv file: verify.json\n")
    write(repo / "verify.json", '["true"]\n')
    return repo


def test_directory_without_skill_md_is_a_collision(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    mine = repo / ".claude/skills/atc-review/notes/mine.md"
    write(mine, "mine\n")
    run = sh("link-skills.sh", repo.as_posix(), "atc-review")
    assert run.returncode == 1
    assert "collision  .claude/skills/atc-review (files but no SKILL.md, left alone)" in run.stdout
    assert mine.read_text(encoding="utf-8") == "mine\n" and not (mine.parents[1] / "SKILL.md").exists()
    assert sh("link-skills.sh", repo.as_posix(), "atc-review").returncode == 1
    assert mine.is_file()


@needs_atc_history
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


@needs_atc_history
def test_dangling_skill_md_link_is_relinked_only_when_alone(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    adapter = repo / ".claude/skills/atc-review"
    adapter.mkdir(parents=True)
    try:
        os.symlink(tmp_path / "moved/SKILL.md", adapter / "SKILL.md")
    except OSError:
        pytest.skip("this host cannot create symlinks")
    write(adapter / "mine.md", "mine\n")
    run = sh("link-skills.sh", repo.as_posix(), "atc-review")
    assert "collision  .claude/skills/atc-review (files but no SKILL.md, left alone)" in run.stdout
    assert (adapter / "mine.md").is_file() and (adapter / "SKILL.md").is_symlink()
    (adapter / "mine.md").unlink()
    assert sh("link-skills.sh", repo.as_posix(), "atc-review").returncode == 0
    assert (adapter / "SKILL.md").is_file() and sh("check-skills.sh", repo.as_posix()).returncode == 0


POINTER = (SCRIPTS.parent / "shared/templates/PROJECT_AGENTS.md").read_text(encoding="utf-8").strip()
SECTION = "## Integration verification\n\n- Verifier argv file: verify.json\n"


def tree(repo: Path) -> dict[str, bytes]:
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo).parts}


def expected_lock() -> str:
    url = subprocess.run(["git", "-C", str(SCRIPTS.parent), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    source = url.rstrip("/").removesuffix(".git").replace(":", "/").split("/")
    revision = subprocess.run(["git", "-C", str(SCRIPTS.parent), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    # link-skills.sh records `unknown` for what the ATC checkout cannot name.
    return f'version = 1\nsource = "{"/".join(source[-2:]) if url else "unknown"}"\nrevision = "{revision or "unknown"}"\n'


@pytest.mark.integration
@pytest.mark.parametrize("agents, reason", [
    ("# AGENTS.md\n\n## Other\n", "has no '## Integration verification' section"),
    ("# AGENTS.md\n\n## Integration verification\n\n- Verifier timeout: 60\n", "has no 'Verifier argv file:' line"),
    # A path followed by other words is not the line, as verification_section.ARGV_FILE_LINE reads it.
    ("# AGENTS.md\n\n## Integration verification\n\n- Verifier argv file: verify.json (the verifier)\n", "has no 'Verifier argv file:' line"),
    # Only the first section counts; a later one with the same heading is not read.
    ("# AGENTS.md\n\n## Integration verification\n\nnone\n\n## Integration verification\n\n- Verifier argv file: verify.json\n", "has no 'Verifier argv file:' line"),
    ("# AGENTS.md\n\n## Integration verification\n\n- Verifier argv file: `missing.json`\n", "verifier argv file missing.json named by AGENTS.md does not exist"),
])
def test_without_the_verification_section_link_skills_stops_and_writes_nothing(tmp_path: Path, agents: str, reason: str) -> None:
    repo = make_repo(tmp_path)
    write(repo / "AGENTS.md", agents)
    before = tree(repo)
    run = sh("link-skills.sh", repo.as_posix(), "atc-review")
    assert run.returncode == 2
    # The same reason onboard gives, so both parsers read the section alike.
    assert run.stdout == f"stop       VERIFICATION_SECTION_MISSING {onboard_project.verification_gate(repo, SCRIPTS.parent)}\n"
    assert reason in run.stdout and tree(repo) == before


@pytest.mark.integration
def test_an_ignored_synced_path_stops_link_skills_before_writing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo / ".gitignore", ".atc/\n")
    before = tree(repo)
    run = sh("link-skills.sh", repo.as_posix(), "atc-review")
    assert run.returncode == 2 and run.stdout.startswith("stop       IGNORED_OUTPUT ")
    assert ".atc/README.md" in run.stdout and ".atc/atc.lock" in run.stdout
    assert tree(repo) == before


@pytest.mark.integration
@pytest.mark.parametrize("path, content", [
    (".atc/notes.txt", "mine\n"),
    (".atc/governance/LOCAL.md", "mine\n"),
    (".atc/atc.lock", 'version = 2\nsource = "a/b"\nrevision = "0"\n'),
])
def test_a_file_sync_does_not_write_under_atc_stops_link_skills_before_writing(tmp_path: Path, path: str, content: str) -> None:
    repo = make_repo(tmp_path)
    write(repo / path, content)
    write(repo / onboard_project.validator.TASKS_DIR / "X-0001.yaml", "id: X-0001\n")
    before = tree(repo)
    run = sh("link-skills.sh", repo.as_posix(), "atc-review")
    assert run.returncode == 1 and f"collision  {path} (" in run.stdout
    assert "stop       nothing written" in run.stdout and tree(repo) == before


@pytest.mark.integration
@needs_atc_history
@pytest.mark.parametrize("path", [".atc/notes.txt", ".atc/governance/LOCAL.md"])
def test_check_skills_reports_a_file_sync_does_not_write_under_atc(tmp_path: Path, path: str) -> None:
    repo = make_repo(tmp_path)
    assert sh("link-skills.sh", repo.as_posix(), "atc-review").returncode == 0
    write(repo / onboard_project.validator.TASKS_DIR / "X-0001.yaml", "id: X-0001\n")
    assert sh("check-skills.sh", repo.as_posix()).returncode == 0
    write(repo / path, "mine\n")
    check = sh("check-skills.sh", repo.as_posix())
    assert check.returncode == 1 and f"stray      {path} (not a file sync writes)" in check.stdout
    assert "stray      .atc/tasks/" not in check.stdout


@pytest.mark.integration
def test_link_skills_writes_the_lock_and_pointer_line_then_stays_current(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo / onboard_project.validator.TASKS_DIR / "X-0001.yaml", "id: X-0001\n")
    run = sh("link-skills.sh", repo.as_posix(), "atc-review")
    assert run.returncode == 0, run.stdout
    lock = (repo / ".atc/atc.lock").read_bytes()
    assert lock == expected_lock().encode()
    agents = (repo / "AGENTS.md").read_text(encoding="utf-8").replace("\r", "").split("\n")
    assert agents.index(POINTER) < agents.index("## Integration verification") and agents.count(POINTER) == 1
    assert "updated    AGENTS.md (pointer line inserted before the first section)" in run.stdout
    assert (repo / ".atc/README.md").is_file() and (repo / ".atc/governance/INDEX.md").is_file()
    assert (repo / onboard_project.validator.TASKS_DIR / "X-0001.yaml").read_text(encoding="utf-8") == "id: X-0001\n"
    before = tree(repo)
    again = sh("link-skills.sh", repo.as_posix(), "atc-review")
    assert again.returncode == 0 and "current    AGENTS.md pointer line" in again.stdout and "current    .atc/" in again.stdout
    assert tree(repo) == before


@pytest.mark.integration
def test_link_skills_rewrites_a_drifted_pointer_line_and_relocks(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert sh("link-skills.sh", repo.as_posix(), "atc-review").returncode == 0
    agents = repo / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8").replace(POINTER, "<!-- atc:governance -->old text"), encoding="utf-8")
    run = sh("link-skills.sh", repo.as_posix(), "atc-review")
    assert run.returncode == 0 and "updated    AGENTS.md (pointer line)" in run.stdout
    text = agents.read_text(encoding="utf-8").replace("\r", "")
    assert text.count("<!-- atc:governance -->") == 1 and POINTER in text and "old text" not in text
    assert "locked     .atc/atc.lock" in run.stdout


@pytest.mark.integration
def test_link_skills_relocks_after_rewriting_governance_or_on_another_source(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert sh("link-skills.sh", repo.as_posix(), "atc-review").returncode == 0
    lock = repo / ".atc/atc.lock"
    index = repo / ".atc/governance/INDEX.md"
    index.write_text("edited\n", encoding="utf-8")
    run = sh("link-skills.sh", repo.as_posix(), "atc-review")
    assert "updated    .atc/governance/INDEX.md" in run.stdout and "locked     .atc/atc.lock" in run.stdout
    revision = expected_lock().split("\n")[2]
    lock.write_bytes(f'version = 1\nsource = "elsewhere/repo"\n{revision}\n'.encode())
    run = sh("link-skills.sh", repo.as_posix(), "atc-review")
    assert run.returncode == 0 and "locked     .atc/atc.lock" in run.stdout
    assert lock.read_bytes() == expected_lock().encode()

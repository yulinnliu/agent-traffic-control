from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import git_preflight as gp


def run_git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
    )
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True)
    return result.stdout.strip()


def commit(repo: Path, path: str, content: str) -> str:
    (repo / path).write_text(content, encoding="utf-8")
    run_git(repo, "add", "--", path)
    run_git(repo, "commit", "-q", "-m", f"edit {path}")
    return run_git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit(root, "a.txt", "a\n")
    commit(root, "b.txt", "b\n")
    return root


def topic(repo: Path, edits: dict[str, str]) -> str:
    run_git(repo, "switch", "-q", "-c", "topic")
    for path, content in edits.items():
        commit(repo, path, content)
    tip = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "switch", "-q", "main")
    return tip


def test_fast_forward_when_main_is_merge_base(repo: Path) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    report = gp.merge_preflight(repo, "topic", tip)
    assert (report.verdict, report.reason) == ("FF", "FAST_FORWARD")
    assert report.evidence["topic_tip"] == tip
    assert report.evidence["main_advanced_by"] == 0
    assert report.evidence["reviewed_state"] == "tip"


def test_no_ff_when_main_advanced_on_other_files(repo: Path) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    commit(repo, "c.txt", "c\n")
    report = gp.merge_preflight(repo, "topic", tip[:10])
    assert (report.verdict, report.reason) == ("NO_FF", "COMPATIBLE")
    assert report.evidence["main_advanced_by"] == 1
    assert report.evidence["reviewed_sha"] == tip
    assert report.evidence["overlap_files"] == []


def test_overlap_stops_without_conflict(repo: Path) -> None:
    (repo / "a.txt").write_text("1\n2\n3\n4\n5\n6\n", encoding="utf-8")
    run_git(repo, "commit", "-q", "-am", "base")
    tip = topic(repo, {"a.txt": "one\n2\n3\n4\n5\n6\n"})
    commit(repo, "a.txt", "1\n2\n3\n4\n5\nsix\n")
    report = gp.merge_preflight(repo, "topic", tip)
    assert (report.verdict, report.reason) == ("STOP", "OVERLAP")
    assert report.evidence["conflict"] is False
    assert report.evidence["overlap_files"] == ["a.txt"]


def test_conflict_stops(repo: Path) -> None:
    tip = topic(repo, {"a.txt": "topic\n"})
    commit(repo, "a.txt", "main\n")
    report = gp.merge_preflight(repo, "topic", tip)
    assert (report.verdict, report.reason) == ("STOP", "CONFLICT")
    assert report.evidence["conflict_files"] == ["a.txt"]


def test_missing_review_stops(repo: Path) -> None:
    topic(repo, {"a.txt": "a2\n"})
    report = gp.merge_preflight(repo, "topic", None)
    assert (report.verdict, report.reason) == ("STOP", "NO_REVIEWED_SHA")


def test_commits_after_review_stop(repo: Path) -> None:
    reviewed = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "switch", "-q", "topic")
    commit(repo, "a.txt", "a3\n")
    run_git(repo, "switch", "-q", "main")
    report = gp.merge_preflight(repo, "topic", reviewed)
    assert (report.verdict, report.reason) == ("STOP", "UNREVIEWED_COMMITS")
    assert report.evidence["unreviewed_commits"] == 1


def test_rewritten_review_stops(repo: Path) -> None:
    reviewed = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "switch", "-q", "topic")
    run_git(repo, "commit", "-q", "--amend", "-m", "reworded")
    run_git(repo, "switch", "-q", "main")
    report = gp.merge_preflight(repo, "topic", reviewed)
    assert (report.verdict, report.reason) == ("STOP", "REVIEW_REWRITTEN")


@pytest.mark.parametrize("reviewed", ["topic", "topic~0", "HEAD", "abc123", "x" * 7])
def test_non_sha_review_is_invalid(repo: Path, reviewed: str) -> None:
    topic(repo, {"a.txt": "a2\n"})
    report = gp.merge_preflight(repo, "topic", reviewed)
    assert (report.verdict, report.reason) == ("STOP", "REVIEWED_SHA_INVALID")
    assert report.evidence["reviewed_state"] == "invalid"


def test_full_and_uppercase_reviewed_sha_still_work(repo: Path) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    for reviewed in (tip, tip[:7].upper()):
        report = gp.merge_preflight(repo, "topic", reviewed)
        assert (report.verdict, report.reason) == ("FF", "FAST_FORWARD")
        assert report.evidence["reviewed_sha"] == tip


def test_unknown_sha_stops(repo: Path) -> None:
    topic(repo, {"a.txt": "a2\n"})
    report = gp.merge_preflight(repo, "topic", "0123456789abcdef0123456789abcdef01234567")
    assert (report.verdict, report.reason) == ("STOP", "REVIEWED_SHA_UNKNOWN")


def test_hex_named_branch_does_not_stand_in_for_a_sha(repo: Path) -> None:
    topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "branch", "deadbeef", "topic")
    report = gp.merge_preflight(repo, "topic", "deadbeef")
    assert (report.verdict, report.reason) == ("STOP", "REVIEWED_SHA_UNKNOWN")


def test_dirty_checkout_stops(repo: Path) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    (repo / "b.txt").write_text("dirty\n", encoding="utf-8")
    report = gp.merge_preflight(repo, "topic", tip)
    assert (report.verdict, report.reason) == ("STOP", "CHECKOUT_DIRTY")


def test_cleanup_reports_unmerged_then_removable(repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    run_git(repo, "worktree", "add", "-q", str(wt), "-b", "topic", "HEAD")
    tip = commit(wt, "a.txt", "a2\n")
    report = gp.cleanup_preflight(repo, wt)
    assert (report.verdict, report.reason) == ("KEEP", "NOT_MERGED")
    run_git(repo, "merge", "-q", "--ff-only", "topic")
    (wt / "scratch.txt").write_text("x\n", encoding="utf-8")
    assert gp.cleanup_preflight(repo, wt).reason == "WORKTREE_DIRTY"
    (wt / "scratch.txt").unlink()
    report = gp.cleanup_preflight(repo, wt)
    assert (report.verdict, report.reason) == ("REMOVABLE", "SAFE_TO_REMOVE")
    assert report.evidence["tip"] == tip


def test_cleanup_refuses_main_checkout_and_unknown_path(repo: Path, tmp_path: Path) -> None:
    assert gp.cleanup_preflight(repo, repo).reason == "MAIN_CHECKOUT"
    assert gp.cleanup_preflight(repo, tmp_path / "nowhere").reason == "NOT_A_WORKTREE"


def test_cli_json_and_exit_codes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    assert gp.main(["merge", "--repo", str(repo), "--branch", "topic", "--reviewed", tip, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "FF" and data["reason"] == "FAST_FORWARD"
    assert data["evidence"]["topic_tip"] == tip
    assert gp.main(["merge", "--repo", str(repo), "--branch", "topic"]) == 1
    assert capsys.readouterr().out.startswith("STOP NO_REVIEWED_SHA")
    assert gp.main(["merge", "--repo", str(repo), "--branch", "missing", "--reviewed", tip]) == 2

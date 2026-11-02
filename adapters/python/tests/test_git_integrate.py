from __future__ import annotations

import json
import sys
import os
import subprocess
from pathlib import Path

import pytest

import git_integrate as gi


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def commit(repo: Path, path: str, content: str) -> str:
    (repo / path).write_text(content, encoding="utf-8")
    run_git(repo, "add", "--", path)
    run_git(repo, "commit", "-q", "-m", f"edit {path}")
    return run_git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The command's own git calls inherit this environment, so the merge
    # commit gets a fixed identity and no user or system config applies.
    for key in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(key)
    for key, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    }.items():
        monkeypatch.setenv(key, value)
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit(root, "a.txt", "a\n")
    commit(root, "b.txt", "b\n")
    return root


def topic(repo: Path, edits: dict[str, str], branch: str = "topic") -> str:
    run_git(repo, "switch", "-q", "-c", branch)
    for path, content in edits.items():
        commit(repo, path, content)
    tip = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "switch", "-q", "main")
    return tip


def main_tip(repo: Path) -> str:
    return run_git(repo, "rev-parse", "main")


def verify_command() -> str:
    script = (
        "import os,subprocess; "
        "assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==os.environ['ATC_INTEGRATION_SHA']; "
        "assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip()"
    )
    return json.dumps([sys.executable, "-c", script])


def integrate(repo: Path, branch: str, reviewed: str, *extra: str) -> int:
    return gi.main([
        "--repo", str(repo), "--branch", branch, "--reviewed", reviewed,
        "--authority-reference", "test-owner-authorization", "--verify-command", verify_command(), *extra
    ])


def test_fast_forward_advances_main_to_reviewed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip) == 0
    assert main_tip(repo) == tip
    assert capsys.readouterr().out.strip().startswith(f"FF main {before} -> {tip} candidate {tip} verification=PASS")
    assert run_git(repo, "status", "--porcelain") == ""


def test_no_ff_merges_with_subject_and_parents(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"}, branch="worktree-claude+demo-topic")
    before = commit(repo, "c.txt", "c\n")
    assert integrate(repo, "worktree-claude+demo-topic", tip) == 0
    after = main_tip(repo)
    assert run_git(repo, "rev-parse", "main^1", "main^2").split() == [before, tip]
    assert run_git(repo, "log", "-1", "--format=%s", "main") == "merge(demo-topic): integrate reviewed changes"
    assert capsys.readouterr().out.strip().startswith(f"NO_FF main {before} -> {after} candidate ")


@pytest.mark.parametrize(("branch", "expected"), [
    ("worktree-claude+demo-topic", "demo-topic"),
    ("topic/demo-topic", "demo-topic"),
    ("task/demo-topic", "demo-topic"),
    ("demo-topic", "demo-topic"),
    ("feature/demo", "feature/demo"),
])
def test_topic_name(branch: str, expected: str) -> None:
    assert gi.topic_name(branch) == expected


@pytest.mark.parametrize("branch", ["topic/x-y", "task/x-y"])
def test_no_ff_subject_strips_prefix(repo: Path, branch: str) -> None:
    tip = topic(repo, {"a.txt": "a2\n"}, branch=branch)
    commit(repo, "c.txt", "c\n")
    assert integrate(repo, branch, tip) == 0
    assert run_git(repo, "log", "-1", "--format=%s", "main") == "merge(x-y): integrate reviewed changes"


def stop_unknown(repo: Path) -> str:
    topic(repo, {"a.txt": "a2\n"})
    return "0123456789abcdef0123456789abcdef01234567"


def stop_rewritten(repo: Path) -> str:
    reviewed = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "switch", "-q", "topic")
    run_git(repo, "commit", "-q", "--amend", "-m", "reworded")
    run_git(repo, "switch", "-q", "main")
    return reviewed


def stop_unreviewed(repo: Path) -> str:
    reviewed = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "switch", "-q", "topic")
    commit(repo, "a.txt", "a3\n")
    run_git(repo, "switch", "-q", "main")
    return reviewed


def stop_already_merged(repo: Path) -> str:
    tip = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "merge", "-q", "--ff-only", tip)
    return tip


def stop_not_on_main(repo: Path) -> str:
    tip = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "switch", "-q", "-c", "elsewhere")
    return tip


def stop_dirty(repo: Path) -> str:
    tip = topic(repo, {"a.txt": "a2\n"})
    (repo / "b.txt").write_text("dirty\n", encoding="utf-8")
    return tip


def stop_conflict(repo: Path) -> str:
    tip = topic(repo, {"a.txt": "topic\n"})
    commit(repo, "a.txt", "main\n")
    return tip


def stop_overlap(repo: Path) -> str:
    commit(repo, "a.txt", "1\n2\n3\n4\n5\n6\n")
    tip = topic(repo, {"a.txt": "one\n2\n3\n4\n5\n6\n"})
    commit(repo, "a.txt", "1\n2\n3\n4\n5\nsix\n")
    return tip


@pytest.mark.parametrize(("reason", "setup"), [
    ("REVIEWED_SHA_UNKNOWN", stop_unknown),
    ("REVIEW_REWRITTEN", stop_rewritten),
    ("UNREVIEWED_COMMITS", stop_unreviewed),
    ("ALREADY_MERGED", stop_already_merged),
    ("CHECKOUT_NOT_ON_MAIN", stop_not_on_main),
    ("CHECKOUT_DIRTY", stop_dirty),
    ("CONFLICT", stop_conflict),
    ("OVERLAP", stop_overlap),
])
def test_preflight_stop_leaves_main_unchanged(repo: Path, capsys: pytest.CaptureFixture[str], reason: str, setup) -> None:
    reviewed = setup(repo)
    before = main_tip(repo)
    head = run_git(repo, "rev-parse", "HEAD")
    status = run_git(repo, "status", "--porcelain")
    assert integrate(repo, "topic", reviewed) == 1
    assert capsys.readouterr().out.startswith(f"STOP {reason}")
    assert (main_tip(repo), run_git(repo, "rev-parse", "HEAD"), run_git(repo, "status", "--porcelain")) == (before, head, status)
    assert not (repo / ".git" / "MERGE_HEAD").exists()


@pytest.mark.parametrize("cut", [lambda sha: sha[:12], lambda sha: sha.upper()])
def test_reviewed_must_be_full_lowercase_sha(repo: Path, capsys: pytest.CaptureFixture[str], cut) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    assert integrate(repo, "topic", cut(tip)) == 2
    assert "full 40-character lowercase SHA" in capsys.readouterr().err
    assert main_tip(repo) == before


def test_linked_worktree_is_refused(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    wt = tmp_path / "wt"
    run_git(repo, "worktree", "add", "-q", str(wt), "-b", "side", "main")
    before = main_tip(repo)
    assert integrate(wt, "topic", tip) == 2
    assert "linked worktree" in capsys.readouterr().err
    assert main_tip(repo) == before


def test_subdirectory_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    (repo / "sub").mkdir()
    assert integrate(repo / "sub", "topic", tip) == 2
    assert "top of the checkout" in capsys.readouterr().err


def test_main_moved_after_preflight_stops(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    real = gi.merge_preflight
    moved = []

    def preflight_then_advance(*args):
        report = real(*args)
        moved.append(commit(repo, "c.txt", "c\n"))
        return report

    monkeypatch.setattr(gi, "merge_preflight", preflight_then_advance)
    assert integrate(repo, "topic", tip) == 1
    assert "moved after preflight" in capsys.readouterr().err
    assert main_tip(repo) == moved[0]


def test_crash_after_main_update_is_reconciled_without_reset(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    real_git = gi.git
    crashed = []

    def merge_then_crash(repo_arg, *args, **kwargs):
        result = real_git(repo_arg, *args, **kwargs)
        if args[:2] == ("merge", "--ff-only"):
            crashed.append(args[2])
            raise SystemExit(77)
        return result

    monkeypatch.setattr(gi, "git", merge_then_crash)
    with pytest.raises(SystemExit, match="77"):
        integrate(repo, "topic", tip)
    assert main_tip(repo) == tip
    assert run_git(repo, "rev-parse", "HEAD") == tip
    assert run_git(repo, "status", "--porcelain") == ""
    refs = run_git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/atc/integration-candidates")
    assert f"refs/atc/integration-candidates/{tip} {tip}" in refs

    monkeypatch.setattr(gi, "git", real_git)
    assert integrate(repo, "topic", tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "RECOVERED"
    assert data["recovery_status"] == "CONSISTENT"
    assert data["main_after"] == tip
    assert main_tip(repo) == tip
    assert run_git(repo, "status", "--porcelain") == ""
    assert run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atc/integration-candidates") == ""



def test_recovery_ref_from_other_request_does_not_short_circuit_integration(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first_tip = topic(repo, {"a.txt": "first\n"}, branch="topic-one")
    real_git = gi.git

    def merge_then_crash(repo_arg, *args, **kwargs):
        result = real_git(repo_arg, *args, **kwargs)
        if args[:2] == ("merge", "--ff-only"):
            raise SystemExit(77)
        return result

    monkeypatch.setattr(gi, "git", merge_then_crash)
    with pytest.raises(SystemExit, match="77"):
        integrate(repo, "topic-one", first_tip)
    monkeypatch.setattr(gi, "git", real_git)
    assert main_tip(repo) == first_tip

    # A different reviewed request must not inherit the first request's
    # RECOVERED success. Its candidate is based on the now-advanced main.
    run_git(repo, "switch", "-q", "-c", "topic-two", "main")
    second_tip = commit(repo, "b.txt", "second\n")
    run_git(repo, "switch", "-q", "main")
    assert integrate(repo, "topic-two", second_tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "FF"
    assert data["reviewed"] == second_tip
    assert data["main_after"] == second_tip
    assert data["recovered_candidates"][0]["candidate_sha"] == first_tip
    assert main_tip(repo) == second_tip


def test_untracked_file_does_not_make_checkout_dirty_for_integration(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    (repo / "local-notes.txt").write_text("keep me\n", encoding="utf-8")
    assert integrate(repo, "topic", tip) == 0
    assert main_tip(repo) == tip
    assert (repo / "local-notes.txt").read_text(encoding="utf-8") == "keep me\n"
    assert "integration checkout became dirty" not in capsys.readouterr().err


def test_verifier_timeout_is_reported_and_main_stays_unchanged(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    slow = json.dumps([sys.executable, "-c", "import time; time.sleep(2)"])
    assert integrate(repo, "topic", tip, "--verify-command", slow, "--verify-timeout", "0.05") == 1
    assert "candidate verification timed out" in capsys.readouterr().err
    assert main_tip(repo) == before


def test_missing_verifier_is_reported_without_traceback(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    missing = json.dumps(["atc-verifier-command-that-does-not-exist"])
    assert integrate(repo, "topic", tip, "--verify-command", missing) == 1
    error = capsys.readouterr().err
    assert "could not start candidate verifier" in error
    assert "Traceback" not in error
    assert main_tip(repo) == before


def test_candidate_verification_failure_leaves_main_unchanged(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    fail_command = json.dumps([sys.executable, "-c", "raise SystemExit(9)"])
    assert integrate(repo, "topic", tip, "--verify-command", fail_command) == 1
    assert "candidate verification failed" in capsys.readouterr().err
    assert main_tip(repo) == before
    assert run_git(repo, "rev-parse", "HEAD") == before
    assert run_git(repo, "status", "--porcelain") == ""
    refs = run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atc/integration-candidates")
    assert refs == ""


def test_no_ff_parents_mismatch_fails_verification(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    commit(repo, "c.txt", "c\n")
    real = gi.git
    moved = []

    def advance_before_merge(repo_arg, *args, **kwargs):
        if args[:2] == ("merge", "--no-ff"):
            moved.append(commit(repo, "d.txt", "d\n"))
        return real(repo_arg, *args, **kwargs)

    monkeypatch.setattr(gi, "git", advance_before_merge)
    assert integrate(repo, "topic", tip) == 1
    assert "main drifted after candidate verification" in capsys.readouterr().err
    assert main_tip(repo) == moved[0]
    assert run_git(repo, "status", "--porcelain") == ""


def test_failed_merge_is_aborted(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "topic\n"})
    before = commit(repo, "a.txt", "main\n")
    real = gi.merge_preflight

    def claims_compatible(*args):
        report = real(*args)
        report.verdict, report.reason = "NO_FF", "COMPATIBLE"
        return report

    monkeypatch.setattr(gi, "merge_preflight", claims_compatible)
    assert integrate(repo, "topic", tip) == 1
    assert "candidate merge failed" in capsys.readouterr().err
    assert main_tip(repo) == before
    assert run_git(repo, "status", "--porcelain") == ""


def test_json_fields(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    keys = {"command", "authority_reference", "verdict", "reason", "main_before", "main_after", "reviewed", "source_before", "candidate_sha", "first_parent", "second_parent", "message", "verification", "candidate_ref"}
    before = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"}, branch="first")
    assert integrate(repo, "first", tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "command": "integrate", "authority_reference": "test-owner-authorization", "verdict": "FF", "reason": "FAST_FORWARD", "main_before": before,
        "main_after": tip, "reviewed": tip, "source_before": tip, "candidate_sha": tip,
        "first_parent": None, "second_parent": None, "message": None, "verification": "PASS", "candidate_ref": None,
    }

    second = topic(repo, {"b.txt": "b2\n"}, branch="topic/next")
    base = commit(repo, "c.txt", "c\n")
    assert integrate(repo, "topic/next", second, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == keys
    assert (data["verdict"], data["reason"], data["main_before"]) == ("NO_FF", "COMPATIBLE", base)
    assert (data["main_after"], data["candidate_sha"], data["first_parent"], data["second_parent"]) == (main_tip(repo), main_tip(repo), base, second)
    assert data["verification"] == "PASS"
    assert data["message"] == "merge(next): integrate reviewed changes"


def test_json_stop_carries_preflight(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = stop_conflict(repo)
    before = main_tip(repo)
    assert integrate(repo, "topic", tip, "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert (data["verdict"], data["reason"], data["main_before"], data["main_after"]) == ("STOP", "CONFLICT", before, before)
    assert data["preflight"]["evidence"]["conflict_files"] == ["a.txt"]

"""`atc integrate` against a moving main: fetch first, rebase with approval carry-over, the push listing.

Each test runs a sandbox repository with a bare `origin` beside it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import git_integrate as gi
import git_preflight as gp
from test_git_integrate import (  # noqa: F401  (repo is a fixture)
    REPORT, add_origin, commit, declare_verifier, evidence_dir, integrate, main_tip, publish, refusal, repo,
    report_command, run_git, topic,
)

pytestmark = pytest.mark.integration


def marker_verifier(marker: Path) -> str:
    """A verifier that leaves ``marker`` behind, so a test can tell whether it ran."""
    return json.dumps([sys.executable, "-c", f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')"])


def other_clone(repo: Path) -> Path:
    """A second clone of origin, standing in for another machine or a cloud writer."""
    other = repo.parent / "other"
    if not other.exists():
        subprocess.run(["git", "clone", "-q", str(add_origin(repo)), str(other)], capture_output=True, check=True)
    return other


def push_from_other(repo: Path, path: str, content: str) -> str:
    other = other_clone(repo)
    run_git(other, "pull", "-q", "--ff-only")
    sha = commit(other, path, content)
    run_git(other, "push", "-q", "origin", "main")
    return sha


def records(repo: Path) -> list[dict]:
    path = repo / ".git" / "atc" / "integrations.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def short(sha: str) -> str:
    """A SHA as human output prints it."""
    return sha[:12]


def run(repo: Path, reviewed: str, *extra: str) -> int:
    return gi.main(["--repo", str(repo), "--reviewed", reviewed, "--authority-reference", "owner-approval", *extra])


def rebase(repo: Path, branch: str, onto: str = "main") -> str:
    run_git(repo, "switch", "-q", branch)
    run_git(repo, "rebase", "-q", onto)
    tip = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "switch", "-q", "main")
    return tip


# Fetch first


def test_main_behind_origin_stops_before_the_verifier(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    marker = tmp_path / "ran"
    declare_verifier(repo, marker_verifier(marker))
    publish(repo, "main")
    tip = topic(repo, {"a.txt": "a2\n"})
    remote = push_from_other(repo, "c.txt", "c\n")
    before = main_tip(repo)
    assert run(repo, tip, "--check") == 1
    assert refusal(capsys.readouterr().err)[0].startswith("STOP MAIN_BEHIND_ORIGIN: ")
    assert run(repo, tip) == 1
    err = capsys.readouterr().err
    first, after = refusal(err)
    assert first == f"STOP MAIN_BEHIND_ORIGIN: local main {short(before)} is 1 commits behind origin/main {short(remote)}"
    assert after.startswith("next: fast-forward local main to origin/main")
    assert "fetched=origin" in err
    assert not marker.exists()
    assert main_tip(repo) == before


def test_main_diverged_from_origin_stops_with_both_counts(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(repo, "main")
    tip = topic(repo, {"a.txt": "a2\n"})
    local = commit(repo, "d.txt", "d\n")
    remote = push_from_other(repo, "c.txt", "c\n")
    assert run(repo, tip) == 1
    first, after = refusal(capsys.readouterr().err)
    assert first == (
        f"STOP MAIN_DIVERGED_FROM_ORIGIN: local main {short(local)} has 1 commits origin/main lacks, "
        f"and origin/main {short(remote)} has 1 commits local main lacks"
    )
    assert after.startswith("next: the owner decides how the local-only main commits reach origin")
    assert main_tip(repo) == local


def test_a_failed_fetch_stops(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "remote", "add", "origin", str(tmp_path / "missing.git"))
    before = main_tip(repo)
    assert run(repo, tip) == 1
    first, after = refusal(capsys.readouterr().err)
    assert first.startswith("STOP FETCH_FAILED: git fetch origin failed")
    assert after == "next: restore access to origin (network, credentials), then run again"
    assert main_tip(repo) == before


def test_local_main_ahead_of_origin_integrates(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(repo, "main")
    first = topic(repo, {"a.txt": "a2\n"}, branch="first")
    assert run(repo, first) == 0
    second = topic(repo, {"b.txt": "b2\n"}, branch="second")
    assert run(repo, second) == 0
    assert main_tip(repo) == second


# An unpublished topic on a stale base


def test_an_unpublished_compatible_topic_requires_a_rebase(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    marker = tmp_path / "ran"
    declare_verifier(repo, marker_verifier(marker))
    tip = topic(repo, {"a.txt": "a2\n"})
    base = commit(repo, "c.txt", "c\n")
    assert run(repo, tip) == 1
    first, after = refusal(capsys.readouterr().err)
    assert first.startswith("STOP REBASE_REQUIRED: main advanced by 1 commits since the topic's base ")
    assert f"rebase the topic onto main {short(base)}" in first
    assert after.startswith("next: rebase the unpublished topic onto main, then run again with --rebased")
    assert not marker.exists()
    assert main_tip(repo) == base


@pytest.mark.parametrize(("setup", "reason", "next_start"), [
    ("overlap", "OVERLAP", "next: rebase the unpublished topic onto main, then run again with --rebased"),
    ("conflict", "CONFLICT", "next: rebase the unpublished topic onto main and resolve the conflicts"),
])
def test_unpublished_overlap_and_conflict_name_the_rebase(
    repo: Path, capsys: pytest.CaptureFixture[str], setup: str, reason: str, next_start: str,
) -> None:
    if setup == "overlap":
        commit(repo, "a.txt", "1\n2\n3\n4\n5\n6\n7\n8\n9\n")
        tip = topic(repo, {"a.txt": "one\n2\n3\n4\n5\n6\n7\n8\n9\n"})
        commit(repo, "a.txt", "1\n2\n3\n4\n5\n6\n7\n8\nnine\n")
    else:
        tip = topic(repo, {"a.txt": "topic\n"})
        commit(repo, "a.txt", "main\n")
    assert run(repo, tip) == 1
    first, after = refusal(capsys.readouterr().err)
    assert first.startswith(f"STOP {reason}: ")
    assert after.startswith(next_start)


def stale_two_commit_topic(repo: Path) -> tuple[str, str]:
    """An unpublished two-commit topic, and main advanced by one commit on another file."""
    reviewed = topic(repo, {"a.txt": "a2\n", "d.txt": "d\n"})
    between = commit(repo, "c.txt", "c\n")
    return reviewed, between


def test_a_clean_rebase_carries_the_approval_over(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_verifier(repo, report_command(json.dumps(REPORT)))
    reviewed, between = stale_two_commit_topic(repo)
    rebased = rebase(repo, "topic")
    assert run(repo, reviewed, "--rebased", rebased, "--check") == 0
    assert (f"carried over: the approval for reviewed {short(reviewed)} applies to rebased {short(rebased)}"
            in capsys.readouterr().out)
    assert run(repo, reviewed, "--rebased", rebased) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0].startswith(f"FF repo main {short(between)} -> {short(rebased)} candidate {short(rebased)} verification=PASS")
    assert lines[1] == (
        f"carried over: the approval for reviewed {short(reviewed)} applies to rebased {short(rebased)} "
        "(2 commits, every patch-id equal, clean replay)"
    )
    assert lines[2:4] == ["main commits between: 1", f"  {short(between)} edit c.txt"]
    assert f"reviewed={short(rebased)} carried_from={short(reviewed)}" in captured.err
    assert main_tip(repo) == rebased
    assert (records(repo)[-1]["reviewed"], records(repo)[-1]["carried_over_from"]) == (rebased, reviewed)
    assert (records(repo)[-1]["task"], records(repo)[-1]["authority"]) == ("owner-approval", {"kind": "unclassified"})
    evidence = json.loads((evidence_dir(repo) / f"{rebased}.json").read_text(encoding="utf-8"))
    assert (evidence["reviewed"], evidence["carried_over_from"], evidence["verdict"]) == (rebased, reviewed, "FF")


def test_a_carried_over_rebase_still_runs_the_verifier(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_verifier(repo, json.dumps([sys.executable, "-c", "import sys; sys.exit(5)"]))
    reviewed, between = stale_two_commit_topic(repo)
    rebased = rebase(repo, "topic")
    assert run(repo, reviewed, "--rebased", rebased) == 3
    assert refusal(capsys.readouterr().err)[0].startswith(f"STOP VERIFICATION_FAILED: candidate verification failed for {short(rebased)}")
    assert main_tip(repo) == between


def amended(repo: Path) -> tuple[str, str]:
    reviewed, _ = stale_two_commit_topic(repo)
    run_git(repo, "switch", "-q", "-C", "topic", "main")
    commit(repo, "a.txt", "a2\n")
    commit(repo, "d.txt", "d changed\n")
    run_git(repo, "switch", "-q", "main")
    return reviewed, run_git(repo, "rev-parse", "topic")


def squashed(repo: Path) -> tuple[str, str]:
    reviewed, _ = stale_two_commit_topic(repo)
    rebase(repo, "topic")
    run_git(repo, "switch", "-q", "topic")
    run_git(repo, "reset", "-q", "--soft", "HEAD~2")
    run_git(repo, "commit", "-q", "-m", "squashed")
    run_git(repo, "switch", "-q", "main")
    return reviewed, run_git(repo, "rev-parse", "topic")


def reordered(repo: Path) -> tuple[str, str]:
    reviewed, _ = stale_two_commit_topic(repo)
    first, second = run_git(repo, "rev-list", "--reverse", f"{reviewed}~2..{reviewed}").split()
    run_git(repo, "switch", "-q", "-C", "topic", "main")
    run_git(repo, "cherry-pick", second, first)
    run_git(repo, "switch", "-q", "main")
    return reviewed, run_git(repo, "rev-parse", "topic")


def onto_old_main(repo: Path) -> tuple[str, str]:
    reviewed, between = stale_two_commit_topic(repo)
    rebased = rebase(repo, "topic")
    commit(repo, "e.txt", "e\n")
    # Published, so the preflight admits it as NO_FF and the carry-over check decides.
    publish(repo, "topic")
    return reviewed, rebased


def published_reviewed(repo: Path) -> tuple[str, str]:
    reviewed, _ = stale_two_commit_topic(repo)
    publish(repo, f"{reviewed}:refs/heads/topic")
    return reviewed, rebase(repo, "topic")


@pytest.mark.parametrize(("setup", "detail"), [
    (amended, "commit 2: patch-id of "),
    (squashed, "has 2 commits, the rebased range "),
    (reordered, "commit 1: patch-id of "),
    (onto_old_main, "the rebased tip is not based on main "),
    (published_reviewed, "the reviewed SHA is published at origin/topic"),
])
def test_a_rebase_that_is_not_equivalent_is_refused(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], setup, detail: str,
) -> None:
    marker = tmp_path / "ran"
    declare_verifier(repo, marker_verifier(marker))
    reviewed, rebased = setup(repo)
    before = main_tip(repo)
    assert run(repo, reviewed, "--rebased", rebased) == 1
    first, after = refusal(capsys.readouterr().err)
    assert first.startswith("STOP CARRY_OVER_REFUSED: ")
    assert detail in first
    assert first.endswith(f"the approval for {short(reviewed)} does not carry over to {short(rebased)}")
    assert after == "next: the rebased tip needs a fresh review; integrate the SHA that review names"
    assert not marker.exists()
    assert main_tip(repo) == before


def test_a_conflicting_replay_is_refused_even_when_the_patch_ids_match(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    reviewed = topic(repo, {"a.txt": "topic\n"})
    commit(repo, "a.txt", "main\n")
    run_git(repo, "switch", "-q", "-C", "topic", "main")
    rebased = commit(repo, "a.txt", "topic\n")
    run_git(repo, "switch", "-q", "main")
    monkeypatch.setattr(gi, "_patch_ids", lambda _repo, commits: ["same"] * len(commits))
    assert run(repo, reviewed, "--rebased", rebased) == 1
    first, _ = refusal(capsys.readouterr().err)
    assert first.startswith(f"STOP CARRY_OVER_REFUSED: replaying {short(reviewed)} onto main conflicts in a.txt")


# A published topic


def test_a_published_topic_merges_and_keeps_the_reviewed_sha(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(repo, "main")
    reviewed = topic(repo, {"a.txt": "a2\n"})
    publish(repo, "topic")
    remote = push_from_other(repo, "c.txt", "c\n")
    run_git(repo, "fetch", "-q", "origin")
    run_git(repo, "merge", "-q", "--ff-only", remote)
    assert run(repo, reviewed) == 0
    assert run_git(repo, "rev-parse", "main^1", "main^2").split() == [remote, reviewed]
    assert run_git(repo, "rev-parse", "topic", "origin/topic").split() == [reviewed, reviewed]
    assert capsys.readouterr().out.startswith(f"NO_FF repo main {short(remote)} -> ")


def test_a_published_overlap_keeps_the_reconcile_step(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit(repo, "a.txt", "1\n2\n3\n4\n5\n6\n7\n8\n9\n")
    tip = topic(repo, {"a.txt": "one\n2\n3\n4\n5\n6\n7\n8\n9\n"})
    publish(repo, "topic")
    commit(repo, "a.txt", "1\n2\n3\n4\n5\n6\n7\n8\nnine\n")
    assert run(repo, tip) == 1
    assert refusal(capsys.readouterr().err)[1] == f"next: {gp.NEXT['OVERLAP']}"


# The push listing


def test_the_integration_lists_and_flags_what_a_push_carries(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(repo, "main")
    origin = main_tip(repo)
    other = topic(repo, {"a.txt": "a2\n"}, branch="other")
    assert run(repo, other) == 0
    manual = commit(repo, "m.txt", "m\n")
    capsys.readouterr()
    mine = topic(repo, {"b.txt": "b2\n"}, branch="mine")
    assert run(repo, mine) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[1:8] == [
        f"push: 3 commits origin/main {short(origin)} -> main {short(mine)}",
        f'  integration {short(other)} task="owner-approval" authority=unclassified [NO PUSH APPROVAL]',
        f"    {short(other)} edit a.txt",
        "  no integration record [NO PUSH APPROVAL]",
        f"    {short(manual)} edit m.txt",
        f'  integration {short(mine)} task="owner-approval" authority=unclassified [this integration]',
        f"    {short(mine)} edit b.txt",
    ]
    assert "WARNING 2 commits in origin/main..main come from other integrations or none" in captured.err


def test_preflight_push_fetches_and_groups_by_integration(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(repo, "main")
    first = topic(repo, {"a.txt": "a2\n"}, branch="first")
    assert run(repo, first) == 0
    second = topic(repo, {"b.txt": "b2\n"}, branch="second")
    assert run(repo, second) == 0
    capsys.readouterr()
    assert gp.main(["push", "--repo", str(repo), "--integration", second]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "PUSH PUSH_LISTED: 2 commits in origin/main..main; 1 lack this integration's push approval"
    assert lines[-4:] == [
        f'  integration {short(first)} task="owner-approval" authority=unclassified [NO PUSH APPROVAL]',
        f"    {short(first)} edit a.txt",
        f'  integration {short(second)} task="owner-approval" authority=unclassified [this integration]',
        f"    {short(second)} edit b.txt",
    ]
    assert not re.search(r"\b[0-9a-f]{40}\b", "\n".join(lines))
    assert gp.main(["push", "--repo", str(repo), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [(c["sha"], c["integration"], c["push_approval"], c["task"], c["authority"])
            for c in data["push"]["commits"]] == [
        (first, first, None, "owner-approval", {"kind": "unclassified"}),
        (second, second, None, "owner-approval", {"kind": "unclassified"}),
    ]
    push_from_other(repo, "c.txt", "c\n")
    assert gp.main(["push", "--repo", str(repo)]) == 1
    assert capsys.readouterr().out.startswith("STOP MAIN_DIVERGED_FROM_ORIGIN: ")


def test_push_and_integrate_take_no_main_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        gp.main(["push", "--repo", ".", "--main", "main"])
    assert exc.value.code == 2
    assert gi.main(["--repo", ".", "--reviewed", "0" * 40, "--main", "main"]) == 2
    assert "--main" in capsys.readouterr().err

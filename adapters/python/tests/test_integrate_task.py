"""`atc integrate ID` for a task record and the Owner's run (`docs/TASKS.md` § Authority on `main`).

Each test runs real Git in a sandbox repository that declares task records. Every run is the Owner's run
(`git_integrate.OWNER_RUN_QUOTE`); the record grants nothing and only the range's records change.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import git_integrate as gi
import git_preflight as gp
import push
import task_records
from test_git_integrate import (  # noqa: F401  (repo is a fixture)
    commit, main_tip, publish, refusal, repo, run_git,
)

pytestmark = pytest.mark.integration

SHA = "0" * 40
ID = "DEMO-0001"
OWNER_WORDS = "Owner: integrate DEMO-0001 as reviewed"
AUTHORITY = {"kind": "owner_words", "quote": gi.OWNER_RUN_QUOTE}


def agents() -> str:
    return (
        "# AGENTS.md\n\n## Task records\n\n- Task prefix: DEMO\n\n"
        "## Integration verification\n\n- Verifier argv file: verify.json\n\n## Other\n"
    )


def record(task_id: str, branch: str, grants: dict | None = None, **extra: object) -> str:
    authority = {"edit": True, "commit": True, "push_topic": False, "integrate_main": False,
                 "push_main": False, "deploy": False, **(grants or {})}
    granted = any(authority[key] for key in ("push_topic", "integrate_main", "push_main", "deploy"))
    basis = {"authority_basis": f'"{OWNER_WORDS}"'} if granted else {}
    data = {"task_id": task_id, "repository": "example/demo", "base_sha": SHA, "scope": "plan § 1",
            "governance_source": "example/governance", "governance_revision": SHA, "branch": branch,
            "authority": authority, **basis, **extra}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def put(repo: Path, files: dict[str, str | None], message: str = "edit") -> str:
    """Write, or with ``None`` remove, ``files`` and commit them on the current branch."""
    for path, content in files.items():
        target = repo / path
        if content is None:
            run_git(repo, "rm", "-q", "--", path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        run_git(repo, "add", "--", path)
    run_git(repo, "commit", "-q", "-m", message)
    return run_git(repo, "rev-parse", "HEAD")


def on_branch(repo: Path, branch: str, files: dict[str, str | None], start: str = "main") -> str:
    """Commit ``files`` on ``branch`` (created from ``start`` when missing), then return to main."""
    exists = run_git(repo, "branch", "--list", branch)
    run_git(repo, "switch", "-q", *([branch] if exists else ["-c", branch, start]))
    tip = put(repo, files, f"edit on {branch}")
    run_git(repo, "switch", "-q", "main")
    return tip


@pytest.fixture
def tasks_repo(repo: Path) -> Path:
    """The integrate fixture repository, declaring the task prefix `DEMO`."""
    put(repo, {"AGENTS.md": agents()}, "declare task records")
    return repo


def run(repo: Path, reviewed: str, *extra: str, task: str = ID) -> int:
    return gi.main([task, "--sha", reviewed, *extra], root=repo.parent)


def run_json(repo: Path, capsys: pytest.CaptureFixture[str], reviewed: str, *extra: str,
             task: str = ID) -> tuple[int, dict, str]:
    status = run(repo, reviewed, "--json", *extra, task=task)
    captured = capsys.readouterr()
    return status, json.loads(captured.out), captured.err


def lines(repo: Path) -> list[dict]:
    return gp.read_integrations(repo)


def statuses(repo: Path) -> dict[str, str]:
    return {row["task_id"]: row["status"] for row in task_records.tasks("demo", repo)}


def bind(repo: Path, capsys: pytest.CaptureFixture[str], branch: str = "work") -> str:
    """Bind DEMO-0001 on main through a records-only integration of its record."""
    tip = on_branch(repo, branch, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", branch, {"push_topic": True})})
    status, data, err = run_json(repo, capsys, tip)
    assert status == 0, err
    return tip


def catch_up(repo: Path, branch: str) -> None:
    run_git(repo, "switch", "-q", branch)
    run_git(repo, "merge", "-q", "--ff-only", "main")
    run_git(repo, "switch", "-q", "main")


# The records-only binding, then the work under the task


def test_a_records_only_binding_then_an_ff_integrates_the_task(tasks_repo: Path,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    repo = tasks_repo
    before = main_tip(repo)
    binding = on_branch(repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work")})
    status, data, err = run_json(repo, capsys, binding)
    assert status == 0, err
    assert (data["verdict"], data["records_only"], data["tasks"]) == ("FF", True, ["DEMO-0001"])
    assert data["authority"] == AUTHORITY
    assert (data["evidence"], data["evidence_reason"]) == (None, "RECORDS_ONLY")
    assert [(line["task"], line["records_only"], line["main_before"], line["authority"]) for line in lines(repo)] == [
        ("DEMO-0001", True, before, AUTHORITY),
    ]
    assert statuses(repo) == {"DEMO-0001": "bound"}

    work = on_branch(repo, "work", {"a.txt": "work\n"})
    assert statuses(repo) == {"DEMO-0001": "open"}
    status, data, err = run_json(repo, capsys, work)
    assert status == 0, err
    assert (data["verdict"], data["main_after"], data["records_only"]) == ("FF", work, False)
    assert data["authority"] == AUTHORITY
    assert (lines(repo)[-1]["task"], lines(repo)[-1]["main_before"]) == ("DEMO-0001", binding)
    assert statuses(repo) == {"DEMO-0001": "integrated"}


def test_the_human_result_names_the_task_and_the_authority_kind(tasks_repo: Path,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    bind(tasks_repo, capsys)
    work = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    assert run(tasks_repo, work) == 0
    captured = capsys.readouterr()
    assert 'task="DEMO-0001" authority=owner_words log ' in captured.out.splitlines()[0]
    assert not re.search(r"\b[0-9a-f]{40}\b", captured.out + captured.err)
    assert str(tasks_repo) not in captured.out + captured.err


def test_a_records_only_binding_of_several_tasks_writes_a_line_per_task(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    before = main_tip(tasks_repo)
    tip = on_branch(tasks_repo, "bind", {
        ".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "bind"),
        ".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "two"),
    })
    status, data, err = run_json(tasks_repo, capsys, tip)
    assert status == 0, err
    assert [(line["task"], line["candidate_sha"], line["main_before"], line["records_only"]) for line in lines(tasks_repo)] == [
        ("DEMO-0001", tip, before, True), ("DEMO-0002", tip, before, True),
    ]


def test_a_records_only_range_runs_the_record_check_not_the_verifier(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    put(tasks_repo, {"verify.json": '["no-such-verifier"]'}, "declare a verifier that cannot start")
    broken = record("DEMO-0001", "work").replace("scope: plan § 1\n", "")
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": broken})
    before = main_tip(tasks_repo)
    status, data, err = run_json(tasks_repo, capsys, tip)
    assert status == 3
    assert data["refusal"]["reason"] == "VERIFICATION_FAILED"
    assert "schema-violation: DEMO-0001.yaml" in data["refusal"]["detail"]
    assert "candidate " in err and "record check" in err
    assert main_tip(tasks_repo) == before


def test_the_check_of_a_records_only_range_reports_the_record_check_for_reuse(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work")})
    status, data, err = run_json(tasks_repo, capsys, tip, "--check")
    assert status == 0, err
    assert data["verification_reuse"] == "RECORDS_ONLY"


def test_the_check_of_a_records_only_range_runs_the_record_check(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    broken = record("DEMO-0001", "work").replace("scope: plan § 1\n", "")
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": broken})
    before = main_tip(tasks_repo)
    status, data, _ = run_json(tasks_repo, capsys, tip, "--check")
    assert (status, data["refusal"]["reason"]) == (1, "VERIFICATION_FAILED")
    assert "schema-violation: DEMO-0001.yaml" in data["refusal"]["detail"]
    assert main_tip(tasks_repo) == before


def test_a_prefix_dropped_from_main_leaves_the_task_unknown(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    # The repository is found from the prefix AGENTS.md declares on main, so a main without it cannot
    # resolve the record ID at all; the record check never runs.
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work")})
    publish(tasks_repo, "work")
    put(tasks_repo, {"AGENTS.md": "# AGENTS.md\n\n## Other\n"}, "drop the task records section on main")
    status, data, _ = run_json(tasks_repo, capsys, tip, "--check")
    assert (status, data["refusal"]["reason"]) == (2, "UNKNOWN_TASK")


def test_a_records_only_range_needs_no_verification_section(tasks_repo: Path,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    put(tasks_repo, {"AGENTS.md": "# AGENTS.md\n\n## Task records\n\n- Task prefix: DEMO\n"})
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work")})
    status, _, err = run_json(tasks_repo, capsys, tip)
    assert status == 0, err


# The task ID

def test_a_task_the_reviewed_commit_lacks_is_not_found(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    status, data, _ = run_json(tasks_repo, capsys, tip, task="DEMO-0007")
    assert (status, data["refusal"]["reason"]) == (2, "TASK_NOT_FOUND")


def test_a_record_in_the_reviewed_commit_bound_to_another_branch_stops(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    on_branch(tasks_repo, "elsewhere", {"c.txt": "c\n"})
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "elsewhere"), "a.txt": "w\n"})
    before = main_tip(tasks_repo)
    status, data, _ = run_json(tasks_repo, capsys, tip)
    assert (status, data["refusal"]["reason"]) == (1, "TASK_NOT_FOUND")
    assert main_tip(tasks_repo) == before


def test_a_missing_task_ID_is_a_usage_error(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    assert gi.main(["--sha", tip], root=tasks_repo.parent) == 2
    assert capsys.readouterr().err.startswith("ERROR USAGE ")


# The record's task in a range (no grant to check: the Owner's run is the authority)


@pytest.mark.parametrize(("change", "reason"), [
    ({".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work", {"push_main": True}), "a.txt": "w\n"},
     "TASK_AUTHORITY_CHANGED"),
    ({".atc/tasks/DEMO-0001.yaml": None, "a.txt": "w\n"}, "TASK_NOT_FOUND"),
    ({".atc/tasks/DEMO-0001.yaml": None, ".atc/tasks/DEMO-0003.yaml": record("DEMO-0003", "work"), "a.txt": "w\n"},
     "TASK_NOT_FOUND"),
], ids=["changed", "removed", "renamed"])
def test_a_mixed_range_never_changes_a_record_on_main(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str], change: dict, reason: str,
) -> None:
    bind(tasks_repo, capsys)
    before = main_tip(tasks_repo)
    work = on_branch(tasks_repo, "work", change)
    status, data, _ = run_json(tasks_repo, capsys, work)
    assert data["refusal"]["reason"] == reason
    assert main_tip(tasks_repo) == before


def test_a_records_only_narrowing_of_a_record_integrates_under_the_owner_run(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    bind(tasks_repo, capsys)
    narrowed = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work")})
    status, data, err = run_json(tasks_repo, capsys, narrowed)
    assert (status, data["records_only"]) == (0, True), err


@pytest.mark.parametrize(("grants", "expected"), [({"push_topic": True}, 1), ({}, 0)])
def test_a_new_record_in_a_mixed_range_grants_edit_and_commit_at_most(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str], grants: dict, expected: int,
) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work", grants), "a.txt": "w\n"})
    status, data, err = run_json(tasks_repo, capsys, tip)
    assert status == expected, err
    if expected:
        assert data["refusal"]["reason"] == "TASK_AUTHORITY_CHANGED"


def test_a_number_main_binds_to_another_branch_is_taken(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clash = on_branch(tasks_repo, "mine", {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "mine"), "a.txt": "w\n"})
    theirs = on_branch(tasks_repo, "theirs", {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "theirs")})
    status, _, err = run_json(tasks_repo, capsys, theirs, task="DEMO-0002")
    assert status == 0, err
    status, data, _ = run_json(tasks_repo, capsys, clash, task="DEMO-0002")
    assert (status, data["refusal"]["reason"]) == (1, "TASK_ID_TAKEN")
    multi = on_branch(tasks_repo, "multi", {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "multi")}, start="main")
    status, data, _ = run_json(tasks_repo, capsys, multi, task="DEMO-0002")
    assert (status, data["refusal"]["reason"]) == (1, "TASK_ID_TAKEN")


# The record names the branch


def test_the_record_names_the_branch_among_several_at_the_reviewed_sha(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work"), "a.txt": "w\n"})
    run_git(tasks_repo, "branch", "copy", tip)
    status, data, err = run_json(tasks_repo, capsys, tip)
    assert status == 0, err
    assert main_tip(tasks_repo) == tip


def test_a_record_naming_a_missing_branch_stops(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "gone"), "a.txt": "w\n"})
    before = main_tip(tasks_repo)
    status, data, _ = run_json(tasks_repo, capsys, tip)
    assert (status, data["refusal"]["reason"]) == (1, "NO_BRANCH_AT_REVIEWED")
    assert "names branch gone, which is no local branch" in data["refusal"]["detail"]
    assert main_tip(tasks_repo) == before


def test_commits_after_the_reviewed_sha_on_the_record_branch_stop(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    reviewed = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work"), "a.txt": "w\n"})
    run_git(tasks_repo, "branch", "keep", reviewed)  # inference alone would take this branch
    run_git(tasks_repo, "switch", "-q", "work")
    put(tasks_repo, {"b.txt": "more\n"})
    run_git(tasks_repo, "switch", "-q", "main")
    before = main_tip(tasks_repo)
    status, data, _ = run_json(tasks_repo, capsys, reviewed)
    assert (status, data["refusal"]["reason"]) == (1, "UNREVIEWED_COMMITS")
    assert main_tip(tasks_repo) == before


def test_a_reworded_record_branch_carries_the_review_over(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    reviewed = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work"), "a.txt": "w\n"})
    run_git(tasks_repo, "switch", "-q", "work")
    run_git(tasks_repo, "commit", "-q", "--amend", "-m", "reworded")
    rebased = run_git(tasks_repo, "rev-parse", "HEAD")
    run_git(tasks_repo, "switch", "-q", "main")
    status, data, err = run_json(tasks_repo, capsys, reviewed)
    assert status == 0, err
    assert data["carried_over"]["carried_over_from"] == reviewed
    assert main_tip(tasks_repo) == rebased


def test_a_records_only_binding_of_several_tasks_takes_the_branch_its_task_record_names(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    tip = on_branch(tasks_repo, "bind", {
        ".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "one"),
        ".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "bind"),
    })
    run_git(tasks_repo, "branch", "copy", tip)
    status, data, err = run_json(tasks_repo, capsys, tip, task="DEMO-0002")
    assert status == 0, err
    assert main_tip(tasks_repo) == tip
    assert [line["task"] for line in lines(tasks_repo)] == ["DEMO-0001", "DEMO-0002"]


@pytest.mark.parametrize("elsewhere", [False, True])
def test_a_records_only_range_whose_record_names_another_branch_stops(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str], elsewhere: bool,
) -> None:
    if elsewhere:
        run_git(tasks_repo, "branch", "two", "main")
    tip = on_branch(tasks_repo, "bind", {
        ".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "one"),
        ".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "two"),
    })
    before = main_tip(tasks_repo)
    status, data, _ = run_json(tasks_repo, capsys, tip, task="DEMO-0002")
    assert (status, data["refusal"]["reason"]) == (1, "NO_BRANCH_AT_REVIEWED")
    expected = "which is not at the reviewed SHA" if elsewhere else "which is no local branch"
    assert expected in data["refusal"]["detail"]
    assert f"point two at {tip} or correct the record's branch" in data["refusal"]["detail"]
    assert (main_tip(tasks_repo), lines(tasks_repo)) == (before, [])


def test_a_records_only_range_names_a_task_it_does_not_change_as_a_usage_error(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    on_branch(tasks_repo, "other", {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "other")})
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work")})
    assert run(tasks_repo, tip, "--check", task="DEMO-0002") == 2
    assert capsys.readouterr().err.startswith(
        "ERROR USAGE DEMO-0002 names no record this records-only range changes: DEMO-0001; a records-only "
        "range integrates under one of the records it changes, so name one of them, or add the record of "
        "DEMO-0002 to the range")


# A recovered candidate resolves its authority as any other run


def stop_after_update(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dirty the checkout right after the fast-forward, so the run stops for recovery with main at the candidate."""
    real_git = gi.git

    def dirty_after_ff(repo_arg, *args, **kwargs):
        result = real_git(repo_arg, *args, **kwargs)
        if args[:2] == ("merge", "--ff-only"):
            (repo / "a.txt").write_text("unexpected checkout mutation\n", encoding="utf-8")
        return result

    monkeypatch.setattr(gi, "git", dirty_after_ff)


def recover(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tip: str) -> None:
    stop_after_update(repo, monkeypatch)
    assert run_json(repo, capsys, tip)[0] == 3
    monkeypatch.undo()
    run_git(repo, "checkout", "--", "a.txt")
    assert main_tip(repo) == tip


def test_a_recovery_under_the_owner_run_is_recovered(
    tasks_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work"), "a.txt": "w\n"})
    recover(tasks_repo, monkeypatch, capsys, tip)
    status, data, err = run_json(tasks_repo, capsys, tip)
    assert (status, data["verdict"], data["authority"]) == (0, "RECOVERED", AUTHORITY), err
    assert len(lines(tasks_repo)) == 1


# The push listing


def test_the_push_listing_names_every_task_of_a_binding(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(tasks_repo, "main")
    tip = on_branch(tasks_repo, "bind", {
        ".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "bind"),
        ".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "two"),
    })
    assert run_json(tasks_repo, capsys, tip)[0] == 0
    listing = gp.push_listing(tasks_repo)
    assert [(c["task"], c["tasks"], c["authority"]["kind"]) for c in listing["commits"]] == [
        (None, ["DEMO-0001", "DEMO-0002"], "owner_words"),
    ]
    assert gp.render_push(listing)[1] == f'  integration {tip[:12]} task="DEMO-0001,DEMO-0002" authority=owner_words'


def test_the_push_listing_reads_lines_written_before_task_and_authority(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    publish(tasks_repo, "main")
    base = main_tip(tasks_repo)
    reviewed = put(tasks_repo, {"a.txt": "old\n"})
    synced = put(tasks_repo, {"b.txt": "sync\n"})
    revision = "b" * 40
    path = tasks_repo / ".git" / "atc" / "integrations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"candidate_sha": reviewed, "reviewed": reviewed, "main_before": base,
                    "authority_reference": f"docs/PLAN.md §2 step 1 @ {reviewed}", "integrated_at": "2026-11-01T00:00:00+00:00"}) + "\n"
        + json.dumps({"candidate_sha": synced, "main_before": reviewed, "kind": "sync", "source": "example/governance",
                      "source_revision": revision, "authority_reference": f"governance sync from example/governance at {revision}",
                      "integrated_at": "2026-11-01T00:00:00+00:00"}) + "\n",
        encoding="utf-8")
    listing = gp.push_listing(tasks_repo)
    assert [(c["sha"], c["task"], c["authority"], c["authority_reference"] is not None) for c in listing["commits"]] == [
        (reviewed, "docs/PLAN.md §2 step 1", {"kind": "unclassified"}, True),
        (synced, revision, {"kind": "sync"}, True),
    ]
    assert gp.render_push(listing)[1:] == [
        f'  integration {reviewed[:12]} task="docs/PLAN.md §2 step 1" authority=unclassified',
        f"    {reviewed[:12]} edit",
        f'  integration {synced[:12]} task="{revision[:12]}" authority=sync',
        f"    {synced[:12]} edit",
    ]
    assert push.main(["repo", "--check"], root=tasks_repo.parent) == 0
    assert 'task="docs/PLAN.md §2 step 1" authority=unclassified' in capsys.readouterr().out


def test_a_legacy_line_without_a_task_shows_none_as_a_dash(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(tasks_repo, "main")
    base = main_tip(tasks_repo)
    tip = put(tasks_repo, {"a.txt": "old\n"})
    path = tasks_repo / ".git" / "atc" / "integrations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"candidate_sha": tip, "reviewed": tip, "main_before": base}) + "\n", encoding="utf-8")
    listing = gp.push_listing(tasks_repo)
    assert [(c["task"], c["tasks"]) for c in listing["commits"]] == [(None, [])]
    rendered = gp.render_push(listing)
    assert rendered[1] == f"  integration {tip[:12]} task=- authority=unclassified"
    assert "None" not in "\n".join(rendered)


# --check


def test_a_check_runs_under_the_owner_run_and_moves_nothing(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    work = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work"), "a.txt": "work\n"})
    before = main_tip(tasks_repo)
    assert run(tasks_repo, work, "--check") == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("CHECK FF ")
    assert 'task="DEMO-0001" authority=owner_words' in captured.out
    assert main_tip(tasks_repo) == before

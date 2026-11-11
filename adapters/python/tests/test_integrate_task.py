"""`atc integrate` with a task and its authority (`docs/TASKS.md` § Authority on `main`).

Each test runs real Git in a sandbox repository that declares task records.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import git_integrate as gi
import git_preflight as gp
import task_records
from test_git_integrate import (  # noqa: F401  (repo is a fixture)
    commit, main_tip, publish, refusal, repo, run_git,
)

pytestmark = pytest.mark.integration

SHA = "0" * 40
WORDS = "Owner: integrate DEMO-0001 as reviewed"
GRANT = {"integrate_main": True}


def agents() -> str:
    return (
        "# AGENTS.md\n\n## Task records\n\n- Task prefix: DEMO\n\n"
        "## Integration verification\n\n- Verifier argv file: verify.json\n\n## Other\n"
    )


def record(task_id: str, branch: str, grants: dict | None = None, **extra: object) -> str:
    authority = {"edit": True, "commit": True, "push_topic": False, "integrate_main": False,
                 "push_main": False, "deploy": False, **(grants or {})}
    basis = {"authority_basis": f'"{WORDS}"'} if any(authority[k] for k in ta_grants()) else {}
    data = {"task_id": task_id, "repository": "example/demo", "base_sha": SHA, "scope": "plan § 1",
            "governance_source": "example/governance", "governance_revision": SHA, "branch": branch,
            "authority": authority, **basis, **extra}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def ta_grants() -> tuple[str, ...]:
    return ("push_topic", "integrate_main", "push_main", "deploy")


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


def run(repo: Path, reviewed: str, *extra: str) -> int:
    return gi.main(["--project", "repo", "--reviewed", reviewed, *extra], root=repo.parent)


def run_json(repo: Path, capsys: pytest.CaptureFixture[str], reviewed: str, *extra: str) -> tuple[int, dict, str]:
    status = run(repo, reviewed, "--json", *extra)
    captured = capsys.readouterr()
    return status, json.loads(captured.out), captured.err


def lines(repo: Path) -> list[dict]:
    return gp.read_integrations(repo)


def statuses(repo: Path) -> dict[str, str]:
    return {row["task_id"]: row["status"] for row in task_records.tasks("demo", repo)}


def bind(repo: Path, capsys: pytest.CaptureFixture[str], branch: str = "work") -> str:
    """Bind DEMO-0001 on main with integrate_main through a records-only `--owner-words` integration."""
    tip = on_branch(repo, branch, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", branch, GRANT)})
    status, data, err = run_json(repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert status == 0, err
    return tip


# The real path: bind on main, then integrate the work under the record


def test_a_records_only_binding_then_an_ff_under_the_record(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tasks_repo
    before = main_tip(repo)
    binding = on_branch(repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work", GRANT)})
    status, data, err = run_json(repo, capsys, binding, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert status == 0, err
    assert (data["verdict"], data["records_only"], data["tasks"]) == ("FF", True, ["DEMO-0001"])
    assert data["authority"] == {"kind": "owner_words", "quote": WORDS}
    assert (data["evidence"], data["evidence_reason"]) == (None, "RECORDS_ONLY")
    assert [(line["task"], line["records_only"], line["main_before"], line["authority"]) for line in lines(repo)] == [
        ("DEMO-0001", True, before, {"kind": "owner_words", "quote": WORDS}),
    ]
    assert statuses(repo) == {"DEMO-0001": "bound"}

    work = on_branch(repo, "work", {"a.txt": "work\n"})
    assert statuses(repo) == {"DEMO-0001": "open"}
    status, data, err = run_json(repo, capsys, work, "--task", "DEMO-0001")
    assert status == 0, err
    assert (data["verdict"], data["main_after"], data["records_only"]) == ("FF", work, False)
    assert data["authority"] == {"kind": "record", "task_id": "DEMO-0001"}
    assert (lines(repo)[-1]["task"], lines(repo)[-1]["main_before"]) == ("DEMO-0001", binding)
    assert statuses(repo) == {"DEMO-0001": "integrated"}


def test_the_human_result_names_the_task_and_the_authority_kind(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bind(tasks_repo, capsys)
    work = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    assert run(tasks_repo, work, "--task", "DEMO-0001") == 0
    captured = capsys.readouterr()
    assert 'task="DEMO-0001" authority=record log ' in captured.out.splitlines()[0]
    assert not re.search(r"\b[0-9a-f]{40}\b", captured.out + captured.err)
    assert str(tasks_repo) not in captured.out + captured.err


def test_a_records_only_binding_of_several_tasks_writes_a_line_per_task(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    before = main_tip(tasks_repo)
    tip = on_branch(tasks_repo, "bind", {
        ".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "bind", GRANT),
        ".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "two", GRANT),
    })
    status, data, err = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)
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
    status, data, err = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert status == 3
    assert data["refusal"]["reason"] == "VERIFICATION_FAILED"
    assert "schema-violation: DEMO-0001.yaml" in data["refusal"]["detail"]
    assert "verifier=record check" in err
    assert main_tip(tasks_repo) == before


def test_the_check_of_a_records_only_range_reports_the_record_check_for_reuse(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work")})
    status, data, err = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS, "--check")
    assert status == 0, err
    assert data["verification_reuse"] == "RECORDS_ONLY"


def test_a_records_only_range_needs_no_verification_section(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    put(tasks_repo, {"AGENTS.md": "# AGENTS.md\n\n## Task records\n\n- Task prefix: DEMO\n"})
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work", GRANT)})
    status, _, err = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert status == 0, err


# --task


def test_a_task_the_reviewed_commit_lacks_is_not_found(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    status, data, _ = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0007", "--owner-words", WORDS)
    assert (status, data["refusal"]["reason"]) == (1, "TASK_NOT_FOUND")


def test_a_record_bound_to_another_branch_integrates_that_branch(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    on_branch(tasks_repo, "elsewhere", {"c.txt": "c\n"})
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "elsewhere"), "a.txt": "w\n"})
    status, data, _ = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert (status, data["refusal"]["reason"]) == (1, "REVIEW_REWRITTEN")


def test_a_task_identity_string_is_recorded_with_owner_words(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    status, data, err = run_json(tasks_repo, capsys, tip, "--task", "docs/PLAN.md §2 step 1", "--owner-words", WORDS)
    assert status == 0, err
    assert (lines(tasks_repo)[-1]["task"], lines(tasks_repo)[-1]["authority"]["kind"]) == ("docs/PLAN.md §2 step 1", "owner_words")


def test_a_task_identity_string_needs_owner_words(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    status, data, _ = run_json(tasks_repo, capsys, tip, "--task", "docs/PLAN.md §2 step 1")
    assert (status, data["refusal"]["reason"]) == (1, "AUTHORITY_MISSING")


def test_a_range_beyond_the_records_needs_a_task(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    assert run(tasks_repo, tip, "--owner-words", WORDS) == 2
    assert capsys.readouterr().err == "ERROR USAGE the following arguments are required: --task\n"


# Authority on main


def test_a_record_without_integrate_main_is_no_authority(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work")})
    assert run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)[0] == 0
    work = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    status, data, _ = run_json(tasks_repo, capsys, work, "--task", "DEMO-0001")
    assert (status, data["refusal"]["reason"]) == (1, "AUTHORITY_MISSING")


def test_a_record_grant_covers_ff_only(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bind(tasks_repo, capsys)
    work = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    publish(tasks_repo, "work")
    put(tasks_repo, {"c.txt": "c\n"})
    before = main_tip(tasks_repo)
    status, data, _ = run_json(tasks_repo, capsys, work, "--task", "DEMO-0001")
    assert (status, data["refusal"]["reason"]) == (1, "AUTHORITY_NOT_FF")
    assert run(tasks_repo, work, "--task", "DEMO-0001", "--check") == 1
    assert refusal(capsys.readouterr().err)[0].startswith("STOP AUTHORITY_NOT_FF: ")
    assert main_tip(tasks_repo) == before
    status, data, err = run_json(tasks_repo, capsys, work, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert (status, data["verdict"]) == (0, "NO_FF"), err


def test_a_grant_committed_to_main_directly_is_not_integrated(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    put(tasks_repo, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work", GRANT)})
    work = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    status, data, _ = run_json(tasks_repo, capsys, work, "--task", "DEMO-0001")
    assert (status, data["refusal"]["reason"]) == (1, "AUTHORITY_NOT_INTEGRATED")
    assert "--owner-words" in data["refusal"]["detail"]


def catch_up(repo: Path, branch: str) -> None:
    run_git(repo, "switch", "-q", branch)
    run_git(repo, "merge", "-q", "--ff-only", "main")
    run_git(repo, "switch", "-q", "main")


def test_a_later_direct_change_of_the_grant_voids_it(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bind(tasks_repo, capsys)
    put(tasks_repo, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work", {**GRANT, "deploy": True})})
    catch_up(tasks_repo, "work")
    work = on_branch(tasks_repo, "work", {"b.txt": "more\n"})
    status, data, _ = run_json(tasks_repo, capsys, work, "--task", "DEMO-0001")
    assert (status, data["refusal"]["reason"]) == (1, "AUTHORITY_NOT_INTEGRATED")


def test_a_later_edit_that_keeps_the_grant_leaves_it_in_place(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bind(tasks_repo, capsys)
    put(tasks_repo, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work", GRANT, writer="claude")})
    catch_up(tasks_repo, "work")
    work = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    status, data, err = run_json(tasks_repo, capsys, work, "--task", "DEMO-0001")
    assert (status, data["authority"]["kind"]) == (0, "record"), err


# The record's authority in a range


@pytest.mark.parametrize("change", [
    {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work", {**GRANT, "push_main": True}), "a.txt": "w\n"},
    {".atc/tasks/DEMO-0001.yaml": None, "a.txt": "w\n"},
    {".atc/tasks/DEMO-0001.yaml": None, ".atc/tasks/DEMO-0003.yaml": record("DEMO-0003", "work", GRANT), "a.txt": "w\n"},
], ids=["changed", "removed", "renamed"])
def test_a_mixed_range_never_changes_a_grant_on_main(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str], change: dict,
) -> None:
    bind(tasks_repo, capsys)
    work = on_branch(tasks_repo, "work", change)
    status, data, _ = run_json(tasks_repo, capsys, work, "--task", "plan § 1 step 2", "--owner-words", WORDS)
    assert (status, data["refusal"]["reason"]) == (1, "TASK_AUTHORITY_CHANGED")


def test_a_records_only_change_of_a_grant_needs_owner_words(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bind(tasks_repo, capsys)
    narrowed = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work")})
    status, data, _ = run_json(tasks_repo, capsys, narrowed, "--task", "DEMO-0001")
    assert (status, data["refusal"]["reason"]) == (1, "TASK_AUTHORITY_CHANGED")
    status, data, err = run_json(tasks_repo, capsys, narrowed, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert (status, data["records_only"]) == (0, True), err


@pytest.mark.parametrize(("grants", "expected"), [({"push_topic": True}, 1), ({}, 0)])
def test_a_new_record_in_a_mixed_range_grants_edit_and_commit_at_most(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str], grants: dict, expected: int,
) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work", grants), "a.txt": "w\n"})
    status, data, err = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert status == expected, err
    if expected:
        assert data["refusal"]["reason"] == "TASK_AUTHORITY_CHANGED"


def test_a_number_main_binds_to_another_branch_is_taken(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clash = on_branch(tasks_repo, "mine", {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "mine"), "a.txt": "w\n"})
    theirs = on_branch(tasks_repo, "theirs", {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "theirs")})
    assert run_json(tasks_repo, capsys, theirs, "--task", "DEMO-0002", "--owner-words", WORDS)[0] == 0
    status, data, _ = run_json(tasks_repo, capsys, clash, "--task", "DEMO-0002", "--owner-words", WORDS)
    assert (status, data["refusal"]["reason"]) == (1, "TASK_ID_TAKEN")
    multi = on_branch(tasks_repo, "multi", {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "multi")}, start="main")
    status, data, _ = run_json(tasks_repo, capsys, multi, "--task", "DEMO-0002", "--owner-words", WORDS)
    assert (status, data["refusal"]["reason"]) == (1, "TASK_ID_TAKEN")


# The record names the branch


def test_the_record_names_the_branch_among_several_at_the_reviewed_sha(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work"), "a.txt": "w\n"})
    run_git(tasks_repo, "branch", "copy", tip)
    status, data, err = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert status == 0, err
    assert "branch=work (record)" in err.splitlines()[0]
    assert main_tip(tasks_repo) == tip


def test_a_task_identity_string_stops_on_several_branches(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = on_branch(tasks_repo, "work", {"a.txt": "w\n"})
    run_git(tasks_repo, "branch", "copy", tip)
    status, data, _ = run_json(tasks_repo, capsys, tip, "--task", "plan step 1", "--owner-words", WORDS)
    assert (status, data["refusal"]["reason"]) == (1, "BRANCH_AMBIGUOUS")


def test_a_record_naming_a_missing_branch_stops(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "gone"), "a.txt": "w\n"})
    before = main_tip(tasks_repo)
    status, data, _ = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert (status, data["refusal"]["reason"]) == (1, "NO_BRANCH_AT_REVIEWED")
    assert "names branch gone, which is no local branch" in data["refusal"]["detail"]
    assert main_tip(tasks_repo) == before


@pytest.mark.parametrize(("change", "reason"), [("amend", "REVIEW_REWRITTEN"), ("commit", "UNREVIEWED_COMMITS")])
def test_the_record_branch_past_or_beside_the_reviewed_sha_stops(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str], change: str, reason: str,
) -> None:
    reviewed = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work"), "a.txt": "w\n"})
    run_git(tasks_repo, "branch", "keep", reviewed)  # inference alone would take this branch
    run_git(tasks_repo, "switch", "-q", "work")
    if change == "amend":
        run_git(tasks_repo, "commit", "-q", "--amend", "-m", "reworded")
    else:
        put(tasks_repo, {"b.txt": "more\n"})
    run_git(tasks_repo, "switch", "-q", "main")
    before = main_tip(tasks_repo)
    status, data, _ = run_json(tasks_repo, capsys, reviewed, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert (status, data["refusal"]["reason"]) == (1, reason)
    assert main_tip(tasks_repo) == before


def test_a_records_only_binding_of_several_tasks_takes_the_branch_its_task_record_names(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    tip = on_branch(tasks_repo, "bind", {
        ".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "one"),
        ".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "bind"),
    })
    run_git(tasks_repo, "branch", "copy", tip)
    status, data, err = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0002", "--owner-words", WORDS)
    assert status == 0, err
    assert "branch=bind (record)" in err.splitlines()[0]
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
    status, data, _ = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0002", "--owner-words", WORDS)
    assert (status, data["refusal"]["reason"]) == (1, "NO_BRANCH_AT_REVIEWED")
    expected = "which is not at the reviewed SHA" if elsewhere else "which is no local branch"
    assert expected in data["refusal"]["detail"]
    assert (main_tip(tasks_repo), lines(tasks_repo)) == (before, [])


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


def recover(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tip: str, *flags: str) -> None:
    stop_after_update(repo, monkeypatch)
    assert run_json(repo, capsys, tip, *flags)[0] == 3
    monkeypatch.undo()
    run_git(repo, "checkout", "--", "a.txt")
    assert main_tip(repo) == tip


def test_a_recovery_without_a_grant_on_main_takes_owner_words(
    tasks_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work"), "a.txt": "w\n"})
    recover(tasks_repo, monkeypatch, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)
    status, data, _ = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001")
    assert (status, data["refusal"]["reason"]) == (1, "AUTHORITY_MISSING")
    status, data, err = run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)
    assert (status, data["verdict"], data["authority"]["kind"]) == (0, "RECOVERED", "owner_words"), err
    assert len(lines(tasks_repo)) == 1


def test_a_recovery_under_a_record_checks_its_provenance(
    tasks_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    bind(tasks_repo, capsys)
    work = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    recover(tasks_repo, monkeypatch, capsys, work, "--task", "DEMO-0001")
    status, data, err = run_json(tasks_repo, capsys, work, "--task", "DEMO-0001")
    assert (status, data["verdict"], data["authority"]) == (0, "RECOVERED", {"kind": "record", "task_id": "DEMO-0001"}), err


def test_a_recovery_under_an_unintegrated_grant_is_refused(
    tasks_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    put(tasks_repo, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work", GRANT)})
    work = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    recover(tasks_repo, monkeypatch, capsys, work, "--task", "DEMO-0001", "--owner-words", WORDS)
    status, data, _ = run_json(tasks_repo, capsys, work, "--task", "DEMO-0001")
    assert (status, data["refusal"]["reason"]) == (1, "AUTHORITY_NOT_INTEGRATED")


# The push listing


def test_the_push_listing_names_every_task_of_a_binding(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(tasks_repo, "main")
    tip = on_branch(tasks_repo, "bind", {
        ".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "bind", GRANT),
        ".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "two", GRANT),
    })
    assert run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)[0] == 0
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
    assert gp.main(["push", "--project", "repo"], root=tasks_repo.parent) == 0
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


def test_a_check_needs_no_authority(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    work = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    before = main_tip(tasks_repo)
    assert run(tasks_repo, work, "--task", "x", "--check") == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("CHECK FF ")
    assert captured.out.rstrip().endswith('task="x" authority=missing')
    assert run(tasks_repo, work, "--check") == 2
    assert run(tasks_repo, work, "--task", "x") == 1
    assert main_tip(tasks_repo) == before


def test_a_check_reports_a_missing_grant_and_the_run_still_stops(tasks_repo: Path,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work")})
    assert run_json(tasks_repo, capsys, tip, "--task", "DEMO-0001", "--owner-words", WORDS)[0] == 0
    work = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    assert run(tasks_repo, work, "--task", "DEMO-0001", "--check") == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("CHECK FF ")
    assert captured.out.rstrip().endswith('task="DEMO-0001" authority=missing')
    status, data, _ = run_json(tasks_repo, capsys, work, "--task", "DEMO-0001")
    assert (status, data["refusal"]["reason"]) == (1, "AUTHORITY_MISSING")


def test_a_check_with_owner_words_still_needs_a_task(tasks_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    work = on_branch(tasks_repo, "work", {"a.txt": "work\n"})
    assert run(tasks_repo, work, "--check", "--owner-words", WORDS) == 2
    assert capsys.readouterr().err == "ERROR USAGE the following arguments are required: --task\n"


def test_atc_verify_runs_the_record_check_for_a_records_only_range(
    tasks_repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    put(tasks_repo, {"verify.json": '["no-such-verifier"]'}, "declare a verifier that cannot start")
    broken = record("DEMO-0001", "work").replace("scope: plan § 1\n", "")
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": broken})
    before = main_tip(tasks_repo)
    assert gi.verify_main(["--project", "repo", "--reviewed", tip], root=tasks_repo.parent) == 3
    err = capsys.readouterr().err
    assert "STOP VERIFICATION_FAILED: the record check failed" in err and "schema-violation: DEMO-0001.yaml" in err
    good = on_branch(tasks_repo, "work2", {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "work2")})
    assert gi.verify_main(["--project", "repo", "--reviewed", good], root=tasks_repo.parent) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("VERIFY PASS ")
    assert main_tip(tasks_repo) == before

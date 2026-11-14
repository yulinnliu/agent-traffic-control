"""`atc task close`: an integrated task's worktree and branch, scratches and reports (docs/TASKS.md § Close)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import close_out
import git_preflight
import managed_worktree as hw
import task_records
from atc_testkit import scratch, testrun
from atc_testkit.locations import Refusal
from test_task_records import PROJECT, Workspace, commit, record, run_git, ws  # noqa: F401 - the fixture

pytestmark = pytest.mark.integration
TASK = "DEMO-0001"


@pytest.fixture()
def done(ws: Workspace, atc_tmp: Path, tmp_path: Path) -> tuple[Workspace, Path, dict[str, str]]:
    """The task's topic integrated by fast-forward, a scratch of the task and of another, and three reports."""
    home = tmp_path / "home"
    topic = ws.worktree(ws.repo, "claude+widget", "worktree-claude+widget")
    tip = commit(topic, {f".atc/tasks/{TASK}.yaml": record(TASK, "worktree-claude+widget")})
    run_git(ws.repo, "merge", "-q", "--ff-only", "worktree-claude+widget")
    ws.integrate(ws.repo, task=TASK, candidate_sha=tip, records_only=False)
    reports = home / ".atc" / "reports"
    reports.mkdir(parents=True)
    for name in (f"{TASK}-abc.md", f"{TASK}-abc-review.md", "DEMO-0002-def.md"):
        (reports / name).write_text("r", encoding="utf-8")
    for name, task in (("mine", TASK), ("theirs", "DEMO-0002")):
        (atc_tmp / name / "tree").mkdir(parents=True)
        scratch.register(atc_tmp, atc_tmp / name, atc_tmp / name / "tree", task=task)
    environ = {"CLAUDECODE": "1", "ATC_TMP": str(atc_tmp), "USERPROFILE": str(home), "HOME": str(home)}
    return ws, topic, environ


def close(ws: Workspace, environ: dict[str, str], capsys: pytest.CaptureFixture[str], cwd: Path,
          *argv: str, task: str = TASK) -> tuple[int, dict, str]:
    """Run `atc task close --json`: the exit code, the parsed verdict object and stderr."""
    code = task_records.main(["close", task, "--json", *argv], root=ws.root, environ=environ, cwd=str(cwd))
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if captured.out.strip() else {}), captured.err


def outcomes(payload: dict) -> dict[str, tuple[str, str | None]]:
    """Each item's path mapped to (outcome, why)."""
    return {item["path"]: (item["outcome"], item["why"]) for item in payload["items"]}


def test_an_integrated_task_loses_its_worktree_scratches_and_reports(done, atc_tmp: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    code, payload, err = close(ws, environ, capsys, ws.root)
    assert code == 0, (payload, err)
    reports = Path(environ["HOME"]) / ".atc" / "reports"
    assert (payload["verdict"], payload["counts"]["deleted"]) == ("CLOSED", 4)
    found = outcomes(payload)
    assert found[str(topic)][0] == "DELETED" and not topic.exists()
    assert found[str(atc_tmp / "mine")][0] == "DELETED" and (atc_tmp / "theirs").is_dir()
    assert sorted(path.name for path in reports.iterdir()) == ["DEMO-0002-def.md"]


def test_check_lists_what_the_run_would_do_and_deletes_nothing(done, atc_tmp: Path,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    reports = Path(environ["HOME"]) / ".atc" / "reports"
    ledger = ws.repo / ".git" / "atc" / "integrations.jsonl"
    before = ledger.read_bytes()
    code, payload, err = close(ws, environ, capsys, ws.root, "--check")
    assert code == 0, (payload, err)
    assert (payload["verdict"], payload["check"]) == ("CHECK", True)
    found = outcomes(payload)
    assert found[str(topic)][0] == "DELETE"
    assert found[str(atc_tmp / "mine")][0] == "DELETE"
    assert topic.is_dir() and (atc_tmp / "mine").is_dir() and len(list(reports.iterdir())) == 3
    assert ledger.read_bytes() == before
    code, payload, err = close(ws, environ, capsys, ws.root)
    assert code == 0 and not topic.exists()


def test_check_lists_a_test_run_scratch_of_the_task_whose_tree_is_outside_its_worktree(
        done, atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    # The run registers its scratch before the selection, whose unknown base then stops it.
    with pytest.raises(Refusal, match="SELECTION_BASE_UNKNOWN"):
        testrun.run(["--scratch", "base", "--tree", str(ws.repo), "--python", sys.executable, "--task", TASK,
                     "--related", "0" * 40], environ=dict(environ))
    assert not Path(os.path.realpath(ws.repo)).is_relative_to(os.path.realpath(topic))
    code, payload, err = close(ws, environ, capsys, ws.root, "--check")
    assert code == 0 and outcomes(payload)[str(atc_tmp / "base")][0] == "DELETE", (payload, err)


def test_check_exits_1_when_an_item_would_be_refused(done, capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    commit(topic, {"later.txt": "x\n"})
    code, payload, err = close(ws, environ, capsys, ws.root, "--check")
    assert code == 1 and (payload["verdict"], payload["check"]) == ("REFUSED", True)
    outcome, why = outcomes(payload)[str(topic)]
    assert outcome == "REFUSED" and why.startswith("UNMERGED: ")
    assert payload["counts"]["refused"] == 1 and topic.is_dir()


def test_the_working_directorys_worktree_is_refused(done, capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    code, payload, err = close(ws, environ, capsys, topic)
    assert code == 3 and outcomes(payload)[str(topic)] == (
        "REFUSED", "the working directory is inside it; run it from elsewhere")
    assert topic.is_dir()


def test_keep_exceptions_in_the_record_are_kept(ws: Workspace, atc_tmp: Path, tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.repo, "claude+kept", "worktree-claude+kept")
    tip = commit(topic, {f".atc/tasks/{TASK}.yaml": record(TASK, "worktree-claude+kept", keep=["worktree", "reports"])})
    run_git(ws.repo, "merge", "-q", "--ff-only", "worktree-claude+kept")
    ws.integrate(ws.repo, task=TASK, candidate_sha=tip, records_only=False)
    reports = tmp_path / "home" / ".atc" / "reports"
    reports.mkdir(parents=True)
    (reports / f"{TASK}-x.md").write_text("r", encoding="utf-8")
    environ = {"CLAUDECODE": "1", "ATC_TMP": str(atc_tmp), "USERPROFILE": str(tmp_path / "home"),
               "HOME": str(tmp_path / "home")}
    code, payload, _err = close(ws, environ, capsys, ws.root)
    assert code == 0
    assert outcomes(payload) == {str(topic): ("KEPT", "record keep"),
                                 str(reports / f"{TASK}-x.md"): ("KEPT", "record keep")}
    assert topic.is_dir() and (reports / f"{TASK}-x.md").is_file()


def test_a_task_main_does_not_hold_is_refused_before_anything(ws: Workspace, atc_tmp: Path,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    """Without an integration line on main nothing is deleted: the close stops before any item."""
    topic = ws.worktree(ws.repo, "claude+early", "worktree-claude+early")
    commit(topic, {f".atc/tasks/{TASK}.yaml": record(TASK, "worktree-claude+early")})
    run_git(ws.repo, "merge", "-q", "--ff-only", "worktree-claude+early")
    code, _payload, err = close(ws, {"CLAUDECODE": "1", "ATC_TMP": str(atc_tmp)}, capsys, ws.root)
    assert code == 1 and err.startswith("STOP NOT_INTEGRATED: ")
    assert topic.is_dir()


def test_the_close_removed_flags_are_usage_errors(ws: Workspace, atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        task_records.main(["close", TASK, "--skip", "branch"], root=ws.root, environ={"ATC_TMP": str(atc_tmp)},
                          cwd=str(ws.root))
    assert stop.value.code == 2


def test_a_bad_keep_list_is_refused(ws: Workspace, atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.repo, "claude+bad", "worktree-claude+bad")
    tip = commit(topic, {f".atc/tasks/{TASK}.yaml": record(TASK, "worktree-claude+bad", keep=["branch"])})
    run_git(ws.repo, "merge", "-q", "--ff-only", "worktree-claude+bad")
    ws.integrate(ws.repo, task=TASK, candidate_sha=tip, records_only=False)
    code, _payload, err = close(ws, {"CLAUDECODE": "1", "ATC_TMP": str(atc_tmp)}, capsys, ws.root)
    assert code == 2 and err.startswith("ERROR RECORD_INVALID ")


def test_a_worktree_cleanup_refuses_is_reported(done, capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    commit(topic, {"later.txt": "x\n"})
    code, payload, err = close(ws, environ, capsys, ws.root)
    assert outcomes(payload)[str(topic)][0] == "REFUSED" and topic.is_dir()
    assert payload["verdict"] == "REFUSED"
    assert code == (3 if payload["counts"]["deleted"] else 1)


def test_the_task_prefix_names_its_repository_for_a_root_task(ws: Workspace, atc_tmp: Path, tmp_path: Path,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    rooted = "ATC-0001"
    topic = ws.worktree(ws.root, "claude+rooted", "worktree-claude+rooted")
    tip = commit(topic, {f".atc/tasks/{rooted}.yaml": record(rooted, "worktree-claude+rooted")})
    run_git(ws.root, "merge", "-q", "--ff-only", "worktree-claude+rooted")
    ws.integrate(ws.root, task=rooted, candidate_sha=tip, records_only=False)
    environ = {"CLAUDECODE": "1", "ATC_TMP": str(atc_tmp), "USERPROFILE": str(tmp_path / "home"),
               "HOME": str(tmp_path / "home")}
    code, payload, err = close(ws, environ, capsys, ws.root, task=rooted)
    assert code == 0, err
    assert outcomes(payload)[str(topic)][0] == "DELETED" and not topic.exists()
    code, _payload, err = close(ws, environ, capsys, ws.repo, task="ZZZ-0001")
    assert (code, err.split(" ", 2)[1].rstrip(":")) == (2, "UNKNOWN_TASK")


def test_a_closed_task_on_an_old_style_branch_is_found_from_its_record(ws: Workspace, atc_tmp: Path, tmp_path: Path,
                                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """A worktree created under the earlier `worktree-` naming is found by the record's `branch` and removed."""
    topic = ws.worktree(ws.repo, "claude+cli-redesign", "worktree-claude+cli-redesign")
    tip = commit(topic, {f".atc/tasks/{TASK}.yaml": record(TASK, "worktree-claude+cli-redesign")})
    run_git(ws.repo, "merge", "-q", "--ff-only", "worktree-claude+cli-redesign")
    ws.integrate(ws.repo, task=TASK, candidate_sha=tip, records_only=False)
    environ = {"CLAUDECODE": "1", "ATC_TMP": str(atc_tmp), "USERPROFILE": str(tmp_path / "home"),
               "HOME": str(tmp_path / "home")}
    code, payload, err = close(ws, environ, capsys, ws.root)
    assert code == 0, (payload, err)
    assert outcomes(payload)[str(topic)][0] == "DELETED"
    assert not topic.exists()
    assert run_git(ws.repo, "branch", "--list", "worktree-claude+cli-redesign") == ""


SQUASH_BRANCH = "claude+squashed"
OWNER_SQUASH = {"kind": "owner_words", "quote": "squash ATC topic onto main"}


def squashed(ws: Workspace, tmp_path: Path, atc_tmp: Path, *, on_main: bool = True, tree_of: str = "tip",
             parent: str = "main", **line: object) -> tuple[Path, str, dict[str, str]]:
    """A topic of two commits squashed onto ``main`` by `git commit-tree` and recorded as a squash: the squash
    commit's tree comes from ``tree_of`` (`tip` or `first`); with ``parent`` `moved` its parent is a commit
    `main` gained after the recorded target tip."""
    topic = ws.worktree(ws.repo, SQUASH_BRANCH, SQUASH_BRANCH)
    first = commit(topic, {f".atc/tasks/{TASK}.yaml": record(TASK, SQUASH_BRANCH), "a.txt": "a\n"})
    tip = commit(topic, {"b.txt": "b\n"})
    before = run_git(ws.repo, "rev-parse", "main")
    on = before if parent == "main" else commit(ws.repo, {"moved.txt": "m\n"})
    tree = run_git(ws.repo, "rev-parse", f"{tip if tree_of == 'tip' else first}^{{tree}}")
    squash = run_git(ws.repo, "commit-tree", tree, "-p", on, "-m", f"squash {tip}")
    if on_main:
        run_git(ws.repo, "merge", "-q", "--ff-only", squash)
    entry = {"kind": "squash", "task": TASK, "records_only": False, "candidate_sha": squash, "main_before": before,
             "reviewed": tip, "authority": OWNER_SQUASH, **line}
    ws.integrate(ws.repo, **{key: value for key, value in entry.items() if value is not None})
    home = tmp_path / "home"
    return topic, tip, {"CLAUDECODE": "1", "ATC_TMP": str(atc_tmp), "USERPROFILE": str(home), "HOME": str(home)}


def test_a_recorded_owner_authorized_squash_closes_and_keeps_the_tip_as_archive(
        ws: Workspace, atc_tmp: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    topic, tip, environ = squashed(ws, tmp_path, atc_tmp)
    code, payload, err = close(ws, environ, capsys, ws.root, "--check")
    assert code == 0, (payload, err)
    assert outcomes(payload)[str(topic)][0] == "DELETE" and "squash-integrated as" in outcomes(payload)[str(topic)][1]
    code, payload, err = close(ws, environ, capsys, ws.root)
    assert code == 0, (payload, err)
    assert outcomes(payload)[str(topic)][0] == "DELETED" and not topic.exists()
    assert run_git(ws.repo, "branch", "--list", SQUASH_BRANCH) == ""
    assert run_git(ws.repo, "rev-parse", f"refs/tags/archive/{TASK}") == tip


@pytest.mark.parametrize("shape", [
    {"tree_of": "first"},
    {"parent": "moved"},
    {"authority": {"kind": "record", "task_id": TASK, "quote": "squash it"}},
    {"authority": {"kind": "owner_words", "quote": " "}},
    {"authority": None},
    {"kind": None},
    {"main_before": None},
    {"reviewed": None},
], ids=["wrong-tree", "wrong-first-parent", "not-owner-words", "empty-quote", "no-authority", "not-a-squash",
        "no-target-tip", "no-source-tip"])
def test_a_squash_that_fails_a_check_stays_unmerged(ws: Workspace, atc_tmp: Path, tmp_path: Path, shape: dict,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    topic, _tip, environ = squashed(ws, tmp_path, atc_tmp, **shape)
    code, payload, err = close(ws, environ, capsys, ws.root)
    outcome, why = outcomes(payload)[str(topic)]
    assert (outcome, why.split(":")[0]) == ("REFUSED", "UNMERGED"), (payload, err)
    assert topic.is_dir() and run_git(ws.repo, "tag", "--list", f"archive/{TASK}") == ""


@pytest.mark.parametrize("race", ["tag", "branch"])
def test_a_squash_close_whose_branch_or_archive_tag_moves_after_the_check_refuses_the_branch(
        ws: Workspace, atc_tmp: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: str,
        capsys: pytest.CaptureFixture[str]) -> None:
    topic, tip, environ = squashed(ws, tmp_path, atc_tmp)
    main = run_git(ws.repo, "rev-parse", "main")
    moved = f"refs/tags/archive/{TASK}" if race == "tag" else f"refs/heads/{SQUASH_BRANCH}"
    real_git = hw.git

    def racing(repo, *args, readonly=False, input=None):
        result = real_git(repo, *args, readonly=readonly, input=input)
        if args[:2] == ("worktree", "remove") and result.returncode == 0:
            run_git(ws.repo, "update-ref", moved, main)
        return result

    monkeypatch.setattr(hw, "git", racing)
    code, payload, err = close(ws, environ, capsys, ws.root)
    found = outcomes(payload)
    assert code == 3 and payload["verdict"] == "REFUSED", (payload, err)
    assert found[str(topic)][0] == "DELETED" and not topic.exists()
    assert found[f"refs/heads/{SQUASH_BRANCH}"][0] == "REFUSED"
    assert run_git(ws.repo, "rev-parse", f"refs/heads/{SQUASH_BRANCH}") == (tip if race == "tag" else main)


def test_a_squash_whose_branch_moved_past_the_source_tip_stays_unmerged(
        ws: Workspace, atc_tmp: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    topic, _tip, environ = squashed(ws, tmp_path, atc_tmp)
    commit(topic, {"c.txt": "c\n"})
    code, payload, err = close(ws, environ, capsys, ws.root)
    assert outcomes(payload)[str(topic)][1].startswith("UNMERGED"), (payload, err)
    assert topic.is_dir()


def test_a_squash_commit_off_main_is_not_integrated(ws: Workspace, atc_tmp: Path, tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    topic, _tip, environ = squashed(ws, tmp_path, atc_tmp, on_main=False)
    code, _payload, err = close(ws, environ, capsys, ws.root)
    assert (code, err.split(" ", 2)[1].rstrip(":")) == (1, "NOT_INTEGRATED") and topic.is_dir()


def _link(link: Path, target: Path) -> None:
    """A directory junction on Windows, a symbolic link elsewhere."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("through", ["direct", "link"])
def test_a_scratch_whose_tree_lies_in_the_worktree_is_removed(done, atc_tmp: Path, tmp_path: Path, through: str,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    (topic / "sub").mkdir()
    base = topic
    if through == "link":
        base = tmp_path / "linked"
        _link(base, topic)
    (atc_tmp / "writer").mkdir()
    scratch.register(atc_tmp, atc_tmp / "writer", Path(str(base).upper() if os.name == "nt" else base) / "sub")
    code, payload, err = close(ws, environ, capsys, ws.root)
    assert code == 0, (payload, err)
    assert outcomes(payload)[str(atc_tmp / "writer")][0] == "DELETED" and (atc_tmp / "theirs").is_dir()


def ledger_reports(ws: Workspace) -> list[dict]:
    path = ws.repo / ".git" / "atc" / "integrations.jsonl"
    return [entry for entry in map(json.loads, path.read_text(encoding="utf-8").splitlines())
            if entry.get("kind") == "report"]


def test_each_deleted_report_but_a_writer_report_leaves_a_summary_line(
        done, capsys: pytest.CaptureFixture[str]) -> None:
    ws, _topic, environ = done
    reports = Path(environ["HOME"]) / ".atc" / "reports"
    writer = reports / f"{TASK}-1a2b3c4.md"
    writer.write_text("step report, tip 1a2b3c4\n", encoding="utf-8")
    (reports / f"{TASK}-1a2b3c4-review.md").write_text("\n# CONDITIONAL PASS at 1a2b3c4\n", encoding="utf-8")
    code, payload, err = close(ws, environ, capsys, ws.root, "--check")
    assert code == 0 and outcomes(payload)[str(writer)] == ("DELETE", "writer report, no ledger line"), (payload, err)
    code, payload, err = close(ws, environ, capsys, ws.root)
    assert code == 0, err
    assert outcomes(payload)[str(writer)][0] == "DELETED" and not writer.exists()
    rows = {entry["file"]: entry for entry in ledger_reports(ws)}
    assert set(rows) == {f"{TASK}-abc.md", f"{TASK}-abc-review.md", f"{TASK}-1a2b3c4-review.md"}
    assert (rows[f"{TASK}-1a2b3c4-review.md"]["role"], rows[f"{TASK}-1a2b3c4-review.md"]["sha"],
            rows[f"{TASK}-1a2b3c4-review.md"]["verdict"]) == ("reviewer", "1a2b3c4", "CONDITIONAL PASS")
    assert (rows[f"{TASK}-abc.md"]["role"], rows[f"{TASK}-abc.md"]["sha"]) == ("unknown", None)
    assert all(entry["task"] == TASK and "candidate_sha" not in entry for entry in rows.values())
    assert [entry["task"] for entry in git_preflight.read_integrations(ws.repo)] == [TASK]


def test_a_report_the_ledger_cannot_take_is_kept(done, capsys: pytest.CaptureFixture[str],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    ws, _topic, environ = done

    def refuse(*_args: object) -> None:
        raise OSError("read-only")
    monkeypatch.setattr(close_out, "record_report", refuse)
    code, payload, _err = close(ws, environ, capsys, ws.root)
    reports = Path(environ["HOME"]) / ".atc" / "reports"
    assert code == 3 and outcomes(payload)[str(reports / f"{TASK}-abc.md")] == ("REFUSED", "read-only")
    assert (reports / f"{TASK}-abc.md").exists()


@pytest.mark.parametrize(("line", "verdict"), [
    ("# ATC-0026 review: PASS at 1a2b3c4", "PASS"), ("CONDITIONAL PASS: rebase first", "CONDITIONAL PASS"),
    ("FAIL - two findings", "FAIL"), ("PASSED every test", None), ("NO PASS yet; FAIL", "FAIL"), ("NOT PASS", None),
    ("", None), ("## Verdict", None),
    ("CONDITIONAL-PASS", "CONDITIONAL PASS"), ("CONDITIONAL_PASS at abc1234", "CONDITIONAL PASS"),
    ("Conditional PASS", "CONDITIONAL PASS"), ("conditional pass: rebase first", "CONDITIONAL PASS"),
    ("pass", "PASS"), ("Fail - one finding", "FAIL"), ("**CONDITIONAL PASS**", "CONDITIONAL PASS"),
    ("PASS (conditional)", "CONDITIONAL PASS"), ("PASS, conditional on a rebase", "CONDITIONAL PASS"),
    ("PASS, not conditional", "PASS"), ("PASS; FAIL if conditional", "PASS"),
    ("NOT CONDITIONAL PASS; FAIL", "FAIL"), ("Not a PASS", None), ("not yet PASS; FAIL", "FAIL"),
    ("NO findings, PASS", "PASS"), ("pass-through review: FAIL", "FAIL")])
def test_the_verdict_is_a_whole_word_of_the_first_line(line: str, verdict: str | None) -> None:
    assert close_out.verdict_of(line) == verdict


def test_a_scratch_registered_to_one_task_refuses_another(atc_tmp: Path, tmp_path: Path) -> None:
    scratch.register(atc_tmp, atc_tmp / "s", tmp_path)
    scratch.register(atc_tmp, atc_tmp / "s", tmp_path, task=TASK)
    scratch.register(atc_tmp, atc_tmp / "s", tmp_path, task=TASK)
    with pytest.raises(Refusal) as refused:
        scratch.register(atc_tmp, atc_tmp / "s", tmp_path, task="DEMO-0009")
    assert refused.value.token == "SCRATCH_TASK_MISMATCH" and refused.value.code == 9
    [row] = scratch.registered(atc_tmp)
    assert row.task == TASK


def test_check_lists_unregistered_scratches_named_for_the_task_for_consent_and_keeps_them(
        done, atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    for name in (f"{TASK.lower()}-review", TASK, f"{TASK}0", "DEMO-0002-review"):
        (atc_tmp / name / "bt").mkdir(parents=True)
    (atc_tmp / f"{TASK}-file").write_text("x", encoding="utf-8")
    code, payload, err = close(ws, environ, capsys, ws.root, "--check")
    consent = sorted(item["path"] for item in payload["items"] if item["outcome"] == "CONSENT")
    assert code == 0, (payload, err)
    assert consent == sorted(str(atc_tmp / name) for name in (TASK, f"{TASK.lower()}-review"))
    assert outcomes(payload)[str(atc_tmp / "mine")][0] == "DELETE"
    assert payload["counts"]["consent"] == 2
    code, payload, err = close(ws, environ, capsys, ws.root)
    assert code == 0 and payload["counts"]["consent"] == 2, (payload, err)
    assert (atc_tmp / f"{TASK.lower()}-review").is_dir() and (atc_tmp / TASK).is_dir()
    assert not (atc_tmp / "mine").exists()


def test_the_consent_list_leaves_out_the_scratches_close_out_takes(atc_tmp: Path) -> None:
    for name in (f"{TASK}-mine", f"{TASK}-other"):
        (atc_tmp / name).mkdir()
    assert close_out.unregistered_scratches(atc_tmp, TASK, [str(atc_tmp / f"{TASK}-mine")]) == [
        atc_tmp / f"{TASK}-other"]

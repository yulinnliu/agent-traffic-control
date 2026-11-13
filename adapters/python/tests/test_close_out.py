"""`atc close-out --task`: an integrated task's worktree, scratches and reports (docs/TASKS.md § Close-out)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import close_out
import git_preflight
import managed_worktree as hw
from atc_testkit import scratch
from test_task_records import PROJECT, Workspace, commit, record, run_git, ws  # noqa: F401 - the fixture

pytestmark = pytest.mark.integration
TASK = "DEMO-0001"


@pytest.fixture()
def done(ws: Workspace, atc_tmp: Path, tmp_path: Path) -> tuple[Workspace, Path, dict[str, str]]:
    """The task's topic integrated by fast-forward, a scratch of the task and of another, and three reports."""
    home = tmp_path / "home"
    assert hw.main(["create", "--project", PROJECT, "--owner", "claude", "--topic", "widget"], root=ws.root,
                   environ={}, home=str(home), cwd=str(ws.root)) == 0
    topic = ws.root / "worktrees" / PROJECT / "claude+widget"
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
    environ = {"ATC_TMP": str(atc_tmp), "USERPROFILE": str(home), "HOME": str(home)}
    return ws, topic, environ


def close(ws: Workspace, environ: dict[str, str], capsys: pytest.CaptureFixture[str], cwd: Path,
          *argv: str) -> tuple[int, list[str], str]:
    code = close_out.main(["--task", TASK, "--project", PROJECT, *argv], root=ws.root, environ=environ, cwd=str(cwd))
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def test_an_integrated_task_loses_its_worktree_scratches_and_reports(done, atc_tmp: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    code, lines, err = close(ws, environ, capsys, ws.root)
    assert code == 0, (lines, err)
    reports = Path(environ["HOME"]) / ".atc" / "reports"
    assert lines[0] == f"DELETED {topic}" and not topic.exists()
    assert f"DELETED {atc_tmp / 'mine'}" in lines and (atc_tmp / "theirs").is_dir()
    assert sorted(path.name for path in reports.iterdir()) == ["DEMO-0002-def.md"]
    assert lines[-1] == f"close-out {TASK}: 4 deleted, 0 skipped, 0 refused"


def test_check_lists_what_the_run_would_do_and_deletes_nothing(done, atc_tmp: Path,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    reports = Path(environ["HOME"]) / ".atc" / "reports"
    ledger = ws.repo / ".git" / "atc" / "integrations.jsonl"
    before = ledger.read_bytes()
    code, lines, err = close(ws, environ, capsys, ws.root, "--check", "--skip", "scratches")
    assert code == 0, (lines, err)
    assert lines == [f"DELETED {topic} (removable)", f"SKIPPED {atc_tmp / 'mine'} (--skip)",
                     f"DELETED {reports / f'{TASK}-abc-review.md'} (ledger line: role unknown, verdict null)",
                     f"DELETED {reports / f'{TASK}-abc.md'} (ledger line: role unknown, verdict null)",
                     f"close-out {TASK} --check: 3 to delete, 1 skipped, 0 refused; nothing deleted"]
    assert topic.is_dir() and (atc_tmp / "mine").is_dir() and len(list(reports.iterdir())) == 3
    assert ledger.read_bytes() == before
    code, lines, err = close(ws, environ, capsys, ws.root, "--check")
    assert code == 0 and f"DELETED {atc_tmp / 'mine'} (registered scratch)" in lines, (lines, err)
    assert len(scratch.registered(atc_tmp)) == 2
    assert close(ws, environ, capsys, ws.root)[0] == 0 and not topic.exists()


def test_check_exits_1_when_an_item_would_be_refused(done, capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    commit(topic, {"later.txt": "x\n"})
    code, lines, err = close(ws, environ, capsys, ws.root, "--check", "--json")
    payload = json.loads("\n".join(lines))
    assert code == 1 and (payload["verdict"], payload["check"]) == ("REFUSED", True)
    assert payload["items"][0] == {"outcome": "REFUSED", "path": str(topic), "why": "not removable: unmerged"}
    assert payload["counts"]["refused"] == 1 and topic.is_dir()
    assert err.startswith("STOP ITEM_REFUSED: 1 would be refused")


def test_check_rehearses_a_leftover_directory(done, capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    run_git(ws.repo, "worktree", "unlock", str(topic))
    run_git(ws.repo, "worktree", "remove", "--force", str(topic))
    topic.mkdir()
    (topic / "stray.txt").write_text("x", encoding="utf-8")
    argv = ("--check", "--skip", "scratches", "--skip", "reports")
    code, lines, _err = close(ws, environ, capsys, ws.root, *argv)
    assert (code, lines[0]) == (1, f"REFUSED {topic} (directory not empty)")
    (topic / "stray.txt").unlink()
    code, lines, _err = close(ws, environ, capsys, ws.root, *argv)
    assert (code, lines[0]) == (0, f"DELETED {topic} (empty leftover directory)") and topic.is_dir()


def test_check_refuses_a_worktree_the_audit_names_no_result_for(done, capsys: pytest.CaptureFixture[str],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    ws, topic, environ = done

    def audit(*_args: object, **_kwargs: object) -> int:
        print(json.dumps({"results": [{"reason": "OK", "target": str(topic.parent / "other"), "detail": "removable",
                                       "evidence": {"removable": True}}]}))
        return 0
    monkeypatch.setattr(close_out.hw, "main", audit)
    code, lines, _err = close(ws, environ, capsys, ws.root, "--check", "--skip", "scratches", "--skip", "reports")
    assert (code, lines[0]) == (1, f"REFUSED {topic} (worktree audit names no result for it)")


def test_the_working_directorys_worktree_is_skipped(done, capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    code, lines, _err = close(ws, environ, capsys, topic)
    assert code == 0 and lines[0] == f"SKIPPED {topic} (working directory)"
    assert topic.is_dir()


def test_keep_exceptions_in_the_record_are_skipped(ws: Workspace, atc_tmp: Path, tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.repo, "claude+kept", "worktree-claude+kept")
    tip = commit(topic, {f".atc/tasks/{TASK}.yaml": record(TASK, "worktree-claude+kept", keep=["worktree", "reports"])})
    run_git(ws.repo, "merge", "-q", "--ff-only", "worktree-claude+kept")
    ws.integrate(ws.repo, task=TASK, candidate_sha=tip, records_only=False)
    reports = tmp_path / "home" / ".atc" / "reports"
    reports.mkdir(parents=True)
    (reports / f"{TASK}-x.md").write_text("r", encoding="utf-8")
    environ = {"ATC_TMP": str(atc_tmp), "USERPROFILE": str(tmp_path / "home"), "HOME": str(tmp_path / "home")}
    code, lines, _err = close(ws, environ, capsys, ws.root, "--skip", "worktree")
    assert code == 0
    assert lines == [f"SKIPPED {topic} (keep)", f"SKIPPED {reports / f'{TASK}-x.md'} (keep)",
                     f"close-out {TASK}: 0 deleted, 2 skipped, 0 refused"]


def test_skip_leaves_its_items_for_this_run_only_and_writes_nothing(done, atc_tmp: Path,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    recorded = run_git(ws.repo, "show", f"main:.atc/tasks/{TASK}.yaml")
    code, lines, err = close(ws, environ, capsys, ws.root, "--skip", "worktree", "--skip", "reports")
    assert code == 0, (lines, err)
    reports = Path(environ["HOME"]) / ".atc" / "reports"
    assert lines[0] == f"SKIPPED {topic} (--skip)" and topic.is_dir()
    assert f"DELETED {atc_tmp / 'mine'}" in lines
    assert [line for line in lines if "reports" in line] == [
        f"SKIPPED {reports / name} (--skip)" for name in (f"{TASK}-abc-review.md", f"{TASK}-abc.md")]
    assert lines[-1] == f"close-out {TASK}: 1 deleted, 3 skipped, 0 refused"
    assert run_git(ws.repo, "show", f"main:.atc/tasks/{TASK}.yaml") == recorded
    code, lines, err = close(ws, environ, capsys, ws.root)
    assert code == 0 and lines[0] == f"DELETED {topic}", (lines, err)
    code, lines, err = close(ws, environ, capsys, ws.root)
    assert (code, lines) == (0, [f"close-out {TASK}: 0 deleted, 0 skipped, 0 refused"]), err


def test_a_leftover_directory_of_the_unregistered_worktree_is_removed(done, capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    run_git(ws.repo, "worktree", "unlock", str(topic))
    run_git(ws.repo, "worktree", "remove", "--force", str(topic))
    run_git(ws.repo, "branch", "-D", "worktree-claude+widget")
    topic.mkdir()
    code, lines, err = close(ws, environ, capsys, ws.root, "--skip", "scratches", "--skip", "reports")
    assert (code, lines[0]) == (0, f"DELETED {topic}"), (lines, err)
    assert not topic.exists()


def test_skip_names_only_the_close_out_items(done, capsys: pytest.CaptureFixture[str]) -> None:
    ws, _topic, environ = done
    with pytest.raises(SystemExit) as stop:
        close(ws, environ, capsys, ws.root, "--skip", "branch")
    assert stop.value.code == 2


@pytest.mark.parametrize("case", ["no line", "records only", "not on main"])
def test_a_task_main_does_not_hold_is_refused_before_anything(ws: Workspace, atc_tmp: Path, case: str,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.repo, "claude+early", "worktree-claude+early")
    tip = commit(topic, {f".atc/tasks/{TASK}.yaml": record(TASK, "worktree-claude+early")})
    if case != "not on main":
        run_git(ws.repo, "merge", "-q", "--ff-only", "worktree-claude+early")
    if case != "no line":
        ws.integrate(ws.repo, task=TASK, candidate_sha=tip, records_only=case == "records only")
    code, lines, err = close(ws, {"ATC_TMP": str(atc_tmp)}, capsys, ws.root)
    assert code == 1 and lines == [] and err.startswith("STOP NOT_INTEGRATED: ")
    assert topic.is_dir()


def test_a_bad_keep_list_is_refused(ws: Workspace, atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.repo, "claude+bad", "worktree-claude+bad")
    tip = commit(topic, {f".atc/tasks/{TASK}.yaml": record(TASK, "worktree-claude+bad", keep=["branch"])})
    run_git(ws.repo, "merge", "-q", "--ff-only", "worktree-claude+bad")
    ws.integrate(ws.repo, task=TASK, candidate_sha=tip, records_only=False)
    code, _lines, err = close(ws, {"ATC_TMP": str(atc_tmp)}, capsys, ws.root)
    assert code == 2 and err.startswith("ERROR RECORD_INVALID ")


def test_a_worktree_cleanup_refuses_is_reported(done, capsys: pytest.CaptureFixture[str]) -> None:
    ws, topic, environ = done
    commit(topic, {"later.txt": "x\n"})
    code, lines, err = close(ws, environ, capsys, ws.root)
    assert lines[0].startswith(f"REFUSED {topic} (") and topic.is_dir()
    assert lines[-1].endswith(", 1 refused")
    assert code == (3 if any(line.startswith("DELETED ") for line in lines) else 1)
    assert err.splitlines()[0].startswith("STOP ITEM_REFUSED: 1 refused, ")


def test_without_a_target_the_task_prefix_names_its_repository(ws: Workspace, atc_tmp: Path, tmp_path: Path,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    rooted = "ATC-0001"
    topic = ws.worktree(ws.root, "claude+rooted", "worktree-claude+rooted")
    tip = commit(topic, {f".atc/tasks/{rooted}.yaml": record(rooted, "worktree-claude+rooted")})
    run_git(ws.root, "merge", "-q", "--ff-only", "worktree-claude+rooted")
    ws.integrate(ws.root, task=rooted, candidate_sha=tip, records_only=False)
    environ = {"ATC_TMP": str(atc_tmp), "USERPROFILE": str(tmp_path / "home"), "HOME": str(tmp_path / "home")}
    code = close_out.main(["--task", rooted, "--skip", "worktree"], root=ws.root, environ=environ, cwd=str(ws.repo))
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out.splitlines() == [f"SKIPPED {topic} (--skip)",
                                         f"close-out {rooted}: 0 deleted, 1 skipped, 0 refused"]
    for cwd, token, expected in ((ws.repo, "NOT_INTEGRATED", 1), (ws.root, "UNKNOWN_PROJECT", 2)):
        code = close_out.main(["--task", "ZZZ-0001"], root=ws.root, environ=environ, cwd=str(cwd))
        assert (code, capsys.readouterr().err.split(" ", 2)[1].rstrip(":")) == (expected, token)


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
    code, lines, err = close(ws, environ, capsys, ws.root)
    assert code == 0, (lines, err)
    assert f"DELETED {atc_tmp / 'writer'}" in lines and (atc_tmp / "theirs").is_dir()


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
    code, lines, err = close(ws, environ, capsys, ws.root, "--check")
    assert code == 0 and f"DELETED {writer} (writer report, no ledger line)" in lines, (lines, err)
    code, lines, err = close(ws, environ, capsys, ws.root)
    assert code == 0, err
    assert f"DELETED {writer}" in lines and not writer.exists()
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
    code, lines, _err = close(ws, environ, capsys, ws.root)
    reports = Path(environ["HOME"]) / ".atc" / "reports"
    assert code == 3 and f"REFUSED {reports / f'{TASK}-abc.md'} (ledger: read-only)" in lines
    assert (reports / f"{TASK}-abc.md").exists()


def test_kept_reports_write_no_summary(done, capsys: pytest.CaptureFixture[str]) -> None:
    ws, _topic, environ = done
    assert close(ws, environ, capsys, ws.root, "--skip", "reports")[0] == 0
    assert ledger_reports(ws) == []


@pytest.mark.parametrize(("line", "verdict"), [
    ("# ATC-0026 review: PASS at 1a2b3c4", "PASS"), ("CONDITIONAL PASS: rebase first", "CONDITIONAL PASS"),
    ("FAIL - two findings", "FAIL"), ("PASSED every test", None), ("NO PASS yet; FAIL", "FAIL"), ("NOT PASS", None),
    ("", None), ("## Verdict", None)])
def test_the_verdict_is_a_whole_word_of_the_first_line(line: str, verdict: str | None) -> None:
    assert close_out.verdict_of(line) == verdict


def _repo_on(tmp_path: Path, branch: str, records: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", branch)
    commit(repo, {f".atc/tasks/{name}.yaml": text for name, text in records.items()} or {"a.txt": "a"})
    (repo / "adapters").mkdir()
    return repo


@pytest.mark.parametrize(("branch", "records", "expected"), [
    ("worktree-claude+widget", {TASK: record(TASK, "worktree-claude+widget")}, TASK),
    ("worktree-claude+widget", {TASK: record(TASK, "worktree-claude+other")}, ""),
    ("worktree-claude+widget", {TASK: record(TASK, "worktree-claude+widget"),
                                "DEMO-0002": record("DEMO-0002", "worktree-claude+widget")}, ""),
    ("topic", {TASK: record(TASK, "topic")}, "")])
def test_a_test_run_scratch_records_the_task_of_its_managed_worktree(tmp_path: Path, atc_tmp: Path, branch: str,
                                                                     records: dict[str, str], expected: str) -> None:
    repo = _repo_on(tmp_path, branch, records)
    assert scratch.task_of_tree(repo / "adapters") == expected
    scratch.register(atc_tmp, atc_tmp / "s", repo / "adapters")
    scratch.register(atc_tmp, atc_tmp / "s", repo / "adapters", task=scratch.task_of_tree(repo / "adapters"))
    scratch.register(atc_tmp, atc_tmp / "s", repo / "adapters", task="DEMO-0009")
    [row] = scratch.registered(atc_tmp)
    assert row.task == (expected or "DEMO-0009")

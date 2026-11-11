"""`atc close-out --task`: an integrated task's worktree, scratches and reports (docs/TASKS.md § Close-out)."""
from __future__ import annotations

from pathlib import Path

import pytest

import close_out
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

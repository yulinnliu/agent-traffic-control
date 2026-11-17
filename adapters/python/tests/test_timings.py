"""docs/TESTKIT.md § Stage times: the fields of a stage-time record and `atc test --timings`."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import atc_testkit
import timings_report
from atc_testkit import elapsed


def git(root: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=root, env=env,
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.mark.unit
def test_counts_come_from_the_last_pytest_summary_line() -> None:
    text = "1 passed earlier\nnoise\n== 3 passed, 1 skipped, 2 errors, 1 failed, 4 deselected in 1.0s ==\nafter\n"
    assert elapsed.counts(text) == {"passed": 3, "failed": 3, "skipped": 1}
    assert elapsed.counts("FAIL 1 failed, 2 passed; log: x") == {"passed": 2, "failed": 1, "skipped": 0}
    assert elapsed.counts("no summary here") == {"passed": None, "failed": None, "skipped": None}


@pytest.mark.unit
def test_a_stage_logs_counts_are_read_from_its_tail(tmp_path: Path) -> None:
    log = tmp_path / "parallel.log"
    log.write_text("1 passed in a header\n" + "x\n" * elapsed.SUMMARY_LINES + "== 5 passed in 2s ==\n",
                   encoding="utf-8")
    assert elapsed.log_counts(log) == {"passed": 5, "failed": 0, "skipped": 0}
    assert elapsed.log_counts(tmp_path / "absent.log")["passed"] is None
    assert elapsed.log_counts(None)["passed"] is None


@pytest.mark.unit
def test_a_full_selection_names_its_first_widening_reason_and_how_many_more() -> None:
    reasons = ["a.py: python", "b.toml: no selection rule -> full suite", "c.lock: lock -> full suite"]
    assert elapsed.selection_reason(elapsed.FULL, reasons, 3) == "b.toml: no selection rule -> full suite (+1 more)"
    assert elapsed.selection_reason(elapsed.FULL, reasons[:2], 2) == "b.toml: no selection rule -> full suite"
    assert elapsed.selection_reason(elapsed.FULL, reasons[:1], 1) == "1 changed paths"
    assert elapsed.selection_reason(elapsed.RELATED, reasons, 3) == "3 changed paths"
    assert elapsed.selection_reason(elapsed.NONE, (), None) == ""
    assert [elapsed.kind_of(paths) for paths in ([], ["tests"], None)] == [elapsed.FULL, elapsed.RELATED, elapsed.NONE]


@pytest.mark.unit
def test_several_tasks_are_one_value_once_each() -> None:
    assert elapsed.tasks_value(["ATC-0001", "ATC-0002", "ATC-0001", ""]) == "ATC-0001,ATC-0002"
    assert elapsed.tasks_value([]) == ""


@pytest.mark.integration
def test_the_project_is_the_input_else_the_main_checkout_of_the_git_directory(tmp_path: Path) -> None:
    main = tmp_path / "hami-demo"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "commit", "-q", "--allow-empty", "-m", "base")
    git(main, "worktree", "add", "-q", "--detach", str(tmp_path / "candidate"))
    assert elapsed.project_of({}, tmp_path / "candidate") == "hami-demo"
    assert elapsed.project_of({elapsed.PROJECT_VARIABLE: "named"}, tmp_path / "candidate") == "named"
    assert elapsed.project_of({}, tmp_path) == "" and elapsed.project_of({}, None) == ""


@pytest.mark.unit
def test_a_record_carries_the_runs_fields_and_the_stages_notes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = {elapsed.PROJECT_VARIABLE: "demo", elapsed.VERSION_VARIABLE: "0.9.0"}
    clock = elapsed.Clock.create("ATC-0001", "a" * 40, "verify", tmp_path, environ)
    clock.select(elapsed.FULL, "b.toml: no selection rule -> full suite")
    clock.note("parallel", reused_files=3, run_files=1)
    clock.stage("parallel", 2.0, 4, "PASS", summary="== 7 passed, 1 skipped in 2s ==")
    clock.stage("serial", 1.0, 1, "PASS", log=tmp_path / "absent.log")
    assert clock.reused(show=False) == "ELAPSED reused 0.0s workers 0 REUSED"
    assert capsys.readouterr().out.splitlines() == ["ELAPSED parallel 2.0s workers 4 PASS",
                                                    "ELAPSED serial 1.0s workers 1 PASS"]
    parallel, serial, reused = elapsed.records(tmp_path)
    assert {key: parallel[key] for key in ("project", "atc_version", "testkit_version", "selection",
                                           "selection_reason", "passed", "failed", "skipped", "reused_files",
                                           "run_files")} == {
        "project": "demo", "atc_version": "0.9.0", "testkit_version": atc_testkit.__version__, "selection": "full",
        "selection_reason": "b.toml: no selection rule -> full suite", "passed": 7, "failed": 0, "skipped": 1,
        "reused_files": 3, "run_files": 1}
    assert (serial["passed"], serial["reused_files"]) == (None, None)
    assert (reused["stage"], reused["result"], reused["seconds"], reused["passed"]) == ("reused", "REUSED", 0.0, None)


def record(**fields) -> dict:
    return {"time": "2026-11-17T00:00:00Z", "task": "ATC-0001", "sha": "a", "command": "test", "stage": "related",
            "seconds": 10.0, "workers": 1, "result": "PASS", **fields}


NEW = {"project": "root", "atc_version": "0.9.0", "testkit_version": "0.16.0"}


@pytest.mark.unit
def test_the_report_groups_by_project_and_versions_and_names_old_records_by_their_task() -> None:
    records = [
        record(task="HQT-0001", seconds=4.0),
        record(**NEW, sha="b", passed=8, failed=1, skipped=1, selection="related"),
        record(**NEW, sha="c", command="verify", stage="checks", seconds=3.0),
        record(**NEW, sha="c", command="verify", stage="parallel", seconds=30.0, passed=20, failed=0, skipped=0,
               selection="full", reused_files=3, run_files=1),
        record(**NEW, sha="c", command="verify", stage="serial", seconds=10.0, passed=None, failed=None, skipped=None,
               selection="full", reused_files=1, run_files=3),
        record(**NEW, sha="d", command="prepush", stage="reused", seconds=0.0, result="REUSED"),
        record(task="ZZ-0001", time="2026-11-16T00:00:00Z"),
    ]
    rows = timings_report.summarize(records, lambda task: {"HQT": "hami-quant-trading"}.get(task.split("-")[0]))
    unknown, hqt, root = rows
    assert (hqt["project"], hqt["atc"], hqt["testkit"], hqt["runs"], hqt["tests"]) == (
        "hami-quant-trading", "-", "-", 1, 0)
    assert root["project"] == "root" and (root["atc"], root["testkit"]) == ("0.9.0", "0.16.0")
    assert (root["runs"], root["tests"], root["test_seconds"]) == (3, 30, 50.0)
    assert root["seconds_per_test"] == round(40.0 / 30, 3)
    assert (root["full_runs"], root["full_seconds"], root["reuse"], root["reused_runs"]) == (1, 40.0, 0.5, 1)
    assert [command["command"] for command in root["commands"]] == ["prepush", "test", "verify"]
    assert unknown["project"] == "-"
    assert timings_report.summarize(records, {}.get, since="2026-11-17")[-1]["project"] == "root"
    assert [row["project"] for row in timings_report.summarize(records, {}.get, project="root")] == ["root"]


@pytest.mark.unit
def test_the_report_prints_a_table_or_one_json_object(capsys: pytest.CaptureFixture[str]) -> None:
    records = [record(**NEW, passed=4, failed=0, skipped=0, selection="full")]
    assert timings_report.report(records, {}.get) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == list(timings_report.COLUMNS)
    assert lines[1].split() == ["root", "0.9.0", "0.16.0", "1", "4", "10.0", "2.5", "1", "10", "-", "0"]
    assert lines[2].split()[0] == "test"
    assert timings_report.report(records, {}.get, as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["groups"][0]["seconds_per_test"] == 2.5
    assert timings_report.report([], {}.get) == 0
    assert capsys.readouterr().out == "no stage-time records\n"

"""docs/TESTKIT.md § Time slack: one knob that passes the isolation, is checked before any test and is recorded."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from atc_testkit import ci, gate, isolation, stages, testrun, timing, verifier
from atc_testkit.locations import Refusal
from atc_testkit.project import Project


@pytest.mark.unit
@pytest.mark.parametrize(("raw", "seconds"), [("0", 0.0), ("5", 5.0), ("2.5", 2.5), ("30", 30.0)])
def test_a_number_of_seconds_from_zero_to_thirty_is_the_slack(raw: str, seconds: float) -> None:
    assert timing.resolve({timing.SLACK_VARIABLE: raw}) == timing.Slack(seconds, timing.SLACK_VARIABLE)


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["-1", "30.0001", "nan", "inf", "-inf", "abc", " "])
def test_any_other_setting_is_refused(raw: str) -> None:
    with pytest.raises(Refusal) as refused:
        timing.resolve({timing.SLACK_VARIABLE: raw})
    assert (refused.value.token, refused.value.code) == ("TIME_SLACK_INVALID", 9)


@pytest.mark.unit
def test_unset_or_empty_is_the_default() -> None:
    assert timing.resolve({}) == timing.resolve({timing.SLACK_VARIABLE: ""}) == timing.Slack(2.0, "default")
    assert timing.slack({}) == 2.0 and timing.Slack(5.0, timing.SLACK_VARIABLE).line() == \
        "time slack: 5 (ATC_TEST_TIME_SLACK)"


@pytest.mark.unit
def test_the_slack_passes_the_isolation(tmp_path: Path) -> None:
    env = isolation.IsolatedRun(tmp_path, environ={timing.SLACK_VARIABLE: "5", "HAMI_TEST_TIME_SLACK": "5"}
                                ).environment("parallel")
    assert env[timing.SLACK_VARIABLE] == "5" and "HAMI_TEST_TIME_SLACK" not in env


@pytest.mark.unit
def test_the_log_header_and_the_parallel_stage_show_the_slack(tmp_path: Path,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    header = stages.log_header(["x"], tmp_path, "private", {timing.SLACK_VARIABLE: "5"}).splitlines()
    assert header[5] == "time slack: 5 (ATC_TEST_TIME_SLACK)"
    stages.run_stages(["r"], [], cwd=tmp_path, stage_list=stages.stages(),
                      env_for=lambda stage: {timing.SLACK_VARIABLE: "7"}, options_for=lambda stage: [],
                      log_for=lambda stage: tmp_path / f"{stage.name}.log", workers=1, run=lambda *a, **k: 0)
    assert "time slack: 7 (ATC_TEST_TIME_SLACK)" in capsys.readouterr().out


def refused_before_any_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert capsys.readouterr().err.startswith("TIME_SLACK_INVALID ")


@pytest.mark.unit
def test_the_gate_refuses_a_bad_slack_before_it_selects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(timing.SLACK_VARIABLE, "99")
    project = Project(root=tmp_path, argv_file=tmp_path / "argv.json", select=lambda *a: pytest.fail("selected"))
    assert gate.main(project, ["--run"], stdin=[], run=lambda *a, **k: pytest.fail("ran")) == 9
    refused_before_any_run(capsys)


@pytest.mark.unit
def test_the_verifier_refuses_a_bad_slack_before_its_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    project = Project(root=tmp_path, argv_file=tmp_path / "argv.json")
    monkeypatch.setattr(verifier, "require_env", lambda environ: pytest.fail("checked"))
    assert verifier.main(project, {timing.SLACK_VARIABLE: "-2"}, run=lambda *a, **k: pytest.fail("ran")) == 9
    assert capsys.readouterr().err.startswith("TIME_SLACK_INVALID\n")


@pytest.mark.unit
def test_the_ci_runner_refuses_a_bad_slack(monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(timing.SLACK_VARIABLE, "x")
    monkeypatch.setattr(ci, "run_pytest", lambda *a, **k: pytest.fail("ran"))
    for arguments in (["--stages"], ["--slow"], ["tests"]):
        assert ci.main(arguments) == 9
        refused_before_any_run(capsys)


@pytest.mark.unit
def test_atc_test_run_refuses_a_bad_slack(tmp_path: Path, atc_tmp: Path) -> None:
    done = subprocess.run([sys.executable, "-m", "atc_testkit.commands", "test", "run", "--scratch", "s",
                           "--tree", str(tmp_path), "--python", sys.executable, "--", "tests"],
                          capture_output=True, text=True,
                          env={**os.environ, "ATC_TMP": str(atc_tmp), timing.SLACK_VARIABLE: "31"})
    assert done.returncode == 9 and done.stderr.startswith("TIME_SLACK_INVALID ")
    assert not (atc_tmp / "s" / testrun.RUNS).exists()


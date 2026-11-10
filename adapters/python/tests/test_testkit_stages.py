"""docs/TESTKIT.md § Stages and § Running and logging."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from atc_testkit import stages
from atc_testkit.evidence import Stage


def test_the_stages_are_the_table_of_the_section() -> None:
    assert [(s.name, s.marker) for s in stages.stages()] == [
        ("parallel", "not slow and not serial and not interactive_desktop"),
        ("serial", "serial and not slow and not interactive_desktop"),
        ("interactive", "interactive_desktop and not slow")]
    assert [stages.private_desktop(s) for s in stages.stages()] == [True, True, False]
    assert stages.SLOW.marker == "slow" and not stages.private_desktop(stages.SLOW)


def test_workers_are_at_most_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    assert stages.default_workers() == 8
    monkeypatch.setattr(os, "cpu_count", lambda: 3)
    assert stages.default_workers() == 3
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert stages.default_workers() == 1


def host_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.unit
def test_the_flag_wins_over_the_host_file_and_the_host_file_over_the_default(tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    host = host_file(tmp_path, "other = 'kept for later keys'\ntest_workers = 16\n")
    assert stages.resolve_workers(4, {}, host) == stages.Workers(4, "flag")
    assert stages.resolve_workers(None, {}, host) == stages.Workers(16, "~/.atc/config.toml")
    assert stages.resolve_workers(None, {}, tmp_path / "absent.toml") == stages.Workers(8, "default")
    assert stages.resolve_workers(None, {}, host_file(tmp_path, "other = 1\n")) == stages.Workers(8, "default")
    assert stages.Workers(16, "~/.atc/config.toml").line() == "workers: 16 (~/.atc/config.toml)"


@pytest.mark.unit
def test_ci_never_reads_the_host_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    assert stages.resolve_workers(None, {"CI": "true"}, host_file(tmp_path, "test_workers = 16\n")) == \
        stages.Workers(4, "default")
    assert stages.resolve_workers(None, {"CI": "true"}, host_file(tmp_path, "not toml [")).source == "default"


@pytest.mark.unit
def test_the_host_file_is_beside_tmp_and_cache_in_the_users_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # drops the suite's stand-in for the host file
    assert stages.host_config() == Path.home() / ".atc" / "config.toml"


@pytest.mark.unit
@pytest.mark.parametrize("text", ["test_workers = 0\n", "test_workers = -2\n", "test_workers = 'eight'\n",
                                  "test_workers = '8'\n", "test_workers = true\n", "test_workers = 2.5\n",
                                  "test_workers = 33\n", "test_workers = [\n"])
def test_a_bad_host_file_is_refused_with_its_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    host = host_file(tmp_path, text)
    with pytest.raises(stages.locations.Refusal) as refused:
        stages.resolve_workers(None, {}, host)
    assert (refused.value.token, refused.value.code) == ("ATC_CONFIG_INVALID", 9)
    assert str(host) in refused.value.detail


@pytest.mark.unit
@pytest.mark.parametrize("encoded", ["# 工作者\ntest_workers = 4\n".encode("cp950"),
                                     "test_workers = 4\n".encode("utf-16")], ids=["cp950", "utf-16"])
def test_a_host_file_that_is_not_utf8_is_refused(tmp_path: Path, encoded: bytes) -> None:
    host = tmp_path / "config.toml"
    host.write_bytes(encoded)
    with pytest.raises(stages.locations.Refusal) as refused:
        stages.resolve_workers(None, {}, host)
    assert (refused.value.token, refused.value.code) == ("ATC_CONFIG_INVALID", 9)
    assert str(host) in refused.value.detail


@pytest.mark.unit
def test_the_host_file_may_name_every_logical_cpu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    assert stages.resolve_workers(None, {}, host_file(tmp_path, "test_workers = 24\n")) == \
        stages.Workers(24, "~/.atc/config.toml")


@pytest.mark.unit
def test_an_unreadable_host_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(stages.locations.Refusal) as refused:
        stages.resolve_workers(None, {}, tmp_path)
    assert refused.value.token == "ATC_CONFIG_INVALID"


@pytest.mark.unit
@pytest.mark.parametrize(("text", "count"), [("1", 1), ("16", 16), ("24", 24), ("25", None), ("100000", None),
                                             ("0", None), ("-1", None), ("x", None), ("", None), (" 8", None),
                                             ("+8", None), ("\u0661\u0666", None), ("\uff18", None)])
def test_a_flag_count_is_ascii_digits_from_one_to_the_cpu_count(monkeypatch: pytest.MonkeyPatch, text: str,
                                                                count: int | None) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    assert stages.flag_count(text) == count


@pytest.mark.unit
@pytest.mark.parametrize(("value", "count"), [(1, 1), (24, 24), (25, None), (0, None), ("8", None), (True, None),
                                              (2.0, None), (None, None)])
def test_a_host_count_is_a_toml_integer_from_one_to_the_cpu_count(monkeypatch: pytest.MonkeyPatch, value: object,
                                                                  count: int | None) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    assert stages.host_count(value) == count


@pytest.mark.unit
def test_the_parallel_stage_prints_its_count_before_it_starts(tmp_path: Path,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    printed = []

    def fake(command, **_options):
        printed.append(capsys.readouterr().out)
        return 0
    stages.run_stages(["runner"], [], cwd=tmp_path, stage_list=stages.stages(), env_for=lambda stage: {},
                      options_for=lambda stage: [], log_for=lambda stage: tmp_path / f"{stage.name}.log",
                      workers=stages.Workers(5, "flag"), run=fake)
    assert printed == ["workers: 5 (flag)\n", "", ""]


def test_every_stage_passes_its_worker_option_explicitly() -> None:
    parallel, serial, interactive = stages.stages(("tests/x.py",))
    assert stages.pytest_arguments(parallel, 3) == ["-q", "-m", parallel.marker, "-n", "3", "--dist", "loadscope",
                                                    "--ignore=tests/x.py"]
    assert stages.pytest_arguments(serial) == ["-q", "-m", serial.marker, "-n", "0", "--ignore=tests/x.py"]
    assert stages.pytest_arguments(interactive) == ["-q", "-m", interactive.marker, "-n", "0", "--ignore=tests/x.py"]
    assert stages.pytest_arguments(stages.SLOW) == ["-q", "-m", "slow", "-n", "0"]


def test_a_stage_record_is_name_marker_and_ignored_paths() -> None:
    assert stages.stages(("a",))[0].record() == {"name": "parallel", "marker": stages.PARALLEL.marker, "ignore": ["a"]}


class Fake:
    def __init__(self, *codes: int):
        self.codes, self.calls = list(codes), []

    def __call__(self, command, *, cwd, env, log, private=False):
        self.calls.append((command, env, log, private))
        return self.codes.pop(0)


def run_with(fake: Fake, tmp_path: Path) -> stages.Outcome:
    return stages.run_stages(["runner"], ["tests"], cwd=tmp_path, stage_list=stages.stages(),
                             env_for=lambda stage: {"DATA": stage.name}, options_for=lambda stage: ["--opt", stage.name],
                             log_for=lambda stage: tmp_path / f"{stage.name}.log", workers=2, run=fake)


def test_stages_run_in_order_with_their_own_data_log_and_desktop(tmp_path: Path) -> None:
    fake = Fake(0, 0, 0)
    assert run_with(fake, tmp_path).token is None
    assert [call[1]["DATA"] for call in fake.calls] == ["parallel", "serial", "interactive"]
    assert [call[2].name for call in fake.calls] == ["parallel.log", "serial.log", "interactive.log"]
    assert [call[3] for call in fake.calls] == [True, True, False]
    assert fake.calls[0][0] == ["runner", "-q", "-m", stages.PARALLEL.marker, "-n", "2", "--dist", "loadscope",
                                "--opt", "parallel", "tests"]


def test_a_stage_that_collects_no_test_passes(tmp_path: Path) -> None:
    assert run_with(Fake(5, 0, 5), tmp_path).token is None


def test_a_run_in_which_every_stage_collects_no_test_is_refused(tmp_path: Path) -> None:
    outcome = run_with(Fake(5, 5, 5), tmp_path)
    assert outcome.token == "NO_TESTS_COLLECTED" and outcome.code == 5


def test_a_failing_stage_stops_the_run(tmp_path: Path) -> None:
    fake = Fake(0, 1)
    outcome = run_with(fake, tmp_path)
    assert (outcome.token, outcome.stage.name, outcome.code) == ("TESTS_FAILED", "serial", 1)
    assert outcome.log == tmp_path / "serial.log" and len(fake.calls) == 2


def test_output_is_live_and_logged_under_a_header(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "deep/check.log"
    code = stages.run_logged([sys.executable, "-c", "print('out'); import sys; print('err', file=sys.stderr); sys.exit(3)"],
                             cwd=tmp_path, env=os.environ, log=log)
    assert code == 3
    captured = capfd.readouterr()
    assert "out" in captured.out and "err" in captured.out and captured.err == ""
    text = log.read_text(encoding="utf-8")
    head = text.splitlines()
    assert sys.version.split()[0] in head[1] and head[2] == f"cwd: {tmp_path}" and head[3] == "desktop: interactive"
    assert head[4].startswith("command: ") and "out" in text and "err" in text


@pytest.mark.skipif(os.name != "nt", reason="a private desktop exists on Windows only")
def test_the_log_header_records_a_private_desktop(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "check.log"
    assert stages.run_logged([sys.executable, "-c", "pass"], cwd=tmp_path, env=os.environ, log=log, private=True) == 0
    assert "desktop: private" in log.read_text(encoding="utf-8")


def test_a_failure_repeats_the_last_twenty_failed_or_error_lines(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("\n".join(["noise", *(f"FAILED t{i}" for i in range(15)), *(f"ERROR e{i}" for i in range(10)), "tail"]),
                   encoding="utf-8")
    lines = stages.failure_lines(log)
    assert len(lines) == 20 and lines[0] == "FAILED t5" and lines[-1] == "ERROR e9"


def test_a_log_without_such_lines_repeats_its_tail(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("\n".join(f"line {i}" for i in range(30)), encoding="utf-8")
    assert stages.failure_lines(log)[0] == "line 10"
    assert stages.failure_lines(tmp_path / "absent.log") == []


def test_stage_values_hash_as_the_evidence_type() -> None:
    assert isinstance(stages.PARALLEL, Stage)

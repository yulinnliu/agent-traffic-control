"""`atc doctor -v` and doctor's memory lines: what a person sees of the host's test runs and memory
(docs/TESTKIT.md § Commands, § Host worker budget)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from atc_testkit import stages, status, workers

import doctor

GIB = workers.GIB


@pytest.fixture
def pooled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(stages, "host_config", lambda: tmp_path / "absent.toml")
    return {workers.POOL_DIRECTORY: str(tmp_path / "run")}


@pytest.mark.unit
def test_progress_is_the_last_percentage_in_the_logs_tail(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_bytes(b"[ 10%]\n" + b"x" * status.TAIL_BYTES + b"\n...... [ 42%]\n....")
    assert status.progress(str(log)) == 42
    log.write_bytes(b"[ 10%]\n" + b"x" * status.TAIL_BYTES)
    assert status.progress(str(log)) is None
    assert status.progress("") is None and status.progress(str(tmp_path / "absent.log")) is None


@pytest.mark.unit
def test_status_names_ci_off_and_an_unused_pool(pooled: dict[str, str], tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    assert status.lines(status.collect({**pooled, "CI": "true"})) == [
        "CI worker pool: CI is set, runs use no host pool"]
    size = stages.pool_size({})
    assert status.lines(status.collect(pooled)) == [f"IDLE worker pool: {size} of {size} free"]
    config = tmp_path / "config.toml"
    config.write_text("test_worker_pool = 0\n", encoding="utf-8")
    monkeypatch.setattr(stages, "host_config", lambda: config)
    assert status.lines(status.collect(pooled)) == ["OFF worker pool: test_worker_pool = 0 in ~/.atc/config.toml"]


@pytest.mark.integration
def test_status_shows_a_run_its_progress_and_limit_and_keeps_a_stale_row(pooled: dict[str, str], tmp_path: Path,
                                                                        capsys: pytest.CaptureFixture[str]) -> None:
    size = stages.pool_size({})
    budget = workers.Budget(workers.Pool(stages.pool_directory(pooled), size), label="atc test run demo")
    grant = budget.acquire("parallel", 2, 1)
    log = tmp_path / "run.log"
    log.write_text("....... [ 37%]\n", encoding="utf-8")
    budget.annotate(log=str(log), limit=f"workers: 2 of {size} (default {size})")
    stale = stages.pool_directory(pooled) / workers.ROWS / "1-dead.json"
    stale.write_bytes(budget.row.path.read_bytes())
    try:
        found = status.lines(status.collect(pooled))
        assert found[0] == f"worker pool: {size - 2} of {size} free"
        assert found[1].startswith("RUN atc test run demo pid ") and " parallel 2/2 workers held " in found[1]
        assert found[1].endswith(f" progress 37% log {log}")
        assert found[2:] == [f"  workers: 2 of {size} (default {size})"]
        assert stale.exists()
        assert status.show(as_json=True, environ=pooled) == 0
        shown = json.loads(capsys.readouterr().out)
        assert [(run["label"], run["tokens"], run["progress"]) for run in shown["runs"]] == [
            ("atc test run demo", 2, 37)]
    finally:
        budget.release(grant)
    assert status.lines(status.collect(pooled)) == [f"IDLE worker pool: {size} of {size} free"]


@pytest.mark.unit
@pytest.mark.parametrize(("figures", "table", "expected"), [
    (workers.Performance(32 * GIB, 17 * GIB, 2 * GIB), "",
     [("READY", "17.0 of 32.0 GiB available; kernel nonpaged pool 2.0 GiB")]),
    (workers.Performance(32 * GIB, 17 * GIB, 5 * GIB), "",
     [("WARN", "kernel nonpaged pool 5.0 GiB (above 4.0 GiB): a driver is leaking kernel memory; test workers are "
               "cut by the memory ceiling until a reboot frees it")]),
    (workers.Performance(64 * GIB, 17 * GIB, 5 * GIB), "", [("READY", None)]),
    (workers.Performance(16 * GIB, 8 * GIB, 3 * GIB), "", [("READY", None)]),
    (workers.Performance(32 * GIB, 17 * GIB, 5 * GIB), "nonpaged_pool_warn_gib = 6\n", [("READY", None)]),
    (workers.Performance(32 * GIB, GIB, 2 * GIB), "",
     [("WARN", "1.0 GiB available: test runs get 1 worker at 256 MiB each")]),
])
def test_doctor_warns_of_a_leaking_kernel_pool_and_low_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                              figures: workers.Performance, table: str,
                                                              expected: list) -> None:
    config = tmp_path / "config.toml"
    config.write_text(table, encoding="utf-8")
    monkeypatch.setattr(stages, "host_config", lambda: config)
    found = doctor.memory_checks({}, doctor.memory({}, figures))
    assert len(found) == len(expected)
    assert [(check.status, check.detail if detail else None) for check, (_, detail) in zip(found, expected)] == expected


@pytest.mark.unit
def test_doctor_memory_refuses_a_bad_threshold_and_says_nothing_where_unknown(tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text("nonpaged_pool_warn_gib = 0\n", encoding="utf-8")
    monkeypatch.setattr(stages, "host_config", lambda: config)
    found = doctor.memory_checks({}, doctor.memory({}, workers.Performance(32 * GIB, 17 * GIB, 2 * GIB)))
    assert [check.status for check in found] == ["ERROR"] and "nonpaged_pool_warn_gib" in found[0].detail
    assert doctor.memory_checks({}, None) == []
    monkeypatch.setattr(workers, "performance", lambda: None)
    assert doctor.memory({}) is None


@pytest.mark.unit
def test_a_doctor_warning_leaves_the_exit_code_alone(monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(doctor, "diagnose", lambda root: [doctor.Check("memory", "WARN", "low")])
    assert doctor.main([]) == 0
    assert "READY doctor: 0 error(s), 1 warning(s), 0 not initialized\n" in capsys.readouterr().out

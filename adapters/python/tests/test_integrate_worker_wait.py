"""`atc integrate` leaves a verifier's queue for host test workers out of its budget, and `atc doctor` shows the pool
(docs/TESTKIT.md § Host worker budget)."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from atc_testkit import workers

import doctor
import git_integrate as gi

pytestmark = pytest.mark.integration


def verifier(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "verifier.py"
    script.write_text("import os, sys, time\nfrom pathlib import Path\n" + body, encoding="utf-8")
    return [sys.executable, str(script)]


def test_the_queue_a_verifier_reports_extends_its_clock(tmp_path: Path) -> None:
    argv = verifier(tmp_path, "Path(os.environ['WAIT']).write_text('3.0')\ntime.sleep(2.5)\n")
    wait_file = tmp_path / "wait.txt"
    code, *_ = gi._run_verifier(argv, tmp_path, {**dict(__import__("os").environ), "WAIT": str(wait_file)}, 1.5,
                                tmp_path, wait_file)
    assert code == 0
    code, *_ = gi._run_verifier(argv, tmp_path, {**dict(__import__("os").environ), "WAIT": str(tmp_path / "x")},
                                1.5, tmp_path, None)
    assert code is None


def test_a_verifier_that_waited_past_its_limit_is_not_a_failed_candidate(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    argv = verifier(tmp_path, "assert os.environ['ATC_WORKER_MAX_WAIT'] == '60'\n"
                              "assert os.environ['ATC_WORKER_WAIT_FILE']\n"
                              "sys.stderr.write('WORKER_WAIT_TIMEOUT\\nwaited 61s for 2 host workers (parallel)\\n"
                              "behind: atc test run x pid 1 4 tokens for 3m\\nnext: atc doctor\\n')\nsys.exit(1)\n")
    with pytest.raises(gi.Refused) as refused:
        gi._verify_candidate(argv, candidate, "a" * 40, "b" * 40, "c" * 40, 60, tmp_path, tmp_path / "report.json")
    assert refused.value.token == workers.WAIT_TIMEOUT
    assert refused.value.token not in gi.VERIFIER_FAILURES
    assert "behind: atc test run x pid 1" in refused.value.detail
    assert not list(tmp_path.glob("verify-wait-*"))


def test_doctor_lists_the_runs_holding_and_leaves_no_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pool = tmp_path / "run"
    environ = {workers.POOL_DIRECTORY: str(pool)}
    monkeypatch.setattr(doctor.stages, "host_config", lambda: tmp_path / "absent.toml")
    assert "no run has used it yet" in doctor.worker_pool_checks(environ)[0].detail
    script = tmp_path / "holder.py"
    script.write_text("import sys, time\nfrom pathlib import Path\nfrom atc_testkit import workers\n"
                      f"budget = workers.Budget(workers.Pool(Path(sys.argv[1]), {workers.default_pool_size(__import__('os').cpu_count() or 1)}), label='atc verify demo')\n"
                      "grant = budget.acquire('parallel', 1, 1)\nprint('HELD', flush=True)\ntime.sleep(60)\n",
                      encoding="utf-8")
    holder = subprocess.Popen([sys.executable, str(script), str(pool)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().startswith("HELD")
        checks = doctor.worker_pool_checks(environ)
        assert checks[0].name == "worker pool" and "1 run active, 0 waiting" in checks[0].detail
        assert checks[1].name == "worker pool run" and checks[1].detail.startswith("atc verify demo pid")
        size = workers.default_pool_size(__import__("os").cpu_count() or 1)
        assert workers.Pool(pool, size).free() == size - 1
    finally:
        holder.kill()
        holder.wait()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and "0 runs active" not in doctor.worker_pool_checks(environ)[0].detail:
        time.sleep(0.2)
    assert "0 runs active" in doctor.worker_pool_checks(environ)[0].detail

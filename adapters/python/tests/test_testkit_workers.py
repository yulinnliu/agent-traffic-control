"""The host worker budget (docs/TESTKIT.md § Host worker budget): memory sizing, the pool, waits and crash reclaim."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from atc_testkit import locations, stages, testrun, workers
from atc_testkit.evidence import Stage

GIB = workers.GIB
SETTLE = 20.0  # seconds a child gets to reach a state on a loaded host

HOLDER = """
import sys, time
from pathlib import Path
from atc_testkit import workers
pool = workers.Pool(Path(sys.argv[1]), int(sys.argv[2]))
budget = workers.Budget(pool, label=sys.argv[3])
grant = budget.acquire("parallel", int(sys.argv[4]), int(sys.argv[4]))
print("HELD", grant.count, flush=True)
time.sleep(float(sys.argv[5]))
budget.release(grant)
print("RELEASED", flush=True)
"""


def memory(monkeypatch: pytest.MonkeyPatch, available: int | None) -> None:
    monkeypatch.setattr(workers, "available_memory", lambda: available)


def start_holder(tmp_path: Path, pool: Path, size: int, count: int, seconds: float,
                 label: str = "holder") -> subprocess.Popen:
    script = tmp_path / "holder.py"
    script.write_text(HOLDER, encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(script), str(pool), str(size), label, str(count), str(seconds)],
                               stdout=subprocess.PIPE, text=True)
    line = process.stdout.readline()
    assert line.startswith("HELD"), line
    return process


@pytest.fixture()
def pool_dir(tmp_path: Path) -> Path:
    return tmp_path / "run"


# Sizing

@pytest.mark.unit
def test_memory_cap_counts_whole_workers_after_the_reserve() -> None:
    assert workers.memory_cap(None, GIB) is None
    assert workers.memory_cap(7 * GIB, GIB) == 6
    assert workers.memory_cap(int(5.4 * GIB), GIB) == 4
    assert workers.memory_cap(GIB // 2, GIB) == 1


@pytest.mark.unit
@pytest.mark.parametrize(("cpus", "size"), [(24, 18), (8, 6), (2, 2), (1, 1)])
def test_the_pool_is_three_quarters_of_the_logical_cpus(cpus: int, size: int) -> None:
    assert workers.default_pool_size(cpus) == size


@pytest.mark.unit
def test_memory_cuts_the_default_and_says_why(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    memory(monkeypatch, int(5.4 * GIB))
    resolved = stages.resolve_workers(None, {}, tmp_path / "absent.toml")
    assert resolved == stages.Workers(4, "default", 8, "memory ceiling: 5.4 GiB available")
    assert resolved.line() == "workers: 4 (default 8, memory ceiling: 5.4 GiB available)"
    memory(monkeypatch, 64 * GIB)
    assert stages.resolve_workers(None, {}, tmp_path / "absent.toml") == stages.Workers(8, "default")


@pytest.mark.unit
@pytest.mark.parametrize("environ", [{}, {"CI": "true"}])
def test_a_flag_is_cut_by_memory_and_never_by_cpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                   environ: dict) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    memory(monkeypatch, 64 * GIB)
    assert stages.resolve_workers(8, environ, tmp_path / "absent.toml") == stages.Workers(8, "flag")
    memory(monkeypatch, 7 * GIB)
    resolved = stages.resolve_workers(8, environ, tmp_path / "absent.toml")
    assert resolved.line() == "workers: 6 (flag 8, memory ceiling: 7.0 GiB available)"
    memory(monkeypatch, None)
    assert stages.resolve_workers(8, environ, tmp_path / "absent.toml") == stages.Workers(8, "flag")


@pytest.mark.unit
def test_a_nested_run_takes_one_worker_and_a_flag_still_counts(monkeypatch: pytest.MonkeyPatch,
                                                               tmp_path: Path) -> None:
    nested = {workers.HELD: "123-abc"}
    assert stages.resolve_workers(None, nested, tmp_path / "absent.toml").line() == \
        "workers: 1 (nested in a budgeted run)"
    assert stages.resolve_workers(3, nested, tmp_path / "absent.toml") == stages.Workers(3, "flag")


@pytest.mark.unit
def test_a_project_declares_its_worker_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    memory(monkeypatch, 5 * GIB)
    (tmp_path / "pyproject.toml").write_text("[tool.atc-testkit]\nworker_memory_mib = 512\n", encoding="utf-8")
    assert stages.resolve_workers(None, {}, tmp_path / "absent.toml", root=tmp_path).count == 8
    (tmp_path / "pyproject.toml").write_text("[tool.atc-testkit]\nworker_memory_mib = 0\n", encoding="utf-8")
    with pytest.raises(locations.Refusal) as refused:
        stages.resolve_workers(None, {}, tmp_path / "absent.toml", root=tmp_path)
    assert refused.value.token == stages.CONFIG_INVALID and "worker_memory_mib" in refused.value.detail


# Opening a budget

@pytest.mark.unit
def test_ci_a_nested_run_and_a_pool_of_zero_stay_outside_the_pool(tmp_path: Path, pool_dir: Path) -> None:
    absent = tmp_path / "absent.toml"
    named = {workers.POOL_DIRECTORY: str(pool_dir)}
    assert not stages.open_budget("x", {**named, "CI": "1"}, absent).active
    assert not stages.open_budget("x", {**named, workers.HELD: "1-a"}, absent).active
    off = tmp_path / "off.toml"
    off.write_text("test_worker_pool = 0\n", encoding="utf-8")
    assert not stages.open_budget("x", named, off).active
    assert not pool_dir.exists()
    assert stages.open_budget("x", named, absent).active


@pytest.mark.unit
@pytest.mark.parametrize("text", ["test_worker_pool = -1\n", "test_worker_pool = 'all'\n",
                                  "test_worker_max_wait = 0\n", "test_worker_max_wait = 90000\n"])
def test_a_bad_pool_key_is_refused(tmp_path: Path, pool_dir: Path, text: str) -> None:
    host = tmp_path / "config.toml"
    host.write_text(text, encoding="utf-8")
    with pytest.raises(locations.Refusal) as refused:
        stages.open_budget("x", {workers.POOL_DIRECTORY: str(pool_dir)}, host)
    assert refused.value.token == stages.CONFIG_INVALID and refused.value.code == 9


@pytest.mark.unit
def test_the_wait_limit_is_the_callers_then_the_runs_then_the_host_files(tmp_path: Path, pool_dir: Path) -> None:
    host = tmp_path / "config.toml"
    host.write_text("test_worker_max_wait = 600\n", encoding="utf-8")
    named = {workers.POOL_DIRECTORY: str(pool_dir)}
    assert stages.open_budget("x", named, tmp_path / "absent.toml").max_wait == workers.MAX_WAIT_SECONDS
    assert stages.open_budget("x", named, host).max_wait == 600
    assert stages.open_budget("x", named, host, max_wait=900).max_wait == 900
    assert stages.open_budget("x", {**named, workers.MAX_WAIT: "1800"}, host, max_wait=900).max_wait == 1800


@pytest.mark.unit
def test_a_pool_directory_that_is_a_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "run"
    target.write_text("", encoding="utf-8")
    with pytest.raises(locations.Refusal) as refused:
        workers.Pool(target, 4)
    assert refused.value.token == workers.POOL_UNSUITABLE and refused.value.code == 9


# The pool across processes

@pytest.mark.integration
def test_two_runs_share_the_pool(tmp_path: Path, pool_dir: Path) -> None:
    holder = start_holder(tmp_path, pool_dir, 4, 3, 30)
    try:
        budget = workers.Budget(workers.Pool(pool_dir, 4), label="second")
        grant = budget.acquire("parallel", 3, 1)
        assert grant.count == 1 and grant.active == 1
        granted = stages.Workers(3, "default").granted(grant)
        assert granted.line() == "workers: 1 (default 3; host budget: 1 of 4 free, 1 run active)"
        budget.release(grant)
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.integration
def test_a_killed_holder_gives_its_tokens_back(tmp_path: Path, pool_dir: Path) -> None:
    holder = start_holder(tmp_path, pool_dir, 4, 4, 60)
    pool = workers.Pool(pool_dir, 4)
    assert pool.free() == 0 and len(pool.status().runs) == 1
    holder.kill()
    holder.wait()
    budget = workers.Budget(pool, label="after", max_wait=SETTLE)
    grant = budget.acquire("parallel", 4, 4)
    assert grant.count == 4
    assert pool.status(budget.row.path).runs == ()
    assert [path.name for path in (pool_dir / workers.ROWS).glob("*.json")] == [budget.row.path.name]
    budget.release(grant)


@pytest.mark.integration
def test_a_row_nobody_holds_is_stale_whatever_its_pid(pool_dir: Path) -> None:
    pool = workers.Pool(pool_dir, 2)
    stale = pool_dir / workers.ROWS / f"{os.getpid()}-deadbeef.json"
    stale.write_text(json.dumps({"pid": os.getpid(), "label": "reused pid", "stage": "parallel", "tokens": [1, 2],
                                 "want": 2, "floor": 2, "granted_at": time.time(), "waiting_since": None,
                                 "reserved": 0}), encoding="utf-8")
    assert pool.status(prune=False).runs == () and stale.exists()
    assert pool.status().runs == () and not stale.exists()


@pytest.mark.integration
def test_a_run_waits_for_its_floor_and_reports_it(tmp_path: Path, pool_dir: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    holder = start_holder(tmp_path, pool_dir, 2, 2, 2, label="first")
    try:
        wait_file = tmp_path / "wait.txt"
        budget = workers.Budget(workers.Pool(pool_dir, 2), label="second", max_wait=SETTLE + 2, wait_file=wait_file)
        grant = budget.acquire("parallel", 2, 2)
        assert grant.count == 2 and grant.waited > 0
        assert workers.wait_total(wait_file) == pytest.approx(budget.waited, abs=0.2)
        out = capsys.readouterr().out
        assert "WAIT host workers: parallel needs 2 (wants 2), 0 of 2 free; 1 run active, oldest first pid" in out
        assert "host budget: waited" in stages.Workers(2, "default").granted(grant).line()
        budget.release(grant)
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.integration
def test_the_turnstile_holds_free_tokens_for_the_run_waiting_first(tmp_path: Path, pool_dir: Path) -> None:
    script = tmp_path / "turnstile.py"
    script.write_text("import os, sys, time\nfrom pathlib import Path\nfrom atc_testkit import workers\n"
                      "pool = workers.Pool(Path(sys.argv[1]), 4)\nassert pool.try_take(0)\n"
                      "print('TURNSTILE', flush=True)\ntime.sleep(2)\n", encoding="utf-8")
    head = subprocess.Popen([sys.executable, str(script), str(pool_dir)], stdout=subprocess.PIPE, text=True)
    assert head.stdout.readline().startswith("TURNSTILE")
    try:
        started = time.monotonic()
        budget = workers.Budget(workers.Pool(pool_dir, 4), label="later", max_wait=SETTLE)
        grant = budget.acquire("serial", 1, 1)
        assert grant.count == 1 and time.monotonic() - started >= 1
        budget.release(grant)
    finally:
        head.kill()
        head.wait()


@pytest.mark.integration
def test_a_wait_past_its_limit_names_what_it_waited_behind(tmp_path: Path, pool_dir: Path) -> None:
    holder = start_holder(tmp_path, pool_dir, 2, 2, 60, label="atc integrate demo")
    try:
        budget = workers.Budget(workers.Pool(pool_dir, 2), label="late", max_wait=1)
        with pytest.raises(workers.WaitTimeout) as refused:
            budget.acquire("parallel", 2, 2)
        assert refused.value.token == workers.WAIT_TIMEOUT and refused.value.code == 124
        assert "behind: atc integrate demo pid" in refused.value.detail
        assert refused.value.detail.endswith("\nnext: atc doctor")
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.integration
def test_the_stages_take_tokens_per_stage_and_nest_their_children(tmp_path: Path, pool_dir: Path) -> None:
    budget = workers.Budget(workers.Pool(pool_dir, 6), label="stages")
    seen: list[tuple[str, int, str | None]] = []

    def fake(command, *, cwd, env, log, private=False) -> int:
        seen.append((log.name, budget.pool.free(), env.get(workers.HELD)))
        return 0

    stage_list = [Stage("parallel", "a"), Stage("serial", "b"), Stage("interactive", "c")]
    outcome = stages.run_stages(["runner"], [], cwd=tmp_path, stage_list=stage_list, env_for=lambda stage: {},
                                options_for=lambda stage: (), log_for=lambda stage: tmp_path / stage.name,
                                workers=stages.Workers(4, "default"), run=fake, budget=budget)
    assert outcome.token is None
    assert [(name, free) for name, free, _ in seen] == [("parallel", 2), ("serial", 5), ("interactive", 5)]
    assert {held for _, _, held in seen} == {budget.held_id()}
    assert budget.pool.free() == 6


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="the stage Job is Windows only")
def test_a_stage_tree_ends_with_the_process_holding_its_tokens(tmp_path: Path, pool_dir: Path) -> None:
    script = tmp_path / "stage.py"
    script.write_text(
        "import sys, time\nfrom pathlib import Path\nfrom atc_testkit import desktop, workers\n"
        "budget = workers.Budget(workers.Pool(Path(sys.argv[1]), 2), label='stage')\n"
        "grant = budget.acquire('parallel', 1, 1)\n"
        "process, _ = desktop.start([sys.executable, '-c', 'import time; time.sleep(120)'], cwd='.', env=dict(__import__('os').environ), private=False)\n"
        "job = workers.StageJob(process)\nprint('CHILD', process.pid, flush=True)\ntime.sleep(120)\n",
        encoding="utf-8")
    holder = subprocess.Popen([sys.executable, str(script), str(pool_dir)], stdout=subprocess.PIPE, text=True)
    child = int(holder.stdout.readline().split()[1])
    holder.kill()
    holder.wait()
    deadline = time.monotonic() + SETTLE
    while time.monotonic() < deadline and _alive(child):
        time.sleep(0.2)
    assert not _alive(child)


def _alive(pid: int) -> bool:
    found = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
    return str(pid) in found.stdout


# atc test run

@pytest.mark.unit
def test_test_run_asks_one_token_or_its_n_cut_by_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    assert testrun._worker_request(["tests"], tmp_path, {}) == (1, 1, ["tests"])
    assert testrun._worker_request(["-n", "0"], tmp_path, {}) == (1, 1, ["-n", "0"])
    memory(monkeypatch, 4 * GIB)
    assert testrun._worker_request(["-n", "8", "x"], tmp_path, {}) == (3, 3, ["-n", "3", "x"])
    assert "workers: 3 (flag 8, memory ceiling: 4.0 GiB available)" in capsys.readouterr().out
    assert testrun._worker_request(["--numprocesses=2"], tmp_path, {}) == (2, 2, ["--numprocesses=2"])
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    want, floor, arguments = testrun._worker_request(["-nauto"], tmp_path, {})
    assert (want, floor) == (3, 2)
    granted = testrun._granted_arguments(arguments, workers.Grant(2, (1, 2), 0, 2, 18, 1, True), want)
    assert granted == ["-n", "2"]
    assert "workers: 2 (auto 3; host budget: 2 of 18 free, 1 run active)" in capsys.readouterr().out


TURNSTILE = ("import sys, time\nfrom pathlib import Path\nfrom atc_testkit import workers\n"
             "assert workers.Pool(Path(sys.argv[1]), 1).try_take(0)\nprint('TURNSTILE', flush=True)\ntime.sleep(120)\n")


def hold_turnstile(tmp_path: Path, pool: Path) -> subprocess.Popen:
    """A process that holds the pool's turnstile, so every other run waits."""
    script = tmp_path / "turnstile.py"
    script.write_text(TURNSTILE, encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(script), str(pool)], stdout=subprocess.PIPE, text=True)
    assert process.stdout.readline().startswith("TURNSTILE")
    return process


def small_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    (tree / "pyproject.toml").write_text('[project]\nname = "small"\nversion = "1"\n', encoding="utf-8")
    return tree


@pytest.mark.integration
def test_an_interrupt_while_queued_stops_the_run_with_130(tmp_path: Path, pool_dir: Path, atc_tmp: Path,
                                                          monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    head = hold_turnstile(tmp_path, pool_dir)
    try:
        def interrupted(self, total: float) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(workers.Budget, "_write_wait", interrupted)
        environ = {**os.environ, workers.POOL_DIRECTORY: str(pool_dir)}
        code = testrun.run(["--scratch", "q", "--tree", str(small_tree(tmp_path)), "--python", sys.executable,
                            "--", "tests"], environ)
        assert code == testrun.INTERRUPTED_EXIT == 130
        out = capsys.readouterr().out
        assert testrun.QUEUE_INTERRUPTED in out and "PASS" not in out
        pool = workers.Pool(pool_dir, stages.pool_size(stages.host_table(environ)))
        assert pool.free() == pool.size and all(not row.tokens for row in pool.status().runs)
    finally:
        head.kill()
        head.wait()


@pytest.mark.integration
def test_a_cli_run_past_its_wait_limit_exits_124_with_the_token(tmp_path: Path, pool_dir: Path,
                                                                atc_tmp: Path) -> None:
    head = hold_turnstile(tmp_path, pool_dir)
    try:
        home = tmp_path / "home"
        home.mkdir()
        environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
        environment.update({workers.POOL_DIRECTORY: str(pool_dir), workers.MAX_WAIT: "1", "HOME": str(home),
                            "USERPROFILE": str(home), "PYTHONIOENCODING": "utf-8"})
        run = subprocess.run([sys.executable, "-m", "atc_testkit.commands", "test", "run", "--scratch", "w",
                              "--tree", str(small_tree(tmp_path)), "--python", sys.executable, "--", "tests"],
                             env=environment, capture_output=True, text=True, encoding="utf-8",
                             stdin=subprocess.DEVNULL, timeout=SETTLE * 3)
        assert run.returncode == 124, run.stdout + run.stderr
        assert run.stderr.startswith(f"{workers.WAIT_TIMEOUT} "), run.stderr
        assert "next: atc doctor" in run.stderr and "PASS" not in run.stdout
    finally:
        head.kill()
        head.wait()

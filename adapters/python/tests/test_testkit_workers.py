"""The host worker budget (docs/TESTKIT.md § Host worker budget): memory sizing, the pool, waits and crash reclaim."""
from __future__ import annotations

import ctypes
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
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0

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
def test_memory_cuts_the_default_outside_the_pool_and_says_why(monkeypatch: pytest.MonkeyPatch,
                                                               tmp_path: Path) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    memory(monkeypatch, int(2.4 * GIB))
    resolved = stages.resolve_workers(None, {"CI": "true"}, tmp_path / "absent.toml")
    assert resolved == stages.Workers(5, "default", 24, "memory ceiling: 2.4 GiB available, 256 MiB/worker default")
    assert resolved.line() == "workers: 5 (default 24, memory ceiling: 2.4 GiB available, 256 MiB/worker default)"
    memory(monkeypatch, 64 * GIB)
    assert stages.resolve_workers(None, {"CI": "true"}, tmp_path / "absent.toml") == stages.Workers(24, "default")


@pytest.mark.unit
def test_the_default_in_the_pool_wants_the_pool_and_memory_waits_for_the_grant(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pool_dir: Path) -> None:
    """An early low reading never shrinks the count or its floor (the verifier that ran on one worker)."""
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    memory(monkeypatch, int(2.1 * GIB))
    resolved = stages.resolve_workers(None, {workers.POOL_DIRECTORY: str(pool_dir)}, tmp_path / "absent.toml")
    assert resolved == stages.Workers(18, "default") and resolved.floor() == workers.FLOOR
    budget = workers.Budget(workers.Pool(pool_dir, 18), label="late")
    grant = budget.acquire("parallel", resolved.count, resolved.floor())
    assert grant.count == 4 and grant.memory == "memory ceiling: 2.1 GiB available, 256 MiB/worker default"
    assert resolved.granted(grant).line() == \
        "workers: 4 of 18 (default 18; memory ceiling: 2.1 GiB available, 256 MiB/worker default)"
    budget.release(grant)


@pytest.mark.unit
@pytest.mark.parametrize("environ", [{}, {"CI": "true"}])
def test_a_flag_is_cut_by_memory_and_never_by_cpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                   environ: dict) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    memory(monkeypatch, 64 * GIB)
    assert stages.resolve_workers(8, environ, tmp_path / "absent.toml") == stages.Workers(8, "flag")
    memory(monkeypatch, int(2.5 * GIB))
    resolved = stages.resolve_workers(8, environ, tmp_path / "absent.toml")
    assert resolved.line() == "workers: 6 (flag 8, memory ceiling: 2.5 GiB available, 256 MiB/worker default)"
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
    resolved = stages.resolve_workers(None, {"CI": "true"}, tmp_path / "absent.toml", root=tmp_path)
    assert resolved.count == 8 and resolved.clamp.endswith("512 MiB/worker pyproject")
    (tmp_path / "pyproject.toml").write_text("[tool.atc-testkit]\nworker_memory_mib = 0\n", encoding="utf-8")
    with pytest.raises(locations.Refusal) as refused:
        stages.resolve_workers(None, {"CI": "true"}, tmp_path / "absent.toml", root=tmp_path)
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
        assert granted.line() == "workers: 1 of 4 (default 3; host budget: 1 of 4 free, 1 run active)"
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
    child = _handle(int(holder.stdout.readline().split()[1]))
    try:
        holder.kill()
        holder.wait()
        assert _ends_within(child, SETTLE)
    finally:
        ctypes.windll.kernel32.CloseHandle(child)


def _handle(pid: int) -> int:
    """A handle to the live process ``pid``: it names that process only, where a PID a loaded host hands to a new
    process within seconds."""
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    assert handle, f"process {pid} is not running"
    return handle


def _ends_within(handle: int, seconds: float) -> bool:
    return ctypes.windll.kernel32.WaitForSingleObject(handle, int(seconds * 1000)) == WAIT_OBJECT_0


# atc test run

@pytest.mark.unit
def test_test_run_asks_the_default_or_its_n_cut_by_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    pooled = {workers.POOL_DIRECTORY: str(tmp_path / "run")}
    assert testrun._worker_request(["tests"], tmp_path, pooled) == (stages.Workers(18, "default"),
                                                                   ["tests", "-n", "auto"])
    assert testrun._worker_request(["tests"], tmp_path, pooled, xdist=False) == (None, ["tests"])
    assert testrun._worker_request(["-n", "0"], tmp_path, pooled) == (None, ["-n", "0"])
    memory(monkeypatch, int(1.75 * GIB))
    cut, arguments = testrun._worker_request(["-n", "8", "x"], tmp_path, pooled)
    assert (cut.count, cut.floor(), arguments) == (3, 3, ["-n", "3", "x"])
    assert testrun._worker_request(["--numprocesses=2"], tmp_path, pooled) == (stages.Workers(2, "flag"),
                                                                              ["--numprocesses=2"])
    default, arguments = testrun._worker_request(["-nauto"], tmp_path, pooled)
    assert (default.count, default.floor()) == (18, 2)
    granted, line = testrun._granted_arguments(arguments, workers.Grant(2, (1, 2), 0, 2, 18, 1, True), 18, default)
    assert granted == ["-n", "2"]
    assert line == "workers: 2 of 18 (default 18; host budget: 2 of 18 free, 1 run active)"
    assert testrun._granted_arguments(["-n", "0"], workers.Grant(1), 1, None) == (["-n", "0"], "")
    one = workers.Grant(1, (1, 1), 0, 1, 18, 1, True)
    assert testrun._granted_arguments(["-n", "auto", "tests"], one, 18, default)[0] == ["-n", "0", "tests"]
    assert testrun._granted_arguments(["-n", "4", "tests"], one, 4, default)[0] == ["-n", "1", "tests"]


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
        try:
            code = testrun.run(["--scratch", "q", "--tree", str(small_tree(tmp_path)), "--python", sys.executable,
                                "--", "tests"], environ)
        except KeyboardInterrupt:
            pytest.fail("the interrupt left atc test run as a traceback")
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


@pytest.mark.integration
def test_a_flag_above_the_pool_runs_the_pool_size_and_names_it(tmp_path: Path, pool_dir: Path,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    budget = workers.Budget(workers.Pool(pool_dir, 2), label="flag")
    flag = stages.Workers(8, stages.SOURCE_FLAG)
    grant = budget.acquire("parallel", flag.count, flag.floor())
    assert grant.count == 2
    assert flag.granted(grant).line() == "workers: 2 of 2 (flag 8, host pool: 2)"
    cut = stages.Workers(6, stages.SOURCE_FLAG, 8, "memory ceiling: 7.0 GiB available")
    assert cut.granted(grant).line() == \
        "workers: 2 of 2 (flag 8, memory ceiling: 7.0 GiB available, host pool: 2)"
    assert testrun._granted_arguments(["-n", "8", "tests"], grant, 8, flag) == \
        (["-n", "2", "tests"], "workers: 2 of 2 (flag 8, host pool: 2)")
    budget.release(grant)
    seen: list[list[str]] = []

    def fake(command, *, cwd, env, log, private=False) -> int:
        seen.append(list(command))
        return 0

    outcome = stages.run_stages(["runner"], [], cwd=tmp_path, stage_list=[Stage("parallel", "a")],
                                env_for=lambda stage: {}, options_for=lambda stage: (),
                                log_for=lambda stage: tmp_path / stage.name, workers=flag, run=fake, budget=budget)
    assert outcome.token is None and seen and seen[0][seen[0].index("-n") + 1] == "2"
    assert "workers: 2 of 2 (flag 8, host pool: 2)" in capsys.readouterr().out
    assert stages.Workers(8, stages.SOURCE_FLAG).granted(workers.Grant(8)).line() == "workers: 8 (flag)"


# Calibration and visibility (ATC-0023)

@pytest.mark.unit
def test_a_project_record_beats_the_default_and_a_declaration_beats_the_record(tmp_path: Path,
                                                                                pool_dir: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "recorded-project"\n', encoding="utf-8")
    environ = {workers.POOL_DIRECTORY: str(pool_dir)}
    assert stages.worker_memory_source(root, environ) == (256 * workers.MIB, "default")
    for value in (100, 160, 120):
        workers.record_memory(pool_dir, "recorded-project", value * workers.MIB)
    assert stages.worker_memory_source(root, environ) == (200 * workers.MIB, "recorded")
    assert stages.worker_memory_source(root, {**environ, "CI": "true"}) == (256 * workers.MIB, "default")
    workers.record_memory(pool_dir, "recorded-project", 10 * workers.MIB)
    for _ in range(workers.RECORDED_RUNS):
        workers.record_memory(pool_dir, "recorded-project", 10 * workers.MIB)
    assert stages.worker_memory_source(root, environ) == (workers.RECORD_LEAST_MIB * workers.MIB, "recorded")
    (root / "pyproject.toml").write_text('[project]\nname = "recorded-project"\n[tool.atc-testkit]\n'
                                         'worker_memory_mib = 300\n', encoding="utf-8")
    assert stages.worker_memory_source(root, environ) == (300 * workers.MIB, "pyproject")


@pytest.mark.unit
@pytest.mark.parametrize(("tree", "recorded"), [(True, 150), (False, 600)])
def test_a_pooled_stage_records_its_peak_per_worker_from_four_workers(monkeypatch: pytest.MonkeyPatch,
                                                                     pool_dir: Path, tree: bool,
                                                                     recorded: int) -> None:
    peaks = iter([3 * 400 * workers.MIB, 4 * 150 * workers.MIB])
    monkeypatch.setattr(workers, "take_peak", lambda: next(peaks))
    monkeypatch.setattr(workers, "PEAK_IS_TREE", tree)
    budget = workers.Budget(workers.Pool(pool_dir, 6), label="calibrate", memory_key="calibrated")
    for count in (3, 4):
        budget.release(budget.acquire("parallel", count, count))
    assert json.loads((pool_dir / workers.MEMORY / "calibrated.json").read_text(encoding="utf-8")) == \
        {"per_worker": [recorded * workers.MIB]}


@pytest.mark.unit
def test_a_processs_own_row_stays_alive_when_its_lock_does_not_conflict_with_itself(
        monkeypatch: pytest.MonkeyPatch, pool_dir: Path) -> None:
    budget = workers.Budget(workers.Pool(pool_dir, 2), label="own")
    grant = budget.acquire("parallel", 1, 1)
    probed: list[int] = []

    def posix_lock(fd: int, offset: int) -> bool:
        probed.append(offset)
        return True

    monkeypatch.setattr(workers, "_try_lock", posix_lock)
    monkeypatch.setattr(workers, "_unlock", lambda fd, offset: None)
    try:
        assert [row.label for row in budget.pool.rows()] == ["own"]
        assert budget.row.path.exists() and workers.ROW_LOCK_OFFSET not in probed
    finally:
        monkeypatch.undo()
        budget.release(grant)


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="the stage Job is Windows only")
def test_a_stage_job_keeps_the_trees_committed_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workers, "holding", lambda: True)
    process = subprocess.Popen([sys.executable, "-c", "b = bytearray(200 * 2 ** 20)"])
    job = workers.StageJob(process)
    process.wait()
    job.close()
    assert workers.take_peak() >= 200 * workers.MIB
    assert workers.take_peak() is None


@pytest.mark.unit
def test_only_the_default_asks_one_worker_per_eight_collected_tests() -> None:
    default = stages.Workers(18, "default")
    assert default.capped(None) == default and default.capped(400) == default
    assert default.capped(31) == stages.Workers(4, "default", 18, "31 tests collected, 8 per worker")
    assert default.capped(3).count == workers.FLOOR
    assert stages.Workers(8, "flag").capped(3) == stages.Workers(8, "flag")
    for kept in (stages.Workers(12, stages.SOURCE_HOST), stages.Workers(1, stages.SOURCE_NESTED)):
        assert kept.capped(20) == kept


@pytest.mark.unit
def test_collected_items_reads_pytests_count(tmp_path: Path) -> None:
    def printing(text: str) -> list[str]:
        return [sys.executable, "-c", f"print({text!r})"]

    assert stages.collected_items(printing("a\n1851 tests collected in 1.44s"), tmp_path, os.environ) == 1851
    assert stages.collected_items(printing("12/40 tests collected (28 deselected) in 0.5s"), tmp_path,
                                  os.environ) == 12
    assert stages.collected_items(printing("no tests collected in 0.1s"), tmp_path, os.environ) == 0
    assert stages.collected_items(printing("ERROR"), tmp_path, os.environ) is None


@pytest.mark.unit
def test_the_workers_line_goes_into_the_logs_header_and_names_a_jobserver(monkeypatch: pytest.MonkeyPatch) -> None:
    header = stages.log_header(["pytest"], ".", "private", {stages.WORKERS_LINE: "workers: 4 of 18 (default 18)"})
    assert "\nworkers: 4 of 18 (default 18)\ntime slack: " in header
    assert "workers:" not in stages.log_header(["pytest"], ".", "private", {})
    monkeypatch.setenv("MAKEFLAGS", " -j8 --jobserver-auth=fifo:/tmp/j")
    assert stages.Workers(4, "flag").line() == "workers: 4 (flag; make jobserver present, not joined)"
    monkeypatch.setenv("MAKEFLAGS", "-k")
    assert stages.Workers(4, "flag").line() == "workers: 4 (flag)"


@pytest.mark.integration
def test_the_stages_collect_first_and_note_the_log_and_limit_on_the_row(tmp_path: Path, pool_dir: Path,
                                                                       capsys: pytest.CaptureFixture[str]) -> None:
    budget = workers.Budget(workers.Pool(pool_dir, 6), label="stages")
    seen: list[tuple[list[str], dict, list]] = []

    def fake(command, *, cwd, env, log, private=False) -> int:
        rows = [(row.log, row.limit) for row in budget.pool.rows()]
        own = workers._read_row(budget.row.path)
        seen.append((list(command), dict(env), [(own.log, own.limit), *rows]))
        return 0

    outcome = stages.run_stages(["runner"], [], cwd=tmp_path, stage_list=[Stage("parallel", "a")],
                                env_for=lambda stage: {}, options_for=lambda stage: (),
                                log_for=lambda stage: tmp_path / stage.name, workers=stages.Workers(6, "default"),
                                run=fake, budget=budget, count=lambda command, cwd, env: 9)
    assert outcome.token is None
    command, env, rows = seen[0]
    assert command[command.index("-n") + 1] == "2"
    line = env[stages.WORKERS_LINE]
    assert line.startswith("workers: 2 of 6 (default 6, 9 tests collected, 8 per worker")
    assert rows[0] == (str(tmp_path / "parallel"), line) and line in capsys.readouterr().out


@pytest.mark.integration
@pytest.mark.parametrize("pooled", [False, True])
def test_only_a_pooled_default_stage_collects_first(tmp_path: Path, pool_dir: Path, pooled: bool) -> None:
    budget = workers.Budget(workers.Pool(pool_dir, 6) if pooled else None, label="stages")
    counted: list[list[str]] = []
    seen: list[list[str]] = []

    def count(command, cwd, env) -> int:
        counted.append(list(command))
        return 9

    def fake(command, *, cwd, env, log, private=False) -> int:
        seen.append(list(command))
        return 0

    for chosen in (stages.Workers(6, "default"), stages.Workers(6, stages.SOURCE_HOST)):
        outcome = stages.run_stages(["runner"], [], cwd=tmp_path, stage_list=[Stage("parallel", "a")],
                                    env_for=lambda stage: {}, options_for=lambda stage: (),
                                    log_for=lambda stage: tmp_path / stage.name, workers=chosen, run=fake,
                                    budget=budget, count=count)
        assert outcome.token is None
    assert len(counted) == (1 if pooled else 0)
    assert [command[command.index("-n") + 1] for command in seen] == (["2", "6"] if pooled else ["6", "6"])

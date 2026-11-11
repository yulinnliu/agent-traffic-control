"""docs/TESTKIT.md § Pre-push gate."""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from atc_testkit import evidence, gate, selection, stages
from atc_testkit.project import Project, StaticCheck

ZERO = "0" * 40
ARGV = ["python", "scripts/integration_verify.py"]


def git(root: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=root, env=env,
                          capture_output=True, text=True, check=True).stdout.strip()


def select(paths: Sequence[str], _deltas) -> selection.Selection:
    chosen = selection.Selection(("tests", "more"))
    for path in paths:
        if path == "broken.py":
            raise ValueError("selection broke")
        if path.endswith(".py"):
            chosen.add(path, {"tests"}, "python")
        else:
            chosen.add(path, (), "not a test input")
    return chosen


class Recorder:
    def __init__(self, **codes: int):
        self.codes, self.calls = codes, []

    def __call__(self, command, *, cwd, env, log, private=False):
        self.calls.append((list(command), Path(log).name, env, private))
        for name, code in self.codes.items():
            if name in " ".join(map(str, command)) or name == Path(log).stem:
                return code
        return 0

    @property
    def logs(self) -> list[str]:
        return [call[1] for call in self.calls]


@pytest.fixture()
def repo(tmp_path: Path, atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "base.md").write_text("x\n", encoding="utf-8")
    (root / ".gitignore").write_text(".tmp/\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts/integration-verify.json").write_text(json.dumps(ARGV), encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    return root


def commit(root: Path, name: str) -> str:
    (root / name).write_text("y\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", name)
    return git(root, "rev-parse", "HEAD")


def project(root: Path, **options) -> Project:
    return Project(root=root, argv_file=root / "scripts/integration-verify.json", select=select,
                   missing_remote=options.pop("missing_remote", "full"), runner=("runner",), **options)


def push_line(root: Path, base: str, sha: str) -> str:
    return f"refs/heads/topic {sha} refs/heads/topic {base}\n"


def test_paths_prints_the_selection_and_runs_nothing(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    recorder = Recorder()
    assert gate.main(project(repo), ["--paths", "a.py"], run=recorder) == 0
    assert "targeted: tests" in capsys.readouterr().out and recorder.calls == []


def test_a_mode_is_required(repo: Path) -> None:
    with pytest.raises(SystemExit):
        gate.main(project(repo), [])


def test_a_selection_failure_aborts_the_push_with_exit_two(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert gate.main(project(repo), ["--paths", "broken.py"]) == 2
    assert "test selection failed" in capsys.readouterr().err


def test_a_push_of_another_commit_than_the_checkout_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    git(repo, "checkout", "-q", base)
    recorder = Recorder()
    assert gate.main(project(repo), ["--run"], [push_line(repo, base, sha)], recorder) == 2
    assert "check out the commit being pushed" in capsys.readouterr().err and recorder.calls == []


def test_a_dirty_checkout_is_refused(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    (repo / "a.py").write_text("changed\n", encoding="utf-8")
    assert gate.main(project(repo), ["--run"], [push_line(repo, base, sha)], Recorder()) == 2


def test_no_test_input_runs_nothing(repo: Path, capsys: pytest.CaptureFixture[str], atc_tmp: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.md")
    recorder = Recorder()
    assert gate.main(project(repo), ["--run"], [push_line(repo, base, sha)], recorder) == 0
    assert recorder.calls == [] and "skip: no test inputs" in capsys.readouterr().out


def stale_logs(repo: Path) -> tuple[Path, Path]:
    logs = repo / ".tmp"
    logs.mkdir(exist_ok=True)
    (logs / "other.txt").write_text("kept", encoding="utf-8")
    (logs / "prepush-serial.log").write_text("an earlier run", encoding="utf-8")
    return logs / "prepush-serial.log", logs / "other.txt"


@pytest.fixture()
def linked_logs(repo: Path, tmp_path: Path):
    """``.tmp`` as a link to a directory that holds a log of another owner."""
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "prepush-serial.log").write_text("not this checkout's", encoding="utf-8")
    link = repo / ".tmp"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    yield target
    os.rmdir(link) if os.name == "nt" else link.unlink()


@pytest.mark.parametrize("name", ["a.py", "a.md"])
def test_a_linked_log_directory_stops_the_run_and_stays_untouched(repo: Path, linked_logs: Path, name: str,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, name)
    recorder = Recorder()
    assert gate.main(project(repo), ["--run"], [push_line(repo, base, sha)], recorder) == 9
    assert capsys.readouterr().err.splitlines()[0].startswith("LOG_DIRECTORY_UNSUITABLE ")
    assert recorder.calls == [] and [path.name for path in linked_logs.iterdir()] == ["prepush-serial.log"]
    assert (linked_logs / "prepush-serial.log").read_text(encoding="utf-8") == "not this checkout's"


def test_a_file_as_the_log_directory_stops_the_run(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / ".tmp").write_text("x", encoding="utf-8")
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    assert gate.main(project(repo), ["--run"], [push_line(repo, base, sha)], Recorder()) == 9
    assert capsys.readouterr().err.startswith("LOG_DIRECTORY_UNSUITABLE ")


def test_printing_a_selection_never_reads_the_log_directory(repo: Path, linked_logs: Path) -> None:
    assert gate.main(project(repo), ["--paths", "a.py"]) == 0


def test_a_run_clears_the_logs_of_the_earlier_run(repo: Path) -> None:
    stale, other = stale_logs(repo)
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    assert gate.main(project(repo), ["--run"], [push_line(repo, base, sha)], Recorder(**{"prepush-parallel": 1})) == 1
    assert not stale.exists() and other.read_text(encoding="utf-8") == "kept"


def test_a_run_with_nothing_to_run_clears_them_too(repo: Path) -> None:
    stale, _other = stale_logs(repo)
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.md")
    assert gate.main(project(repo), ["--run"], [push_line(repo, base, sha)], Recorder()) == 0
    assert not stale.exists()


def test_printing_a_selection_keeps_them(repo: Path) -> None:
    stale, _other = stale_logs(repo)
    assert gate.main(project(repo), ["--paths", "a.py"]) == 0
    assert stale.exists()


def test_a_log_that_cannot_go_is_named(repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    stale, _other = stale_logs(repo)

    def locked(_path):
        raise PermissionError("in use")
    monkeypatch.setattr(Path, "unlink", locked)
    gate._clear_logs(repo / ".tmp")
    assert str(stale) in capsys.readouterr().err


def test_no_test_input_leaves_the_root_unread(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.md")
    monkeypatch.setenv("ATC_TMP", "relative")
    assert gate.main(project(repo), ["--run"], [push_line(repo, base, sha)], Recorder()) == 0


def test_stages_run_when_no_record_covers_the_push(repo: Path, capsys: pytest.CaptureFixture[str], atc_tmp: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    recorder = Recorder()
    assert gate.main(project(repo), ["--run"], [push_line(repo, base, sha)], recorder) == 0
    assert "pre-push: tests RUN (evidence: NO_RECORD)" in capsys.readouterr().out
    assert recorder.logs == ["prepush-parallel.log", "prepush-serial.log", "prepush-interactive.log"]
    assert [call[3] for call in recorder.calls] == [True, True, False]
    assert recorder.calls[0][0][0] == "runner" and recorder.calls[0][0][-1] == "tests"
    assert [path for path in (atc_tmp / "systmp").iterdir()] == [], "the passing run's working directory is removed"


@pytest.mark.integration
def test_the_worker_flag_sets_the_parallel_stage(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    recorder = Recorder()
    assert gate.main(project(repo), ["--run", "--workers", "3"], [push_line(repo, base, sha)], recorder) == 0
    command = recorder.calls[0][0]
    assert command[command.index("-n") + 1] == "3" and "workers: 3 (flag)" in capsys.readouterr().out


@pytest.mark.integration
@pytest.mark.parametrize(("value", "accepted"), [("4", True), ("8", True), ("9", False)])
def test_the_worker_flag_goes_to_eight_on_a_two_cpu_host(repo: Path, monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str], value: str,
                                                         accepted: bool) -> None:
    """On CI, outside the host pool, a flag runs whole; locally the pool caps it (test_testkit_workers)."""
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    monkeypatch.setenv("CI", "1")
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    recorder = Recorder()
    arguments = ["--run", "--workers", value]
    if accepted:
        assert gate.main(project(repo), arguments, [push_line(repo, base, sha)], recorder) == 0
        assert f"workers: {value} (flag)" in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit) as stopped:
            gate.main(project(repo), arguments, [])
        assert stopped.value.code == 2 and "takes an integer from 1 to 8," in capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.parametrize("value", ["0", "100000", "9" * 5000, "\u0661\u0666"])
def test_a_bad_worker_flag_is_a_usage_error(repo: Path, value: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        gate.main(project(repo), ["--run", "--workers", value], [])
    assert stopped.value.code == 2


@pytest.mark.integration
def test_a_bad_host_file_stops_the_run_before_any_check(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    host = tmp_path / "config.toml"
    host.write_text("test_workers = 0\n", encoding="utf-8")
    monkeypatch.setattr(stages, "host_config", lambda: host)
    monkeypatch.delenv("CI", raising=False)
    base = git(repo, "rev-parse", "HEAD")
    recorder = Recorder()
    assert gate.main(project(repo), ["--run"], [push_line(repo, base, commit(repo, "a.py"))], recorder) == 9
    assert capsys.readouterr().err.startswith("ATC_CONFIG_INVALID ") and recorder.calls == []


def test_each_stage_environment_is_isolated(repo: Path, atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAMI_X", "1")
    monkeypatch.setenv("BINANCE_API_KEY", "1")
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    recorder = Recorder()
    assert gate.main(project(repo, data_roots=("QUANT_DATA_ROOT",), ignore=("tests/x.py",)), ["--run"],
                     [push_line(repo, base, sha)], recorder) == 0
    env = recorder.calls[0][2]
    assert "HAMI_X" not in env and "BINANCE_API_KEY" not in env and "HAMI_X" not in os.environ
    assert Path(env["TEMP"]).parent == atc_tmp / "systmp" and env["ATC_TMP"] == str(atc_tmp)
    assert Path(env["HOME"]).is_relative_to(env["TEMP"])
    assert env["QUANT_DATA_ROOT"] != recorder.calls[1][2]["QUANT_DATA_ROOT"]
    command = recorder.calls[0][0]
    assert "--ignore=tests/x.py" in command
    assert Path(command[command.index("--basetemp") + 1]).is_relative_to(env["TEMP"])


def test_a_failing_stage_keeps_its_exit_code_names_the_log_and_keeps_the_directory(
        repo: Path, capsys: pytest.CaptureFixture[str], atc_tmp: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    code = gate.main(project(repo), ["--run"], [push_line(repo, base, sha)], Recorder(**{"prepush-serial": 3}))
    err = capsys.readouterr().err
    assert code == 3 and "TESTS_FAILED in the serial stage" in err and "prepush-serial.log" in err
    assert "working directory kept" in err and list((atc_tmp / "systmp").iterdir())


def test_a_push_that_collects_no_test_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    code = gate.main(project(repo), ["--run"], [push_line(repo, base, sha)],
                     Recorder(**{"prepush-parallel": 5, "prepush-serial": 5, "prepush-interactive": 5}))
    assert code == 5 and "NO_TESTS_COLLECTED" in capsys.readouterr().err


def test_static_checks_follow_their_path_condition_and_a_failure_stops_the_push(
        repo: Path, capsys: pytest.CaptureFixture[str], atc_tmp: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    checks = (StaticCheck("doc", "DOC_FAILED", ("doc-tool",), applies=lambda paths: any(p.endswith(".md") for p in paths)),
              StaticCheck("lint", "LINT_FAILED", ("lint-tool",)))
    recorder = Recorder(**{"lint-tool": 4})
    code = gate.main(project(repo, checks=checks), ["--run"], [push_line(repo, base, sha)], recorder)
    assert code == 4 and recorder.logs == ["prepush-lint.log"]
    assert "LINT_FAILED" in capsys.readouterr().err


def test_static_checks_run_on_an_unknown_range(repo: Path) -> None:
    check = StaticCheck("doc", "DOC_FAILED", ("doc-tool",), applies=lambda paths: paths is None)
    recorder = Recorder()
    unknown = f"refs/heads/topic {git(repo, 'rev-parse', 'HEAD')} refs/heads/topic {'a' * 40}\n"
    assert gate.main(project(repo, checks=(check,)), ["--run"], [unknown], recorder) == 0
    assert recorder.logs[0] == "prepush-doc.log" and "prepush-parallel.log" in recorder.logs


def write_record(repo: Path, commit_sha: str, stages_list, *, selector=None) -> None:
    report = evidence.build_report(repo, [], stages_list)
    if selector:
        report["selector"] = selector
    record = {"schema": evidence.RECORD_SCHEMA, "candidate_sha": commit_sha, "tree": git(repo, "rev-parse", commit_sha + "^{tree}"),
              "clean": True, "verify_argv": ARGV, "verified_at": "t", "platform": evidence.platform_record(), "report": report}
    place = repo / ".git/atc/verification-evidence"
    place.mkdir(parents=True, exist_ok=True)
    (place / f"{commit_sha}.json").write_text(json.dumps(record), encoding="utf-8")


def test_a_record_that_covers_the_push_reuses_it(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    chosen = project(repo)
    write_record(repo, sha, chosen.stage_list())
    recorder = Recorder()
    assert gate.main(chosen, ["--run"], [push_line(repo, base, sha)], recorder) == 0
    assert "tests REUSED" in capsys.readouterr().out and recorder.calls == []


@pytest.mark.parametrize("change", ["selector", "version", "stages"])
def test_a_record_of_another_selector_or_stage_list_covers_nothing(repo: Path, capsys: pytest.CaptureFixture[str], change: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    chosen = project(repo)
    selector = {"selector": {"name": "hami-testkit", "version": evidence.__version__},
                "version": {"name": evidence.SELECTOR_NAME, "version": "0.0.0"}, "stages": None}[change]
    stage_list = chosen.stage_list()[:2] if change == "stages" else chosen.stage_list()
    write_record(repo, sha, stage_list, selector=selector)
    recorder = Recorder()
    assert gate.main(chosen, ["--run"], [push_line(repo, base, sha)], recorder) == 0
    out = capsys.readouterr().out
    assert "tests RUN" in out and ("STAGES_MISMATCH" if change == "stages" else "SELECTOR_MISMATCH") in out
    assert len(recorder.calls) == 3


def test_an_unsuitable_root_stops_before_anything_runs(repo: Path, capsys: pytest.CaptureFixture[str],
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "a.py")
    monkeypatch.setenv("ATC_TMP", "relative")
    recorder = Recorder()
    assert gate.main(project(repo), ["--run"], [push_line(repo, base, sha)], recorder) == 9
    assert "ATC_TMP_UNSUITABLE" in capsys.readouterr().err and recorder.calls == []


def test_pushed_commits_are_unique_and_skip_deletions() -> None:
    assert gate.pushed_commits([f"a {'1' * 40} b {ZERO}", f"a {'1' * 40} b {ZERO}", f"a {ZERO} b {'2' * 40}", ""]) == ["1" * 40]

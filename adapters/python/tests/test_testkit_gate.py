"""docs/TESTKIT.md § Pre-push gate."""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from atc_testkit import evidence, gate, selection
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

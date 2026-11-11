"""`atc integrate` reuses a passing `atc verify` record of the same candidate (GIT_WORKFLOW.md § Integrate;
shared/conformance/verification-evidence/README.md § Candidate record)."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

import git_integrate as gi
from test_git_integrate import (  # noqa: F401  (repo is a fixture)
    OWNER, commit, declare_verifier, evidence_dir, integrate, main_tip, publish, records, repo, run_git, topic,
)

pytestmark = pytest.mark.integration


def counting_verifier(tmp_path: Path) -> tuple[str, Path, Path]:
    """A verifier that writes a /2 report, counts its runs in one file and fails while another exists."""
    runs, failing = tmp_path / "runs.txt", tmp_path / "fail.flag"
    script = (
        "import json,os,pathlib,sys; "
        f"runs=pathlib.Path({str(runs)!r}); runs.write_text(runs.read_text() + 'x' if runs.exists() else 'x'); "
        "report={'schema': 'atc-verification-report/2', 'selector': {'name': 'atc-testkit', 'version': '0.10.0'}, "
        "'selection': {'full': True, 'paths': []}, 'stages': [], 'dependency_inputs': {'uv.lock': 'absent'}, "
        "'time_slack': float(os.environ.get('ATC_TEST_TIME_SLACK') or 2)}; "
        "pathlib.Path(os.environ['ATC_VERIFICATION_REPORT']).write_text(json.dumps(report), encoding='utf-8'); "
        f"sys.exit(1 if pathlib.Path({str(failing)!r}).exists() else 0)"
    )
    return json.dumps([sys.executable, "-c", script]), runs, failing


def run_count(runs: Path) -> int:
    return len(runs.read_text()) if runs.exists() else 0


def verify(repo: Path, reviewed: str, *extra: str, capsys: pytest.CaptureFixture[str] | None = None) -> int:
    """`atc verify`; with ``capsys``, its output is read and dropped so the next command's output stands alone."""
    code = gi.verify_main(["--project", "repo", "--reviewed", reviewed, *extra], root=repo.parent,
                          cwd=str(repo.parent))
    if capsys is not None:
        capsys.readouterr()
    return code


@pytest.fixture
def counted(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.delenv("ATC_TEST_TIME_SLACK", raising=False)
    argv, runs, failing = counting_verifier(tmp_path)
    declare_verifier(repo, argv)
    return runs, failing


def candidate_record(repo: Path, verdict: str, base: str, reviewed: str) -> dict:
    return json.loads(gi.candidate_record_path(repo, verdict, base, reviewed).read_text(encoding="utf-8"))


def test_integrate_reuses_a_passing_verify_of_the_same_candidate(
        repo: Path, counted: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    runs, _failing = counted
    base = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert verify(repo, tip, "--json") == 0
    out = json.loads(capsys.readouterr().out)
    record = candidate_record(repo, "FF", base, tip)
    assert out["candidate_record"] == str(gi.candidate_record_path(repo, "FF", base, tip))
    assert (record["kind"], record["result"], record["clean"], record["candidate_sha"]) == ("verify", "PASS", True, tip)
    assert record["tree"] == run_git(repo, "rev-parse", f"{tip}^{{tree}}") and record["timeout"] == 300
    assert records(repo) == [] and run_count(runs) == 1

    assert integrate(repo, "topic", tip, "--json") == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert run_count(runs) == 1 and main_tip(repo) == tip
    assert (data["verification"], data["verification_reuse"]) == ("PASS", "REUSED")
    assert data["verification_reused"] == out["candidate_record"]
    assert f"verify: candidate {tip} REUSED {out['candidate_record']} (verified {record['verified_at']})" in \
        captured.err.splitlines()
    evidence = json.loads(Path(data["evidence"]).read_text(encoding="utf-8"))
    note = {"record": out["candidate_record"], "candidate_sha": tip, "verified_at": record["verified_at"]}
    assert evidence["reused"] == note and evidence["report"] == record["report"]
    line = json.loads((repo / ".git" / "atc" / "integrations.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["verification_evidence"] == note


def test_the_human_output_names_the_reuse(repo: Path, counted: tuple[Path, Path],
                                         capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    assert verify(repo, tip) == 0
    path = gi.candidate_record_path(repo, "FF", main_tip(repo), tip)
    capsys.readouterr()
    assert integrate(repo, "topic", tip) == 0
    line = next(line for line in capsys.readouterr().err.splitlines() if line.startswith("verify: "))
    assert line.startswith("verify: REUSED ") and line.endswith(str(Path(gi.CANDIDATE_RECORDS) / path.name))


def test_a_merge_candidate_is_reused_when_its_tree_matches(
        repo: Path, counted: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    runs, _failing = counted
    second = topic(repo, {"b.txt": "b2\n"}, branch="topic/next")
    publish(repo, "topic/next")
    base = commit(repo, "c.txt", "c\n")
    assert verify(repo, second, capsys=capsys) == 0
    record = candidate_record(repo, "NO_FF", base, second)
    time.sleep(1.1)  # a later merge commit gets another SHA but the same tree
    assert integrate(repo, "topic/next", second, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert run_count(runs) == 1 and data["verification_reuse"] == "REUSED"
    assert run_git(repo, "rev-parse", f"{data['candidate_sha']}^{{tree}}") == record["tree"]
    evidence = json.loads(Path(data["evidence"]).read_text(encoding="utf-8"))
    assert evidence["reused"]["candidate_sha"] == record["candidate_sha"] != data["candidate_sha"]


def tamper(repo: Path, base: str, tip: str, change) -> None:
    path = gi.candidate_record_path(repo, "FF", base, tip)
    record = json.loads(path.read_text(encoding="utf-8"))
    change(record)
    path.write_text(json.dumps(record), encoding="utf-8")


@pytest.mark.parametrize(("change", "reason"), [
    (lambda r: r.update(kind="integrate"), "SCHEMA_MISMATCH"),
    (lambda r: r.update(report={"schema": "x"}), "SCHEMA_MISMATCH"),
    (lambda r: r.update(clean=False), "NOT_CLEAN"),
    (lambda r: r.update(carried_over_from="f" * 40), "BASE_MISMATCH"),
    (lambda r: r.update(tree="0" * 40), "TREE_MISMATCH"),
    (lambda r: r.update(verify_argv=["other"]), "ARGV_MISMATCH"),
    (lambda r: r.update(platform={"system": "other", "machine": "x"}), "PLATFORM_MISMATCH"),
    (lambda r: r["report"]["dependency_inputs"].update({"uv.lock": "0" * 64}), "DEPENDENCY_MISMATCH"),
    (lambda r: r["report"].update(schema="atc-verification-report/1", time_slack=None), "TIME_SLACK_MISMATCH"),
])
def test_a_mismatched_record_runs_the_verifier(repo: Path, counted: tuple[Path, Path],
                                               capsys: pytest.CaptureFixture[str], change, reason: str) -> None:
    runs, _failing = counted
    base = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert verify(repo, tip) == 0
    tamper(repo, base, tip, change)
    capsys.readouterr()
    assert integrate(repo, "topic", tip, "--json") == 0
    captured = capsys.readouterr()
    assert run_count(runs) == 2 and json.loads(captured.out)["verification_reuse"] == reason
    assert f"verify: candidate {tip} RUN (evidence: {reason}) timeout 300s (default)" in captured.err.splitlines()


def test_an_unreadable_record_runs_the_verifier(repo: Path, counted: tuple[Path, Path],
                                                capsys: pytest.CaptureFixture[str]) -> None:
    runs, _failing = counted
    tip = topic(repo, {"a.txt": "a2\n"})
    assert verify(repo, tip) == 0
    gi.candidate_record_path(repo, "FF", main_tip(repo), tip).write_text("{", encoding="utf-8")
    capsys.readouterr()
    assert integrate(repo, "topic", tip, "--json") == 0
    assert run_count(runs) == 2 and json.loads(capsys.readouterr().out)["verification_reuse"] == "UNREADABLE"


def test_another_timeout_or_slack_or_vendored_selector_runs_the_verifier(
        repo: Path, counted: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    runs, _failing = counted
    tip = topic(repo, {"a.txt": "a2\n"})
    assert verify(repo, tip, capsys=capsys) == 0
    assert integrate(repo, "topic", tip, "--json", "--verify-timeout", "200") == 0
    assert json.loads(capsys.readouterr().out)["verification_reuse"] == "TIMEOUT_MISMATCH"

    second = topic(repo, {"b.txt": "b2\n"}, branch="second")
    assert verify(repo, second, capsys=capsys) == 0
    monkeypatch.setenv("ATC_TEST_TIME_SLACK", "5")
    assert integrate(repo, "second", second, "--json") == 0
    assert json.loads(capsys.readouterr().out)["verification_reuse"] == "TIME_SLACK_MISMATCH"
    monkeypatch.delenv("ATC_TEST_TIME_SLACK")

    (repo / "vendor").mkdir()
    third = topic(repo, {"vendor/atc-testkit.json": json.dumps({"version": "0.9.9"})}, branch="third")
    assert verify(repo, third, capsys=capsys) == 0
    assert integrate(repo, "third", third, "--json") == 0
    assert json.loads(capsys.readouterr().out)["verification_reuse"] == "SELECTOR_MISMATCH"
    assert run_count(runs) == 6


def test_only_the_latest_result_counts(repo: Path, counted: tuple[Path, Path],
                                       capsys: pytest.CaptureFixture[str]) -> None:
    runs, failing = counted
    base = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert verify(repo, tip) == 0
    failing.write_text("x")
    assert verify(repo, tip) == 3
    assert candidate_record(repo, "FF", base, tip)["result"] == "FAIL"
    failing.unlink()
    capsys.readouterr()
    assert integrate(repo, "topic", tip, "--json") == 0
    assert run_count(runs) == 3 and json.loads(capsys.readouterr().out)["verification_reuse"] == "NOT_PASS"


def test_a_moved_main_finds_no_record(repo: Path, counted: tuple[Path, Path],
                                      capsys: pytest.CaptureFixture[str]) -> None:
    runs, _failing = counted
    tip = topic(repo, {"a.txt": "a2\n"})
    publish(repo, "topic")
    assert verify(repo, tip) == 0
    commit(repo, "c.txt", "c\n")
    capsys.readouterr()
    assert integrate(repo, "topic", tip, "--json") == 0
    assert run_count(runs) == 2 and json.loads(capsys.readouterr().out)["verification_reuse"] == "NO_RECORD"


def test_verify_takes_the_task_and_timeout_of_the_integrate_command(
        repo: Path, counted: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    runs, _failing = counted
    base = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert verify(repo, tip, "--task", "some task", "--verify-timeout", "200") == 0
    record = candidate_record(repo, "FF", base, tip)
    assert (record["task"], record["tasks"], record["timeout"]) == ("some task", ["some task"], 200)
    capsys.readouterr()
    assert gi.main(["--project", "repo", "--reviewed", tip, "--task", "other task", "--owner-words", OWNER,
                    "--verify-timeout", "200", "--json"], root=repo.parent, cwd=str(repo.parent)) == 0
    assert run_count(runs) == 1 and json.loads(capsys.readouterr().out)["verification_reuse"] == "REUSED"


def test_verify_without_a_report_or_for_records_only_writes_no_record(repo: Path, tmp_path: Path) -> None:
    declare_verifier(repo, json.dumps([sys.executable, "-c", "pass"]))
    base = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert verify(repo, tip) == 0
    assert not gi.candidate_record_path(repo, "FF", base, tip).exists()


def test_the_gate_never_reads_a_verify_record_and_old_ones_expire(repo: Path, counted: tuple[Path, Path]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    stale = evidence_dir(repo) / gi.CANDIDATE_RECORDS / "FF-old-old.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    old = time.time() - gi.EVIDENCE_RETENTION_SECONDS - 60
    os.utime(stale, (old, old))
    assert verify(repo, tip) == 0
    assert not stale.exists()
    assert records(repo) == [] and not (evidence_dir(repo) / f"{tip}.json").exists()

"""Evidence reuse refuses everything except the exact verified commit, environment and selection."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest

TESTKIT_SRC = Path(__file__).parents[1] / "atc-testkit/src"
sys.path.insert(0, str(TESTKIT_SRC))

import git_integrate as gi
import atc_testkit
from atc_testkit import evidence as ev
from atc_testkit.git import SelectionError

ARGV_FILE = "scripts/integration-verify.json"
STAGES = (ev.Stage("parallel", "not slow and not serial"), ev.Stage("serial", "serial and not slow", ("tests/x.py",)))
A, B = "packages/a/tests", "packages/b/tests"


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode:
        raise SelectionError(result.stderr.strip())
    return result.stdout.strip()


def commit(repo: Path, path: str, content: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_git(repo, "add", "--", path)
    run_git(repo, "commit", "-q", "-m", f"edit {path}")
    return run_git(repo, "rev-parse", "HEAD")


def verifier_argv() -> list[str]:
    """A verifier that reports the test directories of the packages its range changed."""
    script = (
        "import os, subprocess, sys; "
        f"sys.path.insert(0, {str(TESTKIT_SRC)!r}); "
        "from pathlib import Path; "
        "from atc_testkit import evidence as ev; "
        "changed = subprocess.check_output(['git', 'diff', '--name-only', "
        "os.environ['ATC_EXPECTED_MAIN_SHA'] + '..' + os.environ['ATC_INTEGRATION_SHA']], text=True).split(); "
        "paths = sorted({'/'.join(p.split('/')[:2]) + '/tests' for p in changed if p.startswith('packages/')}); "
        "stages = (ev.Stage('parallel', 'not slow and not serial'), "
        "ev.Stage('serial', 'serial and not slow', ('tests/x.py',))); "
        "ev.write_report(os.environ, Path.cwd(), paths or None, stages, classification='impact-selected')"
    )
    return [sys.executable, "-c", script]


def isolate(base: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The Git identity and configuration and the temporary directory a repository here is built under."""
    for key in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(key)
    for key, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    }.items():
        monkeypatch.setenv(key, value)
    scratch = base / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    return base / "repo"


def build(root: Path) -> Path:
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit(root, "uv.lock", "lock 1\n")
    commit(root, ".python-version", "3.13\n")
    commit(root, ARGV_FILE, json.dumps(verifier_argv()))
    commit(root, "AGENTS.md", f"# AGENTS.md\n\n## Integration verification\n\n- Verifier argv file: {ARGV_FILE}\n")
    return root


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return build(isolate(tmp_path, monkeypatch))


def topic(repo: Path, edits: dict[str, str], branch: str) -> str:
    run_git(repo, "switch", "-q", "-c", branch)
    for path, content in edits.items():
        commit(repo, path, content)
    tip = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "switch", "-q", "main")
    return tip


def workspace_beside(repo: Path) -> Path:
    """A workspace.yaml beside ``repo`` registering it under its directory name; returns that workspace root."""
    (repo.parent / "workspace.yaml").write_text(
        f"version: 1\nworkspace_root: '{repo.parent}'\nprojects:\n  {repo.name}:\n    repo: {repo.name}\n"
        f"    worktrees: worktrees/{repo.name}\n    state: state/{repo.name}-data\n",
        encoding="utf-8",
    )
    return repo.parent


def integrate(repo: Path, branch: str, reviewed: str) -> None:
    """`atc integrate` of ``reviewed``, the tip of ``branch``, the one local branch there."""
    assert gi.main([
        "--project", repo.name, "--reviewed", reviewed, "--task", "test", "--owner-words", "Owner: integrate",
    ], root=workspace_beside(repo)) == 0


def decide(repo: Path, pushed: str, paths, stages=STAGES) -> ev.Decision:
    return ev.check(lambda *args: run_git(repo, *args), repo, pushed, paths, stages, repo / ARGV_FILE)


def record_path(repo: Path, sha: str) -> Path:
    return repo / ".git" / "atc" / "verification-evidence" / f"{sha}.json"


@pytest.fixture(scope="module")
def verified_template(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    """A repository after one fast-forward integration whose verifier ran the tests of package a, built once."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        root = build(isolate(tmp_path_factory.mktemp("verified"), monkeypatch))
        tip = topic(root, {"packages/a/src/x.py": "x = 1\n"}, "first")
        integrate(root, "first", tip)
    return root, tip


@pytest.fixture
def verified(repo: Path, verified_template: tuple[Path, str]) -> str:
    """``repo`` replaced by a copy of the template at the same path; the record names commits, not paths."""
    template, tip = verified_template
    shutil.rmtree(repo, onexc=lambda function, path, _error: (os.chmod(path, stat.S_IWRITE), function(path)))
    shutil.copytree(template, repo, symlinks=True)
    return tip


def rewrite(repo: Path, sha: str, change) -> None:
    path = record_path(repo, sha)
    record = json.loads(path.read_text(encoding="utf-8"))
    change(record)
    path.write_text(json.dumps(record), encoding="utf-8")


def test_integrated_commit_reuses_its_record(repo: Path, verified: str) -> None:
    decision = decide(repo, verified, [A])
    assert (decision.reuse, decision.reason, decision.path) == (True, None, record_path(repo, verified))
    line = ev.describe(decision)
    assert line.startswith(f"pre-push: tests REUSED (atc integrate evidence, candidate {verified}, verified 20")
    assert decide(repo, verified, [A + "/test_x.py"]).reuse
    assert decide(repo, verified, None).reuse


def test_recorded_python_version_is_not_compared(repo: Path, verified: str) -> None:
    rewrite(repo, verified, lambda record: record["report"].update(python="0.0.0"))
    assert decide(repo, verified, [A]).reuse


def test_merge_result_does_not_reuse_the_reviewed_tip_record(repo: Path) -> None:
    tip = topic(repo, {"packages/a/src/x.py": "x = 1\n"}, "first")
    # A published topic integrates as a merge.
    origin = repo.parent / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], capture_output=True, check=True)
    run_git(repo, "remote", "add", "origin", str(origin))
    run_git(repo, "push", "-q", "origin", "first")
    commit(repo, "packages/b/src/y.py", "y = 1\n")
    integrate(repo, "first", tip)
    merged = run_git(repo, "rev-parse", "main")
    assert merged != tip and decide(repo, merged, [A]).reuse
    assert decide(repo, tip, [A]).reason == "NO_RECORD"
    record_path(repo, tip).write_bytes(record_path(repo, merged).read_bytes())
    assert decide(repo, tip, [A]).reason == "COMMIT_MISMATCH"
    # A record of the reviewed tip, were one written, names a different commit and tree than the merge.
    rewrite(repo, merged, lambda record: record.update(
        candidate_sha=tip, tree=run_git(repo, "rev-parse", f"{tip}^{{tree}}")))
    assert decide(repo, merged, [A]).reason == "COMMIT_MISMATCH"


def test_amended_commit_with_the_same_tree_does_not_reuse(repo: Path, verified: str) -> None:
    run_git(repo, "commit", "-q", "--amend", "-m", "reworded")
    amended = run_git(repo, "rev-parse", "HEAD")
    assert amended != verified
    assert run_git(repo, "rev-parse", "HEAD^{tree}") == run_git(repo, "rev-parse", f"{verified}^{{tree}}")
    assert decide(repo, amended, [A]).reason == "NO_RECORD"
    record_path(repo, amended).write_bytes(record_path(repo, verified).read_bytes())
    assert decide(repo, amended, [A]).reason == "COMMIT_MISMATCH"


def test_record_with_another_tree_does_not_reuse(repo: Path, verified: str) -> None:
    rewrite(repo, verified, lambda record: record.update(tree=run_git(repo, "rev-parse", "HEAD~1^{tree}")))
    assert decide(repo, verified, [A]).reason == "TREE_MISMATCH"


def test_two_unpushed_integrations_do_not_reuse_the_last_record(repo: Path, verified: str) -> None:
    second = topic(repo, {"packages/b/src/y.py": "y = 1\n"}, "second")
    integrate(repo, "second", second)
    assert run_git(repo, "rev-parse", "main") == second
    # The push range origin/main..main spans both integrations; the last verifier ran package b only.
    decision = decide(repo, second, [A, B])
    assert (decision.reuse, decision.reason) == (False, "SELECTION_NOT_COVERED")
    assert ev.describe(decision) == "pre-push: tests RUN (evidence: SELECTION_NOT_COVERED)"
    assert decide(repo, second, [B]).reuse


def test_full_selection_is_not_covered_by_a_targeted_record(repo: Path, verified: str) -> None:
    assert decide(repo, verified, []).reason == "SELECTION_NOT_COVERED"
    rewrite(repo, verified, lambda record: record["report"].update(selection={"full": True, "paths": []}))
    assert decide(repo, verified, []).reuse and decide(repo, verified, [A, B]).reuse


def test_record_from_another_platform_does_not_reuse(repo: Path, verified: str,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    other = "linux" if sys.platform == "win32" else "win32"
    monkeypatch.setattr(ev.sys, "platform", other)
    assert decide(repo, verified, [A]).reason == "PLATFORM_MISMATCH"
    monkeypatch.undo()
    rewrite(repo, verified, lambda record: record["platform"].update(machine="other"))
    assert decide(repo, verified, [A]).reason == "PLATFORM_MISMATCH"


@pytest.mark.parametrize("name", ev.default_dependency_files())
def test_changed_dependency_input_does_not_reuse(repo: Path, verified: str, name: str) -> None:
    (repo / name).write_text("changed\n", encoding="utf-8")
    assert decide(repo, verified, [A]).reason == "DEPENDENCY_MISMATCH"
    (repo / name).unlink()
    assert decide(repo, verified, [A]).reason == "DEPENDENCY_MISMATCH"


def test_record_without_dependency_inputs_does_not_reuse(repo: Path, verified: str) -> None:
    rewrite(repo, verified, lambda record: record["report"].update(dependency_inputs={}))
    assert decide(repo, verified, [A]).reason == "DEPENDENCY_MISMATCH"


def test_inline_verifier_other_than_the_argv_file_does_not_reuse(repo: Path) -> None:
    tip = topic(repo, {"packages/a/src/x.py": "x = 1\n"}, "first")
    integrate(repo, "first", tip)
    assert record_path(repo, tip).is_file()
    rewrite(repo, tip, lambda record: record.__setitem__("verify_argv", [*verifier_argv(), "extra"]))
    assert decide(repo, tip, [A]).reason == "ARGV_MISMATCH"


@pytest.mark.parametrize("content", [None, "not json"])
def test_unreadable_argv_file_does_not_reuse(repo: Path, verified: str, content: str | None) -> None:
    (repo / ARGV_FILE).unlink()
    if content is not None:
        (repo / ARGV_FILE).write_text(content, encoding="utf-8")
    assert decide(repo, verified, [A]).reason == "ARGV_MISMATCH"


def test_other_selector_version_does_not_reuse(repo: Path, verified: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ev, "__version__", "0.4.0")
    assert decide(repo, verified, [A]).reason == "SELECTOR_MISMATCH"
    monkeypatch.undo()
    rewrite(repo, verified, lambda record: record["report"]["selector"].update(name="other"))
    assert decide(repo, verified, [A]).reason == "SELECTOR_MISMATCH"


@pytest.mark.parametrize("stages", [
    STAGES[:1],
    (ev.Stage("parallel", "not slow"), STAGES[1]),
    (STAGES[0], ev.Stage("serial", "serial and not slow")),
], ids=["fewer", "marker", "ignore"])
def test_other_stages_do_not_reuse(repo: Path, verified: str, stages) -> None:
    assert decide(repo, verified, [A], stages).reason == "STAGES_MISMATCH"


@pytest.mark.integration
def test_another_time_slack_does_not_reuse(repo: Path, verified: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert json.loads(record_path(repo, verified).read_text(encoding="utf-8"))["report"]["time_slack"] == 2.0
    monkeypatch.setenv("ATC_TEST_TIME_SLACK", "5")
    assert decide(repo, verified, [A]).reason == "TIME_SLACK_MISMATCH"
    monkeypatch.setenv("ATC_TEST_TIME_SLACK", "2")
    assert decide(repo, verified, [A]).reuse
    rewrite(repo, verified, lambda record: record["report"].update(time_slack=5))
    assert decide(repo, verified, [A]).reason == "TIME_SLACK_MISMATCH"


def test_dirty_candidate_record_does_not_reuse(repo: Path, verified: str) -> None:
    rewrite(repo, verified, lambda record: record.update(clean=False))
    assert decide(repo, verified, [A]).reason == "NOT_CLEAN"


@pytest.mark.parametrize("damage,reason", [
    (lambda raw: raw[: len(raw) // 2], "UNREADABLE"),
    (lambda raw: b"not json", "UNREADABLE"),
    (lambda raw: b"", "UNREADABLE"),
    (lambda raw: b"\xff\xfe", "UNREADABLE"),
    (lambda raw: b"[" + raw + b"]", "UNREADABLE"),
], ids=["truncated", "not-json", "empty", "not-utf8", "not-object"])
def test_damaged_record_does_not_reuse(repo: Path, verified: str, damage, reason: str) -> None:
    path = record_path(repo, verified)
    path.write_bytes(damage(path.read_bytes()))
    decision = decide(repo, verified, [A])
    assert (decision.reuse, decision.reason) == (False, reason)


@pytest.mark.parametrize("change", [
    lambda record: record.update(schema="atc-verification-evidence/3"),
    lambda record: record.pop("schema"),
    lambda record: record.pop("candidate_sha"),
    lambda record: record.pop("tree"),
    lambda record: record.pop("clean"),
    lambda record: record.update(clean="true"),
    lambda record: record.pop("verify_argv"),
    lambda record: record.pop("platform"),
    lambda record: record.pop("report"),
    lambda record: record["report"].pop("selector"),
    lambda record: record["report"].pop("selection"),
    lambda record: record["report"]["selection"].pop("full"),
    lambda record: record["report"]["selection"].update(paths="packages/a/tests"),
    lambda record: record["report"].pop("stages"),
    lambda record: record["report"].pop("dependency_inputs"),
    lambda record: record["report"].update(schema="atc-verification-report/1"),
    lambda record: record["report"].pop("time_slack"),
    lambda record: record["report"].update(time_slack=True),
    lambda record: record["report"].update(time_slack="2"),
], ids=["schema-version", "no-schema", "no-sha", "no-tree", "no-clean", "clean-type", "no-argv", "no-platform",
        "no-report", "no-selector", "no-selection", "no-full", "paths-type", "no-stages", "no-inputs",
        "report-schema-1", "no-slack", "slack-bool", "slack-text"])
def test_record_of_another_shape_does_not_reuse(repo: Path, verified: str, change) -> None:
    rewrite(repo, verified, change)
    assert decide(repo, verified, [A]).reason == "SCHEMA_MISMATCH"


def test_verifier_without_report_leaves_no_record(repo: Path) -> None:
    commit(repo, ARGV_FILE, json.dumps([sys.executable, "-c", "pass"]))
    tip = topic(repo, {"packages/a/src/x.py": "x = 1\n"}, "first")
    integrate(repo, "first", tip)
    assert run_git(repo, "rev-parse", "main") == tip
    decision = decide(repo, tip, [A])
    assert (decision.reuse, decision.reason) == (False, "NO_RECORD")
    assert ev.describe(decision) == "pre-push: tests RUN (evidence: NO_RECORD)"


def test_git_failure_while_checking_does_not_reuse(repo: Path, verified: str) -> None:
    def broken(*args: str) -> str:
        raise SelectionError("git unavailable")
    decision = ev.check(broken, repo, verified, [A], STAGES, repo / ARGV_FILE)
    assert (decision.reuse, decision.reason) == (False, "CHECK_FAILED")

    def no_tree(*args: str) -> str:
        if args[-1].endswith("^{tree}"):
            raise SelectionError("bad object")
        return run_git(repo, *args)
    decision = ev.check(no_tree, repo, verified, [A], STAGES, repo / ARGV_FILE)
    assert (decision.reuse, decision.reason) == (False, "CHECK_FAILED")


def test_check_never_writes(repo: Path, verified: str) -> None:
    directory = record_path(repo, verified).parent
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    for paths in ([A], [A, B], []):
        decide(repo, verified, paths)
    decide(repo, "0" * 40, [A])
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    assert run_git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("recorded,own,expected", [
    ({"full": True, "paths": []}, {"full": True, "paths": []}, True),
    ({"full": True, "paths": []}, {"full": False, "paths": [A]}, True),
    ({"full": False, "paths": [A]}, {"full": True, "paths": []}, False),
    ({"full": False, "paths": [A, B]}, {"full": False, "paths": [B]}, True),
    ({"full": False, "paths": [A]}, {"full": False, "paths": [A, B]}, False),
    ({"full": False, "paths": [A]}, {"full": False, "paths": [A + "/test_x.py"]}, True),
    ({"full": False, "paths": [A + "/test_x.py"]}, {"full": False, "paths": [A]}, False),
    ({"full": False, "paths": [A]}, {"full": False, "paths": [A + "_more/test_x.py"]}, False),
    ({"full": False, "paths": [A + "/"]}, {"full": False, "paths": [A.replace("/", "\\") + "\\test_x.py"]}, True),
    ({"full": False, "paths": []}, {"full": False, "paths": []}, True),
    ({"full": False, "paths": []}, {"full": False, "paths": [A]}, False),
])
def test_covers(recorded, own, expected: bool) -> None:
    assert ev.covers(recorded, own) is expected


def test_selection_record() -> None:
    assert ev.selection_record(None) == {"full": False, "paths": []}
    assert ev.selection_record([]) == {"full": True, "paths": []}
    assert ev.selection_record(["packages\\a\\tests", B]) == {"full": False, "paths": [A, B]}


def test_report_carries_the_compared_fields(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_bytes(b"lock\n")
    target = tmp_path / "out" / "report.json"
    target.parent.mkdir()
    assert ev.write_report({}, tmp_path, [A], STAGES) is None
    assert ev.write_report({ev.REPORT_ENV: str(target)}, tmp_path, [A], STAGES, checks=["RUFF"]) == target
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report.pop("python").count(".") == 2
    assert report == {
        "schema": "atc-verification-report/2",
        "selector": {"name": "atc-testkit", "version": atc_testkit.__version__},
        "selection": {"full": False, "paths": [A]},
        "stages": [{"name": "parallel", "marker": "not slow and not serial", "ignore": []},
                   {"name": "serial", "marker": "serial and not slow", "ignore": ["tests/x.py"]}],
        "dependency_inputs": {
            "uv.lock": "d8c9f2728aa278ebcd33ccedf3ad309a866870ad5fb93a03526b4b7655c9e911",
            ".python-version": "absent",
        },
        "time_slack": 2.0,
        "checks": ["RUFF"],
    }
    assert gi._valid_report(report)


@pytest.mark.unit
def test_the_report_records_the_slack_the_environment_sets(tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    ev.write_report({ev.REPORT_ENV: str(target), "ATC_TEST_TIME_SLACK": "5"}, tmp_path, [A], STAGES)
    assert json.loads(target.read_text(encoding="utf-8"))["time_slack"] == 5.0


def test_module_version_matches_the_package() -> None:
    assert f"`atc-testkit` {atc_testkit.__version__};" in (TESTKIT_SRC.parent / "README.md").read_text(encoding="utf-8")
    assert (ev.REPORT_SCHEMA, ev.RECORD_SCHEMA) == (gi.REPORT_SCHEMAS[-1], gi.EVIDENCE_SCHEMA)
    assert ev.EVIDENCE_DIR == gi.EVIDENCE_DIR

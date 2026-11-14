"""docs/TESTKIT.md § Integration verifier: one test per row, both forms, and the walk from ``atc integrate``."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

import git_integrate as gi
from atc_testkit import selection, stages, verifier
from atc_testkit.project import Project, StaticCheck

PYPROJECT = """[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
[tool.atc-testkit]
product_module_prefixes = ["fixture_prod"]
automation_markdown_prefixes = [".claude/"]
data_root_variables = ["QUANT_DATA_ROOT"]
credential_variables = ["NAMED_CREDENTIAL"]
lint_scope = ["src"]
"""


def git_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false", *args],
                          cwd=root, env=git_env(), capture_output=True, text=True, check=True).stdout.strip()


def select(paths: Sequence[str], _deltas) -> selection.Selection:
    chosen = selection.Selection(("tests",))
    for path in paths:
        if path == "broken.py":
            raise ValueError("selection broke")
        if path.endswith(".py"):
            chosen.add(path, {"tests"}, "python")
        else:
            chosen.add(path, (), "other")
    return chosen


class Recorder:
    """Git runs for real; every other command returns the code set for a word in its argv or log name."""

    def __init__(self, **codes: int):
        self.codes, self.calls, self.cwds = codes, [], []

    def __call__(self, command, *, cwd, env, log, private=False):
        command = [str(item) for item in command]
        self.calls.append((command, Path(log).name, env, private))
        self.cwds.append(Path(cwd))
        log.parent.mkdir(parents=True, exist_ok=True)
        for word, code in self.codes.items():
            if word == Path(log).stem or word in command:
                log.write_text("FAILED one\nnoise\n", encoding="utf-8")
                return code
        if command[0] == "git":
            return subprocess.run(command, cwd=cwd, env=git_env(), capture_output=True).returncode
        return 0

    @property
    def logs(self) -> list[str]:
        return [call[1] for call in self.calls]


class Candidate:
    def __init__(self, root: Path):
        self.root = root
        self.main = git(root, "rev-parse", "HEAD")

    def advance(self, path: str = "src/fixture_prod/tool.py", content: str = "x = 1\n") -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode())
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", path)
        return git(self.root, "rev-parse", "HEAD")

    def env(self, **overrides: str) -> dict[str, str]:
        head = git(self.root, "rev-parse", "HEAD")
        return {"ATC_EXPECTED_MAIN_SHA": self.main, "ATC_INTEGRATION_SHA": head, "ATC_CANDIDATE_DIR": str(self.root),
                "ATC_REVIEWED_SOURCE_SHA": head, **overrides}


@pytest.fixture()
def candidate(tmp_path: Path, atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> Candidate:
    root = tmp_path / "candidate"
    (root / "src/fixture_prod").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src/fixture_prod/__init__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "argv.json").write_text("[]", encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    monkeypatch.chdir(root)
    return Candidate(root)


def consumer(root: Path, **options) -> Project:
    return Project(root=root, argv_file=root / "argv.json", select=select, runner=("runner",), **options)


def root_form(root: Path, **options) -> Project:
    defaults = dict(root=root, argv_file=root / "argv.json", product_prefixes=("fixture_prod",), credentials=(), data_roots=(),
                    test_paths=lambda paths: [], runner=("root-runner",),
                    documentation_only=lambda paths: all(p.startswith("docs/") and p.endswith(".md") for p in paths))
    return Project(**{**defaults, **options})


def refused(project: Project, env: dict[str, str], capsys: pytest.CaptureFixture[str], run: Recorder | None = None) -> list[str]:
    code = verifier.main(project, env, run or Recorder())
    assert code == 1
    return capsys.readouterr().err.splitlines()


@pytest.mark.parametrize("name", verifier.REQUIRED_ENV)
def test_each_input_must_be_present(candidate: Candidate, capsys: pytest.CaptureFixture[str], name: str) -> None:
    env = candidate.env()
    del env[name]
    assert refused(consumer(candidate.root), env, capsys)[0] == "VERIFY_ENV_MISSING"


@pytest.mark.parametrize("name", ["ATC_EXPECTED_MAIN_SHA", "ATC_INTEGRATION_SHA", "ATC_REVIEWED_SOURCE_SHA"])
def test_each_sha_input_must_be_a_full_object_id(candidate: Candidate, capsys: pytest.CaptureFixture[str], name: str) -> None:
    assert refused(consumer(candidate.root), candidate.env(**{name: "abc"}), capsys)[0] == "VERIFY_ENV_INVALID"


def test_the_candidate_directory_must_be_the_tree_and_working_directory(
        candidate: Candidate, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = consumer(candidate.root)
    assert refused(project, candidate.env(ATC_CANDIDATE_DIR=str(tmp_path)), capsys)[0] == "CANDIDATE_DIR_MISMATCH"
    monkeypatch.chdir(tmp_path)
    assert refused(project, candidate.env(), capsys)[0] == "CANDIDATE_DIR_MISMATCH"


def test_head_must_be_the_integration_sha(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    assert refused(consumer(candidate.root), candidate.env(ATC_INTEGRATION_SHA="a" * 40), capsys)[0] == "CANDIDATE_HEAD_MISMATCH"


@pytest.mark.parametrize(("name", "token"), [("ATC_EXPECTED_MAIN_SHA", "EXPECTED_MAIN_NOT_IN_CANDIDATE"),
                                             ("ATC_REVIEWED_SOURCE_SHA", "REVIEWED_SOURCE_NOT_IN_CANDIDATE")])
def test_the_ancestry_inputs(candidate: Candidate, capsys: pytest.CaptureFixture[str], name: str, token: str) -> None:
    candidate.advance()
    assert refused(consumer(candidate.root), candidate.env(**{name: "a" * 40}), capsys)[0] == token


def test_a_product_module_outside_the_candidate_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str],
                                                           tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "fixture_prod_other.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(outside))
    candidate.advance()
    lines = refused(consumer(candidate.root), candidate.env(), capsys)
    assert lines[0] == "CANDIDATE_IMPORT_MISMATCH" and "fixture_prod_other" in lines[1]


def test_a_product_module_the_tree_defines_must_import(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    (candidate.root / "pyproject.toml").write_text(PYPROJECT.replace('pythonpath = ["src"]', ""), encoding="utf-8")
    candidate.advance()
    assert refused(consumer(candidate.root), candidate.env(), capsys)[0] == "CANDIDATE_IMPORT_MISMATCH"


def test_every_source_root_of_a_namespace_must_be_active(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    for package in ("one", "two"):
        (candidate.root / f"packages/{package}/src/fixture_prod_space").mkdir(parents=True)
        (candidate.root / f"packages/{package}/src/fixture_prod_space/mod.py").write_text("", encoding="utf-8")
    (candidate.root / "pyproject.toml").write_text(
        PYPROJECT.replace('pythonpath = ["src"]', 'pythonpath = ["src", "packages/one/src"]'), encoding="utf-8")
    candidate.advance()
    lines = refused(consumer(candidate.root), candidate.env(), capsys)
    assert lines[0] == "CANDIDATE_IMPORT_MISMATCH" and "two" in lines[1]


def test_an_empty_range_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    assert refused(consumer(candidate.root), candidate.env(), capsys)[0] == "EMPTY_INTEGRATION_DIFF"


def test_a_selection_that_cannot_be_computed_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance("broken.py")
    assert refused(consumer(candidate.root), candidate.env(), capsys)[0] == "SELECTION_FAILED"


def test_a_range_git_cannot_read_is_selection_failed_for_a_consumer(candidate: Candidate, capsys: pytest.CaptureFixture[str],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    candidate.advance()
    monkeypatch.setattr(verifier, "changed_paths", lambda *_args, **_options: None)
    assert refused(consumer(candidate.root), candidate.env(), capsys)[0] == "SELECTION_FAILED"


@pytest.mark.parametrize(("word", "token"), [
    ("lock", "UV_LOCK_CHECK_FAILED"), ("uv_lock_check_failed", "UV_LOCK_CHECK_FAILED"),
    ("diff_check_failed", "DIFF_CHECK_FAILED"), ("testkit_contract_failed", "TESTKIT_CONTRACT_FAILED"),
    ("ruff_check_failed", "RUFF_CHECK_FAILED")])
def test_each_shared_check_refuses_with_its_token(candidate: Candidate, capsys: pytest.CaptureFixture[str], word: str, token: str) -> None:
    candidate.advance()
    lines = refused(consumer(candidate.root), candidate.env(), capsys, Recorder(**{word: 1}))
    assert lines[0] == token and "log" in lines[1] and "FAILED one" in lines
    assert "working directory kept" in lines[-1]


def test_the_checks_run_in_the_order_of_the_table(candidate: Candidate, atc_tmp: Path) -> None:
    candidate.advance("tests/test_x.py")
    checks = (StaticCheck("before", "BEFORE_FAILED", ("before-tool",), first=True),
              StaticCheck("after", "AFTER_FAILED", ("after-tool",)))
    recorder = Recorder()
    assert verifier.main(consumer(candidate.root, checks=checks), candidate.env(), recorder) == 0
    assert recorder.logs == ["before.log", "uv_lock_check_failed.log", "diff_check_failed.log", "testkit_contract_failed.log",
                             "ruff_check_failed.log", "collection_failed.log", "after.log",
                             "pytest-parallel.log", "pytest-serial.log", "pytest-interactive.log"]
    assert [call[0] for call in recorder.calls][1] == ["uv", "lock", "--check"]
    assert recorder.calls[4][0][-3:] == ["ruff", "check", "src"]
    assert recorder.calls[3][0][-3:] == ["-m", "atc_testkit.check", "--installed"]


def test_collection_runs_only_when_the_range_changes_a_test_input(candidate: Candidate) -> None:
    candidate.advance()
    recorder = Recorder()
    assert verifier.main(consumer(candidate.root), candidate.env(), recorder) == 0
    assert "collection_failed.log" not in recorder.logs


@pytest.mark.parametrize("path", ["conftest.py", "tests/helper.py", "tests/data/fixture.json", "packages/a/tests/x.py",
                                  "packages/a/src/conftest.py"])
def test_what_counts_as_a_test_input(path: str) -> None:
    assert verifier.is_test_input(path, ["tests"])


@pytest.mark.parametrize("path", ["src/a.py", "testsuite/a.py", "packages/a/src/x.py"])
def test_what_does_not_count_as_a_test_input(path: str) -> None:
    assert not verifier.is_test_input(path, ["tests"])


def test_a_failing_collection_refuses(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance("tests/test_x.py")
    assert refused(consumer(candidate.root), candidate.env(), capsys, Recorder(collection_failed=2))[0] == "COLLECTION_FAILED"


def test_a_static_check_refuses_with_its_token_and_keeps_its_condition(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    check = StaticCheck("other", "OWN_TOKEN", ("own-tool",))
    skipped = StaticCheck("skipped", "SKIPPED_TOKEN", ("skipped-tool",), applies=lambda paths: False)
    recorder = Recorder(**{"own-tool": 1})
    lines = refused(consumer(candidate.root, checks=(skipped, check)), candidate.env(), capsys, recorder)
    assert lines[0] == "OWN_TOKEN" and "skipped.log" not in recorder.logs


def test_a_failing_stage_refuses_and_a_run_that_collects_nothing_refuses(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    assert refused(consumer(candidate.root), candidate.env(), capsys, Recorder(**{"pytest-serial": 1}))[0] == "TESTS_FAILED"
    recorder = Recorder(**{"pytest-parallel": 5, "pytest-serial": 5, "pytest-interactive": 5})
    assert refused(consumer(candidate.root), candidate.env(), capsys, recorder)[0] == "NO_TESTS_COLLECTED"


def test_head_moved_and_a_dirty_tree_are_refused_after_the_stages(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()

    class Moving(Recorder):
        def __call__(self, command, **options):
            code = super().__call__(command, **options)
            if Path(options["log"]).name == "pytest-interactive.log":
                candidate.advance("src/moved.py")
            return code
    assert refused(consumer(candidate.root), candidate.env(), capsys, Moving())[0] == "CANDIDATE_HEAD_MOVED"

    class Dirtying(Recorder):
        def __call__(self, command, **options):
            code = super().__call__(command, **options)
            (candidate.root / "untracked.txt").write_text("x", encoding="utf-8")
            return code
    assert refused(consumer(candidate.root), candidate.env(), capsys, Dirtying())[0] == "CANDIDATE_DIRTY"


def test_any_unexpected_fault_refuses_with_verifier_error(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()

    def explode(*_args, **_options):
        raise RuntimeError("boom")
    assert verifier.main(consumer(candidate.root), candidate.env(), explode) == 1
    assert capsys.readouterr().err.splitlines()[0] == "VERIFIER_ERROR"


def test_an_unsuitable_root_prints_its_token_first_and_exits_nine(candidate: Candidate, capsys: pytest.CaptureFixture[str],
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATC_TMP", "relative")
    assert verifier.main(consumer(candidate.root), candidate.env(), Recorder()) == 9
    assert capsys.readouterr().err.splitlines()[0] == "ATC_TMP_UNSUITABLE"


@pytest.mark.parametrize(("paths", "selected", "expected"), [
    (["docs/a.md"], None, True), (["README.MD"], None, True), ([], None, False), (["docs/a.md", "b.py"], None, False),
    (["docs/a.md"], ["tests"], False), (["AGENTS.md"], None, False), (["docs/CLAUDE.md"], None, False),
    ([".claude/x.md"], None, False), (["docs/a.md"], [], False)])
def test_documentation_only(paths: list[str], selected: list[str] | None, expected: bool) -> None:
    assert verifier.documentation_only(paths, selected, (".claude/",)) is expected


def test_a_documentation_range_skips_the_stages_and_runs_the_documentation_check(candidate: Candidate, tmp_path: Path) -> None:
    candidate.advance("docs/a.md")
    report = tmp_path / "report.json"
    recorder = Recorder()
    project = consumer(candidate.root, documentation_check=StaticCheck("docs", "DOC_FAILED", ("doc-tool",)))
    assert verifier.main(project, candidate.env(ATC_VERIFICATION_REPORT=str(report)), recorder) == 0
    assert "docs.log" in recorder.logs and not any(log.startswith("pytest-") for log in recorder.logs)
    assert json.loads(report.read_text(encoding="utf-8"))["classification"] == "documentation-only"


def test_automation_markdown_is_not_documentation_only(candidate: Candidate) -> None:
    candidate.advance(".claude/x.md")
    recorder = Recorder()
    assert verifier.main(consumer(candidate.root), candidate.env(), recorder) == 0
    assert "docs.log" not in recorder.logs


MANAGED_SKILL = "---\nname: s\nmanaged-by: agent-traffic-control\ncanonical: s\n---\n\n# S\n"
OWN_SKILL = "---\nname: s\n---\n\n# S\n"


@pytest.mark.integration
@pytest.mark.parametrize("paths", [
    [".atc/tasks/T-0001.yaml", "docs/a.md"], [".atc/tasks/T-0001.yaml"],
    [".atc/atc.lock", ".atc/governance/CORE.md", ".atc/README.md", ".claude/skills/s/SKILL.md"]])
def test_a_record_or_sync_range_is_documentation_only_and_runs_the_documentation_check(
        candidate: Candidate, paths: list[str]) -> None:
    candidate.advance(".claude/skills/s/SKILL.md", MANAGED_SKILL)
    candidate.main = git(candidate.root, "rev-parse", "HEAD")
    for path in paths:
        candidate.advance(path, MANAGED_SKILL + "changed\n" if path.endswith("SKILL.md") else "x\n")
    recorder = Recorder()
    project = consumer(candidate.root, documentation_check=StaticCheck("docs", "DOC_FAILED", ("doc-tool",)),
                       automation_prefixes=(".atc/", ".claude/"))
    assert verifier.main(project, candidate.env(), recorder) == 0
    assert "docs.log" in recorder.logs


@pytest.mark.integration
@pytest.mark.parametrize(("path", "files", "expected"), [
    (".atc/tasks/T-0001.yaml", {}, True), (".atc/tasks/sub/T.yaml", {}, False), (".atc/other.md", {}, False),
    (".agents/skills/s/references/x.md", {".agents/skills/s/SKILL.md": MANAGED_SKILL}, True),
    (".claude/skills/s/SKILL.md", {".claude/skills/s/SKILL.md": OWN_SKILL}, False),
    (".dsh/skills/s/SKILL.md", {}, False), ("AGENTS.md", {}, False)])
def test_atc_managed_paths(tmp_path: Path, path: str, files: dict[str, str], expected: bool) -> None:
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert verifier.is_atc_managed(path, tmp_path, "0" * 40) is expected


@pytest.mark.integration
def test_a_deleted_managed_skill_is_read_at_the_base(candidate: Candidate) -> None:
    candidate.advance(".claude/skills/s/SKILL.md", MANAGED_SKILL)
    base = git(candidate.root, "rev-parse", "HEAD")
    git(candidate.root, "rm", "-q", "-r", ".claude")
    git(candidate.root, "commit", "-q", "-m", "drop")
    assert verifier.is_atc_managed(".claude/skills/s/SKILL.md", candidate.root, base)


@pytest.mark.integration
def test_an_own_skill_beside_a_record_is_not_documentation_only(candidate: Candidate) -> None:
    candidate.advance(".atc/tasks/T-0001.yaml", "x\n")
    candidate.advance(".claude/skills/own/SKILL.md", OWN_SKILL)
    recorder = Recorder()
    project = consumer(candidate.root, documentation_check=StaticCheck("docs", "DOC_FAILED", ("doc-tool",)),
                       automation_prefixes=(".atc/", ".claude/"))
    assert verifier.main(project, candidate.env(), recorder) == 0
    assert "docs.log" not in recorder.logs


def test_a_pass_writes_the_report_and_removes_the_working_directory(candidate: Candidate, tmp_path: Path, atc_tmp: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    sha = candidate.advance()
    report = tmp_path / "report.json"
    assert verifier.main(consumer(candidate.root), candidate.env(ATC_VERIFICATION_REPORT=str(report)), Recorder()) == 0
    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["selector"]["name"] == "atc-testkit" and [s["name"] for s in written["stages"]] == ["parallel", "serial", "interactive"]
    assert f"integration verification: PASS {sha}" in capsys.readouterr().out
    assert list((atc_tmp / "systmp").iterdir()) == []


def test_a_report_that_cannot_be_written_leaves_the_verdict_a_pass(candidate: Candidate, tmp_path: Path,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    env = candidate.env(ATC_VERIFICATION_REPORT=str(tmp_path / "absent/dir/report.json"))
    assert verifier.main(consumer(candidate.root), env, Recorder()) == 0
    assert "not written" in capsys.readouterr().out


def test_a_refusal_keeps_the_working_directory_with_its_path(candidate: Candidate, atc_tmp: Path,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    lines = refused(consumer(candidate.root), candidate.env(), capsys, Recorder(**{"pytest-serial": 1}))
    kept = next(line for line in lines if line.startswith("working directory kept: ")).removeprefix("working directory kept: ")
    assert Path(kept).parent == atc_tmp / "systmp" and Path(kept).is_dir() and len(Path(kept).name) <= 8


def test_stage_processes_get_the_isolated_environment_with_their_base_inside_it(candidate: Candidate, atc_tmp: Path,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    candidate.advance()
    monkeypatch.setenv("HAMI_X", "1")
    monkeypatch.setenv("NAMED_CREDENTIAL", "1")
    recorder = Recorder()
    assert verifier.main(consumer(candidate.root), candidate.env(), recorder) == 0
    command, _log, env, private = next(call for call in recorder.calls if call[1] == "pytest-parallel.log")
    assert private and "HAMI_X" not in env and "NAMED_CREDENTIAL" not in env and "HAMI_X" not in os.environ
    assert Path(env["TEMP"]).parent == atc_tmp / "systmp"
    assert Path(command[command.index("--basetemp") + 1]).is_relative_to(env["TEMP"])
    assert Path(env["QUANT_DATA_ROOT"]).is_relative_to(env["TEMP"])


# the root form


STAGE_LOGS = ["pytest-parallel.log", "pytest-serial.log", "pytest-interactive.log"]


@pytest.mark.integration
def test_the_root_form_has_no_consumer_rows_and_runs_the_stages(candidate: Candidate, tmp_path: Path) -> None:
    candidate.advance()
    report = tmp_path / "report.json"
    checks = (StaticCheck("workspace", "WORKSPACE_FAILED", ("workspace-tool",)),)
    recorder = Recorder()
    project = root_form(candidate.root, checks=checks)
    assert verifier.main(project, candidate.env(ATC_VERIFICATION_REPORT=str(report)), recorder) == 0
    assert recorder.logs == ["diff_check_failed.log", "workspace.log", *STAGE_LOGS]
    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["classification"] == "impact-selected" and written["selection"] == {"full": True, "paths": []}
    for (command, _log, env, private), stage in zip(recorder.calls[-3:], project.stage_list()):
        assert command[:3] == ["root-runner", "-q", "-m"] and command[3] == stage.marker
        assert Path(command[command.index("--basetemp") + 1]).is_relative_to(env["TEMP"])
        assert private == (stage.name != "interactive")
    bases = {call[0][call[0].index("--basetemp") + 1] for call in recorder.calls[-3:]}
    assert len(bases) == 3, "each stage has its own base directory"


@pytest.mark.integration
def test_the_root_stages_run_its_selection_in_its_tests_directory(candidate: Candidate) -> None:
    candidate.advance()
    seen = []
    project = root_form(candidate.root, tests_dir="adapter",
                        test_paths=lambda paths: seen.append(list(paths)) or ["tests/test_x.py"])
    recorder = Recorder()
    assert verifier.main(project, candidate.env(), recorder) == 0
    assert seen == [["src/fixture_prod/tool.py"]]
    assert recorder.cwds[-3:] == [candidate.root / "adapter"] * 3
    assert all(call[0][-1] == "tests/test_x.py" for call in recorder.calls[-3:])


@pytest.mark.integration
def test_the_verifier_reads_the_workers_and_the_memory_key_in_its_tests_directory(
        candidate: Candidate, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate.advance()
    roots: list[Path] = []
    resolve, open_budget = stages.resolve_workers, stages.open_budget
    monkeypatch.setattr(stages, "resolve_workers", lambda *a, root=None, **k: roots.append(root) or
                        resolve(*a, root=root, **k))
    monkeypatch.setattr(stages, "open_budget", lambda *a, root=None, **k: roots.append(root) or
                        open_budget(*a, root=root, **k))
    project = root_form(candidate.root, tests_dir="adapter", test_paths=lambda paths: ["tests/test_x.py"])
    assert verifier.main(project, candidate.env(), Recorder()) == 0
    assert roots == [candidate.root / "adapter"] * 2


@pytest.mark.integration
def test_a_root_selection_of_no_test_runs_no_stage(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    recorder = Recorder()
    assert verifier.main(root_form(candidate.root, test_paths=lambda paths: None), candidate.env(), recorder) == 0
    assert recorder.logs == ["diff_check_failed.log"]
    assert "selection: no test selected" in capsys.readouterr().out


@pytest.mark.integration
def test_a_root_selection_that_fails_refuses_before_any_check(candidate: Candidate,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    recorder = Recorder()

    def broken(_paths):
        raise ValueError("graph broke")
    lines = refused(root_form(candidate.root, test_paths=broken), candidate.env(), capsys, recorder)
    assert lines[:2] == ["SELECTION_FAILED", "ValueError: graph broke"] and recorder.calls == []


def test_the_root_reads_its_range_with_its_own_token(candidate: Candidate, capsys: pytest.CaptureFixture[str],
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    candidate.advance()
    monkeypatch.setattr(verifier, "changed_paths", lambda *_args, **_options: None)
    assert refused(root_form(candidate.root), candidate.env(), capsys)[0] == "RANGE_UNREADABLE"


@pytest.mark.integration
def test_a_root_range_of_markdown_under_docs_skips_the_selection_and_the_stages(candidate: Candidate) -> None:
    candidate.advance("docs/a.md")
    recorder = Recorder()

    def unused(_paths):
        raise AssertionError("a documentation-only range selects nothing")
    assert verifier.main(root_form(candidate.root, test_paths=unused), candidate.env(), recorder) == 0
    assert recorder.logs == ["diff_check_failed.log"]


@pytest.mark.integration
def test_a_root_documentation_range_writes_a_report_of_no_test(candidate: Candidate, tmp_path: Path) -> None:
    candidate.advance("docs/a.md")
    report = tmp_path / "report.json"
    assert verifier.main(root_form(candidate.root), candidate.env(ATC_VERIFICATION_REPORT=str(report)), Recorder()) == 0
    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["classification"] == "documentation-only" and written["selection"] == {"full": False, "paths": []}


@pytest.mark.integration
def test_a_root_report_digests_the_dependency_files_it_names(candidate: Candidate, tmp_path: Path) -> None:
    (candidate.root / "adapter").mkdir()
    (candidate.root / "adapter/uv.lock").write_text("lock", encoding="utf-8")
    candidate.advance()
    report = tmp_path / "report.json"
    project = root_form(candidate.root, dependency_files=("adapter/uv.lock", "adapter/.python-version"))
    assert verifier.main(project, candidate.env(ATC_VERIFICATION_REPORT=str(report)), Recorder()) == 0
    inputs = json.loads(report.read_text(encoding="utf-8"))["dependency_inputs"]
    assert set(inputs) == {"adapter/uv.lock", "adapter/.python-version"}
    assert inputs["adapter/.python-version"] == "absent" and inputs["adapter/uv.lock"] != "absent"


@pytest.mark.integration
def test_root_markdown_outside_docs_runs_the_stages(candidate: Candidate) -> None:
    candidate.advance("README.md")
    recorder = Recorder()
    assert verifier.main(root_form(candidate.root), candidate.env(), recorder) == 0
    assert recorder.logs[-3:] == STAGE_LOGS


@pytest.mark.integration
def test_the_root_stage_tokens(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    assert refused(root_form(candidate.root), candidate.env(), capsys,
                   Recorder(**{"pytest-serial": 1}))[0] == "TESTS_FAILED"
    empty = Recorder(**{"pytest-parallel": 5, "pytest-serial": 5, "pytest-interactive": 5})
    assert refused(root_form(candidate.root), candidate.env(), capsys, empty)[0] == "NO_TESTS_COLLECTED"


@pytest.mark.integration
def test_the_stages_take_the_host_files_workers(candidate: Candidate, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    host = tmp_path / "config.toml"
    host.write_text("test_workers = 3\n", encoding="utf-8")
    monkeypatch.setattr(stages, "host_config", lambda: host)
    recorder = Recorder()
    assert verifier.main(root_form(candidate.root), candidate.env(), recorder) == 0
    parallel = next(call[0] for call in recorder.calls if call[1] == "pytest-parallel.log")
    assert parallel[parallel.index("-n") + 1] == "3"
    assert re.search(r"workers: 3( of \d+)? \(~/\.atc/config\.toml\)", capsys.readouterr().out)


@pytest.mark.integration
def test_a_bad_host_file_refuses_before_any_check(candidate: Candidate, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    host = tmp_path / "config.toml"
    host.write_text("test_workers = 0\n", encoding="utf-8")
    monkeypatch.setattr(stages, "host_config", lambda: host)
    recorder = Recorder()
    assert verifier.main(consumer(candidate.root), candidate.env(), recorder) == 9
    assert capsys.readouterr().err.splitlines()[0] == "ATC_CONFIG_INVALID" and recorder.calls == []


# the real path: `atc integrate` to the exit code


VERIFIER_SCRIPT = """import sys
from pathlib import Path
from atc_testkit import verifier
from atc_testkit.project import Project, StaticCheck

root = Path(__file__).resolve().parent
project = Project(root=root, argv_file=root / "argv.json", product_prefixes=("fixture_prod",), credentials=(), data_roots=(),
                  checks=(StaticCheck("own", "OWN_TOKEN", (sys.executable, "own_check.py")),),
                  test_paths=lambda paths: ["tests"], documentation_only=lambda paths: False)
sys.exit(verifier.main(project))
"""


# The record the topic branch carries; its own copy names the branch, which `atc integrate ATC-0001` reads.
TASK_RECORD = ("task_id: ATC-0001\nrepository: example/fixture\nbase_sha: '" + "0" * 40 + "'\nscope: fixture\n"
               "governance_source: example/governance\ngovernance_revision: '" + "0" * 40 + "'\nbranch: topic\n"
               "authority:\n  edit: true\n  commit: true\n  push_topic: false\n  integrate_main: false\n"
               "  push_main: false\n  deploy: false\n")
TASK = "ATC-0001"


@pytest.fixture()
def integration_repo(tmp_path: Path, atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(key)
    for key, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@example.com"}.items():
        monkeypatch.setenv(key, value)
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "src/fixture_prod").mkdir(parents=True)
    (root / "src/fixture_prod/__init__.py").write_text("", encoding="utf-8")
    (root / "tests/test_a.py").write_text("def test_a(tmp_path):\n    assert tmp_path.is_dir()\n", encoding="utf-8")
    (root / "own_check.py").write_text("", encoding="utf-8")
    (root / "verify.py").write_text(VERIFIER_SCRIPT, encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (root / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["src"]\n', encoding="utf-8")
    (root / "verify.json").write_text(json.dumps([sys.executable, "verify.py"]), encoding="utf-8")
    (root / "AGENTS.md").write_text(
        "# AGENTS.md\n\n## Task records\n\n- Task prefix: ATC\n\n"
        "## Integration verification\n\n- Verifier argv file: verify.json\n", encoding="utf-8",
    )
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    git(root, "switch", "-q", "-c", "topic")
    (root / ".atc/tasks").mkdir(parents=True)
    (root / ".atc/tasks/ATC-0001.yaml").write_text(TASK_RECORD, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "open ATC-0001")
    (root / "src/fixture_prod/tool.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "topic")
    tip = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "main")
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    (root / ".git/atc-test-tip").write_text(tip, encoding="utf-8")
    return root


def workspace_beside(repo: Path) -> Path:
    """A workspace.yaml beside ``repo`` registering it under its directory name; returns that workspace root."""
    (repo.parent / "workspace.yaml").write_text(
        f"version: 1\nworkspace_root: '{repo.parent}'\nprojects:\n  {repo.name}:\n    repo: {repo.name}\n"
        f"    worktrees: worktrees/{repo.name}\n    state: state/{repo.name}-data\n",
        encoding="utf-8",
    )
    return repo.parent


def integrate(root: Path) -> int:
    tip = (root / ".git/atc-test-tip").read_text(encoding="utf-8")
    return gi.main([TASK, "--sha", tip], root=workspace_beside(root))


def test_atc_integrate_runs_the_skeleton_to_a_pass(integration_repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = (integration_repo / ".git/atc-test-tip").read_text(encoding="utf-8")
    assert integrate(integration_repo) == 0, capsys.readouterr()
    assert git(integration_repo, "rev-parse", "main") == tip
    assert list((atc_tmp / "systmp").iterdir()) == []


def test_atc_integrate_reports_the_skeletons_refusal_token(integration_repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    git(integration_repo, "switch", "-q", "topic")
    (integration_repo / "tests/test_a.py").write_text("def test_a():\n    assert False\n", encoding="utf-8")
    git(integration_repo, "add", ".")
    git(integration_repo, "commit", "-q", "-m", "red")
    tip = git(integration_repo, "rev-parse", "HEAD")
    git(integration_repo, "switch", "-q", "main")
    (integration_repo / ".git/atc-test-tip").write_text(tip, encoding="utf-8")
    before = git(integration_repo, "rev-parse", "main")
    assert integrate(integration_repo) == 3
    captured = capsys.readouterr()
    assert "TESTS_FAILED" in captured.out + captured.err
    assert git(integration_repo, "rev-parse", "main") == before
    kept = list((atc_tmp / "systmp").iterdir())
    assert len(kept) == 1 and (kept[0] / "pytest-parallel.log").is_file()


def verify(root: Path) -> int:
    """`atc test ATC-0001 --candidate --sha TIP`."""
    tip = (root / ".git/atc-test-tip").read_text(encoding="utf-8")
    return gi.run_candidate(TASK, tip, root=workspace_beside(root))


@pytest.mark.integration
def test_atc_verify_runs_the_verifier_on_the_candidate_without_the_slot_or_a_ref(
        integration_repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from integration_slot import IntegrationSlot
    before = git(integration_repo, "rev-parse", "main")
    refs = git(integration_repo, "for-each-ref")
    with IntegrationSlot(integration_repo):
        code = verify(integration_repo)
    captured = capsys.readouterr()
    assert code == 0, captured
    last = captured.out.splitlines()[-1]
    assert last.startswith("PASS candidate ") and " FF log " in last
    assert git(integration_repo, "rev-parse", "main") == before and git(integration_repo, "for-each-ref") == refs
    assert git(integration_repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert [path.suffix for path in (atc_tmp / "candidates").iterdir()] == [".log"]


@pytest.mark.integration
def test_atc_integrate_reuses_the_root_forms_verify_record(
        integration_repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = (integration_repo / ".git/atc-test-tip").read_text(encoding="utf-8")
    check = [TASK, "--sha", tip, "--check"]
    assert gi.main([*check, "--json"], root=workspace_beside(integration_repo)) == 0
    assert json.loads(capsys.readouterr().out)["verification_reuse"] == "NO_RECORD"
    assert verify(integration_repo) == 0, capsys.readouterr()
    capsys.readouterr()
    assert gi.main([*check, "--json"], root=workspace_beside(integration_repo)) == 0
    assert json.loads(capsys.readouterr().out)["verification_reuse"] == "REUSED"
    assert gi.main(check, root=workspace_beside(integration_repo)) == 0
    assert capsys.readouterr().out.rstrip().endswith(" reuse=REUSED")
    assert [path.suffix for path in (atc_tmp / "candidates").iterdir()] == [".log"]
    assert gi.main([TASK, "--sha", tip, "--json"], root=workspace_beside(integration_repo)) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["verification_reused"] and " REUSED " in captured.err
    line = json.loads((integration_repo / ".git/atc/integrations.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["reviewed"] == tip and line["verification_evidence"]["candidate_sha"] == tip


@pytest.mark.integration
def test_atc_verify_reports_the_verifiers_refusal_and_moves_nothing(
        integration_repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    git(integration_repo, "switch", "-q", "topic")
    (integration_repo / "tests/test_a.py").write_text("def test_a():\n    assert False\n", encoding="utf-8")
    git(integration_repo, "add", ".")
    git(integration_repo, "commit", "-q", "-m", "red")
    tip = git(integration_repo, "rev-parse", "HEAD")
    git(integration_repo, "switch", "-q", "main")
    (integration_repo / ".git/atc-test-tip").write_text(tip, encoding="utf-8")
    before = git(integration_repo, "rev-parse", "main")
    assert verify(integration_repo) == 3
    captured = capsys.readouterr()
    assert any(line.startswith("STOP VERIFICATION_FAILED") for line in captured.err.splitlines())
    assert "verifier: TESTS_FAILED" in captured.err
    assert "PASS candidate" not in captured.out and git(integration_repo, "rev-parse", "main") == before

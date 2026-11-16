"""The root's data for the verifier skeleton; the skeleton's own rows are tested in ``test_testkit_verifier.py``."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import integration_verify as iv
import select_tests
from atc_testkit import isolation, lint, locations, stages, verifier

pytestmark = pytest.mark.integration

ADAPTER = Path(__file__).resolve().parents[1]
WORKSPACE = ADAPTER.parents[1]
FAILING_OUTPUT = "output line\nFAILED tests/test_x.py::test_y - boom\nlast line\n"
STAGE_NAMES = {stage.marker: stage.name for stage in stages.stages()}
STAGE_RUNNER = ["-m", "atc_testkit.ci"]


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def commit(repo: Path, path: str, content: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode("utf-8"))
    run_git(repo, "add", "--", path)
    run_git(repo, "commit", "-q", "-m", f"edit {path}")
    return run_git(repo, "rev-parse", "HEAD")


def name(argv: list[str]) -> str:
    """A stage's name for a stage command, ``lint`` for the lint stage, the last argument for a static check."""
    if argv[1:3] == ["-m", "pre_commit"]:
        return "lint"
    if argv[1:3] == STAGE_RUNNER:
        return STAGE_NAMES[argv[argv.index("-m", 3) + 1]]
    return argv[-1]


def base_directory(argv: list[str]) -> Path:
    return Path(argv[argv.index("--basetemp") + 1])


class Candidate:
    """A repository at a candidate commit, the adapter's inputs for it and the commands the verifier started."""

    def __init__(self, root: Path, main: str):
        self.root, self.main, self.calls, self.codes = root, main, [], {}

    def advance(self, path: str = "adapters/python/scripts/tool.py", content: str = "x = 1\n") -> str:
        return commit(self.root, path, content)

    def env(self, **overrides: str) -> dict[str, str]:
        head = run_git(self.root, "rev-parse", "HEAD")
        return {"ATC_EXPECTED_MAIN_SHA": self.main, "ATC_INTEGRATION_SHA": head, "ATC_CANDIDATE_DIR": str(self.root),
                "ATC_REVIEWED_SOURCE_SHA": head, **overrides}

    def run(self, argv: list[str], *, cwd: Path, env: dict[str, str], log: Path, private: bool = False) -> int:
        """Git runs for real; every other command returns the code set for its name."""
        self.calls.append((argv, cwd, env, private))
        log.parent.mkdir(parents=True, exist_ok=True)
        if argv[0] == "git":
            result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
            log.write_text(result.stdout + result.stderr, encoding="utf-8")
            return result.returncode
        if name(argv) == "parallel":  # what the suite leaves in its base directory: a repository's read-only object
            left = base_directory(argv) / "repo/object"
            left.parent.mkdir(parents=True)
            left.write_bytes(b"x")
            left.chmod(stat.S_IREAD)
        log.write_text(FAILING_OUTPUT, encoding="utf-8")
        return self.codes.get(name(argv), 0)

    def verify(self, capsys: pytest.CaptureFixture[str], **overrides: str) -> tuple[int, list[str]]:
        code = iv.main(self.env(**overrides), self.root, self.run)
        return code, capsys.readouterr().err.splitlines()

    def started(self) -> list[str]:
        return [name(call[0]) for call in self.calls]


@pytest.fixture
def candidate(tmp_path: Path, atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> Candidate:
    for key in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(key)
    for key, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    }.items():
        monkeypatch.setenv(key, value)
    root = tmp_path / "candidate"
    (root / iv.ADAPTER_DIR).mkdir(parents=True)
    run_git(root, "init", "-q", "-b", "main")
    main = commit(root, "a.txt", "a\n")
    monkeypatch.chdir(root)
    monkeypatch.setattr(iv, "PRODUCT_PREFIXES", ())
    monkeypatch.setattr(iv, "graph", lambda _root: select_tests.Graph(WORKSPACE))
    return Candidate(root, main)


def test_full_range_runs_the_roots_checks_in_order(candidate: Candidate, atc_tmp: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    sha = candidate.advance()
    assert iv.main(candidate.env(), candidate.root, candidate.run) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines()[0] == "classification: full"
    assert captured.out.splitlines()[-1] == f"integration verification: PASS {sha}"
    adapter = candidate.root / iv.ADAPTER_DIR
    assert candidate.calls[0][:2] == (lint.range_argv(sys.executable, candidate.main, sha), candidate.root)
    assert candidate.started()[1:] == ["parallel", "serial", "interactive"]
    for argv, cwd, env, _private in candidate.calls[1:]:
        assert cwd == adapter and argv[1:4] == [*STAGE_RUNNER, "-q"]
        assert base_directory(argv).is_relative_to(env["TEMP"])
    assert "tests/" not in " ".join(candidate.calls[1][0]), "a path no rule maps runs the full suite"


def test_the_working_directory_holds_the_base_directory_and_is_removed_after_a_pass(
        candidate: Candidate, atc_tmp: Path) -> None:
    candidate.advance()
    assert iv.main(candidate.env(), candidate.root, candidate.run) == 0
    base = base_directory(candidate.calls[1][0])
    work = base.parents[1]
    assert work.parent == atc_tmp / locations.SYSTMP and len(work.name) <= isolation.WORK_NAME_BOUND
    assert not verifier.inside(base, candidate.root)
    assert not work.exists()


def test_the_checks_run_in_the_isolated_environment(candidate: Candidate, atc_tmp: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    candidate.advance()
    monkeypatch.setenv("SOME_API_KEY", "x")
    assert iv.main(candidate.env(), candidate.root, candidate.run) == 0
    for _argv, _cwd, env, _private in candidate.calls:
        work = Path(env["HOME"]).parent
        assert work.parent == atc_tmp / locations.SYSTMP
        assert env["USERPROFILE"] == env["HOME"] and "SOME_API_KEY" not in env
        assert env["ATC_TMP"] == str(atc_tmp) and env["TEMP"] == str(work)
        assert env["UV_CACHE_DIR"] == locations.cache_directories()["UV_CACHE_DIR"]
    assert [call[3] for call in candidate.calls] == [False, True, True, False]


def test_documentation_only_range_skips_the_tests_only(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance("docs/NOTE.md", "# note\n")
    assert iv.main(candidate.env(), candidate.root, candidate.run) == 0
    assert capsys.readouterr().out.splitlines()[0] == "classification: documentation-only"
    assert candidate.started() == ["lint"]


@pytest.mark.parametrize(("paths", "expected"), [
    (["docs/x.md"], True),
    (["docs/x.md", "docs/local/y.md"], True),
    ([], False),
    (["docs/x.md", "docs/x.txt"], False),
    (["docs/x.md", "README.md"], False),
    (["README.md"], False),
    (["AGENTS.md"], False),
    (["shared/templates/PROJECT_AGENTS.md"], False),
    ([".dsh/skills/x/SKILL.md"], False),
    (["shared/skills/x.md"], False),
    (["adapters/python/README.md"], True),
    (["adapters/python/atc-testkit/README.md"], False),
    (["shared/docs/x.md"], False),
])
def test_documentation_only_classification(paths: list[str], expected: bool) -> None:
    assert iv.documentation_only(paths, WORKSPACE) is expected


# Built at run time: a literal path in this file would name it, and this test file would read it.
RECORD = f"{iv.TASK_RECORDS}ATC-0001.yaml"
UNNAMED = next((WORKSPACE / ".out-of-scope").glob("*.md")).relative_to(WORKSPACE).as_posix()


@pytest.mark.parametrize(("paths", "expected"), [
    ([RECORD], True),
    (["docs/x.md", RECORD], True),
    ([RECORD, "adapters/python/scripts/tool.py"], False),
    ([UNNAMED], True),
    ([UNNAMED, "README.md"], False),
])
def test_records_and_markdown_no_test_reads_are_documentation_only(paths: list[str], expected: bool) -> None:
    assert iv.documentation_only(paths, WORKSPACE) is expected


def test_file_moved_into_docs_is_not_documentation_only(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.main = candidate.advance("shared/skills/x.md", "# skill\n\nbody line one\nbody line two\n")
    (candidate.root / "docs").mkdir()
    run_git(candidate.root, "mv", "shared/skills/x.md", "docs/x.md")
    run_git(candidate.root, "commit", "-q", "-m", "move")
    assert iv.main(candidate.env(), candidate.root, candidate.run) == 0
    assert capsys.readouterr().out.splitlines()[0] == "classification: full"
    assert "parallel" in candidate.started()


def test_product_module_imported_from_outside_the_candidate_is_refused(
    candidate: Candidate, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate.advance()
    monkeypatch.setattr(iv, "PRODUCT_PREFIXES", ("atc_cli",))
    code, err = candidate.verify(capsys)
    assert (code, err[0]) == (1, "CANDIDATE_IMPORT_MISMATCH")
    assert err[1].startswith("atc_cli: ")
    assert candidate.calls == []


def test_this_checkout_imports_its_own_product_modules() -> None:
    project = iv.project(WORKSPACE)
    names = verifier.module_names(project)
    assert {entry.name for entry in verifier._tree_entries(project)} == {"atc_cli", "atc_testkit"}
    assert {"atc_cli", "atc_testkit"} <= set(names)
    assert verifier.import_violations(names, WORKSPACE) == []


@pytest.mark.parametrize(("failing", "token", "later"), [
    ("lint", "LINT_FAILED", ["parallel"]),
    ("parallel", "TESTS_FAILED", ["serial", "interactive"]),
])
def test_failing_check_is_refused_and_stops_the_run(
    candidate: Candidate, capsys: pytest.CaptureFixture[str], failing: str, token: str, later: list[str],
) -> None:
    candidate.advance()
    candidate.codes[failing] = 1
    code, err = candidate.verify(capsys)
    assert (code, err[0]) == (1, token)
    assert failing in candidate.started() and not set(later) & set(candidate.started())


def test_failing_suite_reports_its_failure_lines_and_keeps_its_log(
    candidate: Candidate, atc_tmp: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    candidate.advance()
    candidate.codes["serial"] = 1
    _code, err = candidate.verify(capsys)
    (work,) = (atc_tmp / locations.SYSTMP).iterdir()
    log = work / "pytest-serial.log"
    assert err[1] == f"serial stage exited 1; log {log}"
    assert err[2:] == ["FAILED tests/test_x.py::test_y - boom", f"working directory kept: {work}"]
    assert "last line" in log.read_text(encoding="utf-8")


def test_suite_that_collects_no_test_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    candidate.codes.update(parallel=5, serial=5, interactive=5)
    code, err = candidate.verify(capsys)
    assert (code, err[0]) == (1, "NO_TESTS_COLLECTED")


def test_unsuitable_temporary_root_exits_nine(candidate: Candidate, capsys: pytest.CaptureFixture[str],
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    candidate.advance()
    monkeypatch.setenv("ATC_TMP", "relative")
    code, err = candidate.verify(capsys)
    assert (code, err[0]) == (9, locations.UNSUITABLE)
    assert candidate.calls == []


def test_the_adapter_suite_carries_the_plugin_from_its_tree_with_no_base_directory_of_its_own(
        pytestconfig: pytest.Config) -> None:
    plugin = pytestconfig.pluginmanager.get_plugin("atc_testkit.plugin")
    assert plugin is not None and verifier.inside(plugin.__file__, ADAPTER / "atc-testkit/src")
    assert "--basetemp" not in pytestconfig.getini("addopts")
    assert "-q" not in pytestconfig.getini("addopts"), "the stages pass -q; a second one hides pytest's count line"


def test_the_root_report_digests_the_adapters_dependency_files_by_their_root_paths() -> None:
    """`atc integrate` digests them in the candidate tree from the repository root (verification-evidence README)."""
    names = iv.project(WORKSPACE).tests_dependency_files()
    assert names == ("adapters/python/uv.lock", "adapters/python/.python-version")
    assert all((WORKSPACE / name).is_file() for name in names)


def test_the_selection_gives_the_stages_adapter_paths_the_full_suite_or_none(capsys: pytest.CaptureFixture[str]) -> None:
    test_file = f"{iv.ADAPTER_DIR}/tests/{select_tests.SELF_TEST}"
    assert iv.selected_tests([test_file], WORKSPACE) == [f"tests/{select_tests.SELF_TEST}"]
    assert f"  tests/{select_tests.SELF_TEST}  <- {test_file}" in capsys.readouterr().out
    assert iv.selected_tests([f"{iv.ADAPTER_DIR}/pyproject.toml"], WORKSPACE) == []
    unnamed = "docs/" + "NO_TEST_NAMES" + "_THIS.md"  # built at run time, so no test file names it
    assert iv.selected_tests([unnamed], WORKSPACE) is None
    assert "no test file selected" in capsys.readouterr().out

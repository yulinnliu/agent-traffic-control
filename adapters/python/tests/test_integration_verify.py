from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import integration_verify as iv

ADAPTER = Path(__file__).resolve().parents[1]
WORKSPACE = ADAPTER.parents[1]


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
    return "pytest" if "pytest" in argv else argv[-1]


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

    def run(self, argv: list[str], cwd: Path) -> tuple[int, str]:
        """Git runs for real; every other command returns the code set for its name."""
        self.calls.append((argv, cwd))
        if argv[0] == "git":
            return iv.run_command(argv, cwd)
        if name(argv) == "pytest":  # what the suite leaves in its base directory: a repository's read-only object
            left = Path(argv[-1]) / "repo/object"
            left.parent.mkdir(parents=True)
            left.write_bytes(b"x")
            left.chmod(stat.S_IREAD)
        return self.codes.get(name(argv), 0), "output line\nFAILED tests/test_x.py::test_y - boom\nlast line\n"

    def verify(self, capsys: pytest.CaptureFixture[str], **overrides: str) -> tuple[int, list[str]]:
        code = iv.main(self.env(**overrides), self.root, self.run)
        return code, capsys.readouterr().err.splitlines()

    def started(self) -> list[str]:
        return [name(argv) for argv, _cwd in self.calls]


@pytest.fixture
def candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Candidate:
    for key in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(key)
    for key, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    }.items():
        monkeypatch.setenv(key, value)
    systmp = tmp_path / "systmp"
    systmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(systmp))
    root = tmp_path / "candidate"
    (root / iv.ADAPTER_DIR).mkdir(parents=True)
    run_git(root, "init", "-q", "-b", "main")
    main = commit(root, "a.txt", "a\n")
    monkeypatch.chdir(root)
    monkeypatch.setattr(iv, "PRODUCT_MODULES", ())
    return Candidate(root, main)


def test_full_range_runs_every_check_in_order(
    candidate: Candidate, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    sha = candidate.advance()
    assert iv.main(candidate.env(), candidate.root, candidate.run) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines()[0] == "classification: full"
    assert captured.out.splitlines()[-1] == f"integration verification: PASS {sha}"
    adapter = candidate.root / iv.ADAPTER_DIR
    pytest_argv = candidate.calls[-1][0]
    assert candidate.calls == [
        (["git", "diff", "--check", f"{candidate.main}..{sha}"], candidate.root),
        (iv.WORKSPACE_CHECK[1], adapter), (iv.POLICY_CHECK[1], adapter), (pytest_argv, adapter),
    ]
    assert pytest_argv[:-2] == iv.PYTEST and pytest_argv[-2] == "--basetemp"
    basetemp = Path(pytest_argv[-1])
    assert basetemp.parent.parent == tmp_path / "systmp" and not iv.inside(basetemp, candidate.root)
    assert list((tmp_path / "systmp").iterdir()) == []


def test_documentation_only_range_skips_pytest_only(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance("docs/NOTE.md", "# note\n")
    assert iv.main(candidate.env(), candidate.root, candidate.run) == 0
    assert capsys.readouterr().out.splitlines()[0] == "classification: documentation-only"
    assert candidate.started()[1:] == ["scripts/validate_workspace.py", "../../shared/policies/examples"]


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
    (["adapters/python/README.md"], False),
    (["shared/docs/x.md"], False),
])
def test_documentation_only_classification(paths: list[str], expected: bool) -> None:
    assert iv.documentation_only(paths) is expected


@pytest.mark.parametrize("name", iv.REQUIRED_ENV)
def test_missing_input_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str], name: str) -> None:
    candidate.advance()
    code, err = candidate.verify(capsys, **{name: ""})
    assert (code, err[0], err[1]) == (1, "VERIFY_ENV_MISSING", f"missing: {name}")
    assert candidate.calls == []


@pytest.mark.parametrize("name", [name for name in iv.REQUIRED_ENV if name.endswith("_SHA")])
@pytest.mark.parametrize("value", ["main", "abc1234", "A" * 40])
def test_abbreviated_or_symbolic_sha_is_refused(
    candidate: Candidate, capsys: pytest.CaptureFixture[str], name: str, value: str,
) -> None:
    candidate.advance()
    code, err = candidate.verify(capsys, **{name: value})
    assert (code, err[0]) == (1, "VERIFY_ENV_INVALID")
    assert candidate.calls == []


def test_candidate_dir_other_than_the_tree_is_refused(
    candidate: Candidate, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    candidate.advance()
    code, err = candidate.verify(capsys, ATC_CANDIDATE_DIR=str(tmp_path))
    assert (code, err[0]) == (1, "CANDIDATE_DIR_MISMATCH")


def test_working_directory_other_than_the_candidate_is_refused(
    candidate: Candidate, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate.advance()
    monkeypatch.chdir(candidate.root / iv.ADAPTER_DIR)
    code, err = candidate.verify(capsys)
    assert (code, err[0]) == (1, "CANDIDATE_DIR_MISMATCH")


def test_working_directory_above_the_candidate_is_refused(
    candidate: Candidate, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate.advance()
    monkeypatch.chdir(candidate.root.parent)
    code, err = candidate.verify(capsys)
    assert (code, err[0]) == (1, "CANDIDATE_DIR_MISMATCH")


def test_head_other_than_the_integration_sha_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    code, err = candidate.verify(capsys, ATC_INTEGRATION_SHA=candidate.main)
    assert (code, err[0]) == (1, "CANDIDATE_HEAD_MISMATCH")


def side_commit(candidate: Candidate) -> str:
    """A commit on a branch from main that the candidate does not contain."""
    head = run_git(candidate.root, "rev-parse", "HEAD")
    run_git(candidate.root, "switch", "-q", "-c", "side", candidate.main)
    side = commit(candidate.root, "side.txt", "s\n")
    run_git(candidate.root, "switch", "-q", "--detach", head)
    return side


def test_expected_main_outside_the_candidate_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    code, err = candidate.verify(capsys, ATC_EXPECTED_MAIN_SHA=side_commit(candidate))
    assert (code, err[0]) == (1, "EXPECTED_MAIN_NOT_IN_CANDIDATE")


def test_reviewed_source_outside_the_candidate_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    code, err = candidate.verify(capsys, ATC_REVIEWED_SOURCE_SHA=side_commit(candidate))
    assert (code, err[0]) == (1, "REVIEWED_SOURCE_NOT_IN_CANDIDATE")


def test_product_module_imported_from_outside_the_candidate_is_refused(
    candidate: Candidate, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate.advance()
    monkeypatch.setattr(iv, "PRODUCT_MODULES", ("atc_cli",))
    code, err = candidate.verify(capsys)
    assert (code, err[0]) == (1, "CANDIDATE_IMPORT_MISMATCH")
    assert err[1].startswith("atc_cli: ")
    assert candidate.calls == []


def test_this_checkout_imports_its_own_product_modules() -> None:
    assert iv.PRODUCT_MODULES == ("atc_cli",)
    assert iv.import_violations(iv.PRODUCT_MODULES, WORKSPACE) == []


def test_range_without_a_changed_path_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    code, err = candidate.verify(capsys)
    assert (code, err[0]) == (1, "EMPTY_INTEGRATION_DIFF")
    assert candidate.calls == []


def test_unreadable_range_is_refused(
    candidate: Candidate, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate.advance()
    real = iv.git

    def git(root: Path, *args: str) -> subprocess.CompletedProcess:
        if args[0] == "diff":
            return subprocess.CompletedProcess(args, 128, "", "fatal: bad object\n")
        return real(root, *args)

    monkeypatch.setattr(iv, "git", git)
    code, err = candidate.verify(capsys)
    assert (code, err[0], err[-1]) == (1, "RANGE_UNREADABLE", "fatal: bad object")
    assert candidate.calls == []


def test_file_moved_into_docs_is_not_documentation_only(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.main = candidate.advance("shared/skills/x.md", "# skill\n\nbody line one\nbody line two\n")
    (candidate.root / "docs").mkdir()
    run_git(candidate.root, "mv", "shared/skills/x.md", "docs/x.md")
    run_git(candidate.root, "commit", "-q", "-m", "move")
    assert run_git(candidate.root, "diff", "--name-only", f"{candidate.main}..HEAD") == "docs/x.md"
    assert iv.main(candidate.env(), candidate.root, candidate.run) == 0
    assert capsys.readouterr().out.splitlines()[0] == "classification: full"
    assert "pytest" in candidate.started()


def test_whitespace_error_in_the_range_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance("docs/NOTE.md", "trailing \n")
    code, err = candidate.verify(capsys)
    assert (code, err[0]) == (1, "DIFF_CHECK_FAILED")
    assert any("trailing whitespace" in line for line in err)
    assert candidate.started() == [candidate.started()[0]]


@pytest.mark.parametrize(("failing", "token", "later"), [
    ("scripts/validate_workspace.py", "WORKSPACE_VALIDATION_FAILED", ["../../shared/policies/examples", "pytest"]),
    ("../../shared/policies/examples", "POLICY_CHECK_FAILED", ["pytest"]),
    ("pytest", "TESTS_FAILED", []),
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
    candidate: Candidate, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    candidate.advance()
    candidate.codes["pytest"] = 1
    _code, err = candidate.verify(capsys)
    (work,) = (tmp_path / "systmp").iterdir()
    log = work / "tests_failed.log"
    assert err[1] == f"{' '.join(candidate.calls[-1][0])} exited 1; log {log}"
    assert err[2:] == ["FAILED tests/test_x.py::test_y - boom", f"working directory kept: {work}"]
    assert "last line" in log.read_text(encoding="utf-8")


def test_failing_check_reports_what_it_wrote_to_stderr(
    candidate: Candidate, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    candidate.advance()
    child = [sys.executable, "-c", "import sys; print('to stdout'); sys.stderr.write('to stderr\\n'); sys.exit(3)"]

    def run(argv: list[str], cwd: Path) -> tuple[int, str]:
        if name(argv) == "scripts/validate_workspace.py":
            return iv.run_command(child, cwd)
        return candidate.run(argv, cwd)

    assert iv.main(candidate.env(), candidate.root, run) == 1
    err = capsys.readouterr().err.splitlines()
    (work,) = (tmp_path / "systmp").iterdir()
    assert err[0] == "WORKSPACE_VALIDATION_FAILED" and "to stdout" in err and "to stderr" in err
    assert "to stderr" in (work / "workspace_validation_failed.log").read_text(encoding="utf-8")


def test_suite_that_collects_no_test_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    candidate.codes["pytest"] = iv.PYTEST_NO_TESTS_COLLECTED
    code, err = candidate.verify(capsys)
    assert (code, err[0]) == (1, "NO_TESTS_COLLECTED")


def test_head_moved_by_a_check_is_refused(candidate: Candidate, capsys: pytest.CaptureFixture[str]) -> None:
    candidate.advance()
    env = candidate.env()

    def run(argv: list[str], cwd: Path) -> tuple[int, str]:
        if name(argv) == "pytest":
            candidate.advance("later.txt", "l\n")
        return candidate.run(argv, cwd)

    assert iv.main(env, candidate.root, run) == 1
    assert capsys.readouterr().err.splitlines()[0] == "CANDIDATE_HEAD_MOVED"


@pytest.mark.parametrize("change", ["untracked", "modified"])
def test_tree_left_dirty_by_a_check_is_refused(
    candidate: Candidate, capsys: pytest.CaptureFixture[str], change: str,
) -> None:
    candidate.advance()

    def run(argv: list[str], cwd: Path) -> tuple[int, str]:
        if name(argv) == "pytest":
            left = candidate.root / ("new/left.txt" if change == "untracked" else "a.txt")
            left.parent.mkdir(exist_ok=True)
            left.write_text("left\n", encoding="utf-8")
        return candidate.run(argv, cwd)

    assert iv.main(candidate.env(), candidate.root, run) == 1
    err = capsys.readouterr().err.splitlines()
    assert err[0] == "CANDIDATE_DIRTY"
    assert err[1].endswith("new/left.txt" if change == "untracked" else "a.txt")


def test_unexpected_fault_is_refused_and_names_the_kept_working_directory(
    candidate: Candidate, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    candidate.advance()

    def run(argv: list[str], cwd: Path) -> tuple[int, str]:
        raise OSError("uv is not installed")

    assert iv.main(candidate.env(), candidate.root, run) == 1
    (work,) = (tmp_path / "systmp").iterdir()
    assert capsys.readouterr().err.splitlines() == [
        "VERIFIER_ERROR", "OSError: uv is not installed", f"working directory kept: {work}"]


def test_fault_before_the_working_directory_exists_is_refused(
    candidate: Candidate, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate.advance()

    def fault(_candidate: str) -> None:
        raise RuntimeError("import machinery broke")

    monkeypatch.setattr(iv, "check_imports", fault)
    assert iv.main(candidate.env(), candidate.root, candidate.run) == 1
    assert capsys.readouterr().err.splitlines() == ["VERIFIER_ERROR", "RuntimeError: import machinery broke"]
    assert list((tmp_path / "systmp").iterdir()) == []


def test_git_hook_variables_do_not_select_another_repository(
    candidate: Candidate, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate.advance()
    env = candidate.env()
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    assert iv.main(env, candidate.root, candidate.run) == 0
    assert capsys.readouterr().err == ""


def test_checks_are_the_python_adapter_job_commands() -> None:
    workflow = (WORKSPACE / ".github/workflows/validate.yml").read_text(encoding="utf-8")
    job = workflow[workflow.index("  python-adapter:"):]
    assert f"working-directory: {iv.ADAPTER_DIR}" in job
    assert [line.split("- run: ", 1)[1] for line in job.splitlines() if "- run: " in line] == [
        " ".join(argv) for argv in (iv.PYTEST, iv.WORKSPACE_CHECK[1], iv.POLICY_CHECK[1])
    ]


def test_agents_section_names_the_argv_file_that_starts_the_verifier() -> None:
    agents = (WORKSPACE / "AGENTS.md").read_text(encoding="utf-8")
    section = agents[agents.index("\n## Integration verification\n"):]
    named = next(line for line in section.splitlines() if line.startswith("- Verifier argv file: "))
    argv_file = WORKSPACE / named.removeprefix("- Verifier argv file: ")
    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    assert argv == ["uv", "run", "--quiet", "--locked", "--project", iv.ADAPTER_DIR, "python",
                    f"{iv.ADAPTER_DIR}/scripts/integration_verify.py"]
    assert (WORKSPACE / argv[-1]).samefile(iv.__file__)
    timeout = next(line for line in section.splitlines() if line.startswith("- Verifier timeout: "))
    assert int(timeout.removeprefix("- Verifier timeout: ")) >= 300

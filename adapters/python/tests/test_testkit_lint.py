"""docs/TESTKIT.md § Lint stage: pre-commit over the whole tree, in a Git working tree or an export."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

from atc_testkit import lint, testrun


CONFIG = """repos:
  - repo: local
    hooks:
      - id: forbidden
        name: no FORBIDDEN word
        entry: FORBIDDEN
        language: pygrep
        types: [text]
        exclude: ^\\.pre-commit-config\\.yaml$
"""

INDEX_HOOK = """repos:
  - repo: local
    hooks:
      - id: index
        name: the index names new.txt
        entry: git ls-files --error-unmatch new.txt
        language: system
        pass_filenames: false
        always_run: true
"""


def environment(tmp_path: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    return {**env, "PRE_COMMIT_HOME": str(tmp_path / "pre-commit-home")}


def tree_with(tmp_path: Path, files: dict[str, str], config: bool = True) -> Path:
    tree = tmp_path / "scratch" / "tree"
    tree.mkdir(parents=True)
    for name, text in {**({".pre-commit-config.yaml": CONFIG} if config else {}), **files}.items():
        (tree / name).write_text(text, encoding="utf-8")
    return tree


def git(tree: Path, tmp_path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True, env=environment(tmp_path)).stdout


def committed(tmp_path: Path, files: dict[str, str]) -> tuple[Path, Path]:
    """A Git working tree holding ``files`` in one commit, and an empty scratch directory beside it."""
    tree = tree_with(tmp_path, files)
    git(tree, tmp_path, "init", "-q")
    git(tree, tmp_path, "add", "-A")
    git(tree, tmp_path, "commit", "-q", "-m", "base")
    place = tmp_path / "place"
    place.mkdir()
    return tree, place


@pytest.mark.integration
def test_an_export_is_linted_whole_through_a_git_directory_the_run_removes(tmp_path: Path) -> None:
    tree = tree_with(tmp_path, {"good.txt": "fine\n"})
    place, env = tree.parent, environment(tmp_path)
    assert lint.run_tree(tree, place, sys.executable, env, place / "pass.log") == 0
    (tree / "bad.txt").write_text("a FORBIDDEN word\n", encoding="utf-8")
    assert lint.run_tree(tree, place, sys.executable, env, place / "fail.log") == 1
    assert "bad.txt:1:a FORBIDDEN word" in (place / "fail.log").read_text(encoding="utf-8")
    assert not (tree / ".git").exists() and not (place / lint.INDEX).exists()
    assert sorted(path.name for path in tree.iterdir()) == [".pre-commit-config.yaml", "bad.txt", "good.txt"]


@pytest.mark.integration
def test_a_working_tree_is_linted_whole_with_its_untracked_files_and_its_own_index_untouched(tmp_path: Path) -> None:
    tree, place = committed(tmp_path, {"good.txt": "fine\n"})
    (tree / "new.txt").write_text("a FORBIDDEN word\n", encoding="utf-8")
    index = tree / ".git" / "index"
    before = index.read_bytes()
    assert lint.run_tree(tree, place, sys.executable, environment(tmp_path), place / "x.log") == 1
    assert "new.txt:1:a FORBIDDEN word" in (place / "x.log").read_text(encoding="utf-8")
    assert index.read_bytes() == before and "new.txt" not in git(tree, tmp_path, "ls-files")
    assert sorted(path.name for path in place.iterdir()) == ["x.log"]


@pytest.mark.integration
def test_a_rule_change_alone_fails_on_a_file_the_change_did_not_touch(tmp_path: Path) -> None:
    """The gap of docs/design/ATC-0070.md § 1.2: a commit that changes only the rules still checks every file."""
    tree, place = committed(tmp_path, {".pre-commit-config.yaml": "repos: []\n", "old.txt": "a FORBIDDEN word\n"})
    (tree / lint.CONFIG).write_text(CONFIG, encoding="utf-8")
    git(tree, tmp_path, "commit", "-q", "-am", "the rule")
    assert lint.run_tree(tree, place, sys.executable, environment(tmp_path), place / "x.log") == 1
    assert "old.txt:1:a FORBIDDEN word" in (place / "x.log").read_text(encoding="utf-8")


@pytest.mark.integration
def test_a_hook_that_reads_the_index_sees_the_scratch_index_with_the_untracked_files(tmp_path: Path) -> None:
    tree, place = committed(tmp_path, {".pre-commit-config.yaml": INDEX_HOOK})
    assert lint.run_tree(tree, place, sys.executable, environment(tmp_path), place / "x.log") == 1
    (tree / "new.txt").write_text("fine\n", encoding="utf-8")
    assert lint.run_tree(tree, place, sys.executable, environment(tmp_path), place / "x.log") == 0, \
        (place / "x.log").read_text(encoding="utf-8")
    assert "new.txt" not in git(tree, tmp_path, "ls-files")


@pytest.mark.integration
def test_a_lint_failure_stops_the_run_with_its_token(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = tree_with(tmp_path, {"good.txt": "fine\n"})
    place, env = tree.parent, environment(tmp_path)
    assert testrun.run_lint(tree, place, sys.executable, env) == 0
    assert capsys.readouterr().out.startswith(f"LINT PASS every file; log {place / 'runs'}")
    (tree / "bad.txt").write_text("FORBIDDEN\n", encoding="utf-8")
    assert testrun.run_lint(tree, place, sys.executable, env) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[-1].startswith(f"STOP {lint.TOKEN}: pre-commit exited 1; log {place / 'runs'}")
    assert any("bad.txt:1:FORBIDDEN" in line for line in out)


@pytest.mark.integration
def test_a_tree_without_the_config_has_not_adopted_the_stage_and_goes_on(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = tree_with(tmp_path, {"bad.txt": "FORBIDDEN\n"}, config=False)
    assert testrun.run_lint(tree, tree.parent, sys.executable, environment(tmp_path)) == 0
    assert capsys.readouterr().out == (f"{lint.NOT_CONFIGURED}: no .pre-commit-config.yaml at {tree}; "
                                       "the lint stage is not adopted and does not run\n")
    assert not (tree.parent / "runs").exists()


@pytest.mark.integration
def test_a_tree_with_the_config_whose_interpreter_has_no_pre_commit_fails(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bare = tmp_path / "bare"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(bare)], check=True, capture_output=True)
    python = bare / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    tree = tree_with(tmp_path, {"good.txt": "fine\n"})
    env = {key: value for key, value in environment(tmp_path).items() if key not in ("VIRTUAL_ENV", "PYTHONPATH")}
    assert testrun.run_lint(tree, tree.parent, str(python), env) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[-1].startswith(f"STOP {lint.TOKEN}: pre-commit exited 1; log {tree.parent / 'runs'}")
    assert any("No module named pre_commit" in line for line in out), out


@pytest.mark.unit
def test_the_lint_stage_runs_over_every_file() -> None:
    assert lint.tree_argv("py") == ["py", "-m", "pre_commit", "run", "--show-diff-on-failure", "--color", "never",
                                    "--all-files"]


@pytest.mark.unit
@pytest.mark.parametrize(("flags", "tests", "expected"), [
    (dict(lint=False, all=False), [], ["--lint", "--related", "BASE"]),
    (dict(lint=False, all=True), [], ["--lint"]),
    (dict(lint=False, all=False), ["t.py"], ["--lint", "--", "t.py"]),
    (dict(lint=True, all=False), [], ["--lint", "--lint-only"])])
def test_atc_test_lints_before_its_tests_and_lint_alone_runs_no_test(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: dict, tests: list[str], expected: list[str]) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.atc-testkit]\n", encoding="utf-8")
    task = argparse.Namespace(id="ATC-0001", worktree=tmp_path, base="BASE", source=lambda: tmp_path)
    seen = []
    monkeypatch.setattr(testrun, "run", lambda argv, environ: seen.append(argv) or 0)
    args = argparse.Namespace(sha=None, verbose=False, **flags)
    import task_tests
    assert task_tests.run_tests(task, args, tests, {}) == 0
    assert seen[0][seen[0].index("--label") + 2:] == expected

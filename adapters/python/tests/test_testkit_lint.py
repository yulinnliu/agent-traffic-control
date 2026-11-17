"""docs/TESTKIT.md § Lint stage: pre-commit over a set of paths, in a Git working tree or an export."""
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
"""


def environment(tmp_path: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    return {**env, "PRE_COMMIT_HOME": str(tmp_path / "pre-commit-home")}


def tree_with(tmp_path: Path, files: dict[str, str]) -> Path:
    tree = tmp_path / "scratch" / "tree"
    tree.mkdir(parents=True)
    for name, text in {".pre-commit-config.yaml": CONFIG, **files}.items():
        (tree / name).write_text(text, encoding="utf-8")
    return tree


@pytest.mark.integration
def test_an_export_is_linted_through_a_git_directory_the_run_removes(tmp_path: Path) -> None:
    tree = tree_with(tmp_path, {"good.txt": "fine\n", "bad.txt": "a FORBIDDEN word\n"})
    place, env = tree.parent, environment(tmp_path)
    assert lint.run_paths(tree, place, sys.executable, ["good.txt"], env, place / "pass.log") == 0
    assert lint.run_paths(tree, place, sys.executable, ["good.txt", "bad.txt"], env, place / "fail.log") == 1
    assert "bad.txt:1:a FORBIDDEN word" in (place / "fail.log").read_text(encoding="utf-8")
    assert not (tree / ".git").exists() and not (place / lint.INDEX).exists()
    assert sorted(path.name for path in tree.iterdir()) == [".pre-commit-config.yaml", "bad.txt", "good.txt"]


@pytest.mark.integration
def test_a_git_working_tree_is_linted_in_place(tmp_path: Path) -> None:
    tree = tree_with(tmp_path, {"bad.txt": "FORBIDDEN\n"})
    subprocess.run(["git", "init", "-q", str(tree)], check=True, env=environment(tmp_path))
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, env=environment(tmp_path))
    assert lint.run_paths(tree, tree.parent, sys.executable, ["bad.txt"], environment(tmp_path), tmp_path / "x.log") == 1
    assert (tree / ".git").is_dir() and not (tree.parent / lint.INDEX).exists()


@pytest.mark.integration
def test_a_lint_failure_stops_the_run_with_its_token_and_a_deleted_path_has_nothing_to_lint(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = tree_with(tmp_path, {"bad.txt": "FORBIDDEN\n"})
    place, env = tree.parent, environment(tmp_path)
    assert testrun.run_lint(tree, place, sys.executable, ["gone.txt"], env) == 0
    assert capsys.readouterr().out == "LINT no changed file\n"
    assert testrun.run_lint(tree, place, sys.executable, ["bad.txt", "gone.txt"], env) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[-1].startswith(f"STOP {lint.TOKEN}: pre-commit exited 1 on 1 files; log {place / 'runs'}")
    assert any("bad.txt:1:FORBIDDEN" in line for line in out)


@pytest.mark.unit
def test_a_range_lints_what_its_commits_add_or_change() -> None:
    assert lint.range_argv("py", "a" * 40, "b" * 40) == [
        "py", "-m", "pre_commit", "run", "--show-diff-on-failure", "--color", "never",
        "--from-ref", "a" * 40, "--to-ref", "b" * 40]


@pytest.mark.unit
@pytest.mark.parametrize(("flags", "expected"), [
    (dict(lint=False, all=False), ["--lint", "BASE", "--related", "BASE"]),
    (dict(lint=False, all=True), ["--lint", "BASE"]),
    (dict(lint=True, all=False), ["--lint", "BASE", "--lint-only"])])
def test_atc_test_lints_before_its_tests_and_lint_alone_runs_no_test(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: dict, expected: list[str]) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.atc-testkit]\n", encoding="utf-8")
    task = argparse.Namespace(id="ATC-0001", worktree=tmp_path, base="BASE", source=lambda: tmp_path)
    seen = []
    monkeypatch.setattr(testrun, "run", lambda argv, environ: seen.append(argv) or 0)
    args = argparse.Namespace(sha=None, verbose=False, **flags)
    import task_tests
    assert task_tests.run_tests(task, args, [], {}) == 0
    assert seen[0][seen[0].index("--label") + 2:] == expected

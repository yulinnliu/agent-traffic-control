"""The fixture history helper (tests/git_history.py, docs/design/ATC-0071.md § 4.1): one `git fast-import` commits
what `add` and `commit` would, and a session copies a built repository instead of rebuilding it."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import git_history
from git_history import Commit

pytestmark = pytest.mark.integration


@pytest.fixture
def env() -> dict[str, str]:
    """No user or system config, so the repository applies no content filter; a fixed identity."""
    environ = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environ.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="a",
                   GIT_AUTHOR_EMAIL="a@example.com", GIT_COMMITTER_NAME="c", GIT_COMMITTER_EMAIL="c@example.com")
    return environ


def git(repo: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env,
                          check=True).stdout.strip()


def test_a_new_repository_holds_each_commit_checked_out_clean(tmp_path: Path, env: dict[str, str]) -> None:
    repo = tmp_path / "repo"
    first, second = git_history.new_repository(repo, [
        Commit({"a.txt": "a\n", "dir/b.txt": "b\n"}, "first"),
        Commit({"a.txt": "a2\n", "dir/b.txt": None}, "second"),
    ], env=env)
    assert git(repo, env, "rev-parse", "main", "main~1") == f"{second}\n{first}"
    assert git(repo, env, "rev-list", "--max-parents=0", "main") == first
    assert git(repo, env, "log", "--format=%s|%an <%ae>|%cn <%ce>", "main") == (
        "second|a <a@example.com>|c <c@example.com>\nfirst|a <a@example.com>|c <c@example.com>")
    assert git(repo, env, "ls-tree", "-r", "--name-only", first) == "a.txt\ndir/b.txt"
    assert git(repo, env, "ls-tree", "-r", "--name-only", second) == "a.txt"
    assert git(repo, env, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert git(repo, env, "status", "--porcelain") == ""
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a2\n"


def test_a_blob_is_what_add_stores_for_a_file_write_text_wrote(tmp_path: Path, env: dict[str, str]) -> None:
    repo = tmp_path / "repo"
    [base] = git_history.new_repository(repo, [Commit({"text.txt": "one\ntwo\n", "raw.bin": b"x\ny\n"}, "base")],
                                        env=env)
    (repo / "text.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "raw.bin").write_bytes(b"x\ny\n")
    assert git(repo, env, "status", "--porcelain") == ""
    assert git_history.blob("one\ntwo\n") == f"one{os.linesep}two{os.linesep}".encode()
    assert git(repo, env, "rev-parse", f"{base}:raw.bin") == git(repo, env, "hash-object", "raw.bin")


def test_a_branch_from_start_leaves_head_index_and_tree_alone(tmp_path: Path, env: dict[str, str]) -> None:
    repo = tmp_path / "repo"
    [base] = git_history.new_repository(repo, [Commit({"a.txt": "a\n"}, "base")], env=env)
    [tip] = git_history.import_commits(repo, "topic", [Commit({"a.txt": "t\n"}, "topic")], start="main", env=env)
    assert git(repo, env, "rev-parse", "topic", "topic~1") == f"{tip}\n{base}"
    assert git(repo, env, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert git(repo, env, "status", "--porcelain") == ""
    [later] = git_history.import_commits(repo, "topic", [Commit({"b.txt": "b\n"}, "more")], start="topic", env=env)
    assert git(repo, env, "rev-parse", "topic~1") == tip and later != tip


def test_only_the_first_of_several_commits_starts_from_start(tmp_path: Path, env: dict[str, str]) -> None:
    repo = tmp_path / "repo"
    [base] = git_history.new_repository(repo, [Commit({"a.txt": "a\n"}, "base")], env=env)
    first, second = git_history.import_commits(repo, "topic", [Commit({"a.txt": "1\n"}, "one"),
                                                               Commit({"a.txt": "2\n"}, "two")], start="main", env=env)
    assert git(repo, env, "rev-parse", "topic", "topic~1", "topic~2") == f"{second}\n{first}\n{base}"
    assert git(repo, env, "rev-list", "--count", "topic") == "3"


def test_no_commits_set_the_branch_to_start_and_need_one(tmp_path: Path, env: dict[str, str]) -> None:
    repo = tmp_path / "repo"
    [base] = git_history.new_repository(repo, [Commit({"a.txt": "a\n"}, "base")], env=env)
    assert git_history.import_commits(repo, "empty", [], start="main", env=env) == []
    assert git(repo, env, "rev-parse", "empty") == base
    with pytest.raises(ValueError, match="needs a start"):
        git_history.import_commits(repo, "none", [], env=env)


@pytest.mark.parametrize("path", ["", '"quoted', "two\nlines"])
def test_a_path_fast_import_cannot_take_as_written_is_refused(tmp_path: Path, env: dict[str, str], path: str) -> None:
    with pytest.raises(ValueError, match="fast-import path"):
        git_history.import_commits(tmp_path, "main", [Commit({path: "x"}, "bad")], env=env)


def test_a_failed_import_raises_with_gits_message(tmp_path: Path, env: dict[str, str]) -> None:
    repo = tmp_path / "repo"
    git_history.new_repository(repo, [Commit({"a.txt": "a\n"}, "base")], env=env)
    with pytest.raises(RuntimeError, match="git fast-import failed in .*missing"):
        git_history.import_commits(repo, "topic", [Commit({"a.txt": "t\n"}, "topic")], start="missing", env=env)


def test_a_session_builds_each_key_once_and_copies_it(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory,
                                                      env: dict[str, str]) -> None:
    built: list[Path] = []

    def build(root: Path) -> str:
        built.append(root)
        return git_history.new_repository(root, [Commit({"a.txt": "a\n"}, "base")], env=env)[0]

    key = f"test-git-history-{tmp_path.name}"
    first = git_history.copied(tmp_path_factory, key, tmp_path / "one", build)
    (tmp_path / "one" / "a.txt").write_text("changed\n", encoding="utf-8")
    second = git_history.copied(tmp_path_factory, key, tmp_path / "two", build)
    git_history.copied(tmp_path_factory, f"{key}-other", tmp_path / "three", build)
    assert len(built) == 2 and first == second
    assert git(tmp_path / "two", env, "rev-parse", "HEAD") == first
    assert git(tmp_path / "two", env, "status", "--porcelain") == ""
    assert (built[0] / "a.txt").read_text(encoding="utf-8") == "a\n"
    with pytest.raises(FileExistsError):
        git_history.copied(tmp_path_factory, key, tmp_path / "two", build)

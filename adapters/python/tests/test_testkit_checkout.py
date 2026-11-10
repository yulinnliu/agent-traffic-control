"""docs/ATC_HOME.md § Checkout: ``atc tmp checkout`` exports a commit into a registered scratch and builds its environment."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from atc_testkit import checkout, commands, scratch

pytestmark = pytest.mark.integration
TASK = "ATC-0008"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A repository whose commit holds a project at the top and one under ``sub``; the commit's full SHA."""
    for key in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(key)
    for key, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_CONFIG_GLOBAL": os.devnull}.items():
        monkeypatch.setenv(key, value)
    repo = tmp_path / "source"
    (repo / "sub").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "pyproject.toml").write_text('[project]\nname = "top"\n', encoding="utf-8")
    (repo / "uv.lock").write_text("lock\n", encoding="utf-8")
    (repo / "sub/pyproject.toml").write_text('[project]\nname = "inner"\n', encoding="utf-8")
    (repo / "sub/uv.lock").write_text("lock\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "x")
    return repo, git(repo, "rev-parse", "HEAD")


class Sync:
    """A stand-in for ``uv sync --locked`` that records its calls."""

    def __init__(self, code: int = 0, output: str = "") -> None:
        self.code, self.output, self.calls = code, output, []

    def __call__(self, argv, cwd, env):
        self.calls.append((list(argv), Path(cwd), dict(env)))
        return subprocess.CompletedProcess(argv, self.code, self.output, "")


def run(argv: list[str], sync: Sync, environ: dict[str, str]) -> tuple[int, list[str]]:
    lines: list[str] = []
    return checkout.checkout(argv, environ, sync, lines.append), lines


def test_a_commit_is_exported_registered_and_synced(source: tuple[Path, str], atc_tmp: Path) -> None:
    repo, sha = source
    sync = Sync()
    environ = {**os.environ, "VIRTUAL_ENV": "elsewhere", "UV_PROJECT_ENVIRONMENT": "elsewhere"}
    code, lines = run([sha[:10], "--task", TASK, "--worktree", str(repo / "sub")], sync, environ)
    place = atc_tmp / f"{TASK}-{sha[:7]}"
    tree = place / "tree"
    assert code == 0
    assert lines == [f"CHECKOUT {place.name} {tree} {sha}", f"SYNCED {tree / '.venv'}"]
    assert (tree / "sub/pyproject.toml").is_file() and not (tree / ".git").exists()
    [row] = scratch.registered(atc_tmp)
    assert row == scratch.Row("top", str(place), str(tree), row.created_utc, TASK, os.path.realpath(repo), sha)
    [(argv, cwd, env)] = sync.calls
    assert argv[1:] == ["sync", "--locked"] and cwd == tree
    assert "VIRTUAL_ENV" not in env and "UV_PROJECT_ENVIRONMENT" not in env


def test_the_scratch_name_and_the_lock_directory_can_be_named(source: tuple[Path, str], atc_tmp: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    repo, sha = source
    monkeypatch.chdir(repo)
    sync = Sync()
    code, _lines = run([sha, "--task", TASK, "--scratch", "mine", "--lock-dir", "sub"], sync, dict(os.environ))
    assert code == 0 and sync.calls[0][1] == atc_tmp / "mine/tree/sub"
    [row] = scratch.registered(atc_tmp)
    assert (row.project, row.tree) == ("inner", str((atc_tmp / "mine/tree/sub").resolve()))


@pytest.mark.parametrize(("argv", "token"), [
    (["HEAD", "--task", "atc-8"], "TASK_INVALID"),
    (["0" * 40, "--task", TASK], "SHA_NOT_FOUND"),
    (["HEAD", "--task", TASK, "--lock-dir", ".."], "LOCK_DIR_OUTSIDE_TREE"),
    (["HEAD", "--task", TASK, "--scratch", "../out"], "SCRATCH_OUTSIDE_TMP"),
])
def test_a_refusal_before_the_export_creates_nothing(source: tuple[Path, str], atc_tmp: Path, argv: list[str],
                                                    token: str, capsys: pytest.CaptureFixture[str]) -> None:
    repo, _sha = source
    sync = Sync()
    assert commands.main(["tmp", "checkout", *argv, "--worktree", str(repo)]) == 9
    assert capsys.readouterr().err.startswith(f"{token} ")
    assert not sync.calls and scratch.registered(atc_tmp) == []
    assert [entry.name for entry in atc_tmp.iterdir() if entry.is_dir() and entry.name != "systmp"] == []


def test_outside_a_repository_is_refused(tmp_path: Path, atc_tmp: Path) -> None:
    with pytest.raises(checkout.Refusal) as refused:
        run(["HEAD", "--task", TASK, "--worktree", str(tmp_path)], Sync(), dict(os.environ))
    assert refused.value.token == "NOT_IN_REPOSITORY"


def test_an_existing_tree_is_refused_and_kept(source: tuple[Path, str], atc_tmp: Path) -> None:
    repo, sha = source
    kept = atc_tmp / f"{TASK}-{sha[:7]}/tree/keep.txt"
    kept.parent.mkdir(parents=True)
    kept.write_text("x", encoding="utf-8")
    with pytest.raises(checkout.Refusal) as refused:
        run([sha, "--task", TASK, "--worktree", str(repo)], Sync(), dict(os.environ))
    assert refused.value.token == "SCRATCH_TREE_EXISTS" and f"atc tmp clean {TASK}-{sha[:7]}" in refused.value.detail
    assert kept.read_text(encoding="utf-8") == "x"


def test_a_missing_lock_and_a_failed_sync_leave_the_scratch_registered(source: tuple[Path, str],
                                                                       atc_tmp: Path) -> None:
    repo, sha = source
    git(repo, "rm", "-q", "uv.lock")
    git(repo, "commit", "-q", "-m", "no lock")
    with pytest.raises(checkout.Refusal) as refused:
        run(["HEAD", "--task", TASK, "--worktree", str(repo)], Sync(), dict(os.environ))
    assert refused.value.token == "LOCK_MISSING" and "AGENTS.md" in refused.value.detail
    assert refused.value.code == 9 and len(scratch.registered(atc_tmp)) == 1
    output = "\n".join(f"line {n}" for n in range(30))
    with pytest.raises(checkout.Refusal) as refused:
        run([sha, "--task", TASK, "--worktree", str(repo)], Sync(2, output), dict(os.environ))
    assert (refused.value.token, refused.value.code) == ("SYNC_FAILED", 1)
    assert "exited 2" in refused.value.detail and "line 29" in refused.value.detail
    assert "line 9\n" not in refused.value.detail
    assert len(scratch.registered(atc_tmp)) == 2


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv on PATH")
def test_a_real_sync_builds_the_environment(tmp_path: Path, atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(key)
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "plain"\nversion = "0"\nrequires-python = ">=3.12"\n'
                                         'dependencies = []\n', encoding="utf-8")
    environ = {**os.environ, "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never", "UV_PYTHON": os.path.realpath(
        os.sys.executable)}
    subprocess.run(["uv", "lock"], cwd=repo, env=environ, check=True, capture_output=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "x")
    lines: list[str] = []
    assert checkout.checkout(["HEAD", "--task", TASK, "--worktree", str(repo)], environ, say=lines.append) == 0
    assert (Path(lines[1].removeprefix("SYNCED ")) / "pyvenv.cfg").is_file()

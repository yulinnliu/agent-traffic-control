"""docs/ATC_HOME.md § Checkout: ``atc test ID --sha`` exports a commit into a registered scratch and builds its environment."""
from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import threading

import pytest

from atc_testkit import checkout, scratch

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
                                                    token: str) -> None:
    repo, _sha = source
    sync = Sync()
    with pytest.raises(checkout.Refusal) as refused:
        run([*argv, "--worktree", str(repo)], sync, dict(os.environ))
    assert (refused.value.token, refused.value.code) == (token, 9)
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
    assert refused.value.token == "SCRATCH_TREE_EXISTS" and f"atc test {TASK} --clean" in refused.value.detail
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


ARCHIVE_STAND_IN = """import sys
sys.stderr.buffer.write(b"w" * int(sys.argv[3])); sys.stderr.buffer.write(sys.argv[4].encode()); sys.stderr.flush()
sys.stdout.buffer.write(open(sys.argv[1], "rb").read()); sys.stdout.buffer.write(bytes(int(sys.argv[2])))
sys.stdout.flush(); sys.exit(int(sys.argv[5]))
"""


@pytest.fixture()
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Runs ``export`` against a stand-in ``git archive``: a one-file tar, ``tail`` bytes after it, ``noise``
    bytes and ``message`` on stderr, exit ``code``; the export finishes within the timeout or the test fails."""
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.PAX_FORMAT) as tar:
        info = tarfile.TarInfo("a.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"a"))
    archive_file = tmp_path / "archive.tar"
    archive_file.write_bytes(payload.getvalue())
    started: list[subprocess.Popen] = []
    real = subprocess.Popen

    def run(tail: int, noise: int, message: str = "", code: int = 0) -> tuple[Path, list[BaseException]]:
        def popen(argv, **kwargs):
            if "archive" not in argv:
                return real(argv, **kwargs)
            started.append(real([sys.executable, "-c", ARCHIVE_STAND_IN, str(archive_file), str(tail), str(noise),
                                 message, str(code)], **kwargs))
            return started[-1]
        monkeypatch.setattr(checkout.subprocess, "Popen", popen)
        tree = tmp_path / "tree"
        raised: list[BaseException] = []

        def target() -> None:
            try:
                checkout.export(tmp_path, "HEAD", tree)
            except BaseException as error:  # noqa: BLE001 - handed to the test thread
                raised.append(error)
        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        worker.join(30)
        assert not worker.is_alive(), "export deadlocked on the stand-in git archive"
        return tree, raised

    yield run
    for process in started:
        process.kill()
        process.wait()


def test_the_export_drains_what_git_writes_after_the_archive_and_on_stderr(stand_in) -> None:
    tree, raised = stand_in(tail=1 << 20, noise=1 << 20)
    assert raised == [] and (tree / "a.txt").read_text(encoding="utf-8") == "a"


def test_a_failed_git_archive_is_refused_with_its_stderr(stand_in) -> None:
    _tree, [refused] = stand_in(tail=10240, noise=0, message="fatal: not a tree object", code=128)
    assert isinstance(refused, checkout.Refusal) and refused.token == "EXPORT_FAILED"
    assert "fatal: not a tree object" in refused.detail


def test_a_bad_ref_is_refused_with_gits_stderr_before_any_archive_bytes(source: tuple[Path, str],
                                                                       tmp_path: Path) -> None:
    repo, _sha = source
    with pytest.raises(checkout.Refusal) as refused:
        checkout.export(repo, "no-such-ref", tmp_path / "tree")
    assert refused.value.token == "EXPORT_FAILED"
    assert "; git: fatal: " in refused.value.detail and "no-such-ref" in refused.value.detail.split("; git: ")[1]


def test_a_failed_export_leaves_the_scratch_registered_to_its_task(source: tuple[Path, str], atc_tmp: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    repo, sha = source

    def failing(worktree, commit, tree):
        tree.mkdir(parents=True)
        raise checkout.Refusal("EXPORT_FAILED", "stand-in")
    monkeypatch.setattr(checkout, "export", failing)
    with pytest.raises(checkout.Refusal, match="EXPORT_FAILED"):
        run([sha, "--task", TASK, "--worktree", str(repo), "--lock-dir", "sub"], Sync(), dict(os.environ))
    place = atc_tmp / f"{TASK}-{sha[:7]}"
    [row] = scratch.registered(atc_tmp)
    assert (row.scratch, row.task, row.project, row.commit) == (str(place), TASK, "inner", sha)
    assert place.is_dir()


@pytest.mark.parametrize(("lock_dir", "expected"), [(".", "top"), ("sub", "inner"), ("absent", "absent")])
def test_the_project_name_is_read_from_the_commit(source: tuple[Path, str], lock_dir: str, expected: str) -> None:
    repo, sha = source
    assert checkout.committed_project_name(repo, sha, Path(lock_dir)) == expected

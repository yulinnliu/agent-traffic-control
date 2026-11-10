"""``atc tmp checkout``: a commit exported into a registered scratch, its environment built (docs/ATC_HOME.md § Checkout)."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

from . import locations, scratch
from .git import git_env
from .locations import Refusal

TASK_ID = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+")
SHORT_SHA = 7
SYNC_TAIL = 20
TREE = "tree"
LOCK_FILE = "uv.lock"
ENVIRONMENT_VARIABLES = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
SyncRun = Callable[[Sequence[str], Path, Mapping[str, str]], subprocess.CompletedProcess]


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True, env=git_env(),
                          stdin=subprocess.DEVNULL, creationflags=scratch.WINDOW_HIDDEN)


def resolve_commit(worktree: Path, sha: str) -> str:
    """The full object ID ``sha`` names in ``worktree``; ``SHA_NOT_FOUND`` otherwise."""
    found = _git(worktree, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if found.returncode != 0:
        raise Refusal("SHA_NOT_FOUND", f"{sha} names no commit in {worktree}")
    return found.stdout.strip()


def source_worktree(argument: str | None) -> Path:
    """The top of the Git working tree that holds ``argument``, the working directory by default."""
    start = Path(os.path.realpath(argument or os.getcwd()))
    top = _git(start, "rev-parse", "--show-toplevel") if start.is_dir() else None
    if top is None or top.returncode != 0:
        raise Refusal("NOT_IN_REPOSITORY", f"{start} is in no Git working tree")
    return Path(os.path.realpath(top.stdout.strip()))


def export(worktree: Path, commit: str, tree: Path) -> None:
    """``git archive`` of ``commit`` unpacked into ``tree``, which must not exist."""
    tree.mkdir(parents=True)
    archive = subprocess.Popen(["git", "-C", str(worktree), "archive", "--format=tar", commit],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                               env=git_env(), creationflags=scratch.WINDOW_HIDDEN)
    try:
        with tarfile.open(fileobj=archive.stdout, mode="r|") as stream:
            stream.extractall(tree, filter="data")
    except (tarfile.TarError, OSError) as error:
        archive.kill()
        archive.wait()
        raise Refusal("EXPORT_FAILED", f"{commit} into {tree}: {error}") from error
    errors = archive.stderr.read().decode("utf-8", errors="replace").strip()
    if archive.wait() != 0:
        raise Refusal("EXPORT_FAILED", f"{commit} into {tree}: {errors}")


def _run_sync(argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), cwd=cwd, env=dict(env), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, creationflags=scratch.WINDOW_HIDDEN)


def sync(directory: Path, environ: Mapping[str, str], run: SyncRun | None = None) -> Path:
    """``uv sync --locked`` in ``directory``; the environment's directory, or a refusal naming the preparation."""
    if not (directory / LOCK_FILE).is_file():
        raise Refusal("LOCK_MISSING", f"{directory} has no {LOCK_FILE}; prepare the tree's environment as the "
                                      "project's AGENTS.md says, or name the directory that holds it with --lock-dir")
    uv = shutil.which("uv", path=environ.get("PATH"))
    if uv is None:
        raise Refusal("UV_NOT_FOUND", "uv is not on PATH")
    env = {key: value for key, value in environ.items() if key not in ENVIRONMENT_VARIABLES}
    done = (run or _run_sync)([uv, "sync", "--locked"], directory, env)
    if done.returncode != 0:
        tail = (done.stdout + done.stderr).strip().splitlines()[-SYNC_TAIL:]
        raise Refusal("SYNC_FAILED", "\n".join([f"uv sync --locked in {directory} exited {done.returncode}:", *tail]),
                      1)
    return directory / ".venv"


def checkout(argv: Sequence[str], environ: Mapping[str, str] | None = None, run: SyncRun | None = None,
             say: Callable[[str], None] = print) -> int:
    """``atc tmp checkout <sha> --task <ID>``: export, register, then sync; refusals exit 9, a failed sync 1."""
    parser = argparse.ArgumentParser(
        prog="atc tmp checkout", allow_abbrev=False,
        description="Export <sha> into <tmp>/<scratch>/tree, register the scratch with its task, source worktree "
                    "and commit, and build its environment with `uv sync --locked` (docs/ATC_HOME.md § Checkout).",
        epilog="Exit: 0 when the environment is built; 1 for a failed sync; 9 for a refusal.")
    parser.add_argument("sha", help="the commit to export: a full SHA or a unique prefix")
    parser.add_argument("--task", required=True, help="the task ID the scratch belongs to, for example ATC-0008")
    parser.add_argument("--worktree", help="the repository that holds <sha> (default: the working directory's)")
    parser.add_argument("--scratch", help="scratch name (default: <task>-<first 7 characters of the commit>)")
    parser.add_argument("--lock-dir", default=".",
                        help="the directory under the tree that holds uv.lock, where `atc test run` runs (default: the tree)")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    if not TASK_ID.fullmatch(args.task):
        raise Refusal("TASK_INVALID", f"{args.task!r} is not a task ID such as ATC-0008")
    worktree = source_worktree(args.worktree)
    commit = resolve_commit(worktree, args.sha)
    root = locations.locate(environ).tmp
    place = scratch.resolve_scratch(args.scratch or f"{args.task}-{commit[:SHORT_SHA]}", root)
    tree = place / TREE
    lock_dir = (tree / args.lock_dir).resolve()
    if not lock_dir.is_relative_to(tree.resolve()):
        raise Refusal("LOCK_DIR_OUTSIDE_TREE", f"{args.lock_dir} is outside {tree}")
    if tree.exists():
        raise Refusal("SCRATCH_TREE_EXISTS", f"{tree} exists; remove the scratch with `atc tmp clean {place.name}` "
                                             "or name another with --scratch")
    export(worktree, commit, tree)
    scratch.register(root, place, lock_dir, task=args.task, source=str(worktree), commit=commit,
                     project=scratch.project_name(lock_dir))
    say(f"CHECKOUT {place.name} {tree} {commit}")
    venv = sync(lock_dir, environ, run)
    say(f"SYNCED {venv}")
    return 0

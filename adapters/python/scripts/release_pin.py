"""The release-pin guard: the installed `atc` acts on a project only while its `.atc/atc.lock` pins the release
the host runs (docs/WORKFLOW.md § Installing a release).

The guard is on only when `atc` runs from a release copy; a development copy (`uv run atc` in a topic worktree)
and an `atc` still installed from the canonical checkout have no installed release to compare. The workspace
root is never compared: it pins no revision, its `main` is the governance itself. Guarded are the commands that
run the task flow under the project's contract, their `--check` rehearsals included: `task create`, `update`,
`resume` and `close`, `test ID` (but `--clean`, `--list` and `--private`), `integrate` and `push`. Everything
else stays usable: `doctor`, `guard`, `task list` read; `sync` and `install` are the fix; `init`, `add`,
`remove` manage the map.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import checkout_guard
import cli_conventions

ROOT_NAME = "root"
TASK_GUARDED = ("create", "update", "resume", "close")
TEST_EXEMPT = ("--clean", "--list", "--private")


@dataclass(frozen=True)
class Release:
    tag: str
    commit: str
    copy: Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in checkout_guard.REPOSITORY_ENV}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, check=False)


def installed(code: Path) -> Release | None:
    """The release ``code`` is, when it is a release copy: its directory name and its HEAD commit."""
    if not checkout_guard.release_copy(code):
        return None
    head = _git(code, "rev-parse", "HEAD")
    return Release(code.name, head.stdout.strip(), code) if head.returncode == 0 else None


def guarded(command: str, rest: list[str]) -> bool:
    head = rest[:rest.index("--")] if "--" in rest else rest
    if any(arg in ("-h", "--help") for arg in head):
        return False
    if command == "task":
        return bool(head) and head[0] in TASK_GUARDED
    if command == "test":
        return not any(arg in TEST_EXEMPT for arg in head)
    return command in ("integrate", "push")


PARSERS = {"task": "task_records", "test": "task_tests", "integrate": "git_integrate", "push": "push"}


def parsed(command: str, head: list[str]):
    """``head`` as the command's own parser reads it, so an ID is taken only from its ID position; None when it does
    not parse, which the command then reports."""
    module = __import__(PARSERS[command])
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return module.build_parser().parse_known_args(head)[0]
    except SystemExit:
        return None


def targets(command: str, rest: list[str], ws, cwd: str) -> list:
    """The projects the call acts on; empty when they cannot be told, which the command then reports."""
    import managed_worktree as hw
    import task_records
    head = rest[:rest.index("--")] if "--" in rest else rest
    args = parsed(command, head)
    if args is None:
        return []
    if command == "push":
        if not args.names:
            return [hw.root_project(ws), *ws.projects.values()]
        return [ws.projects[name] for name in args.names if name in ws.projects]
    try:
        if command == "task" and head[:1] == ["create"]:
            return [task_records.project_of(ws, cwd, args.project)]
        task_id = getattr(args, "task", None)
        if isinstance(task_id, str) and task_records.RECORD_ID.fullmatch(task_id):
            return [task_records.project_of_task(ws, task_id)]
    except task_records.Refusal:
        return []
    return []


def pin(project) -> str | None:
    """The revision the project's `.atc/atc.lock` pins; None when it has no readable lock."""
    import validate_workspace as validator
    try:
        lock = validator.read_lock(Path(project.repo))
    except (OSError, ValueError):
        return None
    return lock["revision"].strip().lower() if lock else None


def release_of(root: Path, revision: str) -> str | None:
    """The release tag naming ``revision`` in the workspace root repository, else None."""
    found = _git(root, "tag", "--points-at", revision, "--list", "v*")
    tags = [line.strip() for line in found.stdout.splitlines() if line.strip()] if found.returncode == 0 else []
    return sorted(tags)[-1] if tags else None


def mismatch(project, release: Release, root: Path) -> cli_conventions.Refusal | None:
    if project.name == ROOT_NAME:
        return None
    pinned = pin(project)
    if pinned == release.commit.lower():
        return None
    installed_line = f"the installed atc is {release.tag} ({release.commit[:12]})"
    if pinned is None:
        return cli_conventions.Refusal("RELEASE_MISMATCH", f"{project.name} pins no revision (.atc/atc.lock); "
                                       f"{installed_line}", f"atc sync {project.name}")
    tag = release_of(root, pinned)
    detail = f"{project.name} pins {tag or 'no release'} ({pinned[:12]}) in .atc/atc.lock; {installed_line}"
    if ahead(root, release.commit, pinned):
        install = f"atc install {tag}" if tag else f"atc install of the release tag at {pinned[:12]}"
        return cli_conventions.Refusal("RELEASE_MISMATCH", detail,
                                       f"the project is ahead of the installed release; the Owner runs {install}")
    return cli_conventions.Refusal("RELEASE_MISMATCH", detail,
                                   f"atc sync {project.name} moves it to the installed release")


def ahead(root: Path, installed: str, pinned: str) -> bool:
    """Whether ``pinned`` descends from the installed commit, which sync never moves a project back from."""
    return _git(root, "merge-base", "--is-ancestor", installed, pinned).returncode == 0


def check(command: str, rest: list[str], root: Path, code: Path, cwd: str,
          environ: dict[str, str]) -> cli_conventions.Refusal | None:
    """The refusal for this call, or None when it may run."""
    if not guarded(command, rest):
        return None
    release = installed(code)
    if release is None:
        return None
    import managed_worktree as hw
    try:
        ws = hw.load_workspace(root, environ)
    except hw.Stop:
        return None
    for project in targets(command, rest, ws, cwd):
        refusal = mismatch(project, release, root)
        if refusal is not None:
            return refusal
    return None

"""The release-pin guard: the installed `atc` acts on a project only while its `.atc/atc.lock` pins the release
the host runs (docs/WORKFLOW.md § Installing a release).

The installed release is the commit `uv tool install` recorded in the distribution's `direct_url.json` (PEP 610)
when it installed `atc` from a tag. An editable development install (`uv run atc` in a topic worktree) and an
install without a recorded commit have no installed release to compare. The workspace root is never compared: it
pins no revision, its `main` is the governance itself. Guarded are the commands that run the task flow under the
project's contract, their `--check` rehearsals included: `task create`, `update`, `resume` and `close`, `test ID`
(but `--clean`, `--list` and `--private`), `policy render`, `integrate` and `push`. Everything else stays usable:
`doctor`, `guard`, `task list` read; `sync` is the fix; `init`, `add`, `remove` manage the map.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import checkout_guard
import cli_conventions

DISTRIBUTION = "agent-traffic-control"
SUBDIRECTORY = "adapters/python"
ROOT_NAME = "root"
TASK_GUARDED = ("create", "update", "resume", "close")
TEST_EXEMPT = ("--clean", "--list", "--private")
# `direct_url()` reads the running distribution's record when no record is named.
READ = object()


@dataclass(frozen=True)
class Release:
    """The commit `uv tool install` installed, the revision it was asked for (a tag, usually) and the source."""
    commit: str
    tag: str | None
    url: str
    subdirectory: str = SUBDIRECTORY

    @property
    def name(self) -> str:
        return self.tag or self.commit[:12]


def direct_url() -> dict | None:
    """The running `atc`'s PEP 610 record, `direct_url.json` of its distribution; None without one."""
    from importlib import metadata
    try:
        text = metadata.distribution(DISTRIBUTION).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    try:
        record = json.loads(text) if text else None
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def installed(record: object = READ) -> Release | None:
    """The release `atc` was installed from: the commit and requested revision of a VCS install, else None."""
    record = direct_url() if record is READ else record
    vcs = record.get("vcs_info") if isinstance(record, dict) else None
    commit = vcs.get("commit_id") if isinstance(vcs, dict) else None
    if not isinstance(commit, str) or not commit:
        return None
    requested = vcs.get("requested_revision")
    tag = requested if isinstance(requested, str) and requested and not commit.startswith(requested) else None
    subdirectory = record.get("subdirectory")
    return Release(commit, tag, str(record.get("url", "")),
                   subdirectory if isinstance(subdirectory, str) and subdirectory else SUBDIRECTORY)


def editable(record: object = READ) -> Path | None:
    """The source directory of an editable install (`uv run` in a worktree, `uv tool install -e`), else None."""
    record = direct_url() if record is READ else record
    info = record.get("dir_info") if isinstance(record, dict) else None
    url = record.get("url") if isinstance(record, dict) else None
    if not (isinstance(info, dict) and info.get("editable") is True and isinstance(url, str)):
        return None
    parts = urlsplit(url)
    return Path(url2pathname(parts.path)) if parts.scheme == "file" else None


def install_command(source: str, revision: str, subdirectory: str = SUBDIRECTORY) -> str:
    """The Owner's command that installs ``revision`` of the `atc` at ``source`` as this host's `atc`."""
    return f'uv tool install --force "git+{source}@{revision}#subdirectory={subdirectory}"'


def reinstall_command(record: object = READ) -> str:
    """How the Owner reinstalls this `atc`: the release it was installed from, or the worktree's environment."""
    release = installed(record)
    if release is not None and release.url:
        return install_command(release.url, release.tag or release.commit, release.subdirectory)
    source = editable(record)
    if source is not None:
        return f'uv sync --locked --directory "{source}"'
    return install_command("<source>", "<tag>")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in checkout_guard.REPOSITORY_ENV}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, check=False)


def guarded(command: str, rest: list[str]) -> bool:
    head = rest[:rest.index("--")] if "--" in rest else rest
    if any(arg in ("-h", "--help") for arg in head):
        return False
    if command == "task":
        return bool(head) and head[0] in TASK_GUARDED
    if command == "test":
        return not any(arg in TEST_EXEMPT for arg in head)
    if command == "policy":
        return head[:1] == ["render"]
    return command in ("integrate", "push")


PARSERS = {"task": "task_records", "test": "task_tests", "policy": "policy_render", "integrate": "git_integrate",
           "push": "push"}


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
    """The release tag naming ``revision`` in the repository ``root``, else None."""
    found = _git(root, "tag", "--points-at", revision, "--list", "v*")
    tags = [line.strip() for line in found.stdout.splitlines() if line.strip()] if found.returncode == 0 else []
    return sorted(tags)[-1] if tags else None


def history(root: Path) -> Path:
    """Where the release tags and commits of the workspace at ``root`` are read: its content repository, read
    without a fetch (`content.repository`), else ``root`` itself."""
    import content
    try:
        return content.repository(root, fetch=False)
    except content.ContentError:
        return root


def mismatch(project, release: Release, root: Path) -> cli_conventions.Refusal | None:
    """Why ``project`` does not pin ``release``, or None; ``root`` is the repository holding the release history."""
    if project.name == ROOT_NAME:
        return None
    pinned = pin(project)
    if pinned == release.commit.lower():
        return None
    installed_line = f"the installed atc is {release.name} ({release.commit[:12]})"
    if pinned is None:
        return cli_conventions.Refusal("RELEASE_MISMATCH", f"{project.name} pins no revision (.atc/atc.lock); "
                                       f"{installed_line}", f"atc sync {project.name}")
    tag = release_of(root, pinned)
    detail = f"{project.name} pins {tag or 'no release'} ({pinned[:12]}) in .atc/atc.lock; {installed_line}"
    if ahead(root, release.commit, pinned):
        install = (install_command(release.url, tag, release.subdirectory) if tag and release.url
                   else f"uv tool install of the release tag at {pinned[:12]}")
        return cli_conventions.Refusal("RELEASE_MISMATCH", detail,
                                       f"the project is ahead of the installed release; the Owner runs {install}")
    return cli_conventions.Refusal("RELEASE_MISMATCH", detail,
                                   f"atc sync {project.name} moves it to the installed release")


def ahead(root: Path, installed: str, pinned: str) -> bool:
    """Whether ``pinned`` descends from the installed commit, which sync never moves a project back from."""
    return _git(root, "merge-base", "--is-ancestor", installed, pinned).returncode == 0


def check(command: str, rest: list[str], root: Path, release: Release | None, cwd: str,
          environ: dict[str, str]) -> cli_conventions.Refusal | None:
    """The refusal for this call, or None when it may run; ``release`` is the installed one (`installed()`)."""
    if release is None or not guarded(command, rest):
        return None
    import managed_worktree as hw
    try:
        ws = hw.load_workspace(root, environ)
    except hw.Stop:
        return None
    repo = history(root)
    for project in targets(command, rest, ws, cwd):
        refusal = mismatch(project, release, repo)
        if refusal is not None:
            return refusal
    return None

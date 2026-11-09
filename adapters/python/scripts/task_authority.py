"""The task and authority of one `atc integrate` run (`docs/TASKS.md` § Authority on `main`).

`git_integrate.py` calls these after it has resolved the repository, the
branch and the reviewed SHA; each check raises `Stop` with the token the run
stops on. Every read goes through Git objects, never a working tree.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from git_preflight import git, is_ancestor

TASKS_DIR = ".atc/tasks"
RECORD_ID = re.compile(r"^(?P<prefix>[A-Z][A-Z0-9]{1,7})-[0-9]{4}$")
GRANTS = ("push_topic", "integrate_main", "push_main", "deploy")
UNREADABLE = {"__unreadable__": True}


class Stop(Exception):
    def __init__(self, token: str, detail: str) -> None:
        super().__init__(f"{token} {detail}")
        self.token, self.detail = token, detail


def record_path(task_id: str) -> str:
    return f"{TASKS_DIR}/{task_id}.yaml"


def is_record_file(path: str) -> bool:
    return path.startswith(f"{TASKS_DIR}/") and path.endswith(".yaml") and "/" not in path[len(TASKS_DIR) + 1:]


def show(repo: Path, revision: str, path: str) -> str | None:
    shown = git(repo, "show", f"{revision}:{path}", check=False)
    return shown.stdout if shown.returncode == 0 else None


def parse(text: str | None) -> dict | None:
    """The record ``text`` holds; `None` when absent, `UNREADABLE` when it is no YAML mapping."""
    if text is None:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return UNREADABLE
    return data if isinstance(data, dict) else UNREADABLE


def grant_fields(record: dict | None) -> str | None:
    """`authority` and `authority_basis` as one comparable value; `None` for no record."""
    if record is None:
        return None
    return json.dumps([record.get("authority"), record.get("authority_basis")], sort_keys=True, default=str)


def grants_beyond_commit(record: dict) -> bool:
    authority = record.get("authority")
    return not isinstance(authority, dict) or any(authority.get(name) is not False for name in GRANTS)


def changed_paths(repo: Path, base: str, tip: str) -> list[str]:
    out = git(repo, "diff", "--name-only", "--no-renames", "-z", base, tip).stdout
    return [path for path in out.split("\0") if path]


@dataclass
class Range:
    """What the range ``base..reviewed`` changes among the task records."""

    paths: list[str]
    record_files: list[str] = field(default_factory=list)

    @property
    def touches_tasks(self) -> bool:
        return any(path.startswith(f"{TASKS_DIR}/") for path in self.paths)

    @property
    def records_only(self) -> bool:
        """Every changed path is under `.atc/tasks/`, and at least one is a record file."""
        return bool(self.record_files) and all(path.startswith(f"{TASKS_DIR}/") for path in self.paths)

    @property
    def task_ids(self) -> list[str]:
        return sorted(Path(path).stem for path in self.record_files)


def scan(repo: Path, base: str, reviewed: str) -> Range:
    paths = changed_paths(repo, base, reviewed)
    return Range(paths, [path for path in paths if is_record_file(path)])


def check_range(repo: Path, rng: Range, main_tip: str, reviewed: str, may_change: bool) -> None:
    """`TASK_ID_TAKEN` and `TASK_AUTHORITY_CHANGED` for each record file the range changes.

    ``may_change`` holds for a records-only range under `--owner-words`, the
    only range that may change a grant on `main` or add a record granting more
    than `edit` and `commit`.
    """
    for path in rng.record_files:
        task_id = Path(path).stem
        on_main = parse(show(repo, main_tip, path))
        new = parse(show(repo, reviewed, path))
        if on_main is not None and new is not None and on_main is not UNREADABLE and new is not UNREADABLE \
                and on_main.get("branch") != new.get("branch"):
            raise Stop("TASK_ID_TAKEN", f"main binds {task_id} to branch {on_main.get('branch')}; the range binds it "
                                        f"to {new.get('branch')}")
        if may_change:
            continue
        if on_main is not None:
            if new is None:
                raise Stop("TASK_AUTHORITY_CHANGED", f"the range removes or renames {path}, which exists on main")
            if grant_fields(new) != grant_fields(on_main):
                raise Stop("TASK_AUTHORITY_CHANGED", f"the range changes authority or authority_basis of {path}, "
                                                     "which exists on main")
        elif new is not None and grants_beyond_commit(new):
            raise Stop("TASK_AUTHORITY_CHANGED", f"the range adds {path} granting more than edit and commit")


def check_record(repo: Path, task_id: str, reviewed: str, branch: str) -> None:
    """`TASK_NOT_FOUND` and `TASK_BRANCH_MISMATCH` for the record `--task` names."""
    record = parse(show(repo, reviewed, record_path(task_id)))
    if record is None:
        raise Stop("TASK_NOT_FOUND", f"the reviewed commit {reviewed} holds no {record_path(task_id)}")
    bound = record.get("branch")
    if bound != branch:
        raise Stop("TASK_BRANCH_MISMATCH", f"{record_path(task_id)} binds branch {bound}, but the integrated branch "
                                           f"is {branch}")


def grant_commit(repo: Path, main_tip: str, path: str) -> str | None:
    """The newest first-parent commit of `main` whose parsed grant differs from its first parent's copy."""
    log = git(repo, "log", "--first-parent", "--format=%H", main_tip, "--", path).stdout.split()
    for sha in log:
        parent = git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^1", check=False).stdout.strip()
        before = parse(show(repo, parent, path)) if parent else None
        if grant_fields(parse(show(repo, sha, path))) != grant_fields(before):
            return sha
    return None


def integrated_binding(repo: Path, grant: str, task_id: str, integrations: list[dict]) -> dict | None:
    """The records-only `--owner-words` line of ``task_id`` whose new commits include ``grant``."""
    for line in integrations:
        authority = line.get("authority")
        base = line.get("main_before")
        if (line.get("task") == task_id and line.get("records_only") is True
                and isinstance(authority, dict) and authority.get("kind") == "owner_words"
                and isinstance(base, str) and is_ancestor(repo, grant, line["candidate_sha"])
                and not is_ancestor(repo, grant, base)):
            return line
    return None


def record_authority(repo: Path, task_id: str, main: str, main_tip: str, integrations: list[dict]) -> dict:
    """The record on `main` as integration authority, or the refusal saying why it is none."""
    path = record_path(task_id)
    record = parse(show(repo, main_tip, path))
    if record is None or record is UNREADABLE:
        raise Stop("AUTHORITY_MISSING", f"{main} holds no readable {path}; integrate with --owner-words")
    authority = record.get("authority")
    if not isinstance(authority, dict) or authority.get("integrate_main") is not True:
        raise Stop("AUTHORITY_MISSING", f"{path} on {main} does not grant integrate_main; integrate with --owner-words")
    grant = grant_commit(repo, main_tip, path)
    if grant is None or integrated_binding(repo, grant, task_id, integrations) is None:
        raise Stop("AUTHORITY_NOT_INTEGRATED", f"the grant of {task_id} on {main} (commit {grant}) did not enter "
                                               f"{main} through a records-only atc integrate with --owner-words "
                                               "in this clone; integrate with --owner-words")
    return {"kind": "record", "task_id": task_id}

#!/usr/bin/env python3
"""Task records: write, list and check `.atc/tasks/<task_id>.yaml` (docs/TASKS.md).

`plan` commits a planned record to the `backlog` branch, `rank` and `drop` change one there, and `start` moves
one into the topic worktree holding the working directory; `new` does both for an unplanned task (§ Backlog).
`list` reads the records on `main` and every local branch without a checkout and derives each task's status.
`check` reports the records and declaration in a working tree, one problem per line.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

import cli_conventions
import task_section

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "shared" / "policies" / "schemas" / "task.schema.json"
TASKS_DIR = ".atc/tasks"
MAIN = "main"
BACKLOG = "backlog"
STATUSES = ("closed", "abandoned", "planned", "open", "integrated", "bound", "unknown")
DONE = ("integrated", "closed")
NO_AUTHORITY = {"edit": False, "commit": False, "push_topic": False, "integrate_main": False,
                "push_main": False, "deploy": False}
CAS_ATTEMPTS = 5
SCOPE_WIDTH = 60
REMOTE_URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*://[^/]+/|[^/@:]+@[^/:]+:)(?:.*/)?([^/]+/[^/]+?)(?:\.git)?/?$")
REPOSITORY_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


# Refusals caused by the invocation or a fault; every other token is a STOP of a valid call (CLI.md § Exit codes).
ERROR_TOKENS = frozenset({"GIT_FAILED", "UNKNOWN_PROJECT", "RECORD_INVALID", "UNKNOWN_TASK", "WRITE_FAILED"})
# Refusals after the command changed something it could not undo (CLI.md § Exit codes, 3 PARTIAL).
PARTIAL_TOKENS = frozenset({"START_INCOMPLETE"})


class Refused(Exception):
    def __init__(self, token: str, detail: str) -> None:
        super().__init__(f"{token} {detail}")
        self.token = token
        self.detail = detail


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in REPOSITORY_ENV}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def git(repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None,
        input: str | None = None) -> subprocess.CompletedProcess:
    run = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, input=input,
                         encoding="utf-8", errors="replace", env={**_env(), **(env or {})}, check=False)
    if check and run.returncode != 0:
        raise Refused("GIT_FAILED", f"git {' '.join(args)} in {repo}: {(run.stderr or '').strip()}")
    return run


# check

def validator() -> Draft202012Validator:
    """The record schema, itself checked against Draft 2020-12 so a broken schema fails every check."""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def record_problems(name: str, text: str, prefix: str | None, schema: Draft202012Validator) -> list[str]:
    """Problems of one record file ``name`` holding ``text``; ``prefix`` is the declared one, if valid."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"invalid-yaml: {name}: {str(exc).splitlines()[0]}"]
    problems = []
    for error in sorted(schema.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path]):
        where = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"schema-violation: {name}: {where}: {error.message}")
    task_id = data.get("task_id") if isinstance(data, dict) else None
    if isinstance(task_id, str):
        if task_id != name.removesuffix(".yaml"):
            problems.append(f"id-mismatch: {name}: task_id {task_id} differs from the file name")
        if prefix and not task_id.startswith(f"{prefix}-"):
            problems.append(f"id-mismatch: {name}: task_id {task_id} lacks the declared prefix {prefix}")
    return problems


def declared_prefix(repo: Path) -> str | None:
    """The prefix `AGENTS.md` in ``repo`` declares; ``""`` for a section without a prefix line."""
    agents = repo / "AGENTS.md"
    return task_section.prefix(agents.read_text(encoding="utf-8")) if agents.is_file() else None


def main_prefix(repo: Path, main: str = "main") -> str | None:
    """The prefix `AGENTS.md` on ``main`` of ``repo`` declares, if any."""
    shown = git(repo, "show", f"refs/heads/{main}:AGENTS.md", check=False)
    return task_section.prefix(shown.stdout) or None if shown.returncode == 0 else None


def declaring(ws, task: str | None) -> list:
    """The repositories, the workspace root and the registered projects, whose `AGENTS.md` on ``main`` declares
    the prefix of the record ID ``task``."""
    import managed_worktree as hw
    import task_authority as ta
    record = ta.RECORD_ID.fullmatch(task or "")
    if not record:
        return []
    return [p for p in [hw.root_project(ws), *ws.projects.values()]
            if Path(p.repo).is_dir() and main_prefix(Path(p.repo)) == record["prefix"]]


def prefix_problem(declared: str | None) -> str | None:
    if declared == "":
        return f"no-prefix-line: AGENTS.md '{task_section.HEADING}' has no 'Task prefix: <PREFIX>' line"
    if declared is not None and not task_section.PREFIX_FORM.match(declared):
        return (f"invalid-prefix: AGENTS.md declares task prefix {declared!r}; a prefix is an uppercase letter "
                "followed by 1 to 7 uppercase letters or digits")
    return None


def problems(repo: Path) -> list[str]:
    """Problems of the declaration and the records in the working tree of ``repo``."""
    directory = repo / TASKS_DIR
    files = sorted(directory.glob("*.yaml")) if directory.is_dir() else []
    return _problems(declared_prefix(repo), [(path.name, path.read_bytes()) for path in files])


def tree_problems(repo: Path, tree: str) -> list[str]:
    """Problems of the declaration and the records in ``tree`` of ``repo``, the check `problems` makes of a
    checkout of it."""
    agents = git(repo, "cat-file", "-p", f"{tree}:AGENTS.md", check=False)
    declared = task_section.prefix(agents.stdout) if agents.returncode == 0 else None
    listed = git(repo, "ls-tree", "-z", tree, "--", f"{TASKS_DIR}/").stdout
    entries = []
    for line in filter(None, listed.split("\0")):
        meta, _, path = line.partition("\t")
        name = path.rsplit("/", 1)[-1]
        if meta.split()[1] == "blob" and name.endswith(".yaml"):
            entries.append((name, meta.split()[2]))
    entries.sort()
    blobs = read_blob_bytes(repo, [sha for _, sha in entries])
    return _problems(declared, [(name, blobs.get(sha, b"")) for name, sha in entries])


def _problems(declared: str | None, files: list[tuple[str, bytes]]) -> list[str]:
    """Problems of the declared prefix ``declared`` and the record ``files`` as (name, content) pairs."""
    found = [p for p in (prefix_problem(declared),) if p]
    if files and declared is None:
        found.append(f"no-declaration: {TASKS_DIR}/ holds {len(files)} record(s) but AGENTS.md has no "
                     f"'{task_section.HEADING}' section")
    valid_prefix = None if prefix_problem(declared) else declared
    schema = validator()
    for name, content in files:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            found.append(f"invalid-yaml: {name}: not UTF-8 ({exc.reason})")
            continue
        found.extend(record_problems(name, text, valid_prefix, schema))
    return found


# targets

def toplevel(directory: Path) -> Path | None:
    run = git(directory, "rev-parse", "--show-toplevel", check=False)
    return Path(run.stdout.strip()) if run.returncode == 0 and run.stdout.strip() else None


def common_dir(repo: Path) -> str:
    out = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return os.path.normcase(os.path.realpath(out))


def resolve(root: Path, environ: dict[str, str], cwd: str, project: str | None, every: bool = False,
            task: str | None = None, root_default: bool = False):
    """The targets as (name, main checkout) pairs, named as `atc worktree` names them.

    Without ``project``, a record ID ``task`` names the one repository declaring its prefix; with none
    or several, the working directory's project, or with ``root_default`` the root for a directory elsewhere in
    the canonical checkout.
    """
    import managed_worktree as hw
    try:
        ws = hw.load_workspace(root, environ)
    except hw.Stop as stop:
        raise Refused(stop.result.code, stop.result.detail) from None
    registered = f"registered: {', '.join(sorted(ws.projects)) or 'none'}"
    root_target = hw.root_project(ws)
    if every:
        return [(root_target.name, Path(ws.root))] + [
            (p.name, Path(p.repo)) for p in sorted(ws.projects.values(), key=lambda p: p.name) if Path(p.repo).is_dir()]
    if project == root_target.name:
        return [(root_target.name, Path(ws.root))]
    if project is not None:
        found = ws.projects.get(project)
        if found is None:
            raise Refused("UNKNOWN_PROJECT", f"{project}; {registered}")
        return [(found.name, Path(found.repo))]
    declared = declaring(ws, task) if task is not None else []
    if len(declared) == 1:
        return [(declared[0].name, Path(declared[0].repo))]
    found = hw.infer_project(ws, cwd)
    if found is None and root_default and hw.within(cwd, ws.root):
        return [(root_target.name, Path(ws.root))]
    if found is None:
        raise Refused("UNKNOWN_PROJECT", f"no --project and {cwd} is in no registered project; {registered}")
    return [(found.name, Path(found.repo))]


# new

def taken_numbers(worktree: Path, prefix: str) -> set[int]:
    """Numbers ``prefix`` already uses on any local or remote-tracking branch, the working tree and integrations."""
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d{{4}})$")
    stems = []
    for ref in git(worktree, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").stdout.split():
        listing = git(worktree, "ls-tree", "--name-only", ref, f"{TASKS_DIR}/", check=False).stdout
        stems += [Path(line).name.removesuffix(".yaml") for line in listing.splitlines() if line.endswith(".yaml")]
    directory = worktree / TASKS_DIR
    if directory.is_dir():
        stems += [p.stem for p in directory.glob("*.yaml")]
    if backlog_tip(worktree) is not None:
        history = git(worktree, "log", "--format=", "--name-only", f"refs/heads/{BACKLOG}", "--", f"{TASKS_DIR}/").stdout
        stems += [Path(line).name.removesuffix(".yaml") for line in history.splitlines() if line.endswith(".yaml")]
    import git_preflight
    stems += [r["task"] for r in git_preflight.read_integrations(worktree) if isinstance(r.get("task"), str)]
    return {int(m.group(1)) for stem in stems if (m := pattern.match(stem))}


def repository_identity(worktree: Path, repo: Path) -> dict[str, str]:
    url = git(worktree, "remote", "get-url", "origin", check=False).stdout.strip()
    if not url:
        return {"repository": repo.name, "repository_identity_status": "no origin remote; local repository name"}
    match = REMOTE_URL.match(url)
    if match:
        return {"repository": match.group(1)}
    return {"repository": repo.name, "repository_identity_status": f"origin {url} names no <owner>/<repo>; local repository name"}


def bound_governance(name: str, root: Path, worktree: Path) -> tuple[str, str]:
    """The governance (source, revision) a new task of ``name`` binds: the root's `main`, or a project's lock."""
    import validate_workspace as validator
    if name == "root":
        import sync_project
        return sync_project.governance_source(root), git(root, "rev-parse", f"refs/heads/{MAIN}").stdout.strip()
    lock_path = worktree / validator.ATC_LOCK
    try:
        lock = validator.read_lock(worktree)
    except (OSError, ValueError) as exc:
        raise Refused("NO_GOVERNANCE_BINDING", f"{lock_path}: {exc}; run atc sync") from None
    if lock is None:
        raise Refused("NO_GOVERNANCE_BINDING", f"{lock_path} is missing; run atc sync")
    return lock["source"], lock["revision"]


def new_hint() -> str:
    """What `atc task new` refused for its place says to do instead."""
    return "Run it inside the new worktree: atc task new --project <name> --scope <text>, root for the workspace root"


def topic_place(root: Path, environ: dict[str, str], cwd: str, project: str | None) -> tuple[Path, str, Path, str, str]:
    """The topic worktree holding ``cwd`` as (worktree, project name, main checkout, branch, prefix), or a refusal."""
    worktree = toplevel(Path(cwd))
    if worktree is None:
        raise Refused("NOT_IN_REPOSITORY", f"{cwd} is in no Git working tree")
    if os.path.normcase(os.path.realpath(worktree)) == os.path.normcase(os.path.realpath(root)):
        raise Refused("CHECKOUT_CANONICAL", f"{worktree} is the workspace root's canonical checkout; "
                                            "write the record in the topic's worktree under worktrees/root. "
                                            f"{new_hint()}")
    [(name, main_checkout)] = resolve(root, environ, cwd, project)
    if common_dir(worktree) != common_dir(main_checkout):
        raise Refused("WRONG_REPOSITORY", f"{worktree} is not a worktree of {name} ({main_checkout})")
    branch = git(worktree, "branch", "--show-current").stdout.strip()
    if not branch:
        raise Refused("DETACHED_HEAD", f"{worktree} is on no branch; a record binds the topic branch")
    if branch == MAIN:
        raise Refused("ON_MAIN", f"{worktree} is on {MAIN}; write the record on the topic branch")
    prefix = declared_prefix(worktree)
    if prefix is None:
        raise Refused("NO_DECLARATION", f"{worktree}/AGENTS.md has no '{task_section.HEADING}' section")
    if (problem := prefix_problem(prefix)) is not None:
        raise Refused("NO_PREFIX", problem)
    return worktree, name, main_checkout, branch, prefix


def dump(record: dict, prefix: str | None) -> str:
    """``record`` as YAML, refused unless it passes the record check."""
    text = yaml.safe_dump(record, sort_keys=False, allow_unicode=True)
    if found := record_problems(f"{record['task_id']}.yaml", text, prefix, validator()):
        raise Refused("RECORD_INVALID", "; ".join(found))
    return text


def new(root: Path, environ: dict[str, str], cwd: str, scope: str, project: str | None,
        version: str | None = None) -> Path:
    """An unplanned task: its ID issued through the backlog like a planned one's, then started at once."""
    worktree, name, main_checkout, branch, prefix = topic_place(root, environ, cwd, project)
    task_id = reserve(root, name, main_checkout, prefix, scope, scan=worktree)
    return bind(root, worktree, name, main_checkout, branch, prefix, task_id, None, version)


# backlog

def backlog_tip(repo: Path) -> str | None:
    run = git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{BACKLOG}", check=False)
    return run.stdout.strip() or None if run.returncode == 0 else None


def backlog_record(repo: Path, tip: str | None, task_id: str) -> dict | None:
    """The record ``task_id`` on the backlog at ``tip``; `None` when absent."""
    if tip is None:
        return None
    shown = git(repo, "show", f"{tip}:{TASKS_DIR}/{task_id}.yaml", check=False)
    if shown.returncode != 0:
        return None
    try:
        data = yaml.safe_load(shown.stdout)
    except yaml.YAMLError:
        data = None
    if not isinstance(data, dict):
        raise Refused("RECORD_INVALID", f"{BACKLOG}:{TASKS_DIR}/{task_id}.yaml is not a YAML mapping")
    return data


def planned(repo: Path, tip: str | None, task_id: str) -> dict:
    """The record ``task_id`` while it is planned on the backlog, or `NOT_PLANNED`."""
    record = backlog_record(repo, tip, task_id)
    if record is None:
        raise Refused("NOT_PLANNED", f"{task_id} has no record on the {BACKLOG} branch; atc task list --status planned")
    if isinstance(record.get("branch"), str) or isinstance(record.get("closure"), dict):
        raise Refused("NOT_PLANNED", f"{task_id} on the {BACKLOG} branch is bound or closed, not planned")
    return record


def backlog_commit(repo: Path, tip: str | None, task_id: str, text: str | None, message: str) -> bool:
    """One commit on the backlog writing (or, for ``text`` `None`, removing) the record of ``task_id``, moved
    there only while the branch is still at ``tip``; `False` when another writer moved it first."""
    path = f"{TASKS_DIR}/{task_id}.yaml"
    with tempfile.TemporaryDirectory() as scratch:
        index = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
        git(repo, "read-tree", *([tip] if tip else ["--empty"]), env=index)
        if text is None:
            git(repo, "update-index", "--force-remove", "--", path, env=index)
        else:
            stored = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input=text.encode("utf-8"),
                                    capture_output=True, env=_env(), check=False)
            if stored.returncode != 0:
                raise Refused("GIT_FAILED", f"git hash-object in {repo}: {stored.stderr.decode('utf-8', 'replace').strip()}")
            blob = stored.stdout.decode("ascii").strip()
            git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=index)
        tree = git(repo, "write-tree", env=index).stdout.strip()
    commit = git(repo, "commit-tree", tree, *(["-p", tip] if tip else []), "-m", message).stdout.strip()
    return git(repo, "update-ref", "-m", message, f"refs/heads/{BACKLOG}", commit, tip or "", check=False).returncode == 0


def backlog_write(repo: Path, change) -> None:
    """Applies ``change(tip) -> (task_id, text or None, message)`` to the backlog by compare-and-swap, reading the
    tip again whenever another writer moved it."""
    for _ in range(CAS_ATTEMPTS):
        tip = backlog_tip(repo)
        if backlog_commit(repo, tip, *change(tip)):
            return
    raise Refused("BACKLOG_BUSY", f"the {BACKLOG} branch of {repo} moved {CAS_ATTEMPTS} times while writing; run it again")


def reserve(root: Path, name: str, repo: Path, prefix: str, scope: str, scan: Path | None = None,
            rank: int | None = None, after: list[str] | None = None, notes: str | None = None,
            version: str | None = None) -> str:
    """Commits a new planned record to the backlog of ``repo`` and returns its ID: one past every number in use."""
    identity = repository_identity(scan or repo, repo)
    source = bound_governance(name, root, scan or repo)[0]
    issued: list[str] = []

    def change(tip: str | None) -> tuple[str, str, str]:
        taken = taken_numbers(scan or repo, prefix)
        unknown = [a for a in after or [] if not (m := re.fullmatch(rf"{re.escape(prefix)}-(\d{{4}})", a))
                   or int(m.group(1)) not in taken]
        if unknown:
            raise Refused("UNKNOWN_TASK", f"--after names {', '.join(unknown)}, which no record or integration of {name} holds")
        number = max(taken, default=0) + 1
        if number > 9999:
            raise Refused("TASK_IDS_EXHAUSTED", f"{prefix} has used every four-digit number")
        task_id = f"{prefix}-{number:04d}"
        record = {"task_id": task_id, **identity, "scope": scope, "governance_source": source,
                  "authority": dict(NO_AUTHORITY)}
        record.update({k: v for k, v in (("rank", rank), ("after", after), ("notes", notes), ("version", version))
                       if v is not None})
        issued[:] = [task_id]
        return task_id, dump(record, prefix), f"plan {task_id}"

    backlog_write(repo, change)
    return issued[0]


def bind(root: Path, worktree: Path, name: str, main_checkout: Path, branch: str, prefix: str, task_id: str,
         scope: str | None, version: str | None = None) -> Path:
    """Moves the planned record ``task_id`` from the backlog into ``worktree``, bound to its branch with `edit` and
    `commit`; the record is left uncommitted in the worktree.

    The worktree copy is written first and the backlog copy removed after it. When the removal fails, the written
    copy is deleted again and the removal's refusal stands; a copy that cannot be deleted is `START_INCOMPLETE`."""
    if not task_id.startswith(f"{prefix}-"):
        raise Refused("WRONG_REPOSITORY", f"{task_id} lacks the prefix {prefix} that {worktree}/AGENTS.md declares")
    path = worktree / TASKS_DIR / f"{task_id}.yaml"
    if path.exists():
        raise Refused("RECORD_EXISTS", f"{path} already exists")
    record = planned(main_checkout, backlog_tip(main_checkout), task_id)
    source, revision = bound_governance(name, root, worktree)
    rest = {k: v for k, v in record.items() if k not in ("task_id", "repository", "repository_identity_status",
                                                          "scope", "governance_source", "authority", "rank")}
    identity = {k: record[k] for k in ("repository", "repository_identity_status") if k in record}
    bound = {
        "task_id": task_id,
        **identity,
        "base_sha": git(worktree, "merge-base", "HEAD", f"refs/heads/{MAIN}").stdout.strip(),
        "scope": scope or record.get("scope"),
        "governance_source": source,
        "governance_revision": revision,
        "branch": branch,
        "authority": {**NO_AUTHORITY, "edit": True, "commit": True},
        **rest,
        **({"version": version} if version is not None else {}),
    }
    text = dump(bound, prefix)

    def change(tip: str | None) -> tuple[str, None, str]:
        planned(main_checkout, tip, task_id)
        return task_id, None, f"start {task_id} on {branch}"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError:
        raise Refused("RECORD_EXISTS", f"{path} already exists") from None
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise Refused("WRITE_FAILED", f"{path}: {exc}") from None
    try:
        backlog_write(main_checkout, change)
    except (Refused, OSError) as failure:
        refused = failure if isinstance(failure, Refused) else Refused("WRITE_FAILED", f"{BACKLOG} branch: {failure}")
        try:
            path.unlink()
        except OSError as exc:
            raise Refused("START_INCOMPLETE", f"{path} is written but {task_id} is still planned on the {BACKLOG} "
                                              f"branch ({refused.token} {refused.detail}) and the written copy "
                                              f"could not be deleted ({exc}); delete it and run atc task start "
                                              f"{task_id} again") from None
        raise refused from None
    return path


def plan(root: Path, environ: dict[str, str], cwd: str, scope: str, project: str | None, rank: int | None,
         after: list[str] | None, notes: str | None, version: str | None = None) -> str:
    [(name, repo)] = resolve(root, environ, cwd, project)
    prefix = main_prefix(repo)
    if prefix is None:
        raise Refused("NO_DECLARATION", f"AGENTS.md on {MAIN} of {repo} declares no task prefix")
    return reserve(root, name, repo, prefix, scope, rank=rank, after=after, notes=notes, version=version)


def start(root: Path, environ: dict[str, str], cwd: str, task_id: str, project: str | None, scope: str | None,
          version: str | None = None) -> Path:
    worktree, name, main_checkout, branch, prefix = topic_place(root, environ, cwd, project)
    return bind(root, worktree, name, main_checkout, branch, prefix, task_id, scope, version)


def amend(root: Path, environ: dict[str, str], cwd: str, task_id: str, project: str | None, verb: str, **fields) -> None:
    """Changes ``fields`` of the planned record ``task_id`` on its backlog (`rank`, `drop`)."""
    [(name, repo)] = resolve(root, environ, cwd, project, task=task_id)

    def change(tip: str | None) -> tuple[str, str, str]:
        record = {**planned(repo, tip, task_id), **fields}
        return task_id, dump(record, main_prefix(repo)), f"{verb} {task_id}"

    backlog_write(repo, change)


# list

def read_blobs(repo: Path, shas: list[str]) -> dict[str, str]:
    """Blob contents by SHA through one `git cat-file --batch`."""
    return {sha: data.decode("utf-8", "replace") for sha, data in read_blob_bytes(repo, shas).items()}


def read_blob_bytes(repo: Path, shas: list[str]) -> dict[str, bytes]:
    """Raw blob contents by SHA through one `git cat-file --batch`."""
    if not shas:
        return {}
    run = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"], input="".join(f"{s}\n" for s in shas).encode(),
                         capture_output=True, env=_env(), check=False)
    data, position, blobs = run.stdout, 0, {}
    for sha in shas:
        end = data.find(b"\n", position)
        if end < 0:
            break
        header = data[position:end].decode("ascii", "replace").split()
        if len(header) < 3:
            position = end + 1
            continue
        size = int(header[2])
        blobs[sha] = data[end + 1:end + 1 + size]
        position = end + 1 + size + 1
    return blobs


def ref_copies(repo: Path, branches: list[str], invalid: list[str] | None = None) -> dict[str, dict[str, dict]]:
    """``{task_id: {branch: record}}`` for every record file on ``branches``; unparsable files are skipped and
    reported: one warning each, or appended to ``invalid`` when the caller folds them."""
    entries = []
    for branch in branches:
        listing = git(repo, "ls-tree", f"refs/heads/{branch}", f"{TASKS_DIR}/", check=False).stdout
        for line in listing.splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob" and path.endswith(".yaml"):
                entries.append((branch, Path(path).stem, parts[2], path))
    blobs = read_blobs(repo, sorted({sha for _, _, sha, _ in entries}))
    copies: dict[str, dict[str, dict]] = {}
    for branch, stem, sha, path in entries:
        try:
            data = yaml.safe_load(blobs.get(sha, ""))
        except yaml.YAMLError:
            data = None
        if not isinstance(data, dict):
            if invalid is not None:
                invalid.append(f"{branch}:{path}")
            else:
                print(f"WARNING INVALID_RECORD {branch}:{path} is not a YAML mapping; atc task check reports it", file=sys.stderr)
            continue
        copies.setdefault(stem, {})[branch] = data
    return copies


def ahead(repo: Path, branch: str) -> bool:
    run = git(repo, "rev-list", "--count", f"refs/heads/{MAIN}..refs/heads/{branch}", check=False)
    return run.returncode == 0 and run.stdout.strip() not in ("", "0")


def on_main(repo: Path, sha: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", sha, f"refs/heads/{MAIN}", check=False).returncode == 0


def status(repo: Path, task_id: str, main_copy: dict | None, detail: dict, branch: str | None,
           branches: set[str], integrations: list[dict]) -> str:
    """`docs/TASKS.md` § Status, rows tried in order."""
    for copy in (detail, main_copy or {}):
        closure = copy.get("closure")
        if isinstance(closure, dict) and closure.get("state") in ("closed", "abandoned"):
            return closure["state"]
    if not isinstance(detail.get("branch"), str):
        return "planned"
    if branch in branches and ahead(repo, branch):
        return "open"
    own = [r for r in integrations if r.get("task") == task_id]
    if any(not r.get("records_only") and on_main(repo, r["candidate_sha"]) for r in own):
        return "integrated"
    if main_copy is not None and own and all(r.get("records_only") for r in own):
        return "bound"
    return "unknown"


def tasks(name: str, repo: Path, fold_warnings: bool = False) -> list[dict]:
    if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{MAIN}", check=False).returncode != 0:
        raise Refused("NO_MAIN", f"{repo} has no {MAIN} branch")
    branches = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split()
    import git_preflight
    integrations = git_preflight.read_integrations(repo)
    rows = []
    invalid: list[str] | None = [] if fold_warnings else None
    copies_by_id = ref_copies(repo, branches, invalid)
    if invalid:
        print(f"WARNING INVALID_RECORD {name}: {len(invalid)} record(s) are not a YAML mapping; atc task check {name}", file=sys.stderr)
    for task_id, copies in sorted(copies_by_id.items()):
        main_copy = copies.get(MAIN)
        bound = {c["branch"] for c in copies.values() if isinstance(c.get("branch"), str)}
        branch = (main_copy or {}).get("branch")
        if not isinstance(branch, str):
            branch = next((b for b, c in copies.items() if c.get("branch") == b), None) or next(iter(sorted(bound)), None)
        detail = copies.get(branch) or main_copy or next(iter(copies.values()))
        scope = detail.get("scope")
        rank, after = detail.get("rank"), detail.get("after")
        rows.append({
            "project": name,
            "task_id": task_id,
            "status": status(repo, task_id, main_copy, detail, branch, set(branches), integrations),
            "branch": branch,
            "scope": scope if isinstance(scope, str) else "",
            "conflict": len(bound) > 1,
            "authority": (main_copy or {}).get("authority"),
            "rank": rank if isinstance(rank, int) else None,
            "after": [a for a in after if isinstance(a, str)] if isinstance(after, list) else [],
            "version": detail.get("version") if isinstance(detail.get("version"), str) else None,
        })
    done = {row["task_id"] for row in rows if row["status"] in DONE}
    dropped = {row["task_id"] for row in rows if row["status"] == "abandoned"}
    for row in rows:
        waiting = row["after"] if row["status"] == "planned" else []
        row["after_dropped"] = [a for a in waiting if a in dropped]
        row["blocked_by"] = [a for a in waiting if a not in done and a not in dropped]
    return rows


def by_rank(rows: list[dict]) -> list[dict]:
    """Planned rows in queue order: ranked first, lowest rank first, then by ID."""
    return sorted(rows, key=lambda row: (row["rank"] is None, row["rank"] or 0, row["project"], row["task_id"]))


CLAIMING = ("open", "planned")
OVERLAP_SHOWN = 5


def changed_files(repo: Path, branch: str) -> set[str]:
    """The paths ``branch`` changes since its merge base with main, task records left out."""
    run = git(repo, "diff", "--name-only", f"refs/heads/{MAIN}...refs/heads/{branch}", check=False)
    return {path for path in run.stdout.splitlines() if path and not path.startswith(f"{TASKS_DIR}/")}


def overlaps(repos: dict[str, Path], rows: list[dict]) -> list[dict]:
    """The files each pair of open tasks of one repository both change."""
    found = []
    for name, repo in repos.items():
        open_rows = sorted((row for row in rows if row["project"] == name and row["status"] == "open" and row["branch"]),
                           key=lambda row: row["task_id"])
        files = {row["task_id"]: changed_files(repo, row["branch"]) for row in open_rows}
        for index, left in enumerate(open_rows):
            for right in open_rows[index + 1:]:
                shared = sorted(files[left["task_id"]] & files[right["task_id"]])
                if shared:
                    found.append({"project": name, "tasks": [left["task_id"], right["task_id"]], "files": shared})
    return found


def summary(rows: list[dict], found: list[dict]) -> list[str]:
    """The `OVERLAP` lines, then one `VERSIONS` line per repository whose open or planned tasks claim a version."""
    lines = []
    for entry in found:
        files = entry["files"]
        more = f" (+{len(files) - OVERLAP_SHOWN})" if len(files) > OVERLAP_SHOWN else ""
        lines.append(f"OVERLAP {' '.join(entry['tasks'])}: {len(files)} files: {', '.join(files[:OVERLAP_SHOWN])}{more}")
    claims: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        if row["version"] is not None and row["status"] in CLAIMING:
            claims.setdefault(row["project"], {}).setdefault(row["version"], []).append(row["task_id"])
    for project, versions in sorted(claims.items()):
        lines.append(f"VERSIONS {project}: " + "; ".join(f"{version} {', '.join(sorted(tasks))}"
                                                           for version, tasks in sorted(versions.items())))
    return lines


def render(rows: list[dict], with_project: bool) -> str:
    if not rows:
        return "no task records"
    lines = []
    for row in rows:
        scope = " ".join(row["scope"].split())
        cells = [row["project"]] if with_project else []
        cells += [row["task_id"], row["status"], row["branch"] or "-", scope[:SCOPE_WIDTH]]
        if row["rank"] is not None and row["status"] == "planned":
            cells.append(f"rank {row['rank']}")
        if row["version"] is not None and row["status"] in CLAIMING:
            cells.append(f"version {row['version']}")
        if row["blocked_by"]:
            cells.append(f"BLOCKED by {','.join(row['blocked_by'])}")
        cells += [f"after {a} (dropped)" for a in row["after_dropped"]]
        lines.append("  ".join(cells) + ("  CONFLICT" if row["conflict"] else ""))
    return "\n".join(lines)


# command line

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc task", allow_abbrev=False, **cli_conventions.form(
        "Write, list and check task records under .atc/tasks/.",
        exit="0 OK; 1 STOP or problems found; 2 ERROR", see="docs/TASKS.md § Commands"))
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    new_parser = sub.add_parser("new", allow_abbrev=False, help="write a record in the current topic worktree",
                                **cli_conventions.form(
                                    "Write a record for the topic worktree holding the working directory.",
                                    exit="0 created; 1 STOP (not a topic worktree, no declaration); 2 ERROR; "
                                           "3 PARTIAL (record written, still planned on the backlog)",
                                    see="docs/TASKS.md § Commands"))
    new_parser.add_argument("--scope", required=True, metavar="TEXT", help="what the task covers, one sentence")
    new_parser.add_argument("--version", metavar="TEXT", help="the version the task claims, for example 0.7.2")
    plan_parser = sub.add_parser("plan", allow_abbrev=False, help="queue a planned task on the backlog branch",
                                 **cli_conventions.form(
                                     "Commit a planned record, which grants nothing, to the backlog branch.",
                                     "The ID is issued here, one past every number in use; the working tree is "
                                     "not touched, so it runs from any checkout.",
                                     exit="0 planned; 1 STOP (no declaration, backlog busy); 2 ERROR",
                                     see="docs/TASKS.md § Backlog"))
    plan_parser.add_argument("--scope", required=True, metavar="TEXT", help="what the task covers, one sentence")
    plan_parser.add_argument("--rank", type=int, metavar="N", help="queue position; lower comes first")
    plan_parser.add_argument("--after", nargs="+", metavar="ID", help="tasks that must be integrated or closed first")
    plan_parser.add_argument("--notes", metavar="TEXT", help="background and conditions for whoever starts it")
    plan_parser.add_argument("--version", metavar="TEXT", help="the version the task claims, for example 0.7.2")
    start_parser = sub.add_parser("start", allow_abbrev=False, help="move a planned record into the current topic worktree",
                                  **cli_conventions.form(
                                      "Bind a planned task to the topic worktree holding the working directory.",
                                      "Writes its record there with the branch, base and governance revision and "
                                      "edit and commit authority, and removes it from the backlog branch.",
                                      exit="0 started; 1 STOP (not planned, not a topic worktree); 2 ERROR; "
                                           "3 PARTIAL (record written, still planned on the backlog)",
                                      see="docs/TASKS.md § Backlog"))
    start_parser.add_argument("task", metavar="ID", help="the planned task")
    start_parser.add_argument("--scope", metavar="TEXT", help="replace the planned scope")
    start_parser.add_argument("--version", metavar="TEXT", help="the version the task claims; replaces a planned one")
    rank_parser = sub.add_parser("rank", allow_abbrev=False, help="move a planned task in the queue",
                                 **cli_conventions.form("Set the rank of a planned task on the backlog branch.",
                                                        exit="0 ranked; 1 STOP (not planned, backlog busy); 2 ERROR",
                                                        see="docs/TASKS.md § Backlog"))
    rank_parser.add_argument("task", metavar="ID", help="the planned task")
    rank_parser.add_argument("rank", type=int, metavar="N", help="queue position; lower comes first")
    drop_parser = sub.add_parser("drop", allow_abbrev=False, help="abandon a planned task",
                                 **cli_conventions.form("Abandon a planned task on the backlog branch; its ID is "
                                                        "never issued again.",
                                                        exit="0 abandoned; 1 STOP (not planned, backlog busy); 2 ERROR",
                                                        see="docs/TASKS.md § Backlog"))
    drop_parser.add_argument("task", metavar="ID", help="the planned task")
    drop_parser.add_argument("--note", required=True, metavar="TEXT", help="why it was dropped")
    list_parser = sub.add_parser("list", allow_abbrev=False, help="list tasks with their derived status",
                                 **cli_conventions.form("List tasks from the records on main and every local branch, "
                                                        "with their derived status.",
                                                        exit="0 listed; 2 ERROR", see="docs/TASKS.md § Status"))
    for p in (new_parser, plan_parser, start_parser, rank_parser, drop_parser, list_parser):
        target = p.add_mutually_exclusive_group()
        cli_conventions.add_project(target, " (default: the working directory's project)")
        if p is list_parser:
            target.add_argument("--all", action="store_true", help="every registered project and the root")
    list_parser.add_argument("--status", choices=STATUSES, help="only tasks with this status")
    check_parser = sub.add_parser("check", allow_abbrev=False, help="check the records in a working tree",
                                  **cli_conventions.form("Check the records and the task declaration in a working "
                                                         "tree, one problem per line.",
                                                         exit="0 no problem; 1 problems found; 2 ERROR",
                                                         see="docs/TASKS.md § Record"))
    check_parser.add_argument("directory", nargs="?", type=Path, metavar="DIR",
                              help="the working tree (default: the one holding the working directory)")
    for p in (new_parser, plan_parser, start_parser, rank_parser, drop_parser, list_parser, check_parser):
        cli_conventions.add_json(p)
    return parser


def main(argv: list[str] | None = None, root: Path = ROOT, environ: dict[str, str] | None = None,
         cwd: str | None = None) -> int:
    args = build_parser().parse_args(argv)
    environ = dict(os.environ) if environ is None else environ
    cwd = cwd or os.getcwd()
    try:
        if args.command == "check":
            directory = args.directory or toplevel(Path(cwd)) or Path(cwd)
            found = problems(directory)
            if args.json:
                print(json.dumps({"verdict": "PROBLEMS" if found else "OK", "directory": str(directory),
                                  "problems": found}, indent=2))
            else:
                for problem in found:
                    print(problem)
                if not found:
                    print(f"ok: {directory}")
            return cli_conventions.STOP if found else cli_conventions.OK
        if args.command == "new":
            path = new(root, environ, cwd, args.scope, args.project, args.version)
            print(json.dumps({"verdict": "CREATED", "task_id": path.stem, "path": str(path)}, indent=2)
                  if args.json else f"CREATED {path.stem} {path}")
            return cli_conventions.OK
        if args.command == "start":
            path = start(root, environ, cwd, args.task, args.project, args.scope, args.version)
            print(json.dumps({"verdict": "STARTED", "task_id": path.stem, "path": str(path)}, indent=2)
                  if args.json else f"STARTED {path.stem} {path}")
            return cli_conventions.OK
        if args.command == "plan":
            task_id = plan(root, environ, cwd, args.scope, args.project, args.rank, args.after, args.notes,
                           args.version)
            print(json.dumps({"verdict": "PLANNED", "task_id": task_id}, indent=2) if args.json else f"PLANNED {task_id}")
            return cli_conventions.OK
        if args.command in ("rank", "drop"):
            fields = ({"rank": args.rank} if args.command == "rank"
                      else {"closure": {"state": "abandoned", "note": args.note}})
            amend(root, environ, cwd, args.task, args.project, args.command, **fields)
            verdict = "RANKED" if args.command == "rank" else "ABANDONED"
            print(json.dumps({"verdict": verdict, "task_id": args.task}, indent=2) if args.json else f"{verdict} {args.task}")
            return cli_conventions.OK
        targets = resolve(root, environ, cwd, args.project, every=args.all, root_default=True)
        rows = [row for name, repo in targets for row in tasks(name, repo, fold_warnings=not args.json)]
        if args.status:
            rows = [row for row in rows if row["status"] == args.status]
        if args.status == "planned":
            rows = by_rank(rows)
        found = overlaps(dict(targets), rows)
        if args.json:
            print(json.dumps({"tasks": rows, "overlaps": found}, indent=2))
        else:
            print("\n".join([render(rows, with_project=args.all), *summary(rows, found)]))
        return cli_conventions.OK
    except Refused as refused:
        if refused.token in ERROR_TOKENS:
            print(f"ERROR {refused.token} {refused.detail}", file=sys.stderr)
            code = cli_conventions.ERROR
        else:
            print(f"STOP {refused.token}: {refused.detail}", file=sys.stderr)
            code = cli_conventions.PARTIAL if refused.token in PARTIAL_TOKENS else cli_conventions.STOP
        if args.json:
            print(json.dumps({"refusal": {"verdict": cli_conventions.EXIT_NAMES[code],
                                          "reason": refused.token, "detail": refused.detail, "next": None}}, indent=2))
        return code


if __name__ == "__main__":
    sys.exit(main())

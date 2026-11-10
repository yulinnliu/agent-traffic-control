#!/usr/bin/env python3
"""Task records: write, list and check `.atc/tasks/<task_id>.yaml` (`docs/TASKS.md`).

    atc task new --scope TEXT [--project P | --repo ROOT]
    atc task list [--project P | --repo ROOT | --all] [--status S] [--json]
    atc task check [DIR]

`new` writes a record in the topic worktree holding the working directory.
`list` reads the records on `main` and every local branch without a checkout
and derives each task's status. `check` reports the records and declaration in
a working tree, one problem per line, and exits 1 when there is any. A refusal
prints `ERROR <TOKEN> <detail>` and exits 2.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

import task_section

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "shared" / "policies" / "schemas" / "task.schema.json"
TASKS_DIR = ".atc/tasks"
MAIN = "main"
STATUSES = ("closed", "abandoned", "open", "integrated", "bound", "unknown")
SCOPE_WIDTH = 60
REMOTE_URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*://[^/]+/|[^/@:]+@[^/:]+:)(?:.*/)?([^/]+/[^/]+?)(?:\.git)?/?$")
REPOSITORY_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


class Refused(Exception):
    def __init__(self, token: str, detail: str) -> None:
        super().__init__(f"{token} {detail}")
        self.token = token
        self.detail = detail


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in REPOSITORY_ENV}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    run = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=_env(), check=False)
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


def prefix_problem(declared: str | None) -> str | None:
    if declared == "":
        return f"no-prefix-line: AGENTS.md '{task_section.HEADING}' has no 'Task prefix: <PREFIX>' line"
    if declared is not None and not task_section.PREFIX_FORM.match(declared):
        return (f"invalid-prefix: AGENTS.md declares task prefix {declared!r}; a prefix is an uppercase letter "
                "followed by 1 to 7 uppercase letters or digits")
    return None


def problems(repo: Path) -> list[str]:
    """Problems of the declaration and the records in the working tree of ``repo``."""
    declared = declared_prefix(repo)
    found = [p for p in (prefix_problem(declared),) if p]
    directory = repo / TASKS_DIR
    files = sorted(directory.glob("*.yaml")) if directory.is_dir() else []
    if files and declared is None:
        found.append(f"no-declaration: {TASKS_DIR}/ holds {len(files)} record(s) but AGENTS.md has no "
                     f"'{task_section.HEADING}' section")
    valid_prefix = None if prefix_problem(declared) else declared
    schema = validator()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            found.append(f"invalid-yaml: {path.name}: not UTF-8 ({exc.reason})")
            continue
        found.extend(record_problems(path.name, text, valid_prefix, schema))
    return found


# targets

def toplevel(directory: Path) -> Path | None:
    run = git(directory, "rev-parse", "--show-toplevel", check=False)
    return Path(run.stdout.strip()) if run.returncode == 0 and run.stdout.strip() else None


def common_dir(repo: Path) -> str:
    out = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return os.path.normcase(os.path.realpath(out))


def resolve(root: Path, environ: dict[str, str], cwd: str, project: str | None, repo: str | None, every: bool = False):
    """The targets as (name, main checkout) pairs, named as `atc worktree` names them."""
    import hami_worktree as hw
    try:
        ws = hw.load_workspace(root, environ)
    except hw.Stop as stop:
        raise Refused(stop.result.code, stop.result.detail) from None
    registered = f"registered: {', '.join(sorted(ws.projects)) or 'none'}"
    root_target = hw.root_project(ws)
    if every:
        return [(root_target.name, Path(ws.root))] + [
            (p.name, Path(p.repo)) for p in sorted(ws.projects.values(), key=lambda p: p.name) if Path(p.repo).is_dir()]
    if repo is not None:
        if hw.key(os.path.realpath(repo)) != hw.key(os.path.realpath(ws.root)):
            raise Refused("UNKNOWN_PROJECT", f"--repo takes the workspace root {ws.root} only; a registered project "
                                             f"takes --project; {registered}")
        return [(root_target.name, Path(ws.root))]
    if project == root_target.name:
        return [(root_target.name, Path(ws.root))]
    if project is not None:
        found = ws.projects.get(project)
        if found is None:
            raise Refused("UNKNOWN_PROJECT", f"{project}; {registered}")
        return [(found.name, Path(found.repo))]
    found = hw.infer_project(ws, cwd)
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
    """The governance (source, revision) a new task of ``name`` binds: the root's `main`, or a project's lock.

    A project not yet migrated to `.atc/atc.lock` binds the `.atc/governance.yaml`
    carrying sync's marker; the final-sync task removes that fallback."""
    import validate_workspace as validator
    if name == "root":
        import onboard_project
        return onboard_project.governance_source(root), git(root, "rev-parse", f"refs/heads/{MAIN}").stdout.strip()
    lock_path = worktree / validator.ATC_LOCK
    try:
        lock = validator.read_lock(worktree)
    except (OSError, ValueError) as exc:
        raise Refused("NO_GOVERNANCE_BINDING", f"{lock_path}: {exc}; run atc onboard --sync") from None
    if lock is not None:
        return lock["source"], lock["revision"]
    manifest = worktree / validator.LEGACY_MANIFEST
    try:
        raw = manifest.read_text(encoding="utf-8")
        pin = yaml.safe_load(raw) or {} if validator.managed_marker_present(raw) else {}
    except (OSError, yaml.YAMLError):
        pin = {}
    source, revision = pin.get("source") if isinstance(pin, dict) else None, pin.get("revision") if isinstance(pin, dict) else None
    if not isinstance(source, str) or not isinstance(revision, str):
        raise Refused("NO_GOVERNANCE_BINDING", f"{lock_path} is missing and no {validator.LEGACY_MANIFEST.as_posix()} names a governance source and revision; run atc onboard --sync")
    return source, revision


def new(root: Path, environ: dict[str, str], cwd: str, scope: str, project: str | None, repo: str | None) -> Path:
    worktree = toplevel(Path(cwd))
    if worktree is None:
        raise Refused("NOT_IN_REPOSITORY", f"{cwd} is in no Git working tree")
    if os.path.normcase(os.path.realpath(worktree)) == os.path.normcase(os.path.realpath(root)):
        raise Refused("CHECKOUT_CANONICAL", f"{worktree} is the workspace root's canonical checkout; "
                                            "write the record in the topic's worktree under worktrees/root")
    [(name, main_checkout)] = resolve(root, environ, cwd, project, repo)
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
    number = max(taken_numbers(worktree, prefix), default=0) + 1
    if number > 9999:
        raise Refused("TASK_IDS_EXHAUSTED", f"{prefix} has used every four-digit number")
    task_id = f"{prefix}-{number:04d}"
    source, revision = bound_governance(name, root, worktree)
    record = {
        "task_id": task_id,
        **repository_identity(worktree, main_checkout),
        "base_sha": git(worktree, "merge-base", "HEAD", f"refs/heads/{MAIN}").stdout.strip(),
        "scope": scope,
        "governance_source": source,
        "governance_revision": revision,
        "branch": branch,
        "authority": {"edit": True, "commit": True, "push_topic": False, "integrate_main": False,
                      "push_main": False, "deploy": False},
    }
    text = yaml.safe_dump(record, sort_keys=False, allow_unicode=True)
    if found := record_problems(f"{task_id}.yaml", text, prefix, validator()):
        raise Refused("RECORD_INVALID", "; ".join(found))
    path = worktree / TASKS_DIR / f"{task_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


# list

def read_blobs(repo: Path, shas: list[str]) -> dict[str, str]:
    """Blob contents by SHA through one `git cat-file --batch`."""
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
        blobs[sha] = data[end + 1:end + 1 + size].decode("utf-8", "replace")
        position = end + 1 + size + 1
    return blobs


def ref_copies(repo: Path, branches: list[str]) -> dict[str, dict[str, dict]]:
    """``{task_id: {branch: record}}`` for every record file on ``branches``; unparsable files are reported and skipped."""
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
    if branch in branches and ahead(repo, branch):
        return "open"
    own = [r for r in integrations if r.get("task") == task_id]
    if any(not r.get("records_only") and on_main(repo, r["candidate_sha"]) for r in own):
        return "integrated"
    if main_copy is not None and own and all(r.get("records_only") for r in own):
        return "bound"
    return "unknown"


def tasks(name: str, repo: Path) -> list[dict]:
    if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{MAIN}", check=False).returncode != 0:
        raise Refused("NO_MAIN", f"{repo} has no {MAIN} branch")
    branches = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split()
    import git_preflight
    integrations = git_preflight.read_integrations(repo)
    rows = []
    for task_id, copies in sorted(ref_copies(repo, branches).items()):
        main_copy = copies.get(MAIN)
        bound = {c["branch"] for c in copies.values() if isinstance(c.get("branch"), str)}
        branch = (main_copy or {}).get("branch")
        if not isinstance(branch, str):
            branch = next((b for b, c in copies.items() if c.get("branch") == b), None) or next(iter(sorted(bound)), None)
        detail = copies.get(branch) or main_copy or next(iter(copies.values()))
        scope = detail.get("scope")
        rows.append({
            "project": name,
            "task_id": task_id,
            "status": status(repo, task_id, main_copy, detail, branch, set(branches), integrations),
            "branch": branch,
            "scope": scope if isinstance(scope, str) else "",
            "conflict": len(bound) > 1,
            "authority": (main_copy or {}).get("authority"),
        })
    return rows


def render(rows: list[dict], with_project: bool) -> str:
    if not rows:
        return "no task records"
    lines = []
    for row in rows:
        scope = " ".join(row["scope"].split())
        cells = [row["project"]] if with_project else []
        cells += [row["task_id"], row["status"], row["branch"] or "-", scope[:SCOPE_WIDTH]]
        lines.append("  ".join(cells) + ("  CONFLICT" if row["conflict"] else ""))
    return "\n".join(lines)


# command line

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc task", allow_abbrev=False, description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    new_parser = sub.add_parser("new", allow_abbrev=False, help="write a record in the current topic worktree")
    new_parser.add_argument("--scope", required=True)
    list_parser = sub.add_parser("list", allow_abbrev=False, help="list tasks with their derived status")
    for p in (new_parser, list_parser):
        target = p.add_mutually_exclusive_group()
        target.add_argument("--project")
        target.add_argument("--repo", metavar="ROOT")
        if p is list_parser:
            target.add_argument("--all", action="store_true")
    list_parser.add_argument("--status", choices=STATUSES)
    list_parser.add_argument("--json", action="store_true")
    check_parser = sub.add_parser("check", allow_abbrev=False, help="check the records in a working tree")
    check_parser.add_argument("directory", nargs="?", type=Path)
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
            for problem in found:
                print(problem)
            if not found:
                print(f"ok: {directory}")
            return 1 if found else 0
        if args.command == "new":
            path = new(root, environ, cwd, args.scope, args.project, args.repo)
            print(f"CREATED {path.stem} {path}")
            return 0
        targets = resolve(root, environ, cwd, args.project, args.repo, every=args.all)
        rows = [row for name, repo in targets for row in tasks(name, repo)]
        if args.status:
            rows = [row for row in rows if row["status"] == args.status]
        print(json.dumps({"tasks": rows}, indent=2) if args.json else render(rows, with_project=args.all))
        return 0
    except Refused as refused:
        print(f"ERROR {refused.token} {refused.detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

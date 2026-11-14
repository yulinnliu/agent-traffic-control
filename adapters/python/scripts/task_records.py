#!/usr/bin/env python3
"""Task records and the task lifecycle: `atc task create|update|list|resume|close` (docs/TASKS.md).

`create` makes the worktree, the branch, the record and its commit in one step; every later command takes the
task ID and finds the task's worktree from its record's `branch`. `check` is `atc-maintain task check`.
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

import cli_conventions
from cli_conventions import Refusal
import task_section

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "shared" / "policies" / "schemas" / "task.schema.json"
TASKS_DIR = ".atc/tasks"
MAIN = "main"
TESTS = ("none", "standard", "strict")
STATUSES = ("open", "integrated", "closed", "abandoned", "bound", "unknown")
TITLE_WIDTH = 60
REMOTE_URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*://[^/]+/|[^/@:]+@[^/:]+:)(?:.*/)?([^/]+/[^/]+?)(?:\.git)?/?$")
REPOSITORY_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")
RECORD_ID = re.compile(r"^(?P<prefix>[A-Z][A-Z0-9]{1,7})-[0-9]{4}$")
NO_TESTKIT_DECISION = "tests: none, the repository declares no [tool.atc-testkit]"
TESTKIT_TABLE = re.compile(r"^\s*\[tool\.atc-testkit[\].]", re.MULTILINE)
TEST_DIR_DEPTH = 2


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in REPOSITORY_ENV}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def git(repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None,
        input: str | None = None) -> subprocess.CompletedProcess:
    run = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, input=input,
                         encoding="utf-8", errors="replace", env={**_env(), **(env or {})}, check=False)
    if check and run.returncode != 0:
        raise Refusal("GIT_FAILED", f"git {' '.join(args)} in {repo}: {(run.stderr or '').strip()}",
                      code=cli_conventions.ERROR)
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
    if isinstance(data, dict) and data.get("tests") == "none" and not data.get("decisions"):
        problems.append(f"tests-none-undecided: {name}: tests is none but the record has no decision line; "
                        "tests none needs the Owner's decision")
    return problems


def declared_prefix(repo: Path) -> str | None:
    """The prefix `AGENTS.md` in ``repo`` declares; ``""`` for a section without a prefix line."""
    agents = repo / "AGENTS.md"
    return task_section.prefix(agents.read_text(encoding="utf-8")) if agents.is_file() else None


def main_prefix(repo: Path, main: str = MAIN) -> str | None:
    """The prefix `AGENTS.md` on ``main`` of ``repo`` declares, if any."""
    shown = git(repo, "show", f"refs/heads/{main}:AGENTS.md", check=False)
    return task_section.prefix(shown.stdout) or None if shown.returncode == 0 else None


def declaring(ws, task: str | None) -> list:
    """The repositories, the workspace root and the registered projects, whose `AGENTS.md` on ``main`` declares
    the prefix of the record ID ``task``."""
    import managed_worktree as hw
    record = RECORD_ID.fullmatch(task or "")
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


# places

def toplevel(directory: Path) -> Path | None:
    run = git(directory, "rev-parse", "--show-toplevel", check=False)
    return Path(run.stdout.strip()) if run.returncode == 0 and run.stdout.strip() else None


def workspace(root: Path, environ: dict[str, str]):
    import managed_worktree as hw
    try:
        return hw.load_workspace(root, environ)
    except hw.Stop as stop:
        raise Refusal(stop.result.code, stop.result.detail, hw.HINTS.get(stop.result.code),
                      cli_conventions.ERROR) from None


def project_of(ws, cwd: str, project: str | None):
    """The project ``project`` names, else the one holding ``cwd``; the root for the rest of the root checkout."""
    import managed_worktree as hw
    registered = f"registered: {', '.join(sorted(ws.projects)) or 'none'}"
    if project is not None:
        try:
            return hw.project_named(ws, project)
        except hw.Stop:
            raise Refusal("UNKNOWN_PROJECT", f"{project}; {registered}", "name a registered project with --project",
                          cli_conventions.ERROR) from None
    found = hw.infer_project(ws, cwd)
    if found is None and hw.within(cwd, ws.root):
        return hw.root_project(ws)
    if found is None:
        raise Refusal("UNKNOWN_PROJECT", f"{cwd} is in no registered project; {registered}",
                      "pass --project NAME (root for the workspace root)", cli_conventions.ERROR)
    return found


def project_of_task(ws, task_id: str):
    """The one repository whose `AGENTS.md` on main declares the prefix of ``task_id``."""
    if not RECORD_ID.fullmatch(task_id):
        raise Refusal("TASK_INVALID", f"{task_id!r} is not a task ID such as ATC-0008", None, cli_conventions.ERROR)
    found = declaring(ws, task_id)
    if len(found) != 1:
        which = "no repository" if not found else ", ".join(p.name for p in found)
        raise Refusal("UNKNOWN_TASK", f"{which} declares the prefix of {task_id} on main",
                      "check the ID; atc task list --all lists every task", cli_conventions.ERROR)
    return found[0]


def task_record(repo: Path, task_id: str) -> tuple[str | None, dict]:
    """The record of ``task_id`` as (branch, record): the copy on the branch it binds, else main's, else an
    archive tag's."""
    copies = ref_copies(repo, local_branches(repo), tags=True)
    found = copies.get(task_id)
    if not found:
        raise Refusal("UNKNOWN_TASK", f"no branch or tag of {repo} holds {TASKS_DIR}/{task_id}.yaml",
                      "check the ID; atc task list --all lists every task", cli_conventions.ERROR)
    own = next((record for ref, record in found.items() if record.get("branch") == ref), None)
    record = own or found.get(MAIN) or next(iter(found.values()))
    branch = record.get("branch")
    return (branch if isinstance(branch, str) else None), record


def test_directory(tree: Path) -> Path | None:
    """The shallowest directory of ``tree``, at most two levels down, whose `pyproject.toml` has a
    `[tool.atc-testkit]` table: where the task's tests run."""
    levels = [[tree]]
    for _ in range(TEST_DIR_DEPTH):
        levels.append([child for parent in levels[-1] if parent.is_dir() for child in sorted(parent.iterdir())
                       if child.is_dir() and not child.name.startswith((".", "_")) and child.name != "node_modules"])
    for level in levels:
        found = [d for d in level if _declares_testkit(d / "pyproject.toml")]
        if found:
            return found[0]
    return None


def _declares_testkit(path: Path) -> bool:
    try:
        return bool(TESTKIT_TABLE.search(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError):
        return False


# create

def taken_numbers(repo: Path, prefix: str) -> set[int]:
    """Numbers ``prefix`` already uses: records and names on every local and remote-tracking branch and tag,
    the task worktrees, and the integration records."""
    pattern = re.compile(rf"(?<![A-Z0-9]){re.escape(prefix)}-(\d{{4}})(?!\d)")
    stems: list[str] = []
    refs = git(repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes", "refs/tags").stdout.split()
    for ref in refs:
        stems.append(ref)
        if ref.startswith(("refs/heads/", "refs/remotes/", "refs/tags/archive/")):
            listing = git(repo, "ls-tree", "--name-only", ref, f"{TASKS_DIR}/", check=False).stdout
            stems += [Path(line).name.removesuffix(".yaml") for line in listing.splitlines() if line.endswith(".yaml")]
    stems += worktree_names(repo)
    import git_preflight
    stems += [r["task"] for r in git_preflight.read_integrations(repo) if isinstance(r.get("task"), str)]
    return {int(m.group(1)) for stem in stems for m in pattern.finditer(stem)}


def worktree_names(repo: Path) -> list[str]:
    """The branch of every worktree and the directory name of every linked one; a path's parent directories,
    such as a test's temporary root named after a task, are never read."""
    names: list[str] = []
    for entry in git(repo, "worktree", "list", "--porcelain").stdout.split("\n\n")[1:]:
        for line in entry.splitlines():
            if line.startswith("worktree "):
                names.append(Path(line.removeprefix("worktree ")).name)
            elif line.startswith("branch "):
                names.append(line.removeprefix("branch "))
    return names


def repository_identity(repo: Path) -> dict[str, str]:
    url = git(repo, "remote", "get-url", "origin", check=False).stdout.strip()
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
        raise Refusal("NO_GOVERNANCE_BINDING", f"{lock_path}: {exc}", "atc sync") from None
    if lock is None:
        raise Refusal("NO_GOVERNANCE_BINDING", f"{lock_path} is missing", "atc sync")
    return lock["source"], lock["revision"]


def dump(record: dict, prefix: str | None) -> str:
    """``record`` as YAML, refused unless it passes the record check."""
    text = yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=1000)
    if found := record_problems(f"{record['task_id']}.yaml", text, prefix, validator()):
        raise Refusal("RECORD_INVALID", "; ".join(found), None, cli_conventions.ERROR)
    return text


def read_task_file(path: Path) -> dict:
    """The fields a Markdown task file gives: `# title`, `## Scope` text, `## Decisions` list items and
    `## Tests` level."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise Refusal("FILE_UNREADABLE", f"{path}: {exc}", None, cli_conventions.ERROR) from None
    fields: dict = {}
    section, body = None, {"scope": [], "decisions": [], "tests": []}
    for line in text.splitlines():
        if line.startswith("# ") and "title" not in fields:
            fields["title"] = line[2:].strip()
            section = None
        elif line.startswith("## "):
            heading = line[3:].strip().lower()
            section = heading if heading in body else None
        elif section == "decisions":
            item = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.*)$", line)
            if item:
                body["decisions"].append(item.group(1).strip())
            elif line.strip() and body["decisions"]:
                body["decisions"][-1] += " " + line.strip()
        elif section is not None:
            body[section].append(line)
    scope = " ".join(" ".join(body["scope"]).split())
    tests = " ".join(body["tests"]).split()
    if scope:
        fields["scope"] = scope
    if body["decisions"]:
        fields["decisions"] = body["decisions"]
    if tests:
        if tests[0] not in TESTS:
            raise Refusal("FILE_INVALID", f"{path}: ## Tests is {tests[0]!r}, not one of {', '.join(TESTS)}",
                          None, cli_conventions.ERROR)
        fields["tests"] = tests[0]
    return fields


def check_tests_level(level: str, decisions: list, testkit: bool, task: str = "<ID>") -> None:
    """A project with the testkit takes every level, `none` only with a decision line; one without takes none."""
    if not testkit and level != "none":
        raise Refusal("TESTS_UNAVAILABLE", f"the project has no [tool.atc-testkit]; its tasks take tests: none",
                      "pass --tests none")
    if testkit and level == "none" and not decisions:
        raise Refusal("TESTS_NONE_UNDECIDED", "tests: none needs the Owner's decision line in the record",
                      f"add it: atc task update {task} --tests none --decision \"<the Owner's words>\"")


def create(root: Path, environ: dict[str, str], cwd: str, title: str | None, scope: str | None,
           tests: str | None, file: Path | None, project: str | None) -> tuple[str, Path]:
    import managed_worktree as hw
    fields = read_task_file(file) if file is not None else {}
    if title is not None and "title" in fields:
        raise Refusal("USAGE", "TITLE and the file's # title both name the title", "drop one of them",
                      cli_conventions.ERROR)
    title = title or fields.get("title")
    if not title:
        raise Refusal("USAGE", "a task needs a title", "pass TITLE, or # title in --file", cli_conventions.ERROR)
    scope = scope or fields.get("scope") or title
    decisions = fields.get("decisions", [])
    ws = workspace(root, environ)
    target = project_of(ws, cwd, project)
    repo = Path(target.repo)
    harness = hw.detect_harness(environ)
    if harness is None:
        raise Refusal("HARNESS_UNKNOWN", f"the environment names none or several of {', '.join(hw.HARNESSES)}",
                      "run it from the Claude Code, Codex or dsh session that works on the task")
    prefix = main_prefix(repo)
    if prefix is None:
        raise Refusal("NO_DECLARATION", f"AGENTS.md on {MAIN} of {target.name} declares no task prefix",
                      f"add a '{task_section.HEADING}' section with 'Task prefix: <PREFIX>'")
    testkit = test_directory(repo) is not None
    level = tests or fields.get("tests") or ("standard" if testkit else "none")
    check_tests_level(level, decisions, testkit)
    if not testkit and not decisions:
        decisions = [NO_TESTKIT_DECISION]
    numbers = taken_numbers(repo, prefix)
    number = max(numbers, default=0) + 1
    if number > 9999:
        raise Refusal("TASK_IDS_EXHAUSTED", f"{prefix} has used every four-digit number")
    task_id = f"{prefix}-{number:04d}"
    try:
        ctx = hw.Context(ws, target, hw.external_roots(ws, environ, os.path.expanduser("~")))
        made = hw.create(ctx, harness, task_id)
    except hw.Stop as stop:
        made = stop.result
    if made.code != "OK":
        raise Refusal(made.code, f"{made.target}: {made.detail}".rstrip(": "), made.hint(task_id),
                      cli_conventions.ERROR if made.code in hw.ENVIRONMENT_CODES else cli_conventions.STOP)
    worktree = Path(made.target)
    try:
        source, revision = bound_governance(target.name, root, worktree)
        record = {"task_id": task_id, "title": title, **repository_identity(repo), "base_sha": made.evidence["base"],
                  "scope": scope, "tests": level, "governance_source": source, "governance_revision": revision,
                  "branch": made.evidence["branch"],
                  "authority": {"edit": True, "commit": True, "push_topic": False, "integrate_main": False,
                                "push_main": False, "deploy": False}}
        if decisions:
            record["decisions"] = decisions
        commit_record(worktree, task_id, dump(record, prefix), f"chore(tasks): open {task_id}, {title}")
    except Refusal:
        discard(repo, worktree, made.evidence["branch"])
        raise
    return task_id, worktree


def discard(repo: Path, worktree: Path, branch: str) -> None:
    """Undo a worktree `create` made this run, when writing its record failed."""
    git(repo, "worktree", "unlock", str(worktree), check=False)
    git(repo, "worktree", "remove", "--force", str(worktree), check=False)
    git(repo, "branch", "-D", branch, check=False)


def commit_record(worktree: Path, task_id: str, text: str, message: str) -> None:
    path = worktree / TASKS_DIR / f"{task_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        raise Refusal("WRITE_FAILED", f"{path}: {exc}", None, cli_conventions.ERROR) from None
    relative = f"{TASKS_DIR}/{task_id}.yaml"
    git(worktree, "add", "--", relative)
    git(worktree, "commit", "--quiet", "-m", message, "--", relative)


# update

def update(root: Path, environ: dict[str, str], task_id: str, title: str | None, scope: str | None,
           tests: str | None, decisions: list[str], file: Path | None) -> list[str]:
    """Apply the changes to the record in the task's worktree in one commit; the changed field names."""
    import managed_worktree as hw
    fields = read_task_file(file) if file is not None else {}
    ws = workspace(root, environ)
    target = project_of_task(ws, task_id)
    repo = Path(target.repo)
    branch, _ = task_record(repo, task_id)
    if branch is None:
        raise Refusal("NO_WORKTREE", f"the record of {task_id} binds no branch", None)
    worktree = hw.find(hw.Context(ws, target, []), branch)
    if worktree is None:
        raise Refusal("NO_WORKTREE", f"no worktree has {branch} checked out", f"atc task list {task_id} -v")
    path = Path(worktree["worktree"]) / TASKS_DIR / f"{task_id}.yaml"
    relative = f"{TASKS_DIR}/{task_id}.yaml"
    if git(Path(worktree["worktree"]), "status", "--porcelain", "--", relative).stdout.strip():
        raise Refusal("RECORD_DIRTY", f"{relative} has uncommitted changes in the task's worktree",
                      "commit or discard them, then run it again")
    record = yaml.safe_load(path.read_text(encoding="utf-8"))
    changed: list[str] = []
    new_title = title or fields.get("title")
    if new_title and new_title != record.get("title"):
        record["title"], changed = new_title, [*changed, "title"]
    added = [*fields.get("decisions", []), *decisions]
    new_scope = scope or fields.get("scope")
    if new_scope and new_scope != record.get("scope"):
        added.insert(0, f"Scope before this update: {record.get('scope')}")
        record["scope"], changed = new_scope, [*changed, "scope"]
    if added:
        record["decisions"] = [*record.get("decisions", []), *added]
        changed.append(f"decisions +{len(added)}")
    new_tests = tests or fields.get("tests")
    if new_tests and new_tests != record.get("tests"):
        record["tests"], changed = new_tests, [*changed, "tests"]
    if new_tests:
        check_tests_level(new_tests, record.get("decisions", []), test_directory(Path(worktree["worktree"])) is not None,
                          task_id)
    if not changed:
        raise Refusal("NOTHING_TO_UPDATE", f"the options change nothing in the record of {task_id}",
                      "pass --title, --scope, --tests, --decision or --file")
    record = {**{k: record[k] for k in ("task_id", "title") if k in record}, **record}
    commit_record(Path(worktree["worktree"]), task_id, dump(record, main_prefix(repo)),
                  f"chore(tasks): update {task_id}, {', '.join(changed)}")
    return changed


# list

def local_branches(repo: Path) -> list[str]:
    return git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split()


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


def ref_copies(repo: Path, branches: list[str], invalid: list[str] | None = None,
               tags: bool = False) -> dict[str, dict[str, dict]]:
    """``{task_id: {ref: record}}`` for every record file on ``branches`` (and, with ``tags``, on the archive
    tags, keyed `archive/<ID>`); unparsable files are skipped and reported."""
    refs = [(branch, f"refs/heads/{branch}") for branch in branches]
    if tags:
        refs += [(name.removeprefix("refs/tags/"), name) for name in
                 git(repo, "for-each-ref", "--format=%(refname)", "refs/tags/archive").stdout.split()]
    entries = []
    for label, ref in refs:
        listing = git(repo, "ls-tree", ref, f"{TASKS_DIR}/", check=False).stdout
        for line in listing.splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob" and path.endswith(".yaml"):
                entries.append((label, Path(path).stem, parts[2], path))
    blobs = read_blobs(repo, sorted({sha for _, _, sha, _ in entries}))
    copies: dict[str, dict[str, dict]] = {}
    for label, stem, sha, path in entries:
        try:
            data = yaml.safe_load(blobs.get(sha, ""))
        except yaml.YAMLError:
            data = None
        if not isinstance(data, dict):
            if invalid is not None:
                invalid.append(f"{label}:{path}")
            continue
        copies.setdefault(stem, {})[label] = data
    return copies


def ahead(repo: Path, branch: str) -> bool:
    run = git(repo, "rev-list", "--count", f"refs/heads/{MAIN}..refs/heads/{branch}", check=False)
    return run.returncode == 0 and run.stdout.strip() not in ("", "0")


def on_main(repo: Path, sha: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", sha, f"refs/heads/{MAIN}", check=False).returncode == 0


def status(repo: Path, task_id: str, main_copy: dict | None, branch: str | None, branches: set[str],
           archived: bool, integrations: list[dict]) -> str:
    """`docs/TASKS.md` § Status, rows tried in order."""
    present = branch in branches
    if present and ahead(repo, branch):
        return "open"
    own = [r for r in integrations if r.get("task") == task_id]
    if any(not r.get("records_only") and on_main(repo, r["candidate_sha"]) for r in own):
        return "integrated" if present else "closed"
    if main_copy is not None and own and all(r.get("records_only") for r in own):
        return "bound"
    if archived and not present:
        return "abandoned"
    return "unknown"


def tasks(name: str, repo: Path) -> list[dict]:
    if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{MAIN}", check=False).returncode != 0:
        raise Refusal("NO_MAIN", f"{repo} has no {MAIN} branch", None, cli_conventions.ERROR)
    branches = local_branches(repo)
    import git_preflight
    integrations = git_preflight.read_integrations(repo)
    invalid: list[str] = []
    rows = []
    for task_id, copies in sorted(ref_copies(repo, branches, invalid, tags=True).items()):
        main_copy = copies.get(MAIN)
        own = next((record for ref, record in copies.items() if record.get("branch") == ref), None)
        detail = own or main_copy or next(iter(copies.values()))
        branch = detail.get("branch") if isinstance(detail.get("branch"), str) else None
        bound = {c["branch"] for c in copies.values() if isinstance(c.get("branch"), str)}
        title = detail.get("title") or detail.get("scope")
        rows.append({
            "project": name,
            "task_id": task_id,
            "status": status(repo, task_id, main_copy, branch, set(branches), f"archive/{task_id}" in copies,
                             integrations),
            "branch": branch,
            "title": title if isinstance(title, str) else "",
            "tests": detail.get("tests") if detail.get("tests") in TESTS else None,
            "conflict": len(bound) > 1,
        })
    if invalid:
        print(f"WARNING INVALID_RECORD {name}: {len(invalid)} record(s) are not a YAML mapping", file=sys.stderr)
    return rows


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


def render(rows: list[dict], with_project: bool, found: list[dict]) -> list[str]:
    lines = []
    for row in rows:
        title = " ".join(row["title"].split())
        cells = [row["project"]] if with_project else []
        cells += [row["task_id"], row["status"], row["branch"] or "-", title[:TITLE_WIDTH]]
        lines.append("  ".join(cells) + ("  CONFLICT" if row["conflict"] else ""))
        lines += [f"    {detail}" for detail in row.get("worktree", [])]
    for entry in found:
        files = entry["files"]
        more = f" (+{len(files) - OVERLAP_SHOWN})" if len(files) > OVERLAP_SHOWN else ""
        lines.append(f"OVERLAP {' '.join(entry['tasks'])}: {len(files)} files: {', '.join(files[:OVERLAP_SHOWN])}{more}")
    return lines or ["no tasks"]


# resume and close

def resume(root: Path, environ: dict[str, str], task_id: str):
    import managed_worktree as hw
    ws = workspace(root, environ)
    target = project_of_task(ws, task_id)
    branch, _ = task_record(Path(target.repo), task_id)
    try:
        result = hw.resume(hw.context(root, environ, target.name), branch or "")
    except hw.Stop as stop:
        result = stop.result
    if result.code != "OK":
        raise Refusal(result.code, f"{hw.display_target(result.target, ws)}: {result.detail}".rstrip(": "),
                      result.hint(task_id),
                      cli_conventions.ERROR if result.code in hw.ENVIRONMENT_CODES else cli_conventions.STOP)
    return hw.display_target(result.target, ws), result.evidence["behind_main"]


# check (atc-maintain task check)

def check_main(argv: list[str] | None = None, cwd: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atc-maintain task check", allow_abbrev=False, **cli_conventions.form(
        "Check the records and the task declaration in a working tree, one problem per line.",
        exit="0 no problem; 1 problems found; 2 ERROR", see="docs/TASKS.md § Record"))
    parser.add_argument("directory", nargs="?", type=Path, metavar="DIR",
                        help="the working tree (default: the one holding the working directory)")
    cli_conventions.add_json(parser)
    args = parser.parse_args(argv)
    cwd = cwd or os.getcwd()
    directory = args.directory or toplevel(Path(cwd)) or Path(cwd)
    found = problems(directory)
    if args.json:
        print(json.dumps({"verdict": "PROBLEMS" if found else "OK", "directory": str(directory), "problems": found},
                         indent=2))
    elif found:
        print(f"PROBLEMS {len(found)} in {directory}", *found, sep="\n")
    else:
        print(f"OK {directory}")
    return cli_conventions.STOP if found else cli_conventions.OK


# command line

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc task", allow_abbrev=False, **cli_conventions.form(
        "Create, update, list, resume and close tasks: a worktree, a branch and a record each.",
        exit="0 OK; 1 STOP; 2 ERROR; 3 PARTIAL (close: an item refused after another was deleted)",
        see="docs/TASKS.md § Commands; docs/WORKFLOW.md § The flow"))
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    create_parser = sub.add_parser("create", allow_abbrev=False, help="create the worktree, branch and record",
                                   **cli_conventions.form(
                                       "Create a task: its ID, worktree and branch <harness>+<ID> from main, and its "
                                       "record, committed on the branch.",
                                       "The harness is the session's own (Claude Code, Codex or dsh).",
                                       exit="0 created; 1 STOP; 2 ERROR", see="docs/TASKS.md § Commands"))
    create_parser.add_argument("title", nargs="?", metavar="TITLE", help="the task's title, a few words")
    update_parser = sub.add_parser("update", allow_abbrev=False, help="change a record in one commit",
                                   **cli_conventions.form(
                                       "Change the record in the task's worktree and commit it.",
                                       "A scope change appends the old scope as a decision; decisions only grow.",
                                       exit="0 updated; 1 STOP; 2 ERROR", see="docs/TASKS.md § Commands"))
    update_parser.add_argument("task", metavar="ID", help="the task")
    update_parser.add_argument("--title", metavar="TEXT", help="a new title")
    update_parser.add_argument("--decision", action="append", default=[], metavar="TEXT",
                               help="append one decision (repeatable)")
    for p in (create_parser, update_parser):
        p.add_argument("--scope", metavar="TEXT", help="what the task covers" +
                       (" (default: the title)" if p is create_parser else ""))
        p.add_argument("--tests", choices=TESTS, help="the test level (docs/TASKS.md § Test levels)")
        p.add_argument("--file", type=Path, metavar="FILE",
                       help="Markdown with # title, ## Scope, ## Decisions (a list) and ## Tests")
    cli_conventions.add_project(create_parser, " (default: the working directory's project)")
    list_parser = sub.add_parser("list", allow_abbrev=False, help="list tasks with their status",
                                 **cli_conventions.form("List tasks from the records on main, every local branch "
                                                        "and the archive tags, with their status.",
                                                        exit="0 listed; 2 ERROR", see="docs/TASKS.md § Status"))
    list_parser.add_argument("task", nargs="?", metavar="ID", help="only this task")
    target = list_parser.add_mutually_exclusive_group()
    cli_conventions.add_project(target, " (default: the working directory's project)")
    target.add_argument("--all", action="store_true", help="every registered project and the root")
    list_parser.add_argument("--status", choices=STATUSES, help="only tasks with this status")
    cli_conventions.add_verbose(list_parser)
    resume_parser = sub.add_parser("resume", allow_abbrev=False, help="find and check a task's worktree",
                                   **cli_conventions.form("Find the task's worktree from its record and check it.",
                                                          exit="0 found; 1 STOP; 2 ERROR",
                                                          see="docs/TASKS.md § Commands"))
    resume_parser.add_argument("task", metavar="ID", help="the task")
    close_parser = sub.add_parser("close", allow_abbrev=False, help="remove a task's worktree, scratches and reports",
                                  **cli_conventions.form(
                                      "Close an integrated task: remove its worktree and branch, scratches and "
                                      "report files.",
                                      "Directories named for the task but not registered to it are listed for the "
                                      "Owner's consent and never deleted. --abandon closes a task that was not "
                                      "integrated, keeping its tip under the local tag archive/<ID>.",
                                      exit="0 every item deleted (under --check: would be); 1 STOP, or an item "
                                           "refused with nothing deleted; 3 PARTIAL; 2 ERROR",
                                      see="docs/TASKS.md § Close; DELIVERY.md § Close-out"))
    close_parser.add_argument("task", metavar="ID", help="the task")
    close_parser.add_argument("--check", action="store_true",
                              help="list what the run would delete and refuse; delete nothing")
    close_parser.add_argument("--abandon", action="store_true",
                              help="close a task that was not integrated; its tip stays under archive/<ID>")
    cli_conventions.add_verbose(close_parser)
    for p in (create_parser, update_parser, list_parser, resume_parser, close_parser):
        cli_conventions.add_json(p)
    return parser


def main(argv: list[str] | None = None, root: Path = ROOT, environ: dict[str, str] | None = None,
         cwd: str | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    environ = dict(os.environ) if environ is None else environ
    cwd = cwd or os.getcwd()
    try:
        if args.command == "create":
            if args.title is None and args.file is None:
                parser.error("create needs TITLE or --file")
            task_id, worktree = create(root, environ, cwd, args.title, args.scope, args.tests, args.file, args.project)
            path = cli_conventions.relative(worktree, root)
            print(json.dumps({"verdict": "CREATED", "task_id": task_id, "worktree": str(worktree)}, indent=2)
                  if args.json else f"CREATED {task_id} {path}")
            return cli_conventions.OK
        if args.command == "update":
            changed = update(root, environ, args.task, args.title, args.scope, args.tests, args.decision, args.file)
            print(json.dumps({"verdict": "UPDATED", "task_id": args.task, "changed": changed}, indent=2)
                  if args.json else f"UPDATED {args.task} ({', '.join(changed)})")
            return cli_conventions.OK
        if args.command == "resume":
            path, behind = resume(root, environ, args.task)
            print(json.dumps({"verdict": "RESUMED", "task_id": args.task, "worktree": path, "behind_main": behind},
                             indent=2) if args.json else f"RESUMED {args.task} {path} (behind main {behind})")
            return cli_conventions.OK
        if args.command == "close":
            import close_out
            return close_out.close(root, environ, cwd, args.task, check=args.check, abandon=args.abandon,
                                   verbose=args.verbose, as_json=args.json)
        return list_main(args, root, environ, cwd)
    except Refusal as refusal:
        return cli_conventions.refuse(refusal, args.json)


def list_main(args: argparse.Namespace, root: Path, environ: dict[str, str], cwd: str) -> int:
    import managed_worktree as hw
    ws = workspace(root, environ)
    if args.task is not None:
        projects = [project_of_task(ws, args.task)]
    elif args.all:
        projects = [hw.root_project(ws), *sorted(ws.projects.values(), key=lambda p: p.name)]
        projects = [p for p in projects if Path(p.repo).is_dir()]
    else:
        projects = [project_of(ws, cwd, args.project)]
    rows = [row for p in projects for row in tasks(p.name, Path(p.repo))]
    if args.task is not None:
        rows = [row for row in rows if row["task_id"] == args.task]
    if args.status:
        rows = [row for row in rows if row["status"] == args.status]
    findings: list[str] = []
    if args.verbose:
        for p in projects:
            findings += worktree_findings(root, environ, ws, p.name, rows)
    found = overlaps({p.name: Path(p.repo) for p in projects}, rows)
    if args.json:
        for row in rows:
            row.pop("worktree", None)
        print(json.dumps({"tasks": rows, "overlaps": found, **({"findings": findings} if args.verbose else {})},
                         indent=2))
    else:
        print("\n".join([*render(rows, with_project=len(projects) > 1, found=found), *findings]))
    return cli_conventions.OK


def worktree_findings(root: Path, environ: dict[str, str], ws, name: str, rows: list[dict]) -> list[str]:
    """``-v``: attach each row's worktree and whether it is removable; return the findings no row explains."""
    import managed_worktree as hw
    try:
        results = hw.audit(hw.context(root, environ, name), environ)
    except hw.Stop as stop:
        return [f"{stop.result.code} {name}: {stop.result.detail}"]
    rows_by_branch = {row["branch"]: row for row in rows if row["project"] == name and row["branch"]}
    findings = []
    for result in results:
        row = rows_by_branch.get(result.evidence.get("branch"))
        where = hw.display_target(result.target, ws)
        if row is not None:
            row["worktree"] = [f"{where}: {result.detail or result.code}"]
            row["worktree_state"] = {"path": where, "code": result.code, "detail": result.detail,
                                     "removable": result.evidence.get("removable")}
        else:
            findings.append(f"{result.code} {where}" + (f" ({result.detail})" if result.detail else ""))
    return findings


if __name__ == "__main__":
    sys.exit(main())

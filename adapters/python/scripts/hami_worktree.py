#!/usr/bin/env python3
"""Create, resume, audit and clean up per-topic worktrees from the workspace map.

Contract 1. Every path derives from workspace.yaml and the durable key
`<owner>+<topic>`: directory `<project.worktrees>/<owner>+<topic>`, branch
`worktree-<owner>+<topic>`, lock reason `<owner>:<topic>:<session>`. The Git
worktree registration plus that lock reason is the whole durable record; the
canonical skill `shared/skills/hami-worktree/SKILL.md` documents each command,
code and next step.

`--project` may be omitted when the working directory lies under a registered
project's repository or worktrees root; an explicit value always wins.

Usage:
  hami_worktree.py create  --project P --owner O --topic T --contract 1 [--session S] [--json]
  hami_worktree.py resume  --project P --owner O --topic T --contract 1 [--session S] [--rebind-session] [--json]
  hami_worktree.py audit   --project P --contract 1 [--owner O] [--topic T] [--json]
  hami_worktree.py cleanup --project P --owner O --topic T --contract 1 [--json]
    [--abandon --archive-tag archive/NAME --expected-head FULL_SHA]
    [--squash-integrated --expected-head FULL_SHA --reviewed-sha FULL_SHA --expected-main FULL_SHA
     --integrated-as FULL_SHA --retained-ref refs/tags/NAME]

Exit status: 0 for OK; 1 for a refusal or an audit finding; 2 for a usage,
environment, permission or contract problem.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import git_preflight as gp
import validate_workspace as validator

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = 1
OWNERS = ("claude", "codex", "dsh")
TOPIC = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
V1_NAME = re.compile(rf"({'|'.join(OWNERS)})\+({TOPIC.pattern})")
BRANCH_PREFIX = "worktree-"
NO_SESSION = "-"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
# A probe covers only the deepest existing directory, so a refusal deeper in a
# missing worktrees root is recognised from Git's own message.
PERMISSION_TEXT = re.compile(r"permission denied|access is denied|operation not permitted", re.IGNORECASE)

NEXT = {
    "OK": "continue",
    "ALREADY_EXISTS": "use resume",
    "NOT_FOUND": "create it if this is a new task; otherwise confirm the topic",
    "IDENTITY_MISMATCH": "stop and hand to the user",
    "IDENTITY_AMBIGUOUS": "use --rebind-session only if the task authority asked to resume this topic; otherwise ask the user",
    "LEGACY_TARGET": "leave it to its original owner and the project's current procedure",
    "PATH_UNSAFE": "stop and hand to the user",
    "DIRTY": "commit or clean the worktree, then retry",
    "UNMERGED": "integrate it per the lifecycle first, otherwise keep the worktree",
    "ALREADY_INTEGRATED": "run cleanup without --squash-integrated",
    "ARCHIVE_MISMATCH": "keep the worktree; verify the local archive tag or retained ref preserves the expected HEAD",
    "HEAD_MISMATCH": "keep the worktree; confirm the exact HEAD authorized for abandonment or squash cleanup",
    "MAP_INVALID": "fix workspace.yaml (or the ATC_ROOT override)",
    "UNKNOWN_PROJECT": "fix --project",
    "CONTRACT_INCOMPATIBLE": "stop; report the contract the adapter expects and the one the workspace provides",
    "PERMISSION_DENIED": "sandbox, ACL or a busy directory; leave the directory or get approval, then retry once",
    "UNSUPPORTED_ENVIRONMENT": "a local capability is missing; stop retrying",
}
ENVIRONMENT_CODES = {"MAP_INVALID", "UNKNOWN_PROJECT", "CONTRACT_INCOMPATIBLE", "PERMISSION_DENIED", "UNSUPPORTED_ENVIRONMENT"}
AUDIT_NON_FINDINGS = {"OK", "LEGACY_TARGET"}


@dataclass
class Result:
    code: str
    target: str
    detail: str = ""
    evidence: dict = field(default_factory=dict)

    @property
    def next(self) -> str:
        return NEXT[self.code]


class Stop(Exception):
    def __init__(self, result: Result) -> None:
        super().__init__(result.code)
        self.result = result


class ProbeLeftBehind(Stop):
    """A writability probe was created but could not be removed.

    Distinct from every other ``Stop`` so failure evidence is never attributed to a
    leftover probe that does not exist.
    """


@dataclass(frozen=True)
class Project:
    name: str
    repo: str
    worktrees: str
    state: str


@dataclass(frozen=True)
class SquashRequest:
    """The recorded same-tree squash integration a cleanup is bound to."""

    reviewed_sha: str
    expected_main: str
    integrated_as: str
    retained_ref: str


@dataclass(frozen=True)
class Workspace:
    root: str
    projects: dict[str, Project]


@dataclass(frozen=True)
class ExternalRoot:
    tool: str
    path: str | None
    evidence: str


def key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def within(child: str, parent: str) -> bool:
    """True when ``child`` is ``parent`` or lies under it, compared lexically."""
    c, p = key(child), key(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def disjoint(a: str, b: str) -> bool:
    return not (within(a, b) or within(b, a))


def load_workspace(root: Path, environ: dict[str, str]) -> Workspace:
    root_str = os.path.normpath(str(root))
    problems = [f"{var}: {p}" for var, p in validator.workspace_env_problems(root, environ).items() if p]
    validator.FAILURES.clear()
    configured, entries = validator.load_and_check_workspace_yaml(root / "workspace.yaml")
    problems += validator.FAILURES
    validator.FAILURES.clear()
    if configured and key(os.path.expanduser(configured)) != key(root_str):
        problems.append(f"workspace_root {configured} does not match the checkout {root_str}")
    if problems:
        raise Stop(Result("MAP_INVALID", root_str, "; ".join(problems)))
    projects = {
        name: Project(name, *(os.path.normpath(os.path.join(root_str, entry[f])) for f in ("repo", "worktrees", "state")))
        for name, entry in entries.items()
    }
    return Workspace(root_str, projects)


def external_roots(ws: Workspace, environ: dict[str, str], home: str) -> list[ExternalRoot]:
    """Worktree roots other tools manage with a lifecycle of their own."""
    codex_home = environ.get("CODEX_HOME") or os.path.join(home, ".codex")
    try:
        with open(os.path.join(codex_home, "config.toml"), "rb") as fh:
            configured = tomllib.load(fh).get("desktop", {}).get("git-worktree-root")
    except FileNotFoundError:
        configured = None
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        configured = False
    if configured is False:
        roots = [ExternalRoot("codex-app", None, "UNVERIFIED")]
    elif isinstance(configured, str) and configured.strip():
        roots = [ExternalRoot("codex-app", os.path.normpath(os.path.expanduser(configured)), "CONFIG-ONLY")]
    else:
        roots = [ExternalRoot("codex-app", os.path.normpath(os.path.join(codex_home, "worktrees")), "DOCUMENTED")]
    for project in ws.projects.values():
        roots.append(ExternalRoot("claude-code", os.path.join(project.repo, ".claude", "worktrees"), "DOCUMENTED"))
    return roots


def overlapping_roots(ws: Workspace, project: Project, externals: list[ExternalRoot]) -> list[ExternalRoot]:
    return [r for r in externals if r.path is not None and not disjoint(r.path, project.worktrees)]


def is_reparse(path: str) -> bool:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(st.st_mode)


def first_reparse_in_tree(top: str) -> str | None:
    """Walk ``top`` without entering any reparse point; return the first one found."""
    pending = [top]
    while pending:
        current = pending.pop()
        with os.scandir(current) as it:
            for entry in it:
                st = entry.stat(follow_symlinks=False)
                if getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT or stat.S_ISLNK(st.st_mode):
                    return entry.path
                if stat.S_ISDIR(st.st_mode):
                    pending.append(entry.path)
    return None


def git(repo: str, *args: str, readonly: bool = False, input: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in gp.SCRUBBED_ENV}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    cmd = ["git", *(["--no-optional-locks"] if readonly else []), "-C", repo, *args]
    try:
        if input is not None:
            # Git's ref protocol requires LF bytes on Windows as well as POSIX.
            result = subprocess.run(cmd, input=input.encode("utf-8"), capture_output=True, env=env)
            return subprocess.CompletedProcess(result.args, result.returncode,
                                               result.stdout.decode("utf-8", errors="replace"),
                                               result.stderr.decode("utf-8", errors="replace"))
        # Closed stdin keeps filesystem mutations noninteractive.
        return subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env
        )
    except FileNotFoundError as exc:
        raise Stop(Result("UNSUPPORTED_ENVIRONMENT", repo, "git not found on PATH")) from exc


def git_ok(repo: str, *args: str, readonly: bool = True) -> str:
    result = git(repo, *args, readonly=readonly)
    if result.returncode != 0:
        raise Stop(Result("UNSUPPORTED_ENVIRONMENT", repo, f"git {' '.join(args)}: {first_line(result.stderr)}"))
    return result.stdout.strip()


def first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def git_available(repo: str) -> bool:
    """False when Git cannot be executed at all, as opposed to refusing an argument."""
    try:
        git(repo, "--version", readonly=True)
    except Stop:
        return False
    return True


def supports_lock_reason(repo: str) -> bool:
    help_text = git(repo, "worktree", "add", "-h", readonly=True)
    return "--reason" in help_text.stdout + help_text.stderr


def list_worktrees(repo: str) -> list[dict]:
    entries: list[dict] = []
    for block in git_ok(repo, "worktree", "list", "--porcelain").split("\n\n"):
        entry: dict = {}
        for line in block.splitlines():
            name, _, value = line.partition(" ")
            entry[name] = value
        if "worktree" in entry:
            entry["worktree"] = os.path.normpath(entry["worktree"])
            entry["branch"] = entry.get("branch", "").removeprefix("refs/heads/") or None
            entries.append(entry)
    return entries


def probe(directory: str, create, remove) -> bool:
    """Create and remove one probe entry; a probe that cannot be removed is reported, never left silently."""
    path = os.path.join(directory, f"hami-worktree-probe-{os.getpid()}")
    try:
        create(path)
    except OSError:
        return False
    try:
        remove(path)
    except OSError as exc:
        raise ProbeLeftBehind(Result("PERMISSION_DENIED", path, f"probe left behind; remove it by hand ({exc.strerror or exc})")) from exc
    return True


def dir_writable(directory: str) -> bool:
    return probe(directory, lambda p: os.close(os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)), os.remove)


def subdir_creatable(directory: str) -> bool:
    """The right Git needs at the destination: creating a subdirectory."""
    return probe(directory, os.mkdir, os.rmdir)


def nearest_existing(path: str) -> str:
    while not os.path.isdir(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return path


def metadata_writable(repo: str) -> bool:
    """Probe the common Git dir and its worktrees/ dir."""
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir", readonly=True).stdout.strip()
    if not common:
        return False
    return all(dir_writable(d) for d in (common, os.path.join(common, "worktrees")) if os.path.isdir(d))


def require_writable(repo: str, target: str) -> None:
    if not metadata_writable(repo):
        raise Stop(Result("PERMISSION_DENIED", target, "Git metadata is not writable from this session"))


def durable(owner: str, topic: str) -> tuple[str, str]:
    name = f"{owner}+{topic}"
    return name, BRANCH_PREFIX + name


def parse_lock(reason: str | None) -> tuple[str, str, str] | None:
    parts = (reason or "").split(":", 2)
    if len(parts) != 3 or not parts[2] or any(ord(ch) < 32 for ch in parts[2]):
        return None
    return parts[0], parts[1], parts[2]


def v1_identity(project: Project, entry: dict) -> tuple[str, str] | None:
    path = entry["worktree"]
    if key(os.path.dirname(path)) != key(project.worktrees):
        return None
    match = V1_NAME.fullmatch(os.path.basename(path))
    return (match.group(1), match.group(2)) if match else None


def identity_problem(entry: dict, owner: str, topic: str, session: str | None) -> Result | None:
    """Apply the identity evidence table to a v1 entry; None when consistent."""
    path = entry["worktree"]
    _, branch = durable(owner, topic)
    if entry.get("branch") != branch:
        return Result("IDENTITY_MISMATCH", path, f"branch is {entry.get('branch') or 'detached'}, expected {branch}")
    if "locked" not in entry:
        return Result("IDENTITY_AMBIGUOUS", path, "lock missing")
    lock = parse_lock(entry["locked"])
    if lock is None or lock[:2] != (owner, topic):
        return Result("IDENTITY_MISMATCH", path, f"lock reason {entry['locked']!r} does not name {owner}:{topic}")
    if session is not None and lock[2] != session:
        return Result("IDENTITY_AMBIGUOUS", path, "lock belongs to another session")
    return None


def check_target_path(ws: Workspace, project: Project, target: str, externals: list[ExternalRoot]) -> None:
    """Steps 1-4 of the path-safety order: lexical only, nothing resolved."""
    if not within(target, ws.root) or not within(project.worktrees, ws.root) or key(project.worktrees) == key(ws.root):
        raise Stop(Result("PATH_UNSAFE", target, "outside-root"))
    unverified = [r.tool for r in externals if r.path is None]
    if unverified:
        raise Stop(Result("UNSUPPORTED_ENVIRONMENT", target, f"external worktree root unverified: {', '.join(unverified)}"))
    overlap = overlapping_roots(ws, project, externals)
    if key(os.path.dirname(target)) != key(project.worktrees) or overlap:
        detail = "external-root-overlap" if overlap else "outside-root"
        raise Stop(Result("PATH_UNSAFE", target, detail, {"external_roots": [asdict(r) for r in overlap]}))
    protected = [ws.root, *(os.path.join(ws.root, d) for d in ("projects", "worktrees", "state"))]
    for other in ws.projects.values():
        protected += [other.repo, other.state]
    if any(within(p, target) for p in protected):
        raise Stop(Result("PATH_UNSAFE", target, "protected"))
    reparse = first_reparse_on_path(ws.root, target)
    if reparse is not None:
        raise Stop(Result("PATH_UNSAFE", target, "reparse", {"reparse_point": reparse}))


def first_reparse_on_path(root: str, path: str) -> str | None:
    """Step 4: lstat every existing component below ``root`` down to ``path``."""
    relative = os.path.relpath(path, root).split(os.sep)
    for depth in range(1, len(relative) + 1):
        component = os.path.join(root, *relative[:depth])
        if is_reparse(component):
            return component
    return None


def check_resolved(target: str) -> None:
    """Step 6: only now resolve, and require it to equal the lexical path."""
    resolved = os.path.realpath(target)
    if key(resolved) != key(target):
        raise Stop(Result("PATH_UNSAFE", target, "resolved-mismatch", {"resolved": resolved}))


def legacy_holder(entries: list[dict], branch: str, target: str) -> dict | None:
    return next((e for e in entries[1:] if e.get("branch") == branch and key(e["worktree"]) != key(target)), None)


def topic_holder(entries: list[dict], topic: str, branch: str) -> dict | None:
    """A registered worktree whose durable branch names this topic under another owner."""
    for entry in entries[1:]:
        other = entry.get("branch") or ""
        if other != branch and other.startswith(BRANCH_PREFIX) and other.endswith(f"+{topic}"):
            return entry
    return None


@dataclass
class Context:
    ws: Workspace
    project: Project
    externals: list[ExternalRoot]


def locate(ctx: Context, owner: str, topic: str) -> tuple[str, str, list[dict], dict | None]:
    name, branch = durable(owner, topic)
    target = os.path.join(ctx.project.worktrees, name)
    check_target_path(ctx.ws, ctx.project, target, ctx.externals)
    entries = list_worktrees(ctx.project.repo)
    exact = next((e for e in entries[1:] if key(e["worktree"]) == key(target)), None)
    return target, branch, entries, exact


def create_conflict(repo: str, owner: str, topic: str, target: str, branch: str) -> Result | None:
    """Every reason the durable key cannot be created now; None when it can."""
    entries = list_worktrees(repo)
    exact = next((e for e in entries[1:] if key(e["worktree"]) == key(target)), None)
    if exact is not None:
        problem = identity_problem(exact, owner, topic, None)
        return problem or Result("ALREADY_EXISTS", target, evidence={"branch": exact.get("branch"), "lock_reason": exact.get("locked")})
    if os.path.lexists(target):
        return Result("PATH_UNSAFE", target, "unregistered")
    holder = legacy_holder(entries, branch, target)
    if holder is not None:
        return Result("LEGACY_TARGET", holder["worktree"], f"{branch} is checked out outside the v1 root")
    holder = topic_holder(entries, topic, branch)
    if holder is not None:
        return Result("IDENTITY_MISMATCH", target, f"topic {topic} is held by {holder['branch']}")
    if git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", readonly=True).returncode == 0:
        return Result("IDENTITY_MISMATCH", target, f"branch {branch} exists without a worktree")
    return None


def cmd_create(ctx: Context, owner: str, topic: str, session: str) -> Result:
    name, branch = durable(owner, topic)
    target = os.path.join(ctx.project.worktrees, name)
    repo = ctx.project.repo
    check_target_path(ctx.ws, ctx.project, target, ctx.externals)
    conflict = create_conflict(repo, owner, topic, target, branch)
    if conflict is not None:
        return conflict
    check_resolved(target)
    base = git(repo, "rev-parse", "--verify", "--quiet", "main^{commit}", readonly=True).stdout.strip()
    if not base:
        return Result("UNSUPPORTED_ENVIRONMENT", repo, "cannot resolve main")
    if not supports_lock_reason(repo):
        return Result("UNSUPPORTED_ENVIRONMENT", repo, "git worktree add does not support --lock --reason")
    require_writable(repo, target)
    # Git creates the worktrees root itself; probe the deepest directory that exists.
    destination = nearest_existing(ctx.project.worktrees)
    if not subdir_creatable(destination):
        return Result("PERMISSION_DENIED", target, f"cannot create directories under {destination} from this session")
    # The SHA, not the name, so the reported base is exactly the commit the worktree starts from.
    added = git(repo, "worktree", "add", "--lock", "--reason", f"{owner}:{topic}:{session}", "-b", branch, target, base)
    if added.returncode == 0:
        return Result("OK", target, evidence={"branch": branch, "base": base})
    evidence = {"git": first_line(added.stderr)}
    conflict = create_conflict(repo, owner, topic, target, branch)
    if conflict is not None:
        conflict.evidence = {**evidence, **conflict.evidence}
        return conflict
    refused = PERMISSION_TEXT.search(added.stderr) is not None
    # Every probe runs and keeps its own attribution, so a failed add reports the
    # full writability evidence and two leftovers cannot overwrite each other.
    metadata = probe_result(lambda: metadata_writable(repo))
    root = probe_result(lambda: subdir_creatable(nearest_existing(ctx.project.worktrees)))
    evidence["metadata_probe"] = metadata
    evidence["worktrees_root_probe"] = root
    if refused or metadata["result"] != "ok" or root["result"] != "ok":
        return Result("PERMISSION_DENIED", target, "Git metadata or the worktrees root is not writable from this session", evidence)
    return Result("UNSUPPORTED_ENVIRONMENT", target, "git worktree add failed", evidence)


def resolve_existing(ctx: Context, owner: str, topic: str) -> tuple[str, dict]:
    """Shared front half of resume and cleanup: steps 1-6 plus legacy routing."""
    target, branch, entries, exact = locate(ctx, owner, topic)
    if exact is None:
        holder = legacy_holder(entries, branch, target)
        if holder is not None:
            raise Stop(Result("LEGACY_TARGET", holder["worktree"], f"{branch} is checked out outside the v1 root"))
        holder = topic_holder(entries, topic, branch)
        if holder is not None:
            raise Stop(Result("IDENTITY_MISMATCH", target, f"topic {topic} is held by {holder['branch']}"))
        if os.path.lexists(target):
            raise Stop(Result("PATH_UNSAFE", target, "unregistered"))
        raise Stop(Result("NOT_FOUND", target))
    if os.path.lexists(target):
        check_resolved(target)
    return target, exact


def cmd_resume(ctx: Context, owner: str, topic: str, session: str, rebind: bool) -> Result:
    target, entry = resolve_existing(ctx, owner, topic)
    repo = ctx.project.repo
    problem = identity_problem(entry, owner, topic, session)
    if problem is not None and not (rebind and problem.code == "IDENTITY_AMBIGUOUS"):
        return problem
    if not os.path.isdir(target):
        return Result("NOT_FOUND", target, "registered but the directory is missing")
    if problem is not None:
        require_writable(repo, target)
        old_reason = entry.get("locked")
        if old_reason is not None:
            unlocked = git(repo, "worktree", "unlock", target)
            if unlocked.returncode != 0:
                return mutation_failure(repo, target, "unlock failed", unlocked)
        relocked = git(repo, "worktree", "lock", "--reason", f"{owner}:{topic}:{session}", target)
        if relocked.returncode != 0:
            return mutation_failure(repo, target, "lock failed", relocked, restoration_evidence(repo, target, old_reason))
    head = entry.get("HEAD", "")
    behind = git_ok(repo, "rev-list", "--count", f"{head}..main")
    return Result("OK", target, "session rebound" if problem else "", {"branch": entry.get("branch"), "behind_main": int(behind)})


def restore_lock(repo: str, target: str, reason: str) -> bool:
    return git(repo, "worktree", "lock", "--reason", reason, target).returncode == 0


def current_lock(repo: str, target: str) -> str | None:
    entry = next((e for e in list_worktrees(repo)[1:] if key(e["worktree"]) == key(target)), None)
    return entry.get("locked") if entry is not None else None


def observed_lock(repo: str, target: str) -> str | None:
    """The current lock reason, or None when it cannot be observed."""
    try:
        return current_lock(repo, target)
    except Stop:
        return None


def restoration_evidence(repo: str, target: str, old_reason: str | None) -> dict:
    """The ``lock_restored`` evidence for a failed mutation.

    Nothing is attempted, and no field is reported, when the worktree held no lock
    to restore. Otherwise the lock is restored and the state read back: ``True``
    when the read-back matches the reason it was restored to, ``False`` when it
    reads back a different lock, and ``None`` when the state cannot be read
    reliably. The read-back alone decides this; the restore command's own result is
    not part of the claim, and a failure in either step is reported as unobservable
    rather than displacing the mutation failure this evidence belongs to.
    """
    if old_reason is None:
        return {}
    try:
        restore_lock(repo, target, old_reason)
        observed = current_lock(repo, target)
    except Stop:
        return {"restoration_observation": "UNOBSERVABLE", "lock_restored": None}
    return {"lock_restored": observed == old_reason}


def probe_result(check) -> dict:
    """Run a writability probe after a mutation already failed.

    ``ok`` when it completes, ``unwritable`` when it reports a refusal, and
    ``leftover`` with the path when the probe itself could not be removed. Any
    other refusal is not this probe's evidence and propagates unchanged.
    """
    try:
        writable = check()
    except ProbeLeftBehind as stop:
        return {"result": "leftover", "path": stop.result.target}
    return {"result": "ok" if writable else "unwritable"}


def mutation_failure(
    repo: str, target: str, what: str, result: subprocess.CompletedProcess, restoration: dict | None = None
) -> Result:
    # A failure to observe the lock here is reported on its own field only: it must
    # neither invent restoration evidence nor overwrite what was already observed.
    evidence: dict = {"git": first_line(result.stderr), "lock_reason": observed_lock(repo, target), **(restoration or {})}
    metadata = probe_result(lambda: metadata_writable(repo))
    evidence["metadata_probe"] = metadata
    if metadata["result"] != "ok":
        return Result("PERMISSION_DENIED", target, f"{what}: Git metadata is not writable from this session", evidence)
    return Result("PERMISSION_DENIED", target, what, evidence)


def archive_identity(repo: str, target: str, tag: str, expected_head: str, tip: str) -> tuple[str, str]:
    """Bind an existing local archive tag to the exact authorized topic tip."""
    if tip != expected_head:
        raise Stop(Result("HEAD_MISMATCH", target, "topic HEAD differs from --expected-head",
                          {"expected_head": expected_head, "head": tip}))
    ref = "refs/tags/" + tag
    if not tag.startswith("archive/") or git(repo, "check-ref-format", ref, readonly=True).returncode != 0:
        raise Stop(Result("ARCHIVE_MISMATCH", target, "use a valid local archive/ tag"))
    oid = git(repo, "rev-parse", "--verify", "--quiet", ref, readonly=True)
    commit = git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}", readonly=True)
    if oid.returncode != 0 or commit.returncode != 0 or commit.stdout.strip() != expected_head:
        raise Stop(Result("ARCHIVE_MISMATCH", target, "archive tag must resolve to --expected-head",
                          {"archive_ref": ref, "expected_head": expected_head}))
    return ref, oid.stdout.strip()


def commit_of(repo: str, rev: str) -> str | None:
    result = git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}", readonly=True)
    return result.stdout.strip() if result.returncode == 0 else None


def is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", ancestor, descendant, readonly=True).returncode == 0


def squash_identity(repo: str, target: str, branch: str, tip: str, expected_head: str, squash: SquashRequest) -> tuple[str, str]:
    """Bind a same-tree squash integration and an existing retained ref to the authorized topic tip.

    The current ``main`` is resolved on every call, so a later call sees a moved or rewound ``main``.
    """
    if tip != expected_head or squash.reviewed_sha != expected_head:
        raise Stop(Result("HEAD_MISMATCH", target, "topic HEAD, --expected-head and --reviewed-sha must be the same commit",
                          {"expected_head": expected_head, "reviewed_sha": squash.reviewed_sha, "head": tip}))
    integration = commit_of(repo, squash.integrated_as)
    parents = git(repo, "rev-list", "--parents", "-n", "1", squash.integrated_as, readonly=True).stdout.split()[1:]
    if integration != squash.integrated_as or parents != [squash.expected_main]:
        raise Stop(Result("UNMERGED", target, "--integrated-as must be a single-parent commit on --expected-main",
                          {"integrated_as": squash.integrated_as, "expected_main": squash.expected_main, "parents": parents}))
    source_tree = git(repo, "rev-parse", "--verify", "--quiet", tip + "^{tree}", readonly=True).stdout.strip()
    integration_tree = git(repo, "rev-parse", "--verify", "--quiet", integration + "^{tree}", readonly=True).stdout.strip()
    if not source_tree or source_tree != integration_tree:
        raise Stop(Result("UNMERGED", target, "the integration tree differs from the topic tip tree",
                          {"source_tree": source_tree, "integration_tree": integration_tree}))
    main_tip = commit_of(repo, "main")
    if main_tip is None:
        raise Stop(Result("UNSUPPORTED_ENVIRONMENT", target, "cannot resolve main"))
    if not is_ancestor(repo, integration, main_tip):
        raise Stop(Result("UNMERGED", target, "--integrated-as is not reachable from main",
                          {"integrated_as": integration, "main": main_tip}))
    ref = squash.retained_ref
    own = "refs/heads/" + branch
    if (not ref.startswith(("refs/tags/", "refs/heads/")) or ref == own or ref.startswith("refs/heads/" + BRANCH_PREFIX)
            or git(repo, "check-ref-format", ref, readonly=True).returncode != 0
            or git(repo, "show-ref", "--verify", "--quiet", ref, readonly=True).returncode != 0):
        raise Stop(Result("ARCHIVE_MISMATCH", target, "--retained-ref must be an existing local tag or non-worktree branch",
                          {"retained_ref": ref}))
    oid = git(repo, "rev-parse", "--verify", "--quiet", ref, readonly=True).stdout.strip()
    retained = commit_of(repo, ref)
    if not oid or retained is None or not is_ancestor(repo, expected_head, retained):
        raise Stop(Result("ARCHIVE_MISMATCH", target, "--retained-ref must reach --expected-head",
                          {"retained_ref": ref, "expected_head": expected_head}))
    return ref, oid


def branch_present(repo: str, branch: str) -> bool | None:
    """Whether the local branch exists; None when Git cannot say."""
    return {0: True, 1: False}.get(git(repo, "show-ref", "--verify", "--quiet", "refs/heads/" + branch, readonly=True).returncode)


def worktree_present(repo: str, target: str) -> bool | None:
    """Whether the worktree is registered or its directory remains; None when it cannot be observed."""
    try:
        registered = any(key(e["worktree"]) == key(target) for e in list_worktrees(repo)[1:])
    except Stop:
        return None
    return registered or os.path.lexists(target)


def completion_evidence(repo: str, target: str, branch: str, branch_kept: bool) -> dict:
    """``worktree_removed`` and ``cleanup_complete`` from the observed worktree and branch state.

    ``cleanup_complete`` is omitted whenever the observations do not settle it.
    """
    worktree, present = worktree_present(repo, target), branch_present(repo, branch)
    evidence: dict = {}
    if worktree is not None:
        evidence["worktree_removed"] = not worktree
    if worktree or present:
        evidence["cleanup_complete"] = False
    elif worktree is False and present is False and not branch_kept:
        evidence["cleanup_complete"] = True
    return evidence


def cleanup_branch_config(repo: str, branch: str) -> dict:
    """Remove local settings of a deleted branch; report conservative skips as warnings."""
    settings = git(repo, "config", "--local", "--get-regexp", rf"^branch\.{re.escape(branch)}\.", readonly=True)
    if settings.returncode == 1:
        return {"branch_config": "absent"}
    if settings.returncode != 0:
        return {"branch_config": "kept", "warning": "could not inspect local branch settings",
                "branch_config_git": first_line(settings.stderr)}
    present = git(repo, "show-ref", "--verify", "--quiet", "refs/heads/" + branch, readonly=True)
    if present.returncode != 1:
        return {"branch_config": "kept", "warning": "branch recreated or its absence is unconfirmed; settings retained"}
    removed = git(repo, "config", "--local", "--remove-section", "branch." + branch)
    if removed.returncode != 0:
        return {"branch_config": "kept", "warning": "worktree and branch removed; local branch settings cleanup failed",
                "branch_config_git": first_line(removed.stderr)}
    return {"branch_config": "removed"}


def cmd_cleanup(ctx: Context, owner: str, topic: str, *, abandon: bool = False, archive_tag: str | None = None,
                expected_head: str | None = None, squash: SquashRequest | None = None) -> Result:
    repo = ctx.project.repo
    try:
        target, entry = resolve_existing(ctx, owner, topic)
    except Stop as stop:
        if stop.result.code == "NOT_FOUND":
            present = branch_present(repo, durable(owner, topic)[1])
            if present is not None:
                stop.result.evidence["branch_present"] = present
        raise
    problem = identity_problem(entry, owner, topic, None)
    if problem is not None:
        return problem
    if not os.path.isdir(target):
        return Result("NOT_FOUND", target, "registered but the directory is missing")
    try:
        report = gp.cleanup_preflight(Path(repo), Path(target))
    except gp.GitError as exc:
        return Result("UNSUPPORTED_ENVIRONMENT", target, str(exc))

    def bind(bound_target: str, tip: str, bound_entry: dict) -> tuple[str, str]:
        if squash is not None:
            return squash_identity(repo, bound_target, bound_entry["branch"], tip, expected_head, squash)
        return archive_identity(repo, bound_target, archive_tag, expected_head, tip)

    retained = None
    if abandon:
        retained = bind(target, report.evidence.get("tip", ""), entry)
    unmerged_retained = (abandon or squash is not None) and report.reason == "NOT_MERGED"
    if unmerged_retained and report.evidence.get("dirty_paths"):
        return Result("DIRTY", target, "the worktree has uncommitted or untracked changes",
                      {"dirty_paths": report.evidence["dirty_paths"]})
    if squash is not None and report.reason == "SAFE_TO_REMOVE":
        return Result("ALREADY_INTEGRATED", target, "the topic tip is reachable from main", {"head": report.evidence.get("tip")})
    if squash is not None and unmerged_retained:
        retained = bind(target, report.evidence.get("tip", ""), entry)
    if report.verdict != "REMOVABLE" and not unmerged_retained:
        code = {"NOT_MERGED": "UNMERGED", "WORKTREE_DIRTY": "DIRTY"}.get(report.reason, "PATH_UNSAFE")
        detail = "unregistered" if code == "PATH_UNSAFE" else report.detail
        return Result(code, target, detail, {"dirty_paths": report.evidence.get("dirty_paths", [])})
    reparse = first_reparse_in_tree(target)
    if reparse is not None:
        return Result("PATH_UNSAFE", target, "reparse", {"reparse_point": reparse})
    require_writable(repo, target)
    if retained is not None:
        # Recheck after writability probes, before any unlock or removal.
        current_target, current_entry = resolve_existing(ctx, owner, topic)
        problem = identity_problem(current_entry, owner, topic, None)
        if problem is not None:
            return problem
        retained = bind(current_target, current_entry.get("HEAD", ""), current_entry)
        entry = current_entry
    reason = entry["locked"]
    unlocked = git(repo, "worktree", "unlock", target)
    if unlocked.returncode != 0:
        return mutation_failure(repo, target, "unlock failed", unlocked)
    removed = git(repo, "worktree", "remove", target)
    if removed.returncode != 0:
        restoration = restoration_evidence(repo, target, reason)
        if "modified or untracked" in removed.stderr.lower():
            evidence: dict = {"git": first_line(removed.stderr), "lock_reason": observed_lock(repo, target), **restoration}
            return Result("DIRTY", target, "git refused removal", evidence)
        return mutation_failure(repo, target, "remove failed", removed, restoration)
    if retained is None:
        deleted = git(repo, "branch", "-d", entry["branch"])
    else:
        ref, retained_oid = retained
        # Verify retained evidence and compare-and-delete the branch in one ref transaction.
        transaction = (f"start\nverify {ref} {retained_oid}\n"
                       f"delete refs/heads/{entry['branch']} {expected_head}\nprepare\ncommit\n")
        deleted = git(repo, "update-ref", "--stdin", input=transaction)
    kept = deleted.returncode != 0
    evidence = {"branch": entry["branch"], "branch_kept": kept}
    if abandon:
        evidence.update(abandoned=True, archive_ref=retained[0], archive_oid=retained[1], preserved_head=expected_head)
    elif squash is not None:
        evidence.update(squash_integrated=True, retained_ref=retained[0], retained_oid=retained[1],
                        preserved_head=expected_head, integrated_as=squash.integrated_as)
    if kept:
        evidence["git"] = first_line(deleted.stderr)
    elif retained is not None:
        evidence.update(cleanup_branch_config(repo, entry["branch"]))
    evidence.update(completion_evidence(repo, target, entry["branch"], kept))
    return Result("OK", target, "removed", evidence)


def cmd_audit(ctx: Context, owner: str | None, topic: str | None) -> list[Result]:
    ws, project = ctx.ws, ctx.project
    results: list[Result] = []
    unverified = [r.tool for r in ctx.externals if r.path is None]
    if unverified:
        results.append(Result("UNSUPPORTED_ENVIRONMENT", project.worktrees, f"external worktree root unverified: {', '.join(unverified)}"))
    reparse = first_reparse_on_path(ws.root, project.worktrees) if within(project.worktrees, ws.root) else None
    if reparse is not None:
        results.append(Result("PATH_UNSAFE", project.worktrees, "reparse", {"reparse_point": reparse}))
    overlap = overlapping_roots(ws, project, ctx.externals)
    if overlap:
        results.append(Result("PATH_UNSAFE", project.worktrees, "external-root-overlap", {"external_roots": [asdict(r) for r in overlap]}))
    entries = list_worktrees(project.repo)
    main_tip = git(project.repo, "rev-parse", "--verify", "--quiet", "main^{commit}", readonly=True).stdout.strip()
    registered = {key(e["worktree"]) for e in entries}
    filtered = owner is not None or topic is not None

    def wanted(identity: tuple[str, str] | None) -> bool:
        return identity is not None and owner in (None, identity[0]) and topic in (None, identity[1])

    for entry in entries[1:]:
        identity = v1_identity(project, entry)
        if identity is None and filtered or identity is not None and not wanted(identity):
            continue
        evidence = state_evidence(project.repo, entry, main_tip)
        if identity is None:
            results.append(Result("LEGACY_TARGET", entry["worktree"], evidence=evidence))
            continue
        try:
            check_target_path(ws, project, entry["worktree"], [])
            problem = identity_problem(entry, *identity, None)
        except Stop as stop:
            problem = stop.result
        result = problem or Result("OK", entry["worktree"])
        result.evidence = {**evidence, **result.evidence}
        results.append(result)
    if os.path.isdir(project.worktrees) and reparse is None:
        for child in sorted(os.scandir(project.worktrees), key=lambda e: e.name):
            match = V1_NAME.fullmatch(child.name)
            if key(child.path) in registered or filtered and not wanted(match and match.groups()):
                continue
            results.append(Result("PATH_UNSAFE", child.path, "unregistered"))
    return results or [Result("OK", project.worktrees, "no worktrees")]


def state_evidence(repo: str, entry: dict, main_tip: str) -> dict:
    path, head = entry["worktree"], entry.get("HEAD", "")
    evidence: dict = {"branch": entry.get("branch"), "head": head, "locked": "locked" in entry, "lock_reason": entry.get("locked") or None}
    if "prunable" in entry or not os.path.isdir(path):
        evidence["missing"] = True
        return evidence
    status = git(path, "status", "--porcelain", readonly=True)
    evidence["clean"] = status.returncode == 0 and not status.stdout.strip()
    if main_tip and head:
        evidence["merged"] = git(repo, "merge-base", "--is-ancestor", head, main_tip, readonly=True).returncode == 0
    return evidence


def readiness(root: Path, environ: dict[str, str], home: str) -> tuple[str, str]:
    """The doctor line: contract, Git support, the CLI, and disjoint external roots."""
    try:
        ws = load_workspace(root, environ)
        if not git_available(ws.root):
            return "ERROR", f"contract {CONTRACT}; git is not available"
        lock_reason = supports_lock_reason(ws.root)
    except Stop as stop:
        return "ERROR", f"contract {CONTRACT}; {stop.result.code}: {stop.result.detail}"
    cli = Path(ws.root) / "adapters" / "python" / "scripts" / "hami_worktree.py"
    if not cli.is_file():
        return "ERROR", f"contract {CONTRACT}; the CLI is missing at {cli}"
    try:
        # `--help` builds the parser, exits before any work, and prints nothing itself.
        ran = subprocess.run([sys.executable, str(cli), "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return "ERROR", f"contract {CONTRACT}; the CLI at {cli} could not be run"
    if ran.returncode != 0:
        return "ERROR", f"contract {CONTRACT}; the CLI at {cli} exited {ran.returncode} for --help"
    externals = external_roots(ws, environ, home)
    overlaps = [
        f"{r.tool} {r.path} ({r.evidence}) overlaps {p.name}"
        for p in ws.projects.values()
        for r in overlapping_roots(ws, p, externals)
    ]
    levels = sorted({f"{r.tool} {r.evidence}" for r in externals})
    detail = f"contract {CONTRACT}; git worktree add --lock --reason: {'yes' if lock_reason else 'no'}; "
    unverified = [f"{r.tool} root unverified (not presumed disjoint)" for r in externals if r.path is None]
    if overlaps or unverified:
        return "ERROR", detail + "; ".join(overlaps + unverified)
    detail += f"no overlap among checked external roots ({', '.join(levels)})"
    return ("READY" if lock_reason else "ERROR"), detail


def topic_arg(value: str) -> str:
    if not TOPIC.fullmatch(value):
        raise argparse.ArgumentTypeError(f"topic must match {TOPIC.pattern}")
    return value


def session_arg(value: str) -> str:
    if not value or any(ord(ch) < 32 for ch in value):
        raise argparse.ArgumentTypeError("session must be a non-empty single line")
    return value


def full_sha_arg(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise argparse.ArgumentTypeError("must be a full 40-character commit SHA")
    return value.lower()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {name: sub.add_parser(name) for name in ("create", "resume", "audit", "cleanup")}
    for name, p in commands.items():
        identity_required = name != "audit"
        p.add_argument("--project")
        p.add_argument("--owner", choices=OWNERS, required=identity_required)
        p.add_argument("--topic", type=topic_arg, required=identity_required)
        p.add_argument("--contract", type=int, required=True)
        p.add_argument("--json", action="store_true")
    for name in ("create", "resume"):
        commands[name].add_argument("--session", type=session_arg, default=NO_SESSION)
    commands["resume"].add_argument("--rebind-session", action="store_true")
    commands["cleanup"].add_argument("--abandon", action="store_true",
                                    help="explicitly abandon a clean topic preserved by a local archive tag")
    commands["cleanup"].add_argument("--archive-tag")
    commands["cleanup"].add_argument("--expected-head", type=full_sha_arg)
    commands["cleanup"].add_argument("--squash-integrated", action="store_true",
                                    help="clean up a topic integrated by an authorized same-tree squash")
    commands["cleanup"].add_argument("--reviewed-sha", type=full_sha_arg)
    commands["cleanup"].add_argument("--expected-main", type=full_sha_arg)
    commands["cleanup"].add_argument("--integrated-as", type=full_sha_arg)
    commands["cleanup"].add_argument("--retained-ref")
    return parser


def cleanup_mode(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Accept exactly one of: plain cleanup, abandonment, or verified squash cleanup."""
    squash_fields = {"--reviewed-sha": args.reviewed_sha, "--expected-main": args.expected_main,
                     "--integrated-as": args.integrated_as, "--retained-ref": args.retained_ref}
    if args.squash_integrated:
        if args.abandon or args.archive_tag:
            parser.error("--squash-integrated cannot be combined with --abandon or --archive-tag")
        missing = [name for name, value in {"--expected-head": args.expected_head, **squash_fields}.items() if not value]
        if missing:
            parser.error(f"squash cleanup requires {', '.join(missing)}")
    elif any(squash_fields.values()):
        parser.error("--reviewed-sha, --expected-main, --integrated-as and --retained-ref require --squash-integrated")
    elif any((args.abandon, args.archive_tag, args.expected_head)) and not all((args.abandon, args.archive_tag, args.expected_head)):
        parser.error("abandoned cleanup requires --abandon, --archive-tag and --expected-head together")


def infer_project(ws: Workspace, cwd: str) -> Project | None:
    """The project whose repository or worktrees root holds ``cwd``; the deepest root wins."""
    matches = [(len(key(root)), project) for project in ws.projects.values()
               for root in (project.repo, project.worktrees) if within(cwd, root)]
    return max(matches, key=lambda m: m[0])[1] if matches else None


def run(args: argparse.Namespace, root: Path, environ: dict[str, str], home: str, cwd: str) -> list[Result]:
    if args.contract != CONTRACT:
        return [Result("CONTRACT_INCOMPATIBLE", args.project or "-", f"adapter requires {args.contract}, workspace provides {CONTRACT}")]
    try:
        ws = load_workspace(root, environ)
        registered = f"registered: {', '.join(sorted(ws.projects)) or 'none'}"
        if args.project is None:
            project = infer_project(ws, cwd)
            if project is None:
                return [Result("UNKNOWN_PROJECT", cwd, f"no --project and the working directory is in no registered project; {registered}")]
        else:
            project = ws.projects.get(args.project)
            if project is None:
                return [Result("UNKNOWN_PROJECT", args.project, registered)]
        ctx = Context(ws, project, external_roots(ws, environ, home))
        if args.command == "audit":
            return cmd_audit(ctx, args.owner, args.topic)
        if args.command == "create":
            return [cmd_create(ctx, args.owner, args.topic, args.session)]
        if args.command == "resume":
            return [cmd_resume(ctx, args.owner, args.topic, args.session, args.rebind_session)]
        squash = SquashRequest(args.reviewed_sha, args.expected_main, args.integrated_as, args.retained_ref) if args.squash_integrated else None
        return [cmd_cleanup(ctx, args.owner, args.topic, abandon=args.abandon, archive_tag=args.archive_tag,
                            expected_head=args.expected_head, squash=squash)]
    except Stop as stop:
        return [stop.result]
    except OSError as exc:
        return [Result("PERMISSION_DENIED", exc.filename or args.project or cwd, f"{type(exc).__name__}: {exc.strerror or exc}")]


def verdict(command: str, result: Result) -> str:
    if result.code in ENVIRONMENT_CODES:
        return "ERROR"
    return "OK" if result.code in (AUDIT_NON_FINDINGS if command == "audit" else {"OK"}) else "STOP"


def exit_status(command: str, results: list[Result]) -> int:
    verdicts = {verdict(command, r) for r in results}
    return 2 if "ERROR" in verdicts else 1 if "STOP" in verdicts else 0


def render(results: list[Result]) -> str:
    return "\n".join(f"{r.code} {r.target}{f' ({r.detail})' if r.detail else ''} - {r.next}" for r in results)


def main(argv: list[str] | None = None, root: Path = ROOT, environ: dict[str, str] | None = None, home: str | None = None,
         cwd: str | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "cleanup":
        cleanup_mode(parser, args)
    environ = dict(os.environ) if environ is None else environ
    results = run(args, root, environ, home if home is not None else os.path.expanduser("~"), cwd or os.getcwd())
    if args.json:
        payload = {
            "contract": CONTRACT,
            "command": args.command,
            "results": [
                {"verdict": verdict(args.command, r), "reason": r.code, "target": r.target, "detail": r.detail, "next": r.next, "evidence": r.evidence}
                for r in results
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render(results))
    return exit_status(args.command, results)


if __name__ == "__main__":
    sys.exit(main())

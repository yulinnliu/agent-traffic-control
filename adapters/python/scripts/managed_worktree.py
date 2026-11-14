#!/usr/bin/env python3
"""Task worktrees from the workspace map: create, find, resume, audit and remove (contract 2).

Every path derives from workspace.yaml, the task ID and the harness that runs it: directory
`<project.worktrees>/<harness>+<ID>`, branch `<harness>+<ID>`, lock reason `<harness>:<ID>`. `atc task`
drives every operation; the canonical skill `shared/skills/atc-worktree/SKILL.md` documents the contract.
A task's worktree is found from its record's `branch`, so a worktree created under an earlier naming is
found and removed the same way.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import git_preflight as gp
from atc_testkit import locations, owner as candidate_owner
import validate_workspace as validator

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = 2
HARNESSES = ("claude", "codex", "dsh")
HARNESS_SIGNS = {"claude": ("CLAUDECODE",), "codex": ("CODEX_",), "dsh": ("DSH_",)}
REMOTE = "origin"
ARCHIVE_PREFIX = "archive/"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
# A probe covers only the deepest existing directory, so a refusal deeper in a
# missing worktrees root is recognised from Git's own message.
PERMISSION_TEXT = re.compile(r"permission denied|access is denied|operation not permitted", re.IGNORECASE)

HINTS = {
    "ALREADY_EXISTS": "atc task resume {task}",
    "NOT_FOUND": "atc task list -v shows the task's worktree, if any",
    "IDENTITY_MISMATCH": "the worktree or branch belongs to something else; ask the Owner",
    "PATH_UNSAFE": "stop and hand to the Owner",
    "DIRTY": "commit or discard the worktree's changes, then run it again",
    "UNMERGED": "integrate the task first, or close it with --abandon",
    "ARCHIVE_MISMATCH": "the tag archive/{task} exists at another commit; ask the Owner",
    "MAP_INVALID": "fix workspace.yaml (or the ATC_ROOT override)",
    "UNKNOWN_PROJECT": "name a registered project with --project",
    "PERMISSION_DENIED": "sandbox, ACL or a busy directory; leave the directory or get approval, then run it again",
    "UNSUPPORTED_ENVIRONMENT": "a local capability is missing; fix it before running it again",
    "CANDIDATE_LEFTOVER": "the next atc test prunes it",
}
ENVIRONMENT_CODES = {"MAP_INVALID", "UNKNOWN_PROJECT", "PERMISSION_DENIED", "UNSUPPORTED_ENVIRONMENT"}
AUDIT_NON_FINDINGS = {"OK", "CANDIDATE_IN_USE"}


@dataclass
class Result:
    code: str
    target: str
    detail: str = ""
    evidence: dict = field(default_factory=dict)

    def hint(self, task: str = "<ID>") -> str | None:
        hint = HINTS.get(self.code)
        return hint.format(task=task) if hint else None


class Stop(Exception):
    def __init__(self, result: Result) -> None:
        super().__init__(result.code)
        self.result = result


class ProbeLeftBehind(Stop):
    """A writability probe was created but could not be removed; never confused with any other refusal."""


@dataclass(frozen=True)
class Project:
    name: str
    repo: str
    worktrees: str
    state: str | None


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


def root_project(ws: Workspace) -> Project:
    """The workspace root as a worktree target: never an entry of ``ws.projects``, and without state."""
    return Project(validator.ROOT_TOPICS_NAME, ws.root, os.path.join(ws.root, *validator.ROOT_WORKTREES.split("/")), None)


def infer_project(ws: Workspace, cwd: str) -> Project | None:
    """The project whose repository or worktrees root holds ``cwd``; the deepest root wins.

    Under the root's worktrees root it is the root; elsewhere in the canonical checkout it is nothing.
    """
    root = root_project(ws)
    if within(cwd, root.worktrees):
        return root
    matches = [(len(key(path)), project) for project in ws.projects.values()
               for path in (project.repo, project.worktrees) if within(cwd, path)]
    return max(matches, key=lambda m: m[0])[1] if matches else None


def project_named(ws: Workspace, name: str) -> Project:
    found = root_project(ws) if name == validator.ROOT_TOPICS_NAME else ws.projects.get(name)
    if found is None:
        raise Stop(Result("UNKNOWN_PROJECT", name, f"registered: {', '.join(sorted(ws.projects)) or 'none'}"))
    return found


def detect_harness(environ: dict[str, str]) -> str | None:
    """The harness the environment names: one of HARNESSES, or None when none or several match."""
    found = [harness for harness, signs in HARNESS_SIGNS.items()
             if any(name == sign or (sign.endswith("_") and name.startswith(sign)) for name in environ for sign in signs)]
    return found[0] if len(found) == 1 else None


def identity(harness: str, task_id: str) -> str:
    """The directory and branch name of a task worktree."""
    return f"{harness}+{task_id}"


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
    for repo in (ws.root, *(project.repo for project in ws.projects.values())):
        roots.append(ExternalRoot("claude-code", os.path.join(repo, ".claude", "worktrees"), "DOCUMENTED"))
    return roots


def overlapping_roots(project: Project, externals: list[ExternalRoot]) -> list[ExternalRoot]:
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


def check_target_path(ws: Workspace, project: Project, target: str, externals: list[ExternalRoot]) -> None:
    """The path-safety order, lexical only, nothing resolved: inside the root, directly under the project's
    worktrees root, clear of external roots and protected directories, no reparse point on the way."""
    if not within(target, ws.root) or not within(project.worktrees, ws.root) or key(project.worktrees) == key(ws.root):
        raise Stop(Result("PATH_UNSAFE", target, "outside-root"))
    unverified = [r.tool for r in externals if r.path is None]
    if unverified:
        raise Stop(Result("UNSUPPORTED_ENVIRONMENT", target, f"external worktree root unverified: {', '.join(unverified)}"))
    overlap = overlapping_roots(project, externals)
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
    """lstat every existing component below ``root`` down to ``path``."""
    relative = os.path.relpath(path, root).split(os.sep)
    for depth in range(1, len(relative) + 1):
        component = os.path.join(root, *relative[:depth])
        if is_reparse(component):
            return component
    return None


def check_resolved(target: str) -> None:
    """Only now resolve, and require it to equal the lexical path."""
    resolved = os.path.realpath(target)
    if key(resolved) != key(target):
        raise Stop(Result("PATH_UNSAFE", target, "resolved-mismatch", {"resolved": resolved}))


@dataclass
class Context:
    ws: Workspace
    project: Project
    externals: list[ExternalRoot]


def context(root: Path, environ: dict[str, str], project: str, home: str | None = None) -> Context:
    ws = load_workspace(root, environ)
    found = project_named(ws, project)
    return Context(ws, found, external_roots(ws, environ, home if home is not None else os.path.expanduser("~")))


def commit_of(repo: str, rev: str) -> str | None:
    result = git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}", readonly=True)
    return result.stdout.strip() if result.returncode == 0 else None


def is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", ancestor, descendant, readonly=True).returncode == 0


def branch_present(repo: str, branch: str) -> bool | None:
    """Whether the local branch exists; None when Git cannot say."""
    return {0: True, 1: False}.get(git(repo, "show-ref", "--verify", "--quiet", "refs/heads/" + branch, readonly=True).returncode)


def find(ctx: Context, branch: str) -> dict | None:
    """The registered worktree that has ``branch`` checked out, other than the main checkout."""
    return next((e for e in list_worktrees(ctx.project.repo)[1:] if e.get("branch") == branch), None)


def create(ctx: Context, harness: str, task_id: str) -> Result:
    """The task's worktree and branch from main, locked with `<harness>:<ID>`."""
    name = identity(harness, task_id)
    target = os.path.join(ctx.project.worktrees, name)
    repo = ctx.project.repo
    check_target_path(ctx.ws, ctx.project, target, ctx.externals)
    if os.path.lexists(target):
        registered = any(key(e["worktree"]) == key(target) for e in list_worktrees(repo)[1:])
        return Result("ALREADY_EXISTS" if registered else "PATH_UNSAFE", target, "" if registered else "unregistered")
    if branch_present(repo, name):
        return Result("IDENTITY_MISMATCH", target, f"branch {name} exists without this worktree")
    check_resolved(target)
    base = commit_of(repo, "main")
    if base is None:
        return Result("UNSUPPORTED_ENVIRONMENT", repo, "cannot resolve main")
    if not supports_lock_reason(repo):
        return Result("UNSUPPORTED_ENVIRONMENT", repo, "git worktree add does not support --lock --reason")
    require_writable(repo, target)
    # Git creates the worktrees root itself; probe the deepest directory that exists.
    destination = nearest_existing(ctx.project.worktrees)
    if not subdir_creatable(destination):
        return Result("PERMISSION_DENIED", target, f"cannot create directories under {destination} from this session")
    # The SHA, not the name, so the reported base is exactly the commit the worktree starts from.
    added = git(repo, "worktree", "add", "--lock", "--reason", f"{harness}:{task_id}", "-b", name, target, base)
    if added.returncode == 0:
        return Result("OK", target, evidence={"branch": name, "base": base})
    evidence = {"git": first_line(added.stderr)}
    if PERMISSION_TEXT.search(added.stderr) or not metadata_writable(repo):
        return Result("PERMISSION_DENIED", target, "Git metadata or the worktrees root is not writable from this session",
                      evidence)
    return Result("UNSUPPORTED_ENVIRONMENT", target, "git worktree add failed", evidence)


def located(ctx: Context, branch: str) -> tuple[str, dict]:
    """The worktree of ``branch``, checked: registered, directly under the project's worktrees root, safe."""
    entry = find(ctx, branch)
    if entry is None:
        raise Stop(Result("NOT_FOUND", ctx.project.worktrees, f"no worktree has {branch} checked out",
                          {"branch_present": branch_present(ctx.project.repo, branch)}))
    target = entry["worktree"]
    check_target_path(ctx.ws, ctx.project, target, ctx.externals)
    if os.path.lexists(target):
        check_resolved(target)
    return target, entry


def resume(ctx: Context, branch: str) -> Result:
    target, entry = located(ctx, branch)
    if not os.path.isdir(target):
        return Result("NOT_FOUND", target, "registered but the directory is missing")
    behind = git_ok(ctx.project.repo, "rev-list", "--count", f"{entry.get('HEAD', '')}..main")
    return Result("OK", target, evidence={"branch": branch, "behind_main": int(behind)})


def restoration_evidence(repo: str, target: str, old_reason: str | None) -> dict:
    """Restore the lock ``old_reason`` after a failed removal and report what reads back; nothing without one."""
    if old_reason is None:
        return {}
    try:
        git(repo, "worktree", "lock", "--reason", old_reason, target)
        entry = next((e for e in list_worktrees(repo)[1:] if key(e["worktree"]) == key(target)), None)
    except Stop:
        return {"restoration_observation": "UNOBSERVABLE", "lock_restored": None}
    return {"lock_restored": (entry or {}).get("locked") == old_reason}


def mutation_failure(repo: str, target: str, what: str, result: subprocess.CompletedProcess,
                     restoration: dict | None = None) -> Result:
    evidence: dict = {"git": first_line(result.stderr), **(restoration or {})}
    try:
        writable = metadata_writable(repo)
    except ProbeLeftBehind as stop:
        evidence["metadata_probe"] = {"result": "leftover", "path": stop.result.target}
        writable = False
    if not writable:
        return Result("PERMISSION_DENIED", target, f"{what}: Git metadata is not writable from this session", evidence)
    return Result("PERMISSION_DENIED", target, what, evidence)


def cleanup_branch_config(repo: str, branch: str) -> dict:
    """Remove local settings of a deleted branch; report conservative skips as warnings."""
    settings = git(repo, "config", "--local", "--get-regexp", rf"^branch\.{re.escape(branch)}\.", readonly=True)
    if settings.returncode == 1:
        return {"branch_config": "absent"}
    if settings.returncode != 0:
        return {"branch_config": "kept", "warning": "could not inspect local branch settings"}
    if git(repo, "show-ref", "--verify", "--quiet", "refs/heads/" + branch, readonly=True).returncode != 1:
        return {"branch_config": "kept", "warning": "branch recreated or its absence is unconfirmed; settings retained"}
    removed = git(repo, "config", "--local", "--remove-section", "branch." + branch)
    if removed.returncode != 0:
        return {"branch_config": "kept", "warning": "local branch settings cleanup failed"}
    return {"branch_config": "removed"}


def assess(ctx: Context, branch: str, abandon: bool = False, task_id: str = "") -> tuple[Result | None, str, dict]:
    """Whether the worktree of ``branch`` may be removed: (refusal or None, target, entry). Changes nothing."""
    target, entry = located(ctx, branch)
    if not os.path.isdir(target):
        return Result("NOT_FOUND", target, "registered but the directory is missing"), target, entry
    try:
        report = gp.cleanup_preflight(Path(ctx.project.repo), Path(target))
    except gp.GitError as exc:
        return Result("UNSUPPORTED_ENVIRONMENT", target, str(exc)), target, entry
    dirty = report.evidence.get("dirty_paths", [])
    if report.reason == "WORKTREE_DIRTY" or (abandon and dirty):
        return Result("DIRTY", target, "uncommitted or untracked changes", {"dirty_paths": dirty}), target, entry
    if report.verdict != "REMOVABLE" and not (abandon and report.reason == "NOT_MERGED"):
        code = "UNMERGED" if report.reason == "NOT_MERGED" else "PATH_UNSAFE"
        return Result(code, target, report.detail), target, entry
    if abandon:
        tag = ARCHIVE_PREFIX + task_id
        held = commit_of(ctx.project.repo, f"refs/tags/{tag}")
        if held is not None and held != entry.get("HEAD"):
            return Result("ARCHIVE_MISMATCH", target, f"{tag} names {held}"), target, entry
    reparse = first_reparse_in_tree(target)
    if reparse is not None:
        return Result("PATH_UNSAFE", target, "reparse", {"reparse_point": reparse}), target, entry
    return None, target, entry


def remove(ctx: Context, branch: str, abandon: bool = False, task_id: str = "") -> Result:
    """Remove the worktree of ``branch`` and the branch: merged into main, or under ``abandon`` kept reachable
    by the local tag `archive/<ID>` at its tip."""
    repo = ctx.project.repo
    refusal, target, entry = assess(ctx, branch, abandon, task_id)
    if refusal is not None:
        return refusal
    require_writable(repo, target)
    tip = entry.get("HEAD", "")
    tag = ARCHIVE_PREFIX + task_id
    if abandon and commit_of(repo, f"refs/tags/{tag}") is None:
        tagged = git(repo, "tag", tag, tip)
        if tagged.returncode != 0:
            return Result("ARCHIVE_MISMATCH", target, f"cannot tag {tag}: {first_line(tagged.stderr)}")
    reason = entry.get("locked")
    if reason is not None:
        unlocked = git(repo, "worktree", "unlock", target)
        if unlocked.returncode != 0:
            return mutation_failure(repo, target, "unlock failed", unlocked)
    removed = git(repo, "worktree", "remove", target)
    if removed.returncode != 0:
        restoration = restoration_evidence(repo, target, reason)
        if "modified or untracked" in removed.stderr.lower():
            return Result("DIRTY", target, "git refused removal", {"git": first_line(removed.stderr), **restoration})
        return mutation_failure(repo, target, "remove failed", removed, restoration)
    if abandon:
        # Verify the archive tag and compare-and-delete the branch in one ref transaction.
        oid = git(repo, "rev-parse", f"refs/tags/{tag}", readonly=True).stdout.strip()
        deleted = git(repo, "update-ref", "--stdin",
                      input=f"start\nverify refs/tags/{tag} {oid}\ndelete refs/heads/{branch} {tip}\nprepare\ncommit\n")
    else:
        deleted = git(repo, "branch", "-d", branch)
    evidence = {"branch": branch, "branch_kept": deleted.returncode != 0, **({"archive": tag} if abandon else {})}
    if deleted.returncode != 0:
        evidence["git"] = first_line(deleted.stderr)
    else:
        evidence.update(cleanup_branch_config(repo, branch))
    return Result("OK", target, "removed", evidence)


def candidate_result(path: str, candidates: str) -> Result | None:
    """An `atc integrate` or `atc test --candidate` candidate under ``candidates``: in use while its owner runs,
    else left over."""
    if not within(os.path.realpath(path), os.path.realpath(candidates)):
        return None
    verdict, data = candidate_owner.state(Path(path))
    detail = candidate_owner.describe(data) + ("" if os.path.isdir(path) else "; record only")
    evidence = {"owner": data, "missing": not os.path.isdir(path)}
    if verdict == "leftover":
        return Result("CANDIDATE_LEFTOVER", path, detail, evidence)
    return Result("CANDIDATE_IN_USE", path, detail, evidence)


def audit(ctx: Context, environ: dict[str, str] | None = None) -> list[Result]:
    """Every worktree of the project with whether it is removable; unregistered directories are findings."""
    ws, project = ctx.ws, ctx.project
    results: list[Result] = []
    unverified = [r.tool for r in ctx.externals if r.path is None]
    if unverified:
        results.append(Result("UNSUPPORTED_ENVIRONMENT", project.worktrees, f"external worktree root unverified: {', '.join(unverified)}"))
    reparse = first_reparse_on_path(ws.root, project.worktrees) if within(project.worktrees, ws.root) else None
    if reparse is not None:
        results.append(Result("PATH_UNSAFE", project.worktrees, "reparse", {"reparse_point": reparse}))
    entries = list_worktrees(project.repo)
    main_tip = commit_of(project.repo, "main") or ""
    registered = {key(e["worktree"]) for e in entries}
    try:
        candidates = str(locations.locate(environ).tmp / locations.CANDIDATES)
    except locations.Refusal:
        candidates = None
    for entry in entries[1:]:
        found = candidate_result(entry["worktree"], candidates) if candidates else None
        if found is not None:
            results.append(found)
            continue
        if key(os.path.dirname(entry["worktree"])) != key(project.worktrees):
            continue
        evidence = state_evidence(project.repo, entry, main_tip)
        try:
            check_target_path(ws, project, entry["worktree"], [])
            results.append(Result("OK", entry["worktree"], removability(evidence), evidence))
        except Stop as stop:
            stop.result.evidence = {**evidence, **stop.result.evidence}
            results.append(stop.result)
    if os.path.isdir(project.worktrees) and reparse is None:
        for child in sorted(os.scandir(project.worktrees), key=lambda e: e.name):
            if key(child.path) not in registered:
                results.append(Result("PATH_UNSAFE", child.path, "unregistered"))
    return results


def state_evidence(repo: str, entry: dict, main_tip: str) -> dict:
    path, head = entry["worktree"], entry.get("HEAD", "")
    evidence: dict = {"branch": entry.get("branch"), "head": head, "locked": "locked" in entry,
                      "lock_reason": entry.get("locked") or None}
    if "prunable" in entry or not os.path.isdir(path):
        evidence.update(missing=True, removable=False)
        return evidence
    status = git(path, "status", "--porcelain", readonly=True)
    evidence["clean"] = status.returncode == 0 and not status.stdout.strip()
    if main_tip and head:
        evidence["merged"] = is_ancestor(repo, head, main_tip)
    evidence["removable"] = evidence["clean"] and evidence.get("merged") is True
    return evidence


def removability(evidence: dict) -> str:
    """The audit line's detail: `removable`, or what keeps the worktree."""
    if evidence.get("removable"):
        return "removable"
    if evidence.get("missing"):
        return "not removable: missing"
    reasons = [reason for reason, holds in (("unmerged", evidence.get("merged") is not True),
                                            ("uncommitted changes", not evidence.get("clean"))) if holds]
    return f"not removable: {', '.join(reasons)}"


def readiness(root: Path, environ: dict[str, str], home: str) -> tuple[str, str]:
    """The doctor line: contract, Git support and disjoint external roots."""
    try:
        ws = load_workspace(root, environ)
        if not git_available(ws.root):
            return "ERROR", f"contract {CONTRACT}; git is not available"
        lock_reason = supports_lock_reason(ws.root)
    except Stop as stop:
        return "ERROR", f"contract {CONTRACT}; {stop.result.code}: {stop.result.detail}"
    externals = external_roots(ws, environ, home)
    overlaps = [f"{r.tool} {r.path} ({r.evidence}) overlaps {p.name}"
                for p in ws.projects.values() for r in overlapping_roots(p, externals)]
    levels = sorted({f"{r.tool} {r.evidence}" for r in externals})
    detail = f"contract {CONTRACT}; git worktree add --lock --reason: {'yes' if lock_reason else 'no'}; "
    unverified = [f"{r.tool} root unverified (not presumed disjoint)" for r in externals if r.path is None]
    if overlaps or unverified:
        return "ERROR", detail + "; ".join(overlaps + unverified)
    detail += f"no overlap among checked external roots ({', '.join(levels)})"
    return ("READY" if lock_reason else "ERROR"), detail


def display_target(target: str, ws: Workspace | None) -> str:
    """A worktree path relative to the workspace root; anything else as given."""
    if ws is None or not within(target, ws.root):
        return target
    return os.path.relpath(target, ws.root).replace(os.sep, "/")

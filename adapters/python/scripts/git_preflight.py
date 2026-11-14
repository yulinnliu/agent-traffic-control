#!/usr/bin/env python3
"""Read-only Git preflight for the lifecycle in shared/governance/GIT_WORKFLOW.md.

`push_preflight()` runs `git fetch origin` and lists every commit a push of main would carry, grouped by
the integration that brought it in, for `atc push`. `merge_preflight()` evaluates that file's integration
table for `atc integrate`, and `cleanup_preflight()` judges a worktree's removal for `atc task close`. None
of them writes a branch, the index or the working tree: conflicts are detected with
`git merge-tree --write-tree`, which creates only unreferenced objects, and the fetch updates only
remote-tracking refs. A STOP carries the step that resolves it (governance `GIT_WORKFLOW.md` § Integrate).
"""

from __future__ import annotations

import json
import os
import re
import subprocess

from dataclasses import dataclass, field
from pathlib import Path

# Hook-exported variables would redirect every `git -C <repo>` call to the
# hook's own repository.
SCRUBBED_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")
REMOTE = "origin"
INTEGRATIONS = ("atc", "integrations.jsonl")

# A review binds to an object ID, never to a ref or revision expression that
# can move after the review.
REVIEWED_SHA = re.compile(r"[0-9a-fA-F]{12,40}")
SHORT_SHA = 12
AUTHORITY_ENDING = re.compile(r"\s@\s+\S+$")
FULL_SHA_WORD = re.compile(r"\b([0-9a-f]{12})[0-9a-f]{28}\b")

REASONS = {
    "NO_REVIEWED_SHA": "no reviewed SHA given; integration requires a formal review bound to a commit",
    "REVIEWED_SHA_INVALID": "the reviewed SHA is not a full SHA or a prefix of 12 or more hexadecimal characters",
    "REVIEWED_SHA_UNKNOWN": "the reviewed SHA does not resolve to a commit in this repository",
    "AMBIGUOUS_SHA": "the prefix matches several objects in this repository",
    "REVIEW_REWRITTEN": "the reviewed SHA is not an ancestor of the topic tip; reviewed history was rewritten",
    "UNREVIEWED_COMMITS": "the topic tip has commits after the reviewed SHA; review the delta first",
    "ALREADY_MERGED": "the topic tip is already reachable from main; nothing to integrate",
    "CHECKOUT_NOT_ON_MAIN": "the integration checkout is not on main",
    "CHECKOUT_DIRTY": "the integration checkout has uncommitted changes to tracked files",
    "FAST_FORWARD": "main is the merge-base; fast-forward to the reviewed tip",
    "CONFLICT": "main advanced and the merge conflicts; reconcile on the topic branch and re-review",
    "OVERLAP": "main advanced and some files changed on both sides; a reviewer judges semantic overlap",
    "COMPATIBLE": "main advanced with no conflict and no shared file; merge preserving the reviewed commits",
    "NOT_A_WORKTREE": "the path is not a linked worktree of this repository",
    "MAIN_CHECKOUT": "the path is the integration checkout itself, never removable",
    "NOT_MERGED": "the worktree's tip is not reachable from main",
    "WORKTREE_DIRTY": "the worktree has uncommitted or untracked changes",
    "SAFE_TO_REMOVE": "tip reachable from main and tree clean; remove through the repo's own tooling",
    "PUSH_LISTED": "every commit a push of main would carry is listed",
    "BRANCH_AMBIGUOUS": "several local branches point at the reviewed SHA",
    "NO_BRANCH_AT_REVIEWED": "no local branch points at or contains the reviewed SHA",
    "FETCH_FAILED": "git fetch origin failed; main cannot be compared with origin",
    "MAIN_BEHIND_ORIGIN": "local main is behind origin/main",
    "MAIN_DIVERGED_FROM_ORIGIN": "local main and origin/main have diverged",
    "REBASE_REQUIRED": (
        "main advanced and the topic is unpublished (no origin branch is at the reviewed SHA); "
        "rebase it onto main"
    ),
}

_RUN_WITH_REVIEWED = "run again with --sha and the full SHA the formal review named"
_REVIEW_DELTA = "review the delta, or re-review"
_REBASE = (
    "rebase the unpublished topic onto main, then run again; "
    "the approval carries over only when the rebase is clean and every patch-id matches, "
    "otherwise the rebased tip needs a fresh review"
)
_RECONCILE = (
    "reconcile on the topic branch with a new commit (merge main in, or fix forward), "
    "never by rewriting; re-review that commit"
)

# The step that resolves each stop: the `Integrate` table's step for a stop
# that is a row of it, otherwise the `Command results` table's step.
NEXT = {
    "NO_REVIEWED_SHA": _RUN_WITH_REVIEWED,
    "REVIEWED_SHA_INVALID": _RUN_WITH_REVIEWED,
    "REVIEWED_SHA_UNKNOWN": _RUN_WITH_REVIEWED,
    "AMBIGUOUS_SHA": "run again with a longer prefix or the full SHA the formal review named",
    "REVIEW_REWRITTEN": _REVIEW_DELTA,
    "UNREVIEWED_COMMITS": _REVIEW_DELTA,
    "ALREADY_MERGED": "nothing to integrate; continue with GIT_WORKFLOW.md § Cleanup trigger",
    "CHECKOUT_NOT_ON_MAIN": "check out main in the integration checkout, then run again",
    "CHECKOUT_DIRTY": "have the owner of the listed paths commit or remove them, then run again",
    "CONFLICT": _RECONCILE,
    "OVERLAP": "a reviewer judges semantic overlap, then prepare a merge candidate or " + _RECONCILE,
    "BRANCH_AMBIGUOUS": "delete or move the other local branches at the reviewed SHA, or integrate with the task's record ID",
    "NO_BRANCH_AT_REVIEWED": "restore the reviewed topic branch at the reviewed SHA, or " + _RUN_WITH_REVIEWED,
    "FETCH_FAILED": "restore access to origin (network, credentials), then run again",
    "MAIN_BEHIND_ORIGIN": (
        "fast-forward local main to origin/main in the integration checkout "
        "(git merge --ff-only origin/main), then run again; the topic is reassessed against the new main"
    ),
    "MAIN_DIVERGED_FROM_ORIGIN": (
        "the owner decides how the local-only main commits reach origin "
        "(each is integrated again onto origin/main); run again once main contains origin/main"
    ),
    "REBASE_REQUIRED": _REBASE,
}

# An unpublished topic reaches the current main by a rebase, so these stops
# name that step instead of a reconciling commit.
NEXT_UNPUBLISHED = {
    "CONFLICT": (
        "rebase the unpublished topic onto main and resolve the conflicts; "
        "the rebased tip needs a fresh review"
    ),
    "OVERLAP": _REBASE,
}


class GitError(Exception):
    pass


@dataclass
class Report:
    command: str
    verdict: str
    reason: str
    detail: str = ""
    evidence: dict = field(default_factory=dict)
    next: str | None = None

    def __post_init__(self) -> None:
        self.detail = self.detail or REASONS[self.reason]
        if self.verdict == "STOP" and self.next is None:
            self.next = NEXT[self.reason]


def git(
    repo: Path, *args: str, check: bool = True, extra_env: dict[str, str] | None = None, input: str | None = None,
) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if key not in SCRUBBED_ENV}
    # Stops `git status` from refreshing and rewriting the index of a checkout
    # another agent may be using.
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env.update(extra_env or {})
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        input=input,
    )
    if check and result.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {result.stderr.strip() or result.returncode}")
    return result


def rev(repo: Path, ref: str) -> str | None:
    result = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def count(repo: Path, range_spec: str) -> int:
    return int(git(repo, "rev-list", "--count", range_spec).stdout.strip())


def has_remote(repo: Path) -> bool:
    return REMOTE in git(repo, "remote").stdout.split()


def fetch_origin(repo: Path) -> str | None:
    """`git fetch origin`; the error text when it fails. A repository without `origin` fetches nothing."""
    if not has_remote(repo):
        return None
    fetched = git(repo, "fetch", "--quiet", REMOTE, check=False, extra_env={"GIT_TERMINAL_PROMPT": "0"})
    if fetched.returncode:
        text = (fetched.stderr or fetched.stdout).splitlines()
        return "; ".join(line.strip() for line in text if line.strip()) or f"exit {fetched.returncode}"
    return None


def origin_tip(repo: Path, main: str) -> str | None:
    return rev(repo, f"refs/remotes/{REMOTE}/{main}")


def published_refs(repo: Path, sha: str) -> list[str]:
    """The origin branches whose tip is ``sha``, as last fetched."""
    names = git(repo, "for-each-ref", "--format=%(refname)", "--points-at", sha, f"refs/remotes/{REMOTE}/").stdout.split()
    return sorted(name.removeprefix("refs/remotes/") for name in names if name != f"refs/remotes/{REMOTE}/HEAD")


def origin_state(repo: Path, main: str, main_tip: str) -> dict:
    """Local main against origin/main as last fetched: both tips and the commits each side lacks."""
    remote = origin_tip(repo, main)
    ahead = count(repo, f"{remote}..{main_tip}") if remote else None
    behind = count(repo, f"{main_tip}..{remote}") if remote else None
    return {"origin_main_tip": remote, "main_ahead_of_origin": ahead, "main_behind_origin": behind}


def origin_stop(state: dict, main: str, main_tip: str) -> tuple[str, str] | None:
    """The stop for a local main that lacks origin/main commits: its reason and detail."""
    ahead, behind, remote = state["main_ahead_of_origin"], state["main_behind_origin"], state["origin_main_tip"]
    if not behind:
        return None
    if ahead:
        return "MAIN_DIVERGED_FROM_ORIGIN", (
            f"local {main} {main_tip} has {ahead} commits origin/{main} lacks, "
            f"and origin/{main} {remote} has {behind} commits local {main} lacks"
        )
    return "MAIN_BEHIND_ORIGIN", f"local {main} {main_tip} is {behind} commits behind origin/{main} {remote}"


def changed_files(repo: Path, base: str, tip: str) -> set[str]:
    out = git(repo, "diff", "--name-only", "--no-renames", base, tip).stdout
    return {line for line in out.splitlines() if line}


def merge_preflight(repo: Path, branch: str, reviewed: str | None, main: str = "main") -> Report:
    topic_tip = rev(repo, branch)
    main_tip = rev(repo, main)
    if topic_tip is None or main_tip is None:
        raise GitError(f"cannot resolve {branch if topic_tip is None else main}")
    merge_base = git(repo, "merge-base", main_tip, topic_tip).stdout.strip()
    ev: dict = {
        "repo": str(repo),
        "branch": branch,
        "topic_tip": topic_tip,
        "main": main,
        "main_tip": main_tip,
        "merge_base": merge_base,
        "topic_commits": count(repo, f"{main_tip}..{topic_tip}"),
        "main_advanced_by": count(repo, f"{topic_tip}..{main_tip}"),
        "reviewed_sha": reviewed,
        "reviewed_state": None,
        "unreviewed_commits": None,
        "checkout_branch": git(repo, "branch", "--show-current").stdout.strip() or None,
        "checkout_dirty_paths": [
            line[3:] for line in git(repo, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()
        ],
        "conflict": None,
        "conflict_files": [],
        "overlap_files": [],
        **origin_state(repo, main, main_tip),
        "published_refs": [],
    }

    ev["checkout_clean"] = not ev["checkout_dirty_paths"]

    def report(verdict: str, reason: str, detail: str = "") -> Report:
        return Report("merge", verdict, reason, detail, evidence=ev)

    def unpublished_stop(reason: str) -> Report:
        stopped = report("STOP", reason)
        if not ev["published_refs"]:
            stopped.next = NEXT_UNPUBLISHED[reason]
        return stopped

    if reviewed is None:
        ev["reviewed_state"] = "missing"
        return report("STOP", "NO_REVIEWED_SHA")
    if not REVIEWED_SHA.fullmatch(reviewed):
        ev["reviewed_state"] = "invalid"
        return report("STOP", "REVIEWED_SHA_INVALID")
    reviewed_full = rev(repo, reviewed)
    # A ref whose name looks like hex takes precedence over an abbreviated
    # object ID; only a commit whose ID starts with the input counts.
    if reviewed_full is None or not reviewed_full.startswith(reviewed.lower()):
        ev["reviewed_state"] = "unknown"
        return report("STOP", "REVIEWED_SHA_UNKNOWN")
    ev["reviewed_sha"] = reviewed_full
    if not is_ancestor(repo, reviewed_full, topic_tip):
        ev["reviewed_state"] = "rewritten"
        return report("STOP", "REVIEW_REWRITTEN")
    ev["unreviewed_commits"] = count(repo, f"{reviewed_full}..{topic_tip}")
    if reviewed_full != topic_tip:
        ev["reviewed_state"] = "ancestor"
        return report("STOP", "UNREVIEWED_COMMITS")
    ev["reviewed_state"] = "tip"
    ev["published_refs"] = published_refs(repo, topic_tip)

    if is_ancestor(repo, topic_tip, main_tip):
        return report("STOP", "ALREADY_MERGED")
    if ev["checkout_branch"] != main:
        on = ev["checkout_branch"] or "a detached HEAD"
        return report("STOP", "CHECKOUT_NOT_ON_MAIN", f"the integration checkout is on {on}, not {main}")
    if not ev["checkout_clean"]:
        return report(
            "STOP", "CHECKOUT_DIRTY",
            f"{REASONS['CHECKOUT_DIRTY']}: {', '.join(ev['checkout_dirty_paths'])}",
        )
    if behind := origin_stop(ev, main, main_tip):
        return report("STOP", *behind)
    if merge_base == main_tip:
        return report("FF", "FAST_FORWARD")

    trial = git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", main_tip, topic_tip, check=False)
    if trial.returncode not in (0, 1):
        raise GitError(f"git merge-tree: {trial.stderr.strip() or trial.returncode}")
    ev["conflict"] = trial.returncode == 1
    if ev["conflict"]:
        ev["conflict_files"] = sorted({line for line in trial.stdout.splitlines()[1:] if line})
        return unpublished_stop("CONFLICT")
    overlap = changed_files(repo, merge_base, topic_tip) & changed_files(repo, merge_base, main_tip)
    ev["overlap_files"] = sorted(overlap)
    if overlap:
        return unpublished_stop("OVERLAP")
    if not ev["published_refs"]:
        return report(
            "STOP", "REBASE_REQUIRED",
            f"main advanced by {ev['main_advanced_by']} commits since the topic's base {merge_base} "
            f"and no origin branch is at the reviewed SHA {topic_tip}; rebase the topic onto main {main_tip}",
        )
    return report("NO_FF", "COMPATIBLE")


def same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def list_worktrees(repo: Path) -> list[dict]:
    entries: list[dict] = []
    for block in git(repo, "worktree", "list", "--porcelain").stdout.strip().split("\n\n"):
        entry: dict = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            entry[key] = value
        if entry:
            entries.append(entry)
    return entries


def cleanup_preflight(repo: Path, worktree: Path, main: str = "main") -> Report:
    entries = list_worktrees(repo)
    ev: dict = {"repo": str(repo), "worktree": str(worktree), "main": main}

    def report(verdict: str, reason: str) -> Report:
        return Report("cleanup", verdict, reason, evidence=ev)

    match = next((e for e in entries if same_path(Path(e["worktree"]), worktree)), None)
    if match is None:
        return report("KEEP", "NOT_A_WORKTREE")
    if match is entries[0]:
        return report("KEEP", "MAIN_CHECKOUT")
    main_tip = rev(repo, main)
    if main_tip is None:
        raise GitError(f"cannot resolve {main}")
    tip = match.get("HEAD")
    ev.update(
        branch=match.get("branch", "").removeprefix("refs/heads/") or None,
        tip=tip,
        main_tip=main_tip,
        locked="locked" in match,
        lock_reason=match.get("locked") or None,
        reachable_from_main=is_ancestor(repo, tip, main_tip),
    )
    status = git(worktree, "status", "--porcelain").stdout.splitlines()
    ev["dirty_paths"] = [line[3:] for line in status]
    if not ev["reachable_from_main"]:
        return report("KEEP", "NOT_MERGED")
    if ev["dirty_paths"]:
        return report("KEEP", "WORKTREE_DIRTY")
    return report("REMOVABLE", "SAFE_TO_REMOVE")


def read_integrations(repo: Path) -> list[dict]:
    """The records `atc integrate` appended to `<git common dir>/atc/integrations.jsonl`."""
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    try:
        lines = common.joinpath(*INTEGRATIONS).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("candidate_sha"), str):
            records.append(record)
    return records


def integration_view(record: dict) -> tuple[str | None, dict]:
    """The task and authority of an integration line; a line written before both existed is read as
    its authority reference without the ` @ <sha>` ending, authority unclassified, or a sync."""
    if isinstance(record.get("authority"), dict):
        return record.get("task"), record["authority"]
    if record.get("kind") == "sync":
        return record.get("source_revision"), {"kind": "sync"}
    reference = record.get("authority_reference")
    task = AUTHORITY_ENDING.sub("", reference).strip() if isinstance(reference, str) else None
    return task, {"kind": "unclassified"}


def short(sha: str | None) -> str:
    return sha[:SHORT_SHA] if sha else "-"


def push_listing(repo: Path, main: str = "main", own: set[str] | str | None = None) -> dict:
    """Every commit in origin/main..main, oldest first, with the integration that brought it in.

    An integration owns the commits from its recorded ``main_before`` to its
    candidate; a record without ``main_before`` owns those its candidate reaches
    that origin/main and every earlier integration in the range do not. Ownership
    stays with the earliest integration. ``own`` is the commits that
    carry this run's push approval, or the candidate SHA of the integration
    that owns them; every other commit is flagged.
    """
    main_tip = rev(repo, main)
    if main_tip is None:
        raise GitError(f"cannot resolve {main}")
    remote = origin_tip(repo, main)
    commits = []
    if remote:
        out = git(repo, "log", "--reverse", "--format=%H %s", f"{remote}..{main_tip}").stdout
        commits = [tuple(line.split(" ", 1)) if " " in line else (line, "") for line in out.splitlines() if line]
    position = {sha: index for index, (sha, _) in enumerate(commits)}
    records: dict[str, dict] = {}
    tasks: dict[str, list[str | None]] = {}
    for record in read_integrations(repo):
        if record["candidate_sha"] in position:
            records.setdefault(record["candidate_sha"], record)
            # A records-only binding of several tasks writes one line per task with one candidate.
            task = integration_view(record)[0]
            named = tasks.setdefault(record["candidate_sha"], [])
            if task is not None and task not in named:
                named.append(task)
    owner: dict[str, dict] = {}
    excluded = [remote] if remote else []
    for candidate in sorted(records, key=position.__getitem__):
        base = records[candidate].get("main_before")
        bounds = [base] if isinstance(base, str) and rev(repo, base) == base else []
        for sha in git(repo, "rev-list", candidate, "--not", *excluded, *bounds).stdout.split():
            owner.setdefault(sha, records[candidate])
        excluded.append(candidate)
    if isinstance(own, str):
        own = {sha for sha, record in owner.items() if record["candidate_sha"] == own}
    entries = []
    for sha, subject in commits:
        record = owner.get(sha, {})
        authority = integration_view(record)[1] if record else None
        named = tasks.get(record.get("candidate_sha"), [])
        entries.append({
            "sha": sha,
            "subject": subject,
            "integration": record.get("candidate_sha"),
            "reviewed": record.get("reviewed"),
            "kind": record.get("kind"),
            "source_revision": record.get("source_revision"),
            "authority_reference": record.get("authority_reference"),
            "task": named[0] if len(named) == 1 else None,
            "tasks": named,
            "authority": authority,
            "push_approval": None if own is None else sha in own,
        })
    return {"main": main, "main_tip": main_tip, "origin_main_tip": remote, "commits": entries}


def render_push(listing: dict) -> list[str]:
    """The listing as text: each integration's task and authority once, then one line per commit it brought in,
    flagged when it lacks this run's push approval. Full values stay in `--json`.

    A repository without origin/main has nothing to push to and lists nothing.
    """
    entries = listing["commits"]
    remote = listing["origin_main_tip"]
    if remote is None:
        return []
    lines = [f"push: {len(entries)} commits origin/{listing['main']} {short(remote)} -> {listing['main']} "
             f"{short(listing['main_tip'])}"]
    group = object()
    for entry in entries:
        key = (entry["integration"], entry["push_approval"])
        if key != group:
            group = key
            if entry["integration"]:
                authority = entry.get("authority") or {}
                named = ",".join(entry.get("tasks") or [])
                task = json.dumps(named, ensure_ascii=False) if named else "-"
                source = (f"integration {short(entry['integration'])} task={task} "
                          f"authority={authority.get('kind', '-')}")
            else:
                source = "no integration record"
            if entry["push_approval"] is True:
                source += " [this integration]"
            elif entry["push_approval"] is False:
                source += " [NO PUSH APPROVAL]"
            lines.append(f"  {source}")
        lines.append(f"    {short(entry['sha'])} {entry['subject']}")
    return [FULL_SHA_WORD.sub(r"\1", line) for line in lines]


def push_preflight(repo: Path, main: str = "main", own: set[str] | str | None = None) -> tuple[Report, dict | None]:
    """Fetch origin, then list what a push of main would carry; stop when main lacks origin commits."""
    error = fetch_origin(repo)
    if error:
        return Report("push", "STOP", "FETCH_FAILED", f"{REASONS['FETCH_FAILED']}: {error}"), None
    main_tip = rev(repo, main)
    if main_tip is None:
        raise GitError(f"cannot resolve {main}")
    state = origin_state(repo, main, main_tip)
    if behind := origin_stop(state, main, main_tip):
        return Report("push", "STOP", *behind, evidence={"main_tip": main_tip, **state}), None
    listing = push_listing(repo, main, own)
    count_ = len(listing["commits"])
    unapproved = sum(entry["push_approval"] is False for entry in listing["commits"])
    detail = f"{count_} commits in origin/{main}..{main}"
    if unapproved:
        detail += f"; {unapproved} lack this integration's push approval"
    return Report("push", "PUSH", "PUSH_LISTED", detail, evidence={"main_tip": main_tip, **state}), listing


def render(report: Report) -> str:
    lines = [f"{report.verdict} {report.reason}: {report.detail}"]
    if report.next:
        lines.append(f"next: {report.next}")
    for key, value in report.evidence.items():
        if isinstance(value, list):
            value = ", ".join(value) if value else "-"
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)

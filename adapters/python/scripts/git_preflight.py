#!/usr/bin/env python3
"""Read-only Git preflight for the lifecycle in shared/governance/GIT_WORKFLOW.md.

`merge` evaluates that file's integration table for one topic branch and
prints a verdict (FF / NO_FF / STOP), a stable reason code, and the evidence
behind it. `cleanup` reports whether a linked worktree is safe to hand to the
repo's own removal tooling (REMOVABLE / KEEP). Neither command writes to the
repository: conflicts are detected with `git merge-tree --write-tree`, which
creates only unreferenced objects.

Usage:
  git_preflight.py merge --repo <checkout> --branch <topic> [--reviewed <sha>] [--main main] [--json]
  git_preflight.py cleanup --repo <checkout> --worktree <path> [--main main] [--json]

Exit status: 0 for FF, NO_FF or REMOVABLE; 1 for STOP or KEEP; 2 for a usage
or git error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Hook-exported variables would redirect every `git -C <repo>` call to the
# hook's own repository.
SCRUBBED_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")

# A review binds to an object ID, never to a ref or revision expression that
# can move after the review.
REVIEWED_SHA = re.compile(r"[0-9a-fA-F]{7,40}")

REASONS = {
    "NO_REVIEWED_SHA": "no reviewed SHA given; integration requires a formal review bound to a commit",
    "REVIEWED_SHA_INVALID": "the reviewed SHA is not a 7-40 character hexadecimal object ID",
    "REVIEWED_SHA_UNKNOWN": "the reviewed SHA does not resolve to a commit in this repository",
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

    def __post_init__(self) -> None:
        self.detail = REASONS[self.reason]


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if key not in SCRUBBED_ENV}
    # Stops `git status` from refreshing and rewriting the index of a checkout
    # another agent may be using.
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
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
        "checkout_clean": not git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip(),
        "conflict": None,
        "conflict_files": [],
        "overlap_files": [],
    }

    def report(verdict: str, reason: str) -> Report:
        return Report("merge", verdict, reason, evidence=ev)

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

    if is_ancestor(repo, topic_tip, main_tip):
        return report("STOP", "ALREADY_MERGED")
    if ev["checkout_branch"] != main:
        return report("STOP", "CHECKOUT_NOT_ON_MAIN")
    if not ev["checkout_clean"]:
        return report("STOP", "CHECKOUT_DIRTY")
    if merge_base == main_tip:
        return report("FF", "FAST_FORWARD")

    trial = git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", main_tip, topic_tip, check=False)
    if trial.returncode not in (0, 1):
        raise GitError(f"git merge-tree: {trial.stderr.strip() or trial.returncode}")
    ev["conflict"] = trial.returncode == 1
    if ev["conflict"]:
        ev["conflict_files"] = sorted({line for line in trial.stdout.splitlines()[1:] if line})
        return report("STOP", "CONFLICT")
    overlap = changed_files(repo, merge_base, topic_tip) & changed_files(repo, merge_base, main_tip)
    ev["overlap_files"] = sorted(overlap)
    if overlap:
        return report("STOP", "OVERLAP")
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


def render(report: Report) -> str:
    lines = [f"{report.verdict} {report.reason}: {report.detail}"]
    for key, value in report.evidence.items():
        if isinstance(value, list):
            value = ", ".join(value) if value else "-"
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    merge = sub.add_parser("merge", help="evaluate the integration table for a topic branch")
    merge.add_argument("--repo", type=Path, required=True, help="integration checkout")
    merge.add_argument("--branch", required=True, help="topic branch")
    merge.add_argument("--reviewed", help="SHA named by the latest formal review")
    cleanup = sub.add_parser("cleanup", help="check whether a linked worktree is safe to remove")
    cleanup.add_argument("--repo", type=Path, required=True, help="integration checkout")
    cleanup.add_argument("--worktree", type=Path, required=True, help="linked worktree path")
    for p in (merge, cleanup):
        p.add_argument("--main", default="main", help="integration branch (default: main)")
        p.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "merge":
            report = merge_preflight(args.repo, args.branch, args.reviewed, args.main)
        else:
            report = cleanup_preflight(args.repo, args.worktree, args.main)
    except GitError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(report), indent=2) if args.json else render(report))
    return 0 if report.verdict in ("FF", "NO_FF", "REMOVABLE") else 1


if __name__ == "__main__":
    sys.exit(main())

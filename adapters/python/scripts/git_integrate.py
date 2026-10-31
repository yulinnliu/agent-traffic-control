#!/usr/bin/env python3
"""Integrate one reviewed topic into the local main of a project checkout.

Executes the integration table in shared/governance/GIT_WORKFLOW.md through
`git_preflight.merge_preflight`: FF runs `git merge --ff-only <reviewed>`,
NO_FF runs `git merge --no-ff` with the `merge(<topic>): integrate reviewed
changes` subject, and STOP prints the preflight report and leaves the
repository untouched. The command advances the local main only.

Usage:
  git_integrate.py --repo <main checkout> --branch <topic> --reviewed <40-hex sha> [--main main] [--json]

Exit status: 0 when main advanced and the result verified; 1 for STOP, a
failed merge or a failed post-merge check; 2 for a usage, checkout or git
error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from git_preflight import GitError, git, merge_preflight, render, rev, same_path

FULL_SHA = re.compile(r"[0-9a-f]{40}")
WORKTREE_BRANCH = re.compile(r"worktree-[^+]+\+(?P<topic>.+)")


class Refused(Exception):
    pass


def topic_name(branch: str) -> str:
    match = WORKTREE_BRANCH.fullmatch(branch)
    if match:
        return match["topic"]
    for prefix in ("topic/", "task/"):
        if branch.startswith(prefix):
            return branch.removeprefix(prefix)
    return branch


def check_checkout(repo: Path) -> None:
    top = git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    if not same_path(Path(top), repo):
        raise Refused(f"--repo must be the top of the checkout: {top}")
    git_dir, common_dir = git(repo, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir").stdout.splitlines()
    if not same_path(Path(git_dir), Path(common_dir)):
        raise Refused("--repo is a linked worktree; use the main checkout")


def integrate(repo: Path, branch: str, reviewed: str, main: str = "main") -> tuple[int, dict]:
    report = merge_preflight(repo, branch, reviewed, main)
    main_before = report.evidence["main_tip"]
    result = {
        "command": "integrate",
        "verdict": report.verdict,
        "reason": report.reason,
        "main_before": main_before,
        "main_after": main_before,
        "reviewed": reviewed,
        "first_parent": None,
        "second_parent": None,
        "message": None,
    }
    if report.verdict == "STOP":
        return 1, {**result, "report": report}
    # The preflight recorded main_tip; the merge acts on HEAD, so main must
    # still be there.
    if rev(repo, main) != main_before:
        return 1, {**result, "error": f"{main} moved after preflight; main unchanged"}
    if report.verdict == "FF":
        merged = git(repo, "merge", "--ff-only", reviewed, check=False)
    else:
        result["message"] = f"merge({topic_name(branch)}): integrate reviewed changes"
        merged = git(repo, "merge", "--no-ff", "--no-edit", "-m", result["message"], reviewed, check=False)
    if merged.returncode:
        git(repo, "merge", "--abort", check=False)
        return 1, {**result, "error": f"{(merged.stderr or merged.stdout).strip()}\nmerge aborted; main unchanged"}

    main_after = rev(repo, main)
    result["main_after"] = main_after
    if report.verdict == "FF":
        if main_after != reviewed:
            return 1, {**result, "error": f"verification failed: {main} is {main_after}, expected {reviewed}"}
        return 0, result
    first, second = git(repo, "rev-parse", f"{main_after}^1", f"{main_after}^2").stdout.split()
    result.update(first_parent=first, second_parent=second)
    if (first, second) != (main_before, reviewed):
        return 1, {**result, "error": (
            f"verification failed: parents {first} {second}, expected {main_before} {reviewed}"
        )}
    return 0, result


def render_result(result: dict) -> str:
    line = f"{result['verdict']} main {result['main_before']} -> {result['main_after']}"
    if result["verdict"] == "NO_FF":
        line += f" (parents {result['first_parent']} {result['second_parent']})"
    return line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True, help="main checkout of the project repository")
    parser.add_argument("--branch", required=True, help="reviewed topic branch")
    parser.add_argument("--reviewed", required=True, help="full 40-character SHA named by the review")
    parser.add_argument("--main", default="main", help="integration branch (default: main)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not FULL_SHA.fullmatch(args.reviewed):
            raise Refused(f"--reviewed must be a full 40-character lowercase SHA: {args.reviewed}")
        check_checkout(args.repo)
        code, result = integrate(args.repo, args.branch, args.reviewed, args.main)
    except (Refused, GitError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    report = result.pop("report", None)
    error = result.pop("error", None)
    if report is not None:
        print(json.dumps({**result, "preflight": asdict(report)}, indent=2) if args.json else render(report))
    elif error is not None:
        print(f"ERROR {error}", file=sys.stderr)
        if args.json:
            print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2) if args.json else render_result(result))
    return code


if __name__ == "__main__":
    sys.exit(main())

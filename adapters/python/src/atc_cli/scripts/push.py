#!/usr/bin/env python3
"""`atc push`: publish main of registered repositories (governance `GIT_WORKFLOW.md` § Publication boundary).

Each repository is fetched, every commit in origin/main..main must come from a recorded integration or
sync, and the checked SHA is pushed to origin/main and confirmed on the remote. Bare `atc push` checks
every repository; `--all` lists each one and asks y/N before pushing it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cli_conventions
import git_preflight as gp
import managed_worktree as hw

ROOT = hw.ROOT
REFUSE_HINTS = {
    "FETCH_FAILED": "restore access to origin (network, credentials), then run again",
    "MAIN_BEHIND_ORIGIN": "fast-forward local main to origin/main (git merge --ff-only origin/main), then run again",
    "MAIN_DIVERGED_FROM_ORIGIN": "the Owner decides how the local-only main commits reach origin",
    "UNRECORDED_COMMITS": "only integrated or synced commits are pushed; integrate the topic with atc integrate ID",
    "PUSH_FAILED": "read git's message, fix the remote or access, then run again",
    "REMOTE_MISMATCH": "origin/main is not at the pushed SHA; fetch and check again",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc push", allow_abbrev=False, **cli_conventions.form(
        "Push main of registered repositories to origin after checking every commit it carries.",
        "Every commit in origin/main..main must come from a recorded integration or sync (the integration\n"
        "ledger in the repository's Git directory). NAME is a project in workspace.yaml, or root. Bare\n"
        "atc push checks every repository; --all lists each and asks y/N before pushing it. The Owner runs it;\n"
        "agents run only --check.",
        exit="0 pushed, current or checked; 1 STOP (fetch failed, main behind or diverged, an unrecorded commit, "
             "declined); 2 ERROR",
        see="GIT_WORKFLOW.md § Publication boundary"))
    parser.add_argument("names", nargs="*", metavar="NAME", help="registered project names, or root")
    parser.add_argument("--all", action="store_true", help="every registered repository, each after a y/N question")
    parser.add_argument("--check", action="store_true", help="fetch and list what would be pushed; push nothing")
    cli_conventions.add_verbose(parser)
    cli_conventions.add_json(parser)
    return parser


def repositories(root: Path, names: list[str]) -> list[tuple[str, Path]]:
    """The main checkouts ``names`` select, every registered one (root first) when empty."""
    try:
        ws = hw.load_workspace(root, dict(os.environ))
    except hw.Stop as exc:
        raise cli_conventions.Refusal(exc.result.code, f"{exc.result.target}: {exc.result.detail}",
                                      "fix workspace.yaml", cli_conventions.ERROR) from None
    known = {"root": hw.root_project(ws), **ws.projects}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise cli_conventions.Refusal("PROJECT_UNKNOWN", f"{', '.join(unknown)} not registered in workspace.yaml",
                                      f"registered: root, {', '.join(sorted(ws.projects)) or 'none'}",
                                      cli_conventions.ERROR)
    return [(name, Path(known[name].repo)) for name in (names or known)]


def assess(name: str, repo: Path) -> tuple[cli_conventions.Refusal | None, dict | None]:
    """Fetch and list origin/main..main; a refusal when main cannot be pushed as it stands."""
    report, listing = gp.push_preflight(repo, "main")
    if report.verdict != "PUSH":
        return cli_conventions.Refusal(report.reason, f"{name}: {report.detail}", REFUSE_HINTS.get(report.reason)), None
    if listing["origin_main_tip"] is None:
        return cli_conventions.Refusal("NO_ORIGIN_MAIN", f"{name}: origin has no main",
                                       "the Owner publishes main the first time by hand"), listing
    unrecorded = [entry["sha"][:12] for entry in listing["commits"] if entry["integration"] is None]
    if unrecorded:
        return cli_conventions.Refusal("UNRECORDED_COMMITS", f"{name}: {', '.join(unrecorded)}",
                                       REFUSE_HINTS["UNRECORDED_COMMITS"]), listing
    return None, listing


def publish(repo: Path, sha: str) -> cli_conventions.Refusal | None:
    """Push ``sha`` to origin/main and confirm the remote now names it."""
    pushed = gp.git(repo, "push", gp.REMOTE, f"{sha}:refs/heads/main", check=False)
    if pushed.returncode != 0:
        return cli_conventions.Refusal("PUSH_FAILED", pushed.stderr.strip() or "git push failed",
                                       REFUSE_HINTS["PUSH_FAILED"], cli_conventions.ERROR)
    remote = gp.git(repo, "ls-remote", gp.REMOTE, "refs/heads/main", check=False).stdout.split()
    if not remote or remote[0] != sha:
        return cli_conventions.Refusal("REMOTE_MISMATCH", f"origin/main is {remote[0][:12] if remote else 'absent'}",
                                       REFUSE_HINTS["REMOTE_MISMATCH"])
    gp.fetch_origin(repo)
    return None


def main(argv: list[str] | None = None, root: Path = ROOT, answer=input) -> int:
    args = build_parser().parse_args(argv)
    if args.all and args.names:
        return cli_conventions.refuse(cli_conventions.Refusal(
            "USAGE", "name repositories or pass --all, not both", "atc push NAME... | --all",
            cli_conventions.ERROR), args.json)
    check = args.check or not (args.all or args.names)
    try:
        targets = repositories(root, args.names)
    except cli_conventions.Refusal as refusal:
        return cli_conventions.refuse(refusal, args.json)
    code = cli_conventions.OK
    rows = []
    for name, repo in targets:
        try:
            refusal, listing = assess(name, repo)
        except gp.GitError as exc:
            refusal, listing = cli_conventions.Refusal("GIT_FAILED", f"{name}: {exc}", "check the repository's Git "
                                                       "state", cli_conventions.ERROR), None
        commits = listing["commits"] if listing else []
        row = {"name": name, "repo": str(repo), "commits": len(commits),
               "main_tip": listing["main_tip"] if listing else None}
        if refusal is None and not commits:
            row["verdict"] = "CURRENT"
        elif refusal is None and check:
            row["verdict"] = "CHECK"
        elif refusal is None:
            if args.all and not args.json:
                print("\n".join(gp.render_push(listing)))
                try:
                    reply = answer(f"push {name}? [y/N] ")
                except EOFError:
                    reply = ""
                if reply.strip().lower() not in ("y", "yes"):
                    refusal = cli_conventions.Refusal("DECLINED", f"{name} was not pushed",
                                                      f"atc push {name}")
            if refusal is None:
                refusal = publish(repo, listing["main_tip"])
            row["verdict"] = "PUSHED" if refusal is None else "REFUSED"
        else:
            row["verdict"] = "REFUSED"
        if refusal is not None:
            row["verdict"] = "REFUSED"
            row.update(reason=refusal.token, detail=refusal.detail, hint=refusal.hint)
            code = max(code, refusal.code)
        rows.append(row)
        if args.json:
            continue
        tip = (row["main_tip"] or "")[:7]
        print(f"{row['verdict']} {name}" + (f" {tip} ({len(commits)} commits)" if commits else ""))
        if refusal is not None:
            for line in refusal.lines():
                print(f"  {line}")
        elif commits and (args.verbose or row["verdict"] == "CHECK"):
            for line in gp.render_push(listing)[1:]:
                print(f"  {line}")
    if args.json:
        print(json.dumps(cli_conventions.envelope("push", code, {"repositories": rows}, "repositories"), indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())

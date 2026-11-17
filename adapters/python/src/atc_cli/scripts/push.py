#!/usr/bin/env python3
"""`atc push`: publish main of registered repositories (governance `GIT_WORKFLOW.md` § Publication boundary).

Each repository is fetched, every commit in origin/main..main must come from a recorded integration or
sync, and the checked SHA is pushed to origin/main and confirmed on the remote. Bare `atc push` checks
every repository; `--all` lists each one and asks y/N before pushing it. The pre-push hook runs before git
connects to origin, and the push skips it. Each step's progress line, and the hook's and git's own output, go
to stderr as they happen.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
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
    "GATE_FAILED": "fix what the pre-push hook's output names, integrate the fix, then run again",
    "PUSH_FAILED": "read git's message, fix the remote or access, then run again",
    "REMOTE_MISMATCH": "origin/main is not at the pushed SHA; fetch and check again",
}
# The lines of a failed push's streamed output that are git's or the transport's own, which its refusal repeats;
# the pre-push hook's output streamed once and stays out of it.
GIT_MESSAGE = re.compile(r"^(?:(?:error|fatal|remote): |To |ssh: | ! |Connection to .* closed)")
# What an exit status of git push says when git prints nothing of its own.
EXIT_MEANINGS = {141: "SIGPIPE: the connection to the remote closed before git wrote to it"}


def push_failure(code: int, output: str) -> str:
    """The detail of ``PUSH_FAILED``: git's exit code, its meaning when known, and git's and the transport's own
    lines, without the hook's output already streamed."""
    meaning = EXIT_MEANINGS.get(code)
    head = f"git push exited {code}" + (f" ({meaning})" if meaning else "")
    own = list(dict.fromkeys(line.rstrip() for line in output.splitlines() if GIT_MESSAGE.match(line)))
    return "\n".join([head, *own])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc push", allow_abbrev=False, **cli_conventions.form(
        "Push main of registered repositories to origin after checking every commit it carries.",
        "Every commit in origin/main..main must be one an integration or sync recorded in the integration\n"
        "ledger in the repository's Git directory, also after main was reset below that integration's\n"
        "candidate (listed as 'candidate off main'). NAME is a project in workspace.yaml, or root. Bare\n"
        "atc push checks every repository; --all lists each and asks y/N before pushing it. The Owner runs it;\n"
        "agents run only --check.\n\n"
        "Progress goes to stderr, also under --json: one line as each step of a repository ends,\n"
        "'STEP <step> <name> <seconds>s <result>', for fetch, ledger (the commits in origin/main..main and\n"
        "how many are recorded), gate (the pre-push hook, run before git connects to origin), push (git push\n"
        "--no-verify, the hook having run) and read-back (the SHA origin/main names); gate and push also have\n"
        "a line as they start. The hook's and git push's own output stream to stderr as they run.",
        exit="0 pushed, current or checked; 1 STOP (fetch failed, main behind or diverged, an unrecorded commit, "
             "declined); 2 ERROR (the gate or the push failed)",
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


def count_commits(n: int) -> str:
    return f"{n} commit" if n == 1 else f"{n} commits"


def assess(name: str, repo: Path, steps: cli_conventions.Steps) -> tuple[cli_conventions.Refusal | None, dict | None]:
    """Fetch and list origin/main..main; a refusal when main cannot be pushed as it stands."""
    steps.begin()
    error = gp.fetch_origin(repo)
    steps.done("fetch", "FAILED" if error else "OK")
    if error:
        return cli_conventions.Refusal("FETCH_FAILED", f"{name}: {gp.fetch_report(error).detail}",
                                       REFUSE_HINTS["FETCH_FAILED"]), None
    steps.begin()
    report, listing = gp.push_state(repo, "main")
    if report.verdict != "PUSH":
        steps.done("ledger", report.reason)
        return cli_conventions.Refusal(report.reason, f"{name}: {report.detail}", REFUSE_HINTS.get(report.reason)), None
    if listing["origin_main_tip"] is None:
        steps.done("ledger", "origin has no main")
        return cli_conventions.Refusal("NO_ORIGIN_MAIN", f"{name}: origin has no main",
                                       "the Owner publishes main the first time by hand"), listing
    unrecorded = [entry["sha"][:12] for entry in listing["commits"] if entry["integration"] is None]
    steps.done("ledger", f"{count_commits(len(listing['commits']))} in origin/main..main, "
                         f"{len(listing['commits']) - len(unrecorded)} recorded")
    if unrecorded:
        return cli_conventions.Refusal("UNRECORDED_COMMITS", f"{name}: {', '.join(unrecorded)}",
                                       REFUSE_HINTS["UNRECORDED_COMMITS"]), listing
    return None, listing


def gate(repo: Path, sha: str, remote_sha: str | None, steps: cli_conventions.Steps,
         hook_env: dict[str, str] | None = None) -> cli_conventions.Refusal | None:
    """Run the repository's pre-push hook for pushing ``sha`` over ``remote_sha`` before git connects to the remote,
    with the arguments and stdin git push gives it (githooks(5) § pre-push), its output streamed to stderr. Git runs
    the hook after it has connected, and a remote that drops the idle connection while a long gate runs fails the
    push (docs/design/ATC-0063.md § 8)."""
    steps.begin("gate", gp.short(sha))
    url = gp.git(repo, "remote", "get-url", "--push", gp.REMOTE).stdout.strip()
    line = f"refs/heads/main {sha} refs/heads/main {remote_sha or '0' * len(sha)}\n"
    with tempfile.TemporaryDirectory(prefix="atc-push-") as directory:
        stdin = Path(directory) / "pre-push.stdin"
        stdin.write_text(line, encoding="utf-8", newline="\n")
        code, _ = gp.git_streamed(repo, "hook", "run", "--ignore-missing", f"--to-stdin={stdin}", "pre-push", "--",
                                  gp.REMOTE, url, out=sys.stderr, extra_env=hook_env)
    steps.done("gate", "OK" if code == 0 else f"FAILED exit {code}")
    if code != 0:
        return cli_conventions.Refusal("GATE_FAILED", f"the pre-push hook exited {code}; its output is above",
                                       REFUSE_HINTS["GATE_FAILED"], cli_conventions.ERROR)
    return None


def publish(repo: Path, sha: str, steps: cli_conventions.Steps) -> cli_conventions.Refusal | None:
    """Push ``sha`` to origin/main, with git's output streamed to stderr, and confirm the remote now names it. The
    pre-push hook already ran in ``gate``, so the push skips it. The push itself moves origin/main's remote-tracking
    ref, so no fetch follows."""
    steps.begin("push", f"{gp.short(sha)} -> origin/main")
    # Git shows its transfer progress only on a terminal; its stderr here is a pipe.
    progress = ["--progress"] if sys.stderr.isatty() else []
    code, output = gp.git_streamed(repo, "push", "--no-verify", *progress, gp.REMOTE, f"{sha}:refs/heads/main",
                                   out=sys.stderr)
    steps.done("push", "OK" if code == 0 else f"FAILED exit {code}")
    if code != 0:
        return cli_conventions.Refusal("PUSH_FAILED", push_failure(code, output), REFUSE_HINTS["PUSH_FAILED"],
                                       cli_conventions.ERROR)
    steps.begin()
    remote = gp.git(repo, "ls-remote", gp.REMOTE, "refs/heads/main", check=False).stdout.split()
    named = gp.short(remote[0]) if remote else "absent"
    steps.done("read-back", f"origin/main {named}")
    if not remote or remote[0] != sha:
        return cli_conventions.Refusal("REMOTE_MISMATCH", f"origin/main is {named}", REFUSE_HINTS["REMOTE_MISMATCH"])
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
        steps = cli_conventions.Steps(name)
        try:
            refusal, listing = assess(name, repo, steps)
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
            if args.all:
                # The question and the listing it asks about are human lines: on stderr under --json, which
                # keeps stdout for the JSON object (CLI.md § Output). No answer is a no.
                with contextlib.redirect_stdout(sys.stderr if args.json else sys.stdout):
                    print("\n".join(gp.render_push(listing)))
                    try:
                        reply = answer(f"push {name}? [y/N] ")
                    except EOFError:
                        reply = ""
                if reply.strip().lower() not in ("y", "yes"):
                    refusal = cli_conventions.Refusal("DECLINED", f"{name} was not pushed",
                                                      f"atc push {name}")
            if refusal is None:
                tasks = [task for entry in commits for task in entry.get("tasks") or ()]
                refusal = (gate(repo, listing["main_tip"], listing["origin_main_tip"], steps,
                                cli_conventions.testkit_inputs(name, tasks))
                           or publish(repo, listing["main_tip"], steps))
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
        print(f"{row['verdict']} {name}" + (f" {tip} ({count_commits(len(commits))})" if commits else ""))
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

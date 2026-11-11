#!/usr/bin/env python3
"""Close out an integrated task: its worktree, scratches and report files (docs/TASKS.md § Close-out).

Rules: governance `DELIVERY.md` § Close-out; options: `--help`.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

from atc_testkit import locations, scratch
import cli_conventions
import managed_worktree as hw
import task_records

INTEGRATIONS = ("atc", "integrations.jsonl")
ITEMS = ("worktree", "scratches", "reports")
REPORTS = "reports"


class Stop(Exception):
    """A refusal before anything is removed."""

    def __init__(self, token: str, detail: str) -> None:
        super().__init__(f"{token} {detail}")
        self.token, self.detail = token, detail


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                          env=task_records._env(), stdin=subprocess.DEVNULL)


def integrated(repo: Path, task: str) -> str:
    """The candidate SHA of the task's latest integration that local ``main`` contains."""
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    try:
        lines = Path(common).joinpath(*INTEGRATIONS).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    candidates = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("task") == task and not entry.get("records_only") and isinstance(entry.get("candidate_sha"), str):
            candidates.append(entry["candidate_sha"])
    for sha in reversed(candidates):
        if git(repo, "merge-base", "--is-ancestor", sha, "main").returncode == 0:
            return sha
    raise Stop("NOT_INTEGRATED", f"integrations.jsonl of {repo} names no integration of {task} that main contains")


def record(repo: Path, task: str) -> dict:
    shown = git(repo, "show", f"main:{task_records.TASKS_DIR}/{task}.yaml")
    if shown.returncode != 0:
        raise Stop("RECORD_MISSING", f"main of {repo} holds no record of {task}")
    try:
        data = yaml.safe_load(shown.stdout)
    except yaml.YAMLError as error:
        raise Stop("RECORD_INVALID", f"{task}: {error}") from None
    keep = data.get("keep", []) if isinstance(data, dict) else None
    if not isinstance(keep, list) or not set(keep) <= set(ITEMS):
        raise Stop("RECORD_INVALID", f"{task}: keep is a list of {', '.join(ITEMS)}")
    return data


def task_worktree(repo: Path, branch: str) -> Path | None:
    listed = git(repo, "worktree", "list", "--porcelain").stdout.split("\n\n")
    for block in listed:
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        if fields.get("branch") == f"refs/heads/{branch}":
            return Path(fields["worktree"])
    return None


def leftover_directory(root: Path, name: str, branch: str, environ: dict[str, str]) -> Path | None:
    """The managed path of ``worktree-<owner>+<topic>`` when Git no longer lists it but a directory is still there."""
    owner, plus, topic = branch.removeprefix(hw.BRANCH_PREFIX).partition("+")
    if not branch.startswith(hw.BRANCH_PREFIX) or not plus or owner not in hw.OWNERS or not hw.TOPIC.fullmatch(topic):
        return None
    try:
        ws = hw.load_workspace(root, environ)
    except hw.Stop:
        return None
    project = hw.root_project(ws) if name == hw.root_project(ws).name else ws.projects.get(name)
    path = Path(project.worktrees) / hw.durable(owner, topic)[0] if project is not None else None
    return path if path is not None and os.path.lexists(path) else None


def remove_worktree(root: Path, name: str, worktree: Path, cwd: str, environ: dict[str, str]) -> str:
    """One report line for the task's worktree, removed through `atc worktree clean` and its checks."""
    if Path(os.path.normcase(os.path.realpath(cwd))).is_relative_to(os.path.normcase(os.path.realpath(worktree))):
        return f"SKIPPED {worktree} (working directory)"
    owner, plus, topic = worktree.name.partition("+")
    if not plus or owner not in hw.OWNERS:
        return f"REFUSED {worktree} (not a managed worktree <owner>+<topic>)"
    target = ["--project", name]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = hw.main(["clean", *target, "--owner", owner, "--topic", topic, "--json"], root=root,
                       environ=environ, home=str(locations.atc_home(environ).parent), cwd=cwd)
    if code == 0:
        return f"DELETED {worktree}"
    try:
        first = json.loads(out.getvalue())["results"][0]
        why = f"{first['reason']}: {first['detail']}" if first.get("detail") else first["reason"]
    except (ValueError, KeyError, IndexError):
        why = f"worktree clean exited {code}"
    return f"REFUSED {worktree} ({why})"


def main(argv: list[str] | None = None, root: Path = task_records.ROOT, environ: dict[str, str] | None = None,
         cwd: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atc close-out", allow_abbrev=False, **cli_conventions.form(
        "Close out an integrated task: remove its worktree, scratches and report files.",
        exit="0 every item DELETED or SKIPPED; 1 STOP before anything is removed, or an item REFUSED with nothing "
             "deleted; 3 PARTIAL: an item REFUSED while another was deleted; 2 ERROR",
        see="docs/TASKS.md § Close-out; DELIVERY.md § Close-out"))
    parser.add_argument("--task", required=True, metavar="ID", help="the integrated task, for example ATC-0008")
    cli_conventions.add_project(parser, " (default: the repository declaring the task's prefix, else the working "
                                        "directory's)")
    parser.add_argument("--skip", action="append", choices=ITEMS, default=[], metavar="ITEM",
                        help=f"leave this item for this run only, nothing written ({', '.join(ITEMS)}; repeatable; "
                             "an absent item is a no-op); the record's keep is permanent")
    cli_conventions.add_json(parser)
    args = parser.parse_args(argv)
    environ = dict(os.environ) if environ is None else environ
    cwd = cwd or os.getcwd()
    lines: list[str] = []
    try:
        [(name, checkout)] = task_records.resolve(root, environ, cwd, args.project, task=args.task)
        integrated(checkout, args.task)
        data = record(checkout, args.task)
    except (Stop, task_records.Refused) as stop:
        error = stop.token in task_records.ERROR_TOKENS
        print(f"ERROR {stop.token} {stop.detail}" if error else f"STOP {stop.token}: {stop.detail}", file=sys.stderr)
        if args.json:
            print(json.dumps({"task": args.task, "refusal": {"verdict": "ERROR" if error else "STOP",
                                                             "reason": stop.token, "detail": stop.detail,
                                                             "next": None}}, indent=2))
        return cli_conventions.ERROR if error else cli_conventions.STOP
    keep, skip = set(data.get("keep", [])), set(args.skip)

    def skipped(item: str) -> str | None:
        """The source that leaves ``item`` this run: the record's permanent ``keep`` first, then ``--skip``."""
        return "keep" if item in keep else "--skip" if item in skip else None

    branch = str(data.get("branch", ""))
    worktree = task_worktree(checkout, branch) or leftover_directory(root, name, branch, environ)
    if worktree is None:
        pass
    elif skipped("worktree"):
        lines.append(f"SKIPPED {worktree} ({skipped('worktree')})")
    else:
        lines.append(remove_worktree(root, name, worktree, cwd, environ))

    tmp = locations.locate(environ).tmp
    scratches = [row.scratch for row in scratch.registered(tmp) if row.task == args.task]
    if skipped("scratches"):
        lines += [f"SKIPPED {item} ({skipped('scratches')})" for item in scratches]
    elif scratches:
        scratch.clean(scratches, say=lines.append, environ=environ, with_runs=True)

    reports = sorted((locations.atc_home(environ) / REPORTS).glob(f"{args.task}-*.md"))
    for report in reports:
        if skipped("reports"):
            lines.append(f"SKIPPED {report} ({skipped('reports')})")
            continue
        try:
            report.unlink()
            lines.append(f"DELETED {report}")
        except OSError as error:
            lines.append(f"REFUSED {report} ({error})")

    counts = {word: sum(line.startswith(f"{word} ") for line in lines) for word in ("DELETED", "SKIPPED", "REFUSED")}
    if not counts["REFUSED"]:
        code = cli_conventions.OK
    else:
        code = cli_conventions.PARTIAL if counts["DELETED"] else cli_conventions.STOP
        print(f"STOP ITEM_REFUSED: {counts['REFUSED']} refused, {counts['DELETED']} deleted; each REFUSED item "
              f"names the path and why", file=sys.stderr)
    if args.json:
        items = []
        for line in lines:
            outcome, _, rest = line.partition(" ")
            target, _, why = rest.partition(" (")
            items.append({"outcome": outcome, "path": target, "why": why[:-1] if why.endswith(")") else why or None})
        print(json.dumps({"verdict": {0: "DONE", 1: "REFUSED", 3: "PARTIAL"}[code], "task": args.task,
                          "items": items, "counts": {k.lower(): v for k, v in counts.items()}}, indent=2))
        return code
    for line in lines:
        print(line)
    print(f"close-out {args.task}: {counts['DELETED']} deleted, {counts['SKIPPED']} skipped, "
          f"{counts['REFUSED']} refused")
    return code


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""`atc task close`: a task's worktree and branch, scratches and report files (docs/TASKS.md § Close).

Rules: governance `DELIVERY.md` § Close-out. Report files are also read here for `atc integrate`, which takes
the reviewed SHA from the newest PASS review.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

from atc_testkit import locations, scratch
import cli_conventions
from cli_conventions import Refusal
import managed_worktree as hw
import task_records

INTEGRATIONS = ("atc", "integrations.jsonl")
ITEMS = ("worktree", "scratches", "reports")
REPORTS = "reports"
CONSENT = "CONSENT"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                          env=task_records._env(), stdin=subprocess.DEVNULL)


def integrated(repo: Path, task: str) -> dict:
    """The line of the task's latest integration whose candidate local ``main`` contains."""
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
        if (isinstance(entry, dict) and entry.get("task") == task and not entry.get("records_only")
                and isinstance(entry.get("candidate_sha"), str)):
            candidates.append(entry)
    for entry in reversed(candidates):
        if git(repo, "merge-base", "--is-ancestor", entry["candidate_sha"], "main").returncode == 0:
            return entry
    raise Refusal("NOT_INTEGRATED", f"no integration of {task} is on main",
                  f"integrate it first (atc integrate {task}), or close it with --abandon")


def _rev(repo: Path, spec: str) -> str | None:
    run = git(repo, "rev-parse", "--verify", "--quiet", spec)
    return run.stdout.strip() if run.returncode == 0 and run.stdout.strip() else None


def squash_integrated(repo: Path, entry: dict, branch: str | None) -> bool:
    """Whether ``entry`` records an Owner-authorized squash of the branch's tip (GIT_WORKFLOW.md § Explicitly
    authorized squash): its squash commit, on ``main``, has the recorded target tip as its only parent and the
    recorded source tip's tree, and the branch still ends at that source tip."""
    authority = entry.get("authority")
    source, target, squash = entry.get("reviewed"), entry.get("main_before"), entry["candidate_sha"]
    if (entry.get("kind") != "squash" or branch is None or not isinstance(authority, dict)
            or authority.get("kind") != "owner_words" or not isinstance(authority.get("quote"), str)
            or not authority["quote"].strip() or not isinstance(source, str) or not isinstance(target, str)):
        return False
    parents = git(repo, "rev-list", "--parents", "-n", "1", squash).stdout.split()[1:]
    tree = _rev(repo, f"{squash}^{{tree}}")
    return (parents == [_rev(repo, f"{target}^{{commit}}")] and tree is not None
            and tree == _rev(repo, f"{source}^{{tree}}")
            and _rev(repo, f"refs/heads/{branch}") == _rev(repo, f"{source}^{{commit}}"))


def keep_of(record: dict, task: str) -> set[str]:
    keep = record.get("keep", [])
    if not isinstance(keep, list) or not set(keep) <= set(ITEMS):
        raise Refusal("RECORD_INVALID", f"{task}: keep is a list of {', '.join(ITEMS)}", None, cli_conventions.ERROR)
    return set(keep)


def _real(path: str | Path) -> Path:
    return Path(os.path.normcase(os.path.realpath(path)))


def task_scratches(tmp: Path, task: str, worktree: Path | None) -> list[str]:
    """The scratches whose registry line names ``task`` or whose tree lies inside the task's worktree."""
    inside = _real(worktree) if worktree is not None else None
    return [row.scratch for row in scratch.registered(tmp)
            if row.task == task or (inside is not None and row.tree.strip() and _real(row.tree).is_relative_to(inside))]


def unregistered_scratches(tmp: Path, task: str, taken: list[str]) -> list[Path]:
    """The directories under ``tmp`` named for ``task`` (its ID, any case, then the end or a character other than a
    digit) that are not among ``taken``."""
    prefix = task.lower()
    try:
        children = sorted(tmp.iterdir())
    except OSError:
        return []
    return [child for child in children
            if child.is_dir() and child.name.lower().startswith(prefix)
            and not child.name[len(prefix):len(prefix) + 1].isdigit()
            and not any(locations.same_path(child, path) for path in taken)]


REPORT_NAME = re.compile(r"(?P<task>.+?)-(?P<sha>[0-9a-f]{7,40})(?P<review>-review)?\.md")
NEGATIONS = frozenset({"NO", "NOT"})
FILLERS = frozenset({"A", "AN", "YET"})
JOINED_CONDITIONAL = frozenset({"CONDITIONAL-PASS", "CONDITIONAL_PASS"})


def _negated(words: list[str], index: int) -> bool:
    """Whether ``NO`` or ``NOT`` stands before ``words[index]``, across ``A``, ``AN`` and ``YET``."""
    index -= 1
    while index >= 0 and words[index] in FILLERS:
        index -= 1
    return index >= 0 and words[index] in NEGATIONS


def verdict_of(line: str) -> str | None:
    """The first whole-word ``CONDITIONAL PASS``, ``PASS`` or ``FAIL`` of a line in any case, a leading ``#``
    stripped and a negated word skipped; a ``PASS`` that a later, unnegated ``CONDITIONAL`` qualifies before any
    ``FAIL`` is ``CONDITIONAL PASS``."""
    words = [word.upper() for word in re.findall(r"[\w-]+", line.lstrip().lstrip("#"))]
    for index, word in enumerate(words):
        if _negated(words, index):
            continue
        if word in JOINED_CONDITIONAL or (word == "CONDITIONAL" and words[index + 1:index + 2] == ["PASS"]):
            return "CONDITIONAL PASS"
        if word == "FAIL":
            return word
        if word == "PASS" and (not index or words[index - 1] != "CONDITIONAL"):
            for later in range(index + 1, len(words)):
                if words[later] == "FAIL":
                    break
                if words[later] == "CONDITIONAL" and not _negated(words, later):
                    return "CONDITIONAL PASS"
            return word
    return None


def review_verdict(report: Path) -> str | None:
    first = next((line for line in report.read_text(encoding="utf-8").splitlines() if line.strip()), "")
    return verdict_of(first)


def latest_pass_review(task: str, environ: dict[str, str]) -> tuple[str, Path] | None:
    """The SHA named by the newest `<ID>-<sha>-review.md` whose verdict is PASS, with its file."""
    found = []
    for report in (locations.atc_home(environ) / REPORTS).glob(f"{task}-*-review.md"):
        match = REPORT_NAME.fullmatch(report.name)
        if match is None or match["task"] != task:
            continue
        try:
            if review_verdict(report) == "PASS":
                found.append((report.stat().st_mtime, match["sha"], report))
        except (OSError, UnicodeError):
            continue
    if not found:
        return None
    _, sha, report = max(found)
    return sha, report


def report_summary(task: str, report: Path) -> dict:
    """The ledger line of a report file: its role and SHA from the name, a reviewer's verdict from its first line."""
    match = REPORT_NAME.fullmatch(report.name)
    if match is None or match["task"] != task:
        role, sha = "unknown", None
    else:
        role, sha = ("reviewer" if match["review"] else "writer"), match["sha"]
    verdict = review_verdict(report) if role == "reviewer" else None
    return {"kind": "report", "task": task, "role": role, "sha": sha, "verdict": verdict, "file": report.name,
            "closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def is_writer_report(task: str, report: Path) -> bool:
    """A writer report (`<ID>-<sha>.md`) is deleted with no ledger line."""
    match = REPORT_NAME.fullmatch(report.name)
    return match is not None and match["task"] == task and not match["review"]


def record_report(repo: Path, task: str, report: Path) -> None:
    """Append the report's summary line to the repository's ``integrations.jsonl``; a writer report has none."""
    if is_writer_report(task, report):
        return
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    if not common:
        raise OSError(f"no git common directory for {repo}")
    path = Path(common).joinpath(*INTEGRATIONS)
    line = json.dumps(report_summary(task, report), sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def rehearse_report(repo: Path, task: str, report: Path) -> str:
    if is_writer_report(task, report):
        return f"DELETE {report} (writer report, no ledger line)"
    try:
        summary = report_summary(task, report)
    except (OSError, UnicodeError) as error:
        return f"REFUSED {report} (ledger: {error})"
    return f"DELETE {report} (ledger line: role {summary['role']}, verdict {summary['verdict'] or 'null'})"


def worktree_item(ctx: hw.Context, branch: str, task: str, cwd: str, check: bool, abandon: bool,
                  squash: str | None = None) -> str | None:
    """One item line for the task's worktree and branch; None when the task has none. A topic ``squash``
    integrated is removed as an abandoned one is, its tip kept as `archive/<ID>`."""
    archive = abandon or squash is not None
    kept_as = f", squash-integrated as {squash}" if squash else ""
    try:
        target, _ = hw.located(ctx, branch)
    except hw.Stop as stop:
        if stop.result.code == "NOT_FOUND":
            return None
        return f"REFUSED {stop.result.target} ({stop.result.code}: {stop.result.detail})"
    if _real(cwd).is_relative_to(_real(target)):
        return f"REFUSED {target} (the working directory is inside it; run it from elsewhere)"
    if check:
        try:
            refusal, _, _ = hw.assess(ctx, branch, archive, task)
        except hw.Stop as stop:
            refusal = stop.result
        if refusal is not None:
            return f"REFUSED {target} ({refusal.code}: {refusal.detail})"
        return f"DELETE {target} (branch {branch}{kept_as}{', archived as archive/' + task if archive else ''})"
    try:
        result = hw.remove(ctx, branch, archive, task)
    except hw.Stop as stop:
        result = stop.result
    if result.code != "OK":
        return f"REFUSED {target} ({result.code}: {result.detail})"
    kept = " (branch kept: " + result.evidence.get("git", "") + ")" if result.evidence.get("branch_kept") else ""
    return f"DELETED {target}{kept}"


def close(root: Path, environ: dict[str, str], cwd: str, task: str, *, check: bool = False, abandon: bool = False,
          verbose: bool = False, as_json: bool = False) -> int:
    ws = task_records.workspace(root, environ)
    project = task_records.project_of_task(ws, task)
    repo = Path(project.repo)
    branch, record = task_records.task_record(repo, task)
    squash = None
    if not abandon:
        entry = integrated(repo, task)
        squash = entry["candidate_sha"] if squash_integrated(repo, entry, branch) else None
    keep = keep_of(record, task)
    ctx = hw.context(root, environ, project.name)
    lines: list[str] = []
    worktree = None
    if branch is not None:
        found = hw.find(ctx, branch)
        worktree = Path(found["worktree"]) if found else None
        if "worktree" in keep:
            lines += [f"KEPT {worktree} (record keep)"] if worktree else []
        elif (line := worktree_item(ctx, branch, task, cwd, check, abandon, squash)) is not None:
            lines.append(line)
    tmp = locations.locate(environ).tmp
    scratches = task_scratches(tmp, task, worktree)
    if "scratches" in keep:
        lines += [f"KEPT {item} (record keep)" for item in scratches]
    elif scratches:
        said: list[str] = []
        scratch.clean(scratches, say=said.append, environ=environ, with_runs=True, check=check)
        lines += [line.replace("DELETED ", "DELETE ", 1) if check else line for line in said]
    named = [line.split(" ", 2)[1] for line in lines if " " in line]
    lines += [f"{CONSENT} {directory} (named for the task, not registered to it; deleted only on the Owner's word)"
              for directory in unregistered_scratches(tmp, task, scratches)
              if not any(locations.same_path(directory, path) for path in named)]
    for report in sorted((locations.atc_home(environ) / REPORTS).glob(f"{task}-*.md")):
        if "reports" in keep:
            lines.append(f"KEPT {report} (record keep)")
        elif check:
            lines.append(rehearse_report(repo, task, report))
        else:
            try:
                record_report(repo, task, report)
                report.unlink()
                lines.append(f"DELETED {report}")
            except (OSError, UnicodeError) as error:
                lines.append(f"REFUSED {report} ({error})")
    return report_close(task, lines, check, verbose, as_json, root)


def report_close(task: str, lines: list[str], check: bool, verbose: bool, as_json: bool, root: Path) -> int:
    counts = {word: sum(line.startswith(f"{word} ") for line in lines)
              for word in ("DELETED", "DELETE", "KEPT", "REFUSED", CONSENT)}
    deleted = counts["DELETE" if check else "DELETED"]
    refused = counts["REFUSED"]
    if not refused:
        code = cli_conventions.OK
    elif check or not counts["DELETED"]:
        code = cli_conventions.STOP
    else:
        code = cli_conventions.PARTIAL
    items = []
    for line in lines:
        outcome, _, rest = line.partition(" ")
        target, _, why = rest.partition(" (")
        items.append({"outcome": outcome, "path": target, "why": why[:-1] if why.endswith(")") else why or None})
    verdict = "REFUSED" if refused else ("CHECK" if check else "CLOSED")
    hint = (f"each REFUSED item names why; fix it and run atc task close {task}{' --check' if check else ''} again"
            if refused else None)
    if as_json:
        print(json.dumps({"verdict": verdict, "hint": hint, "task": task, "check": check, "items": items,
                          "counts": {k.lower(): v for k, v in counts.items()}}, indent=2))
        return code
    shown = [line for line in lines if verbose or line.startswith(("REFUSED ", f"{CONSENT} "))]
    word = "to delete" if check else "deleted"
    summary = f"{verdict} {task}: {deleted} {word}"
    if counts[CONSENT]:
        summary += f", {counts[CONSENT]} need the Owner's consent"
    if refused:
        print(f"STOP ITEM_REFUSED: {refused} refused, {deleted} {word}", file=sys.stderr)
        print(f"hint: {hint}", file=sys.stderr)
    print(summary)
    for line in shown:
        print("  " + _relative_line(line, root))
    return code


def _relative_line(line: str, root: Path) -> str:
    outcome, _, rest = line.partition(" ")
    target, sep, why = rest.partition(" (")
    return f"{outcome} {cli_conventions.relative(target, root)}{sep}{why}"

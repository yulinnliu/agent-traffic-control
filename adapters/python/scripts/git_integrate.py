#!/usr/bin/env python3
"""Candidate-first integration for a reviewed topic in the local main checkout.

The caller must establish integrate_main authority before invoking this tool
and supply the repository's required verification command. The command is run
against an isolated candidate checkout with ATC_INTEGRATION_SHA bound to its
immutable commit ID. The tool serializes cooperative ATC writers with an
Integration Slot and never blindly resets main after a failed update.

Usage:
  git_integrate.py --repo <main checkout> --branch <topic> --reviewed <40-hex sha>
      --authority-reference <record> --verify-command <JSON argv> [--main main] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
import tempfile
from dataclasses import asdict
from pathlib import Path

from git_preflight import GitError, git, merge_preflight, render, rev, same_path
from integration_slot import IntegrationSlot

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
    git_dir, common_dir = git(
        repo, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"
    ).stdout.splitlines()
    if not same_path(Path(git_dir), Path(common_dir)):
        raise Refused("--repo is a linked worktree; use the main checkout")


def _git_ok(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.strip()


def _remove_candidate(repo: Path, candidate_dir: Path) -> str | None:
    if not candidate_dir.exists():
        return None
    removed = git(repo, "worktree", "remove", str(candidate_dir), check=False)
    if removed.returncode:
        return f"candidate worktree retained at {candidate_dir}: {(removed.stderr or removed.stdout).strip()}"
    return None


def _verify_candidate(
    command: str, candidate_dir: Path, candidate_sha: str, expected_main: str, reviewed: str
) -> None:
    try:
        argv = json.loads(command)
    except json.JSONDecodeError as exc:
        raise Refused("--verify-command must be a JSON array of executable and arguments") from exc
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise Refused("--verify-command must be a non-empty JSON array of strings")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "ATC_INTEGRATION_SHA": candidate_sha,
        "ATC_CANDIDATE_DIR": str(candidate_dir),
        "ATC_EXPECTED_MAIN_SHA": expected_main,
        "ATC_REVIEWED_SOURCE_SHA": reviewed,
    })
    result = subprocess.run(argv, cwd=candidate_dir, env=env, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise Refused(
            f"candidate verification failed for {candidate_sha} (exit {result.returncode}): {detail}"
        )
    if rev(candidate_dir, "HEAD") != candidate_sha:
        raise Refused("verification command changed candidate HEAD; candidate SHA is no longer bound")
    if git(candidate_dir, "status", "--porcelain").stdout.strip():
        raise Refused("verification command dirtied candidate checkout; refusing integration")



def _recover_interrupted_update(repo: Path, main: str) -> tuple[int, dict] | None:
    """Reconcile a verified candidate already installed on main after a crash."""
    main_tip = rev(repo, main)
    if main_tip is None:
        return None
    refs = git(
        repo, "for-each-ref", "--format=%(refname) %(objectname)",
        "refs/atc/integration-candidates",
    ).stdout.splitlines()
    matching = []
    for line in refs:
        ref_name, _, sha = line.partition(" ")
        if sha == main_tip:
            matching.append(ref_name)
    if not matching:
        return None
    head = rev(repo, "HEAD")
    status = git(repo, "status", "--porcelain").stdout.strip()
    result = {
        "command": "integrate",
        "verdict": "RECOVERED",
        "reason": "CANDIDATE_ALREADY_ON_MAIN",
        "main_before": main_tip,
        "main_after": main_tip,
        "reviewed": None,
        "source_before": None,
        "candidate_sha": main_tip,
        "first_parent": None,
        "second_parent": None,
        "message": None,
        "verification": "PASS",
        "candidate_ref": matching[0],
        "recovery_status": "CONSISTENT",
    }
    if head != main_tip or status:
        result.update(
            recovery_status="REQUIRES_MANUAL_RECONCILIATION",
            error=f"candidate is main={main_tip}, but HEAD={head} and status={status!r}; retained recovery ref",
            recovery_required=True,
        )
        return 1, result
    for ref_name in matching:
        git(repo, "update-ref", "-d", ref_name, main_tip)
    result["candidate_ref"] = None
    return 0, result


def integrate(
    repo: Path, branch: str, reviewed: str, main: str = "main", verify_command: str | None = None,
    authority_reference: str | None = None,
) -> tuple[int, dict]:
    if not authority_reference or not authority_reference.strip():
        raise Refused("--authority-reference is required to record the integrate_main authority source")
    if not verify_command:
        raise Refused("--verify-command is required; main cannot advance without candidate verification")

    with IntegrationSlot(repo):
        recovered = _recover_interrupted_update(repo, main)
        if recovered is not None:
            return recovered
        report = merge_preflight(repo, branch, reviewed, main)
        main_before = report.evidence["main_tip"]
        source_before = report.evidence["topic_tip"]
        result = {
            "command": "integrate",
            "authority_reference": authority_reference,
            "verdict": report.verdict,
            "reason": report.reason,
            "main_before": main_before,
            "main_after": main_before,
            "reviewed": reviewed,
            "source_before": source_before,
            "candidate_sha": None,
            "first_parent": None,
            "second_parent": None,
            "message": None,
            "verification": "NOT_RUN",
            "candidate_ref": None,
        }
        if report.verdict == "STOP":
            return 1, {**result, "report": report}

        if rev(repo, main) != main_before or rev(repo, "HEAD") != main_before:
            return 1, {**result, "error": f"{main} moved after preflight; main unchanged"}

        candidate_root = Path(tempfile.gettempdir()) / "atc-integration-candidates"
        candidate_root.mkdir(parents=True, exist_ok=True)
        repo_id = str(abs(hash(str(repo.resolve()))))
        candidate_dir = candidate_root / f"{repo_id}-{uuid.uuid4().hex}"
        candidate_created = False
        candidate_ref: str | None = None
        integration_updated = False
        try:
            base = main_before if report.verdict == "NO_FF" else reviewed
            add = git(repo, "worktree", "add", "--detach", str(candidate_dir), base, check=False)
            if add.returncode:
                raise Refused(f"could not create isolated candidate worktree: {(add.stderr or add.stdout).strip()}")
            candidate_created = True

            if report.verdict == "NO_FF":
                result["message"] = f"merge({topic_name(branch)}): integrate reviewed changes"
                merged = git(
                    candidate_dir, "merge", "--no-ff", "--no-edit", "-m", result["message"], reviewed,
                    check=False,
                )
                if merged.returncode:
                    git(candidate_dir, "merge", "--abort", check=False)
                    raise Refused(f"candidate merge failed: {(merged.stderr or merged.stdout).strip()}")
            candidate_sha = rev(candidate_dir, "HEAD")
            if candidate_sha is None or not FULL_SHA.fullmatch(candidate_sha):
                raise Refused("candidate did not resolve to a full commit SHA")
            result["candidate_sha"] = candidate_sha
            if report.verdict == "NO_FF":
                first, second = _git_ok(candidate_dir, "rev-parse", f"{candidate_sha}^1", f"{candidate_sha}^2").split()
                result.update(first_parent=first, second_parent=second)
                if (first, second) != (main_before, reviewed):
                    raise Refused(f"candidate parent mismatch: {first} {second}; expected {main_before} {reviewed}")
            elif candidate_sha != reviewed:
                raise Refused(f"FF candidate is {candidate_sha}, expected reviewed SHA {reviewed}")

            _verify_candidate(verify_command, candidate_dir, candidate_sha, main_before, reviewed)
            result["verification"] = "PASS"

            # Final source and target checks are inside the cooperative slot.
            if rev(repo, branch) != source_before or source_before != reviewed:
                raise Refused("source tip changed after candidate verification; main unchanged")
            if rev(repo, main) != main_before or rev(repo, "HEAD") != main_before:
                raise Refused(f"{main} drifted after candidate verification; main unchanged")
            if git(repo, "status", "--porcelain").stdout.strip():
                raise Refused("integration checkout became dirty; main unchanged")

            candidate_ref = f"refs/atc/integration-candidates/{candidate_sha}"
            ref_create = git(repo, "update-ref", candidate_ref, candidate_sha, check=False)
            if ref_create.returncode:
                raise Refused(f"could not retain candidate recovery ref: {(ref_create.stderr or ref_create.stdout).strip()}")
            result["candidate_ref"] = candidate_ref

            update = git(repo, "merge", "--ff-only", candidate_sha, check=False)
            if update.returncode:
                raise Refused(
                    f"checkout-aware fast-forward rejected candidate; main was not reset: "
                    f"{(update.stderr or update.stdout).strip()}"
                )
            integration_updated = True
            main_after = rev(repo, main)
            result["main_after"] = main_after
            if main_after != candidate_sha or rev(repo, "HEAD") != candidate_sha:
                raise Refused(
                    f"recovery required: main={main_after}, HEAD={rev(repo, 'HEAD')}, "
                    f"candidate={candidate_sha}; retained {candidate_ref}"
                )
            status = git(repo, "status", "--porcelain").stdout.strip()
            if status:
                raise Refused(
                    f"recovery required: main advanced to {candidate_sha} but checkout is dirty "
                    f"({status}); retained {candidate_ref}"
                )
            if report.verdict == "FF":
                return 0, result
            return 0, result
        except Refused as exc:
            result["error"] = str(exc)
            if integration_updated:
                result["recovery_required"] = True
            return 1, result
        finally:
            cleanup_error = _remove_candidate(repo, candidate_dir) if candidate_created else None
            if cleanup_error:
                result["candidate_cleanup_warning"] = cleanup_error
            # Keep the immutable candidate ref when integration/verification
            # failed. On success main now retains the commit through its branch.
            if candidate_ref and result.get("verification") == "PASS" and result.get("main_after") == result.get("candidate_sha"):
                git(repo, "update-ref", "-d", candidate_ref, check=False)
                result["candidate_ref"] = None


def render_result(result: dict) -> str:
    line = f"{result['verdict']} main {result['main_before']} -> {result['main_after']}"
    if result.get("candidate_sha"):
        line += f" candidate {result['candidate_sha']} verification={result['verification']}"
    if result["verdict"] == "NO_FF":
        line += f" (parents {result['first_parent']} {result['second_parent']})"
    return line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True, help="main checkout of the project repository")
    parser.add_argument("--branch", required=True, help="reviewed topic branch")
    parser.add_argument("--reviewed", required=True, help="full 40-character SHA named by the review")
    parser.add_argument("--main", default="main", help="integration branch (default: main)")
    parser.add_argument("--authority-reference", required=True, help="reference to the existing record granting integrate_main authority")
    parser.add_argument("--verify-command", required=True, help="required verification argv as a JSON string array, run in the candidate checkout")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not FULL_SHA.fullmatch(args.reviewed):
            raise Refused(f"--reviewed must be a full 40-character lowercase SHA: {args.reviewed}")
        check_checkout(args.repo)
        code, result = integrate(args.repo, args.branch, args.reviewed, args.main, args.verify_command, args.authority_reference)
    except (Refused, GitError, RuntimeError) as exc:
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

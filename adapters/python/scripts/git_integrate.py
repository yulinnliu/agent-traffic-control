#!/usr/bin/env python3
"""Candidate-first integration for a reviewed topic in the local main checkout.

The caller must establish integrate_main authority before invoking this tool
and supply the repository's required verification command. The command is run
against an isolated candidate checkout with ATC_INTEGRATION_SHA bound to its
immutable commit ID. The tool serializes cooperative ATC writers with an
Integration Slot and never blindly resets main after a failed update.

Usage:
  git_integrate.py --repo <main checkout> --branch <topic> --reviewed <40-hex sha>
      --authority-reference <record>
      (--verify-command <JSON argv> | --verify-command-file <JSON argv file>)
      [--verify-timeout <seconds>] [--main main] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
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


class _ProcessTree:
    """Every process the verifier starts, tracked so all of them end together.

    Windows: a Job Object with KILL_ON_JOB_CLOSE, which follows processes
    through re-parenting. POSIX: the verifier's own session/process group.
    """

    FALLBACK_WARNING = (
        "verifier process tree is not isolated by a Job Object ({stage} failed); "
        "a descendant re-parented past the verifier may outlive the verification"
    )

    def __init__(self) -> None:
        self.job = None
        self.warning: str | None = None
        if sys.platform == "win32":
            self.job = _create_kill_on_close_job()
            if self.job is None:
                self.warning = self.FALLBACK_WARNING.format(stage="job creation")

    def popen_kwargs(self) -> dict:
        if sys.platform == "win32":
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    def adopt(self, process: subprocess.Popen) -> None:
        if self.job is None:
            return
        kernel32 = _kernel32()
        if not kernel32.AssignProcessToJobObject(self.job, int(process._handle)):
            kernel32.CloseHandle(self.job)
            self.job = None
            self.warning = self.FALLBACK_WARNING.format(stage="job assignment")

    def terminate(self, process: subprocess.Popen) -> None:
        if sys.platform == "win32":
            if self.job is not None:
                _kernel32().TerminateJobObject(self.job, 1)
            else:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False,
                )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        process.wait()

    def close(self) -> None:
        if self.job is not None:
            _kernel32().CloseHandle(self.job)
            self.job = None


_KERNEL32 = None


def _kernel32():
    """A private kernel32 binding with typed Job Object entry points."""
    global _KERNEL32
    if _KERNEL32 is None:
        import ctypes
        from ctypes import wintypes

        lib = ctypes.WinDLL("kernel32", use_last_error=True)
        lib.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        lib.CreateJobObjectW.restype = wintypes.HANDLE
        lib.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        lib.SetInformationJobObject.restype = wintypes.BOOL
        lib.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        lib.AssignProcessToJobObject.restype = wintypes.BOOL
        lib.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        lib.TerminateJobObject.restype = wintypes.BOOL
        lib.CloseHandle.argtypes = [wintypes.HANDLE]
        lib.CloseHandle.restype = wintypes.BOOL
        _KERNEL32 = lib
    return _KERNEL32


def _create_kill_on_close_job():
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = _kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    job_object_extended_limit_information = 9
    if not kernel32.SetInformationJobObject(
        job, job_object_extended_limit_information, ctypes.byref(info), ctypes.sizeof(info)
    ):
        kernel32.CloseHandle(job)
        return None
    return job


def _run_verifier(
    argv: list[str], cwd: Path, env: dict[str, str], timeout: float,
) -> tuple[int, str, str, str | None]:
    """Run the verifier with a bounded wall clock and end its whole process tree.

    Output goes to temporary files rather than pipes so a descendant that
    outlives the verifier cannot hold the wait open past the timeout. When the
    verifier returns or times out, every process it started is terminated.
    The last element names a weaker isolation the run had to fall back to.
    """
    tree = _ProcessTree()
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                process = subprocess.Popen(
                    argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    **tree.popen_kwargs(),
                )
            except OSError as exc:
                raise Refused(f"could not start candidate verifier: {exc}") from exc
            tree.adopt(process)
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                tree.terminate(process)
                raise Refused(f"candidate verification timed out after {timeout:g}s") from exc
            tree.terminate(process)
            out.seek(0)
            err.seek(0)
            return (
                returncode,
                out.read().decode("utf-8", errors="replace"),
                err.read().decode("utf-8", errors="replace"),
                tree.warning,
            )
    finally:
        tree.close()


def parse_verify_command(command: str) -> list[str]:
    try:
        argv = json.loads(command)
    except json.JSONDecodeError as exc:
        raise Refused("the verifier argv (--verify-command or --verify-command-file) must be a JSON array of executable and arguments") from exc
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise Refused("the verifier argv (--verify-command or --verify-command-file) must be a non-empty JSON array of strings")
    return argv


def _verify_candidate(
    argv: list[str], candidate_dir: Path, candidate_sha: str, expected_main: str, reviewed: str,
    timeout: float,
) -> str | None:
    """Run the verifier in the candidate; return the isolation warning, if any."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "ATC_INTEGRATION_SHA": candidate_sha,
        "ATC_CANDIDATE_DIR": str(candidate_dir),
        "ATC_EXPECTED_MAIN_SHA": expected_main,
        "ATC_REVIEWED_SOURCE_SHA": reviewed,
    })
    returncode, stdout, stderr, warning = _run_verifier(argv, candidate_dir, env, timeout)
    if returncode:
        detail = (stderr or stdout).strip()
        raise Refused(
            f"candidate verification failed for {candidate_sha} (exit {returncode}): {detail}"
        )
    if rev(candidate_dir, "HEAD") != candidate_sha:
        raise Refused("verification command changed candidate HEAD; candidate SHA is no longer bound")
    if git(candidate_dir, "status", "--porcelain").stdout.strip():
        raise Refused("verification command dirtied candidate checkout; refusing integration")
    return warning



def _recover_interrupted_update(
    repo: Path, main: str, branch: str, reviewed: str, authority_reference: str,
    recovered_candidates: list[dict],
) -> tuple[int, dict] | None:
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

    # A recovery ref is evidence of a retained candidate, not proof that this
    # invocation requested that candidate. Never report another request as ours.
    reviewed_tip = rev(repo, reviewed)
    branch_tip = rev(repo, branch)
    parents = git(repo, "rev-list", "--parents", "-n", "1", main_tip).stdout.split()
    candidate_matches_request = (
        reviewed_tip == reviewed
        and branch_tip == reviewed
        and (main_tip == reviewed or (len(parents) == 3 and parents[2] == reviewed))
    )
    head = rev(repo, "HEAD")
    status = git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    if not candidate_matches_request:
        # A ref retained because the checkout needs manual reconciliation is
        # evidence for that reconciliation; only a consistent checkout lets
        # another request release it.
        if head != main_tip or status:
            disposition = "main_retains_commit_ref_retained_checkout_inconsistent"
        else:
            removed = [
                git(repo, "update-ref", "-d", ref_name, main_tip, check=False).returncode == 0
                for ref_name in matching
            ]
            disposition = (
                "main_retains_commit_ref_removed_request_not_matched" if all(removed)
                else "main_retains_commit_ref_removal_failed_request_not_matched"
            )
        recovered_candidates.append({
            "candidate_sha": main_tip,
            "candidate_refs": matching,
            "disposition": disposition,
        })
        return None

    result = {
        "command": "integrate",
        "authority_reference": authority_reference,
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
    authority_reference: str | None = None, verify_timeout: float = 300.0,
) -> tuple[int, dict]:
    if not authority_reference or not authority_reference.strip():
        raise Refused("--authority-reference is required to record the integrate_main authority source")
    if not verify_command or not verify_command.strip():
        raise Refused("a verifier argv (--verify-command or --verify-command-file) is required; main cannot advance without candidate verification")
    verify_argv = parse_verify_command(verify_command)

    with IntegrationSlot(repo):
        main_tip_for_refs = rev(repo, main)
        ref_lines = git(
            repo, "for-each-ref", "--format=%(refname) %(objectname)",
            "refs/atc/integration-candidates",
        ).stdout.splitlines()
        stale_candidate_refs = []
        for line in ref_lines:
            name, _, sha = line.partition(" ")
            if sha != main_tip_for_refs:
                stale_candidate_refs.append({"ref": name, "sha": sha})
        recovered_candidates: list[dict] = []
        recovered = _recover_interrupted_update(
            repo, main, branch, reviewed, authority_reference or "", recovered_candidates
        )
        if recovered is not None:
            code, recovered_result = recovered
            if stale_candidate_refs:
                recovered_result["stale_candidate_refs"] = stale_candidate_refs
            return code, recovered_result
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
        if recovered_candidates:
            result["recovered_candidates"] = recovered_candidates
        if stale_candidate_refs:
            result["stale_candidate_refs"] = stale_candidate_refs
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

            verification_warning = _verify_candidate(
                verify_argv, candidate_dir, candidate_sha, main_before, reviewed, verify_timeout,
            )
            result["verification"] = "PASS"
            if verification_warning:
                result["verification_warning"] = verification_warning

            # Final source and target checks are inside the cooperative slot.
            if rev(repo, branch) != source_before or source_before != reviewed:
                raise Refused("source tip changed after candidate verification; main unchanged")
            if rev(repo, main) != main_before or rev(repo, "HEAD") != main_before:
                raise Refused(f"{main} drifted after candidate verification; main unchanged")
            if git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
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
            status = git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
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
            if (
                candidate_ref
                and not result.get("recovery_required")
                and result.get("verification") == "PASS"
                and result.get("main_after") == result.get("candidate_sha")
            ):
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
    verifier = parser.add_mutually_exclusive_group(required=True)
    verifier.add_argument("--verify-command", help="verification argv as a JSON string array, run in the candidate checkout")
    verifier.add_argument(
        "--verify-command-file", type=Path,
        help="file holding the verification argv as a JSON string array (shell-quoting safe)",
    )
    parser.add_argument("--verify-timeout", type=float, default=300.0, help="maximum verifier runtime in seconds (default: 300)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not FULL_SHA.fullmatch(args.reviewed):
            raise Refused(f"--reviewed must be a full 40-character lowercase SHA: {args.reviewed}")
        check_checkout(args.repo)
        if args.verify_timeout <= 0:
            raise Refused("--verify-timeout must be greater than zero")
        verify_command = args.verify_command
        if args.verify_command_file is not None:
            try:
                verify_command = args.verify_command_file.read_text(encoding="utf-8-sig")
            except OSError as exc:
                raise Refused(f"could not read --verify-command-file: {exc}") from exc
        code, result = integrate(
            args.repo, args.branch, args.reviewed, args.main, verify_command,
            args.authority_reference, args.verify_timeout,
        )
    except (Refused, GitError, RuntimeError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    report = result.pop("report", None)
    error = result.pop("error", None)
    if not args.json:
        for key in ("verification_warning", "candidate_cleanup_warning"):
            if result.get(key):
                print(f"WARNING {result[key]}", file=sys.stderr)
        for entry in result.get("recovered_candidates", []):
            print(
                f"NOTE recovery ref for candidate {entry['candidate_sha']}: {entry['disposition']}"
                f" ({', '.join(entry['candidate_refs'])})",
                file=sys.stderr,
            )
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

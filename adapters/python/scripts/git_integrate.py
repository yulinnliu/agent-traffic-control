#!/usr/bin/env python3
"""Candidate-first integration of a reviewed topic into the local main checkout.

Rules: governance `GIT_WORKFLOW.md` § Integrate, § Integration verifier source
and § Command results; options: `--help`.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from atc_testkit import locations
import git_preflight as gp
import hami_worktree as hw
import task_authority as ta
import task_section
import verification_section
from git_preflight import GitError, git, is_ancestor, merge_preflight, render, rev, same_path
from integration_slot import IntegrationSlot

FULL_SHA = re.compile(r"[0-9a-f]{40}")
CANDIDATE_ID_BYTES = 6
WORKTREE_BRANCH = re.compile(r"worktree-[^+]+\+(?P<topic>.+)")
# Formats: shared/conformance/verification-evidence/README.md
REPORT_SCHEMA = "atc-verification-report/1"
EVIDENCE_SCHEMA = "atc-verification-evidence/2"
EVIDENCE_DIR = ("atc", "verification-evidence")
EVIDENCE_RETENTION_SECONDS = 14 * 24 * 3600
INTEGRATIONS = ("atc", "integrations.jsonl")
CANDIDATE_REFS = "refs/atc/integration-candidates"
AUTHORITY_ENDING = re.compile(r"\s@\s+(\S+)$")
TAIL_LINES = 20
SHORT_SHA = 12
FULL_SHA_WORD = re.compile(r"\b([0-9a-f]{12})[0-9a-f]{28}\b")
RECORD_CHECK = "record check"
ALIAS_NOTICE = (
    "--authority-reference is deprecated; name the task with --task and the authority with --owner-words "
    "or the record's integrate_main on main"
)

STOP_BEFORE_CANDIDATE = 1
ERROR = 2
STOP_AFTER_CANDIDATE = 3

_RERUN = "run again; the preflight reassesses the table against the new refs"
_CORRECT = (
    "correct the cause on the topic branch with a new commit and re-review it, "
    "or correct the project's verifier, then run again"
)
# Stops outside the preflight table (`GIT_WORKFLOW.md` § Command results).
NEXT = {
    **gp.NEXT,
    "VERIFICATION_SECTION_MISSING": (
        "obtain the section from the owner, add it under GIT_WORKFLOW.md § Project verification section, "
        "then run again"
    ),
    "REF_DRIFT": _RERUN,
    "VERIFICATION_FAILED": _CORRECT,
    "VERIFICATION_TIMEOUT": _CORRECT,
    "CANDIDATE_CHANGED": _CORRECT,
    "RECOVERY_REQUIRED": (
        "reconcile main and its checkout with the retained candidate ref the detail names "
        "before any other integration of this repository"
    ),
    "CARRY_OVER_REFUSED": "the rebased tip needs a fresh review; integrate the SHA that review names",
    "TASK_NOT_FOUND": "commit the task's record on the topic branch and re-review, or name the task the record holds",
    "TASK_BRANCH_MISMATCH": "integrate the branch the record binds, or correct the record on the topic and re-review",
    "AUTHORITY_MISSING": "obtain the Owner's word and run again with --owner-words quoting it",
    "AUTHORITY_NOT_FF": (
        "a record's integrate_main covers FF only; obtain the Owner's word for this NO_FF and run again "
        "with --owner-words quoting it"
    ),
    "AUTHORITY_NOT_INTEGRATED": "obtain the Owner's word and run again with --owner-words quoting it",
    "TASK_AUTHORITY_CHANGED": (
        "move the authority change into a range that changes only .atc/tasks/ and integrate it with "
        "--owner-words; keep authority and authority_basis of records on main unchanged elsewhere"
    ),
    "TASK_ID_TAKEN": "take the next number with atc task new on the topic, re-review, then run again",
    "AUTHORITY_REFERENCE_REFUSED": "integrate a range that touches .atc/tasks/ with --task and --owner-words",
}


class Refused(Exception):
    """A refusal: `STOP <TOKEN>` with exit 1 or 3, or `ERROR <TOKEN>` with exit 2."""

    def __init__(self, token: str, detail: str, code: int = ERROR, extra: list[str] | None = None) -> None:
        super().__init__(detail)
        self.token, self.detail, self.code = token, detail, code
        self.next = NEXT[token] if code != ERROR else None
        self.extra = extra or []

    @property
    def kind(self) -> str:
        return "ERROR" if self.code == ERROR else "STOP"

    def lines(self) -> list[str]:
        if self.code == ERROR:
            return [f"ERROR {self.token} {self.detail}", *self.extra]
        return [f"STOP {self.token}: {self.detail}", f"next: {self.next}", *self.extra]

    def as_dict(self) -> dict:
        return {"verdict": self.kind, "reason": self.token, "detail": self.detail, "next": self.next}


def stop(token: str, detail: str, *, after_candidate: bool = False, extra: list[str] | None = None) -> Refused:
    return Refused(token, detail, STOP_AFTER_CANDIDATE if after_candidate else STOP_BEFORE_CANDIDATE, extra)


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
        raise Refused("REPO_NOT_TOP", f"--repo must be the top of the checkout: {top}")
    git_dir, common_dir = git(
        repo, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"
    ).stdout.splitlines()
    if not same_path(Path(git_dir), Path(common_dir)):
        raise Refused("REPO_LINKED_WORKTREE", "--repo is a linked worktree; use the main checkout")


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
    argv: list[str], cwd: Path, env: dict[str, str], timeout: float, captures: Path,
) -> tuple[int | None, str, str, str | None]:
    """Run the verifier with a bounded wall clock and end its whole process tree.

    Output goes to temporary files in ``captures`` rather than pipes so a descendant that
    outlives the verifier cannot hold the wait open past the timeout. When the
    verifier returns or times out, every process it started is terminated.
    Returns the exit code (``None`` on timeout), both streams, and the weaker
    isolation the run had to fall back to, if any.
    """
    tree = _ProcessTree()
    try:
        with tempfile.TemporaryFile(dir=captures) as out, tempfile.TemporaryFile(dir=captures) as err:
            try:
                process = subprocess.Popen(
                    argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    **tree.popen_kwargs(),
                )
            except OSError as exc:
                raise Refused("VERIFIER_NOT_STARTED", f"could not start candidate verifier: {exc}") from exc
            tree.adopt(process)
            try:
                returncode: int | None = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                returncode = None
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


def _tails(stdout: str, stderr: str) -> list[str]:
    """The last lines of both verifier streams, for the lines after a refusal."""
    lines = []
    for name, text in (("stdout", stdout), ("stderr", stderr)):
        tail = text.rstrip().splitlines()[-TAIL_LINES:]
        if tail:
            lines += [f"verifier {name} (last {len(tail)} lines):", *(f"  {line}" for line in tail)]
    return lines


def parse_verify_command(command: str) -> list[str]:
    try:
        argv = json.loads(command)
    except json.JSONDecodeError as exc:
        raise Refused("VERIFIER_ARGV_INVALID", "the verifier argv (--verify-command or --verify-command-file) must be a JSON array of executable and arguments") from exc
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise Refused("VERIFIER_ARGV_INVALID", "the verifier argv (--verify-command or --verify-command-file) must be a non-empty JSON array of strings")
    return argv


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _valid_report(report: object) -> bool:
    if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
        return False
    selector, selection, stages, inputs = (
        report.get(key) for key in ("selector", "selection", "stages", "dependency_inputs")
    )
    return (
        isinstance(selector, dict)
        and isinstance(selector.get("name"), str) and isinstance(selector.get("version"), str)
        and isinstance(selection, dict)
        and isinstance(selection.get("full"), bool) and _strings(selection.get("paths"))
        and isinstance(stages, list)
        and all(
            isinstance(stage, dict) and isinstance(stage.get("name"), str)
            and isinstance(stage.get("marker"), str) and _strings(stage.get("ignore"))
            for stage in stages
        )
        and isinstance(inputs, dict)
        and all(isinstance(key, str) and isinstance(value, str) for key, value in inputs.items())
    )


def _read_report(path: Path) -> tuple[dict | None, str | None]:
    """The verifier's report, or the reason no evidence record can be written."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, "NO_REPORT"
    except OSError:
        return None, "INVALID_REPORT"
    try:
        report = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "INVALID_REPORT"
    return (report, None) if _valid_report(report) else (None, "INVALID_REPORT")


def _write_evidence(repo: Path, record: dict) -> Path:
    """Store the record under the Git common directory and drop expired ones."""
    common = Path(_git_ok(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    directory = common.joinpath(*EVIDENCE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{record['candidate_sha']}.json"
    partial = directory / f".{record['candidate_sha']}.{uuid.uuid4().hex}.tmp"
    partial.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, target)
    cutoff = time.time() - EVIDENCE_RETENTION_SECONDS
    for old in directory.glob("*.json"):
        try:
            if old != target and old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    return target



def _verify_candidate(
    argv: list[str], candidate_dir: Path, candidate_sha: str, expected_main: str, reviewed: str,
    timeout: float, captures: Path, report_path: Path,
) -> str | None:
    """Run the verifier in the candidate; return the isolation warning, if any."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "ATC_INTEGRATION_SHA": candidate_sha,
        "ATC_CANDIDATE_DIR": str(candidate_dir),
        "ATC_EXPECTED_MAIN_SHA": expected_main,
        "ATC_REVIEWED_SOURCE_SHA": reviewed,
        "ATC_VERIFICATION_REPORT": str(report_path),
    })
    returncode, stdout, stderr, warning = _run_verifier(argv, candidate_dir, env, timeout, captures)
    if returncode is None:
        raise stop(
            "VERIFICATION_TIMEOUT", f"candidate verification timed out after {timeout:g}s for {candidate_sha}",
            after_candidate=True, extra=_tails(stdout, stderr),
        )
    if returncode:
        last = (stderr.strip() or stdout.strip()).splitlines()
        raise stop(
            "VERIFICATION_FAILED",
            f"candidate verification failed for {candidate_sha} (exit {returncode}): {last[-1] if last else 'no output'}",
            after_candidate=True, extra=_tails(stdout, stderr),
        )
    if rev(candidate_dir, "HEAD") != candidate_sha:
        raise stop("CANDIDATE_CHANGED", "verification command changed candidate HEAD; candidate SHA is no longer bound", after_candidate=True)
    if git(candidate_dir, "status", "--porcelain").stdout.strip():
        raise stop("CANDIDATE_CHANGED", "verification command dirtied candidate checkout; refusing integration", after_candidate=True)
    return warning


def _record_integration(repo: Path, records: list[dict]) -> Path:
    """Append one integration, a line per task, to `<git common dir>/atc/integrations.jsonl`."""
    common = Path(_git_ok(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    path = common.joinpath(*INTEGRATIONS)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        try:
            if json.loads(line).get("candidate_sha") == records[0]["candidate_sha"]:
                return path  # a run that stopped for recovery already recorded it
        except (json.JSONDecodeError, AttributeError):
            continue
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(json.dumps(record, sort_keys=True) + "\n" for record in records))
    return path


@dataclass
class Binding:
    """The tasks an integration names, its authority, and whether its range changes only task records."""

    tasks: list[str]
    authority: dict
    records_only: bool = False


def _binding_keys(binding: Binding) -> dict:
    return {
        "task": binding.tasks[0] if len(binding.tasks) == 1 else None,
        "tasks": binding.tasks,
        "authority": binding.authority,
        "records_only": binding.records_only,
    }


def _note_integration(
    repo: Path, result: dict, candidate_sha: str, reviewed: str, binding: Binding,
    carried_from: str | None = None, main_before: str | None = None,
) -> None:
    """One line per task; `main_before` is present on every line, `None` only after a recovery."""
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        result["integration_record"] = str(_record_integration(repo, [{
            "candidate_sha": candidate_sha,
            "reviewed": reviewed,
            "main_before": main_before,
            **({"carried_over_from": carried_from} if carried_from else {}),
            "task": task,
            "authority": binding.authority,
            "records_only": binding.records_only,
            "integrated_at": at,
        } for task in binding.tasks]))
    except (OSError, GitError) as exc:
        result["integration_record"] = None
        result["integration_record_warning"] = f"could not append the integration record: {exc}"


def _local_branches(repo: Path, main: str, *selector: str) -> list[str]:
    names = git(repo, "for-each-ref", "--format=%(refname:short)", *selector, "refs/heads/").stdout.split()
    return sorted(name for name in names if name != main)


def _recovery_ref_at(repo: Path, sha: str) -> bool:
    return any(
        line.split(" ", 1)[-1] == sha
        for line in git(repo, "for-each-ref", "--format=%(refname) %(objectname)", CANDIDATE_REFS).stdout.splitlines()
    )


def _recover_interrupted_update(
    repo: Path, main: str, branch: str | None, reviewed: str, binding: Binding,
    recovered_candidates: list[dict], *, check: bool = False,
) -> tuple[int, dict] | None:
    """Reconcile a verified candidate already installed on main after a crash.

    With ``check`` it only reports what the run would do and changes nothing.
    """
    main_tip = rev(repo, main)
    if main_tip is None:
        return None
    refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", CANDIDATE_REFS).stdout.splitlines()
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
    branch_at_reviewed = rev(repo, branch) == reviewed if branch else bool(_local_branches(repo, main, "--points-at", reviewed))
    parents = git(repo, "rev-list", "--parents", "-n", "1", main_tip).stdout.split()
    candidate_matches_request = (
        reviewed_tip == reviewed
        and branch_at_reviewed
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
        elif check:
            disposition = "main_retains_commit_ref_would_be_removed_request_not_matched"
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
        **_binding_keys(binding),
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
        "verification_seconds": None,
        "evidence": None,
        "evidence_reason": "RECOVERED",
        "candidate_ref": matching[0],
        "recovery_status": "CONSISTENT",
    }
    if head != main_tip or status:
        result.update(
            recovery_status="REQUIRES_MANUAL_RECONCILIATION",
            recovery_required=True,
            refusal=stop(
                "RECOVERY_REQUIRED",
                f"candidate is main={main_tip}, but HEAD={head} and status={status!r}; retained {matching[0]}",
                after_candidate=True,
            ),
        )
        return STOP_AFTER_CANDIDATE, result
    if check:
        return 0, result
    for ref_name in matching:
        git(repo, "update-ref", "-d", ref_name, main_tip)
    result["candidate_ref"] = None
    _note_integration(repo, result, main_tip, reviewed, binding)
    return 0, result


def _patch_ids(repo: Path, commits: list[str]) -> list[str]:
    """`git patch-id --stable` of each commit, in order; an empty change has the empty ID."""
    if not commits:
        return []
    patches = git(repo, "diff-tree", "--stdin", "-p", "--no-color", input="\n".join(commits) + "\n").stdout
    ids = {}
    for line in git(repo, "patch-id", "--stable", input=patches).stdout.splitlines():
        patch_id, _, sha = line.partition(" ")
        ids[sha] = patch_id
    return [ids.get(sha, "") for sha in commits]


def _commits(repo: Path, range_spec: str) -> tuple[list[str], int]:
    """The commits of the range oldest first, and how many of them are merges."""
    listed = git(repo, "rev-list", "--reverse", range_spec).stdout.split()
    merges = len(git(repo, "rev-list", "--merges", range_spec).stdout.split())
    return listed, merges


def carry_over(repo: Path, main: str, approved: str, rebased: str, main_tip: str) -> dict:
    """Prove ``rebased`` is a clean rebase of the unpublished ``approved`` onto ``main_tip``.

    Same commit count and order, every `git patch-id --stable` equal, and a
    conflict-free replay of the approved commits onto ``main_tip`` that yields
    the rebased tree. Writes only unreferenced objects; any failure is
    `CARRY_OVER_REFUSED`.
    """
    def refuse(detail: str) -> Refused:
        return stop("CARRY_OVER_REFUSED", f"{detail}; the approval for {approved} does not carry over to {rebased}")

    published = gp.published_refs(repo, approved)
    if published:
        raise refuse(f"the reviewed SHA is published at {', '.join(published)}; a published topic integrates as NO_FF")
    if not is_ancestor(repo, main_tip, rebased):
        raise refuse(f"the rebased tip is not based on {main} {main_tip}; rebase onto the current {main}")
    old_base = _git_ok(repo, "merge-base", approved, main_tip)
    old, old_merges = _commits(repo, f"{old_base}..{approved}")
    new, new_merges = _commits(repo, f"{main_tip}..{rebased}")
    if old_merges or new_merges:
        raise refuse(f"the reviewed range holds {old_merges} and the rebased range {new_merges} merge commits")
    if len(old) != len(new):
        raise refuse(f"the reviewed range {old_base}..{approved} has {len(old)} commits, the rebased range {main_tip}..{rebased} {len(new)}")
    for index, (a, b, id_a, id_b) in enumerate(zip(old, new, _patch_ids(repo, old), _patch_ids(repo, new)), 1):
        if not id_a or id_a != id_b:
            raise refuse(f"commit {index}: patch-id of {b} ({id_b or 'empty'}) differs from {a} ({id_a or 'empty'})")
    replay = main_tip
    for sha in old:
        merged = git(
            repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", f"--merge-base={sha}^", replay, sha,
            check=False,
        )
        if merged.returncode == 1:
            files = ", ".join(line for line in merged.stdout.splitlines()[1:] if line) or "-"
            raise refuse(f"replaying {sha} onto {main} conflicts in {files}")
        if merged.returncode:
            raise GitError(f"git merge-tree: {merged.stderr.strip() or merged.returncode}")
        replay = _git_ok(repo, "commit-tree", merged.stdout.splitlines()[0], "-p", replay, "-m", f"replay {sha}")
    if _git_ok(repo, "rev-parse", f"{replay}^{{tree}}") != _git_ok(repo, "rev-parse", f"{rebased}^{{tree}}"):
        raise refuse(f"the clean replay of {old_base}..{approved} onto {main_tip} differs from the rebased tree")
    between = git(repo, "log", "--reverse", "--format=%H %s", f"{old_base}..{main_tip}").stdout.splitlines()
    return {"carried_over_from": approved, "rebased": rebased, "commits": len(old), "main_between": between}


def render_carry_over(carried: dict) -> list[str]:
    return [
        f"carried over: the approval for reviewed {carried['carried_over_from']} applies to rebased {carried['rebased']} "
        f"({carried['commits']} commits, every patch-id equal, clean replay)",
        f"main commits between: {len(carried['main_between'])}",
        *(f"  {line}" for line in carried["main_between"]),
    ]


def render_binding(binding: Binding) -> str:
    """The tasks and the authority by kind; the quote stays in `--json` and the records."""
    tasks = ",".join(binding.tasks)
    records_only = " records-only" if binding.records_only else ""
    return f"task={json.dumps(tasks, ensure_ascii=False)} authority={binding.authority['kind']}{records_only}"


def authority_verdict_stop(binding: Binding, verdict: str) -> Refused | None:
    """A record's `integrate_main` covers `FF` only."""
    if binding.authority.get("kind") == "record" and verdict == "NO_FF":
        return stop("AUTHORITY_NOT_FF", f"the record of {binding.authority['task_id']} on main grants integrate_main, "
                                        "which covers FF only; this integration is NO_FF")
    return None


def _check_records(candidate_dir: Path, candidate_sha: str) -> None:
    """The record check in place of the verifier, for a range that changes only `.atc/tasks/`."""
    import task_records
    problems = task_records.problems(candidate_dir)
    if problems:
        raise stop(
            "VERIFICATION_FAILED", f"the record check failed for {candidate_sha}: {problems[0]}",
            after_candidate=True, extra=[f"  {problem}" for problem in problems],
        )
    if git(candidate_dir, "status", "--porcelain").stdout.strip():
        raise stop("CANDIDATE_CHANGED", "the record check dirtied the candidate checkout; refusing integration", after_candidate=True)


@dataclass
class Target:
    """Every value an integration runs with, resolved before anything runs.

    ``reviewed`` is the source integrated; with ``--rebased`` it is the rebased
    tip and ``carried_from`` the reviewed SHA whose approval carries over.
    """

    repo: Path
    main: str
    main_tip: str
    branch: str | None
    branch_source: str
    reviewed: str
    binding: Binding
    verify_argv: list[str]
    argv_source: str
    timeout: float
    timeout_source: str
    warnings: list[str] = field(default_factory=list)
    carried_from: str | None = None
    fetched: str = "none"
    project: str = ""

    def line(self) -> str:
        carried = f" carried_from={self.carried_from}" if self.carried_from else ""
        verifier = (
            RECORD_CHECK if self.binding.records_only
            else f"{json.dumps(self.verify_argv, ensure_ascii=False)} ({self.argv_source}) "
                 f"timeout={self.timeout:g}s ({self.timeout_source})"
        )
        return (
            f"resolved: {self.project} main={self.main}@{self.main_tip} fetched={self.fetched} "
            f"branch={self.branch or '-'} ({self.branch_source}) reviewed={self.reviewed}{carried} "
            f"{render_binding(self.binding)} verifier={verifier}"
        )


def integrate(t: Target) -> tuple[int, dict]:
    repo, main, branch, reviewed = t.repo, t.main, t.branch, t.reviewed
    with IntegrationSlot(repo):
        main_tip_for_refs = rev(repo, main)
        stale_candidate_refs = []
        for line in git(repo, "for-each-ref", "--format=%(refname) %(objectname)", CANDIDATE_REFS).stdout.splitlines():
            name, _, sha = line.partition(" ")
            if sha != main_tip_for_refs:
                stale_candidate_refs.append({"ref": name, "sha": sha})
        recovered_candidates: list[dict] = []
        recovered = _recover_interrupted_update(
            repo, main, branch, reviewed, t.binding, recovered_candidates
        )
        if recovered is not None:
            code, recovered_result = recovered
            if stale_candidate_refs:
                recovered_result["stale_candidate_refs"] = stale_candidate_refs
            return code, recovered_result
        if branch is None:
            raise stop("ALREADY_MERGED", f"the reviewed SHA {reviewed} is already reachable from {main}; nothing to integrate")
        report = merge_preflight(repo, branch, reviewed, main)
        main_before = report.evidence["main_tip"]
        source_before = report.evidence["topic_tip"]
        result = {
            "command": "integrate",
            **_binding_keys(t.binding),
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
            "verification_seconds": None,
            "evidence": None,
            "evidence_reason": None,
            "candidate_ref": None,
        }
        if recovered_candidates:
            result["recovered_candidates"] = recovered_candidates
        if stale_candidate_refs:
            result["stale_candidate_refs"] = stale_candidate_refs
        if report.verdict == "STOP":
            return STOP_BEFORE_CANDIDATE, {**result, "report": report}
        if refusal := authority_verdict_stop(t.binding, report.verdict):
            return STOP_BEFORE_CANDIDATE, {**result, "refusal": refusal}
        if t.carried_from:
            try:
                result["carried_over"] = carry_over(repo, main, t.carried_from, reviewed, main_before)
            except Refused as exc:
                return exc.code, {**result, "refusal": exc}

        if main_before != t.main_tip:
            return STOP_BEFORE_CANDIDATE, {**result, "refusal": stop(
                "REF_DRIFT", f"{main} moved from {t.main_tip}, whose verification section was read, to {main_before}; main unchanged",
            )}
        if rev(repo, main) != main_before or rev(repo, "HEAD") != main_before:
            return STOP_BEFORE_CANDIDATE, {**result, "refusal": stop("REF_DRIFT", f"{main} moved after preflight; main unchanged")}

        places = locations.locate()
        candidate_root = places.tmp / locations.CANDIDATES
        try:
            candidate_root.mkdir(exist_ok=True)
        except OSError as exc:
            raise locations.Refusal(locations.UNSUITABLE, f"cannot be created: {candidate_root}: {exc}") from exc
        candidate_dir = candidate_root / secrets.token_hex(CANDIDATE_ID_BYTES)
        report_path = candidate_dir.with_name(candidate_dir.name + ".report.json")
        candidate_created = False
        candidate_ref: str | None = None
        integration_updated = False
        try:
            base = main_before if report.verdict == "NO_FF" else reviewed
            add = git(repo, "worktree", "add", "--detach", str(candidate_dir), base, check=False)
            if add.returncode:
                raise Refused("CANDIDATE_NOT_CREATED", f"could not create isolated candidate worktree: {(add.stderr or add.stdout).strip()}")
            candidate_created = True

            if report.verdict == "NO_FF":
                result["message"] = f"merge({topic_name(branch)}): integrate reviewed changes"
                merged = git(
                    candidate_dir, "merge", "--no-ff", "--no-edit", "-m", result["message"], reviewed,
                    check=False,
                )
                if merged.returncode:
                    git(candidate_dir, "merge", "--abort", check=False)
                    raise Refused("CANDIDATE_MERGE_FAILED", f"candidate merge failed: {(merged.stderr or merged.stdout).strip()}")
            candidate_sha = rev(candidate_dir, "HEAD")
            if candidate_sha is None or not FULL_SHA.fullmatch(candidate_sha):
                raise Refused("CANDIDATE_NOT_CREATED", "candidate did not resolve to a full commit SHA")
            result["candidate_sha"] = candidate_sha
            if report.verdict == "NO_FF":
                first, second = _git_ok(candidate_dir, "rev-parse", f"{candidate_sha}^1", f"{candidate_sha}^2").split()
                result.update(first_parent=first, second_parent=second)
                if (first, second) != (main_before, reviewed):
                    raise stop(
                        "CANDIDATE_CHANGED", f"candidate parent mismatch: {first} {second}; expected {main_before} {reviewed}",
                        after_candidate=True,
                    )
            elif candidate_sha != reviewed:
                raise stop("CANDIDATE_CHANGED", f"FF candidate is {candidate_sha}, expected reviewed SHA {reviewed}", after_candidate=True)

            verification_started = time.monotonic()
            if t.binding.records_only:
                print(f"verify: candidate {candidate_sha} {RECORD_CHECK}", file=sys.stderr)
                _check_records(candidate_dir, candidate_sha)
                verification_warning, verifier_report, evidence_reason = None, None, "RECORDS_ONLY"
            else:
                print(f"verify: candidate {candidate_sha} timeout {t.timeout:g}s ({t.timeout_source})", file=sys.stderr)
                verification_warning = _verify_candidate(
                    t.verify_argv, candidate_dir, candidate_sha, main_before, reviewed, t.timeout, places.systmp,
                    report_path,
                )
                verifier_report, evidence_reason = _read_report(report_path)
            result["verification"] = "PASS"
            result["verification_seconds"] = round(time.monotonic() - verification_started, 1)
            result["evidence_reason"] = evidence_reason
            if verification_warning:
                result["verification_warning"] = verification_warning

            # Final source and target checks are inside the cooperative slot.
            if rev(repo, branch) != source_before or source_before != reviewed:
                raise stop("REF_DRIFT", "source tip changed after candidate verification; main unchanged", after_candidate=True)
            if rev(repo, main) != main_before or rev(repo, "HEAD") != main_before:
                raise stop("REF_DRIFT", f"{main} drifted after candidate verification; main unchanged", after_candidate=True)
            if git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
                raise stop("REF_DRIFT", "integration checkout became dirty; main unchanged", after_candidate=True)

            candidate_ref = f"{CANDIDATE_REFS}/{candidate_sha}"
            ref_create = git(repo, "update-ref", candidate_ref, candidate_sha, check=False)
            if ref_create.returncode:
                raise Refused("CANDIDATE_REF_FAILED", f"could not retain candidate recovery ref: {(ref_create.stderr or ref_create.stdout).strip()}")
            result["candidate_ref"] = candidate_ref

            update = git(repo, "merge", "--ff-only", candidate_sha, check=False)
            if update.returncode:
                raise stop(
                    "REF_DRIFT",
                    f"checkout-aware fast-forward rejected candidate; main was not reset: {(update.stderr or update.stdout).strip()}",
                    after_candidate=True,
                )
            integration_updated = True
            main_after = rev(repo, main)
            result["main_after"] = main_after
            if main_after == candidate_sha:
                # main holds the candidate, so the integration happened even
                # when its checkout needs recovery below.
                _note_integration(repo, result, candidate_sha, reviewed, t.binding, t.carried_from, main_before)
            if main_after != candidate_sha or rev(repo, "HEAD") != candidate_sha:
                raise stop(
                    "RECOVERY_REQUIRED",
                    f"main={main_after}, HEAD={rev(repo, 'HEAD')}, candidate={candidate_sha}; retained {candidate_ref}",
                    after_candidate=True,
                )
            status = git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
            if status:
                raise stop(
                    "RECOVERY_REQUIRED",
                    f"main advanced to {candidate_sha} but checkout is dirty ({status}); retained {candidate_ref}",
                    after_candidate=True,
                )
            if verifier_report is not None:
                try:
                    result["evidence"] = str(_write_evidence(repo, {
                        "schema": EVIDENCE_SCHEMA,
                        "candidate_sha": candidate_sha,
                        "tree": _git_ok(repo, "rev-parse", f"{candidate_sha}^{{tree}}"),
                        "clean": True,
                        "expected_main": main_before,
                        "reviewed": reviewed,
                        "verdict": result["verdict"],
                        **({"carried_over_from": t.carried_from} if t.carried_from else {}),
                        "task": t.binding.tasks[0],
                        "authority": t.binding.authority,
                        "verify_argv": t.verify_argv,
                        "platform": {"system": sys.platform, "machine": platform.machine()},
                        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "duration_seconds": result["verification_seconds"],
                        "report": verifier_report,
                    }))
                except (OSError, GitError):
                    result["evidence_reason"] = "WRITE_FAILED"
            try:
                own = set(git(repo, "rev-list", f"{main_before}..{candidate_sha}").stdout.split())
                result["push"] = gp.push_listing(repo, main, own)
            except GitError as exc:
                result["push_warning"] = f"could not list origin/{main}..{main}: {exc}"
            return 0, result
        except Refused as exc:
            result["refusal"] = exc
            if integration_updated:
                result["recovery_required"] = True
            return exc.code, result
        finally:
            report_path.unlink(missing_ok=True)
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


def _project_name(repo: Path, root: Path) -> str:
    return hw.validator.ROOT_TOPICS_NAME if same_path(repo, root) else repo.name


def declared_prefix(repo: Path, main: str = "main") -> str | None:
    """The task prefix `AGENTS.md` on ``main`` declares, if any."""
    shown = git(repo, "show", f"refs/heads/{main}:AGENTS.md", check=False)
    return task_section.prefix(shown.stdout) or None if shown.returncode == 0 else None


def resolve_repo(args: argparse.Namespace, root: Path, root_given: bool, cwd: str) -> tuple[str, Path]:
    """The project name and main checkout: `--repo`, `--project` through workspace.yaml (`root` names the
    workspace root), the repository whose declared prefix a record `--task` carries, or the project holding ``cwd``."""
    if args.repo is not None:
        if root_given:
            raise Refused(
                "ROOT_WITH_REPO",
                "--root names the workspace that --project or the working directory resolves in; "
                "--repo names the checkout itself, so give --root or --repo, not both",
            )
        return _project_name(args.repo, root), args.repo
    try:
        ws = hw.load_workspace(root, dict(os.environ))
    except hw.Stop as exc:
        raise Refused(exc.result.code, f"{exc.result.target}: {exc.result.detail}") from exc
    registered = f"registered: {', '.join(sorted(ws.projects)) or 'none'}"
    root_project = hw.root_project(ws)
    if args.project is not None:
        project = root_project if args.project == root_project.name else ws.projects.get(args.project)
        if project is None:
            raise Refused("UNKNOWN_PROJECT", f"{args.project} is not in {root / 'workspace.yaml'}; {registered}")
        return project.name, Path(project.repo)
    record = ta.RECORD_ID.fullmatch(args.task or "")
    if record:
        declaring = [p for p in [root_project, *ws.projects.values()]
                     if Path(p.repo).is_dir() and declared_prefix(Path(p.repo)) == record["prefix"]]
        if len(declaring) == 1:
            return declaring[0].name, Path(declaring[0].repo)
    if args.task is not None:
        raise Refused(
            "USAGE",
            f"--task {args.task} names no record of a declared task prefix; a task identity string needs "
            "--project or --repo",
        )
    project = hw.infer_project(ws, cwd)
    if project is None:
        raise Refused(
            "UNKNOWN_PROJECT",
            f"no --repo or --project and the working directory {cwd} is in no registered project; "
            f"{registered}; the workspace root takes --repo or --project root",
        )
    return project.name, Path(project.repo)


def infer_branch(repo: Path, reviewed: str, main: str, main_tip: str) -> tuple[str | None, str]:
    """The branch to integrate from the reviewed SHA, or the stop of `GIT_WORKFLOW.md` § Command results."""
    if is_ancestor(repo, reviewed, main_tip):
        if _recovery_ref_at(repo, main_tip):
            # A crash after the update left the candidate on main; the slot's
            # recovery decides whether it is this request's.
            at = _local_branches(repo, main, "--points-at", reviewed)
            return (at[0] if len(at) == 1 else None), "recovery"
        raise stop("ALREADY_MERGED", f"the reviewed SHA {reviewed} is already reachable from {main}; nothing to integrate")
    at = _local_branches(repo, main, "--points-at", reviewed)
    if len(at) == 1:
        return at[0], "inferred"
    if at:
        raise stop("BRANCH_AMBIGUOUS", f"{gp.REASONS['BRANCH_AMBIGUOUS']} {reviewed}: {', '.join(at)}")
    containing = _local_branches(repo, main, "--contains", reviewed)
    if containing:
        raise stop(
            "UNREVIEWED_COMMITS",
            f"no local branch is at the reviewed SHA {reviewed}; these have commits after it: {', '.join(containing)}",
        )
    raise stop("NO_BRANCH_AT_REVIEWED", f"{gp.REASONS['NO_BRANCH_AT_REVIEWED']}: {reviewed}")


def resolve_authority(given: str, reviewed: str) -> tuple[str, str]:
    """The reference ending in ` @ <reviewed>`, and its record part (§ Authority reference)."""
    record = given.strip()
    match = AUTHORITY_ENDING.search(record)
    if match:
        if match[1].lower() != reviewed:
            raise Refused(
                "AUTHORITY_SHA_MISMATCH",
                f"the authority reference ends with @ {match[1]}, but --reviewed is {reviewed}",
            )
        record = record[:match.start()].rstrip()
    if not record:
        raise Refused("AUTHORITY_REFERENCE_MISSING", "--authority-reference is required to record the integrate_main authority source")
    return f"{record} @ {reviewed}", record


def authority_record_warning(repo: Path, record: str, reviewed: str) -> str | None:
    """A warning when the record part names a file the reviewed commit does not hold."""
    words = record.split()
    path = words[words.index("per") + 1] if "per" in words[:-1] else words[0]
    if "/" not in path and not re.search(r"\.[A-Za-z0-9]+$", path):
        return None
    if git(repo, "cat-file", "-e", f"{reviewed}:{path}", check=False).returncode == 0:
        return None
    return f"the authority record {path} is not in the reviewed commit {reviewed}"


def declared_verifier(repo: Path, main: str, main_tip: str) -> tuple[verification_section.Declaration, list[str] | None, str | None]:
    """The main revision's declaration, its argv, and why that argv is unusable, if it is."""
    shown = git(repo, "show", f"{main_tip}:AGENTS.md", check=False)
    declared = verification_section.declaration(shown.stdout) if shown.returncode == 0 else None
    where = f"AGENTS.md at {main} {main_tip}"
    if declared is None:
        raise stop("VERIFICATION_SECTION_MISSING", f"{where} has no '{verification_section.HEADING}' section")
    if declared.argv_file is None:
        raise stop("VERIFICATION_SECTION_MISSING", f"{where}: '{verification_section.HEADING}' has no 'Verifier argv file:' line")
    argv_text = git(repo, "show", f"{main_tip}:{declared.argv_file}", check=False)
    if argv_text.returncode:
        return declared, None, f"{main} {main_tip} holds no {declared.argv_file}"
    try:
        return declared, parse_verify_command(argv_text.stdout.lstrip("﻿")), None
    except Refused as exc:
        return declared, None, f"{declared.argv_file} at {main}: {exc.detail}"


def check_executable(argv0: str) -> None:
    """The verifier's executable is on PATH; a path names a file of the candidate."""
    if any(sep and sep in argv0 for sep in (os.sep, os.altsep, "/")):
        return
    if shutil.which(argv0) is None:
        raise Refused("VERIFIER_NOT_FOUND", f"the verifier executable {argv0} is not on PATH")


def tool_checkout_warning(checkout: Path = hw.ROOT) -> str | None:
    """A warning when the checkout this code runs from is not a clean `main`."""
    if git(checkout, "rev-parse", "--show-toplevel", check=False).returncode:
        return None
    branch = git(checkout, "branch", "--show-current", check=False).stdout.strip()
    dirty = bool(git(checkout, "status", "--porcelain", "--untracked-files=no", check=False).stdout.strip())
    if branch == "main" and not dirty:
        return None
    sha = git(checkout, "rev-parse", "HEAD", check=False).stdout.strip()
    state = f"on {branch}" if branch else "on a detached HEAD"
    return (
        f"the atc tool checkout {checkout} is {state} at {sha}"
        f"{' with uncommitted changes' if dirty else ''}; this run uses its code"
    )


def resolve_binding(
    args: argparse.Namespace, repo: Path, main: str, main_tip: str, branch: str | None, branch_source: str,
    reviewed: str, alias: str | None,
) -> Binding:
    """The tasks and authority of the run (`docs/TASK_CONTRACT_PLAN.md` § B), or the stop that refuses them."""
    words = {"kind": "owner_words", "quote": args.owner_words} if args.owner_words is not None else None
    match = ta.RECORD_ID.fullmatch(args.task or "")
    record_task = args.task if match and declared_prefix(repo, main) == match["prefix"] else None
    if branch is None or branch_source == "recovery":
        # A recovered candidate is already on main; its range was checked by the run that made it,
        # and its authority is resolved here as for any other run.
        if alias is not None:
            return Binding([alias], {"kind": "unclassified"})
        if args.task is None:
            raise Refused("USAGE", "the reviewed SHA is already on main; name the integration's task with --task")
        if record_task is not None and branch is not None:
            ta.check_record(repo, record_task, reviewed, branch)
        return Binding([args.task], resolve_authority_of(repo, main, main_tip, args.task, record_task, [args.task], words))
    rng = ta.scan(repo, git(repo, "merge-base", main_tip, reviewed).stdout.strip(), reviewed)
    if alias is not None:
        if rng.touches_tasks:
            raise ta.Stop("AUTHORITY_REFERENCE_REFUSED", "--authority-reference never integrates a range that touches "
                                                          f"{ta.TASKS_DIR}/; use --task with --owner-words")
        return Binding([alias], {"kind": "unclassified"})
    if record_task is not None:
        ta.check_record(repo, record_task, reviewed, branch)
    if rng.records_only:
        tasks = rng.task_ids
        if args.task is not None and args.task not in tasks:
            raise Refused("USAGE", f"--task {args.task} names no record this records-only range changes: {', '.join(tasks)}")
    elif args.task is None:
        raise Refused("USAGE", "a range that changes more than .atc/tasks/ needs --task")
    else:
        tasks = [args.task]
    ta.check_range(repo, rng, main_tip, reviewed, may_change=rng.records_only and words is not None)
    return Binding(tasks, resolve_authority_of(repo, main, main_tip, args.task, record_task, tasks, words),
                   rng.records_only)


def resolve_authority_of(
    repo: Path, main: str, main_tip: str, task: str | None, record_task: str | None, tasks: list[str],
    words: dict | None,
) -> dict:
    """Exactly one authority: the Owner's quoted words, or the record on main granting `integrate_main`."""
    if words is not None:
        return words
    if record_task is None:
        raise ta.Stop("AUTHORITY_MISSING", f"--task {task} is no task record; integrate with --owner-words")
    if tasks != [record_task]:
        raise ta.Stop("AUTHORITY_MISSING", f"the range binds {', '.join(tasks)}; the record of {record_task} "
                                           "covers that task alone; integrate with --owner-words")
    return ta.record_authority(repo, record_task, main, main_tip, gp.read_integrations(repo))


def resolve(args: argparse.Namespace, root: Path, root_given: bool, cwd: str) -> Target:
    reviewed = args.reviewed.strip().lower()
    if not FULL_SHA.fullmatch(reviewed):
        raise stop("REVIEWED_SHA_INVALID", f"--reviewed must be the full 40-character SHA: {args.reviewed}")
    if args.authority_reference is not None and (args.task is not None or args.owner_words is not None):
        raise Refused("USAGE", "--authority-reference is the deprecated form of --task with an authority; "
                               "give --task and --owner-words, or --authority-reference alone")
    if args.authority_reference is None and args.task is None and args.owner_words is None:
        raise Refused("USAGE", "name the task with --task and the authority with --owner-words or the record's "
                               "integrate_main on main")
    if args.owner_words is not None and not args.owner_words.strip():
        raise Refused("USAGE", "--owner-words needs the Owner's words, quoted")
    project, repo = resolve_repo(args, root, root_given, cwd)
    check_checkout(repo)
    fetched = "none"
    if gp.has_remote(repo):
        if error := gp.fetch_origin(repo):
            raise stop("FETCH_FAILED", f"{gp.REASONS['FETCH_FAILED']}: {error}")
        fetched = gp.REMOTE
    main = args.main
    main_tip = rev(repo, main)
    if main_tip is None:
        raise Refused("GIT", f"cannot resolve {main} in {repo}")
    if rev(repo, reviewed) != reviewed:
        raise stop("REVIEWED_SHA_UNKNOWN", f"{gp.REASONS['REVIEWED_SHA_UNKNOWN']}: {reviewed}")
    warnings: list[str] = []
    alias = None
    if args.authority_reference is not None:
        _, alias = resolve_authority(args.authority_reference, reviewed)
        warnings.append(ALIAS_NOTICE)
        if warning := authority_record_warning(repo, alias, reviewed):
            warnings.append(warning)
    carried_from = None
    if args.rebased is not None:
        rebased = args.rebased.strip().lower()
        if not FULL_SHA.fullmatch(rebased):
            raise stop("REVIEWED_SHA_INVALID", f"--rebased must be the full 40-character SHA: {args.rebased}")
        if rev(repo, rebased) != rebased:
            raise stop("REVIEWED_SHA_UNKNOWN", f"--rebased names no commit in this repository: {rebased}")
        if rebased == reviewed:
            raise Refused("USAGE", "--rebased equals --reviewed; integrate the reviewed SHA without --rebased")
        carried_from, reviewed = reviewed, rebased
    if args.branch is not None:
        branch, branch_source = args.branch, "--branch"
    else:
        branch, branch_source = infer_branch(repo, reviewed, main, main_tip)
    try:
        binding = resolve_binding(args, repo, main, main_tip, branch, branch_source, reviewed, alias)
    except ta.Stop as exc:
        raise stop(exc.token, exc.detail) from None
    if binding.records_only:
        return Target(
            repo, main, main_tip, branch, branch_source, reviewed, binding, [], RECORD_CHECK, 0, RECORD_CHECK,
            warnings, carried_from, fetched, project,
        )

    declared, declared_argv, problem = declared_verifier(repo, main, main_tip)
    explicit = None
    if args.verify_command is not None:
        explicit, argv_source = args.verify_command, "--verify-command"
    elif args.verify_command_file is not None:
        path = args.verify_command_file if args.verify_command_file.is_absolute() else repo / args.verify_command_file
        try:
            explicit = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise Refused("VERIFIER_ARGV_UNREADABLE", f"could not read --verify-command-file: {exc}") from exc
        argv_source = f"--verify-command-file {path}"
    if explicit is not None:
        if not explicit.strip():
            raise Refused(
                "VERIFIER_ARGV_MISSING",
                "a verifier argv (--verify-command or --verify-command-file) is required; "
                "main cannot advance without candidate verification",
            )
        argv = parse_verify_command(explicit)
        if argv != declared_argv:
            warnings.append(
                f"the verifier argv from {argv_source} differs from {declared.argv_file} that AGENTS.md declares at {main}: "
                f"{json.dumps(declared_argv) if declared_argv else problem}"
            )
    elif declared_argv is None:
        raise Refused("VERIFIER_ARGV_INVALID", f"AGENTS.md declares the verifier argv file {declared.argv_file}, but {problem}")
    else:
        argv, argv_source = declared_argv, f"AGENTS.md {declared.argv_file}"

    declared_timeout = verification_section.DEFAULT_TIMEOUT if declared.timeout is None else declared.timeout
    declared_from = "the default" if declared.timeout is None else f"AGENTS.md at {main}"
    if args.verify_timeout is not None:
        if args.verify_timeout <= 0:
            raise Refused("VERIFY_TIMEOUT_INVALID", "--verify-timeout must be greater than zero")
        timeout, timeout_source = args.verify_timeout, "--verify-timeout"
        if timeout != declared_timeout:
            warnings.append(f"--verify-timeout {timeout:g} differs from the {declared_timeout:g} seconds of {declared_from}")
    else:
        timeout, timeout_source = declared_timeout, "default" if declared.timeout is None else "AGENTS.md"
        if timeout <= 0:
            raise Refused("VERIFY_TIMEOUT_INVALID", f"AGENTS.md at {main} declares a verifier timeout that is not greater than zero")

    check_executable(argv[0])
    if warning := tool_checkout_warning():
        warnings.append(warning)
    return Target(
        repo, main, main_tip, branch, branch_source, reviewed, binding,
        argv, argv_source, timeout, timeout_source, warnings, carried_from, fetched, project,
    )


def render_result(result: dict, binding: Binding, project: str) -> str:
    line = f"{result['verdict']} {project} main {result['main_before']} -> {result['main_after']}"
    if result.get("candidate_sha"):
        line += f" candidate {result['candidate_sha']} verification={result['verification']}"
    if result["verdict"] == "NO_FF":
        line += f" (parents {result['first_parent']} {result['second_parent']})"
    if result.get("verification") == "PASS":
        line += f" evidence={'recorded' if result.get('evidence') else 'NONE(' + str(result.get('evidence_reason')) + ')'}"
    return f"{line} {render_binding(binding)}"


class _Human:
    """A text stream for human output: 12-character SHAs and project names in place of checkout paths."""

    def __init__(self, stream) -> None:
        self.stream = stream
        self.paths: dict[str, str] = {}

    def write(self, text: str) -> int:
        for path, name in sorted(self.paths.items(), key=lambda item: -len(item[0])):
            text = text.replace(path, name)
        self.stream.write(FULL_SHA_WORD.sub(r"\1", text))
        return len(text)

    def flush(self) -> None:
        self.stream.flush()

    def __getattr__(self, name: str):
        return getattr(self.stream, name)


class _Parser(argparse.ArgumentParser):
    """Argument errors take the `ERROR <TOKEN> <detail>` form of § Command results."""

    def error(self, message: str):
        raise Refused("USAGE", message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(allow_abbrev=False, description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--repo", type=Path, help="main checkout of the repository; the workspace root takes this form")
    target.add_argument("--project", help="registered project name from workspace.yaml; root names the workspace root")
    parser.add_argument("--branch", help="reviewed topic branch (default: the one local branch at --reviewed)")
    parser.add_argument("--reviewed", required=True, help="full 40-character SHA named by the review")
    parser.add_argument(
        "--rebased",
        help="full SHA of the unpublished topic rebased onto main; integrates it carrying over the approval "
        "of --reviewed when the rebase is clean and every patch-id matches",
    )
    parser.add_argument("--main", default="main", help="integration branch (default: main)")
    parser.add_argument(
        "--task",
        help="the task: a record ID with a declared prefix (it names the repository), or a task identity string",
    )
    parser.add_argument(
        "--owner-words", metavar="QUOTE",
        help="the Owner's words granting this integration, quoted; without it the record on main must grant "
        "integrate_main, which covers FF only",
    )
    parser.add_argument(
        "--authority-reference",
        help="deprecated: a task identity string recorded with authority unclassified; refused for a range "
        "that touches .atc/tasks/",
    )
    verifier = parser.add_mutually_exclusive_group()
    verifier.add_argument("--verify-command", help="verification argv as a JSON string array, in place of the declared one")
    verifier.add_argument(
        "--verify-command-file", type=Path,
        help="file holding the verification argv as a JSON string array; a relative path is under the repository",
    )
    parser.add_argument(
        "--verify-timeout", type=float,
        help="maximum verifier runtime in seconds (default: the AGENTS.md declaration, else 300)",
    )
    parser.add_argument("--check", action="store_true", help="resolve, preflight and warn; take no slot and create no candidate")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def _report_refusal(report: gp.Report) -> dict:
    return {"verdict": report.verdict, "reason": report.reason, "detail": report.detail, "next": report.next}


def _print_stop_report(report: gp.Report, payload: dict, as_json: bool) -> None:
    lines = render(report).splitlines()
    print("\n".join(lines[:2]), file=sys.stderr)
    if as_json:
        print(json.dumps({**payload, "preflight": asdict(report), "refusal": _report_refusal(report)}, indent=2))
    else:
        print("\n".join(lines[2:]))


def _print_refusal(refusal: Refused, payload: dict, as_json: bool) -> None:
    print("\n".join(refusal.lines()), file=sys.stderr)
    if as_json:
        print(json.dumps({**payload, "refusal": refusal.as_dict()}, indent=2))


def check(t: Target, as_json: bool) -> int:
    payload = {"command": "integrate", "check": True, "resolved": {**asdict(t), "repo": str(t.repo)}}
    recovered = _recover_interrupted_update(t.repo, t.main, t.branch, t.reviewed, t.binding, [], check=True)
    if recovered is not None:
        code, result = recovered
        refusal = result.pop("refusal", None)
        if refusal is not None:
            _print_refusal(refusal, {**payload, **result}, as_json)
        elif as_json:
            print(json.dumps({**payload, **result}, indent=2))
        else:
            print(f"CHECK {result['verdict']} {result['reason']}: main {result['main_after']} is the candidate of this request")
        return code
    if t.branch is None:
        _print_refusal(
            stop("ALREADY_MERGED", f"the reviewed SHA {t.reviewed} is already reachable from {t.main}; nothing to integrate"),
            payload, as_json,
        )
        return STOP_BEFORE_CANDIDATE
    report = merge_preflight(t.repo, t.branch, t.reviewed, t.main)
    if report.verdict == "STOP":
        _print_stop_report(report, payload, as_json)
        return STOP_BEFORE_CANDIDATE
    if refusal := authority_verdict_stop(t.binding, report.verdict):
        _print_refusal(refusal, {**payload, "preflight": asdict(report)}, as_json)
        return refusal.code
    carried = None
    if t.carried_from:
        try:
            carried = carry_over(t.repo, t.main, t.carried_from, t.reviewed, report.evidence["main_tip"])
        except Refused as refusal:
            _print_refusal(refusal, {**payload, "preflight": asdict(report)}, as_json)
            return refusal.code
    if as_json:
        print(json.dumps({**payload, "preflight": asdict(report), **({"carried_over": carried} if carried else {})}, indent=2))
    else:
        print(f"CHECK {render(report)}")
        if carried:
            print("\n".join(render_carry_over(carried)))
    return 0


def main(argv: list[str] | None = None, root: Path = hw.ROOT, root_given: bool = False, cwd: str | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    try:
        args = build_parser().parse_args(argv)
    except Refused as refusal:
        print("\n".join(refusal.lines()), file=sys.stderr)
        return refusal.code
    if args.json:
        return _main(args, root, root_given, cwd)
    streams = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Human(streams[0]), _Human(streams[1])
    try:
        return _main(args, root, root_given, cwd)
    finally:
        sys.stdout, sys.stderr = streams


def _main(args: argparse.Namespace, root: Path, root_given: bool, cwd: str | None) -> int:
    payload = {"command": "integrate"}
    try:
        locations.enter()
        target = resolve(args, root, root_given, cwd or os.getcwd())
        for stream in (sys.stdout, sys.stderr):
            if isinstance(stream, _Human):
                stream.paths.update({str(target.repo): target.project,
                                     str(target.repo.resolve()): target.project})
        print(target.line(), file=sys.stderr)
        for warning in target.warnings:
            print(f"WARNING {warning}", file=sys.stderr)
        if args.check:
            return check(target, args.json)
        code, result = integrate(target)
    except locations.Refusal as refusal:
        print(f"{refusal.token} {refusal.detail}", file=sys.stderr)
        if args.json:
            print(json.dumps({**payload, "refusal": {
                "verdict": "REFUSAL", "reason": refusal.token, "detail": refusal.detail, "next": None,
            }}, indent=2))
        return refusal.code
    except Refused as refusal:
        _print_refusal(refusal, payload, args.json)
        return refusal.code
    except (GitError, RuntimeError) as exc:
        _print_refusal(Refused("GIT" if isinstance(exc, GitError) else "RUNTIME", str(exc)), payload, args.json)
        return ERROR
    result["project"] = target.project
    result["warnings"] = target.warnings
    report = result.pop("report", None)
    refusal = result.pop("refusal", None)
    if not args.json:
        for key in ("verification_warning", "candidate_cleanup_warning", "integration_record_warning", "push_warning"):
            if result.get(key):
                print(f"WARNING {result[key]}", file=sys.stderr)
        unapproved = [entry for entry in (result.get("push") or {}).get("commits", []) if not entry["push_approval"]]
        if unapproved:
            print(
                f"WARNING {len(unapproved)} commits in origin/{target.main}..{target.main} come from other integrations "
                "or none; a push carries them and needs push approval for each",
                file=sys.stderr,
            )
        for entry in result.get("recovered_candidates", []):
            print(
                f"NOTE recovery ref for candidate {entry['candidate_sha']}: {entry['disposition']}"
                f" ({', '.join(entry['candidate_refs'])})",
                file=sys.stderr,
            )
    if report is not None:
        _print_stop_report(report, result, args.json)
    elif refusal is not None:
        _print_refusal(refusal, result, args.json)
    elif args.json:
        print(json.dumps(result, indent=2))
    else:
        lines = [render_result(result, target.binding, target.project)]
        if result.get("carried_over"):
            lines += render_carry_over(result["carried_over"])
        if result.get("push"):
            lines += gp.render_push(result["push"])
        print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())

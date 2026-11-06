"""The pre-push gate skeleton (docs/TESTKIT.md § Pre-push gate); a project's gate script calls ``main``."""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
import os
from pathlib import Path
import subprocess
import sys

from . import evidence, isolation, locations, scratch, selection, stages
from .evidence import Decision
from .git import SelectionError, baseline_deltas, changed_paths, git_env, verify_push_checkout
from .project import Project, StaticCheck

LOG_PATTERN = "prepush-*.log"
SELECTION_FAILED_EXIT = 2


def _git(root: Path):
    def run(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, env=git_env(),
                                stdin=subprocess.DEVNULL, creationflags=scratch.WINDOW_HIDDEN)
        if result.returncode != 0:
            raise SelectionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout
    return run


def pushed_commits(push_lines: Iterable[str]) -> list[str]:
    """The commit each pushed ref ends at, once each, without ref deletions."""
    commits: list[str] = []
    for line in push_lines:
        fields = line.split()
        if len(fields) == 4 and set(fields[1]) != {"0"} and fields[1] not in commits:
            commits.append(fields[1])
    return commits


def _clear_logs(logs: Path) -> None:
    """The directory holds the latest run's logs only; a log that cannot go is named."""
    for log in logs.glob(LOG_PATTERN):
        try:
            log.unlink()
        except OSError as error:
            print(f"pre-push: log of an earlier run left: {log} ({error})", file=sys.stderr)


def _selection(project: Project, push_lines: list[str], explicit: Sequence[str] | None, run_mode: bool, git):
    """The selection for the pushed refs or for explicit paths, and the changed paths."""
    if explicit:
        return project.select(explicit, None), list(explicit)
    if run_mode:
        verify_push_checkout(push_lines, git)
    paths = changed_paths(push_lines, git, missing_remote=project.missing_remote)
    if paths is None:
        chosen = project.select((), None)
        chosen.widen("<push range>", "no merge-base with origin/main")
        return chosen, None
    touched = project.baseline_files.intersection(path.replace("\\", "/") for path in paths)
    deltas = baseline_deltas(push_lines, git, touched, missing_remote=project.missing_remote) if touched else None
    return project.select(paths, deltas), paths


def _decisions(project: Project, push_lines: list[str], pytest_paths: list[str], git) -> list[Decision]:
    decisions = []
    for pushed in pushed_commits(push_lines):
        try:
            commit = git("rev-parse", pushed + "^{commit}").strip()
        except SelectionError:
            commit = pushed  # an object Git cannot peel to a commit has no record
        decisions.append(evidence.check(git, project.root, commit, pytest_paths, project.stage_list(),
                                        project.argv_file))
    return decisions


def _run_checks(project: Project, checks: Iterable[StaticCheck], isolated: isolation.IsolatedRun, logs: Path,
                run: stages.Run) -> int:
    for check in checks:
        log = logs / f"prepush-{check.name}.log"
        code = run(list(check.argv), cwd=project.root / check.cwd, env=isolated.environment("static"), log=log)
        if code:
            print(f"pre-push: {check.token}; push aborted (full output: {log})", file=sys.stderr)
            for line in stages.failure_lines(log):
                print(line, file=sys.stderr)
            return code
    return 0


def _run_tests(project: Project, push_lines: list[str], pytest_paths: list[str], isolated: isolation.IsolatedRun,
               logs: Path, git, run: stages.Run) -> int:
    decisions = _decisions(project, push_lines, pytest_paths, git)
    for decision in decisions:
        print(evidence.describe(decision), flush=True)
    if decisions and all(decision.reuse for decision in decisions):
        return 0
    if not decisions:
        print("pre-push: tests RUN (evidence: NO_PUSHED_COMMIT)", flush=True)
    outcome = stages.run_stages(
        project.runner, pytest_paths, cwd=project.root, stage_list=project.stage_list(),
        env_for=lambda stage: isolated.environment(stage.name),
        options_for=lambda stage: isolated.pytest_options(stage.name),
        log_for=lambda stage: logs / f"prepush-{stage.name}.log", run=run)
    if outcome.token == stages.NO_TESTS_COLLECTED:
        print(f"pre-push: {outcome.token}; push aborted (logs: {logs})", file=sys.stderr)
    elif outcome.token:
        print(f"pre-push: {outcome.token} in the {outcome.stage.name} stage; push aborted "
              f"(full output: {outcome.log})", file=sys.stderr)
        for line in stages.failure_lines(outcome.log):
            print(line, file=sys.stderr)
    return outcome.code


def main(project: Project, argv: Sequence[str] | None = None, stdin: Iterable[str] | None = None,
         run: stages.Run = stages.run_logged) -> int:
    parser = argparse.ArgumentParser(description="Pre-push gate")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true", help="read the pushed refs from stdin and run the gate")
    mode.add_argument("--paths", nargs="+", help="print the selection for these paths and run nothing")
    args = parser.parse_args(argv)
    git = _git(project.root)
    push_lines = [] if args.paths else list(sys.stdin if stdin is None else stdin)
    try:
        chosen, paths = _selection(project, push_lines, args.paths, args.run, git)
        pytest_paths = chosen.pytest_paths()
    except Exception as error:  # noqa: BLE001 - any selection failure stops the push
        print(f"pre-push: test selection failed, push aborted: {error}", file=sys.stderr)
        return SELECTION_FAILED_EXIT
    print(selection.describe(chosen), flush=True)
    if not args.run:
        return 0
    logs = project.root / stages.LOG_DIRECTORY
    refused = stages.refuse_log_directory(logs)
    if refused:
        return refused
    _clear_logs(logs)
    checks = [check for check in project.checks if check.applies(paths)]
    if not checks and pytest_paths is None:
        return 0
    isolation.drop_hami_variables(os.environ)
    try:
        locations.enter()
        work = isolation.create_working_directory()
        locations.enter(work)
    except locations.Refusal as refusal:
        print(f"{refusal.token} {refusal.detail}", file=sys.stderr)
        return refusal.code
    isolated = isolation.IsolatedRun(work, credentials=project.credential_variables,
                                     data_roots=project.data_root_variables)
    code = _run_checks(project, checks, isolated, logs, run)
    if not code and pytest_paths is not None:
        code = _run_tests(project, push_lines, pytest_paths, isolated, logs, git, run)
    if code:
        print(f"pre-push: working directory kept: {work}", file=sys.stderr)
        return code
    why = scratch.remove_tree(work, scratch.working_directory_links(work))
    if why:
        print(f"pre-push: working directory left: {work} ({why})", flush=True)
    return 0

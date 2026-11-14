#!/usr/bin/env python3
"""`atc test`: a task's tests in isolation (docs/TESTKIT.md § Commands).

`atc test ID` runs the tests related to the task's changes since its record's `base_sha` in the task's own
worktree; `--sha` runs them in an export of that commit, `--mutants` judges a mutant list there, and
`--candidate` verifies the candidate `atc integrate ID` would build. Every run happens in a registered
scratch named after the task, and every run prunes stale scratch entries first.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from atc_testkit import checkout, desktop, locations, scratch, testrun, toolchain as toolchains
from atc_testkit.config import tool_table

import cli_conventions
import git_preflight as gp
import managed_worktree as hw
import task_records
from cli_conventions import Refusal

ROOT = hw.ROOT
TESTKIT_HINTS = {
    "TREE_NOT_FOUND": "the task's worktree is gone; pass --sha to test a commit",
    "SCRATCH_TREE_EXISTS": "atc test ID --clean deletes the task's scratches",
    "SHA_NOT_FOUND": "name a commit of the task's repository",
    "VENV_MISSING": "atc test ID --clean, then run again to rebuild the scratch",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc test", allow_abbrev=False, **cli_conventions.form(
        "Run a task's tests in isolation, its output kept in a log.",
        "atc test ID [--sha SHA] [--all] [-v] [-- PYTEST_ARG...]\n"
        "    the tests related to the task's changes since its base_sha (--all: the full suite), in the task's\n"
        "    worktree, or with --sha in an export of SHA (scratch ID-SHA7, its environment built once)\n"
        "atc test ID --mutants FILE [--sha SHA] [-- TEST...]\n"
        "    apply each mutant of FILE alone to an export of SHA (default: the branch tip) and run its tests\n"
        "    (default: the related tests); a mutant's kill_rule is failure or assertion\n"
        "atc test ID --candidate [--sha SHA]\n"
        "    the integration verifier on the candidate atc integrate ID would build (default SHA: the branch tip)\n"
        "atc test ID --clean      delete the task's scratches\n"
        "atc test --list [ID]     list the registered scratches, or the task's\n"
        "atc test --private -- COMMAND   run one command on a private desktop\n\n"
        "The tests run in the shallowest directory of the tree whose pyproject.toml has [tool.atc-testkit];\n"
        "its timeout key bounds the run. -v streams pytest's output.",
        exit="pytest's code for a run (0 passed); 1 a mutant run that survived or failed its baseline, a STOP;\n"
             "2 ERROR; 8 a guard refusal; 9 a refusal before the run; 124 a timeout; 130 interrupted",
        see="docs/TESTKIT.md § Commands; docs/WORKFLOW.md § The flow"))
    parser.add_argument("task", nargs="?", metavar="ID", help="the task record ID")
    form = parser.add_mutually_exclusive_group()
    form.add_argument("--mutants", metavar="FILE", help="JSON list of mutants: {label, edits: [{path, old, new}], "
                                                        "tests?: [...], kill_rule?: failure|assertion}")
    form.add_argument("--candidate", action="store_true", help="verify the integration candidate")
    form.add_argument("--clean", action="store_true", help="delete the task's scratches")
    form.add_argument("--list", action="store_true", help="list registered scratches")
    form.add_argument("--private", action="store_true", help="run the command after -- on a private desktop")
    parser.add_argument("--sha", metavar="SHA", help="test this commit in an export instead of the worktree")
    parser.add_argument("--all", action="store_true", help="the full suite instead of the related tests")
    cli_conventions.add_verbose(parser)
    cli_conventions.add_json(parser)
    return parser


class Task:
    """A task's repository, branch, base and worktree."""

    def __init__(self, root: Path, environ: dict[str, str], task_id: str) -> None:
        ws = task_records.workspace(root, environ)
        self.project = task_records.project_of_task(ws, task_id)
        self.id = task_id
        self.repo = Path(self.project.repo)
        self.branch, self.record = task_records.task_record(self.repo, task_id)
        base = self.record.get("base_sha")
        self.base = base if isinstance(base, str) else None
        try:
            target, _ = hw.located(hw.context(root, environ, self.project.name), self.branch or "")
            self.worktree = Path(target) if os.path.isdir(target) else None
        except hw.Stop:
            self.worktree = None

    def tip(self) -> str:
        found = gp.git(self.repo, "rev-parse", "--verify", "-q", f"refs/heads/{self.branch}^{{commit}}",
                       check=False).stdout.strip() if self.branch else ""
        if not found:
            raise Refusal("NO_BRANCH", f"the branch {self.branch} of {self.id} is no local branch",
                          "pass --sha with the commit to test")
        return found

    def source(self) -> Path:
        return self.worktree or self.repo


def test_dir(tree: Path, task: Task) -> Path:
    found = task_records.test_directory(tree)
    if found is None:
        raise Refusal("NO_TESTKIT", f"{task.project.name} has no pyproject.toml with [tool.atc-testkit]",
                      "its tasks use tests: none; the reviewer reads the diff")
    return found


def timeout_of(directory: Path) -> list[str]:
    value = tool_table(directory).get("timeout")
    return ["--timeout", str(value)] if isinstance(value, (int, float)) and value > 0 else []


def export(task: Task, sha: str, environ: dict[str, str]) -> tuple[str, str, Path]:
    """The scratch ``<ID>-<sha7>`` holding an export of ``sha`` with its environment built: (name, full SHA,
    the export's test directory)."""
    commit = checkout.resolve_commit(task.source(), sha)
    name = f"{task.id}-{commit[:checkout.SHORT_SHA]}"
    root = locations.locate(environ).tmp
    tree = root / name / checkout.TREE
    if not tree.is_dir():
        shown = gp.git(task.repo, "ls-tree", "-r", "--name-only", commit, check=False).stdout.splitlines()
        lock = next((str(Path(p).parent.as_posix()) for p in sorted(shown, key=lambda p: p.count("/"))
                     if Path(p).name == "pyproject.toml" and p.count("/") <= task_records.TEST_DIR_DEPTH
                     and "atc-testkit" in gp.git(task.repo, "show", f"{commit}:{p}", check=False).stdout), None)
        if lock is None:
            raise Refusal("NO_TESTKIT", f"{commit[:12]} has no pyproject.toml with [tool.atc-testkit]",
                          "its tasks use tests: none; the reviewer reads the diff")
        code = checkout.checkout([commit, "--task", task.id, "--worktree", str(task.source()), "--scratch", name,
                                  "--lock-dir", lock], environ, say=lambda _line: None)
        if code:
            raise Refusal("SYNC_FAILED", f"building the environment of {name} failed",
                          f"atc test {task.id} --clean, then run again", cli_conventions.STOP)
    return name, commit, test_dir(tree, task)


def run_tests(task: Task, args: argparse.Namespace, pytest_args: list[str], environ: dict[str, str]) -> int:
    if args.sha:
        name, _commit, directory = export(task, args.sha, environ)
    else:
        if task.worktree is None:
            raise Refusal("NO_WORKTREE", f"{task.id} has no worktree", f"atc test {task.id} --sha SHA tests a commit")
        name, directory = task.id, test_dir(task.worktree, task)
    argv = ["--scratch", name, "--task", task.id, "--tree", str(directory), "--label", "all" if args.all else "related",
            *timeout_of(directory), *(["--progress"] if args.verbose else [])]
    if not args.all and task.base:
        argv += ["--related", task.base]
    return testrun.run([*argv, *(["--", *pytest_args] if pytest_args else [])], environ)


def related_tests(directory: Path, task: Task, name: str, environ: dict[str, str]) -> list[str]:
    """The test files the task's changes since its base touch, relative to ``directory``, the test directory of
    the export in the scratch ``name``, whose registry line names the source commit to diff."""
    if not task.base:
        return []
    root = locations.locate(environ).tmp
    row = next((r for r in scratch.registered(root) if Path(r.scratch).name == name), None)
    python = toolchains.for_tree(directory).interpreter(directory)
    paths = testrun.changed_paths(directory, task.base, row)
    return testrun.touched(directory, python, paths, environ)


def run_mutants(task: Task, args: argparse.Namespace, tests: list[str], environ: dict[str, str]) -> int:
    name, commit, directory = export(task, args.sha or task.tip(), environ)
    tree = directory
    while tree.name != checkout.TREE:
        tree = tree.parent
    prefix = directory.relative_to(tree).as_posix()
    tests = tests or [t if prefix == "." else f"{prefix}/{t}" for t in related_tests(directory, task, name, environ)]
    if not tests:
        raise Refusal("NO_TESTS", "no test is related to the task's changes", "name the judging tests after --")
    return testrun.mutants([name, commit, str(task.source()), str(Path(args.mutants).resolve()), "--task", task.id,
                            "--tests", *tests], environ)


def clean(task_id: str, environ: dict[str, str]) -> int:
    root = locations.locate(environ).tmp
    names = [Path(row.scratch).name for row in scratch.registered(root) if row.task == task_id]
    if not names:
        print(f"CLEAN {task_id}: no scratch")
        return cli_conventions.OK
    refused = []

    def say(line: str) -> None:
        if line.startswith("REFUSED "):
            refused.append(line)
    code = scratch.clean(names, with_runs=True, say=say, environ=environ)
    print(f"{'REFUSED' if refused else 'CLEAN'} {task_id}: {len(names) - len(refused)} scratches deleted")
    for line in refused:
        print(f"  {line}")
    if refused:
        print(f"STOP SCRATCH_REFUSED: {len(refused)} refused", file=sys.stderr)
        print("hint: each REFUSED line names the scratch and why", file=sys.stderr)
    return code


def list_scratches(task_id: str | None, environ: dict[str, str], as_json: bool) -> int:
    if task_id is None:
        return scratch.list_scratches(environ=environ, as_json=as_json)
    root = locations.locate(environ).tmp
    rows = [row for row in scratch.registered(root) if row.task == task_id]
    print(f"{len(rows)} scratches of {task_id}")
    for row in rows:
        print(f"  {Path(row.scratch).name} {row.tree} {row.created_utc}")
    return cli_conventions.OK


def main(argv: list[str] | None = None, root: Path = ROOT, environ: dict[str, str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rest: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, rest = argv[:at], argv[at + 1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    environ = dict(os.environ) if environ is None else environ
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    if args.private:
        if args.task or not rest:
            parser.error("--private takes no ID and a command after --")
        return desktop.run(rest, cwd=os.getcwd(), env=environ)
    try:
        if args.list:
            return list_scratches(args.task, environ, args.json)
        if args.task is None:
            parser.error("an ID is required")
        if (args.clean or args.candidate) and (rest or args.all):
            parser.error("--clean and --candidate take no tests and no --all")
        if args.clean and args.sha:
            parser.error("--clean takes no --sha")
        if args.clean:
            task_records.workspace(root, environ)
            return clean(args.task, environ)
        if args.candidate:
            import git_integrate
            return git_integrate.run_candidate(args.task, args.sha, root, as_json=args.json)
        task = Task(root, environ, args.task)
        scratch.prune(say=lambda _line: None, environ=environ)
        if args.mutants:
            return run_mutants(task, args, rest, environ)
        return run_tests(task, args, rest, environ)
    except Refusal as refusal:
        return cli_conventions.refuse(refusal, args.json)
    except locations.Refusal as refusal:
        print(f"STOP {refusal.token}: {refusal.detail}", file=sys.stderr)
        if TESTKIT_HINTS.get(refusal.token):
            print(f"hint: {TESTKIT_HINTS[refusal.token]}", file=sys.stderr)
        return refusal.code


if __name__ == "__main__":
    sys.exit(main())

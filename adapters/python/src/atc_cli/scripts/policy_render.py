#!/usr/bin/env python3
"""`atc policy render ID`: render a project's enforcement files from its `policies/hard-deny.yaml`, in the task's
worktree (docs/ENFORCEMENT.md § Outputs).

It writes `generated/enforcement/` and the copies the project keeps, when present: the `permissions.deny` list of
`.claude/settings.json` (its other keys untouched) and `scripts/git-hooks/pre-commit`. `--check` writes nothing and
reports each file that differs. Run as a script, `policy_render.py DIR [--check]` renders the tree DIR (the
conformance cases of `shared/conformance/generate-enforcement/`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cli_conventions
import generate_enforcement as ge
import policies
from cli_conventions import Refusal

ROOT = Path(__file__).resolve().parents[5]
POLICY = "policies/hard-deny.yaml"
GENERATED = "generated/enforcement"
SETTINGS = ".claude/settings.json"
HOOK_COPY = "scripts/git-hooks/pre-commit"
HOOK_FILE = "git-hooks/pre-commit"
CLAUDE_FILE = "claude-code/settings.json"
SEE = "docs/ENFORCEMENT.md § Outputs"


class Problems(Exception):
    """The policy's problems, one line each."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def load_hard(tree: Path) -> dict:
    """The tree's hard-deny policy; Refusal NO_POLICY without one, Problems for one that does not load."""
    if not (tree / POLICY).is_file():
        raise Refusal("NO_POLICY", f"{tree} has no {POLICY}",
                      "a project without a hard-deny policy has no enforcement files")
    try:
        hard = policies.load(tree / "policies")["hard-deny"]
    except policies.PolicyError as exc:
        raise Problems(list(exc.problems)) from None
    problems = policies.unresolved_tokens(hard, project=True)
    if problems:
        raise Problems(problems)
    return hard


def settings_doc(path: Path) -> dict:
    """`.claude/settings.json` as an object, read past a UTF-8 BOM; Refusal SETTINGS_INVALID otherwise."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8").removeprefix("﻿"))
    except (OSError, ValueError) as exc:
        raise Refusal("SETTINGS_INVALID", f"{SETTINGS}: {exc}", "fix the file's JSON, then run again") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("permissions", {}), dict):
        raise Refusal("SETTINGS_INVALID", f"{SETTINGS}: the top level or its permissions is not a JSON object",
                      "fix the file's JSON, then run again")
    return doc


def plan(tree: Path, hard: dict) -> dict[str, str]:
    """Each file to hold, by tree-relative path, with the content it must have; a copy only when present."""
    files = ge.render(hard)
    wanted = {f"{GENERATED}/{rel}": content for rel, content in files.items()}
    settings = tree / SETTINGS
    if settings.is_file():
        doc = settings_doc(settings)
        deny = json.loads(files[CLAUDE_FILE])["permissions"]["deny"]
        if doc.get("permissions", {}).get("deny") != deny:
            doc.setdefault("permissions", {})["deny"] = deny
            wanted[SETTINGS] = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if (tree / HOOK_COPY).is_file():
        wanted[HOOK_COPY] = files[HOOK_FILE]
    return wanted


def differing(tree: Path, wanted: dict[str, str]) -> list[str]:
    """The paths whose content differs, ignoring line-ending style; the settings copy is in ``wanted`` only when
    its deny list differs."""
    return [rel for rel, content in wanted.items() if rel == SETTINGS or ge._read(tree / rel) != content]


def render(tree: Path, check: bool) -> list[str]:
    """Write (or, with ``check``, only list) the files that differ; the paths, in plan order."""
    wanted = plan(tree, load_hard(tree))
    changed = differing(tree, wanted)
    if check:
        return changed
    for rel in changed:
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        ge.write_if_changed(target, wanted[rel])
    for rel in (f"{GENERATED}/{HOOK_FILE}", HOOK_COPY):
        if rel in wanted:
            os.chmod(tree / rel, 0o755)
    return changed


def report(tree: Path, check: bool, verbose: bool, as_json: bool, again: str) -> int:
    """Run `render` on ``tree`` and print its verdict (shared/conformance/CLI.md § Output); ``again`` is the command
    a hint names."""
    try:
        changed = render(tree, check)
    except Refusal as refusal:
        return cli_conventions.refuse(refusal, as_json)
    except Problems as exc:
        refusal = Refusal("POLICY_INVALID", f"{len(exc.problems)} problems",
                          f"fix {POLICY}, then run {again}")
        print("\n".join(f"  {problem}" for problem in exc.problems), file=sys.stderr)
        return cli_conventions.refuse(refusal, as_json, problems=exc.problems)
    if check and changed:
        if as_json:
            print(json.dumps({"verdict": "DRIFT", "drift": changed}, indent=2))
        else:
            print(f"DRIFT {len(changed)} files")
            print("\n".join(f"  {rel} differs from {POLICY}" for rel in changed))
        return cli_conventions.STOP
    if not changed:
        verdict, line = "CURRENT", "CURRENT enforcement files"
    else:
        verdict, line = "RENDERED", f"RENDERED {len(changed)} files"
    if as_json:
        print(json.dumps({"verdict": verdict, "files": [] if check else changed}, indent=2))
    else:
        print(line)
        if verbose:
            print("\n".join(f"  {rel}" for rel in changed))
    return cli_conventions.OK


def worktree_of(root: Path, environ: dict[str, str], task_id: str) -> Path:
    """The managed worktree of a project task; the workspace policy has no enforcement files (the hook composes it
    from `main`)."""
    import managed_worktree as hw
    import validate_workspace as validator
    import task_records
    ws = task_records.workspace(root, environ)
    project = task_records.project_of_task(ws, task_id)
    if project.name == validator.ROOT_TOPICS_NAME:
        raise Refusal("ROOT_POLICY", f"{task_id} is a task of the workspace root, whose policy has no enforcement "
                      "files", "check it with atc-maintain policies check; the hook composes it from main")
    branch, _ = task_records.task_record(Path(project.repo), task_id)
    if branch is None:
        raise Refusal("NO_WORKTREE", f"the record of {task_id} binds no branch", None)
    found = hw.find(hw.Context(ws, project, []), branch)
    if found is None:
        raise Refusal("NO_WORKTREE", f"no worktree has {branch} checked out", f"atc task resume {task_id}")
    return Path(found["worktree"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc policy", allow_abbrev=False, **cli_conventions.form(
        "Render a project's enforcement files from its hard-deny policy.",
        exit="0 rendered or current; 1 STOP or drift under --check; 2 ERROR", see=SEE))
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    render_parser = sub.add_parser("render", allow_abbrev=False, help="render the task worktree's enforcement files",
                                   **cli_conventions.form(
                                       "Render generated/enforcement/ from policies/hard-deny.yaml in the task's "
                                       "worktree, and the copies the project keeps.",
                                       "The copies, when present: permissions.deny of .claude/settings.json (its "
                                       "other keys kept)\nand scripts/git-hooks/pre-commit. Commit them with the "
                                       "policy change.",
                                       exit="0 rendered or current; 1 STOP, or drift under --check; 2 ERROR",
                                       see=SEE))
    render_parser.add_argument("task", metavar="ID", help="the task whose worktree holds the policy")
    render_parser.add_argument("--check", action="store_true",
                               help="write nothing; exit 1 when a file differs from the policy's render")
    cli_conventions.add_verbose(render_parser)
    cli_conventions.add_json(render_parser)
    return parser


def main(argv: list[str] | None = None, root: Path = ROOT, environ: dict[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    environ = dict(os.environ) if environ is None else environ
    try:
        tree = worktree_of(root, environ, args.task)
    except Refusal as refusal:
        return cli_conventions.refuse(refusal, args.json)
    return report(tree, args.check, args.verbose, args.json, f"atc policy render {args.task}")


def tree_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="policy_render.py", allow_abbrev=False, **cli_conventions.form(
        "Render the enforcement files of the tree DIR from its policies/hard-deny.yaml.",
        exit="0 rendered or current; 1 STOP, or drift under --check; 2 ERROR", see=SEE))
    parser.add_argument("tree", type=Path, metavar="DIR", help="the project tree")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when a file differs")
    cli_conventions.add_verbose(parser)
    cli_conventions.add_json(parser)
    args = parser.parse_args(argv)
    return report(args.tree, args.check, args.verbose, args.json, f"policy_render.py {args.tree}")


if __name__ == "__main__":
    sys.exit(tree_main())

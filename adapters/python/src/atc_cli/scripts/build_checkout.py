#!/usr/bin/env python3
"""`atc checkout`: a clean tree of a project at an exact commit on its `main`, for a build (docs/WORKFLOW.md § Build
trees).

By default a detached, locked worktree `worktrees/<project>/build+<sha7>/` of the project's repository: no task
record, no branch, so no commit can come from it. `--no-git` exports the commit with `git archive` into a scratch
under the ATC home instead. Either is registered in the scratch registry, so `atc test --list` shows it;
`--remove` removes it and its registry line.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from atc_testkit import checkout as testkit_checkout
from atc_testkit import locations, scratch

import cli_conventions
import managed_worktree

ROOT = Path(__file__).resolve().parents[5]
PREFIX = "build+"
SHORT = 7
MAIN = "refs/heads/main"


def _git(repo: str | Path, *args: str) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, check=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc checkout", allow_abbrev=False, **cli_conventions.form(
        "Make a clean tree of a project at a commit on its main, for a build; or --remove it.",
        "A detached, locked worktree worktrees/<project>/build+<sha7>/ with no task record and no branch, or with\n"
        "--no-git an exported tree in a scratch. Both are registered scratches (atc test --list).",
        exit="0 made, current or removed; 1 STOP (the commit is not on main, the path holds something else); 2 ERROR",
        see="docs/WORKFLOW.md § Build trees"))
    parser.add_argument("project", metavar="NAME", help="a registered project, or root for the governance repository")
    parser.add_argument("sha", metavar="SHA", help="a commit in the history of the project's main: 7 to 40 hex characters")
    parser.add_argument("--no-git", action="store_true", help="export the files into a scratch instead of a worktree")
    parser.add_argument("--remove", action="store_true", help="remove the tree this call names and its registry line")
    cli_conventions.add_json(parser)
    return parser


def resolve(repo: str, sha: str) -> str:
    """The full SHA of ``sha`` when it is a commit in the history of ``repo``'s `main`; a STOP otherwise."""
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
        raise cli_conventions.Refusal("SHA_INVALID", f"{sha!r} is not 7 to 40 hex characters",
                                      "name a commit SHA", cli_conventions.ERROR)
    found = _git(repo, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}").stdout.strip()
    if not found or _git(repo, "merge-base", "--is-ancestor", found, MAIN).returncode:
        raise cli_conventions.Refusal("NOT_ON_MAIN", f"{sha} is not a commit in the history of {repo}'s main",
                                      "name a commit on the project's main")
    return found


def worktree_head(path: Path) -> str | None:
    found = _git(path, "rev-parse", "--verify", "-q", "HEAD")
    top = _git(path, "rev-parse", "--show-toplevel")
    if found.returncode or top.returncode or not locations.same_path(top.stdout.strip(), path):
        return None
    return found.stdout.strip()


def make_worktree(project: managed_worktree.Project, commit: str, tmp: Path) -> tuple[str, Path]:
    path = Path(project.worktrees) / f"{PREFIX}{commit[:SHORT]}"
    if path.exists():
        if worktree_head(path) != commit:
            raise cli_conventions.Refusal("PATH_OCCUPIED", f"{path} exists and is not the build worktree of {commit}",
                                          "move it aside, or remove it with --remove")
        scratch.register(tmp, path, path, source=project.repo, commit=commit, project=project.name)
        return "CURRENT", path
    path.parent.mkdir(parents=True, exist_ok=True)
    added = _git(project.repo, "worktree", "add", "--quiet", "--detach", "--lock", str(path), commit)
    if added.returncode:
        raise cli_conventions.Refusal("GIT_FAILED", f"git worktree add: {(added.stderr or added.stdout).strip()}",
                                      "check the project's Git state", cli_conventions.ERROR)
    scratch.register(tmp, path, path, source=project.repo, commit=commit, project=project.name)
    return "CHECKOUT", path


def make_export(project: managed_worktree.Project, commit: str, tmp: Path) -> tuple[str, Path]:
    place = scratch.resolve_scratch(f"build-{project.name}-{commit[:SHORT]}", tmp)
    tree = place / testkit_checkout.TREE
    if tree.exists():
        return "CURRENT", tree
    scratch.register(tmp, place, tree, source=project.repo, commit=commit, project=project.name)
    testkit_checkout.export(Path(project.repo), commit, tree)
    return "CHECKOUT", tree


def remove(project: managed_worktree.Project, commit: str, tmp: Path, no_git: bool) -> tuple[str, Path]:
    if no_git:
        place = scratch.resolve_scratch(f"build-{project.name}-{commit[:SHORT]}", tmp)
        if not place.exists():
            return "ABSENT", place
        if scratch.clean([str(place)], say=lambda line: print(line, file=sys.stderr)):
            raise cli_conventions.Refusal("REMOVE_FAILED", f"{place} was not removed", "see the lines above")
        return "REMOVED", place
    path = Path(project.worktrees) / f"{PREFIX}{commit[:SHORT]}"
    if not path.exists():
        scratch.unregister(tmp, str(path))
        return "ABSENT", path
    if worktree_head(path) != commit:
        raise cli_conventions.Refusal("PATH_OCCUPIED", f"{path} is not the build worktree of {commit}; not removed",
                                      "remove it yourself after checking what it holds")
    _git(project.repo, "worktree", "unlock", str(path))
    removed = _git(project.repo, "worktree", "remove", "--force", str(path))
    if removed.returncode:
        raise cli_conventions.Refusal("GIT_FAILED", f"git worktree remove: {(removed.stderr or removed.stdout).strip()}",
                                      "close what holds the directory, then run it again", cli_conventions.ERROR)
    scratch.unregister(tmp, str(path))
    return "REMOVED", path


def main(argv: list[str] | None = None, root: Path = ROOT, environ: dict[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    environ = dict(os.environ) if environ is None else environ
    try:
        workspace = managed_worktree.load_workspace(root, environ)
        project = managed_worktree.project_named(workspace, args.project)
    except managed_worktree.Stop as stop:
        found = stop.result
        refusal = cli_conventions.Refusal(found.code, f"{found.target}: {found.detail}", found.hint())
        return cli_conventions.refuse(refusal, args.json)
    try:
        commit = resolve(project.repo, args.sha)
        tmp = locations.locate(environ).tmp
        if args.remove:
            verdict, path = remove(project, commit, tmp, args.no_git)
        else:
            verdict, path = (make_export if args.no_git else make_worktree)(project, commit, tmp)
    except cli_conventions.Refusal as refusal:
        return cli_conventions.refuse(refusal, args.json)
    except locations.Refusal as refusal:
        return cli_conventions.refuse(cli_conventions.Refusal(refusal.token, refusal.detail, None,
                                                              cli_conventions.ERROR), args.json)
    if args.json:
        print(json.dumps({"verdict": verdict, "project": project.name, "commit": commit, "path": str(path)}, indent=2))
    else:
        print(f"{verdict} {commit} {path}")
    return cli_conventions.OK


if __name__ == "__main__":
    sys.exit(main())

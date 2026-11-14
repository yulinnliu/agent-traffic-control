#!/usr/bin/env python3
"""`atc install`: install a release tag as this host's `atc` (docs/WORKFLOW.md § Installing a release).

The release copy is a detached, locked worktree of the workspace root repository at the tag, under
`~/.atc/releases/<tag>`; `atc` becomes an editable install of its `adapters/python`, so integrating into
`main` changes nothing the host runs until the Owner installs the next tag. The Owner runs it; agents run
only --check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import checkout_guard
import cli_conventions
import git_preflight as gp

ROOT = Path(__file__).resolve().parents[3]
TAG = re.compile(r"^v\d+\.\d+\.\d+$")
DISTRIBUTION = "agent-traffic-control"
# The console scripts uv writes into its bin directory; on Windows they are copied launchers.
LAUNCHERS = ("atc", "atc-maintain")
WINDOWS = os.name == "nt"
ASIDE = re.compile(r"^atc(?:-maintain)?\.exe\.\d+\.old$")


@dataclass(frozen=True)
class Plan:
    tag: str
    commit: str
    copy: Path
    create: bool
    current: bool
    old_version: str
    old_from: Path
    new_version: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc install", allow_abbrev=False, **cli_conventions.form(
        "Install a release tag as this host's atc.",
        "Creates the release copy ~/.atc/releases/TAG, a detached and locked worktree of the workspace root\n"
        "repository at TAG, unless it exists, then installs atc as an editable install of its adapters/python\n"
        "and reports the old and new version. The PreToolUse hook runs the new copy after atc guard --install.\n"
        "The Owner runs it from their own terminal; agents run only --check.",
        exit="0 installed, current or checked; 1 STOP (unknown tag, tag not on main, a release copy that does not "
             "match the tag); 2 ERROR",
        see="docs/WORKFLOW.md § Installing a release"))
    parser.add_argument("tag", metavar="TAG", help="a release tag vMAJOR.MINOR.PATCH on main")
    parser.add_argument("--check", action="store_true", help="report what would be installed; change nothing")
    cli_conventions.add_json(parser)
    return parser


def running() -> tuple[str, Path]:
    """The version and the checkout of the `atc` running this command."""
    from importlib import metadata

    import atc_cli
    try:
        found = metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        found = "unknown"
    return found, atc_cli.CHECKOUT


def copy_version(copy: Path) -> str:
    """The `atc` version a release copy carries, from its `adapters/python/pyproject.toml`."""
    try:
        with (copy / "adapters" / "python" / "pyproject.toml").open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def tag_version(root: Path, commit: str) -> str:
    shown = gp.git(root, "show", f"{commit}:adapters/python/pyproject.toml", check=False)
    if shown.returncode != 0:
        return "unknown"
    try:
        return str(tomllib.loads(shown.stdout)["project"]["version"])
    except (KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def copy_problem(root: Path, copy: Path, commit: str, tag: str) -> str | None:
    """Why an existing ``copy`` is not the release copy of ``commit``, or None."""
    if not copy.is_dir():
        return "it is not a directory"
    entry = next((e for e in gp.list_worktrees(root) if gp.same_path(Path(e["worktree"]), copy)), None)
    if entry is None:
        return f"it is not a worktree of {root}"
    if "detached" not in entry:
        return f"it has the branch {entry.get('branch', '').removeprefix('refs/heads/')} checked out"
    if entry.get("HEAD") != commit:
        return f"it is at {gp.short(entry.get('HEAD'))}, not at {tag} ({gp.short(commit)})"
    status = gp.git(copy, "status", "--porcelain", "--untracked-files=no", check=False)
    if status.returncode != 0:
        return f"its Git state cannot be read ({(status.stderr or '').strip()})"
    if status.stdout.strip():
        return "it has tracked modifications"
    return None


def make_plan(root: Path, tag: str, releases: Path, now: tuple[str, Path]) -> Plan:
    """What installing ``tag`` means on this host; raises Refusal when it cannot be installed."""
    if not TAG.match(tag):
        raise cli_conventions.Refusal("TAG_INVALID", f"{tag} is not a release tag vMAJOR.MINOR.PATCH",
                                      f'name one of git -C "{root}" tag -l "v*"', cli_conventions.ERROR)
    main_worktree = checkout_guard.main_worktree(root)
    if main_worktree is None or not gp.same_path(main_worktree, root):
        raise cli_conventions.Refusal("ROOT_NOT_MAIN_WORKTREE", f"{root} is not the main worktree of a repository",
                                      "run it from the workspace root, or from a clone, or name it with --root",
                                      cli_conventions.ERROR)
    commit = gp.rev(root, f"refs/tags/{tag}")
    if commit is None:
        raise cli_conventions.Refusal("TAG_UNKNOWN", f"{root} has no tag {tag}",
                                      f'git -C "{root}" fetch --tags origin, then run again')
    if not gp.is_ancestor(root, commit, "main"):
        raise cli_conventions.Refusal("TAG_NOT_ON_MAIN", f"{tag} ({gp.short(commit)}) is not in the history of main",
                                      "a release tag names a commit on main; name another tag")
    copy = releases / tag
    create = not os.path.lexists(copy)
    if not create:
        problem = copy_problem(root, copy, commit, tag)
        if problem:
            raise cli_conventions.Refusal(
                "RELEASE_COPY_INVALID", f"{copy}: {problem}",
                f'the Owner removes it (git -C "{root}" worktree remove --force --force "{copy}"), then runs again')
    old_version, old_from = now
    current = not create and gp.same_path(old_from, copy)
    new_version = tag_version(root, commit) if create else copy_version(copy)
    return Plan(tag, commit, copy, create, current, old_version, old_from, new_version)


def create_copy(root: Path, plan: Plan) -> None:
    plan.copy.parent.mkdir(parents=True, exist_ok=True)
    gp.git(root, "worktree", "add", "--detach", "--lock", "--reason", f"atc release {plan.tag}", str(plan.copy),
           plan.commit)


def set_launchers_aside(bin_dir: Path, pid: int) -> None:
    """Rename each launcher and put a copy back in its place, so uv overwrites a file no process runs.

    Windows refuses to overwrite a running executable, such as the `atc.exe` running this command, but lets
    it be renamed."""
    for name in LAUNCHERS:
        launcher = bin_dir / f"{name}.exe"
        if launcher.is_file():
            aside = bin_dir / f"{name}.exe.{pid}.old"
            os.replace(launcher, aside)
            try:
                shutil.copy2(aside, launcher)
            except OSError:
                os.replace(aside, launcher)  # the host keeps a working atc
                raise


def sweep_asides(bin_dir: Path) -> None:
    """Delete the launchers set aside by earlier runs; the one still running stays until the next run."""
    if not bin_dir.is_dir():
        return
    for entry in bin_dir.iterdir():
        if ASIDE.match(entry.name):
            try:
                entry.unlink()
            except OSError:
                pass


def install_tool(copy: Path, run=subprocess.run, windows: bool | None = None) -> cli_conventions.Refusal | None:
    """`uv tool install -e <copy>/adapters/python --force`, with the running launchers set aside on Windows."""
    adapter = copy / "adapters" / "python"
    manual = f'uv tool install -e "{adapter}" --force'
    uv = shutil.which("uv")
    if uv is None:
        return cli_conventions.Refusal("UV_MISSING", "uv is not on PATH", f"install uv, then run {manual}",
                                       cli_conventions.ERROR)
    bin_dir = None
    if WINDOWS if windows is None else windows:
        found = run([uv, "tool", "dir", "--bin"], capture_output=True, text=True, check=False)
        if found.returncode == 0 and found.stdout.strip():
            bin_dir = Path(found.stdout.strip())
            sweep_asides(bin_dir)
            try:
                set_launchers_aside(bin_dir, os.getpid())
            except OSError as exc:
                return cli_conventions.Refusal("INSTALL_FAILED", f"cannot set the running launcher aside ({exc})",
                                               f"run {manual} from a terminal where no atc runs",
                                               cli_conventions.ERROR)
    result = run([uv, "tool", "install", "-e", str(adapter), "--force"], capture_output=True, text=True, check=False)
    if bin_dir is not None:
        sweep_asides(bin_dir)
    if result.returncode != 0:
        lines = (result.stderr or "").strip().splitlines()
        return cli_conventions.Refusal("INSTALL_FAILED", lines[-1] if lines else f"uv exited {result.returncode}",
                                       f"run {manual} from a terminal where no atc runs", cli_conventions.ERROR)
    return None


def hook_installed(home: Path) -> bool:
    import generate_enforcement as ge
    return (home / ".atc" / "enforcement" / ge.HOOK_TABLE).is_file()


def main(argv: list[str] | None = None, root: Path = ROOT, releases: Path | None = None,
         now: tuple[str, Path] | None = None, home: Path | None = None, run=subprocess.run) -> int:
    args = build_parser().parse_args(argv)
    releases = releases or checkout_guard.releases_dir()
    home = home or Path.home()
    try:
        plan = make_plan(root, args.tag, releases, now or running())
    except cli_conventions.Refusal as refusal:
        return cli_conventions.refuse(refusal, args.json)
    except gp.GitError as exc:
        return cli_conventions.refuse(cli_conventions.Refusal("GIT_FAILED", str(exc), "check the repository's Git "
                                                              "state", cli_conventions.ERROR), args.json)
    fields = {"tag": plan.tag, "commit": plan.commit, "copy": str(plan.copy), "created": False,
              "old": {"version": plan.old_version, "from": str(plan.old_from)},
              "new": {"version": plan.new_version, "from": str(plan.copy)}}
    if plan.current:
        verdict, line = "CURRENT", f"CURRENT atc {plan.new_version} ({plan.tag}) from {plan.copy}"
    elif args.check:
        action = f"would create {plan.copy} and install" if plan.create else f"would install from {plan.copy}"
        verdict, line = "CHECK", (f"CHECK atc {plan.new_version} ({plan.tag}, {gp.short(plan.commit)}): {action}; "
                                  f"now {plan.old_version} from {plan.old_from}")
    else:
        try:
            if plan.create:
                create_copy(root, plan)
                fields["created"] = True
        except gp.GitError as exc:
            return cli_conventions.refuse(cli_conventions.Refusal(
                "COPY_FAILED", str(exc), f"read git's message, fix it, then run atc install {plan.tag} again",
                cli_conventions.ERROR), args.json, **fields)
        refusal = install_tool(plan.copy, run)
        if refusal is not None:
            return cli_conventions.refuse(refusal, args.json, **fields)
        hook = "; the hook runs it after atc guard --install" if hook_installed(home) else ""
        verdict, line = "INSTALLED", (f"INSTALLED atc {copy_version(plan.copy)} ({plan.tag}) from {plan.copy}; "
                                      f"was {plan.old_version} from {plan.old_from}{hook}")
        fields["new"]["version"] = copy_version(plan.copy)
    if args.json:
        print(json.dumps({"verdict": verdict, **fields}, indent=2))
    else:
        print(line)
    return cli_conventions.OK


if __name__ == "__main__":
    sys.exit(main())

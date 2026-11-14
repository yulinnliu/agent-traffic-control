#!/usr/bin/env python3
"""`atc init`, `atc add` and `atc remove`: set up this checkout as a workspace root and bring projects under it.

`init` writes `workspace.yaml` with this checkout as `workspace_root`, creates `projects/`, `worktrees/` and
`state/`, and prints how to put the `atc` command on PATH. `add` registers one project: clones `--url` into
`projects/NAME` or expects a checkout there, appends the entry to `workspace.yaml`, creates its worktree and
state directories, installs the requested opt-in skills into the harness directories its `skill_harnesses`
selects, runs sync's apply step and validates. `remove` drops a project's entry and its `skill_harnesses`
setting and an empty `worktrees/NAME/`, and deletes nothing else. Project-owned files are never rewritten.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

import cli_conventions
import validate_workspace
import sync_project

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parent
NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
URL = re.compile(r"^(https?://|git@|ssh://)")
GOVERNANCE_SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")

MAP_HEADER = """# Machine-specific root map for this checkout. Absolute paths live here and
# nowhere else; never committed. Sub-paths under `projects:` are relative to
# `workspace_root`. Repository, disposable worktrees and persistent state have
# independent lifecycles; `repo` and `state` are never nested in one another.
"""


def origin_slug(root: Path) -> str | None:
    try:
        top = subprocess.check_output(["git", "-C", str(root), "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL).strip()
        if Path(top).resolve() != root.resolve():
            return None
        url = subprocess.check_output(["git", "-C", str(root), "remote", "get-url", "origin"], text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    match = re.search(r"[:/]([^/:]+/[^/]+?)(\.git)?$", url)
    return match.group(1) if match else None


def init(root: Path, governance_source: str | None, force: bool, environ: dict[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    target = root / "workspace.yaml"
    if target.exists() and not force:
        return _stop("WORKSPACE_EXISTS", f"{target} exists", "pass --force to rewrite it")
    source = governance_source or origin_slug(root)
    if not source:
        return _error("GOVERNANCE_SOURCE_UNKNOWN", "cannot infer governance_source from the origin remote",
                      "pass --governance-source OWNER/REPO")
    if not GOVERNANCE_SOURCE.fullmatch(source):
        return _error("GOVERNANCE_SOURCE_INVALID", f"{source!r} is not OWNER/REPO",
                      "pass a GitHub owner and repository name")
    text =f"{MAP_HEADER}\nversion: 1\n\ngovernance_source: {source}\n\nworkspace_root: {root.as_posix()}\n\nprojects: {{}}\n"
    target.write_text(text, encoding="utf-8", newline="\n")
    for name in ("projects", "worktrees", "state"):
        (root / name).mkdir(exist_ok=True)
    print(f"INITIALIZED workspace.yaml (governance_source {source})")
    if not shutil.which("atc"):
        print(f'  put atc on PATH: uv tool install -e "{root / "adapters" / "python"}"')
    return 0


def _stop(token: str, detail: str, hint: str | None = None) -> int:
    """A refusal by the state found, before anything changed (shared/conformance/CLI.md § Exit codes)."""
    return cli_conventions.refuse(cli_conventions.Refusal(token, detail, hint))


def _error(token: str, detail: str, hint: str | None = None) -> int:
    """Invalid input or a failed step (shared/conformance/CLI.md § Exit codes)."""
    return cli_conventions.refuse(cli_conventions.Refusal(token, detail, hint, cli_conventions.ERROR))


def register(root: Path, name: str) -> dict:
    path = root / "workspace.yaml"
    if not path.is_file():
        raise SystemExit(_stop("WORKSPACE_MISSING", f"{path} is missing", "atc init"))
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    projects = data.get("projects") or {}
    entry = {"repo": f"projects/{name}", "worktrees": f"worktrees/{name}", "state": f"state/{name}-data"}
    if name in projects:
        return projects[name]
    text = path.read_text(encoding="utf-8")
    block = f"  {name}:\n    repo:      {entry['repo']}\n    worktrees: {entry['worktrees']}\n    state:     {entry['state']}\n"
    if re.search(r"^projects:\s*\{\}\s*$", text, re.MULTILINE):
        text = re.sub(r"^projects:\s*\{\}\s*$", "projects:\n" + block.rstrip("\n"), text, flags=re.MULTILINE)
    elif re.search(r"^projects:\s*$", text, re.MULTILINE):
        text = text.rstrip("\n") + "\n" + block
    else:
        text = text.rstrip("\n") + "\nprojects:\n" + block
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"registered {name} in {path}")
    return entry


def install_opt_in(root: Path, project: Path, skills: list[str],
                   tool_dirs: tuple[str, ...] = validate_workspace.TOOL_DIRS) -> None:
    for skill in skills:
        if not (root / "shared" / "skills" / skill / "SKILL.md").is_file():
            raise SystemExit(_error("SKILL_UNKNOWN", skill, "name a skill under shared/skills/"))
        for tool_dir in tool_dirs:
            target = project / tool_dir / "skills" / skill / "SKILL.md"
            if target.exists():
                continue
            if sync_project.holds_files(target.parent):
                raise SystemExit(_stop("COLLISION", f"{target.relative_to(project).as_posix()}: "
                                                    f"{sync_project.UNOWNED_DIRECTORY}",
                                       "move the directory aside, then run it again"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(sync_project.validator.render_adapter(root, skill, tool_dir), encoding="utf-8", newline="\n")
            sync_project.apply_plan(sync_project.supporting_plan(root, skill, target.parent), "sync")


def add(root: Path, name: str, url: str | None, skills: list[str], verbose: bool = False) -> int:
    if not NAME.match(name):
        return _error("PROJECT_NAME_INVALID", name, "use a lowercase hyphenated name")
    reserved = validate_workspace.reserved_root_problem(name, f"worktrees/{name}")
    if reserved:
        return _error("PROJECT_NAME_RESERVED", f"{name}: {reserved}", "choose another name")
    project = root / "projects" / name
    if url and URL.match(url):
        if project.exists():
            return _stop("PROJECT_EXISTS", f"{project} exists", "drop --url to register it as it is")
        cloned = subprocess.run(["git", "clone", "--quiet", url, str(project)], capture_output=True, text=True)
        if cloned.returncode != 0:
            return _error("CLONE_FAILED", f"git clone {url}: {cloned.stderr.strip()}", "check the URL and access")
    elif url:
        return _error("URL_INVALID", f"--url {url} is not a URL",
                      f"place an existing checkout at projects/{name} and run it without --url")
    if not (project / ".git").exists():
        return _stop("PROJECT_NOT_A_REPOSITORY", f"projects/{name} is not a git repository",
                     "clone it there, or pass --url")
    entry = register(root, name)
    for key in ("worktrees", "state"):
        (root / entry[key]).mkdir(parents=True, exist_ok=True)
    install_opt_in(root, project, skills,
                   validate_workspace.load_skill_harnesses(root / "workspace.yaml").get(name, validate_workspace.TOOL_DIRS))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        applied = sync_project.apply(name, root=root)
    if verbose or applied:
        sys.stdout.write(buffer.getvalue())
    if applied:
        return applied
    problems = validate_workspace.project_problems(root, [name])
    if problems:
        print(*(f"PROBLEM {p}" for p in problems), sep="\n")
        return _stop("PROJECT_PROBLEMS", f"{len(problems)} found in {name}",
                     "each PROBLEM line names the file and the fix")
    print(f"ADDED {name} projects/{name}")
    return cli_conventions.OK


def _block_end(lines: list[str], start: int) -> int:
    """The index after the indented block that starts at ``start``."""
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def without_entry(text: str, section: str, name: str) -> str:
    """``text`` with the entry ``name`` of the top-level mapping ``section`` removed; an emptied section reads
    `{}`."""
    lines = text.split("\n")
    top = next((i for i, line in enumerate(lines) if re.match(rf"^{re.escape(section)}:\s*$", line)), None)
    if top is None:
        return text
    end = _block_end(lines, top)
    entry = next((i for i in range(top + 1, end) if re.match(rf"^\s+{re.escape(name)}:(\s|$)", lines[i])), None)
    if entry is None:
        return text
    del lines[entry:_block_end(lines, entry)]
    if not any(line.strip() and not line.lstrip().startswith("#") for line in lines[top + 1:_block_end(lines, top)]):
        lines[top] = f"{section}: {{}}"
    return "\n".join(lines)


def remove(root: Path, name: str, check: bool, answer=input) -> int:
    path = root / "workspace.yaml"
    if not path.is_file():
        return _stop("WORKSPACE_MISSING", f"{path} is missing", "atc init")
    projects = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("projects") or {}
    if name not in projects:
        return _error("PROJECT_UNKNOWN", f"{name} is not registered in workspace.yaml",
                      f"registered: {', '.join(sorted(projects)) or 'none'}")
    entry = projects[name]
    worktrees = root / entry.get("worktrees", f"worktrees/{name}")
    held = sorted(child.name for child in worktrees.iterdir()) if worktrees.is_dir() else []
    if held:
        return _stop("WORKTREES_PRESENT", f"{worktrees.relative_to(root).as_posix()}/ holds {', '.join(held)}",
                     f"close each task first: atc task close ID (atc task list --project {name} -v)")
    text = path.read_text(encoding="utf-8")
    harnesses = validate_workspace.SKILL_HARNESSES
    changed = without_entry(without_entry(text, "projects", name), harnesses, name)
    lines = [f"  drop projects.{name} from workspace.yaml",
             *([f"  drop {harnesses}.{name}"] if without_entry(text, harnesses, name) != text else []),
             *([f"  remove the empty {worktrees.relative_to(root).as_posix()}/"] if worktrees.is_dir() else []),
             *(f"  keep {entry[key]}" for key in ("repo", "state") if entry.get(key)),
             "  keep the project's ATC-managed files and .atc/tasks"]
    if check:
        print(f"CHECK remove {name}", *lines, sep="\n")
        return cli_conventions.OK
    print(*lines, sep="\n")
    try:
        reply = answer(f"remove {name}? [y/N] ")
    except EOFError:
        reply = ""
    if reply.strip().lower() not in ("y", "yes"):
        return _stop("DECLINED", f"{name} was not removed", f"run atc remove {name} again and answer y")
    path.write_text(changed, encoding="utf-8", newline="\n")
    if worktrees.is_dir():
        worktrees.rmdir()
    print(f"REMOVED {name}")
    return cli_conventions.OK


def build_parser(command: str) -> argparse.ArgumentParser:
    see = "README.md § Installation"
    if command == "init":
        parser = argparse.ArgumentParser(prog="atc init", allow_abbrev=False, **cli_conventions.form(
            "Write workspace.yaml with this checkout as workspace_root and create projects/, worktrees/ and state/.",
            exit="0 written; 1 workspace.yaml exists (without --force); 2 ERROR", see=see))
        parser.add_argument("--governance-source", metavar="OWNER/REPO",
                            help="the repository governance is read from (default: this checkout's origin)")
        parser.add_argument("--force", action="store_true", help="overwrite an existing workspace.yaml")
        return parser
    if command == "add":
        parser = argparse.ArgumentParser(prog="atc add", allow_abbrev=False, **cli_conventions.form(
            "Register one project, create the files ATC owns in it and validate the result.",
            exit="0 registered and its files created; 1 STOP from sync or validation; 2 ERROR (invalid name or URL, "
                 "a failed clone)", see=see))
        parser.add_argument("name", metavar="NAME", help="the project's name in workspace.yaml and under projects/")
        parser.add_argument("--url", metavar="URL", help="git URL to clone into projects/NAME")
        parser.add_argument("--skills", default="", metavar="NAME,...", help="comma-separated opt-in skills to install")
        cli_conventions.add_verbose(parser)
        return parser
    parser = argparse.ArgumentParser(prog="atc remove", allow_abbrev=False, **cli_conventions.form(
        "Unregister a project after a y/N question: drop its workspace.yaml entry and skill_harnesses setting and "
        "an empty worktrees/NAME/.",
        "Never deletes projects/NAME/, its state directory, the project's ATC-managed files or its task records.",
        exit="0 removed, or under --check listed; 1 STOP (worktrees present, declined); 2 ERROR", see=see))
    parser.add_argument("name", metavar="NAME", help="the registered project")
    parser.add_argument("--check", action="store_true", help="list what the run would change; change nothing")
    return parser


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else ""
    if command not in ("init", "add", "remove"):
        print("usage: atc init | add | remove ...", file=sys.stderr)
        return cli_conventions.ERROR
    args = build_parser(command).parse_args(argv[1:])
    if command == "init":
        return init(root, args.governance_source, args.force)
    if command == "add":
        return add(root, args.name, args.url, [s for s in args.skills.split(",") if s], args.verbose)
    return remove(root, args.name, args.check)


if __name__ == "__main__":
    sys.exit(main())

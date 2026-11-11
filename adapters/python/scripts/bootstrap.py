#!/usr/bin/env python3
"""Set up this checkout as a workspace root and bring projects under it.

`init` writes `workspace.yaml` with this checkout as `workspace_root`, creates `projects/`, `worktrees/` and
`state/`, and prints how to put the `atc` command on PATH. `add` registers one project: clones `--url` into
`projects/NAME` or expects a checkout there, appends the entry to `workspace.yaml`, creates its worktree and
state directories, installs the requested opt-in skills, runs onboarding's apply step and validates. `status`
runs doctor and `atc onboard --check --all`.
Project-owned files are never rewritten.
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
import doctor
import validate_workspace
import onboard_project

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
        return _stop("WORKSPACE_EXISTS", f"{target} exists; pass --force to rewrite it")
    source = governance_source or origin_slug(root)
    if not source:
        return _error("GOVERNANCE_SOURCE_UNKNOWN", "cannot infer governance_source from the origin remote; "
                      "pass --governance-source OWNER/REPO")
    if not GOVERNANCE_SOURCE.fullmatch(source):
        return _error("GOVERNANCE_SOURCE_INVALID", f"{source!r}: use OWNER/REPO, a GitHub owner and repository name")
    text =f"{MAP_HEADER}\nversion: 1\n\ngovernance_source: {source}\n\nworkspace_root: {root.as_posix()}\n\nprojects: {{}}\n"
    target.write_text(text, encoding="utf-8", newline="\n")
    for name in ("projects", "worktrees", "state"):
        (root / name).mkdir(exist_ok=True)
    print(f"wrote {target} (governance_source: {source}, workspace_root: {root.as_posix()})")
    if shutil.which("atc"):
        print(f"atc command: {shutil.which('atc')}")
    else:
        print("put the atc command on PATH for the skills that call it:")
        print(f'  uv tool install -e "{root / "adapters" / "python"}"')
    return 0


def _stop(token: str, detail: str) -> int:
    """A refusal by the state found, before anything changed (shared/conformance/CLI.md § Exit codes)."""
    print(f"STOP {token}: {detail}", file=sys.stderr)
    return cli_conventions.STOP


def _error(token: str, detail: str) -> int:
    """Invalid input or a failed step (shared/conformance/CLI.md § Exit codes)."""
    print(f"ERROR {token} {detail}", file=sys.stderr)
    return cli_conventions.ERROR


def register(root: Path, name: str) -> dict:
    path = root / "workspace.yaml"
    if not path.is_file():
        raise SystemExit(_stop("WORKSPACE_MISSING", f"{path} missing; run `atc bootstrap init` first"))
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    projects = data.get("projects") or {}
    entry = {"repo": f"projects/{name}", "worktrees": f"worktrees/{name}", "state": f"state/{name}-data"}
    if name in projects:
        print(f"{name} already registered in {path}")
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


def install_opt_in(root: Path, project: Path, skills: list[str]) -> None:
    for skill in skills:
        if not (root / "shared" / "skills" / skill / "SKILL.md").is_file():
            raise SystemExit(_error("SKILL_UNKNOWN", f"{skill}; see shared/skills/"))
        metadata = onboard_project.canonical_skills(root)[skill]
        for tool_dir in onboard_project.validator.adapter_dirs(metadata):
            target = project / tool_dir / "skills" / skill / "SKILL.md"
            if target.exists():
                continue
            if onboard_project.holds_files(target.parent):
                raise SystemExit(_stop("COLLISION", f"{target.relative_to(project).as_posix()}: "
                                                    f"{onboard_project.UNOWNED_DIRECTORY}"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(onboard_project.validator.render_adapter(root, skill, tool_dir), encoding="utf-8", newline="\n")
            onboard_project.apply_plan(onboard_project.supporting_plan(root, skill, target.parent), "sync")
            print(f"installed opt-in adapter {target.relative_to(project).as_posix()}")


def validate(root: Path) -> int:
    cmd = [sys.executable, str(SCRIPTS / "validate_workspace.py"), "--projects", "--root", str(root)]
    env = {k: v for k, v in os.environ.items() if k != "ATC_ROOT"}
    print("$ validate_workspace.py --projects")
    return subprocess.call(cmd, cwd=root, env=env)


def add(root: Path, name: str, url: str | None, skills: list[str]) -> int:
    if not NAME.match(name):
        return _error("PROJECT_NAME_INVALID", f"{name}: use a lowercase hyphenated name")
    reserved = validate_workspace.reserved_root_problem(name, f"worktrees/{name}")
    if reserved:
        return _error("PROJECT_NAME_RESERVED", f"{name}: {reserved}")
    project = root / "projects" / name
    if url and URL.match(url):
        if project.exists():
            return _stop("PROJECT_EXISTS", f"{project} exists; drop --url to register it as is")
        print(f"$ git clone {url} {project}")
        if subprocess.call(["git", "clone", url, str(project)]) != 0:
            return _error("CLONE_FAILED", f"git clone {url} failed")
    elif url:
        return _error("URL_INVALID", f"--url must be a URL; place an existing checkout at {project} and rerun "
                      "without --url")
    if not (project / ".git").exists():
        return _stop("PROJECT_NOT_A_REPOSITORY", f"{project} is not a git repository")
    entry = register(root, name)
    for key in ("worktrees", "state"):
        (root / entry[key]).mkdir(parents=True, exist_ok=True)
    install_opt_in(root, project, skills)
    print(f"$ onboard apply {name}")
    applied = onboard_project.apply(name, root=root)
    if applied:
        return applied
    return validate(root)


def status(root: Path, as_json: bool = False) -> int:
    checks = doctor.diagnose(root)
    errors = sum(check.status == "ERROR" for check in checks)
    if as_json:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = onboard_project.main(["--check", "--all", "--json"], root=root)
        code = code or (cli_conventions.STOP if errors else cli_conventions.OK)
        print(json.dumps({"verdict": "NOT_READY" if code else "READY",
                          "doctor": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks],
                          "onboard": json.loads(buffer.getvalue())}, indent=2))
        return code
    for check in checks:
        print(f"{check.status:15} {check.name}: {check.detail}")
    print(f"\nDoctor: {errors} error(s).\n$ atc onboard --check --all")
    return onboard_project.main(["--check", "--all"], root=root) or (1 if errors else 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc bootstrap", allow_abbrev=False, **cli_conventions.form(
        "Set up this checkout as a workspace root and bring projects under it.",
        exit="0 OK; 1 STOP or drift; 2 ERROR (usage, invalid input, a failed step)", see="README.md § Installation"))
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    p_init = sub.add_parser("init", allow_abbrev=False, help="write workspace.yaml and the workspace directories",
                            **cli_conventions.form("Write workspace.yaml with this checkout as workspace_root and "
                                                   "create projects/, worktrees/ and state/.",
                                                   exit="0 written; 1 workspace.yaml exists (without --force); "
                                                        "2 ERROR", see="README.md § Installation"))
    p_init.add_argument("--governance-source", metavar="OWNER/REPO",
                        help="the repository governance is read from (default: this checkout's origin)")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing workspace.yaml")
    p_add = sub.add_parser("add", allow_abbrev=False, help="register one project and onboard it",
                           **cli_conventions.form("Register one project, onboard it and validate the result.",
                                                  exit="0 registered and onboarded; 1 STOP from onboard or "
                                                       "validate; 2 ERROR (invalid name or URL, a failed clone)",
                                                  see="README.md § Installation"))
    cli_conventions.add_project(p_add, help="the project's name in workspace.yaml and under projects/", required=True)
    p_add.add_argument("--url", metavar="URL", help="git URL to clone into projects/NAME")
    p_add.add_argument("--skills", default="", metavar="NAME,...", help="comma-separated opt-in skills to install")
    p_status = sub.add_parser("status", allow_abbrev=False, help="run doctor and atc onboard --check --all",
                              **cli_conventions.form("Run doctor and `atc onboard --check --all`; changes nothing.",
                                                     exit="0 ready and current; 1 an error or drift; 2 usage error",
                                                     see="README.md § Keeping installed files current"))
    cli_conventions.add_json(p_status)
    return parser


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        return init(root, args.governance_source, args.force)
    if args.command == "add":
        return add(root, args.project, args.url, [s for s in args.skills.split(",") if s])
    return status(root, args.json)


if __name__ == "__main__":
    sys.exit(main())

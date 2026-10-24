#!/usr/bin/env python3
"""Set up this checkout as a workspace root and bring projects under it.

    bootstrap.py init   [--governance-source OWNER/REPO] [--force]
    bootstrap.py add    NAME [--repo URL] [--skills a,b]
    bootstrap.py status

`init` writes `workspace.yaml` with this checkout as `workspace_root`,
creates `projects/`, `worktrees/` and `state/`, and prints how to put the
`atc` command on PATH. No environment variable is needed.

`add` registers one project: clones `--repo` into `projects/NAME`, or expects
a checkout already there; appends the entry to `workspace.yaml`; creates its
worktree and state directories; installs the requested opt-in skill
adapters; then runs onboarding `--apply` so the project receives `CLAUDE.md`,
the policy block in `AGENTS.md` and every baseline adapter, and validates the
result. Project-owned files are never rewritten.

`sync [NAME] [--prune]` converges one or every project's workspace-owned
files: creates missing baseline adapters, rewrites drifted managed ones,
with `--prune` deletes managed adapters whose canonical skill was renamed or
removed, then validates. Project-owned files are never touched.

`status` runs doctor and a dry onboarding check across every registered
project.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

import doctor
import onboard_project

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parent
NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
URL = re.compile(r"^(https?://|git@|ssh://)")

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
        print(f"{target} exists; pass --force to rewrite it")
        return 1
    source = governance_source or origin_slug(root)
    if not source:
        print("cannot infer governance_source from the origin remote; pass --governance-source OWNER/REPO")
        return 1
    text = f"{MAP_HEADER}\nversion: 1\n\ngovernance_source: {source}\n\nworkspace_root: {root.as_posix()}\n\nprojects: {{}}\n"
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


def register(root: Path, name: str) -> dict:
    path = root / "workspace.yaml"
    if not path.is_file():
        print(f"{path} missing; run `bootstrap.py init` first")
        raise SystemExit(1)
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
            print(f"unknown skill {skill}; see shared/skills/")
            raise SystemExit(1)
        metadata = onboard_project.canonical_skills(root)[skill]
        for tool_dir in onboard_project.validator.adapter_dirs(metadata):
            target = project / tool_dir / "skills" / skill / "SKILL.md"
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(onboard_project.validator.render_adapter(root, skill, tool_dir), encoding="utf-8", newline="\n")
            print(f"installed opt-in adapter {target.relative_to(project).as_posix()}")


def validate(root: Path) -> int:
    cmd = [sys.executable, str(SCRIPTS / "validate_workspace.py"), "--projects", "--root", str(root)]
    env = {k: v for k, v in os.environ.items() if k != "ATC_ROOT"}
    print("$ validate_workspace.py --projects")
    return subprocess.call(cmd, cwd=root, env=env)


def add(root: Path, name: str, repo: str | None, skills: list[str]) -> int:
    if not NAME.match(name):
        print(f"{name}: use a lowercase hyphenated name")
        return 1
    project = root / "projects" / name
    if repo and URL.match(repo):
        if project.exists():
            print(f"{project} exists; drop --repo to register it as is")
            return 1
        print(f"$ git clone {repo} {project}")
        if subprocess.call(["git", "clone", repo, str(project)]) != 0:
            return 1
    elif repo:
        print(f"--repo must be a URL; place an existing checkout at {project} and rerun without --repo")
        return 1
    if not (project / ".git").exists():
        print(f"{project} is not a git repository")
        return 1
    entry = register(root, name)
    for key in ("worktrees", "state"):
        (root / entry[key]).mkdir(parents=True, exist_ok=True)
    install_opt_in(root, project, skills)
    print(f"$ onboard_project.py --apply {name}")
    if onboard_project.main(["--apply", name], root=root) != 0:
        return 1
    return validate(root)


def sync(root: Path, name: str | None, prune: bool) -> int:
    args = ["--sync", *(["--prune"] if prune else []), *(["--all"] if name is None else [name])]
    print("$ onboard_project.py " + " ".join(args))
    code = onboard_project.main(args, root=root)
    if code == 2:
        return code
    return validate(root) or code


def status(root: Path) -> int:
    checks = doctor.diagnose(root)
    for check in checks:
        print(f"{check.status:15} {check.name}: {check.detail}")
    errors = sum(check.status == "ERROR" for check in checks)
    print(f"\nDoctor: {errors} error(s).\n$ onboard_project.py --check --all")
    return onboard_project.main(["--check", "--all"], root=root) or (1 if errors else 0)


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("--governance-source", metavar="OWNER/REPO")
    p_init.add_argument("--force", action="store_true")
    p_add = sub.add_parser("add")
    p_add.add_argument("name")
    p_add.add_argument("--repo", help="git URL to clone into projects/NAME")
    p_add.add_argument("--skills", default="", help="comma-separated opt-in skills to install")
    p_sync = sub.add_parser("sync")
    p_sync.add_argument("name", nargs="?")
    p_sync.add_argument("--prune", action="store_true", help="delete orphaned managed adapters")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.command == "init":
        return init(root, args.governance_source, args.force)
    if args.command == "add":
        return add(root, args.name, args.repo, [s for s in args.skills.split(",") if s])
    if args.command == "sync":
        return sync(root, args.name, args.prune)
    return status(root)


if __name__ == "__main__":
    sys.exit(main())

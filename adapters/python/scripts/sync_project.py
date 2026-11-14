#!/usr/bin/env python3
"""`atc sync`: check or write the files Agent Traffic Control owns inside a project.

What sync delivers is what the sync target's `atc-manifest.yaml` names (docs/LAYOUT.md § The manifest):
`owned` files rewritten on drift (`.atc/README.md`, `.atc/governance/`), the `pointer-line` in `AGENTS.md`
(created minimal when absent), `create-only` files (`CLAUDE.md`), and one managed adapter per baseline skill
of the `skill-render` source in each harness directory `skill_harnesses` selects; sync also writes
`.atc/atc.lock`. `.atc/tasks/` is the project's; an adapter without an ownership marker is project-owned;
neither is ever rewritten. A revision without a manifest is not a sync target (`NO_MANIFEST`).

`atc sync [NAME...]` renders the sync target, read from the content repository (`content.py`): the
installed release, else the latest release tag on its `main`, or the SHA the Owner names with `--revision`
(CORE.md § Effective authority and revocation). It prunes managed adapters with no canonical skill or in a
harness directory the project no longer selects, commits what it writes on each project's `main` and
records the commit in `integrations.jsonl`; `--check` writes nothing and also validates the projects. The
workspace directory's own copies are checked by `workspace_adapter_plan`. `atc-maintain sync --root`
(`root_main`) renders the adapters of the checkout it runs in and commits nothing. The rules:
GIT_WORKFLOW.md "Governance sync commits". `apply()` creates a new project's missing files from the same
target, updates nothing and commits nothing (`atc add`).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

import content
import manifest as mf
import validate_workspace as validator
import verification_section
import cli_conventions
from integration_slot import IntegrationSlot

ROOT = Path(__file__).resolve().parents[3]
MAIN = "main"
# The `--json` result: one entry per checkout `run` plans, None outside `--json` (shared/conformance/CLI.md § Output).
EVENTS: list[dict] | None = None
# The `--json` result's `target`: the revision projects were rendered from, None until resolved.
TARGET_EVENT: dict | None = None
POINTER_LINE = re.compile(rf"^{re.escape(validator.POINTER_MARKER)}.*$", re.MULTILINE)


@dataclass(frozen=True)
class PlanItem:
    action: str
    path: Path
    detail: str
    content: str | None = None


@dataclass(frozen=True)
class Target:
    """What sync renders and locks: ``tree`` holds the sources of ``revision``, read from the Git repository
    ``repo`` (the tree itself when None); ``manifest`` is the tree's, loaded once."""
    tree: Path
    revision: str
    tag: str | None = None
    named: bool = False
    manifest: mf.Manifest | None = None
    repo: Path | None = None

    def delivery(self) -> mf.Manifest:
        return self.manifest or mf.load(self.tree)


def load_projects(root: Path = ROOT) -> dict[str, dict]:
    validator.FAILURES.clear()
    workspace_file = root / "workspace.yaml"
    _, projects = validator.load_and_check_workspace_yaml(workspace_file)
    if validator.FAILURES:
        raise ValueError("; ".join(validator.FAILURES))
    return projects


def canonical_skills(root: Path = ROOT) -> dict[str, dict]:
    validator.FAILURES.clear()
    original = validator.ROOT, validator.TREE
    try:
        validator.ROOT, validator.TREE = root, None
        canonical = validator.load_canonical_skills()
    finally:
        validator.ROOT, validator.TREE = original
    if validator.FAILURES:
        raise ValueError("; ".join(validator.FAILURES))
    return {name: canonical[name] for name in sorted(canonical)}


def managed_adapter(path: Path, skill: str) -> bool:
    """Whether ``path`` is ours to rewrite: the ownership marker and the
    canonical identity of ``skill``. Anything else about the file may be
    stale; sync re-renders it."""
    if not path.exists():
        return False
    validator.FAILURES.clear()
    metadata, _ = validator.parse_frontmatter(path)
    validator.FAILURES.clear()
    return (
        isinstance(metadata, dict)
        and validator.is_managed(metadata)
        and metadata.get("canonical") == skill
        and metadata.get("name") == skill
    )


def minimal_agents_md(line: str) -> str:
    """The AGENTS.md sync creates in a project that has none: a title, the pointer line and where project rules go."""
    return f"# AGENTS.md\n\n{line}\n\nThis project's own rules go in this file, below the line above.\n"


def pointer_plan(project_path: Path, line: str) -> PlanItem:
    """The pointer line in AGENTS.md: before the first section, at the end of a file with none, or in a minimal
    AGENTS.md when the project has none."""
    agents_md = project_path / "AGENTS.md"
    if not agents_md.exists():
        return PlanItem("create", agents_md, "minimal AGENTS.md with the pointer line", minimal_agents_md(line))
    text = agents_md.read_text(encoding="utf-8")
    current = POINTER_LINE.search(text)
    if current:
        if current.group(0) == line:
            return PlanItem("skip", agents_md, "pointer line is current")
        return PlanItem("update", agents_md, "pointer line drift", text[: current.start()] + line + text[current.end() :])
    if any(token in text for token in ("shared/governance/INDEX.md", ".atc/governance/INDEX.md", "AGENT_DECISION_POLICY.md", "GIT_LIFECYCLE.md")):
        return PlanItem(
            "collision",
            agents_md,
            "governance reference exists without ownership markers; refusing to modify",
        )
    heading = re.search(r"^## .+$", text, re.MULTILINE)
    if not heading:
        return PlanItem("create", agents_md, "append the pointer line (no section heading)",
                        text.rstrip("\n") + ("\n\n" if text.strip() else "") + line + "\n")
    updated = text[: heading.start()] + line + "\n\n" + text[heading.start() :]
    return PlanItem("create", agents_md, "insert the pointer line", updated)


def governance_source(root: Path) -> str:
    """The locked source name: workspace.yaml's governance_source, else the
    origin remote's owner/repo, else ``unknown``."""
    workspace_file = root / "workspace.yaml"
    if workspace_file.is_file():
        try:
            doc = yaml.safe_load(workspace_file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            doc = {}
        if isinstance(doc, dict) and isinstance(doc.get("governance_source"), str):
            return doc["governance_source"]
    try:
        url = subprocess.check_output(["git", "-C", str(root), "remote", "get-url", "origin"], text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    match = re.search(r"[:/]([^/:]+/[^/]+?)(\.git)?$", url)
    return match.group(1) if match else "unknown"


def governance_revision(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip() or "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def atc_plan(project_path: Path, root: Path, found: mf.Manifest | None = None) -> list[PlanItem]:
    """The manifest's `owned` files (the bootloader and the vendored governance), rendered from the tree
    ``root``; collisions alone when `.atc/` holds what sync does not own."""
    found = found or mf.load(root)
    plan: list[PlanItem] = []
    for stray in validator.atc_strays(project_path, tree=root):
        plan.append(PlanItem("collision", project_path / stray, "not a file sync writes; .atc/ holds README.md, atc.lock, governance/ and tasks/"))
    try:
        validator.read_lock(project_path)
    except ValueError as exc:
        plan.append(PlanItem("collision", project_path / validator.ATC_LOCK, f"{exc}; refusing to manage .atc/"))
    if plan:
        return plan
    for relative, destination in found.files(mf.OWNED):
        source = root / relative
        if not source.is_file():
            raise ValueError(f"{source}: missing source of an owned item in {mf.FILE}")
        expected = source.read_text(encoding="utf-8")
        target = project_path / destination
        if not target.exists():
            plan.append(PlanItem("create", target, f"owned: {relative}", expected))
        elif validator.normalise(target.read_text(encoding="utf-8")) != validator.normalise(expected):
            plan.append(PlanItem("update", target, f"owned drift: {relative}", expected))
        else:
            plan.append(PlanItem("skip", target, "owned file is current"))
    return plan


def create_only_plan(project_path: Path, root: Path, found: mf.Manifest, scope: str = "project") -> list[PlanItem]:
    """The manifest's `create-only` files: created when absent, never modified after."""
    plan: list[PlanItem] = []
    for relative, destination in found.files(mf.CREATE_ONLY, scope):
        expected = (root / relative).read_text(encoding="utf-8")
        target = project_path / destination
        if not target.exists():
            plan.append(PlanItem("create", target, f"create-only: {relative}", expected))
        elif target.read_text(encoding="utf-8").strip() == expected.strip():
            plan.append(PlanItem("skip", target, f"create-only {relative} is current"))
        else:
            plan.append(PlanItem("skip", target, f"differs from {relative}; create-only, not modified"))
    return plan


def lock_plan(project_path: Path, root: Path, changed: bool, revision: str, repo: Path | None = None) -> PlanItem:
    """`.atc/atc.lock` moves to the sync target ``revision`` when sync changes any file it writes
    (GIT_WORKFLOW.md § Lock advancement); a lock outside the history of ``repo``'s `main` (the content
    repository; ``root`` by default) moves too."""
    path = project_path / validator.ATC_LOCK
    source = governance_source(root)
    content = validator.render_lock(source, revision)
    lock = validator.read_lock(project_path)
    if lock is None:
        return PlanItem("create", path, "ATC lock", content)
    if changed or lock["source"] != source:
        return PlanItem("update", path, "ATC lock moves to the synced revision", content)
    if validator.main_state(repo or root, lock["revision"]) == "stale":
        return PlanItem("update", path, "ATC lock revision is not in the history of main", content)
    return PlanItem("skip", path, "ATC lock is current")


def checkout_target(root: Path) -> Target:
    """The ATC checkout as it stands, locked at its HEAD (source mode; docs/LAYOUT.md § The manifest)."""
    return Target(root, governance_revision(root), manifest=mf.load(root), repo=root)


def build_plan(project_path: Path, root: Path = ROOT, target: Target | None = None,
               tool_dirs: tuple[str, ...] = validator.TOOL_DIRS) -> list[PlanItem]:
    """Every file sync writes, in its write order: `.atc/` bootloader and governance, the pointer line,
    `CLAUDE.md`, the adapters in each of ``tool_dirs`` (the project's `skill_harnesses`), the prunes, then the
    lock. Rendered from ``target`` (the checkout ``root`` by default); the source name and the lock's
    history check come from ``root``."""
    target = target or checkout_target(root)
    tree = target.tree
    found = target.delivery()
    plan: list[PlanItem] = atc_plan(project_path, tree, found)
    atc_collision = any(item.action == "collision" for item in plan)
    plan.append(pointer_plan(project_path, validator.pointer_line(tree)))
    plan.extend(create_only_plan(project_path, tree, found))
    import testkit_contract
    testkit_record = testkit_contract.record(project_path)
    if testkit_record is not None:
        problems = testkit_contract.problems(project_path)
        plan.append(PlanItem('collision' if problems else 'skip',
                             testkit_record,
                             '; '.join(problems) if problems else 'testkit contract current; project configuration preserved'))

    canonical = canonical_skills(tree)
    for skill, metadata in canonical.items():
        # An opt-in skill managed in one selected directory is adopted, so every selected directory gets it.
        adopted = metadata.get("adoption") == "baseline" or any(
            managed_adapter(project_path / tool_dir / "skills" / skill / "SKILL.md", skill) for tool_dir in tool_dirs)
        for tool_dir in tool_dirs:
            adapter = project_path / tool_dir / "skills" / skill / "SKILL.md"
            if not adopted and not adapter.exists():
                continue
            expected = validator.render_adapter(tree, skill, tool_dir)
            if not adapter.exists() and holds_files(adapter.parent):
                plan.append(PlanItem("collision", adapter, UNOWNED_DIRECTORY))
            elif not adapter.exists():
                plan.append(PlanItem("create", adapter, "baseline managed adapter", expected))
            elif not managed_adapter(adapter, skill):
                plan.append(
                    PlanItem(
                        "collision",
                        adapter,
                        "adapter ownership or canonical identity is invalid or uncertain; refusing to modify",
                    )
                )
            elif validator.normalise(adapter.read_text(encoding="utf-8")) != expected:
                plan.append(PlanItem("update", adapter, "managed adapter drift", expected))
            else:
                plan.append(PlanItem("skip", adapter, "managed adapter is current"))
            if plan[-1].action != "collision":
                plan.extend(supporting_plan(tree, skill, adapter.parent))
    plan.extend(orphan_plan(project_path, set(canonical), tool_dirs))
    if not atc_collision:
        changed = any(item.action in ("create", "update", "prune") for item in plan)
        plan.append(lock_plan(project_path, root, changed, target.revision, target.repo))
    return plan


def verification_gate(project_path: Path, tree: Path | None = None) -> str | None:
    """Why the lock cannot advance to the sync target (GIT_WORKFLOW.md § Lock advancement), or ``None``: the
    project's AGENTS.md lacks § Integration verifier source, parsed as `atc integrate` parses it. Every
    revision sync renders carries a manifest, and all of them carry that rule, so it always applies."""
    agents_md = project_path / "AGENTS.md"
    declared = verification_section.declaration(agents_md.read_text(encoding="utf-8")) if agents_md.is_file() else None
    if declared is None:
        return f"AGENTS.md has no '{verification_section.HEADING}' section"
    if declared.argv_file is None:
        return f"AGENTS.md '{verification_section.HEADING}' has no 'Verifier argv file:' line"
    if not (project_path / declared.argv_file).is_file():
        return f"verifier argv file {declared.argv_file} named by AGENTS.md does not exist"
    return None


def ignored_outputs(project_path: Path, plan: list[PlanItem]) -> list[str]:
    """Paths the plan would write that Git ignores or excludes, so they would never be committed."""
    paths = sorted({os.path.relpath(item.path, project_path).replace(os.sep, "/")
                    for item in plan if item.action in ("create", "update") and item.content is not None})
    if not paths or not validator.git_toplevel(project_path):
        return []  # a checkout that is not its own repository stops at check_project_checkout
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(project_path), "check-ignore", "-z", "--stdin"], input="\0".join(paths) + "\0",
                            capture_output=True, text=True, encoding="utf-8", env=env)
    if result.returncode not in (0, 1):
        return []  # not a Git checkout: nothing to commit into
    return sorted(entry for entry in result.stdout.split("\0") if entry)


UNOWNED_DIRECTORY = "directory holds files but no SKILL.md, so nothing marks it managed; refusing to modify"


def holds_files(directory: Path) -> bool:
    return directory.is_dir() and any(path.is_file() or path.is_symlink() for path in directory.rglob("*"))


def supporting_plan(root: Path, skill: str, adapter_dir: Path) -> list[PlanItem]:
    """Copy the canonical skill's supporting files into one managed adapter
    directory. Sync owns the whole directory once its SKILL.md is managed, so a
    file there that the canonical skill does not ship is an update with no
    content, which sync removes."""
    plan: list[PlanItem] = []
    expected = validator.supporting_files(root, skill)
    for relative, content in expected.items():
        target = adapter_dir / relative
        if not target.exists():
            plan.append(PlanItem("create", target, "supporting file", content))
        elif validator.normalise(target.read_text(encoding="utf-8")) != content:
            plan.append(PlanItem("update", target, "supporting file drift", content))
        else:
            plan.append(PlanItem("skip", target, "supporting file is current"))
    if adapter_dir.is_dir():
        for extra in validator.adapter_extras(adapter_dir, expected):
            plan.append(PlanItem("update", extra, "no longer in the canonical skill; sync removes it"))
    return plan


def orphan_plan(project_path: Path, canonical: set[str], tool_dirs: tuple[str, ...] = validator.TOOL_DIRS) -> list[PlanItem]:
    """Managed adapters sync deletes: those whose canonical skill no longer
    exists (renamed or removed), and every one in a tool directory outside
    ``tool_dirs``. Only a directory whose SKILL.md carries an ownership marker
    is listed; a project-owned skill directory is never touched."""
    plan: list[PlanItem] = []
    for tool_dir in validator.TOOL_DIRS:
        skills_dir = project_path / tool_dir / "skills"
        if not skills_dir.is_dir():
            continue
        selected = tool_dir in tool_dirs
        for entry in sorted(skills_dir.iterdir()):
            skill_file = entry / "SKILL.md"
            if not entry.is_dir() or (selected and entry.name in canonical) or not skill_file.is_file():
                continue
            validator.FAILURES.clear()
            metadata, _ = validator.parse_frontmatter(skill_file)
            validator.FAILURES.clear()
            if not validator.is_managed(metadata):
                continue
            if entry.name not in canonical:
                plan.append(PlanItem("prune", entry, f"orphaned managed adapter: no canonical skill '{entry.name}'"))
            else:
                plan.append(PlanItem("prune", entry, f"managed adapter in {tool_dir}/, which skill_harnesses does not select"))
    return plan


def root_adapter_plan(root: Path = ROOT, tree: Path | None = None) -> list[PlanItem]:
    """Render every adapter of the directory ``root`` from the canonical skills of ``tree`` (``root`` itself
    by default), and prune the orphaned ones.

    The root is a consumer like any project with every tool selected, so its
    `.claude/`, `.agents/` and `.dsh/` copies are the rendered output and are
    regenerated here; the shell installers copy them as they are."""
    tree = tree or root
    plan: list[PlanItem] = []
    canonical = canonical_skills(tree)
    for skill in canonical:
        for tool_dir in validator.TOOL_DIRS:
            target = root / tool_dir / "skills" / skill / "SKILL.md"
            expected = validator.render_adapter(tree, skill, tool_dir)
            if not target.exists() and holds_files(target.parent):
                plan.append(PlanItem("collision", target, UNOWNED_DIRECTORY))
            elif not target.exists():
                plan.append(PlanItem("create", target, "rendered adapter", expected))
            elif not managed_adapter(target, skill):
                plan.append(
                    PlanItem(
                        "collision",
                        target,
                        "adapter ownership or canonical identity is invalid or uncertain; refusing to modify",
                    )
                )
            elif validator.normalise(target.read_text(encoding="utf-8")) != expected:
                plan.append(PlanItem("update", target, "adapter drifted from canonical", expected))
            else:
                plan.append(PlanItem("skip", target, "managed adapter is current"))
            if plan[-1].action != "collision":
                plan.extend(supporting_plan(tree, skill, target.parent))
    plan.extend(orphan_plan(root, set(canonical)))
    return plan


@contextlib.contextmanager
def main_tip_tree(checkout: Path) -> Iterator[Path]:
    """The files of the tip of ``checkout``'s `main`, exported from its Git objects for the block."""
    tip = _git(checkout, "rev-parse", "--verify", "-q", f"refs/heads/{MAIN}^{{commit}}", check=False).stdout.strip()
    if not tip:
        raise SyncStop("EXPORT_FAILED", f"{checkout} has no {MAIN} branch")
    with tempfile.TemporaryDirectory(prefix="atc-root-", ignore_cleanup_errors=True) as temporary:
        tree = Path(temporary) / "tree"
        try:
            validator.export_revision(checkout, tip, tree)
        except ValueError as exc:
            raise SyncStop("EXPORT_FAILED", str(exc)) from None
        yield tree


def workspace_scope_plan(root: Path, target: Target) -> list[PlanItem]:
    """The manifest's `workspace` items for a workspace directory that is not the governance checkout: its
    `create-only` files and the adapters of its `skill-render` source in every harness directory."""
    found = target.delivery()
    plan = create_only_plan(root, target.tree, found, "workspace")
    if found.one(mf.SKILLS, "workspace") is not None:
        plan.extend(root_adapter_plan(root, target.tree))
    return plan


def workspace_adapter_plan(root: Path, target: Target | None = None) -> tuple[list[PlanItem], bool]:
    """The root-adapter check doctor and sync share (docs/LAYOUT.md § The manifest), and whether ``root`` is
    the governance checkout. The actual copies are the workspace directory's. The expected ones are rendered
    from the Git objects of the tip of `main` when the workspace directory is the governance checkout, never
    from its working tree; otherwise from ``target``, the sync target (resolved when None), whose `workspace`
    items sync delivers."""
    if content.is_workspace_checkout(root):
        with main_tip_tree(root) as tree:
            return root_adapter_plan(root, tree), True
    if target is not None:
        return workspace_scope_plan(root, target), False
    with exported_target(root, None) as resolved:
        return workspace_scope_plan(root, resolved), False


def apply_plan(plan: list[PlanItem], mode: str) -> None:
    for item in plan:
        if item.action == "prune":
            if mode == "sync":
                shutil.rmtree(item.path)
            continue
        if item.action == "create" and mode in ("apply", "sync"):
            if item.content is None:
                item.path.mkdir(parents=True, exist_ok=True)
                if not any(item.path.iterdir()):
                    (item.path / ".gitkeep").write_text("", encoding="utf-8")
            else:
                item.path.parent.mkdir(parents=True, exist_ok=True)
                item.path.write_text(item.content, encoding="utf-8", newline="\n")
        elif item.action == "update" and mode == "sync":
            if item.content is None:
                item.path.unlink()
            else:
                item.path.write_text(item.content, encoding="utf-8", newline="\n")


STOP_HINTS = {
    "VERIFICATION_SECTION_MISSING": "add the '## Integration verification' section to the project's AGENTS.md first",
    "IGNORED_OUTPUT": "stop Git ignoring those paths in the project; sync commits every file it writes",
    "ROOT_ADAPTER_DRIFT": "render them with `uv run --locked atc-maintain sync --root` in a topic and integrate it",
    "ROOT_NOT_MAIN": "sync commits render from the ATC checkout on main; switch it back to main",
    "ROOT_DIRTY": "commit or discard the ATC checkout's tracked changes",
    "NOT_A_REPOSITORY": "fix the project's repo path in workspace.yaml",
    "NOT_MAIN": "switch the project's checkout to main",
    "CHECKOUT_DIRTY": "commit or move the project's uncommitted changes first",
    "BEHIND_ORIGIN": "bring the project's main up to date with origin/main",
    "UNMANAGED_CHANGE": "commit or move the listed changes, then sync again",
    "INTEGRATION_BUSY": "wait for the running integration, then sync again",
    "REVISION_NOT_ON_MAIN": "name a commit on the ATC checkout's main",
    "NO_RELEASE_TAG": "fetch the tags, or name a revision with --revision",
    "RELEASE_NOT_ON_MAIN": "install a release tagged on this checkout's main with atc install TAG",
    "EXPORT_FAILED": "check the content repository's Git state",
    "NO_MANIFEST": "name a revision that carries atc-manifest.yaml (0.7.0 or later)",
    "MANIFEST_INVALID": "fix atc-manifest.yaml in a topic of the governance repository",
    "MANIFEST_SOURCE_MISSING": "fix atc-manifest.yaml in a topic of the governance repository",
    "MANIFEST_TOO_NEW": "install a newer atc release first",
    "GOVERNANCE_CHECKOUT_INVALID": "point governance_checkout in workspace.yaml at a Git checkout",
    "CONTENT_SOURCE_UNKNOWN": "set governance_source (<owner>/<repo>) or governance_checkout in workspace.yaml",
    "CONTENT_FETCH_FAILED": "check the network and that governance_source names a reachable repository",
    "GIT_FAILED": "check the project's Git state",
}
COLLISION_HINT = "the file is not ATC-managed; move it aside or ask the Owner, then sync again"
SIGNS = {"create": "+", "update": "~", "prune": "-", "collision": "!", "skip": "="}


def summary(plan: list[PlanItem]) -> str:
    """`+new ~updated -deleted !collisions`, or `current`."""
    counts = [(SIGNS[action], sum(item.action == action for item in plan))
              for action in ("create", "update", "prune", "collision")]
    shown = [f"{sign}{count}" for sign, count in counts if count]
    return " ".join(shown) or "current"


def report_plan(name: str, project_path: Path, plan: list[PlanItem], mode: str, verbose: bool = False,
                commit: str | None = None, stops: list[tuple[str, str]] | None = None) -> None:
    """One line for a checkout, then its collisions and stops with their hints; ``verbose`` lists each changed
    file by its path relative to the checkout."""
    if EVENTS is not None:
        event = {"project": name, "path": str(project_path), "mode": mode, "summary": summary(plan),
                 "items": [{"action": item.action, "path": str(item.path), "detail": item.detail}
                           for item in plan if verbose or item.action != "skip"]}
        if commit is not None:
            event["commit"] = commit
        if stops:
            event["stops"] = [{"reason": code, "detail": detail, "hint": STOP_HINTS.get(code)} for code, detail in stops]
        EVENTS.append(event)
    line = f"{name} {summary(plan)}"
    if commit:
        line += f" commit {commit[:7]}"
    print(line)
    for item in plan:
        if item.action == "collision" or (verbose and item.action != "skip"):
            print(f"  {SIGNS[item.action]} {cli_conventions.relative(item.path, project_path)}"
                  + (f" ({item.detail})" if item.action == "collision" else ""))
    if any(item.action == "collision" for item in plan):
        print(f"  hint: {COLLISION_HINT}")
    for code, detail in stops or []:
        print(f"  STOP {code}: {detail}")
        if STOP_HINTS.get(code):
            print(f"  hint: {STOP_HINTS[code]}")


def changes(plan: list[PlanItem]) -> bool:
    return any(item.action in ("create", "update", "prune", "collision") for item in plan)


class SyncStop(Exception):
    """A sync commit precondition failed; ``code`` names it in the output."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code} {detail}")
        self.code = code
        self.detail = detail


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", env=env)
    if check and result.returncode:
        raise SyncStop("GIT_FAILED", f"git {' '.join(args)}: {(result.stderr or result.stdout).strip()}")
    return result


def _within(path: Path, owner: Path) -> bool:
    candidate = os.path.normcase(os.path.abspath(path))
    base = os.path.normcase(os.path.abspath(owner))
    return candidate == base or candidate.startswith(base.rstrip(os.sep) + os.sep)


def changed_paths(repo: Path) -> list[str]:
    """Every path `git status` reports, untracked files included; ignored files are not reported."""
    entries = _git(repo, "status", "--porcelain", "-z", "--untracked-files=all").stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC":
            index += 1  # the rename or copy source follows as its own entry
    return paths


def root_commit_revision(root: Path, target: Target) -> str:
    """The ATC revision a sync commit names: the target, synced from the ATC checkout on main with no tracked change."""
    if validator.lock_state(root, "HEAD") != "current":
        raise SyncStop("ROOT_NOT_MAIN", "sync commits render from the ATC checkout on main; in a topic run atc-maintain sync --root")
    if _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise SyncStop("ROOT_DIRTY", "the ATC checkout has uncommitted tracked changes")
    return target.revision


def check_project_checkout(project_path: Path) -> None:
    """Stop unless the project checkout is the top of its repository, on a clean main, not behind origin/main."""
    top = _git(project_path, "rev-parse", "--show-toplevel", check=False)
    if top.returncode or os.path.normcase(os.path.realpath(top.stdout.strip())) != os.path.normcase(os.path.realpath(project_path)):
        raise SyncStop("NOT_A_REPOSITORY", f"{project_path} is not the top of a Git checkout")
    branch = _git(project_path, "symbolic-ref", "-q", "--short", "HEAD", check=False).stdout.strip()
    if branch != "main":
        raise SyncStop("NOT_MAIN", f"the checkout is on {branch or 'a detached HEAD'}; sync commits land on main")
    if changed_paths(project_path):
        raise SyncStop("CHECKOUT_DIRTY", "the checkout has uncommitted changes; commit or move them first")
    remote = _git(project_path, "rev-parse", "--verify", "-q", "refs/remotes/origin/main", check=False).stdout.strip()
    if remote and _git(project_path, "merge-base", "--is-ancestor", remote, "HEAD", check=False).returncode:
        raise SyncStop("BEHIND_ORIGIN", "local main does not contain origin/main; bring main up to date first")


RELEASE_TAG = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)")
FULL_SHA = re.compile(r"[0-9a-f]{40}")


def merged_release_tags(root: Path, ref: str) -> list[tuple[str, str]]:
    """``(tag, commit)`` of every ``vMAJOR.MINOR.PATCH`` tag in the history of ``ref``, highest version first,
    read from the ATC checkout's local tags."""
    listed = _git(root, "for-each-ref", "--merged", ref, "--format=%(refname:short)%09%(*objectname)%09%(objectname)",
                  "refs/tags", check=False)
    tags = []
    for line in listed.stdout.splitlines() if listed.returncode == 0 else []:
        name, peeled, direct = line.split("\t")
        version = RELEASE_TAG.fullmatch(name)
        if version:
            tags.append((tuple(int(part) for part in version.groups()), name, peeled or direct))
    return [(name, commit) for _, name, commit in sorted(tags, reverse=True)]


def release_tags(root: Path, revision: str) -> tuple[str | None, str | None]:
    """The release tag at ``revision``, else the latest release tag before it, read in the ATC checkout."""
    tags = merged_release_tags(root, revision)
    exact = next((name for name, commit in tags if commit == revision), None)
    if exact:
        return exact, None
    return None, tags[0][0] if tags else None


def installed_release():
    """The release this code is when it runs from a release copy (`release_pin`), else None."""
    import release_pin
    return release_pin.installed(Path(__file__).resolve().parents[3])


def content_repository(root: Path) -> Path:
    """The content repository of the workspace directory ``root`` (`content.repository`); SyncStop without one."""
    try:
        return content.repository(root)
    except content.ContentError as exc:
        raise SyncStop(exc.token, exc.detail) from None


def running_version() -> str:
    from importlib import metadata
    try:
        return metadata.version("agent-traffic-control")
    except metadata.PackageNotFoundError:
        return "unknown"


def resolve_target(root: Path, revision: str | None) -> tuple[str, str | None, bool]:
    """``(revision, tag, named)`` sync renders, in the content repository ``root``: the Owner-named SHA on
    main, else the installed release, so a project never moves ahead of the tool, else the latest release
    tag on main."""
    if revision is not None:
        known = _git(root, "rev-parse", "--verify", "-q", f"{revision}^{{commit}}", check=False)
        if known.stdout.strip() != revision or _git(root, "merge-base", "--is-ancestor", revision, f"refs/heads/{MAIN}", check=False).returncode:
            raise SyncStop("REVISION_NOT_ON_MAIN", f"{revision} is not a commit SHA in the history of the ATC checkout's {MAIN}")
        return revision, release_tags(root, revision)[0], True
    release = installed_release()
    if release is not None:
        if _git(root, "merge-base", "--is-ancestor", release.commit, f"refs/heads/{MAIN}", check=False).returncode:
            raise SyncStop("RELEASE_NOT_ON_MAIN", f"the installed release {release.tag} ({release.commit[:12]}) is not "
                                                  f"in the history of the ATC checkout's {MAIN}")
        return release.commit, release.tag, False
    tags = merged_release_tags(root, f"refs/heads/{MAIN}")
    if not tags:
        raise SyncStop("NO_RELEASE_TAG", f"no vMAJOR.MINOR.PATCH tag in the history of {MAIN} in this checkout; "
                                         "fetch the tags or name a SHA with --revision")
    name, commit = tags[0]
    return commit, name, False


@contextlib.contextmanager
def exported_target(root: Path, revision: str | None) -> Iterator[Target]:
    """The sync target of the workspace directory ``root`` with its sources exported from the content
    repository to a temporary tree for the duration of the block. A revision without a usable manifest, or
    one that needs a newer atc, stops (D5: `NO_MANIFEST`, `MANIFEST_INVALID`, `MANIFEST_SOURCE_MISSING`,
    `MANIFEST_TOO_NEW`)."""
    repo = content_repository(root)
    commit, tag, named = resolve_target(repo, revision)
    with tempfile.TemporaryDirectory(prefix="atc-sync-", ignore_cleanup_errors=True) as temporary:
        tree = Path(temporary) / "tree"
        try:
            validator.export_revision(repo, commit, tree)
        except ValueError as exc:
            raise SyncStop("EXPORT_FAILED", str(exc)) from None
        try:
            found = mf.load(tree)
            mf.check_version(found, running_version())
        except mf.ManifestError as exc:
            raise SyncStop(exc.token, f"{commit[:12]}: {exc.detail}") from None
        yield Target(tree, commit, tag, named, found, repo)


def behind_lock(project_path: Path, root: Path, target: Target) -> str | None:
    """The locked revision when the default target is a strict ancestor of it, which sync never moves back to."""
    if target.named:
        return None
    try:
        lock = validator.read_lock(project_path)
    except ValueError:
        return None
    if lock is None or lock["revision"] == target.revision:
        return None
    if _git(target.repo or root, "merge-base", "--is-ancestor", target.revision, lock["revision"], check=False).returncode:
        return None
    return lock["revision"]


def sync_label(revision: str, tags: tuple[str | None, str | None]) -> str:
    """``v0.4.0 (abc1234)`` at a release tag, ``abc1234 (after v0.4.0)`` past one, else the short SHA."""
    tag, after = tags
    if tag:
        return f"{tag} ({revision[:7]})"
    return f"{revision[:7]} (after {after})" if after else revision[:7]


def commit_sync(project_path: Path, plan: list[PlanItem], source: str, revision: str,
                tags: tuple[str | None, str | None] = (None, None)) -> str | None:
    """Commit the files sync just wrote and record the commit; None when nothing tracked changed."""
    paths = changed_paths(project_path)
    if not paths:
        return None
    owned = [item.path for item in plan if item.action in ("create", "update", "prune")]
    stray = [path for path in paths if not any(_within(project_path / path, owner) for owner in owned)]
    if stray:
        raise SyncStop("UNMANAGED_CHANGE", "changed outside the synced files, left uncommitted: " + ", ".join(stray))
    before = _git(project_path, "rev-parse", "HEAD").stdout.strip()
    _git(project_path, "add", "-A")
    _git(project_path, "commit", "-q", "-m", f"chore(governance): sync from {source} {sync_label(revision, tags)}",
         "-m", f"Rendered by `atc sync` from {source} at {revision}.")
    sha = _git(project_path, "rev-parse", "HEAD").stdout.strip()
    common = Path(_git(project_path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    record_file = common / "atc" / "integrations.jsonl"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    with record_file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({
            "authority": {"kind": "sync"},
            "candidate_sha": sha,
            "integrated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": "sync",
            "main_before": before,
            "records_only": False,
            "source": source,
            "source_after": tags[1],
            "source_revision": revision,
            "source_tag": tags[0],
            "task": revision,
        }, sort_keys=True) + "\n")
    return sha


def sync_and_commit(project_path: Path, plan: list[PlanItem], source: str, revision: str,
                    tags: tuple[str | None, str | None] = (None, None)) -> str | None:
    """Write the plan into the project's main checkout and commit it, holding the project's Integration Slot."""
    check_project_checkout(project_path)
    slot = IntegrationSlot(project_path)
    if not slot.acquire(blocking=False):
        raise SyncStop("INTEGRATION_BUSY", f"another writer holds the integration slot at {slot.path}")
    try:
        check_project_checkout(project_path)
        apply_plan(plan, "sync")
        return commit_sync(project_path, plan, source, revision, tags)
    finally:
        slot.release()


def run(mode: str, targets: list[tuple[str, Path]], root: Path = ROOT, commit: bool = False,
        verbose: bool = False, target: Target | None = None,
        tools: dict[str, tuple[str, ...]] | None = None, render_root: bool = False) -> int:
    """``commit`` makes a sync commit on each project's main (GIT_WORKFLOW.md "Governance sync commits").
    Projects are rendered from ``target``, the checkout ``root`` as it stands by default, into the harness
    directories ``tools`` names for them (`skill_harnesses`; every one for a project it does not name). The
    checkout's own adapters are reported only when they would change; ``render_root`` writes them."""
    tools = tools or {}
    has_collision = has_pending = has_updates = has_stop = False
    if target is None:  # source mode: the checkout's own copies from its own tree
        target, checkout = checkout_target(root), True
        root_plan = root_adapter_plan(root)
    else:
        root_plan, checkout = workspace_adapter_plan(root, target)
    root_changes = changes(root_plan)
    root_collision = any(item.action == "collision" for item in root_plan)
    if commit:
        try:
            if checkout and root_changes:
                raise SyncStop("ROOT_ADAPTER_DRIFT", "the ATC checkout's own adapters are not current")
            revision = root_commit_revision(root, target) if checkout else target.revision
        except SyncStop as stop:
            report_plan("root", root, root_plan, mode, verbose, stops=[(stop.code, stop.detail)])
            return cli_conventions.STOP
        if not checkout:
            if root_changes or verbose:
                report_plan("workspace", root, root_plan, mode, verbose)
            has_collision |= root_collision
            if not root_collision:
                apply_plan(root_plan, "sync")
        source = governance_source(root)
        tags = release_tags(target.repo or root, revision)
    else:
        if root_changes or render_root or not targets:
            report_plan("root", root, root_plan, "sync" if render_root else mode, verbose)
        has_collision |= root_collision
        if render_root or mode == "check":
            has_pending |= root_changes
        if render_root and not root_collision:
            apply_plan(root_plan, "sync")
    for name, project_path in targets:
        if not project_path.is_dir():
            print(f"{name} NOT_INITIALIZED (the registered repo path {project_path} is absent)")
            print(f"  hint: atc add {name}, or fix its repo path in workspace.yaml")
            if EVENTS is not None:
                EVENTS.append({"project": name, "path": str(project_path), "mode": mode, "readiness": "NOT_INITIALIZED"})
            continue
        ahead = behind_lock(project_path, root, target)
        if ahead:
            print(f"{name} current (locked at {ahead[:7]}, ahead of {target.tag or target.revision[:7]}; "
                  "sync never moves a project back without --revision)")
            if EVENTS is not None:
                EVENTS.append({"project": name, "path": str(project_path), "mode": mode, "summary": "ahead",
                               "items": []})
            continue
        plan = build_plan(project_path, root, target, tools.get(name, validator.TOOL_DIRS))
        gate = verification_gate(project_path, target.tree)
        ignored = ignored_outputs(project_path, plan)
        stops = []
        if gate:
            stops.append(("VERIFICATION_SECTION_MISSING", gate))
        if ignored:
            stops.append(("IGNORED_OUTPUT", f"Git ignores or excludes: {', '.join(ignored)}"))
        project_collision = any(item.action == "collision" for item in plan)
        if stops:
            report_plan(name, project_path, plan, mode, verbose, stops=stops)
            has_stop = True
            continue
        has_collision |= project_collision
        has_pending |= any(item.action in ("create", "update", "prune") for item in plan)
        has_updates |= any(item.action == "update" for item in plan)
        sha = None
        if commit and not project_collision:
            try:
                sha = sync_and_commit(project_path, plan, source, revision, tags)
            except SyncStop as stop:
                report_plan(name, project_path, plan, mode, verbose, stops=[(stop.code, stop.detail)])
                has_stop = True
                continue
        elif not commit and (not project_collision or mode == "check"):
            apply_plan(plan, mode)
        report_plan(name, project_path, plan, mode, verbose, commit=sha)
    if has_collision or has_stop:
        return cli_conventions.STOP
    if mode == "check" and has_pending:
        print("STOP DRIFT: the files above would change", file=sys.stderr)
        print(f"hint: atc sync{''.join(' ' + name for name, _ in targets) if len(targets) == 1 else ''}",
              file=sys.stderr)
        return cli_conventions.STOP
    if mode == "apply" and has_updates:
        return cli_conventions.STOP
    return cli_conventions.OK


def short_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
        raise argparse.ArgumentTypeError("expects a commit SHA of 7 to 40 hex characters")
    return value.lower()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc sync", allow_abbrev=False, **cli_conventions.form(
        "Write and commit the files Agent Traffic Control owns inside projects, or check them.",
        "Owned: CLAUDE.md when absent, AGENTS.md when absent and its pointer line, .atc/ (README.md, atc.lock,\n"
        "governance/) and one managed adapter per baseline skill in each harness directory skill_harnesses\n"
        "selects, rendered from the latest release tag on main or the --revision SHA. Managed adapters with no\n"
        "canonical skill or in an unselected harness directory are deleted. Each project's changes are one commit\n"
        "on its main. One line per project: +created ~updated -deleted !collisions, or current, and the commit.",
        exit="0 current or synced; 1 STOP (drift or project problems under --check, a collision, a sync "
             "precondition); 2 ERROR",
        see="docs/LAYOUT.md § Inside a consuming repository; GIT_WORKFLOW.md § Governance sync commits"))
    parser.add_argument("projects", nargs="*", metavar="NAME",
                        help="registered project names from workspace.yaml (default: every registered project)")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 when a sync would change anything or a project fails validation")
    parser.add_argument("--revision", metavar="SHA", type=short_sha,
                        help="render this commit on main (7 or more hex characters) instead of the latest release tag")
    cli_conventions.add_verbose(parser)
    cli_conventions.add_json(parser)
    return parser


def build_root_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc-maintain sync", allow_abbrev=False, **cli_conventions.form(
        "Render the managed skill copies of this ATC checkout from shared/skills; commit nothing.",
        exit="0 rendered or current; 1 STOP (a collision, or drift under --check); 2 ERROR",
        see="root AGENTS.md § Changing this repo"))
    parser.add_argument("--root", action="store_true", required=True, help="render this checkout's own adapters")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when the copies would change")
    cli_conventions.add_verbose(parser)
    cli_conventions.add_json(parser)
    return parser


def _with_events(as_json: bool, mode: str, body) -> int:
    global EVENTS, TARGET_EVENT
    if not as_json:
        return body()
    EVENTS = []
    try:
        with contextlib.redirect_stdout(sys.stderr):
            code = body()
        result = {"mode": mode, "checkouts": EVENTS}
        if TARGET_EVENT is not None:
            result["target"] = TARGET_EVENT
        print(json.dumps(result, indent=2))
        return code
    finally:
        EVENTS = TARGET_EVENT = None


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = build_parser().parse_args(argv)
    mode = "check" if args.check else "sync"
    return _with_events(args.json, mode, lambda: _run(args, mode, root))


def root_main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    """`atc-maintain sync --root`: render the checkout's own adapters, or check them."""
    args = build_root_parser().parse_args(argv)
    mode = "check" if args.check else "sync"
    try:
        return _with_events(args.json, mode, lambda: run(mode, [], root, verbose=args.verbose, render_root=not args.check))
    except ValueError as exc:
        print(f"ERROR SYNC {exc}", file=sys.stderr)
        print("hint: fix the canonical skills named by shared/skills", file=sys.stderr)
        return cli_conventions.ERROR


def full_revision(root: Path, revision: str | None) -> str | None:
    """The full SHA ``revision`` names in the content repository of the workspace directory ``root``."""
    if revision is None:
        return None
    found = _git(content_repository(root), "rev-parse", "--verify", "-q", f"{revision}^{{commit}}", check=False).stdout.strip()
    if not found:
        raise SyncStop("REVISION_NOT_ON_MAIN", f"{revision} names no commit in the content repository")
    return found


def run_at_target(mode: str, targets: list[tuple[str, Path]], root: Path, revision: str | None = None,
                  commit: bool = False, verbose: bool = False, tools: dict[str, tuple[str, ...]] | None = None,
                  after=None) -> int:
    """``run`` against the sync target: the latest release tag on main, or ``revision`` (the Owner's SHA).
    ``after(target, code)`` runs while the target's tree still exists and returns the exit code."""
    global TARGET_EVENT
    try:
        with exported_target(root, full_revision(root, revision)) as target:
            if verbose:
                label = f"{target.tag} ({target.revision[:7]})" if target.tag else target.revision[:7]
                print(f"target {label}{' (named)' if target.named else ''}")
            if EVENTS is not None:
                TARGET_EVENT = {"revision": target.revision, "tag": target.tag, "named": target.named}
            code = run(mode, targets, root, commit=commit, verbose=verbose, target=target, tools=tools)
            return after(target, code) if after else code
    except SyncStop as stop:
        print(f"STOP {stop.code}: {stop.detail}", file=sys.stderr)
        if STOP_HINTS.get(stop.code):
            print(f"hint: {STOP_HINTS[stop.code]}", file=sys.stderr)
        if EVENTS is not None:
            TARGET_EVENT = {"revision": None, "tag": None, "named": revision is not None,
                            "stops": [{"reason": stop.code, "detail": stop.detail, "hint": STOP_HINTS.get(stop.code)}]}
        return cli_conventions.STOP


def _run(args: argparse.Namespace, mode: str, root: Path) -> int:
    try:
        projects = load_projects(root)
        unknown = [name for name in args.projects if name not in projects]
        if unknown:
            print(f"ERROR PROJECT_UNKNOWN {', '.join(unknown)} not registered in workspace.yaml", file=sys.stderr)
            print(f"hint: registered: {', '.join(sorted(projects)) or 'none'}; atc add NAME registers one",
                  file=sys.stderr)
            return cli_conventions.ERROR
        selected = [(name, projects[name]) for name in args.projects] if args.projects else projects.items()
        targets = [(name, (root / entry["repo"]).resolve()) for name, entry in selected]

        def problems(target: Target, code: int) -> int:
            if mode != "check":
                return code
            found = validator.project_problems(root, [name for name, _ in targets], target.tree, target.repo)
            for problem in found:
                print(f"PROBLEM {problem}")
            if found:
                print(f"STOP PROJECT_PROBLEMS: {len(found)} found", file=sys.stderr)
                print("hint: each PROBLEM line names the file and the fix", file=sys.stderr)
                code = max(code, cli_conventions.STOP)
            return code

        return run_at_target(mode, targets, root, args.revision, commit=mode == "sync", verbose=args.verbose,
                             tools=validator.load_skill_harnesses(root / "workspace.yaml"), after=problems)
    except ValueError as exc:
        print(f"ERROR SYNC {exc}", file=sys.stderr)
        print("hint: fix workspace.yaml or the canonical skills it names", file=sys.stderr)
        return cli_conventions.ERROR


def apply(name: str, root: Path = ROOT, target: Target | None = None) -> int:
    """Create the missing owned files of the registered project ``name`` from ``target`` (the sync target,
    resolved when None); update nothing, commit nothing."""
    try:
        projects = load_projects(root)
        if name not in projects:
            print(f"ERROR PROJECT_UNKNOWN '{name}' is not registered in workspace.yaml", file=sys.stderr)
            return cli_conventions.ERROR
        targets = [(name, (root / projects[name]["repo"]).resolve())]
        tools = validator.load_skill_harnesses(root / "workspace.yaml")
        if target is not None:
            return run("apply", targets, root, target=target, tools=tools)
        return run_at_target("apply", targets, root, tools=tools)
    except ValueError as exc:
        print(f"ERROR SYNC {exc}", file=sys.stderr)
        return cli_conventions.ERROR


if __name__ == "__main__":
    sys.exit(main())

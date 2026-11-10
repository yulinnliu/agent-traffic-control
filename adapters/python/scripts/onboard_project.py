#!/usr/bin/env python3
"""Check, apply, or sync the files Agent Traffic Control owns inside a project.

Owned files: `CLAUDE.md` (only when the project has none, or has the
previous workspace entrypoint), the pointer line in `AGENTS.md`, sync's files
under `.atc/` (`README.md`, `atc.lock`, `governance/`), and one managed
adapter per baseline skill and tool directory. Each adapter is rendered from
the canonical skill, so a project needs nothing from this checkout at run
time. `.atc/tasks/` is the project's; an adapter without an ownership marker
is project-owned; neither is ever rewritten. A project in the layout before
`atc.lock` is migrated in the same pass (docs/LAYOUT.md).

`--sync` with a project or `--all` commits what it writes on each project's
`main` and records the commit in `integrations.jsonl`; `--root-only` renders
the adapters of the checkout it runs in and commits nothing. The rules:
GIT_WORKFLOW.md "Governance sync commits".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

import validate_workspace as validator
import verification_section
from integration_slot import IntegrationSlot

ROOT = Path(__file__).resolve().parents[3]
LEGACY_POLICY_PATTERN = validator.LEGACY_POLICY_BLOCK
POINTER_LINE = re.compile(rf"^{re.escape(validator.POINTER_MARKER)}.*$", re.MULTILINE)
# The workspace root's CLAUDE.md before it imported AGENTS.md; a project copy
# equal to it is replaced, any other CLAUDE.md is the project's.
LEGACY_CLAUDE_MD = (
    "# CLAUDE.md\n\n"
    "Canonical agent guidance for the directory holding this file lives in the\n"
    "`AGENTS.md` next to it. Read that `AGENTS.md` in full before starting work and\n"
    "follow it as if it were this file.\n"
)
VERIFIER_SOURCE_HEADING = re.compile(r"^#+ Integration verifier source\s*$", re.MULTILINE)


@dataclass(frozen=True)
class PlanItem:
    action: str
    path: Path
    detail: str
    content: str | None = None


def load_projects(root: Path = ROOT) -> dict[str, dict]:
    validator.FAILURES.clear()
    workspace_file = root / "workspace.yaml"
    _, projects = validator.load_and_check_workspace_yaml(workspace_file)
    if validator.FAILURES:
        raise ValueError("; ".join(validator.FAILURES))
    return projects


def canonical_skills(root: Path = ROOT) -> dict[str, dict]:
    validator.FAILURES.clear()
    original_root = validator.ROOT
    try:
        validator.ROOT = root
        canonical = validator.load_canonical_skills()
    finally:
        validator.ROOT = original_root
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


def pointer_plan(project_path: Path, line: str) -> PlanItem:
    """The pointer line in AGENTS.md; an older release's policy block is replaced by it in place."""
    agents_md = project_path / "AGENTS.md"
    if not agents_md.exists():
        return PlanItem("collision", agents_md, "missing; create project-specific AGENTS.md first")
    text = agents_md.read_text(encoding="utf-8")
    current = POINTER_LINE.search(text)
    if current:
        if current.group(0) == line:
            return PlanItem("skip", agents_md, "pointer line is current")
        return PlanItem("update", agents_md, "pointer line drift", text[: current.start()] + line + text[current.end() :])
    legacy = LEGACY_POLICY_PATTERN.search(text)
    if legacy:
        return PlanItem("update", agents_md, "older policy block replaced by the pointer line",
                        text[: legacy.start()] + line + text[legacy.end() :])
    if any(token in text for token in ("shared/governance/INDEX.md", ".atc/governance/INDEX.md", "AGENT_DECISION_POLICY.md", "GIT_LIFECYCLE.md")):
        return PlanItem(
            "collision",
            agents_md,
            "governance reference exists without ownership markers; refusing to modify",
        )
    heading = re.search(r"^## .+$", text, re.MULTILINE)
    if not heading:
        return PlanItem("collision", agents_md, "no section heading; cannot place the pointer line safely")
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


def legacy_manifest(project_path: Path) -> bool:
    """Whether the project holds the pin file of the layout before `atc.lock`, carrying sync's marker."""
    path = project_path / validator.LEGACY_MANIFEST
    return path.is_file() and validator.managed_marker_present(path.read_text(encoding="utf-8"))


def atc_plan(project_path: Path, root: Path) -> list[PlanItem]:
    """The bootloader and the vendored governance; collisions alone when `.atc/` holds what sync does not own."""
    plan: list[PlanItem] = []
    for stray in validator.atc_strays(project_path):
        if stray == validator.LEGACY_MANIFEST:
            if not legacy_manifest(project_path):
                plan.append(PlanItem("collision", project_path / stray, "exists without an ownership marker; refusing to manage .atc/"))
            continue
        plan.append(PlanItem("collision", project_path / stray, "not a file sync writes; .atc/ holds README.md, atc.lock, governance/ and tasks/"))
    try:
        validator.read_lock(project_path)
    except ValueError as exc:
        plan.append(PlanItem("collision", project_path / validator.ATC_LOCK, f"{exc}; refusing to manage .atc/"))
    if plan:
        return plan
    readme = project_path / validator.ATC_README
    expected_readme = validator.atc_readme_template_path(root).read_text(encoding="utf-8")
    if not readme.exists():
        plan.append(PlanItem("create", readme, "governance bootloader", expected_readme))
    elif validator.normalise(readme.read_text(encoding="utf-8")) != validator.normalise(expected_readme):
        plan.append(PlanItem("update", readme, "governance bootloader drift", expected_readme))
    else:
        plan.append(PlanItem("skip", readme, "governance bootloader is current"))
    for module in validator.vendored_governance_modules():
        source = root / "shared" / "governance" / module
        if not source.is_file():
            raise ValueError(f"{source}: missing canonical governance module")
        expected = source.read_text(encoding="utf-8")
        target = project_path / validator.VENDORED_GOVERNANCE / module
        if not target.exists():
            plan.append(PlanItem("create", target, "vendored governance module", expected))
        elif validator.normalise(target.read_text(encoding="utf-8")) != validator.normalise(expected):
            plan.append(PlanItem("update", target, "vendored governance drift", expected))
        else:
            plan.append(PlanItem("skip", target, "vendored governance is current"))
    return plan


def lock_plan(project_path: Path, root: Path, changed: bool) -> PlanItem:
    """`.atc/atc.lock` moves to the ATC HEAD when sync changes any file it writes (GIT_WORKFLOW.md § Lock advancement)."""
    path = project_path / validator.ATC_LOCK
    source = governance_source(root)
    content = validator.render_lock(source, governance_revision(root))
    lock = validator.read_lock(project_path)
    if lock is None:
        return PlanItem("create", path, "ATC lock", content)
    if changed or lock["source"] != source:
        return PlanItem("update", path, "ATC lock moves to the synced revision", content)
    state = validator.lock_state(root, lock["revision"])
    if state == "stale":
        return PlanItem("update", path, "ATC lock revision is not in the history of main", content)
    if state == "not-main":
        return PlanItem("skip", path, "ATC lock not checked: the ATC checkout is not on main")
    return PlanItem("skip", path, "ATC lock is current")


def build_plan(project_path: Path, root: Path = ROOT, prune: bool = False) -> list[PlanItem]:
    """Every file sync writes, in its write order: `.atc/` bootloader and governance, the pointer line,
    `CLAUDE.md`, the adapters, then the lock and last the removal of the previous layout's pin file."""
    template_path = validator.pointer_template_path(root)
    if not template_path.exists():
        raise ValueError(f"{template_path}: missing canonical pointer line template")
    if not validator.atc_readme_template_path(root).exists():
        raise ValueError(f"{validator.atc_readme_template_path(root)}: missing canonical bootloader template")
    plan: list[PlanItem] = atc_plan(project_path, root)
    atc_collision = any(item.action == "collision" for item in plan)
    plan.append(pointer_plan(project_path, validator.pointer_line(root)))

    claude_source = (root / "CLAUDE.md").read_text(encoding="utf-8")
    claude_target = project_path / "CLAUDE.md"
    if not claude_target.exists():
        plan.append(PlanItem("create", claude_target, "workspace entrypoint", claude_source))
    elif claude_target.read_text(encoding="utf-8").strip() == claude_source.strip():
        plan.append(PlanItem("skip", claude_target, "workspace entrypoint is current"))
    elif validator.normalise(claude_target.read_text(encoding="utf-8")) == LEGACY_CLAUDE_MD:
        plan.append(PlanItem("update", claude_target, "previous workspace entrypoint; replaced by the import", claude_source))
    else:
        plan.append(
            PlanItem(
                "skip",
                claude_target,
                "differs from workspace entrypoint; no ownership marker, not modified",
            )
        )
    import testkit_contract
    testkit_record = testkit_contract.record(project_path)
    if testkit_record is not None:
        problems = testkit_contract.problems(project_path)
        plan.append(PlanItem('collision' if problems else 'skip',
                             testkit_record,
                             '; '.join(problems) if problems else 'testkit contract current; project configuration preserved'))

    canonical = canonical_skills(root)
    for skill, metadata in canonical.items():
        baseline = metadata.get("adoption") == "baseline"
        for tool_dir in validator.adapter_dirs(metadata):
            target = project_path / tool_dir / "skills" / skill / "SKILL.md"
            if not baseline and not target.exists():
                continue
            expected = validator.render_adapter(root, skill, tool_dir)
            if not target.exists() and holds_files(target.parent):
                plan.append(PlanItem("collision", target, UNOWNED_DIRECTORY))
            elif not target.exists():
                plan.append(PlanItem("create", target, "baseline managed adapter", expected))
            elif not managed_adapter(target, skill):
                plan.append(
                    PlanItem(
                        "collision",
                        target,
                        "adapter ownership or canonical identity is invalid or uncertain; refusing to modify",
                    )
                )
            elif validator.normalise(target.read_text(encoding="utf-8")) != expected:
                plan.append(PlanItem("update", target, "managed adapter drift", expected))
            else:
                plan.append(PlanItem("skip", target, "managed adapter is current"))
            if plan[-1].action != "collision":
                plan.extend(supporting_plan(root, skill, target.parent))
    plan.extend(orphan_plan(project_path, set(canonical)))
    if not atc_collision:
        changed = any(item.action in ("create", "update") or (prune and item.action == "prune") for item in plan)
        plan.append(lock_plan(project_path, root, changed))
        if legacy_manifest(project_path):
            plan.append(PlanItem("update", project_path / validator.LEGACY_MANIFEST, "migrated to .atc/atc.lock; sync removes it"))
    return plan


def verification_gate(project_path: Path, root: Path) -> str | None:
    """Why the lock cannot advance to the ATC HEAD (GIT_WORKFLOW.md § Lock advancement), or ``None``.

    Applies once the governance at HEAD carries § Integration verifier source;
    the section is parsed as `atc integrate` parses it."""
    workflow = root / "shared" / "governance" / "GIT_WORKFLOW.md"
    if not workflow.is_file() or not VERIFIER_SOURCE_HEADING.search(workflow.read_text(encoding="utf-8")):
        return None
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


def orphan_plan(project_path: Path, canonical: set[str]) -> list[PlanItem]:
    """Managed adapters whose canonical skill no longer exists (renamed or
    removed). Only a directory whose SKILL.md carries an ownership marker is
    listed; a project-owned skill directory is never touched."""
    plan: list[PlanItem] = []
    for tool_dir in validator.TOOL_DIRS:
        skills_dir = project_path / tool_dir / "skills"
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            skill_file = entry / "SKILL.md"
            if not entry.is_dir() or entry.name in canonical or not skill_file.is_file():
                continue
            validator.FAILURES.clear()
            metadata, _ = validator.parse_frontmatter(skill_file)
            validator.FAILURES.clear()
            if validator.is_managed(metadata):
                plan.append(PlanItem("prune", entry, f"orphaned managed adapter: no canonical skill '{entry.name}'"))
    return plan


def root_adapter_plan(root: Path = ROOT) -> list[PlanItem]:
    """Render every checkout-root adapter from its canonical skill.

    The root is a consumer like any project, so its `.claude/`, `.agents/`
    and `.dsh/` copies are the rendered output and are regenerated here; the
    shell installers copy them as they are."""
    plan: list[PlanItem] = []
    for skill, metadata in canonical_skills(root).items():
        for tool_dir in validator.adapter_dirs(metadata):
            target = root / tool_dir / "skills" / skill / "SKILL.md"
            expected = validator.render_adapter(root, skill, tool_dir)
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
                plan.extend(supporting_plan(root, skill, target.parent))
    return plan


def apply_plan(plan: list[PlanItem], mode: str, prune: bool = False) -> None:
    for item in plan:
        if item.action == "prune":
            if mode == "sync" and prune:
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


def print_plan(name: str, project_path: Path, plan: list[PlanItem], mode: str, prune: bool = False, verbose: bool = False) -> None:
    applied = {"apply": ("create",), "sync": ("create", "update") + (("prune",) if prune else ())}.get(mode, ())
    print(f"\n=== {name} ({project_path}) [{mode}{' --prune' if prune else ''}] ===")
    for action in ("create", "update", "prune", "collision", "skip"):
        items = [item for item in plan if item.action == action]
        if not items:
            continue
        if action == "skip" and not verbose:
            continue
        if action in ("create", "update", "prune"):
            verb = action if action in applied else f"would {action}"
        else:
            verb = action
        print(f"  {verb} ({len(items)}):")
        for item in items:
            print(f"    {item.path}: {item.detail}")
    if not verbose:
        counts = {action: sum(1 for item in plan if item.action == action) for action in ("skip", "update")}
        print(f"  skip {counts['skip']}, update {counts['update']}")


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


def root_commit_revision(root: Path) -> str:
    """The ATC revision a sync commit names: HEAD of the ATC checkout on main with no tracked change."""
    if validator.lock_state(root, "HEAD") != "current":
        raise SyncStop("ROOT_NOT_MAIN", "sync commits render from the ATC checkout on main; in a topic run --root-only")
    if _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise SyncStop("ROOT_DIRTY", "the ATC checkout has uncommitted tracked changes")
    return governance_revision(root)


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


def commit_sync(project_path: Path, plan: list[PlanItem], source: str, revision: str) -> str | None:
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
    _git(project_path, "commit", "-q", "-m", f"chore(governance): sync from {source} {revision[:7]}",
         "-m", f"Rendered by `atc onboard --sync` from {source} at {revision}.")
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
            "source_revision": revision,
            "task": revision,
        }, sort_keys=True) + "\n")
    return sha


def sync_and_commit(project_path: Path, plan: list[PlanItem], prune: bool, source: str, revision: str) -> str | None:
    """Write the plan into the project's main checkout and commit it, holding the project's Integration Slot."""
    check_project_checkout(project_path)
    slot = IntegrationSlot(project_path)
    if not slot.acquire(blocking=False):
        raise SyncStop("INTEGRATION_BUSY", f"another writer holds the integration slot at {slot.path}")
    try:
        check_project_checkout(project_path)
        apply_plan(plan, "sync", prune)
        return commit_sync(project_path, plan, source, revision)
    finally:
        slot.release()


def run(mode: str, targets: list[tuple[str, Path]], root: Path = ROOT, prune: bool = False, commit: bool = False, verbose: bool = False) -> int:
    """``commit`` makes a sync commit on each project's main (GIT_WORKFLOW.md "Governance sync commits")."""
    has_collision = False
    has_pending = False
    has_updates = False
    has_stop = False
    unpruned = False
    root_plan = root_adapter_plan(root)
    print_plan("checkout-root adapters", root, root_plan, mode, verbose=verbose)
    root_collision = any(item.action == "collision" for item in root_plan)
    has_collision |= root_collision
    has_pending |= any(item.action in ("create", "update") for item in root_plan)
    has_updates |= any(item.action == "update" for item in root_plan)
    if commit:
        try:
            if any(item.action in ("create", "update", "collision") for item in root_plan):
                raise SyncStop("ROOT_ADAPTER_DRIFT", "the ATC checkout's own adapters are not current; render them in the topic that changed the skill")
            revision = root_commit_revision(root)
        except SyncStop as stop:
            print(f"  stop: {stop.code} {stop.detail}")
            return 2
        source = governance_source(root)
    elif not root_collision or mode == "check":
        apply_plan(root_plan, mode)
    for name, project_path in targets:
        if not project_path.is_dir():
            print(f"\n=== {name} ({project_path}) [{mode}] ===")
            print("  readiness: NOT_INITIALIZED (registered repo path is absent)")
            continue
        plan = build_plan(project_path, root, prune)
        print_plan(name, project_path, plan, mode, prune, verbose)
        gate = verification_gate(project_path, root)
        ignored = ignored_outputs(project_path, plan)
        if gate or ignored:
            if gate:
                print(f"  stop: VERIFICATION_SECTION_MISSING {gate}; add it in the project first (GIT_WORKFLOW.md § Lock advancement)")
            if ignored:
                print(f"  stop: IGNORED_OUTPUT every file sync writes is committed; Git ignores or excludes: {', '.join(ignored)}")
            has_stop = True
            continue
        project_collision = any(item.action == "collision" for item in plan)
        has_collision |= project_collision
        has_pending |= any(item.action in ("create", "update", "prune") for item in plan)
        has_updates |= any(item.action == "update" for item in plan)
        unpruned |= (not prune) and any(item.action == "prune" for item in plan)
        if commit and not project_collision:
            try:
                sha = sync_and_commit(project_path, plan, prune, source, revision)
            except SyncStop as stop:
                print(f"  stop: {stop.code} {stop.detail}")
                has_stop = True
                continue
            print(f"  commit: {sha}" if sha else "  commit: none (no tracked file changed)")
        elif not commit and (not project_collision or mode == "check"):
            apply_plan(plan, mode, prune)
    if has_collision or has_stop:
        return 2
    if mode == "check" and has_pending:
        return 1
    if mode == "apply" and has_updates:
        return 1
    if mode == "sync" and unpruned:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_const", const="check", dest="mode")
    modes.add_argument("--apply", action="store_const", const="apply", dest="mode")
    modes.add_argument("--sync", action="store_const", const="sync", dest="mode")
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("project", nargs="?")
    targets.add_argument("--all", action="store_true")
    targets.add_argument("--root-only", action="store_true", help="render only this checkout's own adapters; commits nothing")
    parser.add_argument("--verbose", action="store_true", help="also list the items already current")
    parser.add_argument("--prune", action="store_true", help="with --sync: delete orphaned managed adapters (canonical skill renamed or removed)")
    return parser


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.prune and args.mode != "sync":
        parser.error("--prune only applies with --sync")
    try:
        if args.root_only:
            return run(args.mode, [], root, args.prune, verbose=args.verbose)
        projects = load_projects(root)
        if args.all:
            selected = projects.items()
        elif args.project not in projects:
            print(f"'{args.project}' is not registered in workspace.yaml.", file=sys.stderr)
            return 2
        else:
            selected = [(args.project, projects[args.project])]
        targets = [(name, (root / entry["repo"]).resolve()) for name, entry in selected]
        return run(args.mode, targets, root, args.prune, commit=args.mode == "sync", verbose=args.verbose)
    except ValueError as exc:
        print(f"onboard: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

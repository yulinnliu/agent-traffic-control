#!/usr/bin/env python3
"""Contract validator for an Agent Traffic Control checkout.

Checks the things that are easy to let drift silently: workspace.yaml.example's
shape, and canonical-skill vs. adapter consistency. An adapter is a managed
copy rendered from its canonical skill (``render_adapter``): same name and
description, the canonical body, this tool's overlay, and the ownership
fields ``managed-by`` and ``canonical``. A canonical
skill never depends on an environment variable naming this checkout, so the
copies work in a repository that knows nothing about it. No orphaned adapter
is left behind after a canonical skill is removed.
The task records under `.atc/tasks/` and their `## Task records` declaration
are checked here and, with --projects, in each project, along with one task
prefix per repository (`docs/TASKS.md`).

By default only checks this repo's own `.claude/skills/`, `.agents/skills/`
and `.dsh/skills/` — the ones CI can see. Pass --projects to also check each
project listed in the real (gitignored, machine-local) workspace.yaml, for
whichever of those projects actually exist on disk right now — each must
carry adapters for every `adoption: baseline` skill, the pointer line in
AGENTS.md and a current `.atc/` (lock, bootloader, vendored governance), all
tracked — and to reject user-level duplicates of a canonical skill under
~/.claude/skills, ~/.agents/skills or ~/.dsh/skills. This only makes sense to
run locally, not in CI, since projects/ isn't present in a fresh CI checkout.

Third-party dependencies: PyYAML, and jsonschema for the task record check.

Exit code is 0 iff everything below passed; non-zero otherwise, with every
failure printed (not just the first one).
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml

import verification_section

ROOT = Path(__file__).resolve().parents[3]
FAILURES: list[str] = []
MANAGED_BY = "agent-traffic-control"
ROOT_TOPICS_NAME = "root"
ROOT_WORKTREES = f"worktrees/{ROOT_TOPICS_NAME}"
FORBIDDEN_FALLBACK = re.compile(r"~[\\/]hami(?:[\\/]|\b)", re.IGNORECASE)
# A canonical skill or a shipped file must not locate this checkout through
# the environment; that is what makes the managed copies self-contained.
SUPPORTING_PATH = re.compile(r"[A-Za-z0-9._/-]+")
WORKSPACE_ENV_REFERENCE = re.compile(r"\$(?:\{)?ATC_ROOT\b|\$env:ATC_ROOT\b")
HARDCODED_GOVERNANCE_SOURCE = re.compile(r"[A-Za-z0-9_.-]+/agent-traffic-control\b")
POINTER_MARKER = "<!-- atc:governance -->"
GOVERNANCE_INDEX_RELATIVE = Path("shared") / "governance" / "INDEX.md"
POLICY_RELATIVE = GOVERNANCE_INDEX_RELATIVE
GOVERNANCE_MODULES = ("CORE.md", "GIT_WORKFLOW.md", "VERIFICATION.md", "DELIVERY.md", "FAILURE_ESCALATION.md", "GOVERNANCE_CHANGES.md", "PROJECT_RUNTIME.md")
ATC_DIR = Path(".atc")
VENDORED_GOVERNANCE = ATC_DIR / "governance"
ATC_LOCK = ATC_DIR / "atc.lock"
ATC_README = ATC_DIR / "README.md"
TASKS_DIR = ATC_DIR / "tasks"
LOCK_VERSION = 1
VENDORED_INDEX_REFERENCE = ".atc/governance/INDEX.md"
# GIT_WORKFLOW.md § Integration verifier source: the section every project's
# AGENTS.md carries and the two fixed lines validate reads out of it.
VERIFICATION_SECTION = verification_section.HEADING
# Agent catalogs truncate longer descriptions, which can cut off routing cues.
MAX_DESCRIPTION = 500
TOOL_DIRS = (".claude", ".agents", ".dsh")
# Tool directory -> the overlay name a canonical skill may ship under adapters/.
TOOL_KEYS = {".claude": "claude", ".agents": "codex", ".dsh": "dsh"}


def pointer_template_path(root: Path | None = None) -> Path:
    return (root or ROOT) / "shared" / "templates" / "PROJECT_AGENTS.md"


def atc_readme_template_path(root: Path | None = None) -> Path:
    return (root or ROOT) / "shared" / "templates" / "ATC_README.md"


def pointer_line(root: Path | None = None) -> str:
    """The one line sync places in a project's AGENTS.md."""
    return normalise(pointer_template_path(root).read_text(encoding="utf-8")).strip()


def render_lock(source: str, revision: str) -> str:
    """`.atc/atc.lock`: three LF-terminated lines in this key order (docs/LAYOUT.md)."""
    return f'version = {LOCK_VERSION}\nsource = "{source}"\nrevision = "{revision}"\n'


def parse_lock(text: str) -> dict:
    """The lock's fields; ValueError says what is wrong."""
    import tomllib
    try:
        doc = tomllib.loads(normalise(text))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"not valid TOML ({exc})") from None
    version = doc.get("version")
    if isinstance(version, bool) or version != LOCK_VERSION:
        raise ValueError(f"version {version!r} is not {LOCK_VERSION}")
    for key in ("source", "revision"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            raise ValueError(f"missing '{key}'")
    return doc


def read_lock(project_path: Path) -> dict | None:
    """The project's lock, ``None`` when it has none; ValueError for a lock that does not parse."""
    path = project_path / ATC_LOCK
    if not path.is_file():
        return None
    return parse_lock(path.read_text(encoding="utf-8"))


def atc_strays(repo: Path, sync_owned: bool = True) -> list[Path]:
    """Files under ``repo``'s `.atc/` that neither sync nor the project owns, relative to ``repo``.

    Sync owns `README.md`, `atc.lock` and the vendored modules under
    `governance/`; `tasks/` is the project's. With ``sync_owned`` false (the
    workspace root, which is the governance source) only `tasks/` belongs."""
    atc = repo / ATC_DIR
    if not atc.is_dir():
        return []
    allowed = {ATC_README, ATC_LOCK, *(VENDORED_GOVERNANCE / module for module in vendored_governance_modules())} if sync_owned else set()
    strays = []
    for path in sorted(atc.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = path.relative_to(repo)
        if relative.parts[:2] == TASKS_DIR.parts or Path(*relative.parts) in allowed:
            continue
        strays.append(relative)
    return strays


def fail(msg: str) -> None:
    FAILURES.append(msg)


def normalise(text: str) -> str:
    return text.replace("\r\n", "\n")


def looks_absolute(path_str: str) -> bool:
    """Cross-platform "is this an absolute path" check for a path *string*
    from a config file — Path.is_absolute() depends on the OS the validator
    happens to run on (e.g. it's False for POSIX-style '/foo' on Windows),
    but the string may be written for either convention.
    """
    return bool(
        re.match(r"^(/|\\\\|[A-Za-z]:[\\/])", path_str)
    )


def split_frontmatter(text: str) -> tuple[str, str] | None:
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", text, re.DOTALL)
    return (m.group(1), m.group(2)) if m else None


def parse_frontmatter(path: Path) -> tuple[dict, str]:
    """Return (frontmatter dict, body) for a SKILL.md-style file."""
    text = path.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if not parts:
        fail(f"{path}: no frontmatter block found")
        return {}, text
    fm_text, body = parts
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as e:
        fail(f"{path}: frontmatter is not valid YAML ({e})")
        return {}, body
    if not isinstance(fm, dict):
        fail(f"{path}: frontmatter must be a mapping")
        return {}, body
    return fm, body


def is_managed(fm: Mapping) -> bool:
    return fm.get("managed-by") == MANAGED_BY


MANAGED_MARKER = re.compile(r"(?m)^managed-by:\s*" + re.escape(MANAGED_BY) + r"\s*$")


def managed_marker_present(raw: str) -> bool:
    return MANAGED_MARKER.search(raw) is not None


def load_and_check_workspace_yaml(path: Path) -> tuple[str | None, dict[str, dict]]:
    """Parse and shape-check a workspace.yaml-style file (used for both
    workspace.yaml.example and the real local workspace.yaml).

    Never raises — every shape problem is recorded via fail(), and a
    malformed project entry is simply dropped rather than returned. Returns
    (workspace_root value or None, {name: entry} for every project entry
    that passed all shape checks).
    """
    if not path.exists():
        fail(f"{path}: missing")
        return None, {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        fail(f"{path}: not valid YAML ({e})")
        return None, {}
    if not isinstance(doc, dict):
        fail(f"{path}: top level must be a mapping")
        return None, {}

    if doc.get("version") != 1:
        fail(f"{path}: 'version' must be exactly 1, got {doc.get('version')!r}")
    root_val = doc.get("workspace_root")
    if not isinstance(root_val, str) or not root_val.strip():
        fail(f"{path}: 'workspace_root' must be a non-empty string")
        root_val = None
    elif not looks_absolute(root_val):
        fail(f"{path}: 'workspace_root' must be an absolute path, got {root_val!r}")
        root_val = None

    if "projects" not in doc:
        fail(f"{path}: missing top-level key 'projects'")
        return root_val, {}
    projects = doc.get("projects")
    if not isinstance(projects, dict):
        fail(f"{path}: 'projects' must be a mapping")
        return root_val, {}

    safe_projects: dict[str, dict] = {}
    for name, entry in projects.items():
        if not isinstance(entry, dict):
            fail(f"{path}: projects.{name} must be a mapping")
            continue
        entry_ok = True
        for field in ("repo", "worktrees", "state"):
            if field not in entry:
                fail(f"{path}: projects.{name} missing '{field}'")
                entry_ok = False
            elif not isinstance(entry[field], str):
                fail(f"{path}: projects.{name}.{field} must be a string path")
                entry_ok = False
        if entry_ok:
            reserved = reserved_root_problem(name, entry["worktrees"], root_val)
            if reserved:
                fail(f"{path}: projects.{name}: {reserved}")
                continue
            safe_projects[name] = entry
    return root_val, safe_projects


def reserved_root_problem(name: str, worktrees: str, workspace_root: str | None = None) -> str | None:
    """The workspace root's own worktrees live under `worktrees/root`, so a project may neither take
    the name nor a worktrees root that contains it or lies inside it."""
    if name.lower() == ROOT_TOPICS_NAME:
        return f"the name '{ROOT_TOPICS_NAME}' is reserved for the workspace root's worktrees"
    spelled = worktrees.replace("\\", "/")
    if looks_absolute(spelled):
        if workspace_root is None:
            return None
        base = workspace_root.replace("\\", "/").rstrip("/")
        if spelled.lower() == base.lower():
            spelled = "."
        elif spelled.lower().startswith(base.lower() + "/"):
            spelled = spelled[len(base) + 1:]
        else:
            return None
    parts = [p for p in posixpath.normpath(spelled).lower().split("/") if p != "."]
    reserved = ROOT_WORKTREES.split("/")
    shared = min(len(parts), len(reserved))
    if parts[:shared] == reserved[:shared]:
        return f"worktrees '{worktrees}' overlaps '{ROOT_WORKTREES}', reserved for the workspace root's worktrees"
    return None


def description_line(fm_text: str) -> str | None:
    """The raw single-line ``description:`` entry, copied verbatim into adapters."""
    m = re.search(r"(?m)^description: (\S.*)$", fm_text)
    return m.group(1).rstrip() if m else None


def overlay_files(canonical_dir: Path) -> list[Path]:
    overlays = canonical_dir / "adapters"
    return sorted(overlays.glob("*.md")) if overlays.is_dir() else []


def load_canonical_skills() -> dict[str, dict]:
    """Return {name: frontmatter} for every shared/skills/<name>/SKILL.md."""
    skills_dir = ROOT / "shared" / "skills"
    canonical: dict[str, dict] = {}
    if not skills_dir.is_dir():
        fail(f"{skills_dir}: missing")
        return canonical
    for canonical_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        name = canonical_dir.name
        canonical_file = canonical_dir / "SKILL.md"
        if not canonical_file.exists():
            fail(f"{canonical_file}: missing canonical SKILL.md for '{name}'")
            continue
        fm, body = parse_frontmatter(canonical_file)
        if fm.get("name") != name:
            fail(
                f"{canonical_file}: frontmatter name '{fm.get('name')}' != "
                f"directory name '{name}'"
            )
        description = fm.get("description")
        if not isinstance(description, str) or not description.strip():
            fail(f"{canonical_file}: missing description")
        elif len(description) > MAX_DESCRIPTION:
            fail(f"{canonical_file}: description is {len(description)} characters, limit {MAX_DESCRIPTION}")
        else:
            parts = split_frontmatter(canonical_file.read_text(encoding="utf-8"))
            raw = description_line(parts[0]) if parts else None
            if raw is None or yaml.safe_load(f"description: {raw}").get("description") != description:
                fail(f"{canonical_file}: description must be one plain `description: ...` line so adapters can copy it verbatim")
        contract = fm.get("contract")
        if "contract" in fm and not valid_contract(contract):
            fail(f"{canonical_file}: contract must be a positive integer, got {contract!r}")
        adoption = fm.get("adoption")
        if adoption not in (None, "baseline"):
            fail(
                f"{canonical_file}: adoption must be 'baseline' or omitted, "
                f"got {adoption!r}"
            )
        if fm.get("invocation") not in (None, "user"):
            fail(f"{canonical_file}: invocation must be 'user' or omitted, got {fm.get('invocation')!r}")
        shipped = [(body, canonical_file)] + [(p.read_text(encoding="utf-8"), p) for p in overlay_files(canonical_dir)]
        for text, where in shipped:
            if WORKSPACE_ENV_REFERENCE.search(text):
                fail(
                    f"{where}: locates this checkout through an environment variable — a "
                    "canonical skill must work as a self-contained copy in any repository"
                )
            if FORBIDDEN_FALLBACK.search(text):
                fail(f"{where}: hardcodes a '~/Hami'-style path")
        try:
            supporting_files(ROOT, name)
        except ValueError as exc:
            fail(str(exc))
        for overlay in overlay_files(canonical_dir):
            if overlay.stem not in TOOL_KEYS.values():
                fail(f"{overlay}: overlay name must be one of {', '.join(TOOL_KEYS.values())}")
        canonical[name] = fm
    return canonical


def valid_contract(value: object) -> bool:
    """A contract declaration the catalog accepts: a positive integer, not a bool."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def declares_contract(canonical_fm: dict) -> bool:
    """Whether the frontmatter declares a contract the contract check accepts.

    The same predicate the check applies, so an invalid declaration never pulls in
    the DSH adapter requirement on its way to being rejected.
    """
    return valid_contract(canonical_fm.get("contract"))


def adapter_dirs(canonical_fm: dict) -> tuple[str, ...]:
    """Tool directories a canonical skill needs adapters in.

    Every skill gets a Claude Code and a Codex-compatible adapter (DSH also
    discovers the latter). A skill that declares a ``contract`` binds the
    calling harness's identity, so DSH needs its own higher-priority adapter.
    """
    return TOOL_DIRS if declares_contract(canonical_fm) else TOOL_DIRS[:2]


def render_adapter(root: Path, name: str, tool_dir: str) -> str:
    """The managed copy of canonical skill ``name`` for ``tool_dir``.

    Frontmatter: name and description copied verbatim, this tool's invocation
    flag, ``requires-contract`` when the skill declares a contract, then the
    ownership fields. Body: the canonical body, followed by the tool overlay
    ``shared/skills/<name>/adapters/<tool>.md`` when one exists.
    """
    canonical_dir = root / "shared" / "skills" / name
    text = canonical_dir.joinpath("SKILL.md").read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if not parts:
        raise ValueError(f"{canonical_dir / 'SKILL.md'}: no frontmatter block found")
    fm_text, body = parts
    fm = yaml.safe_load(fm_text) or {}
    description = description_line(fm_text)
    if description is None:
        raise ValueError(f"{canonical_dir / 'SKILL.md'}: description must be one plain line")
    lines = [f"name: {fm.get('name', name)}", f"description: {description}"]
    if fm.get("invocation") == "user" and tool_dir == ".claude":
        lines.append("disable-model-invocation: true")
    if declares_contract(fm):
        lines.append(f"requires-contract: {fm['contract']}")
    lines += [f"managed-by: {MANAGED_BY}", f"canonical: {name}"]
    rendered = "---\n" + "\n".join(lines) + "\n---\n" + normalise(body).rstrip("\n") + "\n"
    overlay = canonical_dir / "adapters" / f"{TOOL_KEYS[tool_dir]}.md"
    if overlay.is_file():
        rendered += "\n" + normalise(overlay.read_text(encoding="utf-8")).strip("\n") + "\n"
    return rendered


def supporting_files(root: Path, name: str) -> dict[str, str]:
    """Files canonical skill ``name`` ships beside its SKILL.md, keyed by POSIX path
    relative to the skill directory. Every adapter carries them verbatim so a
    relative link in the body resolves in any repository; the ``adapters/``
    overlays are rendered into SKILL.md instead. A supporting file is UTF-8 text
    under a plain path (the shell installers split on whitespace); anything else
    raises ValueError before a plan writes anything."""
    canonical_dir = root / "shared" / "skills" / name
    files: dict[str, str] = {}
    for path in sorted(canonical_dir.rglob("*")):
        relative = path.relative_to(canonical_dir)
        if not path.is_file() or relative.as_posix() == "SKILL.md" or relative.parts[0] == "adapters":
            continue
        if "__pycache__" in relative.parts:
            continue
        if not SUPPORTING_PATH.fullmatch(relative.as_posix()):
            raise ValueError(f"{path}: supporting file path must use only letters, digits, '.', '_', '-' and '/'")
        try:
            files[relative.as_posix()] = normalise(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            raise ValueError(f"{path}: supporting file must be UTF-8 text") from None
    return files


def adapter_extras(adapter_dir: Path, expected: dict[str, str]) -> list[Path]:
    """Files in a managed adapter directory that neither its SKILL.md nor its
    canonical supporting files account for."""
    return sorted(
        path for path in adapter_dir.rglob("*")
        if path.is_file()
        and path.relative_to(adapter_dir).as_posix() != "SKILL.md"
        and path.relative_to(adapter_dir).as_posix() not in expected
        and "__pycache__" not in path.relative_to(adapter_dir).parts
    )


def check_supporting_files(adapter_dir: Path, name: str) -> None:
    try:
        expected = supporting_files(ROOT, name)
    except ValueError:
        return  # reported once against the canonical skill by load_canonical_skills
    for relative, content in expected.items():
        target = adapter_dir / relative
        if not target.is_file():
            fail(f"{target}: missing supporting file of shared/skills/{name}/ — run sync to copy it")
        elif normalise(target.read_text(encoding="utf-8")) != content:
            fail(f"{target}: drifted from shared/skills/{name}/{relative} — run sync to re-copy it")
    for extra in adapter_extras(adapter_dir, expected):
        fail(f"{extra}: not in shared/skills/{name}/ — run sync to remove it")


def check_one_adapter(adapter_file: Path, name: str, canonical_fm: dict) -> bool:
    """Check one adapter's SKILL.md; return whether it is managed."""
    adapter_fm, _ = parse_frontmatter(adapter_file)

    if not is_managed(adapter_fm):
        fail(
            f"{adapter_file}: name collision — '{name}' matches a canonical "
            f"shared skill but is not explicitly marked managed-by: {MANAGED_BY}; "
            "sync must not modify it"
        )
        return False
    check_managed_adapter(adapter_file, name, canonical_fm, adapter_fm)
    return True


def check_managed_adapter(adapter_file: Path, name: str, canonical_fm: dict, adapter_fm: dict) -> None:
    if adapter_fm.get("canonical") != name:
        fail(f"{adapter_file}: canonical must be '{name}'")
    if adapter_fm.get("name") != canonical_fm.get("name"):
        fail(
            f"{adapter_file}: name '{adapter_fm.get('name')}' does not match "
            f"canonical '{canonical_fm.get('name')}'"
        )
    if adapter_fm.get("description") != canonical_fm.get("description"):
        fail(
            f"{adapter_file}: description drifted from canonical "
            f"shared/skills/{name}/SKILL.md — run sync to re-render it"
        )
        return
    tool_dir = adapter_file.parents[2].name
    try:
        expected = render_adapter(ROOT, name, tool_dir)
    except (ValueError, KeyError) as exc:
        fail(f"{adapter_file}: cannot render the expected adapter ({exc})")
        return
    if normalise(adapter_file.read_text(encoding="utf-8")) != expected:
        fail(
            f"{adapter_file}: content drifted from canonical "
            f"shared/skills/{name}/SKILL.md — run sync to re-render it"
        )


def check_adapter_tree(
    adapters_root: Path,
    canonical: dict[str, dict],
    label: str,
    required: set[str],
) -> None:
    """Check .claude/skills, .agents/skills and .dsh/skills under adapters_root.

    Every skill in ``required`` must have an adapter in each of its
    ``adapter_dirs`` here: the workspace
    root requires every canonical skill, a registered project requires the
    ``adoption: baseline`` ones. Other canonical skills are optional for a
    project and are checked only when adopted. Orphaned adapters are always
    flagged, and so is a local skill whose name collides with a canonical
    skill's name (ambiguous for agent discovery); unrelated local skills are
    ignored.
    """
    for tool_dir in (d.removeprefix(".") for d in TOOL_DIRS):
        tool_skills_dir = adapters_root / f".{tool_dir}" / "skills"
        if not tool_skills_dir.is_dir():
            actual_names: set[str] = set()
            adapter_like: set[str] = set()
        else:
            actual_names = {p.name for p in tool_skills_dir.iterdir() if p.is_dir()}
            adapter_like = set()
            for name in actual_names:
                skill_file = tool_skills_dir / name / "SKILL.md"
                if skill_file.exists() and managed_marker_present(skill_file.read_text(encoding="utf-8")):
                    adapter_like.add(name)

        orphan = adapter_like - set(canonical)
        for name in sorted(orphan):
            fail(
                f"{label}: .{tool_dir}/skills/{name}/ is a managed adapter with "
                "no canonical counterpart in shared/skills/ — orphaned "
                "adapter (canonical skill renamed or deleted without "
                "updating this)"
            )

        needed = {name for name in required if f".{tool_dir}" in adapter_dirs(canonical[name])}
        for name in sorted(needed - actual_names):
            fail(f"{label}: .{tool_dir}/skills/{name}/ missing")

        for name in sorted(actual_names & set(canonical)):
            adapter_file = tool_skills_dir / name / "SKILL.md"
            if not adapter_file.exists():
                fail(f"{adapter_file}: directory exists but SKILL.md missing")
                continue
            if check_one_adapter(adapter_file, name, canonical[name]):
                check_supporting_files(adapter_file.parent, name)


def check_templates() -> None:
    """The pointer line and the bootloader sync writes into every project."""
    pointer = pointer_template_path()
    if not pointer.exists():
        fail(f"{pointer}: missing")
    else:
        lines = normalise(pointer.read_text(encoding="utf-8")).strip().splitlines()
        if len(lines) != 1 or not lines[0].startswith(POINTER_MARKER) or ATC_README.as_posix() not in lines[0]:
            fail(f"{pointer}: must be one line starting {POINTER_MARKER} and naming {ATC_README.as_posix()}")
    readme = atc_readme_template_path()
    if not readme.exists():
        fail(f"{readme}: missing")
        return
    text = readme.read_text(encoding="utf-8")
    if VENDORED_INDEX_REFERENCE not in text:
        fail(f"{readme}: missing the vendored governance index reference {VENDORED_INDEX_REFERENCE}")
    if "governance revision" not in text.lower() or ATC_LOCK.as_posix() not in text:
        fail(f"{readme}: missing the exact governance revision lock ({ATC_LOCK.as_posix()})")
    if HARDCODED_GOVERNANCE_SOURCE.search(text):
        fail(f"{readme}: hard-codes a governance source instead of referring to the locked one")
    if FORBIDDEN_FALLBACK.search(text) or WORKSPACE_ENV_REFERENCE.search(text):
        fail(f"{readme}: locates governance through a machine-local path or environment variable")


def check_no_hardcoded_governance_source() -> None:
    for path in (ROOT / "AGENTS.md", ROOT / "shared" / "governance" / "CORE.md"):
        if path.is_file() and HARDCODED_GOVERNANCE_SOURCE.search(path.read_text(encoding="utf-8")):
            fail(f"{path}: hard-codes a governance source instead of referring to governance_source")


def check_project_pointer(project_path: Path, label: str) -> None:
    """AGENTS.md carries the current pointer line before its first section."""
    agents_md = project_path / "AGENTS.md"
    if not agents_md.exists():
        fail(f"{label}: {agents_md} missing")
        return
    text = normalise(agents_md.read_text(encoding="utf-8"))
    lines = text.splitlines()
    pointer = next((index for index, line in enumerate(lines) if line.startswith(POINTER_MARKER)), None)
    if pointer is None:
        fail(f"{label}: AGENTS.md has no {POINTER_MARKER} pointer line — run sync")
        return
    if lines[pointer] != pointer_line():
        fail(f"{label}: AGENTS.md pointer line differs from shared/templates/PROJECT_AGENTS.md — run sync")
    heading = next((index for index, line in enumerate(lines) if line.startswith("## ")), None)
    if heading is not None and heading < pointer:
        fail(f"{label}: AGENTS.md pointer line comes after its first section")
    if HARDCODED_GOVERNANCE_SOURCE.search(text):
        fail(f"{label}: AGENTS.md hard-codes a governance source instead of referring to the locked one")
    if FORBIDDEN_FALLBACK.search(text):
        fail(f"{label}: AGENTS.md contains forbidden ~/Hami fallback")


def check_project_integration_verifier(project_path: Path, label: str) -> None:
    """AGENTS.md names an existing verifier argv file for `atc integrate` (GIT_WORKFLOW.md § Integration verifier source)."""
    agents_md = project_path / "AGENTS.md"
    if not agents_md.is_file():
        return  # check_project_policy_reference reports the missing file
    declared = verification_section.declaration(agents_md.read_text(encoding="utf-8"))
    if declared is None:
        fail(
            f"{label}: AGENTS.md has no '{VERIFICATION_SECTION}' section naming the atc integrate "
            "verifier argv file (GIT_WORKFLOW.md § Integration verifier source)"
        )
        return
    if declared.argv_file is None:
        fail(f"{label}: AGENTS.md '{VERIFICATION_SECTION}' has no 'Verifier argv file: <repo-relative path>' line")
        return
    relative = declared.argv_file
    if looks_absolute(relative) or ".." in Path(relative).parts:
        fail(f"{label}: verifier argv file {relative} must be a path inside the repository")
        return
    argv_file = project_path / relative
    if not argv_file.is_file():
        fail(f"{label}: verifier argv file {relative} named by AGENTS.md does not exist")
        return
    import git_integrate
    try:
        git_integrate.parse_verify_command(argv_file.read_text(encoding="utf-8-sig"))
    except git_integrate.Refused as exc:
        fail(f"{label}: verifier argv file {relative}: {exc}")
    if declared.timeout is not None and declared.timeout <= 0:
        fail(f"{label}: AGENTS.md verifier timeout must be greater than zero")


def check_task_records(path: Path, label: str) -> str | None:
    """The task records and declaration of ``path`` (docs/TASKS.md § Commands); returns its valid prefix, if any."""
    import task_records
    for problem in task_records.problems(path):
        fail(f"{label}: task records: {problem}")
    declared = task_records.declared_prefix(path)
    return declared if declared and task_records.prefix_problem(declared) is None else None


def check_unique_prefixes(prefixes: Mapping[str, str | None]) -> None:
    """One task prefix per repository across the workspace root and the registered projects."""
    holders: dict[str, list[str]] = {}
    for label, prefix in prefixes.items():
        if prefix:
            holders.setdefault(prefix, []).append(label)
    for prefix, labels in sorted(holders.items()):
        if len(labels) > 1:
            fail(f"task prefix {prefix} is declared by {', '.join(labels)}; a prefix names one repository")


def vendored_governance_modules() -> tuple[str, ...]:
    return ("INDEX.md", *GOVERNANCE_MODULES)


def lock_state(root: Path, revision: str) -> str:
    """`current` when `revision` is in the history of `root`'s HEAD on `main`, else `stale`.

    `not-main` when the checkout is on another branch, whose history would
    re-lock projects to a commit that disappears with the branch, and
    `unverifiable` when `root` has no commits.
    """
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env)

    try:
        if git("rev-parse", "--verify", "-q", "HEAD").returncode != 0:
            return "unverifiable"
        if git("symbolic-ref", "-q", "--short", "HEAD").stdout.strip() != "main":
            return "not-main"
        return "current" if git("merge-base", "--is-ancestor", revision, "HEAD").returncode == 0 else "stale"
    except FileNotFoundError:
        return "unverifiable"


def check_project_enforcement(project_path: Path, label: str) -> None:
    """A project with policies/hard-deny.yaml commits generated/enforcement/ rendered from it."""
    if not (project_path / "policies" / "hard-deny.yaml").is_file():
        return
    import generate_enforcement
    import policies
    try:
        hard = policies.load(project_path / "policies")["hard-deny"]
    except policies.PolicyError as exc:
        for problem in exc.problems:
            fail(f"{label}: {problem}")
        return
    problems = generate_enforcement.unresolved_tokens(hard)
    for problem in problems:
        fail(f"{label}: {problem}")
    if problems:
        return
    drift = generate_enforcement.drift(generate_enforcement.render(hard), project_path / "generated" / "enforcement")
    if drift:
        fail(f"{label}: generated/enforcement/ differs from policies/hard-deny.yaml: {', '.join(drift)} — run atc enforce")


def report_machine_local(project_path: Path, label: str, workspace_yaml: Path) -> None:
    """Report, never fail: `.claude/settings.local.json` carries the state-root deny `atc enforce` renders."""
    import generate_enforcement
    try:
        roots = generate_enforcement.state_roots(workspace_yaml)
    except ValueError:
        return  # load_and_check_workspace_yaml already failed on workspace_root
    if not roots:
        return
    target = project_path / ".claude" / "settings.local.json"
    expected = json.loads(generate_enforcement.local_settings(roots))["permissions"]["deny"]
    current = generate_enforcement.machine_local(generate_enforcement.deny_entries(target), keep=True)
    if not current:
        print(f"--projects: {label}: machine-local state deny missing from .claude/settings.local.json; run atc enforce --local-out and merge")
    elif current != expected:
        print(f"--projects: {label}: machine-local state deny in .claude/settings.local.json differs from {workspace_yaml.name}; run atc enforce --local-out and merge")


def report_hook(workspace_yaml: Path) -> None:
    """Report, never fail: an installed PreToolUse hook table or entry that differs from the policies."""
    import generate_enforcement
    if not workspace_yaml.is_file():
        return
    found = generate_enforcement.hook_drift(workspace_yaml)
    if found:
        print(f"--projects: pretooluse hook differs from the policies: {'; '.join(found)}; the Owner runs atc enforce --install-hooks")


def check_atc_dir(project_path: Path, label: str) -> None:
    """The project's `.atc/`: a valid lock, the current bootloader and vendored governance, nothing else but `tasks/`."""
    for stray in atc_strays(project_path):
        fail(f"{label}: {stray.as_posix()} is not a file sync writes; .atc/ holds README.md, atc.lock, governance/ and the project's tasks/")
    try:
        lock = read_lock(project_path)
    except ValueError as exc:
        fail(f"{label}: {ATC_LOCK.as_posix()}: {exc}")
        lock = None
    else:
        if lock is None:
            fail(f"{label}: {ATC_LOCK.as_posix()} missing — run sync")
    if lock is not None and lock_state(ROOT, lock["revision"]) == "stale":
        fail(f"{label}: {ATC_LOCK.as_posix()} revision {lock['revision']} is not in the history of main — run sync")
    readme = project_path / ATC_README
    if not readme.is_file():
        fail(f"{label}: {ATC_README.as_posix()} missing — run sync")
    elif normalise(readme.read_text(encoding="utf-8")) != normalise(atc_readme_template_path().read_text(encoding="utf-8")):
        fail(f"{label}: {ATC_README.as_posix()} differs from shared/templates/ATC_README.md — run sync")
    for module in vendored_governance_modules():
        source = ROOT / "shared" / "governance" / module
        target = project_path / VENDORED_GOVERNANCE / module
        if not target.is_file():
            fail(f"{label}: {(VENDORED_GOVERNANCE / module).as_posix()} missing — run sync")
        elif source.is_file() and normalise(target.read_text(encoding="utf-8")) != normalise(source.read_text(encoding="utf-8")):
            fail(f"{label}: {(VENDORED_GOVERNANCE / module).as_posix()} differs from shared/governance/{module} — run sync")


def sync_written_paths(project_path: Path, canonical: dict[str, dict]) -> list[str]:
    """The repository-relative paths sync writes that exist in ``project_path``."""
    paths = ["AGENTS.md", ATC_README.as_posix(), ATC_LOCK.as_posix(), VENDORED_GOVERNANCE.as_posix()]
    claude = project_path / "CLAUDE.md"
    if claude.is_file() and normalise(claude.read_text(encoding="utf-8")) == normalise((ROOT / "CLAUDE.md").read_text(encoding="utf-8")):
        paths.append("CLAUDE.md")
    paths += [f"{tool_dir}/skills/{name}" for name in canonical for tool_dir in TOOL_DIRS]
    return [path for path in paths if (project_path / path).exists()]


def git_toplevel(path: Path) -> bool:
    """Whether ``path`` is the top of its own Git working tree, as a project's checkout is."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, env=env)
    # realpath on both sides: Git prints the long form of a Windows 8.3 short path.
    return result.returncode == 0 and os.path.normcase(os.path.realpath(result.stdout.strip())) == os.path.normcase(os.path.realpath(path))


def check_tracked(project_path: Path, label: str, canonical: dict[str, dict]) -> None:
    """Every file sync writes is committed (GIT_WORKFLOW.md § Governance sync commits).

    A file Git ignores or excludes can never be committed and fails; a file
    not committed yet, as `atc bootstrap add` leaves its first onboarding, is
    reported."""
    paths = sync_written_paths(project_path, canonical)
    if not paths or not git_toplevel(project_path):
        return
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}

    def untracked(*options: str) -> list[str]:
        result = subprocess.run(["git", "-C", str(project_path), "ls-files", "--others", *options, "-z", "--", *paths],
                                capture_output=True, text=True, encoding="utf-8", env=env)
        return [] if result.returncode else sorted(entry for entry in result.stdout.split("\0") if entry)

    for path in untracked("--ignored", "--exclude-standard"):
        fail(f"{label}: {path} is written by sync but Git ignores or excludes it; every file sync writes is committed")
    for path in untracked("--exclude-standard"):
        print(f"--projects: {label}: {path} is written by sync and not committed yet")


def check_root_atc() -> None:
    """The workspace root is the governance source: its `.atc/` holds `tasks/` alone."""
    for stray in atc_strays(ROOT, sync_owned=False):
        fail(f"workspace root: {stray.as_posix()}: the root's .atc/ holds only tasks/")


def check_rules_referenced_in_agents_md() -> None:
    governance_dir = ROOT / "shared" / "governance"
    index = governance_dir / "INDEX.md"
    if not index.is_file():
        fail(f"{index}: missing canonical governance router")
        return
    text = index.read_text(encoding="utf-8")
    for name in GOVERNANCE_MODULES:
        if not (governance_dir / name).is_file():
            fail(f"{governance_dir / name}: missing canonical governance module")
        if name not in text:
            fail(f"{index}: does not route to {name}")
    for path in sorted(governance_dir.glob("*.md")):
        if path.name != "INDEX.md" and path.name not in GOVERNANCE_MODULES:
            fail(f"{path}: not listed in GOVERNANCE_MODULES, so sync never vendors it")


def check_shipped_files_are_self_contained() -> None:
    """Governance travels into other repositories; it cannot point back here."""
    for module in vendored_governance_modules():
        path = ROOT / "shared" / "governance" / module
        if path.is_file() and WORKSPACE_ENV_REFERENCE.search(path.read_text(encoding="utf-8")):
            fail(f"{path}: locates this checkout through an environment variable; it is vendored into repositories that have none")


def workspace_env_problems(root: Path, environ: Mapping[str, str]) -> dict[str, str | None]:
    """Check the optional ``ATC_ROOT`` override against the checkout at ``root``.

    Nothing requires the variable: tools find their root from their own
    location or from the nearest ``workspace.yaml``. When it is set it must
    agree. Returns {variable: problem or None}; an unset variable is omitted.
    """
    results: dict[str, str | None] = {}
    root = root.resolve()
    value = environ.get("ATC_ROOT")
    if value:
        path = Path(value).expanduser()
        if not path.exists():
            results["ATC_ROOT"] = f"{value} does not exist"
        elif not path.is_dir():
            results["ATC_ROOT"] = f"{value} is a file; ATC_ROOT must be the checkout directory"
        elif path.resolve() != root:
            results["ATC_ROOT"] = f"resolves to {path.resolve()}, expected {root}"
        elif not (path / POLICY_RELATIVE).is_file():
            results["ATC_ROOT"] = f"canonical governance index missing: {path / POLICY_RELATIVE}"
        else:
            results["ATC_ROOT"] = None
    return results


def check_workspace_env() -> None:
    for variable, problem in workspace_env_problems(ROOT, os.environ).items():
        if problem:
            fail(f"{variable}: {problem}")


def baseline_skills(canonical: dict[str, dict]) -> set[str]:
    """Canonical skills every registered project must carry adapters for."""
    return {name for name, fm in canonical.items() if fm.get("adoption") == "baseline"}


def check_user_level_duplicates(canonical: dict[str, dict], home: Path) -> None:
    """Local-only: a user-level copy of a canonical skill shadows every adapter."""
    for name in sorted(canonical):
        for tool_dir in TOOL_DIRS:
            duplicate = home / tool_dir / "skills" / name / "SKILL.md"
            if duplicate.exists():
                fail(
                    f"{duplicate}: user-level duplicate of canonical shared skill "
                    f"'{name}' — remove it; the workspace/project adapters are the only entry points"
                )


def check_projects(canonical: dict[str, dict], project_checkouts: dict[str, Path] | None = None,
                   root_prefix: str | None = None) -> None:
    """Local-only: check each configured project's adapters, if it exists."""
    project_checkouts = project_checkouts or {}
    prefixes: dict[str, str | None] = {"workspace root": root_prefix}
    real_workspace_yaml = ROOT / "workspace.yaml"
    if not real_workspace_yaml.exists():
        if project_checkouts:
            fail("--project-checkout requires workspace.yaml so candidate names can be verified as registered")
        else:
            print(
                "--projects: no workspace.yaml at the workspace root — nothing "
                "to check (this is expected in CI; run locally after setting "
                "one up)."
            )
        return

    root_val, safe_projects = load_and_check_workspace_yaml(real_workspace_yaml)

    if root_val is not None:
        configured_root = Path(root_val).expanduser().resolve()
        if configured_root != ROOT.resolve():
            fail(
                f"{real_workspace_yaml}: workspace_root ({configured_root}) "
                f"does not match this repo's actual location ({ROOT.resolve()}) "
                "— workspace.yaml's own location and workspace_root must agree"
            )

    for name in sorted(set(project_checkouts) - set(safe_projects)):
        fail(f"--project-checkout names unregistered project {name!r}")

    for name, entry in safe_projects.items():
        explicit_candidate = name in project_checkouts
        project_path = project_checkouts.get(name, ROOT / entry["repo"]).expanduser().resolve()
        if not project_path.is_dir():
            if explicit_candidate:
                fail(f"projects.{name}: explicit candidate checkout {project_path} is not a directory")
            else:
                print(f"--projects: '{name}' -> {project_path} not present, skipping")
            continue
        import testkit_contract
        for problem in testkit_contract.problems(project_path):
            fail(f"projects.{name}.testkit: {problem}")
        check_project_pointer(project_path, label=f"projects.{name}")
        check_atc_dir(project_path, label=f"projects.{name}")
        check_tracked(project_path, f"projects.{name}", canonical)
        check_project_integration_verifier(project_path, label=f"projects.{name}")
        check_project_enforcement(project_path, label=f"projects.{name}.enforcement")
        report_machine_local(project_path, f"projects.{name}.machine-local", real_workspace_yaml)
        check_adapter_tree(
            project_path, canonical, label=f"projects.{name}", required=baseline_skills(canonical)
        )
        prefixes[f"projects.{name}"] = check_task_records(project_path, label=f"projects.{name}")
    check_unique_prefixes(prefixes)
    report_hook(real_workspace_yaml)


def main(argv: list[str] | None = None) -> int:
    import cli_conventions
    parser = argparse.ArgumentParser(prog="atc validate", allow_abbrev=False, **cli_conventions.form(
        "Check this checkout's contract: workspace.yaml.example, canonical skills against their managed adapters,\n"
        "and task records. With --projects, also every registered project on disk.",
        exit="0 every check passed; 1 problems found, all of them printed; 2 usage error",
        see="adapters/python/README.md § validate"))
    parser.add_argument("--projects", action="store_true",
                        help="also check each project in the local workspace.yaml and user-level skill copies")
    parser.add_argument("--adapters-only", action="store_true", help="check only canonical skills and their adapters")
    parser.add_argument("--root", metavar="DIR", help="the ATC checkout to validate, as the global atc --root")
    parser.add_argument("--project-checkout", action="append", default=[], metavar="NAME=PATH",
                        help="also validate this checkout as registered project NAME (repeatable)")
    cli_conventions.add_json(parser)
    args = parser.parse_args(argv)
    if args.root:
        global ROOT
        ROOT = Path(args.root).resolve()
    project_checkouts = {}
    for raw in args.project_checkout:
        if "=" not in raw:
            parser.error("--project-checkout expects NAME=PATH")
        name, path = raw.split("=", 1)
        project_checkouts[name] = Path(path)

    if not args.adapters_only:
        load_and_check_workspace_yaml(ROOT / "workspace.yaml.example")
    canonical = load_canonical_skills()
    check_adapter_tree(ROOT, canonical, label="workspace root", required=set(canonical))
    root_prefix = None
    if not args.adapters_only:
        root_prefix = check_task_records(ROOT, label="workspace root")
        check_templates()
        check_root_atc()
        check_no_hardcoded_governance_source()
        check_rules_referenced_in_agents_md()
        check_shipped_files_are_self_contained()
        check_workspace_env()

    if args.projects or project_checkouts:
        check_projects(canonical, project_checkouts, root_prefix=root_prefix)
        check_user_level_duplicates(canonical, Path.home())

    if args.json:
        print(json.dumps({"verdict": "PROBLEMS" if FAILURES else "OK", "problems": FAILURES}, indent=2))
        return 1 if FAILURES else 0
    if FAILURES:
        print(f"validate_workspace: {len(FAILURES)} problem(s) found:\n")
        for f in FAILURES:
            print(f"  - {f}")
        return 1

    print("validate_workspace: all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The release tree's manifest: what `atc sync` delivers and where (docs/LAYOUT.md § The manifest).

`atc-manifest.yaml` at the root of a revision names, per scope, each delivered item's source in that revision,
its target and its mode. The fields and the four modes are the tool's format; the manifest names only paths.
`project` items go into every registered project, `workspace` items into a workspace directory that is not
itself the governance checkout. A revision without a manifest is not a sync target (`NO_MANIFEST`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

FILE = "atc-manifest.yaml"
FORMAT = 1
OWNED, POINTER, CREATE_ONLY, SKILLS = "owned", "pointer-line", "create-only", "skill-render"
MODES = (OWNED, POINTER, CREATE_ONLY, SKILLS)
SCOPES = ("project", "workspace")
HARNESS_TARGET = "{harness}/skills/"
# A project's `owned` targets lie in .atc/ outside tasks/, so the path alone tells a reader what sync owns there.
ATC_DIR, TASKS_DIR = ".atc/", ".atc/tasks/"
KEYS = {"manifest", "min_atc", "project", "workspace"}
ENTRY_KEYS = {"id", "source", "target", "mode", "files"}
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class ManifestError(ValueError):
    """A manifest that cannot be used; ``token`` names the refusal."""

    def __init__(self, token: str, detail: str):
        super().__init__(f"{token} {detail}")
        self.token = token
        self.detail = detail


@dataclass(frozen=True)
class Entry:
    id: str
    source: str
    target: str
    mode: str
    files: tuple[str, ...] = ()

    @property
    def directory(self) -> bool:
        return self.source.endswith("/")


@dataclass(frozen=True)
class Manifest:
    min_atc: tuple[int, int, int]
    project: tuple[Entry, ...]
    workspace: tuple[Entry, ...] = ()

    def entries(self, scope: str = "project") -> tuple[Entry, ...]:
        return self.project if scope == "project" else self.workspace

    def one(self, mode: str, scope: str = "project") -> Entry | None:
        found = [entry for entry in self.entries(scope) if entry.mode == mode]
        return found[0] if found else None

    def files(self, mode: str, scope: str = "project") -> list[tuple[str, str]]:
        """``(source, target)`` of every single file the ``mode`` items deliver, a directory item's ``files``
        expanded; both are repository-relative POSIX paths."""
        pairs: list[tuple[str, str]] = []
        for entry in self.entries(scope):
            if entry.mode != mode:
                continue
            if entry.directory:
                pairs += [(entry.source + name, entry.target + name) for name in entry.files]
            else:
                pairs.append((entry.source, entry.target))
        return pairs

    def skills_source(self, scope: str = "project") -> str | None:
        """The skill-render item's source directory, without its trailing slash."""
        entry = self.one(SKILLS, scope)
        return entry.source.rstrip("/") if entry else None

    def governance_modules(self) -> tuple[str, ...]:
        """The module names the `owned` directory item into `.atc/governance/` delivers."""
        for entry in self.project:
            if entry.mode == OWNED and entry.directory and entry.target == ".atc/governance/":
                return entry.files
        return ()


def present(tree: Path) -> bool:
    return (tree / FILE).is_file()


def load(tree: Path) -> Manifest:
    """The manifest of the revision exported to ``tree``; ``NO_MANIFEST`` without one, ``MANIFEST_INVALID``
    for one that breaks the format, ``MANIFEST_SOURCE_MISSING`` for a source the tree lacks."""
    path = tree / FILE
    if not path.is_file():
        raise ManifestError("NO_MANIFEST", f"the revision has no {FILE}; atc renders only revisions that carry one")
    found = parse(path.read_text(encoding="utf-8"))
    for scope in SCOPES:
        for entry in found.entries(scope):
            sources = [tree / source for source, _ in found.files(entry.mode, scope)] if entry.mode == OWNED \
                else [tree / entry.source]
            for source in sources:
                if not (source.is_dir() if entry.mode == SKILLS else source.is_file()):
                    raise ManifestError("MANIFEST_SOURCE_MISSING",
                                        f"{FILE} {scope}.{entry.id}: {source.relative_to(tree).as_posix()} is not in the revision")
    return found


def _invalid(detail: str) -> ManifestError:
    return ManifestError("MANIFEST_INVALID", f"{FILE}: {detail}")


def _relative(value: object, where: str, directory: bool | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(f"{where} must be a non-empty string")
    pure = PurePosixPath(value)
    if "\\" in value or pure.is_absolute() or re.match(r"^[A-Za-z]:", value) or ".." in pure.parts:
        raise _invalid(f"{where} {value!r} must be a relative POSIX path inside the tree, without '..'")
    if directory is not None and value.endswith("/") != directory:
        raise _invalid(f"{where} {value!r} must {'end' if directory else 'not end'} with '/'")
    return value


def _entry(raw: object, where: str, scope: str) -> Entry:
    if not isinstance(raw, dict):
        raise _invalid(f"{where} must be a mapping")
    unknown = set(raw) - ENTRY_KEYS
    if unknown:
        raise _invalid(f"{where} has unknown keys {', '.join(sorted(map(str, unknown)))}")
    if not isinstance(raw.get("id"), str) or not raw["id"]:
        raise _invalid(f"{where}.id must be a non-empty string")
    where = f"{scope}.{raw['id']}"
    mode = raw.get("mode")
    if mode not in MODES:
        raise _invalid(f"{where}.mode must be one of {', '.join(MODES)}")
    if mode == SKILLS:
        source = _relative(raw.get("source"), f"{where}.source", directory=True)
        if raw.get("target") != HARNESS_TARGET:
            raise _invalid(f"{where}.target must be {HARNESS_TARGET!r}")
        target = HARNESS_TARGET
    else:
        source = _relative(raw.get("source"), f"{where}.source")
        target = _relative(raw.get("target"), f"{where}.target", directory=source.endswith("/"))
    if mode == OWNED and scope == "project" and not (target.startswith(ATC_DIR) and not target.startswith(TASKS_DIR)):
        raise _invalid(f"{where}.target {target!r}: a project's owned item lies under {ATC_DIR}, outside {TASKS_DIR}")
    files: tuple[str, ...] = ()
    if source.endswith("/") and mode != SKILLS:
        if mode != OWNED:
            raise _invalid(f"{where}: only an owned item may name a directory")
        listed = raw.get("files")
        if not isinstance(listed, list) or not listed:
            raise _invalid(f"{where}.files must list the files of {source}")
        files = tuple(_relative(name, f"{where}.files", directory=False) for name in listed)
        if any("/" in name for name in files) or len(set(files)) != len(files):
            raise _invalid(f"{where}.files must be distinct file names")
    elif "files" in raw:
        raise _invalid(f"{where}.files is only for an owned directory item")
    return Entry(raw["id"], source, target, mode, files)


def parse(text: str) -> Manifest:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _invalid(f"is not YAML ({exc})") from None
    if not isinstance(doc, dict):
        raise _invalid("must be a mapping")
    unknown = set(doc) - KEYS
    if unknown:
        raise _invalid(f"unknown keys {', '.join(sorted(map(str, unknown)))}")
    if doc.get("manifest") != FORMAT:
        raise _invalid(f"manifest must be {FORMAT}, the format this atc reads")
    version = VERSION.fullmatch(str(doc.get("min_atc", "")))
    if version is None:
        raise _invalid("min_atc must be MAJOR.MINOR.PATCH")
    scopes: dict[str, tuple[Entry, ...]] = {}
    for scope in SCOPES:
        raw = doc.get(scope, [] if scope == "workspace" else None)
        if not isinstance(raw, list):
            raise _invalid(f"{scope} must be a list")
        entries = tuple(_entry(item, f"{scope}[{index}]", scope) for index, item in enumerate(raw))
        ids = [entry.id for entry in entries]
        if len(set(ids)) != len(ids):
            raise _invalid(f"{scope} repeats an id")
        scopes[scope] = entries
    for mode in (POINTER, SKILLS):
        if sum(entry.mode == mode for entry in scopes["project"]) != 1:
            raise _invalid(f"project must hold exactly one {mode} item")
    if any(entry.mode == POINTER for entry in scopes["workspace"]):
        raise _invalid(f"workspace holds no {POINTER} item")
    found = Manifest(tuple(int(part) for part in version.groups()), scopes["project"], scopes["workspace"])
    if not found.governance_modules():
        raise _invalid("project must hold the owned directory item into .atc/governance/")
    return found


def check_version(found: Manifest, running: str) -> None:
    """``MANIFEST_TOO_NEW`` when the running atc is older than ``min_atc``; an unparsable version is not compared."""
    version = VERSION.match(running)
    if version and tuple(int(part) for part in version.groups()) < found.min_atc:
        needed = ".".join(map(str, found.min_atc))
        raise ManifestError("MANIFEST_TOO_NEW", f"the revision needs atc {needed} or later; this is atc {running}")

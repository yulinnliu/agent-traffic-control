"""`atc-maintain release prepare|check`: a release's version pins, its task list and its notes
(docs/development/RELEASING.md; design docs/design/ATC-0064.md)."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import tomllib

import cli_conventions

NOTES = "docs/releases"
MANIFEST = "atc-manifest.yaml"
TASKS = ".atc/tasks"
LEDGER = "atc/integrations.jsonl"
PLACEHOLDER = "TODO(release)"
PROJECTS_LINK = "../TESTKIT.md#upgrading-a-projects-testkit"
TESTKIT_PROJECT = "adapters/python/atc-testkit/pyproject.toml"
SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")
TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
MIN_ATC = re.compile(r"(?m)^min_atc: *['\"]?(\d+\.\d+\.\d+)['\"]? *$")
TASK_LINE = re.compile(r"(?m)^- ([A-Z][A-Z0-9]*-\d+):")


@dataclass(frozen=True)
class Pin:
    """One place a version is written: the file, a pattern whose group 1 is the version, and which version."""
    path: str
    pattern: re.Pattern[str]
    package: str  # "atc" or "testkit"


PINS = (
    Pin("adapters/python/pyproject.toml", re.compile(r'(?m)^version = "(\d+\.\d+\.\d+)"$'), "atc"),
    Pin("adapters/python/uv.lock", re.compile(r'(?m)^name = "agent-traffic-control"\nversion = "(\d+\.\d+\.\d+)"$'),
        "atc"),
    Pin("README.md", re.compile(r"Version (\d+\.\d+\.\d+), the version the Python adapter carries"), "atc"),
    Pin("README.zh-TW.md", re.compile(r"目前版本是 (\d+\.\d+\.\d+)，"), "atc"),
    Pin("adapters/python/atc-testkit/src/atc_testkit/__init__.py", re.compile(r'(?m)^__version__ = "(\d+\.\d+\.\d+)"$'),
        "testkit"),
    Pin("adapters/python/atc-testkit/README.md", re.compile(r"`atc-testkit` (\d+\.\d+\.\d+);"), "testkit"),
    Pin("adapters/python/atc-testkit/README.md", re.compile(r"`atc-testkit==(\d+\.\d+\.\d+)`"), "testkit"),
)
TESTKIT_SOURCE, TESTKIT_PIN = PINS[4].path, PINS[4].pattern


def version(text: str) -> tuple[int, int, int]:
    found = SEMVER.fullmatch(text)
    if found is None:
        raise ValueError(f"{text!r} is not MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in found.groups())  # type: ignore[return-value]


def git(root: Path, *args: str) -> str | None:
    """Git's stdout in ``root``, or None when the command fails (an export without history, a shallow clone)."""
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL)
    return done.stdout if done.returncode == 0 else None


def read(root: Path, path: str) -> str | None:
    try:
        return (root / path).read_text(encoding="utf-8")
    except OSError:
        return None


def pinned(root: Path) -> tuple[dict[str, set[str]], list[str]]:
    """Every pinned version by package, and one problem per pin that is missing or written more than once."""
    found: dict[str, set[str]] = {"atc": set(), "testkit": set()}
    problems = []
    for pin in PINS:
        text = read(root, pin.path)
        matches = pin.pattern.findall(text) if text is not None else []
        if len(matches) != 1:
            problems.append(f"RELEASE_VERSION_MISMATCH {pin.path}: {len(matches)} matches of {pin.pattern.pattern}")
            continue
        found[pin.package].add(matches[0])
    return found, problems


def last_tag(root: Path) -> tuple[str, tuple[int, int, int]] | None:
    """The highest `vX.Y.Z` tag reachable from HEAD, or None when the history holds none."""
    out = git(root, "tag", "--merged", "HEAD", "--list", "v*")
    tags = [(name, tuple(int(p) for p in found.groups())) for name in (out or "").split()
            if (found := TAG.fullmatch(name))]
    return max(tags, key=lambda item: item[1]) if tags else None  # type: ignore[return-value]


def tasks_since(root: Path, tag: str) -> list[str]:
    """The tasks whose record the index adds since ``tag``: a task's record reaches main with its integration."""
    out = git(root, "diff", "--cached", "--name-only", "--diff-filter=A", tag, "--", TASKS) or ""
    return sorted(Path(line).stem for line in out.splitlines() if line.endswith(".yaml"))


def ledger_tasks(root: Path, tag: str) -> list[str]:
    """The tasks the integration ledger records with a commit in ``tag..HEAD``, oldest first; records-only lines
    are left out."""
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    commits = set((git(root, "rev-list", f"{tag}..HEAD") or "").split())
    if common is None:
        return []
    ledger = Path(common.strip()) / LEDGER
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found: list[str] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or "candidate_sha" not in entry or entry.get("records_only") \
                or not entry.get("task"):
            continue
        if commits & {entry["candidate_sha"], *entry.get("commits", ())} and entry["task"] not in found:
            found.append(entry["task"])
    return found


def sections(text: str) -> list[tuple[int, str, int]]:
    """Each heading as (level, title, offset)."""
    return [(len(m.group(1)), m.group(2).strip(), m.start()) for m in re.finditer(r"(?m)^(#{2,3}) (.+)$", text)]


def section(text: str, title: str, level: int = 2) -> str | None:
    """The body of the heading ``title`` at ``level``, up to the next heading at that level or above."""
    heads = sections(text)
    for index, (lvl, name, start) in enumerate(heads):
        if lvl == level and name == title:
            end = next((s for lv, _n, s in heads[index + 1:] if lv <= level), len(text))
            return text[start:end]
    return None


def check(root: Path) -> tuple[list[str], list[str]]:
    """The release problems of the checkout at ``root``, and the notes (what was not checked)."""
    found, problems = pinned(root)
    notes: list[str] = []
    for package, versions in found.items():
        if len(versions) > 1:
            problems.append(f"RELEASE_VERSION_MISMATCH {package} is pinned as {', '.join(sorted(versions))}")
    try:
        testkit_project = tomllib.loads(read(root, TESTKIT_PROJECT) or "")
        project, dynamic = testkit_project["project"], testkit_project["tool"]["setuptools"]["dynamic"]
        if "version" in project or project.get("dynamic") != ["version"] \
                or dynamic.get("version") != {"attr": "atc_testkit.__version__"}:
            raise KeyError("version")
    except (KeyError, TypeError, tomllib.TOMLDecodeError):
        problems.append(f"RELEASE_VERSION_MISMATCH {TESTKIT_PROJECT}: the version must be dynamic from "
                        "atc_testkit.__version__")
    if len(found["atc"]) != 1 or len(found["testkit"]) != 1:
        return problems, notes
    atc, testkit = next(iter(found["atc"])), next(iter(found["testkit"]))
    minimum = MIN_ATC.search(read(root, MANIFEST) or "")
    if minimum is None:
        problems.append(f"RELEASE_MIN_ATC {MANIFEST}: no min_atc line")
    elif version(minimum.group(1)) > version(atc):
        problems.append(f"RELEASE_MIN_ATC min_atc {minimum.group(1)} is above atc {atc}")
    path = f"{NOTES}/v{atc}.md"
    text = read(root, path)
    if text is None:
        problems.append(f"RELEASE_NOTES_MISSING {path}")
        return problems, notes
    if text.split("\n", 1)[0].strip() != f"atc {atc} (atc-testkit {testkit})":
        problems.append(f"RELEASE_VERSION_MISMATCH {path}: the first line is not `atc {atc} (atc-testkit {testkit})`")
    tag = last_tag(root)
    if tag is None:
        notes.append("NOTE no release tag in this history: the notes' sections and tasks are not checked")
        return problems, notes
    if tag[0] == f"v{atc}":
        return problems, notes  # released: the version is tagged, the notes are history
    if version(atc) <= tag[1]:
        problems.append(f"RELEASE_NOT_AHEAD atc {atc} is not above the last tag {tag[0]}")
    problems += notes_problems(root, path, text, tag[0], testkit)
    return problems, notes


def notes_problems(root: Path, path: str, text: str, tag: str, testkit: str) -> list[str]:
    """The sections and task list of release notes in preparation."""
    problems = []
    tagged = TESTKIT_PIN.search(git(root, "show", f"{tag}:{TESTKIT_SOURCE}") or "")
    required = ["Breaking", "Tasks", "Upgrade", "Assets"]
    if tagged is None or tagged.group(1) != testkit:
        required.insert(1, f"atc-testkit {testkit}")
    problems += [f"RELEASE_SECTION_MISSING {path}: ## {title}" for title in required if section(text, title) is None]
    upgrade = section(text, "Upgrade") or ""
    projects = section(upgrade, "Projects", 3)
    if projects is None:
        problems.append(f"RELEASE_SECTION_MISSING {path}: ### Projects under ## Upgrade")
    elif PROJECTS_LINK not in projects:
        problems.append(f"RELEASE_SECTION_MISSING {path}: ### Projects does not link to {PROJECTS_LINK}")
    if PLACEHOLDER in text:
        problems.append(f"RELEASE_PLACEHOLDER {path}: {text.count(PLACEHOLDER)} {PLACEHOLDER} left")
    listed = TASK_LINE.findall(section(text, "Tasks") or "")
    expected = tasks_since(root, tag)
    problems += [f"RELEASE_TASK_MISSING {path}: {task} (its record was added since {tag})"
                 for task in expected if task not in listed]
    problems += [f"RELEASE_TASK_EXTRA {path}: {task} (no record added since {tag})"
                 for task in listed if task not in expected]
    return problems


def scaffold(atc: str, testkit: str, tasks: list[str], testkit_changed: bool) -> str:
    todo = f"{PLACEHOLDER}: see docs/development/RELEASING.md"
    parts = [f"atc {atc} (atc-testkit {testkit})", "",
             f"{todo} § Release notes, the summary paragraph.", "",
             "## Breaking", "", f"{todo} § Breaking.", ""]
    if testkit_changed:
        parts += [f"## atc-testkit {testkit}", "", f"{todo} § The atc-testkit table.", ""]
    parts += ["## Tasks", "", *[f"- {task}: {PLACEHOLDER}" for task in tasks], "",
              "## Upgrade", "", "The Owner's steps on each host, in this order:", "",
              "1. Run no `atc test`, verifier or other `atc` command while uv replaces `atc`: `atc doctor`",
              "   reports its `atc environment` line READY. On Windows, rerun the install after a `WinError 32`.",
              f"2. {todo} § Upgrade.", "",
              "### Projects", "",
              f"{todo} § Projects: this version's delta only. A project that upgrades its",
              f"vendored atc-testkit follows [Upgrading a project's testkit]({PROJECTS_LINK}).", "",
              "## Assets", "", "Verify the downloads with `sha256sum -c SHA256SUMS`.", ""]
    return "\n".join(parts)


def prepare(root: Path, atc: str, testkit: str | None, min_atc: str | None) -> int:
    found, problems = pinned(root)
    if problems or len(found["atc"]) != 1 or len(found["testkit"]) != 1:
        print(*problems, f"ERROR RELEASE_VERSION_MISMATCH the pins disagree: {found}", sep="\n")
        return cli_conventions.ERROR
    old = {"atc": next(iter(found["atc"])), "testkit": next(iter(found["testkit"]))}
    new = {"atc": atc, "testkit": testkit or old["testkit"]}
    tag = last_tag(root)
    try:
        for value in (*new.values(), *([min_atc] if min_atc else [])):
            version(value)
    except ValueError as exc:
        print(f"ERROR RELEASE_USAGE {exc}")
        return cli_conventions.ERROR
    if tag is None:
        print("ERROR RELEASE_NO_TAG no vX.Y.Z tag is reachable from HEAD; fetch the tags")
        return cli_conventions.ERROR
    if version(atc) <= tag[1]:
        print(f"ERROR RELEASE_NOT_AHEAD atc {atc} is not above the last tag {tag[0]}")
        return cli_conventions.ERROR
    if min_atc and version(min_atc) > version(atc):
        print(f"ERROR RELEASE_MIN_ATC min_atc {min_atc} is above atc {atc}")
        return cli_conventions.ERROR
    notes = root / NOTES / f"v{atc}.md"
    if notes.exists():
        print(f"ERROR RELEASE_NOTES_EXIST {NOTES}/v{atc}.md; prepare never overwrites notes")
        return cli_conventions.ERROR
    # Every edit is computed before the first write, so a refusal leaves the tree as it was.
    texts: dict[str, str] = {}
    changes: list[str] = []
    if min_atc:
        manifest = read(root, MANIFEST) or ""
        match = MIN_ATC.search(manifest)
        if match is None:
            print(f"ERROR RELEASE_MIN_ATC {MANIFEST}: no min_atc line")
            return cli_conventions.ERROR
        texts[MANIFEST] = manifest[:match.start(1)] + min_atc + manifest[match.end(1):]
        changes.append(f"UPDATED {MANIFEST} min_atc {match.group(1)} -> {min_atc}")
    for pin in PINS:
        if old[pin.package] != new[pin.package]:
            text = texts.get(pin.path) or (root / pin.path).read_text(encoding="utf-8")
            match = pin.pattern.search(text)
            assert match is not None  # pinned() found exactly one
            texts[pin.path] = text[:match.start(1)] + new[pin.package] + text[match.end(1):]
            changes.append(f"UPDATED {pin.path} {old[pin.package]} -> {new[pin.package]}")
    for path, text in texts.items():
        (root / path).write_text(text, encoding="utf-8", newline="\n")
    print(*changes, sep="\n")
    tree = tasks_since(root, tag[0])
    ledger = ledger_tasks(root, tag[0])
    ordered = [task for task in ledger if task in tree] + [task for task in tree if task not in ledger]
    if only := [task for task in tree if task not in ledger]:
        print(f"NOTE not in the integration ledger since {tag[0]}: {', '.join(only)} "
              "(this release's own task, or integrated on another host)")
    if only := [task for task in ledger if task not in tree]:
        print(f"NOTE in the ledger but no record added since {tag[0]}: {', '.join(only)}; the notes list "
              "the records (docs/development/RELEASING.md § Tasks)")
    tagged = TESTKIT_PIN.search(git(root, "show", f"{tag[0]}:{TESTKIT_SOURCE}") or "")
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text(scaffold(atc, new["testkit"], ordered, tagged is None or tagged.group(1) != new["testkit"]),
                     encoding="utf-8", newline="\n")
    print(f"WROTE {NOTES}/v{atc}.md ({len(ordered)} tasks)")
    return cli_conventions.OK


def main(argv: list[str], root: Path) -> int:
    parser = argparse.ArgumentParser(prog="atc-maintain release", allow_abbrev=False, **cli_conventions.form(
        "Prepare a release's version pins and notes, or check them.",
        exit="0 OK; 1 problems found (check); 2 ERROR", see="docs/development/RELEASING.md"))
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    prepare_parser = sub.add_parser("prepare", allow_abbrev=False, help="bump the pins, list the tasks, scaffold "
                                    "the notes", **cli_conventions.form(
                                        "Bump every pinned version, list the tasks since the last tag and write "
                                        "the notes' scaffold; commits nothing.",
                                        exit="0 OK; 2 ERROR", see="docs/development/RELEASING.md § Prepare"))
    prepare_parser.add_argument("atc", metavar="VERSION", help="the new atc version, MAJOR.MINOR.PATCH")
    prepare_parser.add_argument("--testkit", metavar="VERSION", help="the new atc-testkit version (default: unchanged)")
    prepare_parser.add_argument("--min-atc", metavar="VERSION", help="the new min_atc of atc-manifest.yaml "
                                "(default: unchanged)")
    check_parser = sub.add_parser("check", allow_abbrev=False, help="check the pins, min_atc and the notes",
                                  **cli_conventions.form(
                                      "Check the version pins, min_atc and, before the version is tagged, the "
                                      "notes' sections and tasks; one problem per line.",
                                      exit="0 no problem; 1 problems found; 2 ERROR",
                                      see="docs/development/RELEASING.md § Check"))
    cli_conventions.add_json(check_parser)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        return prepare(root, args.atc, args.testkit, args.min_atc)
    problems, notes = check(root)
    if args.json:
        print(json.dumps({"verdict": "PROBLEMS" if problems else "OK", "problems": problems, "notes": notes},
                         indent=2))
    else:
        print(*notes, *problems, f"{'PROBLEMS ' + str(len(problems)) if problems else 'OK'} release check",
              sep="\n")
    return cli_conventions.STOP if problems else cli_conventions.OK

"""Input-keyed evidence: a check's result keyed by its identity and the contents of the files it reads.

Formats and the rules: shared/conformance/verification-evidence/README.md § Input evidence at the workspace
governance revision; docs/TESTKIT.md § Input evidence.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path


SCHEMA = "atc-input-evidence/1"
EVIDENCE_DIR = ("atc", "verification-evidence")
STORE = (*EVIDENCE_DIR, "inputs")
EXCLUDED = "excluded"
KEEP_SECONDS = 14 * 86400
CLASSIFY = "ATC_INPUTS_OF"
OUTCOMES = "ATC_FILE_OUTCOMES"
COLLECTED = "ATC_COLLECTED_FILES"
KIND = "test-file"

Entry = tuple[str, str, str]


def entries(git: Callable[..., str], commit: str) -> list[Entry]:
    """The ``(mode, blob, path)`` of every file of ``commit``'s tree, from ``git ls-tree -r``."""
    listed = git("ls-tree", "-r", "-z", "--full-tree", commit)
    found = []
    for line in listed.split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            mode, _kind, blob = meta.split(" ")
            found.append((mode, blob, path))
    return found


def digest(tree: Iterable[Entry], excluded: Iterable[str] = ()) -> str:
    """The sha256 of the sorted ``<mode> <blob> <path>`` lines of ``tree``'s paths outside ``excluded``."""
    skip = frozenset(excluded)
    lines = sorted(f"{mode} {blob} {path}" for mode, blob, path in tree if path not in skip)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def non_inputs(paths: Iterable[str], non_input: Callable[[str], bool]) -> list[str]:
    """The sorted paths the project's documentation-only classifier holds no check reads."""
    return sorted(path for path in paths if non_input(path))


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def key(identity: Mapping, inputs: str) -> str:
    return hashlib.sha256(canonical({"identity": identity, "inputs": inputs})).hexdigest()


def identity_name(identity: Mapping) -> str:
    return hashlib.sha256(canonical(identity)).hexdigest()[:32]


def store(common: Path) -> Path:
    return common.joinpath(*STORE)


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    staged.write_bytes(data)
    os.replace(staged, path)


def keep_excluded(common: Path, excluded: Sequence[str]) -> str:
    """Store the excluded path list once, by its digest, and return the digest."""
    data = canonical(sorted(excluded))
    name = hashlib.sha256(data).hexdigest()
    path = store(common) / EXCLUDED / f"{name}.json"
    if path.is_file():
        os.utime(path)
    else:
        _atomic(path, data)
    return name


def read_excluded(common: Path, name: str) -> list[str] | None:
    try:
        value = json.loads((store(common) / EXCLUDED / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def write(common: Path, identity: Mapping, tree: Sequence[Entry], excluded: Sequence[str], ran_on: str,
          now: float | None = None) -> Path:
    """Write the immutable record of a passed check that ran on commit ``ran_on``; an existing one stays."""
    inputs = digest(tree, excluded)
    record_key = key(identity, inputs)
    path = store(common) / f"{identity_name(identity)}-{record_key}.json"
    if not path.is_file():
        stamp = time.time() if now is None else now
        record = {"schema": SCHEMA, "key": record_key, "identity": dict(identity), "inputs": inputs,
                  "excluded": keep_excluded(common, excluded), "ran_on": ran_on, "result": "PASS",
                  "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))}
        _atomic(path, json.dumps(record, indent=1).encode("utf-8"))
    return path


def _well_formed(record: object) -> bool:
    return (isinstance(record, dict) and record.get("schema") == SCHEMA and record.get("result") == "PASS"
            and all(isinstance(record.get(name), str) for name in ("key", "inputs", "excluded", "ran_on"))
            and isinstance(record.get("identity"), dict))


class Finder:
    """Looks records up for the tree of one commit, reading each excluded list once."""

    def __init__(self, common: Path, tree: Sequence[Entry]):
        self.common, self.tree = common, list(tree)
        self._digests: dict[str, str | None] = {}

    def _digest(self, name: str) -> str | None:
        if name not in self._digests:
            excluded = read_excluded(self.common, name)
            self._digests[name] = None if excluded is None else digest(self.tree, excluded)
        return self._digests[name]

    def find(self, identity: Mapping) -> dict | None:
        """The newest record of ``identity`` whose inputs equal this tree's outside the record's excluded paths."""
        found = []
        for path in store(self.common).glob(f"{identity_name(identity)}-*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (_well_formed(record) and record["identity"] == dict(identity)
                    and record["key"] == key(identity, record["inputs"])
                    and self._digest(record["excluded"]) == record["inputs"]):
                found.append(record)
        return max(found, key=lambda record: record.get("recorded_at", ""), default=None)


def has_records(common: Path) -> bool:
    directory = store(common)
    return directory.is_dir() and any(directory.glob("*.json"))


def prune(common: Path, now: float | None = None) -> None:
    """Delete records and excluded lists older than 14 days."""
    cutoff = (time.time() if now is None else now) - KEEP_SECONDS
    directory = store(common)
    for path in itertools.chain(directory.glob("*.json"), (directory / EXCLUDED).glob("*.json")):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def common_directory(root: Path) -> Path:
    done = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                          capture_output=True, text=True, check=True)
    return Path(done.stdout.strip())


def git_reader(root: Path) -> Callable[..., str]:
    def run(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
                              encoding="utf-8").stdout
    return run


def repository_files(listing: Path, top: Path, outcome: str | None = None) -> list[str] | None:
    """The test files a plugin listing names (``ATC_COLLECTED_FILES``, or with ``outcome`` the files of
    ``ATC_FILE_OUTCOMES`` that had it), each by its path from the repository's top ``top``; ``None`` when the
    listing is missing or unreadable."""
    try:
        data = json.loads(listing.read_text(encoding="utf-8"))
        rootdir, files = Path(data["rootdir"]), data["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(files, dict):
        files = [file for file, seen in files.items() if seen == outcome]
    if not isinstance(files, list) or not all(isinstance(file, str) for file in files):
        return None
    prefix = Path(os.path.relpath(os.path.realpath(rootdir), os.path.realpath(top))).as_posix()
    if prefix.startswith(".."):
        return None
    return [file if prefix == "." else f"{prefix}/{file}" for file in files]


def classify(argv: Sequence[str], cwd: Path, env: Mapping[str, str], commit: str
             ) -> tuple[list[str], list[str]] | None:
    """The paths of ``commit`` the project's verifier ``argv`` classifies as no check's input and the project's
    dependency files, from the repository's top ``cwd`` with ``ATC_INPUTS_OF``; ``None`` when it fails or prints no
    listing."""
    try:
        done = subprocess.run(list(argv), cwd=cwd, env={**env, CLASSIFY: commit}, capture_output=True, text=True,
                              encoding="utf-8", timeout=600, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = done.stdout.strip().splitlines()
    if done.returncode or not lines:
        return None
    try:
        listing = json.loads(lines[-1])
    except ValueError:
        return None
    if not isinstance(listing, dict) or listing.get("commit") != commit:
        return None
    excluded, dependencies = listing.get("excluded"), listing.get("dependency_files")
    if not all(isinstance(value, list) and all(isinstance(item, str) for item in value)
               for value in (excluded, dependencies)):
        return None
    return excluded, dependencies


def summary(tree: Sequence[Entry], excluded: Sequence[str], ran_on: str) -> dict:
    """A report's ``inputs``: the commit the check ran on, its input digest and the paths outside its input set."""
    return {"ran_on": ran_on, "digest": digest(tree, excluded), "excluded": sorted(excluded)}

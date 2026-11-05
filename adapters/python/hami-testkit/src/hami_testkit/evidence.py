"""Verification evidence: the verifier's report and the gate's reuse decision.

Formats and the reuse table: shared/conformance/verification-evidence/README.md
at the workspace governance revision.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import __version__

REPORT_SCHEMA = "atc-verification-report/1"
RECORD_SCHEMA = "atc-verification-evidence/1"
REPORT_ENV = "ATC_VERIFICATION_REPORT"
SELECTOR_NAME = "hami-testkit"
DEPENDENCY_FILES = ("uv.lock", ".python-version")
ABSENT = "absent"
EVIDENCE_DIR = ("atc", "verification-evidence")


@dataclass(frozen=True)
class Stage:
    """What decides which tests one stage runs: its marker expression and ignored paths."""
    name: str
    marker: str
    ignore: tuple[str, ...] = ()

    def record(self) -> dict:
        return {"name": self.name, "marker": self.marker, "ignore": list(self.ignore)}


@dataclass(frozen=True)
class Decision:
    reuse: bool
    reason: str | None = None
    record: Mapping | None = None
    path: Path | None = None


def _posix(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")


def selection_record(pytest_paths: Iterable[str] | None) -> dict:
    """``Selection.pytest_paths()`` as a record: ``[]`` is the full default suite, ``None`` no tests."""
    if pytest_paths is None:
        return {"full": False, "paths": []}
    paths = [_posix(path) for path in pytest_paths]
    return {"full": not paths, "paths": paths}


def dependency_digests(root: Path, names: Iterable[str] = DEPENDENCY_FILES) -> dict[str, str]:
    digests = {}
    for name in names:
        try:
            digests[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
        except FileNotFoundError:
            digests[name] = ABSENT
    return digests


def platform_record() -> dict[str, str]:
    return {"system": sys.platform, "machine": platform.machine()}


def selector_record() -> dict[str, str]:
    return {"name": SELECTOR_NAME, "version": __version__}


def build_report(root: Path, pytest_paths: Iterable[str] | None, stages: Iterable[Stage], *,
                 dependency_files: Iterable[str] = DEPENDENCY_FILES, **recorded: object) -> dict:
    """The verifier's report; ``recorded`` keys (classification, checks, durations) are never compared."""
    return {
        "schema": REPORT_SCHEMA,
        "selector": selector_record(),
        "selection": selection_record(pytest_paths),
        "stages": [stage.record() for stage in stages],
        "dependency_inputs": dependency_digests(root, dependency_files),
        "python": platform.python_version(),
        **recorded,
    }


def write_report(environ: Mapping[str, str], root: Path, pytest_paths: Iterable[str] | None,
                 stages: Iterable[Stage], **options: object) -> Path | None:
    """Write the report where ``atc integrate`` asked for it; ``None`` when it asked for none."""
    target = environ.get(REPORT_ENV)
    if not target:
        return None
    path = Path(target)
    path.write_text(json.dumps(build_report(root, pytest_paths, stages, **options), indent=2) + "\n",
                    encoding="utf-8")
    return path


def covers(recorded: Mapping, own: Mapping) -> bool:
    """Whether the executed selection ``recorded`` includes every test of the gate's ``own`` selection."""
    if recorded["full"]:
        return True
    if own["full"]:
        return False
    executed = [_posix(path) for path in recorded["paths"]]
    return all(any(path == done or path.startswith(done + "/") for done in executed)
               for path in map(_posix, own["paths"]))


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _well_formed(record: Mapping) -> bool:
    report = record.get("report")
    if record.get("schema") != RECORD_SCHEMA or not isinstance(report, dict):
        return False
    selection, selector = report.get("selection"), report.get("selector")
    return (
        isinstance(record.get("candidate_sha"), str) and isinstance(record.get("tree"), str)
        and isinstance(record.get("clean"), bool) and _strings(record.get("verify_argv"))
        and isinstance(record.get("platform"), dict)
        and isinstance(selector, dict)
        and isinstance(selection, dict) and isinstance(selection.get("full"), bool)
        and _strings(selection.get("paths"))
        and isinstance(report.get("stages"), list) and isinstance(report.get("dependency_inputs"), dict)
    )


def _argv_file(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _decide(git: Callable[..., str], root: Path, pushed: str, pytest_paths: Iterable[str] | None,
            stages: Iterable[Stage], argv_file: Path, dependency_files: Iterable[str]) -> Decision:
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    path = common.joinpath(*EVIDENCE_DIR) / f"{pushed}.json"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Decision(False, "NO_RECORD")
    except OSError:
        return Decision(False, "UNREADABLE", path=path)
    try:
        record = json.loads(raw.decode("utf-8"))
    except ValueError:
        return Decision(False, "UNREADABLE", path=path)
    if not isinstance(record, dict):
        return Decision(False, "UNREADABLE", path=path)
    if not _well_formed(record):
        return Decision(False, "SCHEMA_MISMATCH", path=path)
    report = record["report"]
    checks = (
        ("COMMIT_MISMATCH", lambda: record["candidate_sha"] == pushed),
        ("TREE_MISMATCH", lambda: record["tree"] == git("rev-parse", f"{pushed}^{{tree}}").strip()),
        ("NOT_CLEAN", lambda: record["clean"] is True),
        ("PLATFORM_MISMATCH", lambda: record["platform"] == platform_record()),
        ("DEPENDENCY_MISMATCH",
         lambda: report["dependency_inputs"] == dependency_digests(root, dependency_files)),
        ("ARGV_MISMATCH", lambda: record["verify_argv"] == _argv_file(argv_file)),
        ("SELECTOR_MISMATCH", lambda: report["selector"] == selector_record()),
        ("STAGES_MISMATCH", lambda: report["stages"] == [stage.record() for stage in stages]),
        ("SELECTION_NOT_COVERED", lambda: covers(report["selection"], selection_record(pytest_paths))),
    )
    for reason, holds in checks:
        if not holds():
            return Decision(False, reason, record, path)
    return Decision(True, None, record, path)


def check(git: Callable[..., str], root: Path, pushed: str, pytest_paths: Iterable[str] | None,
          stages: Iterable[Stage], argv_file: Path, *,
          dependency_files: Iterable[str] = DEPENDENCY_FILES) -> Decision:
    """Whether the gate may reuse the ``atc integrate`` record of commit ``pushed`` instead of testing.

    ``git(*args)`` returns stdout for the gate's checkout at ``root``; ``pytest_paths`` and ``stages``
    are the gate's own selection and stages; ``argv_file`` is the project's verifier argv file. Any
    failure while checking refuses reuse.
    """
    try:
        return _decide(git, root, pushed, pytest_paths, tuple(stages), argv_file, tuple(dependency_files))
    except Exception:  # noqa: BLE001 - an evidence fault must run the tests
        return Decision(False, "CHECK_FAILED")


def describe(decision: Decision) -> str:
    if not decision.reuse:
        return f"pre-push: tests RUN (evidence: {decision.reason})"
    record = decision.record or {}
    return (f"pre-push: tests REUSED (atc integrate evidence, candidate {record.get('candidate_sha')}, "
            f"verified {record.get('verified_at')})")

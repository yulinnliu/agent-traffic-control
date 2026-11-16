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

from . import __version__, inputs, timing

# /3 adds the inputs the verifier ran on, which every reuse compares.
REPORT_SCHEMA = "atc-verification-report/3"
RECORD_SCHEMA = "atc-verification-evidence/3"
REPORT_ENV = "ATC_VERIFICATION_REPORT"
SELECTOR_NAME = "atc-testkit"
ABSENT = "absent"
EVIDENCE_DIR = inputs.EVIDENCE_DIR


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


def default_dependency_files() -> tuple[str, ...]:
    """The Python toolchain's dependency files, for a caller that names none."""
    from . import toolchain
    return tuple(toolchain.resolve().dependency_files)


def dependency_digests(root: Path, names: Iterable[str] | None = None) -> dict[str, str]:
    """The digest of each dependency file under ``root``, ``absent`` for a missing one."""
    digests = {}
    for name in default_dependency_files() if names is None else names:
        try:
            digests[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
        except FileNotFoundError:
            digests[name] = ABSENT
    return digests


def platform_record() -> dict[str, str]:
    return {"system": sys.platform, "machine": platform.machine()}


def selector_record() -> dict[str, str]:
    return {"name": SELECTOR_NAME, "version": __version__}


def test_file_identity(file: str, marker: str, dependency_inputs: Mapping[str, str], time_slack: float) -> dict:
    """The identity of one test file's run: the file by its path from the repository's top, the category
    expression it ran under, the selector, the platform, the time slack and the dependency files."""
    return {"kind": inputs.KIND, "file": file, "marker": marker, "selector": selector_record(),
            "platform": platform_record(), "time_slack": time_slack,
            "dependency_inputs": dict(dependency_inputs)}


def build_report(root: Path, pytest_paths: Iterable[str] | None, stages: Iterable[Stage], *,
                 dependency_files: Iterable[str] | None = None, time_slack: float | None = None,
                 inputs: Mapping | None = None, **recorded: object) -> dict:
    """The verifier's report; ``recorded`` keys (classification, checks, durations) are never compared.

    ``time_slack`` is the slack the stages ran with, the process environment's by default. ``inputs`` names the
    commit the verifier ran on, its input digest and the paths outside its input set; ``None`` when they could not
    be read, which no reuse accepts.
    """
    return {
        "schema": REPORT_SCHEMA,
        "selector": selector_record(),
        "selection": selection_record(pytest_paths),
        "stages": [stage.record() for stage in stages],
        "dependency_inputs": dependency_digests(root, dependency_files),
        "time_slack": timing.slack() if time_slack is None else time_slack,
        "inputs": None if inputs is None else dict(inputs),
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
    options.setdefault("time_slack", timing.slack(environ))
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


def _seconds(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _recorded_inputs(record: Mapping) -> Mapping | None:
    report = record.get("report")
    found = report.get("inputs") if isinstance(report, dict) else None
    if (isinstance(found, dict) and isinstance(found.get("digest"), str) and isinstance(found.get("ran_on"), str)
            and _strings(found.get("excluded"))):
        return found
    return None


def _well_formed(record: Mapping) -> bool:
    report = record.get("report")
    if record.get("schema") != RECORD_SCHEMA or not isinstance(report, dict):
        return False
    if report.get("schema") != REPORT_SCHEMA or not _seconds(report.get("time_slack")):
        return False
    selection, selector = report.get("selection"), report.get("selector")
    return (
        _recorded_inputs(record) is not None
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


def _inputs_match(record: Mapping, tree: list[inputs.Entry]) -> bool:
    recorded = _recorded_inputs(record)
    return recorded is not None and inputs.digest(tree, recorded["excluded"]) == recorded["digest"]


def _read_record(path: Path) -> object:
    return json.loads(path.read_bytes().decode("utf-8"))


def _matching_record(directory: Path, tree: list[inputs.Entry]) -> Path | None:
    """The newest record whose run's inputs equal the pushed commit's."""
    found = []
    for path in directory.glob("*.json"):
        try:
            record = _read_record(path)
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and _well_formed(record) and _inputs_match(record, tree):
            found.append((str(record.get("verified_at", "")), path))
    return max(found)[1] if found else None


def _decide(git: Callable[..., str], root: Path, pushed: str, pytest_paths: Iterable[str] | None,
            stages: Iterable[Stage], argv_file: Path, dependency_files: Iterable[str],
            time_slack: float) -> Decision:
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    directory = common.joinpath(*EVIDENCE_DIR)
    tree = inputs.entries(git, pushed)
    path = directory / f"{pushed}.json"
    if not path.is_file():
        path = _matching_record(directory, tree) or path
    try:
        record = _read_record(path)
    except FileNotFoundError:
        return Decision(False, "NO_RECORD")
    except (OSError, ValueError):
        return Decision(False, "UNREADABLE", path=path)
    if not isinstance(record, dict):
        return Decision(False, "UNREADABLE", path=path)
    if not _well_formed(record):
        return Decision(False, "SCHEMA_MISMATCH", path=path)
    report = record["report"]
    checks = (
        ("INPUTS_MISMATCH", lambda: _inputs_match(record, tree)),
        ("NOT_CLEAN", lambda: record["clean"] is True),
        ("PLATFORM_MISMATCH", lambda: record["platform"] == platform_record()),
        ("DEPENDENCY_MISMATCH",
         lambda: report["dependency_inputs"] == dependency_digests(root, dependency_files)),
        ("ARGV_MISMATCH", lambda: record["verify_argv"] == _argv_file(argv_file)),
        ("SELECTOR_MISMATCH", lambda: report["selector"] == selector_record()),
        ("STAGES_MISMATCH", lambda: report["stages"] == [stage.record() for stage in stages]),
        ("TIME_SLACK_MISMATCH", lambda: report["time_slack"] == time_slack),
        ("SELECTION_NOT_COVERED", lambda: covers(report["selection"], selection_record(pytest_paths))),
    )
    for reason, holds in checks:
        if not holds():
            return Decision(False, reason, record, path)
    return Decision(True, None, record, path)


def check(git: Callable[..., str], root: Path, pushed: str, pytest_paths: Iterable[str] | None,
          stages: Iterable[Stage], argv_file: Path, *,
          dependency_files: Iterable[str] | None = None, time_slack: float | None = None) -> Decision:
    """Whether the gate may reuse the ``atc integrate`` record of commit ``pushed`` instead of testing.

    ``git(*args)`` returns stdout for the gate's checkout at ``root``; ``pytest_paths`` and ``stages``
    are the gate's own selection and stages; ``argv_file`` is the project's verifier argv file;
    ``time_slack`` is the gate's, the process environment's by default. Any failure while checking
    refuses reuse.
    """
    try:
        files = default_dependency_files() if dependency_files is None else tuple(dependency_files)
        slack = timing.slack() if time_slack is None else time_slack
        return _decide(git, root, pushed, pytest_paths, tuple(stages), argv_file, files, slack)
    except Exception:  # noqa: BLE001 - an evidence fault must run the tests
        return Decision(False, "CHECK_FAILED")


def describe(decision: Decision) -> str:
    if not decision.reuse:
        return f"pre-push: tests RUN (evidence: {decision.reason})"
    record = decision.record or {}
    ran_on = (_recorded_inputs(record) or {}).get("ran_on")
    return (f"pre-push: tests REUSED (atc integrate evidence, candidate {record.get('candidate_sha')}, "
            f"verified {record.get('verified_at')}, reused_from ran_on {ran_on})")

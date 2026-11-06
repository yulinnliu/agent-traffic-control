"""Verify the candidate checkout ``atc integrate`` prepared for this repository (AGENTS.md "Integration verification").

Runs with the candidate as its working directory, in the environment ``scripts/integration-verify.json`` builds
from ``uv.lock`` under the candidate's ignored ``adapters/python/.venv/``. It runs the checks of the
``python-adapter`` CI job plus ``git diff --check`` over the ``ATC_EXPECTED_MAIN_SHA..ATC_INTEGRATION_SHA`` range,
and skips pytest for a documentation-only range. Pytest's base directory and the log of a failing check lie in
a working directory under the system temporary directory, which is short enough for the repositories the tests
build and is removed after a pass. On any failure the exit code is nonzero and the first stderr line is the
reason token. Tokens follow ``docs/TESTKIT.md`` § Integration verifier; this script moves to the
shared verifier when ``atc-testkit`` ships it.

    uv run --quiet --locked --project adapters/python python adapters/python/scripts/integration_verify.py
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PurePosixPath

ADAPTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER / "hami-testkit/src"))

from hami_testkit.git import git_env  # noqa: E402

ROOT = ADAPTER.parents[1]
ADAPTER_DIR = "adapters/python"
REQUIRED_ENV = ("ATC_EXPECTED_MAIN_SHA", "ATC_INTEGRATION_SHA", "ATC_CANDIDATE_DIR", "ATC_REVIEWED_SOURCE_SHA")
FULL_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
PRODUCT_MODULES = ("atc_cli",)
AUTOMATION_MARKDOWN_NAMES = frozenset({"AGENTS.md", "CLAUDE.md"})
AUTOMATION_MARKDOWN_PREFIXES = ("shared/skills/", "shared/governance/", "shared/conformance/", "adapters/")
PYTEST_NO_TESTS_COLLECTED = 5
FAILURE_LINE = re.compile(r"^(?:FAILED|ERROR) ")
REPORTED_LINES = 20
WORK_PREFIX = "atc-root-verify-"

WORKSPACE_CHECK = ("WORKSPACE_VALIDATION_FAILED", ["uv", "run", "--locked", "python", "scripts/validate_workspace.py"])
POLICY_CHECK = ("POLICY_CHECK_FAILED",
                ["uv", "run", "--locked", "python", "scripts/policies.py", "check", "../../shared/policies/examples"])
PYTEST = ["uv", "run", "--locked", "pytest"]

Run = Callable[[list[str], Path], tuple[int, str]]


class VerifyError(Exception):
    """A refusal: ``token`` is the first stderr line, ``lines`` the summary after it."""

    def __init__(self, token: str, *lines: str):
        super().__init__(token)
        self.token, self.lines = token, lines


def require_env(environ: Mapping[str, str]) -> dict[str, str]:
    missing = [name for name in REQUIRED_ENV if not environ.get(name)]
    if missing:
        raise VerifyError("VERIFY_ENV_MISSING", "missing: " + ", ".join(missing))
    values = {name: environ[name] for name in REQUIRED_ENV}
    invalid = [name for name in REQUIRED_ENV if name.endswith("_SHA") and not FULL_OBJECT_ID.fullmatch(values[name])]
    if invalid:
        raise VerifyError("VERIFY_ENV_INVALID", *(f"{name} {values[name]} is not a full object ID" for name in invalid))
    return values


def inside(path: str | Path, root: str | Path) -> bool:
    """Whether ``path`` is ``root`` or below it, compared by long resolved names."""
    path, root = (Path(os.path.normcase(Path(value).resolve())) for value in (path, root))
    return path.is_relative_to(root)


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, env=git_env())


def check_candidate(root: Path, values: Mapping[str, str], cwd: Path) -> None:
    """The verifier, its working directory and ``HEAD`` are the candidate the adapter named."""
    candidate = values["ATC_CANDIDATE_DIR"]
    if not (inside(root, candidate) and inside(candidate, root) and inside(cwd, root) and inside(root, cwd)):
        raise VerifyError("CANDIDATE_DIR_MISMATCH", f"candidate: {candidate}", f"verifier tree: {root}", f"cwd: {cwd}")
    sha = values["ATC_INTEGRATION_SHA"]
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    if head != sha:
        raise VerifyError("CANDIDATE_HEAD_MISMATCH", f"HEAD {head or '<none>'}, ATC_INTEGRATION_SHA {sha}")
    for name, token in (("ATC_EXPECTED_MAIN_SHA", "EXPECTED_MAIN_NOT_IN_CANDIDATE"),
                        ("ATC_REVIEWED_SOURCE_SHA", "REVIEWED_SOURCE_NOT_IN_CANDIDATE")):
        if git(root, "merge-base", "--is-ancestor", values[name], sha).returncode != 0:
            raise VerifyError(token, f"{name} {values[name]} is not an ancestor of {sha}")


def import_violations(names: Iterable[str], candidate: str | Path) -> list[str]:
    """``name: location`` for each module whose file or package directory lies outside ``candidate``."""
    importlib.invalidate_caches()
    violations = []
    for name in names:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError) as exc:
            violations.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        locations = [] if spec is None else [*([spec.origin] if spec.has_location else []),
                                             *(spec.submodule_search_locations or ())]
        if not locations:
            violations.append(f"{name}: not importable")
        violations.extend(f"{name}: {location}" for location in locations if not inside(location, candidate))
    return violations


def check_imports(candidate: str | Path) -> None:
    """The environment the checks run in installs the candidate's own ``atc_cli``."""
    violations = import_violations(PRODUCT_MODULES, candidate)
    if violations:
        raise VerifyError("CANDIDATE_IMPORT_MISMATCH", *violations)


def changed_paths(root: Path, base: str, sha: str) -> list[str]:
    result = git(root, "diff", "--name-only", "--no-renames", f"{base}..{sha}")
    if result.returncode != 0:
        raise VerifyError("SELECTION_FAILED", f"range {base}..{sha} is not readable in the candidate",
                          result.stderr.strip())
    paths = sorted(line for line in result.stdout.splitlines() if line.strip())
    if not paths:
        raise VerifyError("EMPTY_INTEGRATION_DIFF", f"range {base}..{sha} changes no path")
    return paths


def automation_markdown(path: str) -> bool:
    """Markdown that is rendered into managed copies or read by the tests."""
    return PurePosixPath(path).name in AUTOMATION_MARKDOWN_NAMES or path.startswith(AUTOMATION_MARKDOWN_PREFIXES)


def documentation_only(paths: Iterable[str]) -> bool:
    """Every changed path is Markdown and none is automation Markdown."""
    paths = list(paths)
    return bool(paths) and all(path.endswith(".md") and not automation_markdown(path) for path in paths)


def run_command(argv: list[str], cwd: Path) -> tuple[int, str]:
    """Exit code and merged output of ``argv``, started without Git hook variables."""
    result = subprocess.run(argv, cwd=cwd, env=git_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    return result.returncode, result.stdout


def tail(output: str, pattern: re.Pattern | None = None) -> list[str]:
    lines = output.splitlines()
    chosen = [line for line in lines if pattern.match(line)] if pattern else []
    return (chosen or lines)[-REPORTED_LINES:]


def execute(root: Path, base: str, sha: str, doc_only: bool, work: Path, run: Run) -> None:
    """``git diff --check``, the two contract checks, then the suite unless the range is documentation-only."""
    adapter = root / ADAPTER_DIR
    checks = [("DIFF_CHECK_FAILED", ["git", "diff", "--check", f"{base}..{sha}"], root),
              (*WORKSPACE_CHECK, adapter), (*POLICY_CHECK, adapter)]
    if not doc_only:
        checks.append(("TESTS_FAILED", [*PYTEST, "--basetemp", str(work / "bt")], adapter))
    for token, argv, cwd in checks:
        started = time.monotonic()
        code, output = run(argv, cwd)
        command = " ".join(argv)
        if token == "TESTS_FAILED" and code == PYTEST_NO_TESTS_COLLECTED:
            raise VerifyError("NO_TESTS_COLLECTED", f"{command} collected no test")
        if code:
            log = work / (token.lower() + ".log")
            log.write_text(output, encoding="utf-8")
            raise VerifyError(token, f"{command} exited {code}; log {log}",
                              *tail(output, FAILURE_LINE if token == "TESTS_FAILED" else None))
        print(f"ok {command} ({time.monotonic() - started:.1f}s)", flush=True)


def check_final_state(root: Path, sha: str) -> None:
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    if head != sha:
        raise VerifyError("CANDIDATE_HEAD_MOVED", f"HEAD {head or '<none>'}, ATC_INTEGRATION_SHA {sha}")
    dirty = git(root, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
    if dirty:
        raise VerifyError("CANDIDATE_DIRTY", *dirty[:REPORTED_LINES])


def remove_tree(path: Path) -> None:
    """Delete ``path``, read-only files included (Git writes its objects read-only); a leftover keeps the verdict."""
    def writable(function: Callable[[str], object], target: str, _exc: BaseException) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    try:
        shutil.rmtree(path, onexc=writable)
    except OSError as exc:
        print(f"working directory left: {path} ({type(exc).__name__}: {exc})", flush=True)


def main(environ: Mapping[str, str] | None = None, root: Path = ROOT, run: Run = run_command) -> int:
    environ = os.environ if environ is None else environ
    work = None
    try:
        values = require_env(environ)
        check_candidate(root, values, Path.cwd())
        check_imports(values["ATC_CANDIDATE_DIR"])
        base, sha = values["ATC_EXPECTED_MAIN_SHA"], values["ATC_INTEGRATION_SHA"]
        doc_only = documentation_only(changed_paths(root, base, sha))
        print(f"classification: {'documentation-only' if doc_only else 'full'}", flush=True)
        work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX))
        execute(root, base, sha, doc_only, work, run)
        check_final_state(root, sha)
    except VerifyError as exc:
        print(exc.token, file=sys.stderr)
        for line in exc.lines:
            print(line, file=sys.stderr)
        if work is not None:
            print(f"working directory kept: {work}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - any verifier fault refuses the candidate
        print("VERIFIER_ERROR", file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    remove_tree(work)
    print(f"integration verification: PASS {sha}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

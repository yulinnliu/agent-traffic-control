"""Verify the candidate checkout ``atc integrate`` prepared for this repository (AGENTS.md "Integration verification").

The root's data for the verifier skeleton (docs/TESTKIT.md § Integration verifier, the workspace-root paragraph),
imported from the candidate's own tree: the environment ``scripts/integration-verify.json`` builds from ``uv.lock``
under the candidate's ignored ``adapters/python/.venv/`` installs ``atc_testkit`` from there. Its test command is
``select_tests.py`` over the integration range; the full suite runs in a dispatched CI run.

    uv run --quiet --locked --project adapters/python python adapters/python/scripts/integration_verify.py
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
import os
import sys

from atc_testkit import stages, verifier
from atc_testkit.project import Project, StaticCheck, TestCommand

import select_tests

ROOT = Path(__file__).resolve().parents[3]
ADAPTER_DIR = "adapters/python"
PRODUCT_PREFIXES = ("atc_cli", "atc_testkit")
SOURCE_ROOTS = (f"{ADAPTER_DIR}/src", f"{ADAPTER_DIR}/atc-testkit/src")
DOCUMENTATION_PREFIX = "docs/"
TASK_RECORDS = ".atc/tasks/"
BASE_VARIABLE = "ATC_EXPECTED_MAIN_SHA"

WORKSPACE_CHECK = StaticCheck("workspace", "WORKSPACE_VALIDATION_FAILED",
                              ("uv", "run", "--locked", "python", "scripts/validate_workspace.py"), cwd=ADAPTER_DIR)
POLICY_CHECK = StaticCheck("policies", "POLICY_CHECK_FAILED",
                           ("uv", "run", "--locked", "python", "scripts/policies.py", "check",
                            "../../shared/policies/examples"), cwd=ADAPTER_DIR)
SELECTION = ("uv", "run", "--locked", "python", "scripts/select_tests.py")


def selection_command(base: str | None) -> TestCommand:
    """The selection over ``base``..HEAD, run; the skeleton's ``--basetemp`` reaches pytest after ``--``."""
    return TestCommand((*SELECTION, "--base", base or "main", "--run", "--"), cwd=ADAPTER_DIR)


def graph(root: Path) -> select_tests.Graph:
    return select_tests.Graph(root)


def documentation_only(paths: Sequence[str], root: Path = ROOT) -> bool:
    """Every path is Markdown under ``docs/``, a task record, or Markdown the selection maps to no test file."""
    if not paths:
        return False
    rest = [path for path in paths if not (
        (path.startswith(DOCUMENTATION_PREFIX) and path.endswith(".md")) or path.startswith(TASK_RECORDS))]
    if not all(PurePosixPath(path).suffix.lower() == ".md" for path in rest):
        return False
    return not rest or select_tests.select(rest, graph(root)).pytest_paths() is None


def documentation_predicate(root: Path) -> Callable[[Sequence[str]], bool]:
    return lambda paths: documentation_only(paths, root)


def project(root: Path = ROOT, environ: Mapping[str, str] | None = None) -> Project:
    return Project(root=root, argv_file=root / ADAPTER_DIR / "scripts/integration-verify.json",
                   checks=(WORKSPACE_CHECK, POLICY_CHECK), source_roots=SOURCE_ROOTS,
                   product_prefixes=PRODUCT_PREFIXES, credentials=(), data_roots=(),
                   test_command=selection_command((environ or {}).get(BASE_VARIABLE)),
                   documentation_only=documentation_predicate(root))


def main(environ: Mapping[str, str] | None = None, root: Path = ROOT, run: stages.Run = stages.run_logged) -> int:
    environ = os.environ if environ is None else environ
    return verifier.main(project(root, environ), environ, run)


if __name__ == "__main__":
    sys.exit(main())

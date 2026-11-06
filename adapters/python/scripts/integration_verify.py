"""Verify the candidate checkout ``atc integrate`` prepared for this repository (AGENTS.md "Integration verification").

The root's data for the verifier skeleton (docs/TESTKIT.md § Integration verifier, the workspace-root paragraph),
imported from the candidate's own tree: the environment ``scripts/integration-verify.json`` builds from ``uv.lock``
under the candidate's ignored ``adapters/python/.venv/`` installs ``atc_testkit`` from there.

    uv run --quiet --locked --project adapters/python python adapters/python/scripts/integration_verify.py
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import sys

from atc_testkit import stages, verifier
from atc_testkit.project import Project, StaticCheck, TestCommand

ROOT = Path(__file__).resolve().parents[3]
ADAPTER_DIR = "adapters/python"
PRODUCT_PREFIXES = ("atc_cli", "atc_testkit")
SOURCE_ROOTS = (f"{ADAPTER_DIR}/src", f"{ADAPTER_DIR}/atc-testkit/src")
DOCUMENTATION_PREFIX = "docs/"

WORKSPACE_CHECK = StaticCheck("workspace", "WORKSPACE_VALIDATION_FAILED",
                              ("uv", "run", "--locked", "python", "scripts/validate_workspace.py"), cwd=ADAPTER_DIR)
POLICY_CHECK = StaticCheck("policies", "POLICY_CHECK_FAILED",
                           ("uv", "run", "--locked", "python", "scripts/policies.py", "check",
                            "../../shared/policies/examples"), cwd=ADAPTER_DIR)
TESTS = TestCommand(("uv", "run", "--locked", "pytest"), cwd=ADAPTER_DIR)


def documentation_only(paths: Sequence[str]) -> bool:
    """Every changed path is Markdown under ``docs/``."""
    return bool(paths) and all(path.startswith(DOCUMENTATION_PREFIX) and path.endswith(".md") for path in paths)


def project(root: Path = ROOT) -> Project:
    return Project(root=root, argv_file=root / ADAPTER_DIR / "scripts/integration-verify.json",
                   checks=(WORKSPACE_CHECK, POLICY_CHECK), source_roots=SOURCE_ROOTS,
                   product_prefixes=PRODUCT_PREFIXES, credentials=(), data_roots=(),
                   test_command=TESTS, documentation_only=documentation_only)


def main(environ: Mapping[str, str] | None = None, root: Path = ROOT, run: stages.Run = stages.run_logged) -> int:
    return verifier.main(project(root), environ, run)


if __name__ == "__main__":
    sys.exit(main())

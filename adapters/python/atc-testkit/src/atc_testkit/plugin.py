"""The pytest plugin a project's root ``conftest.py`` registers (docs/TESTKIT.md § Adoption, § Markers).

It registers the markers, enforces the marker contract, moves the temporary directory
(docs/ATC_HOME.md § Resolving the locations) and ends child processes (docs/TESTKIT.md § Child processes).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from . import children, config as project_config, locations
from .markers import MARKER_DESCRIPTIONS, load_unmarked_baseline, marker_contract_violations

TEMP_ROOT_VARIABLE = "PYTEST_DEBUG_TEMPROOT"


def pytest_configure(config):
    for name, description in MARKER_DESCRIPTIONS.items():
        config.addinivalue_line("markers", f"{name}: {description}")
    if config.option.basetemp or os.environ.get(TEMP_ROOT_VARIABLE):
        return
    try:
        if not locations.temporary_directory_below_tmp():
            locations.enter()
    except locations.Refusal as refusal:
        pytest.exit(f"{refusal.token} {refusal.detail}", returncode=refusal.code)


def _unmarked_baseline(config) -> frozenset[str]:
    name = project_config.tool_table(config.rootpath).get("unmarked_baseline")
    return load_unmarked_baseline(Path(config.rootpath) / name) if name else frozenset()


def pytest_collection_modifyitems(config, items):
    violations = marker_contract_violations(items, allowed_unmarked=_unmarked_baseline(config))
    if violations:
        detail = "\n".join(f"- {violation}" for violation in violations)
        raise pytest.UsageError(f"pytest marker contract violations:\n{detail}")


@pytest.fixture(autouse=True)
def _atc_reap_children():
    yield
    children.reap()


def _base_directory(config) -> Path | None:
    factory = getattr(config, "_tmp_path_factory", None)
    base = getattr(factory, "_basetemp", None) or getattr(factory, "_given_basetemp", None)
    return Path(base) if base else None


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    base = _base_directory(config)
    if hasattr(config, "workerinput") or base is None:
        return
    try:
        alive = [f"{process.pid} {process.command}" for process in children.survivors(base)]
    except Exception as error:  # noqa: BLE001 - an unknown process list fails the run
        alive = [f"process inventory failed: {error!r}"]
    if alive:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        lines = [f"{children.ALIVE_TOKEN} under {base}", *alive]
        reporter = config.pluginmanager.get_plugin("terminalreporter")
        if reporter is None:
            print("\n".join(lines))
        else:
            reporter.write_sep("=", "child processes still running", red=True)
            for line in lines:
                reporter.write_line(line)

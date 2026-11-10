"""The pytest plugin a project's root ``conftest.py`` registers (docs/TESTKIT.md § Adoption, § Markers).

It registers the markers, enforces the marker contract, groups tests that share a wider-scoped fixture for
``--dist loadgroup`` (docs/TESTKIT.md § Stages), moves the temporary directory (docs/ATC_HOME.md § Resolving the
locations) and ends child processes (docs/TESTKIT.md § Child processes).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from . import children, config as project_config, locations
from .markers import MARKER_DESCRIPTIONS, load_unmarked_baseline, marker_contract_violations

TEMP_ROOT_VARIABLE = "PYTEST_DEBUG_TEMPROOT"
REAP_OPTION = "atc_testkit_reap_seconds"
REAP_INVALID = "REAP_SECONDS_INVALID"
GROUP_MARK = "xdist_group"
FIXTURE_INFO_MISSING = "FIXTURE_INFO_MISSING"


def pytest_addoption(parser):
    parser.addini(REAP_OPTION, f"seconds a child may outlive the run, above 0 and up to {children.REAP_SECONDS:g}",
                  default=None)


def reap_seconds(config) -> float:
    """The survivor window: the ini option, or ``children.REAP_SECONDS``; any other value is a usage error."""
    value = config.getini(REAP_OPTION)
    if value in (None, ""):
        return children.REAP_SECONDS
    try:
        seconds = float(value)
    except ValueError:
        seconds = -1.0
    if not 0 < seconds <= children.REAP_SECONDS:
        raise pytest.UsageError(f"{REAP_INVALID} {REAP_OPTION} = {value!r} is not a number above 0 and up to "
                                f"{children.REAP_SECONDS:g}")
    return seconds


def pytest_configure(config):
    for name, description in MARKER_DESCRIPTIONS.items():
        config.addinivalue_line("markers", f"{name}: {description}")
    reap_seconds(config)
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


def shared_group(item) -> str | None:
    """The ``xdist_group`` of a test that uses a package-, module- or class-scoped fixture; ``None`` otherwise."""
    if not isinstance(item, pytest.Function):
        return None
    info = getattr(item, "_fixtureinfo", None)
    if info is None or not hasattr(info, "name2fixturedefs"):
        raise pytest.UsageError(f"{FIXTURE_INFO_MISSING} {item.nodeid}: pytest's fixture information is not where "
                                "the testkit reads it; the tests cannot be grouped")
    scopes = {definitions[-1].scope: definitions[-1] for definitions in info.name2fixturedefs.values() if definitions}
    module = item.nodeid.split("::", 1)[0]
    if "package" in scopes:
        return f"package:{scopes['package'].baseid or '.'}"
    if "module" in scopes or ("class" in scopes and item.cls is None):
        return module
    if "class" in scopes:
        return f"{module}::{item.cls.__name__}"
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Runs before xdist's worker hook, which reads ``xdist_group`` to name the scheduler's groups."""
    violations = marker_contract_violations(items, allowed_unmarked=_unmarked_baseline(config))
    if violations:
        detail = "\n".join(f"- {violation}" for violation in violations)
        raise pytest.UsageError(f"pytest marker contract violations:\n{detail}")
    for item in items:
        if item.get_closest_marker(GROUP_MARK) is None and (group := shared_group(item)) is not None:
            item.add_marker(pytest.mark.xdist_group(group))


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
        alive = [f"{process.pid} {process.command}" for process in children.lasting_survivors(base, reap_seconds(config))]
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

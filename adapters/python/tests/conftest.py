from __future__ import annotations

import os
import secrets
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import pytest
from atc_testkit import locations, stages, workers

pytest_plugins = ("atc_testkit.plugin",)

ROOT_NAME_LENGTH = 9
SHORTEST_ROOT_NAME = 3
ROOT_MARGIN = len("/new")
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import root_reads  # noqa: E402  (needs SCRIPTS on the path for select_tests)


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(root_reads.RootReads.for_checkout(), "root_reads")


@pytest.fixture(autouse=True)
def _no_host_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Code a test runs in process reads no ``~/.atc/config.toml`` of the host; a test that needs one supplies it."""
    monkeypatch.setattr(stages, "host_config", lambda: SCRIPTS / "no-host-config.toml")


@pytest.fixture(scope="session")
def _test_worker_pool(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("worker-pool")


@pytest.fixture(autouse=True)
def _no_host_worker_pool(monkeypatch: pytest.MonkeyPatch, _test_worker_pool: Path) -> None:
    """Code a test runs, in process or in a child, takes tokens from a pool of the session, never the host's, and
    sizes workers without the host's free memory; a test that needs either supplies it."""
    monkeypatch.setattr(workers, "available_memory", lambda: None)
    monkeypatch.setenv(workers.POOL_DIRECTORY, str(_test_worker_pool))
    named = stages.pool_directory
    monkeypatch.setattr(stages, "pool_directory",
                        lambda environ: named(environ) if environ.get(workers.POOL_DIRECTORY) else _test_worker_pool)
    for name in (workers.HELD, workers.WAIT_FILE, workers.MAX_WAIT):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _guard_checks_no_live_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """`atc_cli.main` guards a checkout holding no workspace.yaml unless a test names one, whatever branch runs the suite."""
    import atc_cli
    monkeypatch.setattr(atc_cli, "GUARDED_CHECKOUT", SCRIPTS)


def _writable_retry(function, target, _error) -> None:
    os.chmod(target, stat.S_IWRITE)
    function(target)


@pytest.fixture()
def restored_process():
    """The process's environment, temporary directory and working directory come back after the test."""
    environ, temporary, cwd = dict(os.environ), tempfile.tempdir, os.getcwd()
    yield
    os.chdir(cwd)
    os.environ.clear()
    os.environ.update(environ)
    tempfile.tempdir = temporary


def _new_root(reserve: int) -> Path:
    """A new directory inside the caller's root that leaves ``reserve`` characters within the bound; a skip when none fits."""
    caller = Path(os.path.realpath(os.environ.get("ATC_TMP") or Path.home() / ".atc" / "tmp"))
    length = min(ROOT_NAME_LENGTH, locations.TMP_NAME_BOUND - reserve - len(str(caller)) - 1)
    if length < SHORTEST_ROOT_NAME:
        pytest.skip(f"{caller} leaves no room for a directory within the bound")
    caller.mkdir(parents=True, exist_ok=True)
    while True:
        root = caller / secrets.token_hex(ROOT_NAME_LENGTH)[:length]
        try:
            root.mkdir()
        except FileExistsError:
            continue
        return root


@pytest.fixture()
def atc_tmp(restored_process, monkeypatch: pytest.MonkeyPatch):
    """A ``<tmp>`` of its own, outside ``AppData`` and every repository, set as ``ATC_TMP``; a test's root below it stays within the bound."""
    root = Path(os.path.realpath(_new_root(ROOT_MARGIN)))
    monkeypatch.setenv("ATC_TMP", str(root))
    monkeypatch.delenv("CI", raising=False)
    yield root
    shutil.rmtree(root, onexc=_writable_retry)


@pytest.fixture()
def fake_home():
    """A home directory whose default ``<tmp>``, ``.atc/tmp``, stays within the bound; a test requests it before ``atc_tmp``."""
    home = Path(os.path.realpath(_new_root(len(os.path.join(".atc", "tmp")) + 1)))
    yield home
    shutil.rmtree(home, onexc=_writable_retry)

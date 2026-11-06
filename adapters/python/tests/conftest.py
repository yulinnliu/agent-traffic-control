from __future__ import annotations

import os
import secrets
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def pytest_configure(config: pytest.Config) -> None:
    # A sandboxed %TEMP% can differ between calls; keep temporary repos under
    # the gitignored tests/.tmp-pytest/ instead.
    if config.option.basetemp is None:
        config.option.basetemp = Path(__file__).resolve().parent / ".tmp-pytest"


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


@pytest.fixture()
def atc_tmp(restored_process, monkeypatch: pytest.MonkeyPatch):
    """A ``<tmp>`` of its own, outside ``AppData`` and every repository, set as ``ATC_TMP``."""
    root = Path.home() / ".atc" / "tmp" / ("t" + secrets.token_hex(4))
    root.mkdir(parents=True)
    root = Path(os.path.realpath(root))
    monkeypatch.setenv("ATC_TMP", str(root))
    monkeypatch.delenv("CI", raising=False)
    yield root
    shutil.rmtree(root, onexc=_writable_retry)

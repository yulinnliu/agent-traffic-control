from __future__ import annotations

import os
import secrets
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import pytest
from atc_testkit import locations

pytest_plugins = ("atc_testkit.plugin",)

ROOT_NAME_LENGTH = 9
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


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


def _new_root() -> Path:
    """A new directory within the bound on ``<tmp>``: inside the caller's root, or beside it when the name does not fit."""
    caller = Path(os.path.realpath(os.environ.get("ATC_TMP") or Path.home() / ".atc" / "tmp"))
    parent = caller if len(str(caller)) + 1 + ROOT_NAME_LENGTH <= locations.TMP_NAME_BOUND else caller.parent
    length = min(ROOT_NAME_LENGTH, locations.TMP_NAME_BOUND - len(str(parent)) - 1)
    parent.mkdir(parents=True, exist_ok=True)
    while True:
        root = parent / secrets.token_hex(ROOT_NAME_LENGTH)[:length]
        try:
            root.mkdir()
        except FileExistsError:
            continue
        return root


@pytest.fixture()
def atc_tmp(restored_process, monkeypatch: pytest.MonkeyPatch):
    """A ``<tmp>`` of its own, outside ``AppData`` and every repository, set as ``ATC_TMP``."""
    root = Path(os.path.realpath(_new_root()))
    monkeypatch.setenv("ATC_TMP", str(root))
    monkeypatch.delenv("CI", raising=False)
    yield root
    shutil.rmtree(root, onexc=_writable_retry)

"""`atc doctor`'s `atc environment` line: the processes that keep `uv tool install` from replacing atc's environment
(docs/WORKFLOW.md § Installing a release; design docs/design/ATC-0064.md § 4)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from atc_testkit import children

import doctor


@pytest.mark.unit
def test_no_process_from_the_environment_is_ready(tmp_path: Path) -> None:
    listed = [children.HostProcess(10, 1, r"C:\other\python.exe -m atc_cli test ATC-0001"),
              children.HostProcess(11, 1, f'"{tmp_path / "environment-sibling" / "python.exe"}" x')]
    assert doctor.environment_checks(tmp_path / "environment", listed) == [
        doctor.Check("atc environment", "READY", f"no other process runs from {tmp_path / 'environment'}")]


@pytest.mark.unit
def test_doctor_and_its_ancestors_are_not_counted(tmp_path: Path) -> None:
    command = f'"{tmp_path / "environment" / "Scripts" / "python.exe"}" atc doctor'
    listed = [children.HostProcess(os.getpid(), 77, command), children.HostProcess(77, 0, command)]
    assert doctor.environment_checks(tmp_path / "environment", listed)[0].status == "READY"


@pytest.mark.unit
def test_a_process_from_the_environment_is_named_with_its_command(tmp_path: Path) -> None:
    prefix = tmp_path / "environment"
    command = f'"{prefix / "Scripts" / "python.exe"}" "C:/bin/atc.exe" test ATC-0001 ' + "x" * 200
    found = doctor.environment_checks(prefix, [children.HostProcess(4242, 1, command)])
    assert found[0] == doctor.Check("atc environment", "WARN", f"{doctor.ENV_IN_USE}: 1 processes run from "
                                                               f"{prefix}; uv tool install fails until they end")
    assert found[1] == doctor.Check("atc environment process", "WARN",
                                    f"pid 4242 {command[:doctor.COMMAND_SHOWN]}")


@pytest.mark.integration
def test_a_live_process_running_from_the_environment_is_found(tmp_path: Path) -> None:
    """The host's own process list: a child whose command line runs a file of the environment is in use.

    The child is the base interpreter, one process: a venv's launcher starts the interpreter as a second process
    naming the same file, which killing and waiting for the launcher does not wait for."""
    prefix = tmp_path / "environment"
    script = prefix / "Scripts" / "hold.py"
    script.parent.mkdir(parents=True)
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    process = children.start([sys._base_executable, str(script)], stdin=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while (found := doctor.environment_checks(prefix))[0].status == "READY" and time.monotonic() < deadline:
            time.sleep(0.1)
        assert found[0].detail.startswith(f"{doctor.ENV_IN_USE}: 1 processes")
        assert found[1].detail.startswith(f"pid {process.pid} ")
    finally:
        process.kill()
        process.wait()
    deadline = time.monotonic() + 10  # an ended process stays listed for the rest of its teardown
    while doctor.environment_checks(prefix)[0].status != "READY" and time.monotonic() < deadline:
        time.sleep(0.1)
    assert doctor.environment_checks(prefix)[0].status == "READY"


@pytest.mark.unit
def test_a_process_list_that_cannot_be_read_is_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args):
        raise OSError("denied")
    monkeypatch.setattr(children, "survivors", broken)
    found = doctor.environment_checks(tmp_path)
    assert found[0].status == "WARN" and "cannot be listed (denied)" in found[0].detail

"""docs/ATC_CLI_PLAN.md § 4 Command surface: help, missing dependencies and option names of every ``atc`` command."""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pytest

import atc_cli
from atc_testkit import check, gate

pytestmark = pytest.mark.integration

SUBCOMMANDS = [
    ("worktree",), ("worktree", "create"), ("worktree", "resume"), ("worktree", "audit"), ("worktree", "cleanup"),
    ("preflight",), ("preflight", "merge"), ("preflight", "cleanup"),
    ("integrate",), ("validate",), ("onboard",),
    ("bootstrap",), ("bootstrap", "init"), ("bootstrap", "add"), ("bootstrap", "sync"), ("bootstrap", "status"),
    ("doctor",), ("policies",), ("policies", "check"), ("enforce",), ("classify",),
    ("friction",), ("friction", "add"), ("friction", "list"), ("friction", "summary"), ("friction", "resolve"),
    ("test",), ("test", "run"), ("test", "mutants"),
    ("tmp",), ("tmp", "list"), ("tmp", "clean"), ("tmp", "prune"),
]


@pytest.fixture()
def parsers(monkeypatch: pytest.MonkeyPatch) -> list[argparse.ArgumentParser]:
    """Every argument parser built while the test runs."""
    built: list[argparse.ArgumentParser] = []
    original = argparse.ArgumentParser.__init__

    def record(self, *args, **kwargs):
        original(self, *args, **kwargs)
        built.append(self)
    monkeypatch.setattr(argparse.ArgumentParser, "__init__", record)
    return built


def ascii_stdout(monkeypatch: pytest.MonkeyPatch) -> io.BytesIO:
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="ascii", errors="strict"))
    return raw


def call(arguments: list[str]) -> int:
    try:
        return atc_cli.main(arguments)
    except SystemExit as stop:
        return stop.code or 0


@pytest.mark.parametrize("subcommand", SUBCOMMANDS, ids=" ".join)
def test_help_names_the_subcommand_exits_zero_and_escapes_non_ascii(
        subcommand: tuple[str, ...], monkeypatch: pytest.MonkeyPatch,
        parsers: list[argparse.ArgumentParser]) -> None:
    raw = ascii_stdout(monkeypatch)
    code = call([*subcommand, "--help"])
    sys.stdout.flush()
    text = raw.getvalue().decode("ascii")
    assert code == 0
    assert text.startswith(f"usage: atc {' '.join(subcommand)}")
    assert "python -m" not in text and ".py" not in text.splitlines()[0]
    assert all(not parser.allow_abbrev for parser in parsers)


def test_help_restores_the_program_name(monkeypatch: pytest.MonkeyPatch) -> None:
    ascii_stdout(monkeypatch)
    before = sys.argv[0]
    assert call(["classify", "--help"]) == 0
    assert sys.argv[0] == before


@pytest.mark.parametrize("main", [lambda: check.main(["--help"]), lambda: gate.main(None, ["--help"])],
                         ids=["check", "gate"])
def test_testkit_entry_points_take_no_abbreviation(main, parsers: list[argparse.ArgumentParser],
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        main()
    assert stop.value.code == 0 and parsers and all(not parser.allow_abbrev for parser in parsers)


def test_an_abbreviated_option_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    code = call(["worktree", "cleanup", "--owner", "claude", "--topic", "t", "--contract", "1",
                 "--squash-integrated", "--reviewe", "a" * 40])
    assert code == 2 and "unrecognized arguments: --reviewe" in capsys.readouterr().err


def test_a_missing_dependency_names_the_reinstall_command(monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setitem(atc_cli.COMMANDS, "classify", "atc_missing_dependency_probe")
    assert atc_cli.main(["classify"]) == 2
    err = capsys.readouterr().err
    expected = f'uv tool install -e "{Path(atc_cli.__file__).resolve().parents[2]}" --force'
    assert err.startswith("ERROR MISSING_DEPENDENCY atc classify needs the module 'atc_missing_dependency_probe'")
    assert expected in err and "Traceback" not in err


def test_without_a_named_root_outside_any_workspace_the_root_is_this_checkout(tmp_path: Path) -> None:
    root = atc_cli.resolve_root(None, {}, tmp_path)
    assert (root / "adapters" / "python" / "src" / "atc_cli").is_dir() and (root / "shared" / "governance").is_dir()

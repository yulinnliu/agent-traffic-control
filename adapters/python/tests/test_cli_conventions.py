"""shared/conformance/CLI.md: help form, metavars, the `--json` envelope, `--root`, refusals and their exit codes."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import atc_cli
import cli_conventions
import pytest
from test_atc_cli import NOT_CANONICAL, SUBCOMMANDS, ascii_stdout, call, parsers  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

METAVAR_WORDS = {"NAME", "ID", "SHA", "DIR", "FILE", "PATH", "TEXT", "URL", "OWNER", "REPO", "PROJECT", "COMMAND",
                 "N", "EVENT_ID", "TITLE"}


def metavar_ok(metavar: str) -> bool:
    return all(word in METAVAR_WORDS for word in re.split(r"[=,/]|\.\.\.", metavar) if word)


@pytest.mark.parametrize("subcommand", SUBCOMMANDS, ids=" ".join)
def test_every_argument_has_help_and_a_typed_metavar(subcommand: tuple[str, ...], monkeypatch: pytest.MonkeyPatch,
                                                     parsers: list[argparse.ArgumentParser]) -> None:  # noqa: F811
    ascii_stdout(monkeypatch)
    assert call([*subcommand, "--help"]) == 0
    for parser in parsers:
        for action in parser._actions:
            if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
                if isinstance(action, argparse._SubParsersAction):
                    assert all(choice.help for choice in action._choices_actions), parser.prog
                continue
            assert action.help, f"{parser.prog} {action.option_strings or action.dest} has no help"
            takes_value = action.nargs != 0 and not isinstance(action, (argparse._StoreTrueAction,
                                                                        argparse._StoreConstAction))
            if takes_value and action.choices is None:
                assert action.metavar and metavar_ok(action.metavar), \
                    f"{parser.prog} {action.option_strings or action.dest}: metavar {action.metavar!r}"


def test_every_top_level_help_page_builds_and_names_its_parsers(parsers: list[argparse.ArgumentParser],
                                                               capsys: pytest.CaptureFixture[str]) -> None:  # noqa: F811
    for name in atc_cli.COMMANDS:
        assert call([name, "--help"]) == 0
    capsys.readouterr()
    assert parsers and all(parser.prog.startswith("atc ") for parser in parsers), [p.prog for p in parsers]


@pytest.mark.parametrize("subcommand", SUBCOMMANDS, ids=" ".join)
def test_every_help_page_ends_with_exit_and_see_lines(subcommand: tuple[str, ...],
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    assert call([*subcommand, "--help"]) == 0
    block = capsys.readouterr().out.rstrip().split("\n\n")[-1].splitlines()
    assert block[0].startswith("Exit: ") and re.fullmatch(r"See: \S.+", block[-1]), block
    assert sum(line.startswith(("Exit: ", "See: ")) for line in block) == 2, block
    for reference in block[-1].removeprefix("See: ").split("; "):
        assert re.fullmatch(r"\S+\.md § \S.*", reference), f"{reference!r} names no document and section"


def test_top_level_help_groups_every_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert atc_cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for group in ("Task lifecycle:", "Workspace:"):
        assert group in out
    listed = {line.split()[0] for line in out.splitlines() if line.startswith("  ") and line.split()}
    assert set(atc_cli.COMMANDS) <= listed
    assert ".py" not in out


def test_version_names_both_packages(capsys: pytest.CaptureFixture[str]) -> None:
    import tomllib

    from atc_testkit import __version__
    pyproject = Path(atc_cli.__file__).resolve().parents[2] / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    assert project["name"] == "agent-traffic-control"
    assert atc_cli.main(["--version"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"atc {project['version']} ") and f"(atc-testkit {__version__})" in out


def test_the_envelope_leads_with_command_verdict_exit_code_and_hint() -> None:
    wrapped = cli_conventions.envelope("atc x", 1, {"command": "x", "verdict": "STOP", "hint": "do y", "detail": 1})
    assert list(wrapped)[:4] == ["command", "verdict", "exit_code", "hint"]
    assert wrapped == {"command": "atc x", "verdict": "STOP", "exit_code": 1, "hint": "do y", "detail": 1}
    refused = cli_conventions.envelope("atc x", 1, {"refusal": {"verdict": "STOP", "hint": "do z"}})
    assert (refused["verdict"], refused["hint"]) == ("STOP", "do z")
    assert cli_conventions.envelope("atc tests list", 0, [1], "scratches") == {
        "command": "atc tests list", "verdict": "OK", "exit_code": 0, "hint": None, "scratches": [1]}
    assert cli_conventions.envelope("atc y", 3, {})["verdict"] == "PARTIAL"


def test_atc_wraps_a_json_result_and_keeps_the_exit_code(monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(atc_cli, "_dispatch", lambda command, rest, root: print(json.dumps({"tasks": []})) or 1)
    assert atc_cli.main(["task", "list", "--json"], checkout=NOT_CANONICAL) == 1
    assert json.loads(capsys.readouterr().out) == {"command": "atc task list", "verdict": "STOP", "exit_code": 1,
                                                   "hint": None, "tasks": []}


@pytest.mark.parametrize(("name", "token"), [("Bad_Name", "PROJECT_NAME_INVALID"), ("root", "PROJECT_NAME_RESERVED")])
def test_bootstrap_add_exits_2_on_an_invalid_name(name: str, token: str, tmp_path: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    import bootstrap
    assert bootstrap.add(tmp_path, name, None, []) == 2
    assert capsys.readouterr().err.startswith(f"ERROR {token} ")
    assert bootstrap.add(tmp_path, "fine", "not a url", []) == 2
    assert capsys.readouterr().err.startswith("ERROR URL_INVALID ")


@pytest.mark.parametrize("source", ["bad source!", "owner", "owner/repo/extra", "/repo", "owner/"])
def test_bootstrap_init_exits_2_on_an_invalid_governance_source_and_writes_nothing(
        source: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import bootstrap
    assert bootstrap.init(tmp_path, source, force=False, environ={}) == 2
    assert capsys.readouterr().err.startswith("ERROR GOVERNANCE_SOURCE_INVALID ")
    assert not (tmp_path / "workspace.yaml").exists()
    assert bootstrap.init(tmp_path, "owner-1/repo.name_2", force=False, environ={}) == 0


def test_sync_apply_names_an_unknown_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import sync_project
    (tmp_path / "workspace.yaml").write_text(f"version: 1\nworkspace_root: {tmp_path.as_posix()}\nprojects: {{}}\n",
                                             encoding="utf-8")
    assert sync_project.apply("absent", root=tmp_path) == 2
    assert capsys.readouterr().err.startswith("ERROR PROJECT_UNKNOWN ")


@pytest.mark.parametrize("argv", [["task", "cleanup"], ["worktree", "audit"], ["onboard", "demo"], ["classify"]],
                         ids=" ".join)
def test_a_removed_form_is_an_argparse_usage_error(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert call(argv) == 2
    err = capsys.readouterr().err
    assert "usage:" in err or "unknown command" in err


def test_doctor_takes_json_and_refuses_unknown_arguments(monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    import doctor
    monkeypatch.setattr(doctor, "diagnose", lambda root, environ=None, checkout=None: [doctor.Check("x", "ERROR", "d")])
    monkeypatch.setattr(doctor, "worker_pool", lambda environ: {"state": "off", "detail": "off"})
    monkeypatch.setattr(doctor, "memory", lambda environ: None)
    assert doctor.main(["--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert (result["verdict"], result["errors"]) == ("NOT_READY", 1)
    assert result["checks"] == [{"name": "x", "status": "ERROR", "detail": "d"}]
    with pytest.raises(SystemExit) as stop:
        doctor.main(["--bogus"])
    assert stop.value.code == 2


@pytest.mark.parametrize(("argv", "root", "rest"), [
    (["--root", "R", "sync", "--check"], "R", ["--check"]),
    (["--root=R", "sync", "demo"], "R", ["demo"]),
    (["sync", "demo"], None, ["demo"]),
], ids=["global root before", "global root equals", "no root"])
def test_the_global_root_is_resolved_before_the_subcommand_sees_its_arguments(
        argv: list[str], root: str | None, rest: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    monkeypatch.setattr(atc_cli, "_dispatch", lambda command, rest, explicit: seen.append((command, rest, explicit)) or 0)
    assert atc_cli.main(argv, checkout=NOT_CANONICAL) == 0
    assert seen == [("sync", rest, root)]

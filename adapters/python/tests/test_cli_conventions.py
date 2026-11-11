"""shared/conformance/CLI.md: help form, metavars, the `--json` envelope, `--root`, versions and deprecated forms."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import atc_cli
import cli_conventions
import pytest
from test_atc_cli import SUBCOMMANDS, ascii_stdout, call, parsers  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

METAVAR_WORDS = {"NAME", "ID", "EVENT_ID", "SHA", "DIR", "FILE", "PATH", "SECONDS", "TEXT", "QUOTE", "URL", "OWNER",
                 "REPO", "REF", "TAG", "KEY", "ITEM", "TOPIC", "ARG", "COMMAND"}


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


def test_the_help_pages_checked_are_every_parser_and_testkit_usage(
        parsers: list[argparse.ArgumentParser], capsys: pytest.CaptureFixture[str]) -> None:  # noqa: F811
    """SUBCOMMANDS equals the parsers each top-level `--help` builds, plus the atc-testkit groups and usages."""
    from atc_testkit import commands
    for name in atc_cli.COMMANDS:
        assert call([name, "--help"]) == 0
    capsys.readouterr()
    built = {tuple(parser.prog.split()[1:]) for parser in parsers}
    assert all(parser.prog.startswith("atc ") for parser in parsers), [parser.prog for parser in parsers]
    expected = built | {(group,) for group in commands.GROUPS} | set(commands.USAGES)
    assert set(SUBCOMMANDS) == expected
    assert len(SUBCOMMANDS) == len(set(SUBCOMMANDS))


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
    for group in ("Task lifecycle:", "Testing:", "Workspace:", "Policy:", "Log:"):
        assert group in out
    listed = {line.split()[0] for line in out.splitlines() if line.startswith("  ") and line.split()}
    assert set(atc_cli.COMMANDS) | set(atc_cli.TESTKIT_COMMANDS) <= listed
    assert ".py" not in out


def test_version_names_both_packages(capsys: pytest.CaptureFixture[str]) -> None:
    from atc_testkit import __version__
    assert atc_cli.main(["--version"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("atc ") and f"(atc-testkit {__version__})" in out


@pytest.mark.parametrize(("argv", "root", "rest"), [
    (["--root", "R", "validate"], "R", ["validate"]),
    (["validate", "--root", "R", "--projects"], "R", ["validate", "--projects"]),
    (["validate", "--root=R"], "R", ["validate"]),
    (["test", "private", "--", "x", "--root", "R"], None, ["test", "private", "--", "x", "--root", "R"]),
])
def test_root_is_taken_before_or_after_the_command(argv: list[str], root: str | None, rest: list[str]) -> None:
    assert atc_cli._take_root(argv) == (root, rest, None)


def test_the_envelope_leads_with_command_verdict_exit_code_and_next() -> None:
    wrapped = cli_conventions.envelope("atc x", 1, {"command": "x", "refusal": {"verdict": "STOP", "next": "do y"},
                                                    "detail": 1})
    assert list(wrapped)[:4] == ["command", "verdict", "exit_code", "next"]
    assert wrapped == {"command": "atc x", "verdict": "STOP", "exit_code": 1, "next": "do y",
                       "refusal": {"verdict": "STOP", "next": "do y"}, "detail": 1}
    assert cli_conventions.envelope("atc tmp list", 0, [1], "scratches") == {
        "command": "atc tmp list", "verdict": "OK", "exit_code": 0, "next": None, "scratches": [1]}
    assert cli_conventions.envelope("atc y", 3, {})["verdict"] == "PARTIAL"


def test_atc_wraps_a_json_result_and_keeps_the_exit_code(monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(atc_cli, "_guard", lambda *args: None)
    monkeypatch.setattr(atc_cli, "_dispatch", lambda command, rest, root: print(json.dumps({"tasks": []})) or 1)
    assert atc_cli.main(["task", "list", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"command": "atc task list", "verdict": "STOP", "exit_code": 1,
                                                   "next": None, "tasks": []}


def test_classify_exits_2_on_an_unreadable_change_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import classify_change
    root = Path(atc_cli.__file__).resolve().parents[4]
    policy = root / "shared" / "policies" / "examples" / "escalation.yaml"
    assert classify_change.main(["--policy", str(policy), "--changed", str(tmp_path / "absent.txt")]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("ERROR CHANGED_UNREADABLE ")
    assert classify_change.main(["--policy", str(policy), "--changed", str(tmp_path / "absent.txt"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "CHANGED_UNREADABLE"


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


def test_onboard_apply_names_an_unknown_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import onboard_project
    (tmp_path / "workspace.yaml").write_text(f"version: 1\nworkspace_root: {tmp_path.as_posix()}\nprojects: {{}}\n",
                                             encoding="utf-8")
    assert onboard_project.apply("absent", root=tmp_path) == 2
    assert capsys.readouterr().err.startswith("ERROR PROJECT_UNKNOWN ")


def test_friction_exits_2_on_an_unknown_event_and_1_on_a_resolved_one(tmp_path: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    import friction
    log = tmp_path / "events.jsonl"
    assert friction.main(["resolve", "absent", "--evidence", "x"], events_file=log) == 2
    assert capsys.readouterr().err.startswith("ERROR FRICTION_INPUT ")
    event = friction.append_incident(kind=friction.ALLOWED_KINDS[0], project="p", tool="t", policy_key=None, skill=None,
                                  session_id=None, expected="e", observed="o", evidence="v", recurrence_key="k",
                                  events_file=log)
    assert friction.main(["resolve", event["event_id"], "--evidence", "x"], events_file=log) == 0
    assert friction.main(["resolve", event["event_id"], "--evidence", "x"], events_file=log) == 1
    assert "STOP ALREADY_RESOLVED: " in capsys.readouterr().err


def test_a_deprecated_bootstrap_sync_warns_and_runs_onboard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    import bootstrap
    seen: list[list[str]] = []
    monkeypatch.setattr(bootstrap.onboard_project, "main", lambda argv, root: seen.append(argv) or 0)
    monkeypatch.setattr(bootstrap, "validate", lambda root: 0)
    assert bootstrap.main(["sync", "demo", "--prune"], root=tmp_path) == 0
    assert seen == [["--sync", "--prune", "--project", "demo"]]
    assert capsys.readouterr().err.startswith(
        "WARNING DEPRECATED atc bootstrap sync: use atc onboard --sync --prune --project demo; removed in atc 0.4.0")


def test_a_positional_onboard_project_warns_and_names_the_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    import onboard_project
    monkeypatch.setattr(onboard_project, "load_projects", lambda root: {"demo": {"repo": "projects/demo"}})
    seen: list = []
    monkeypatch.setattr(onboard_project, "run", lambda mode, targets, *args, **kwargs: seen.append(targets) or 0)
    assert onboard_project.main(["--check", "demo"], root=tmp_path) == 0
    assert [name for name, _ in seen[0]] == ["demo"]
    assert "WARNING DEPRECATED atc onboard --check demo: use atc onboard --check --project demo" in capsys.readouterr().err


def test_worktree_cleanup_is_the_deprecated_spelling_of_clean(capsys: pytest.CaptureFixture[str]) -> None:
    import managed_worktree
    parser = managed_worktree.build_parser()
    assert parser.parse_args(["clean", "--owner", "claude", "--topic", "t"]).command == "clean"
    assert parser.parse_args(["cleanup", "--owner", "claude", "--topic", "t"]).command == "cleanup"
    assert call(["worktree", "cleanup", "--owner", "claude", "--topic", "t", "--retained-ref", "x"]) == 2
    assert "WARNING DEPRECATED atc worktree cleanup: use atc worktree clean" in capsys.readouterr().err


def test_doctor_takes_json_and_refuses_unknown_arguments(monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    import doctor
    monkeypatch.setattr(doctor, "diagnose", lambda root: [doctor.Check("x", "ERROR", "d")])
    monkeypatch.setattr(doctor, "worker_pool", lambda environ: {"state": "off", "detail": "off"})
    assert doctor.main(["--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"verdict": "NOT_READY", "errors": 1, "not_initialized": 0,
                                                   "checks": [{"name": "x", "status": "ERROR", "detail": "d"}],
                                                   "worker_pool": {"state": "off", "detail": "off"}}
    with pytest.raises(SystemExit) as stop:
        doctor.main(["--bogus"])
    assert stop.value.code == 2


def test_cli_md_lists_every_deprecated_form_and_names_the_next_release() -> None:
    root = Path(atc_cli.__file__).resolve().parents[4]
    text = (root / "shared" / "conformance" / "CLI.md").read_text(encoding="utf-8")
    for form in ("atc worktree cleanup", "atc onboard --check\\|--sync NAME", "atc bootstrap add NAME",
                 "atc bootstrap sync"):
        assert form in text
    assert f"| {cli_conventions.NEXT_RELEASE} |" in text

"""docs/TESTKIT.md § Commands and shared/conformance/CLI.md: help, usage errors, root resolution, the checkout guard,
dispatch of `atc` and `atc-maintain`, and the missing-dependency message."""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

import atc_cli
from atc_cli import maintain
from atc_testkit import check, gate

pytestmark = pytest.mark.integration

SUBCOMMANDS = [
    ("task",), ("task", "create"), ("task", "update"), ("task", "list"), ("task", "resume"), ("task", "close"),
    ("test",), ("integrate",), ("push",), ("init",), ("add",), ("remove",), ("sync",), ("doctor",), ("guard",),
    ("install",), ("checkout",), ("hook",),
]

# A directory without workspace.yaml is not a canonical checkout, so the guard lets the command through.
NOT_CANONICAL = Path(__file__).resolve().parent


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


def call(arguments: list[str], checkout: Path = NOT_CANONICAL) -> int:
    try:
        return atc_cli.main(arguments, checkout=checkout)
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


def test_a_usage_error_prints_the_usage_line_and_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    error = argparse.ArgumentParser.error
    assert call(["sync", "--no-such-option"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: atc sync") and "atc sync: error:" in err
    assert argparse.ArgumentParser.error is error


def test_a_removed_command_is_an_unknown_command_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert call(["classify"]) == 2
    assert "unknown command: classify" in capsys.readouterr().err


def test_help_restores_the_program_name(monkeypatch: pytest.MonkeyPatch) -> None:
    ascii_stdout(monkeypatch)
    before = sys.argv[0]
    assert call(["task", "--help"]) == 0
    assert sys.argv[0] == before


@pytest.mark.parametrize("main", [lambda: check.main(["--help"]), lambda: gate.main(None, ["--help"])],
                         ids=["check", "gate"])
def test_testkit_entry_points_take_no_abbreviation(main, parsers: list[argparse.ArgumentParser],
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        main()
    assert stop.value.code == 0 and parsers and all(not parser.allow_abbrev for parser in parsers)


def test_an_abbreviated_option_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    code = call(["sync", "--chec"])
    assert code == 2 and "unrecognized arguments: --chec" in capsys.readouterr().err


def test_a_missing_dependency_names_the_reinstall_command(monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setitem(atc_cli.COMMANDS, "doctor", "atc_missing_dependency_probe")
    assert call(["doctor"]) == 2
    err = capsys.readouterr().err
    expected = f'uv tool install -e "{Path(atc_cli.__file__).resolve().parents[2]}" --force'
    assert err.startswith("ERROR MISSING_DEPENDENCY atc doctor needs the module 'atc_missing_dependency_probe'")
    assert expected in err and "Traceback" not in err


@pytest.fixture(autouse=True)
def _a_workspace_above_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Commands resolve a workspace directory of the test's own unless a test names or removes one: the suite's
    working directory need not lie in a workspace."""
    workspace = tmp_path / "default-workspace"
    workspace.mkdir()
    (workspace / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setenv("ATC_ROOT", str(workspace))


def test_outside_any_workspace_a_command_stops_and_init_doctor_and_help_act_on_the_working_directory(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """There is no fallback to the checkout the code comes from: a workspace is a directory holding workspace.yaml."""
    import doctor
    import sync_project
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.delenv("ATC_ROOT")
    monkeypatch.chdir(outside)
    with pytest.raises(atc_cli.WorkspaceNotFound):
        atc_cli.resolve_root(None, {}, outside)
    monkeypatch.setattr(sync_project, "main", lambda rest, root: pytest.fail("sync ran without a workspace"))
    assert call(["sync"]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"STOP WORKSPACE_NOT_FOUND: no workspace.yaml in {outside.resolve()} or any parent\n")
    assert "hint: " in err
    seen: list[Path] = []
    monkeypatch.setattr(doctor, "main", lambda rest, root: seen.append(root) or 0)
    assert call(["doctor"]) == 0 and seen == [outside.resolve()]
    assert call(["task", "--help"]) == 0


def nested_checkouts(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace directory holding workspace.yaml, and a linked worktree inside it without one; neither holds
    shared/governance, which root resolution never asks for."""
    workspace = tmp_path / "workspace"
    worktree = workspace / ".claude" / "worktrees" / "topic"
    for checkout in (workspace, worktree):
        (checkout / "adapters" / "python" / "src" / "atc_cli" / "scripts").mkdir(parents=True)
        (checkout / "adapters" / "python" / "src" / "atc_cli" / "scripts" / "validate_workspace.py").write_text("", encoding="utf-8")
    (workspace / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    return workspace, worktree


def test_a_worktree_resolves_to_the_workspace_above_it(tmp_path: Path) -> None:
    workspace, worktree = nested_checkouts(tmp_path)
    cwd = worktree / "adapters" / "python"
    assert atc_cli.resolve_root(None, {}, cwd) == workspace.resolve()
    assert atc_cli.resolve_root(None, {"ATC_ROOT": str(worktree)}, cwd) == worktree.resolve()
    assert atc_cli.resolve_root(str(worktree), {"ATC_ROOT": str(workspace)}, cwd) == worktree.resolve()


@pytest.mark.parametrize("args", [["sync"], ["sync", "demo", "--check"]], ids=" ".join)
def test_sync_dispatch_passes_the_resolved_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                args: list[str]) -> None:
    workspace, worktree = nested_checkouts(tmp_path)
    import sync_project
    seen: dict[str, Path] = {}

    def record(rest: list[str], root: Path) -> int:
        seen["root"] = root
        return 0

    monkeypatch.setattr(sync_project, "main", record)
    monkeypatch.delenv("ATC_ROOT", raising=False)
    monkeypatch.chdir(worktree / "adapters" / "python")
    assert call(args) == 0
    assert seen["root"] == workspace.resolve()


def test_task_dispatch_passes_the_resolved_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import task_records
    seen: dict[str, object] = {}

    def record(rest: list[str], root: Path) -> int:
        seen.update(rest=rest, root=root)
        return 0

    monkeypatch.setattr(task_records, "main", record)
    assert call(["--root", str(tmp_path), "task", "list", "--all"]) == 0
    assert seen == {"rest": ["list", "--all"], "root": tmp_path.resolve()}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture()
def canonical(tmp_path: Path) -> Path:
    """A canonical checkout: holds workspace.yaml naming itself the governance checkout, its git dir is the common dir,
    on main with one tracked file."""
    checkout = tmp_path / "workspace"
    checkout.mkdir()
    git(checkout, "init", "-q", "-b", "main")
    git(checkout, "config", "user.email", "t@example.invalid")
    git(checkout, "config", "user.name", "t")
    (checkout / "AGENTS.md").write_text("# root\n", encoding="utf-8")
    git(checkout, "add", "AGENTS.md")
    git(checkout, "commit", "-q", "-m", "root")
    (checkout / "workspace.yaml").write_text("version: 1\ngovernance_checkout: .\n", encoding="utf-8")
    return checkout


def test_the_canonical_checkout_on_main_and_clean_passes_silently(canonical: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert atc_cli._guard("sync", [], canonical) is None
    assert capsys.readouterr().err == ""


def test_the_guard_applies_only_when_the_workspace_directory_is_the_governance_checkout(
        canonical: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import doctor
    git(canonical, "switch", "-q", "-c", "topic")
    for content in ("version: 1\n", f"version: 1\ngovernance_checkout: {(tmp_path / 'elsewhere').as_posix()}\n"):
        (canonical / "workspace.yaml").write_text(content, encoding="utf-8")
        assert atc_cli._guard("sync", [], canonical) is None
        assert doctor.checkout_check(canonical).status == "READY"
    assert capsys.readouterr().err == ""
    (canonical / "workspace.yaml").write_text("version: 1\ngovernance_checkout: .\n", encoding="utf-8")
    assert atc_cli._guard("sync", [], canonical) == 2


def test_the_guard_applies_to_a_governance_checkout_whose_workspace_yaml_does_not_say_so(
        canonical: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A Git top level holding atc-manifest.yaml is the governance checkout without governance_checkout too."""
    import doctor
    (canonical / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    (canonical / "atc-manifest.yaml").write_text("manifest: 1\n", encoding="utf-8")
    git(canonical, "add", "atc-manifest.yaml")
    git(canonical, "commit", "-q", "-m", "manifest")
    assert atc_cli._guard("task", ["list"], canonical) is None
    git(canonical, "switch", "-q", "-c", "topic")
    for command in ("task", "test", "integrate", "push"):
        assert atc_cli._guard(command, [], canonical) == 2
    assert "CHECKOUT_NOT_MAIN" in capsys.readouterr().err
    assert doctor.checkout_check(canonical).status == "ERROR"


def test_a_canonical_checkout_off_main_refuses_every_command_but_doctor_and_help(
    canonical: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    git(canonical, "switch", "-q", "-c", "topic")
    assert atc_cli.main(["sync"], checkout=canonical) == 2
    assert capsys.readouterr().err.startswith(f"ERROR CHECKOUT_NOT_MAIN {canonical} is on topic\n")
    for command in ("task", "test", "integrate", "push", "init", "add", "remove", "sync", "guard"):
        assert atc_cli._guard(command, [], canonical) == 2
    assert atc_cli._guard("doctor", [], canonical) is None
    assert atc_cli._guard("integrate", ["--help"], canonical) is None
    git(canonical, "switch", "-q", "--detach")
    assert atc_cli._guard("sync", [], canonical) == 2
    assert "is on a detached HEAD" in capsys.readouterr().err


def test_a_dirty_canonical_checkout_warns_and_runs(canonical: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (canonical / "AGENTS.md").write_text("# edited\n", encoding="utf-8")
    assert atc_cli._guard("sync", [], canonical) is None
    assert capsys.readouterr().err == (f"WARNING CHECKOUT_DIRTY {canonical} has tracked modifications; "
                                       "atc and sessions read them\n")


def test_the_guard_reads_the_resolved_root_not_the_code_checkout(
    canonical: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """The installed atc runs a release copy; the checkout the guard protects is the root it acts on."""
    import sync_project
    monkeypatch.setattr(atc_cli, "GUARDED_CHECKOUT", None)
    monkeypatch.setattr(atc_cli, "CHECKOUT", NOT_CANONICAL)
    monkeypatch.setattr(sync_project, "main", lambda rest, root: 0)
    monkeypatch.delenv("ATC_ROOT", raising=False)
    assert atc_cli.main(["--root", str(canonical), "sync"]) == 0
    git(canonical, "switch", "-q", "-c", "topic")
    assert atc_cli.main(["--root", str(canonical), "sync"]) == 2
    assert capsys.readouterr().err.startswith(f"ERROR CHECKOUT_NOT_MAIN {canonical.resolve()} is on topic\n")
    monkeypatch.setenv("ATC_ROOT", str(canonical))
    assert atc_cli.main(["sync"]) == 2
    monkeypatch.delenv("ATC_ROOT")
    (canonical / "shared" / "governance").mkdir(parents=True)
    monkeypatch.chdir(canonical)
    assert atc_cli.main(["sync"]) == 2


def test_a_linked_worktree_and_a_checkout_without_the_map_are_not_canonical(canonical: Path) -> None:
    linked = canonical / "worktrees" / "root" / "claude+topic"
    git(canonical, "worktree", "add", "-q", "-b", "claude+topic", str(linked))
    (linked / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    assert atc_cli._guard("sync", [], linked) is None
    (canonical / "workspace.yaml").unlink()
    git(canonical, "switch", "-q", "-c", "topic")
    assert atc_cli._guard("sync", [], canonical) is None


def test_doctor_lists_the_guard_conditions(canonical: Path) -> None:
    import doctor
    assert doctor.checkout_check(canonical).status == "READY"
    (canonical / "AGENTS.md").write_text("# edited\n", encoding="utf-8")
    assert doctor.checkout_check(canonical).status == "ERROR"
    git(canonical, "stash", "-q")
    git(canonical, "switch", "-q", "-c", "topic")
    check_ = doctor.checkout_check(canonical)
    assert (check_.status, check_.detail.endswith("every other atc command refuses")) == ("ERROR", True)


def test_an_unreadable_canonical_checkout_refuses(canonical: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (canonical / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")
    assert atc_cli._guard("sync", [], canonical) == 2
    assert capsys.readouterr().err.startswith(f"ERROR CHECKOUT_UNREADABLE {canonical} ")
    import doctor
    assert doctor.checkout_check(canonical).status == "ERROR"


def test_an_inherited_git_dir_does_not_redirect_the_guard(
    canonical: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "topic")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert atc_cli._guard("sync", [], canonical) is None


def test_the_console_entry_sets_utf8_for_every_child(monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8:surrogateescape")
    monkeypatch.setattr(atc_cli.sys, "argv", ["atc", "--help"])
    assert atc_cli.main() == 0
    capsys.readouterr()
    assert os.environ["PYTHONIOENCODING"] == "utf-8"


def test_a_call_with_arguments_leaves_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8:surrogateescape")
    assert atc_cli.main(["--help"]) == 0
    assert os.environ["PYTHONIOENCODING"] == "utf-8:surrogateescape"


def test_root_is_taken_before_or_after_the_command() -> None:
    assert atc_cli.take_root(["--root", "R", "sync"]) == ("R", ["sync"], None)
    assert atc_cli.take_root(["sync", "--root", "R", "--check"]) == ("R", ["sync", "--check"], None)
    assert atc_cli.take_root(["sync", "--root=R"]) == ("R", ["sync"], None)
    assert atc_cli.take_root(["task", "create", "--", "x", "--root", "R"]) == (None, ["task", "create", "--", "x", "--root", "R"], None)
    assert atc_cli.take_root(["sync", "--root"])[2] == "--root needs a directory"


def test_maintainer_commands_refuse_the_canonical_checkout(canonical: Path, monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    scripts = canonical / "adapters" / "python" / "src" / "atc_cli" / "scripts"  # maintain finds the checkout by its scripts
    scripts.mkdir(parents=True)
    (scripts / "validate_workspace.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(canonical)
    assert maintain.main(["validate"]) == 2
    assert capsys.readouterr().err.startswith(f"ERROR CHECKOUT_CANONICAL {canonical.resolve()} ")


def test_maintainer_commands_refuse_a_release_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    import checkout_guard
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    copy = tmp_path / "home" / ".atc" / "releases" / "v0.6.0"
    scripts = copy / "adapters" / "python" / "src" / "atc_cli" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "validate_workspace.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(maintain, "CHECKOUT", copy)
    monkeypatch.chdir(tmp_path)
    assert maintain.main(["validate"]) == 2
    assert capsys.readouterr().err.startswith(f"ERROR CHECKOUT_CANONICAL {copy} is a release copy of the installed atc")
    assert checkout_guard.release_copy(copy)
    assert not checkout_guard.release_copy(scripts)
    assert not checkout_guard.release_copy(tmp_path / "home" / ".atc" / "other" / "v0.6.0")


def test_maintainer_commands_dispatch_to_their_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, worktree = nested_checkouts(tmp_path)
    import sync_project
    import task_records
    import validate_workspace
    seen: list = []
    monkeypatch.setattr(maintain, "CHECKOUT", worktree)  # a linked worktree is not canonical
    monkeypatch.chdir(worktree / "adapters" / "python")
    monkeypatch.setattr(sync_project, "root_main", lambda argv, root: seen.append(("sync", argv, root)) or 0)
    monkeypatch.setattr(validate_workspace, "main", lambda argv: seen.append(("validate", argv)) or 0)
    monkeypatch.setattr(task_records, "check_main", lambda argv: seen.append(("task", argv)) or 0)
    assert maintain.main(["sync", "--root", "--check"]) == 0
    assert maintain.main(["validate"]) == 0
    assert maintain.main(["task", "check", "DIR"]) == 0
    assert seen == [
        ("sync", ["--root", "--check"], worktree.resolve()),
        ("validate", ["--root", str(worktree.resolve())]),
        ("task", ["DIR"]),
    ]


def test_maintainer_task_without_check_is_a_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    _, worktree = nested_checkouts(tmp_path)
    monkeypatch.setattr(maintain, "CHECKOUT", worktree)
    monkeypatch.chdir(worktree / "adapters" / "python")
    assert maintain.main(["task", "list"]) == 2
    assert "usage: atc-maintain task check" in capsys.readouterr().err


def test_maintainer_unknown_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert maintain.main(["release"]) == 2
    assert "unknown command: release" in capsys.readouterr().err


def test_doctor_verdict_line_counts_and_lists_only_what_is_not_ready(monkeypatch: pytest.MonkeyPatch,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    import doctor
    monkeypatch.setattr(doctor, "diagnose", lambda root, environ=None, checkout=None: [
        doctor.Check("a", "READY", "ok"), doctor.Check("b", "WARN", "w"), doctor.Check("c", "NOT_INITIALIZED", "n")])
    assert doctor.main([], root=NOT_CANONICAL) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "READY doctor: 0 error(s), 1 warning(s), 1 not initialized"
    assert any(line.endswith("b: w") for line in lines) and any(line.endswith("c: n") for line in lines)
    assert not any(line.endswith("a: ok") for line in lines)


def test_doctor_verbose_lists_every_check_and_the_worker_pool(monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    import doctor
    from atc_testkit import status as pool_status
    monkeypatch.setattr(doctor, "diagnose", lambda root, environ=None, checkout=None: [doctor.Check("a", "READY", "ok")])
    monkeypatch.setattr(pool_status, "collect", lambda environ: {})
    monkeypatch.setattr(pool_status, "lines", lambda state: ["pool-line"])
    assert doctor.main(["-v"], root=NOT_CANONICAL) == 0
    out = capsys.readouterr().out
    assert "READY  " in out and out.count("\n") >= 3
    assert "a: ok" in out and "test workers:" in out and "pool-line" in out


def test_doctor_error_is_not_ready_and_stops_on_stderr(monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    import doctor
    monkeypatch.setattr(doctor, "diagnose", lambda root, environ=None, checkout=None: [doctor.Check("x", "ERROR", "bad")])
    assert doctor.main([], root=NOT_CANONICAL) == 1
    captured = capsys.readouterr()
    assert captured.out.startswith("NOT_READY doctor: 1 error(s), 0 warning(s), 0 not initialized\n")
    assert captured.err.startswith("STOP NOT_READY: ") and "hint: fix the ERROR checks" in captured.err

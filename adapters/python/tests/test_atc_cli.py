"""docs/TESTKIT.md § Commands: help, missing dependencies and option names of every ``atc`` command."""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

import atc_cli
from atc_testkit import check, gate

pytestmark = pytest.mark.integration

SUBCOMMANDS = [
    ("worktree",), ("worktree", "create"), ("worktree", "resume"), ("worktree", "audit"), ("worktree", "cleanup"),
    ("preflight",), ("preflight", "merge"), ("preflight", "cleanup"),
    ("integrate",), ("close-out",), ("task",), ("task", "new"), ("task", "list"), ("task", "check"), ("validate",), ("onboard",),
    ("bootstrap",), ("bootstrap", "init"), ("bootstrap", "add"), ("bootstrap", "sync"), ("bootstrap", "status"),
    ("doctor",), ("policies",), ("policies", "check"), ("enforce",), ("classify",),
    ("friction",), ("friction", "add"), ("friction", "list"), ("friction", "summary"), ("friction", "resolve"),
    ("test",), ("test", "run"), ("test", "mutants"),
    ("tmp",), ("tmp", "checkout"), ("tmp", "list"), ("tmp", "clean"), ("tmp", "prune"),
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


def nested_checkouts(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace checkout holding workspace.yaml and a linked worktree inside it without one."""
    workspace = tmp_path / "workspace"
    worktree = workspace / ".claude" / "worktrees" / "topic"
    for checkout in (workspace, worktree):
        (checkout / "shared" / "governance").mkdir(parents=True)
        (checkout / "adapters" / "python" / "scripts").mkdir(parents=True)
        (checkout / "adapters" / "python" / "scripts" / "onboard_project.py").write_text("", encoding="utf-8")
    (workspace / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    return workspace, worktree


def test_root_only_onboard_renders_the_linked_worktree_not_the_workspace_above(tmp_path: Path) -> None:
    workspace, worktree = nested_checkouts(tmp_path)
    cwd = worktree / "adapters" / "python"
    assert atc_cli.resolve_root(None, {}, cwd) == workspace.resolve()
    assert atc_cli.resolve_checkout(None, {}, cwd) == worktree.resolve()
    assert atc_cli.resolve_checkout(None, {"ATC_ROOT": str(workspace)}, cwd) == workspace.resolve()


@pytest.mark.parametrize(("args", "expected"), [(["--sync", "--root-only"], "worktree"), (["--check", "demo"], "workspace")])
def test_onboard_dispatch_picks_the_root_by_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], expected: str,
) -> None:
    workspace, worktree = nested_checkouts(tmp_path)
    import onboard_project
    seen: dict[str, Path] = {}

    def record(rest: list[str], root: Path) -> int:
        seen["root"] = root
        return 0

    monkeypatch.setattr(onboard_project, "main", record)
    monkeypatch.delenv("ATC_ROOT", raising=False)
    monkeypatch.chdir(worktree / "adapters" / "python")
    assert atc_cli.main(["onboard", *args]) == 0
    assert seen["root"] == {"worktree": worktree, "workspace": workspace}[expected].resolve()


def test_task_dispatch_passes_the_resolved_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import task_records
    seen: dict[str, object] = {}

    def record(rest: list[str], root: Path) -> int:
        seen.update(rest=rest, root=root)
        return 0

    monkeypatch.setattr(task_records, "main", record)
    assert atc_cli.main(["--root", str(tmp_path), "task", "list", "--all"]) == 0
    assert seen == {"rest": ["list", "--all"], "root": tmp_path.resolve()}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture()
def canonical(tmp_path: Path) -> Path:
    """A canonical checkout: holds workspace.yaml, its git dir is the common dir, on main with one tracked file."""
    checkout = tmp_path / "workspace"
    checkout.mkdir()
    git(checkout, "init", "-q", "-b", "main")
    git(checkout, "config", "user.email", "t@example.invalid")
    git(checkout, "config", "user.name", "t")
    (checkout / "AGENTS.md").write_text("# root\n", encoding="utf-8")
    git(checkout, "add", "AGENTS.md")
    git(checkout, "commit", "-q", "-m", "root")
    (checkout / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    return checkout


def test_the_canonical_checkout_on_main_and_clean_passes_silently(canonical: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert atc_cli._guard("validate", [], canonical) is None
    assert capsys.readouterr().err == ""


def test_a_canonical_checkout_off_main_refuses_every_command_but_doctor_and_help(
    canonical: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    git(canonical, "switch", "-q", "-c", "topic")
    assert atc_cli.main(["validate"], checkout=canonical) == 2
    assert capsys.readouterr().err.startswith(f"ERROR CHECKOUT_NOT_MAIN {canonical} is on topic;")
    for command in ("worktree", "integrate", "task", "onboard", "test", "tmp"):
        assert atc_cli._guard(command, [], canonical) == 2
    assert atc_cli._guard("doctor", [], canonical) is None
    assert atc_cli._guard("integrate", ["--help"], canonical) is None
    git(canonical, "switch", "-q", "--detach")
    assert atc_cli._guard("validate", [], canonical) == 2
    assert "is on a detached HEAD" in capsys.readouterr().err


def test_a_dirty_canonical_checkout_warns_and_runs(canonical: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (canonical / "AGENTS.md").write_text("# edited\n", encoding="utf-8")
    assert atc_cli._guard("validate", [], canonical) is None
    assert capsys.readouterr().err.startswith(f"WARNING CHECKOUT_DIRTY {canonical} ")


def test_a_linked_worktree_and_a_checkout_without_the_map_are_not_canonical(canonical: Path, tmp_path: Path) -> None:
    linked = canonical / "worktrees" / "root" / "claude+topic"
    git(canonical, "worktree", "add", "-q", "-b", "worktree-claude+topic", str(linked))
    (linked / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    assert atc_cli._guard("validate", [], linked) is None
    (canonical / "workspace.yaml").unlink()
    git(canonical, "switch", "-q", "-c", "topic")
    assert atc_cli._guard("validate", [], canonical) is None


def test_root_only_onboard_refuses_the_canonical_checkout(
    canonical: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    (canonical / "shared" / "governance").mkdir(parents=True)
    (canonical / "adapters" / "python" / "scripts").mkdir(parents=True)
    (canonical / "adapters" / "python" / "scripts" / "onboard_project.py").write_text("", encoding="utf-8")
    monkeypatch.delenv("ATC_ROOT", raising=False)
    monkeypatch.chdir(canonical)
    assert atc_cli.main(["onboard", "--sync", "--root-only"]) == 2
    assert capsys.readouterr().err.startswith(f"ERROR CHECKOUT_CANONICAL {canonical.resolve()} ")


def test_doctor_lists_the_guard_conditions(canonical: Path) -> None:
    import doctor
    assert doctor.checkout_check(canonical).status == "READY"
    (canonical / "AGENTS.md").write_text("# edited\n", encoding="utf-8")
    assert doctor.checkout_check(canonical).status == "ERROR"
    git(canonical, "stash", "-q")
    git(canonical, "switch", "-q", "-c", "topic")
    check = doctor.checkout_check(canonical)
    assert (check.status, check.detail.endswith("every other atc command refuses")) == ("ERROR", True)


def test_an_unreadable_canonical_checkout_refuses(canonical: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (canonical / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")
    assert atc_cli._guard("validate", [], canonical) == 2
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
    assert atc_cli._guard("validate", [], canonical) is None


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

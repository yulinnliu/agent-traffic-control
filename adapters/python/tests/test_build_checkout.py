"""`atc checkout` (src/atc_cli/scripts/build_checkout.py, docs/WORKFLOW.md § Build trees) and `atc hook`."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import atc_cli
import build_checkout
import git_history
import pretooluse_hook
from atc_testkit import locations, scratch

pytestmark = pytest.mark.integration


def git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env,
                          check=True).stdout.strip()


def commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "--", name)
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


class Ws:
    def __init__(self, root: Path, project: Path, on_main: str, off_main: str) -> None:
        self.root, self.project, self.on_main, self.off_main = root, project, on_main, off_main

    def run(self, *args: str) -> int:
        environ = {key: value for key, value in os.environ.items() if key != "ATC_ROOT"}
        return build_checkout.main(list(args), root=self.root, environ=environ)

    def tmp(self) -> Path:
        return locations.locate(dict(os.environ)).tmp


def built_project(built: Path) -> tuple[str, str]:
    """Project demo's repository: two commits on main and one on a side branch; returns the commit on main and the
    commit off it."""
    project = built / "projects" / "demo"
    project.mkdir(parents=True)
    git(project, "init", "-q", "-b", "main")
    commit(project, "a.txt", "one\n")
    on_main = commit(project, "a.txt", "two\n")
    git(project, "switch", "-q", "-c", "side")
    off_main = commit(project, "b.txt", "side\n")
    git(project, "switch", "-q", "main")
    return on_main, off_main


@pytest.fixture()
def ws(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Ws:
    """A workspace with project demo: two commits on main and one on a side branch."""
    monkeypatch.delenv("ATC_ROOT", raising=False)
    root = tmp_path / "workspace"
    on_main, off_main = git_history.copied(tmp_path_factory, "build-checkout-project", root, built_project)
    (root / "workspace.yaml").write_text(
        f"version: 1\nworkspace_root: '{root}'\ngovernance_source: example/governance\nprojects:\n"
        "  demo:\n    repo: projects/demo\n    worktrees: worktrees/demo\n    state: state/demo-data\n",
        encoding="utf-8")
    return Ws(root, root / "projects" / "demo", on_main, off_main)


def registered(ws: Ws, path: Path) -> bool:
    return any(locations.same_path(row.scratch, path) for row in scratch.registered(ws.tmp()))


def test_a_build_worktree_is_detached_locked_registered_then_removed(ws: Ws,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    assert ws.run("demo", ws.on_main[:7]) == 0
    path = ws.root / "worktrees" / "demo" / f"build+{ws.on_main[:7]}"
    assert capsys.readouterr().out.strip().split(maxsplit=2) == ["CHECKOUT", ws.on_main, str(path)]
    assert git(path, "rev-parse", "HEAD") == ws.on_main
    assert git(path, "branch", "--show-current") == ""
    assert "locked" in git(ws.project, "worktree", "list", "--porcelain")
    assert registered(ws, path)

    assert ws.run("demo", ws.on_main, "--json") == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "CURRENT"

    assert ws.run("demo", ws.on_main, "--remove") == 0
    assert capsys.readouterr().out.startswith("REMOVED ")
    assert not path.exists() and not registered(ws, path)
    assert ws.run("demo", ws.on_main, "--remove") == 0
    assert capsys.readouterr().out.startswith("ABSENT ")


def test_an_exported_build_tree_holds_the_files_and_is_removed(ws: Ws, capsys: pytest.CaptureFixture[str]) -> None:
    assert ws.run("demo", ws.on_main, "--no-git") == 0
    verdict, _, tree = capsys.readouterr().out.strip().split(maxsplit=2)
    assert verdict == "CHECKOUT"
    assert (Path(tree) / "a.txt").read_text(encoding="utf-8") == "two\n"
    assert not (Path(tree) / ".git").exists()
    assert registered(ws, Path(tree).parent)
    assert ws.run("demo", ws.on_main, "--no-git") == 0
    assert capsys.readouterr().out.startswith("CURRENT ")
    assert ws.run("demo", ws.on_main, "--no-git", "--remove") == 0
    assert not Path(tree).parent.exists() and not registered(ws, Path(tree).parent)


def test_a_commit_off_main_and_an_occupied_path_stop(ws: Ws, capsys: pytest.CaptureFixture[str]) -> None:
    assert ws.run("demo", ws.off_main) == 1
    assert capsys.readouterr().err.startswith("STOP NOT_ON_MAIN")
    assert ws.run("demo", "xyz") == 2
    assert capsys.readouterr().err.startswith("ERROR SHA_INVALID")
    occupied = ws.root / "worktrees" / "demo" / f"build+{ws.on_main[:7]}"
    occupied.mkdir(parents=True)
    (occupied / "keep.txt").write_text("mine\n", encoding="utf-8")
    assert ws.run("demo", ws.on_main) == 1
    assert capsys.readouterr().err.startswith("STOP PATH_OCCUPIED")
    assert ws.run("demo", ws.on_main, "--remove") == 1
    assert (occupied / "keep.txt").is_file()


def test_an_unknown_project_stops(ws: Ws, capsys: pytest.CaptureFixture[str]) -> None:
    assert ws.run("nope", ws.on_main) == 1
    assert capsys.readouterr().err.startswith("STOP UNKNOWN_PROJECT")


def test_atc_hook_runs_the_engine_outside_any_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATC_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    seen: list[list[str]] = []
    monkeypatch.setattr(pretooluse_hook, "main", lambda argv: seen.append(argv) or 0)
    assert atc_cli.main(["hook", "--harness", "codex"]) == 0
    assert seen == [["--harness", "codex"]]

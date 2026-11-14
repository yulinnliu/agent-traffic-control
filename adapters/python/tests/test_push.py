"""`atc push`: the Owner's publication of main to origin after checking every commit (`push.py`).

Each test runs real Git: a repository that is the workspace root (so bare `atc push` checks it as `root`) and
a bare `origin` beside it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import push
from test_git_integrate import (  # noqa: F401  (repo is a fixture)
    add_origin, commit, main_tip, publish, repo, run_git,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def ws(repo: Path) -> Path:
    """The repository is the workspace root with no projects: bare `atc push` checks it as `root`."""
    (repo / "workspace.yaml").write_text(
        f"version: 1\nworkspace_root: '{repo}'\nprojects: {{}}\n", encoding="utf-8")
    return repo


def origin_main(repo: Path) -> str:
    """The SHA origin's main names, read back from the remote itself."""
    out = subprocess.run(["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                         capture_output=True, text=True, check=True).stdout.split()
    return out[0] if out else ""


def ledger(repo: Path, *shas: str, base: str) -> None:
    """Record ``shas`` as integrations of main, the ledger `atc integrate` writes."""
    path = repo / ".git" / "atc" / "integrations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    previous = base
    for sha in shas:
        rows.append({"candidate_sha": sha, "reviewed": sha, "main_before": previous,
                     "authority_reference": f"Owner run: approve integration @ {sha}",
                     "integrated_at": "2026-11-01T00:00:00+00:00"})
        previous = sha
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def other_clone(repo: Path) -> Path:
    """A second clone of origin, to advance origin without touching the checkout under test."""
    clone = repo.parent / "other"
    subprocess.run(["git", "clone", "-q", str(repo.parent / "origin.git"), str(clone)],
                   capture_output=True, text=True, check=True)
    return clone


def test_bare_push_checks_and_reports_current(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(ws, "main")
    assert push.main([], root=ws) == 0
    assert capsys.readouterr().out.splitlines()[0].startswith("CURRENT root")


def test_bare_push_lists_unpushed_integrations_and_pushes_nothing(ws: Path,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    publish(ws, "main")
    base = main_tip(ws)
    integrated = commit(ws, "a.txt", "integrated\n")
    ledger(ws, integrated, base=base)
    assert push.main([], root=ws) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("CHECK root")
    assert f"integration {integrated[:12]}" in out
    assert origin_main(ws) == base


def test_an_unrecorded_commit_refuses_the_push(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(ws, "main")
    base = main_tip(ws)
    commit(ws, "a.txt", "unrecorded\n")
    assert push.main(["root"], root=ws) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("REFUSED root")
    assert "UNRECORDED_COMMITS" in out
    assert origin_main(ws) == base


def test_main_behind_origin_refuses_the_push(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(ws, "main")
    other = other_clone(ws)
    commit(other, "b.txt", "from origin\n")
    run_git(other, "push", "-q", "origin", "main")
    assert push.main(["root"], root=ws) == 1
    out = capsys.readouterr().out
    assert "MAIN_BEHIND_ORIGIN" in out


def test_diverged_main_refuses_the_push(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(ws, "main")
    base = main_tip(ws)
    local = commit(ws, "a.txt", "local\n")
    ledger(ws, local, base=base)
    other = other_clone(ws)
    theirs = commit(other, "b.txt", "from origin\n")
    run_git(other, "push", "-q", "origin", "main")
    assert push.main(["root"], root=ws) == 1
    out = capsys.readouterr().out
    assert "MAIN_DIVERGED_FROM_ORIGIN" in out
    assert origin_main(ws) == theirs != base


def test_a_named_push_publishes_the_checked_sha_and_reads_it_back(ws: Path,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    publish(ws, "main")
    base = main_tip(ws)
    integrated = commit(ws, "a.txt", "integrated\n")
    ledger(ws, integrated, base=base)
    assert push.main(["root"], root=ws) == 0
    assert capsys.readouterr().out.splitlines()[0].startswith("PUSHED root")
    assert origin_main(ws) == integrated


def test_a_named_push_with_nothing_to_push_is_current(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(ws, "main")
    assert push.main(["root"], root=ws) == 0
    assert capsys.readouterr().out.splitlines()[0].startswith("CURRENT root")


def test_all_declined_pushes_nothing(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(ws, "main")
    base = main_tip(ws)
    integrated = commit(ws, "a.txt", "integrated\n")
    ledger(ws, integrated, base=base)
    asked: list[str] = []

    def decline(prompt: str) -> str:
        asked.append(prompt)
        return "n"

    assert push.main(["--all"], root=ws, answer=decline) == 1
    assert asked == ["push root? [y/N] "]
    assert "DECLINED" in capsys.readouterr().out
    assert origin_main(ws) == base


def test_all_accepted_pushes_the_repository(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(ws, "main")
    base = main_tip(ws)
    integrated = commit(ws, "a.txt", "integrated\n")
    ledger(ws, integrated, base=base)
    assert push.main(["--all"], root=ws, answer=lambda _prompt: "y") == 0
    assert origin_main(ws) == integrated


def test_no_origin_main_refuses_the_first_publication(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add_origin(ws)
    base = main_tip(ws)
    assert push.main(["root"], root=ws) == 1
    out = capsys.readouterr().out
    assert "NO_ORIGIN_MAIN" in out
    assert origin_main(ws) == ""
    assert main_tip(ws) == base


@pytest.mark.parametrize("argv", [["root", "--check"], ["--all", "--check"], ["root", "--check", "--json"]])
def test_check_with_names_or_all_lists_and_never_pushes(ws: Path, argv: list[str],
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    """The agents' allowed form: `--check` beside a name or `--all` only lists, asks nothing and pushes nothing."""
    publish(ws, "main")
    base = main_tip(ws)
    integrated = commit(ws, "a.txt", "integrated\n")
    ledger(ws, integrated, base=base)

    def never(prompt: str) -> str:
        raise AssertionError(f"--check asked {prompt!r}")

    assert push.main(argv, root=ws, answer=never) == 0
    out = capsys.readouterr().out
    if "--json" in argv:
        assert json.loads(out)["repositories"][0]["verdict"] == "CHECK"
    else:
        assert out.splitlines()[0].startswith("CHECK root") and f"integration {integrated[:12]}" in out
    assert origin_main(ws) == base

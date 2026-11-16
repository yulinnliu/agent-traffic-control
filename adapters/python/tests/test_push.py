"""`atc push`: the Owner's publication of main to origin after checking every commit (`push.py`).

Each test runs real Git: a repository that is the workspace root (so bare `atc push` checks it as `root`) and
a bare `origin` beside it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import git_preflight as gp
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


# A main reset below a recorded candidate (design ATC-0057)

MISSING = "f" * 40  # a well-formed commit ID no repository here holds


def write_ledger(repo: Path, *lines: dict) -> None:
    path = repo / ".git" / "atc" / "integrations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def line(candidate: str, base: str | None, task: str = "ATC-0001", **extra) -> dict:
    return {"candidate_sha": candidate, "reviewed": candidate, "main_before": base, "task": task,
            "authority": {"kind": "owner_words", "quote": "Owner run: approve integration"},
            "records_only": False, "integrated_at": "2026-11-01T00:00:00+00:00", **extra}


def reset_below(ws: Path) -> tuple[str, str, str, str]:
    """origin/main at base; L1 brought c1 and c2; main reset to c1; c3 integrated on c1. Returns the four SHAs."""
    publish(ws, "main")
    base = main_tip(ws)
    c1 = commit(ws, "a.txt", "one\n")
    c2 = commit(ws, "a.txt", "two\n")
    run_git(ws, "reset", "-q", "--hard", c1)
    c3 = commit(ws, "b.txt", "three\n")
    return base, c1, c2, c3


def owners(listing: dict) -> list[tuple]:
    return [(e["sha"], e["integration"], e["candidate_on_main"], e["recorded_by"]) for e in listing["commits"]]


def test_a_commit_kept_by_a_reset_below_its_candidate_is_recorded(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base, c1, c2, c3 = reset_below(ws)
    write_ledger(ws, line(c2, base), line(c3, c1, task="ATC-0002"))
    assert push.main(["root", "--check"], root=ws) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("CHECK root")
    assert f'integration {c2[:12]} task="ATC-0001" authority=owner_words candidate off main' in out
    assert f'integration {c3[:12]} task="ATC-0002" authority=owner_words\n' in out
    assert owners(gp.push_listing(ws)) == [(c1, c2, False, "graph"), (c3, c3, True, "graph")]
    assert origin_main(ws) == base


def test_a_recorded_commit_list_needs_no_commit_object(ws: Path) -> None:
    base, c1, _c2, c3 = reset_below(ws)
    write_ledger(ws, line(MISSING, MISSING, commits=[c1, MISSING]), line(c3, c1, commits=[c3]))
    assert owners(gp.push_listing(ws)) == [(c1, MISSING, False, "line"), (c3, c3, True, "line")]


def test_a_supplement_line_records_a_line_whose_objects_are_gone(ws: Path) -> None:
    base, c1, _c2, c3 = reset_below(ws)
    write_ledger(ws, line(MISSING, base), {"kind": "commits", "of": MISSING, "main_before": base,
                                           "commits": [c1, MISSING], "recorded_at": "2026-11-16T00:00:00+00:00"},
                 line(c3, c1))
    assert owners(gp.push_listing(ws)) == [(c1, MISSING, False, "backfill"), (c3, c3, True, "graph")]
    assert [r["candidate_sha"] for r in gp.read_integrations(ws)] == [MISSING, c3]


def test_a_supplement_for_another_main_before_records_nothing(ws: Path) -> None:
    base, c1, _c2, c3 = reset_below(ws)
    write_ledger(ws, line(MISSING, base), {"kind": "commits", "of": MISSING, "main_before": c3,
                                           "commits": [c1], "recorded_at": "2026-11-16T00:00:00+00:00"},
                 line(c3, c1))
    assert owners(gp.push_listing(ws))[0] == (c1, None, None, None)


def test_an_unrecorded_commit_after_a_reset_still_refuses(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base, c1, c2, c3 = reset_below(ws)
    stray = commit(ws, "c.txt", "stray\n")
    write_ledger(ws, line(c2, base), line(c3, c1))
    assert push.main(["root"], root=ws) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("REFUSED root")
    assert f"UNRECORDED_COMMITS: root: {stray[:12]}" in out
    assert c1[:12] not in out.split("UNRECORDED_COMMITS", 1)[1]
    assert origin_main(ws) == base


@pytest.mark.parametrize("first", ["main_before missing", "no main_before", "main_before null", "candidate missing",
                                   "commits malformed"])
def test_an_off_main_line_without_both_commits_claims_nothing(ws: Path, first: str,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    base, c1, c2, c3 = reset_below(ws)
    l1 = {"main_before missing": line(c2, MISSING), "no main_before": {k: v for k, v in line(c2, base).items()
                                                                        if k != "main_before"},
          "main_before null": line(c2, None), "candidate missing": line(MISSING, base),
          "commits malformed": line(MISSING, base, commits=[c1[:12]])}[first]
    write_ledger(ws, l1, line(c3, c1))
    assert owners(gp.push_listing(ws))[0] == (c1, None, None, None)
    assert push.main(["root", "--check"], root=ws) == 1
    assert f"UNRECORDED_COMMITS: root: {c1[:12]}" in capsys.readouterr().out


def test_a_live_line_keeps_a_commit_an_off_main_line_also_records(ws: Path) -> None:
    base, c1, c2, c3 = reset_below(ws)
    write_ledger(ws, line(c2, base), line(MISSING, base, commits=[c1]), line(c3, base, task="ATC-0002"))
    assert owners(gp.push_listing(ws)) == [(c1, c3, True, "graph"), (c3, c3, True, "graph")]


def test_the_earlier_off_main_line_keeps_a_commit_two_record(ws: Path) -> None:
    base, c1, c2, c3 = reset_below(ws)
    other = "e" * 40
    write_ledger(ws, line(other, base, commits=[c1]), line(c2, base), line(c3, c1))
    assert owners(gp.push_listing(ws))[0] == (c1, other, False, "line")
    write_ledger(ws, line(c2, base), line(other, base, commits=[c1]), line(c3, c1))
    assert owners(gp.push_listing(ws))[0] == (c1, c2, False, "graph")


def test_an_off_main_binding_names_every_task(ws: Path) -> None:
    base, c1, c2, c3 = reset_below(ws)
    write_ledger(ws, line(c2, base, task="ATC-0001"), line(c2, base, task="ATC-0003"), line(c3, c1))
    entry = gp.push_listing(ws)["commits"][0]
    assert (entry["task"], entry["tasks"]) == (None, ["ATC-0001", "ATC-0003"])


def test_an_unrecorded_commit_lists_null_provenance_in_json(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish(ws, "main")
    stray = commit(ws, "a.txt", "stray\n")
    entry = gp.push_listing(ws)["commits"][0]
    assert (entry["sha"], entry["integration"], entry["candidate_on_main"], entry["recorded_by"]) == (
        stray, None, None, None)
    assert push.main(["root", "--check", "--json"], root=ws) == 1
    row = json.loads(capsys.readouterr().out)["repositories"][0]
    assert set(row) == {"name", "repo", "commits", "main_tip", "verdict", "reason", "detail", "hint"}
    assert (row["reason"], row["detail"]) == ("UNRECORDED_COMMITS", f"root: {stray[:12]}")


@pytest.mark.parametrize("of", ["absent", "a list"])
def test_a_supplement_without_a_string_of_is_skipped(ws: Path, of: str) -> None:
    base, c1, _c2, c3 = reset_below(ws)
    bad = {"kind": "commits", "main_before": base, "commits": [c1], "recorded_at": "2026-11-16T00:00:00+00:00"}
    if of == "a list":
        bad["of"] = [MISSING]
    write_ledger(ws, line(MISSING, base), bad, line(c3, c1))
    assert owners(gp.push_listing(ws))[0] == (c1, None, None, None)
    lines, unresolvable = gp.ledger_backfill(ws, gp._ledger(ws), "2026-11-16T00:00:00+00:00")
    assert ([s["of"] for s in lines], unresolvable) == ([c3], 1)


@pytest.mark.parametrize(("extra", "owner"), [
    ({"kind": "report"}, None),
    ({"kind": "commits", "candidate_sha": "d" * 40}, ("d" * 40, "line")),
])
def test_only_a_commits_line_without_a_candidate_is_a_supplement(ws: Path, extra: dict, owner: tuple | None) -> None:
    """Another kind, or a line with its own candidate, never stands in for MISSING's commits."""
    base, c1, _c2, c3 = reset_below(ws)
    other = {"of": MISSING, "main_before": base, "commits": [c1], "recorded_at": "2026-11-16T00:00:00+00:00", **extra}
    write_ledger(ws, line(MISSING, base), other, line(c3, c1))
    entry = owners(gp.push_listing(ws))[0]
    assert (entry[1], entry[3]) == (owner or (None, None))


def test_a_malformed_commit_list_falls_back_to_the_recorded_range(ws: Path) -> None:
    base, c1, c2, c3 = reset_below(ws)
    write_ledger(ws, line(c2, base, commits=[c1[:12], c2[:12]]), line(c3, c1))
    assert owners(gp.push_listing(ws))[0] == (c1, c2, False, "graph")

"""`atc push`: the Owner's publication of main to origin after checking every commit (`push.py`).

Each test runs real Git: a repository that is the workspace root (so bare `atc push` checks it as `root`) and
a bare `origin` beside it.
"""
from __future__ import annotations

import io
import json
import re
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


# Progress and the streamed push (ATC-0061)

def integrated_main(ws: Path) -> str:
    """origin/main at a base and main one recorded integration ahead; the integration's SHA."""
    publish(ws, "main")
    integrated = commit(ws, "a.txt", "integrated\n")
    ledger(ws, integrated, base=run_git(ws, "rev-parse", "origin/main"))
    return integrated


def pre_push(ws: Path, body: str) -> None:
    hook = ws / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
    hook.chmod(0o755)


def test_a_push_reports_each_step_with_its_time_on_stderr(ws: Path, capsys: pytest.CaptureFixture[str],
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    integrated = integrated_main(ws)
    pre_push(ws, "echo 'gate: PASS'\n")
    fetches = []
    real_fetch = gp.fetch_origin
    monkeypatch.setattr(gp, "fetch_origin", lambda repo: fetches.append(repo) or real_fetch(repo))
    assert push.main(["root"], root=ws) == 0
    captured = capsys.readouterr()
    err = captured.err.splitlines()
    steps = [line for line in err if line.startswith("STEP ")]
    assert [re.sub(r" \d+\.\ds ", " Ts ", line) for line in steps] == [
        "STEP fetch root Ts OK",
        "STEP ledger root Ts 1 commit in origin/main..main, 1 recorded",
        f"STEP gate root Ts START {integrated[:12]}",
        "STEP gate root Ts OK",
        f"STEP push root Ts START {integrated[:12]} -> origin/main",
        "STEP push root Ts OK",
        f"STEP read-back root Ts origin/main {integrated[:12]}",
    ]
    # The hook ran once, in the gate step before git connected; the push skipped it.
    assert err.index("gate: PASS") == err.index(steps[2]) + 1 and err.count("gate: PASS") == 1
    assert steps[4].startswith("STEP push root 0.0s START ")
    assert captured.out.splitlines()[0] == f"PUSHED root {integrated[:7]} (1 commit)"
    assert "gate: PASS" not in captured.out and not any(line in captured.out for line in steps)
    # The push moved origin/main's remote-tracking ref itself: one fetch, before the push.
    assert len(fetches) == 1
    assert run_git(ws, "rev-parse", "refs/remotes/origin/main") == integrated


def test_a_failing_pre_push_hook_streams_once_and_stops_before_git_connects(
        ws: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    integrated_main(ws)
    base = run_git(ws, "rev-parse", "origin/main")
    pre_push(ws, "i=1; while [ $i -le 30 ]; do echo \"gate: line $i\"; i=$((i+1)); done\nexit 1\n")
    pushes = []
    monkeypatch.setattr(push, "publish", lambda *args: pushes.append(args))
    assert push.main(["root"], root=ws) == 2
    captured = capsys.readouterr()
    assert "gate: line 1\n" in captured.err and "gate: line 30\n" in captured.err  # streamed whole
    assert re.search(r"^STEP gate root \d+\.\ds FAILED exit 1$", captured.err, re.M)
    refusal = captured.out[captured.out.index("GATE_FAILED"):]
    assert "gate: line" not in refusal and "the pre-push hook exited 1" in refusal
    assert pushes == [] and origin_main(ws) == base


def test_the_gate_gives_the_hook_what_git_push_gives_it(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """githooks(5) § pre-push: the remote's name and URL as arguments, one line per pushed ref on stdin."""
    integrated = integrated_main(ws)
    base = run_git(ws, "rev-parse", "origin/main")
    url = run_git(ws, "remote", "get-url", "--push", "origin")
    pre_push(ws, 'echo "args $1 $2"\nwhile read line; do echo "stdin $line"; done\n')
    assert push.main(["root"], root=ws) == 0
    err = capsys.readouterr().err.splitlines()
    assert f"args origin {url}" in err
    assert f"stdin refs/heads/main {integrated} refs/heads/main {base}" in err


def test_a_failed_push_after_a_passed_gate_names_gits_own_message(
        ws: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    integrated_main(ws)
    monkeypatch.setattr(gp, "git_streamed", lambda repo, *args, out, extra_env=None: (
        (0, "") if args[0] == "hook" else (141, "Connection to github.com closed by remote host.\n")))
    assert push.main(["root"], root=ws) == 2
    captured = capsys.readouterr()
    assert re.search(r"^STEP push root \d+\.\ds FAILED exit 141$", captured.err, re.M)
    refusal = captured.out[captured.out.index("PUSH_FAILED"):]
    assert "git push exited 141 (SIGPIPE" in refusal and "Connection to github.com closed" in refusal


def test_a_push_failure_names_the_exit_and_gits_and_the_transports_lines_once() -> None:
    output = ("gate: tests RUN\nConnection to github.com closed by remote host.\n=== 3 passed ===\n"
              "Connection to github.com closed by remote host.\n")
    assert push.push_failure(141, output).splitlines() == [
        f"git push exited 141 ({push.EXIT_MEANINGS[141]})", "Connection to github.com closed by remote host."]
    assert push.push_failure(128, "fatal: unable to access\n") == "git push exited 128\nfatal: unable to access"


def test_the_pre_push_hook_learns_the_project_version_and_tasks_of_what_it_pushes(
        ws: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/TESTKIT.md § Stage times: the gate's records name the tasks whose integrations the push carries."""
    monkeypatch.setenv("ATC_TASK", "inherited")
    publish(ws, "main")
    base = run_git(ws, "rev-parse", "origin/main")
    first = commit(ws, "a.txt", "first\n")
    second = commit(ws, "b.txt", "second\n")
    path = ws / ".git" / "atc" / "integrations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"candidate_sha": sha, "reviewed": sha, "main_before": before, "task": task,
             "authority": {"kind": "owner_words", "quote": "x"},
             "authority_reference": f"Owner run: approve integration @ {sha}",
             "integrated_at": "2026-11-01T00:00:00+00:00"}
            for sha, before, task in ((first, base, "ATC-0007"), (second, first, "ATC-0008"))]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    pre_push(ws, 'echo "inputs $ATC_PROJECT $ATC_TASK $ATC_VERSION"\n')
    assert push.main(["root"], root=ws) == 0
    from atc_cli import package_version
    assert f"inputs root ATC-0007,ATC-0008 {package_version()}" in capsys.readouterr().err.splitlines()


def test_json_keeps_progress_and_hook_output_off_stdout(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    integrated = integrated_main(ws)
    pre_push(ws, "echo 'gate: PASS'\n")
    assert push.main(["root", "--json"], root=ws) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["repositories"][0]["verdict"] == "PUSHED"
    assert "gate: PASS" in captured.err and re.search(r"^STEP fetch root \d+\.\ds OK$", captured.err, re.M)
    assert origin_main(ws) == integrated


@pytest.mark.parametrize("reply, verdict", [("n", "REFUSED"), (None, "REFUSED"), ("y", "PUSHED")])
def test_all_with_json_asks_on_stderr_and_pushes_only_on_a_yes(ws: Path, reply: str | None, verdict: str,
                                                               capsys: pytest.CaptureFixture[str],
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """`--all` confirms every repository under `--json` too; the question goes through the real `input` to stderr,
    and no answer (end of input) is a no."""
    integrated = integrated_main(ws)
    base = run_git(ws, "rev-parse", "origin/main")
    monkeypatch.setattr("sys.stdin", io.StringIO("" if reply is None else reply + "\n"))
    assert push.main(["--all", "--json"], root=ws) == (0 if reply == "y" else 1)
    captured = capsys.readouterr()
    row = json.loads(captured.out)["repositories"][0]
    assert row["verdict"] == verdict and (reply == "y" or row["reason"] == "DECLINED")
    assert "push root? [y/N] " in captured.err and f"integration {integrated[:12]}" in captured.err
    assert origin_main(ws) == (integrated if reply == "y" else base)


def test_git_output_reaches_the_terminal_while_git_still_runs(ws: Path, tmp_path: Path) -> None:
    """The hook waits for a file that only the copy of its first line creates: output buffered until git exits
    would time the hook out."""
    integrated = integrated_main(ws)
    flag = tmp_path / "released"
    pre_push(ws, f"echo 'gate: waiting'\ni=0\nwhile [ ! -f '{flag.as_posix()}' ] && [ $i -lt 300 ]; do "
                 "sleep 0.1; i=$((i+1)); done\n"
                 f"[ -f '{flag.as_posix()}' ] && echo 'gate: released'\n")

    class Terminal(io.StringIO):
        def write(self, text: str) -> int:
            if "gate: waiting" in self.getvalue() + text:
                flag.touch()
            return super().write(text)

    out = Terminal()
    code, output = gp.git_streamed(ws, "push", "origin", f"{integrated}:refs/heads/main", out=out)
    assert code == 0 and "gate: released" in out.getvalue() and output == out.getvalue()


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

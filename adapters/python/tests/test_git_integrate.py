from __future__ import annotations

import json
import re
import sys
import os
import subprocess
import time
from pathlib import Path

import pytest

from atc_testkit import locations
import git_integrate as gi

CANDIDATE_PATH_BOUND = 64


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def commit(repo: Path, path: str, content: str) -> str:
    (repo / path).write_text(content, encoding="utf-8")
    run_git(repo, "add", "--", path)
    run_git(repo, "commit", "-q", "-m", f"edit {path}")
    return run_git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The command's own git calls inherit this environment, so the merge
    # commit gets a fixed identity and no user or system config applies.
    for key in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(key)
    for key, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    }.items():
        monkeypatch.setenv(key, value)
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit(root, "a.txt", "a\n")
    commit(root, "b.txt", "b\n")
    declare_verifier(root, verify_command())
    return root


def declare_verifier(repo: Path, argv: str, timeout: str | None = None) -> str:
    """Commit the verification section and argv file `atc integrate` reads from main."""
    (repo / "verify.json").write_text(argv, encoding="utf-8")
    run_git(repo, "add", "--", "verify.json")
    lines = "- Verifier argv file: verify.json\n" + (f"- Verifier timeout: {timeout}\n" if timeout else "")
    return commit(repo, "AGENTS.md", f"# AGENTS.md\n\n## Integration verification\n\n{lines}\n## Other\n")


def topic(repo: Path, edits: dict[str, str], branch: str = "topic") -> str:
    run_git(repo, "switch", "-q", "-c", branch)
    for path, content in edits.items():
        commit(repo, path, content)
    tip = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "switch", "-q", "main")
    return tip


def main_tip(repo: Path) -> str:
    return run_git(repo, "rev-parse", "main")


def add_origin(repo: Path) -> Path:
    """A bare `origin` beside the repository, added once."""
    origin = repo.parent / "origin.git"
    if not origin.exists():
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], capture_output=True, check=True)
        run_git(repo, "remote", "add", "origin", str(origin))
    return origin


def publish(repo: Path, *refs: str) -> None:
    """Push ``refs`` to origin, so a topic branch there is at its reviewed SHA."""
    add_origin(repo)
    run_git(repo, "push", "-q", "origin", *refs)


def verify_command() -> str:
    script = (
        "import os,subprocess; "
        "assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==os.environ['ATC_INTEGRATION_SHA']; "
        "assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip()"
    )
    return json.dumps([sys.executable, "-c", script])


def refusal(err: str) -> tuple[str, str]:
    """The refusal line of stderr and the line after it."""
    lines = err.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith(("STOP ", "ERROR ")))
    return lines[at], (lines[at + 1] if at + 1 < len(lines) else "")


def integrate(repo: Path, branch: str, reviewed: str, *extra: str) -> int:
    return gi.main([
        "--repo", str(repo), "--branch", branch, "--reviewed", reviewed,
        "--authority-reference", "test-owner-authorization", *extra
    ])


def test_fast_forward_advances_main_to_reviewed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip) == 0
    assert main_tip(repo) == tip
    line = capsys.readouterr().out.strip()
    assert line.startswith(f'FF repo main {before[:12]} -> {tip[:12]} task="test-owner-authorization" authority=unclassified log ')
    assert run_git(repo, "status", "--porcelain") == ""


def test_no_ff_merges_with_subject_and_parents(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"}, branch="worktree-claude+demo-topic")
    publish(repo, "worktree-claude+demo-topic")
    before = commit(repo, "c.txt", "c\n")
    assert integrate(repo, "worktree-claude+demo-topic", tip) == 0
    after = main_tip(repo)
    assert run_git(repo, "rev-parse", "main^1", "main^2").split() == [before, tip]
    assert run_git(repo, "log", "-1", "--format=%s", "main") == "merge(demo-topic): integrate reviewed changes"
    assert capsys.readouterr().out.strip().startswith(f"NO_FF repo main {before[:12]} -> {after[:12]} task=")


@pytest.mark.parametrize(("branch", "expected"), [
    ("worktree-claude+demo-topic", "demo-topic"),
    ("topic/demo-topic", "demo-topic"),
    ("task/demo-topic", "demo-topic"),
    ("demo-topic", "demo-topic"),
    ("feature/demo", "feature/demo"),
])
def test_topic_name(branch: str, expected: str) -> None:
    assert gi.topic_name(branch) == expected


@pytest.mark.parametrize("branch", ["topic/x-y", "task/x-y"])
def test_no_ff_subject_strips_prefix(repo: Path, branch: str) -> None:
    tip = topic(repo, {"a.txt": "a2\n"}, branch=branch)
    publish(repo, branch)
    commit(repo, "c.txt", "c\n")
    assert integrate(repo, branch, tip) == 0
    assert run_git(repo, "log", "-1", "--format=%s", "main") == "merge(x-y): integrate reviewed changes"


def stop_unknown(repo: Path) -> str:
    topic(repo, {"a.txt": "a2\n"})
    return "0123456789abcdef0123456789abcdef01234567"


def stop_rewritten(repo: Path) -> str:
    reviewed = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "switch", "-q", "topic")
    run_git(repo, "commit", "-q", "--amend", "-m", "reworded")
    run_git(repo, "switch", "-q", "main")
    return reviewed


def stop_unreviewed(repo: Path) -> str:
    reviewed = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "switch", "-q", "topic")
    commit(repo, "a.txt", "a3\n")
    run_git(repo, "switch", "-q", "main")
    return reviewed


def stop_already_merged(repo: Path) -> str:
    tip = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "merge", "-q", "--ff-only", tip)
    return tip


def stop_not_on_main(repo: Path) -> str:
    tip = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "switch", "-q", "-c", "elsewhere")
    return tip


def stop_dirty(repo: Path) -> str:
    tip = topic(repo, {"a.txt": "a2\n"})
    (repo / "b.txt").write_text("dirty\n", encoding="utf-8")
    return tip


def stop_conflict(repo: Path) -> str:
    tip = topic(repo, {"a.txt": "topic\n"})
    commit(repo, "a.txt", "main\n")
    return tip


def stop_overlap(repo: Path) -> str:
    commit(repo, "a.txt", "1\n2\n3\n4\n5\n6\n")
    tip = topic(repo, {"a.txt": "one\n2\n3\n4\n5\n6\n"})
    commit(repo, "a.txt", "1\n2\n3\n4\n5\nsix\n")
    return tip


@pytest.mark.parametrize(("reason", "setup"), [
    ("REVIEWED_SHA_UNKNOWN", stop_unknown),
    ("REVIEW_REWRITTEN", stop_rewritten),
    ("UNREVIEWED_COMMITS", stop_unreviewed),
    ("ALREADY_MERGED", stop_already_merged),
    ("CHECKOUT_NOT_ON_MAIN", stop_not_on_main),
    ("CHECKOUT_DIRTY", stop_dirty),
    ("CONFLICT", stop_conflict),
    ("OVERLAP", stop_overlap),
])
def test_preflight_stop_leaves_main_unchanged(repo: Path, capsys: pytest.CaptureFixture[str], reason: str, setup) -> None:
    reviewed = setup(repo)
    before = main_tip(repo)
    head = run_git(repo, "rev-parse", "HEAD")
    status = run_git(repo, "status", "--porcelain")
    assert integrate(repo, "topic", reviewed) == 1
    first, after = refusal(capsys.readouterr().err)
    assert first.startswith(f"STOP {reason}: ")
    assert after.startswith("next: ")
    assert (main_tip(repo), run_git(repo, "rev-parse", "HEAD"), run_git(repo, "status", "--porcelain")) == (before, head, status)
    assert not (repo / ".git" / "MERGE_HEAD").exists()


@pytest.mark.integration
def test_a_reviewed_prefix_under_twelve_characters_stops(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    assert integrate(repo, "topic", tip[:11]) == 1
    first, after = refusal(capsys.readouterr().err)
    assert first.startswith("STOP REVIEWED_SHA_INVALID: --reviewed must be the full SHA or a unique prefix of 12 or more")
    assert after == "next: run again with the full SHA the formal review named"
    assert main_tip(repo) == before


@pytest.mark.integration
def test_a_twelve_character_prefix_integrates_and_the_record_holds_the_full_sha(
        repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip[:12].upper()) == 0
    assert main_tip(repo) == tip
    record = json.loads((repo / ".git" / "atc" / "integrations.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["reviewed"] == tip


@pytest.mark.integration
def test_an_unknown_prefix_stops(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", "0" * 12) == 1
    assert refusal(capsys.readouterr().err)[0] == (
        "STOP REVIEWED_SHA_UNKNOWN: the reviewed SHA does not resolve to a commit in this repository: 000000000000")


def colliding_commits(repo: Path, length: int) -> tuple[str, str]:
    """Write two commit objects whose IDs share their first ``length`` characters."""
    import hashlib

    tree = run_git(repo, "rev-parse", "main^{tree}")
    seen: dict[str, bytes] = {}
    for n in range(1 << 20):
        body = f"tree {tree}\nauthor t <t@example.com> {n} +0000\ncommitter t <t@example.com> {n} +0000\n\nc\n".encode()
        sha = hashlib.sha1(b"commit %d\x00" % len(body) + body).hexdigest()
        if sha[:length] in seen:
            ids = []
            for content in (seen[sha[:length]], body):
                written = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
                                         input=content, capture_output=True, check=True)
                ids.append(written.stdout.decode().strip())
            return ids[0], ids[1]
        seen[sha[:length]] = body
    raise AssertionError("no collision")


@pytest.mark.integration
def test_an_ambiguous_prefix_stops_and_lists_the_matches(
        repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # Git abbreviates no shorter than four characters, so the floor drops to four.
    monkeypatch.setattr(gi, "MIN_PREFIX", 4)
    topic(repo, {"a.txt": "a2\n"})
    first_id, second_id = colliding_commits(repo, 4)
    before = main_tip(repo)
    assert integrate(repo, "topic", first_id[:4]) == 1
    line, after = refusal(capsys.readouterr().err)
    assert line.startswith(f"STOP AMBIGUOUS_SHA: --reviewed {first_id[:4]} matches ")
    shown = line.rsplit(": ", 1)[1].split(", ")
    for full in (first_id, second_id):
        assert [cut for cut in shown if full.startswith(cut) and 4 < len(cut) < 40] != []
    assert after == "next: run again with a longer prefix or the full SHA the formal review named"
    assert integrate(repo, "topic", first_id[:4], "--json") == 1
    matches = json.loads(capsys.readouterr().out)["refusal"]["evidence"]["matches"]
    assert {first_id, second_id} <= set(matches) and len(shown) == len(matches)
    assert main_tip(repo) == before


@pytest.mark.integration
def test_preflight_merge_runs_integrate_check(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import git_preflight as gp

    tip = topic(repo, {"a.txt": "a2\n"})
    assert gp.main(["merge", "--repo", str(repo), "--branch", "topic", "--reviewed", tip]) == 0
    captured = capsys.readouterr()
    assert captured.err.splitlines()[0] == (
        f"NOTE atc preflight merge is retired; this ran atc integrate --check --repo {repo} --reviewed {tip}")
    assert captured.out.startswith(f"CHECK FF FAST_FORWARD: repo branch=topic tip={tip[:12]} ")
    assert gp.main(["merge", "--repo", str(repo), "--branch", "topic", "--reviewed", tip[:12], "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["command"], data["check"], data["preflight"]["verdict"]) == ("integrate", True, "FF")
    (repo / "b.txt").write_text("dirty\n", encoding="utf-8")
    assert gp.main(["merge", "--repo", str(repo), "--branch", "topic", "--reviewed", tip]) == 1
    assert refusal(capsys.readouterr().err)[0].startswith("STOP CHECKOUT_DIRTY: ")
    assert main_tip(repo) != tip


@pytest.mark.integration
@pytest.mark.parametrize("extra, given", [((), "no --reviewed"), (("--reviewed", "f" * 40, "--main", "trunk"), "--main trunk")])
def test_preflight_merge_refuses_what_integrate_check_does_not_take(
        repo: Path, capsys: pytest.CaptureFixture[str], extra: tuple[str, ...], given: str) -> None:
    import git_preflight as gp

    assert gp.main(["merge", "--repo", str(repo), "--branch", "topic", *extra]) == 2
    err = capsys.readouterr().err
    assert err.startswith("ERROR USAGE atc preflight merge is retired and runs atc integrate --check")
    assert err.rstrip().endswith(given)


@pytest.mark.integration
def test_preflight_cleanup_is_gone(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import git_preflight as gp

    with pytest.raises(SystemExit) as exited:
        gp.main(["cleanup", "--repo", str(repo), "--worktree", str(repo)])
    assert exited.value.code == 2
    assert "invalid choice: 'cleanup'" in capsys.readouterr().err


@pytest.mark.integration
def test_an_uppercase_reviewed_sha_is_lowered(repo: Path) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip.upper()) == 0
    assert main_tip(repo) == tip


def test_linked_worktree_is_refused(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    wt = tmp_path / "wt"
    run_git(repo, "worktree", "add", "-q", str(wt), "-b", "side", "main")
    before = main_tip(repo)
    assert integrate(wt, "topic", tip) == 2
    assert "linked worktree" in capsys.readouterr().err
    assert main_tip(repo) == before


def test_subdirectory_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    (repo / "sub").mkdir()
    assert integrate(repo / "sub", "topic", tip) == 2
    assert "top of the checkout" in capsys.readouterr().err


def test_main_moved_after_preflight_stops(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    real = gi.merge_preflight
    moved = []

    def preflight_then_advance(*args):
        report = real(*args)
        moved.append(commit(repo, "c.txt", "c\n"))
        return report

    monkeypatch.setattr(gi, "merge_preflight", preflight_then_advance)
    assert integrate(repo, "topic", tip) == 1
    assert "moved after preflight" in capsys.readouterr().err
    assert main_tip(repo) == moved[0]


def test_crash_after_main_update_is_reconciled_without_reset(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    real_git = gi.git
    crashed = []

    def merge_then_crash(repo_arg, *args, **kwargs):
        result = real_git(repo_arg, *args, **kwargs)
        if args[:2] == ("merge", "--ff-only"):
            crashed.append(args[2])
            raise SystemExit(77)
        return result

    monkeypatch.setattr(gi, "git", merge_then_crash)
    with pytest.raises(SystemExit, match="77"):
        integrate(repo, "topic", tip)
    assert main_tip(repo) == tip
    assert run_git(repo, "rev-parse", "HEAD") == tip
    assert run_git(repo, "status", "--porcelain") == ""
    refs = run_git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/atc/integration-candidates")
    assert f"refs/atc/integration-candidates/{tip} {tip}" in refs

    monkeypatch.setattr(gi, "git", real_git)
    assert integrate(repo, "topic", tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "RECOVERED"
    assert data["recovery_status"] == "CONSISTENT"
    assert data["main_after"] == tip
    assert main_tip(repo) == tip
    assert run_git(repo, "status", "--porcelain") == ""
    assert run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atc/integration-candidates") == ""



def test_recovery_ref_from_other_request_does_not_short_circuit_integration(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first_tip = topic(repo, {"a.txt": "first\n"}, branch="topic-one")
    real_git = gi.git

    def merge_then_crash(repo_arg, *args, **kwargs):
        result = real_git(repo_arg, *args, **kwargs)
        if args[:2] == ("merge", "--ff-only"):
            raise SystemExit(77)
        return result

    monkeypatch.setattr(gi, "git", merge_then_crash)
    with pytest.raises(SystemExit, match="77"):
        integrate(repo, "topic-one", first_tip)
    monkeypatch.setattr(gi, "git", real_git)
    assert main_tip(repo) == first_tip

    # A different reviewed request must not inherit the first request's
    # RECOVERED success. Its candidate is based on the now-advanced main.
    run_git(repo, "switch", "-q", "-c", "topic-two", "main")
    second_tip = commit(repo, "b.txt", "second\n")
    run_git(repo, "switch", "-q", "main")
    assert integrate(repo, "topic-two", second_tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "FF"
    assert data["reviewed"] == second_tip
    assert data["main_after"] == second_tip
    assert data["recovered_candidates"][0]["candidate_sha"] == first_tip
    assert main_tip(repo) == second_tip


def test_stale_candidate_ref_is_reported_without_automatic_deletion(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale_sha = main_tip(repo)
    commit(repo, "main-only.txt", "advance main beyond stale candidate\n")
    stale_ref = f"refs/atc/integration-candidates/{stale_sha}"
    run_git(repo, "update-ref", stale_ref, stale_sha)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["main_after"] == tip
    assert data["stale_candidate_refs"] == [{"ref": stale_ref, "sha": stale_sha}]
    assert run_git(repo, "rev-parse", stale_ref) == stale_sha

def test_untracked_file_does_not_make_checkout_dirty_for_integration(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    (repo / "local-notes.txt").write_text("keep me\n", encoding="utf-8")
    assert integrate(repo, "topic", tip) == 0
    assert main_tip(repo) == tip
    assert (repo / "local-notes.txt").read_text(encoding="utf-8") == "keep me\n"
    assert "integration checkout became dirty" not in capsys.readouterr().err


def test_verifier_timeout_is_reported_and_main_stays_unchanged(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    declare_verifier(repo, json.dumps([sys.executable, "-c", "import time; time.sleep(2)"]))
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    assert integrate(repo, "topic", tip, "--verify-timeout", "0.05") == 3
    err = capsys.readouterr().err
    assert "candidate verification timed out" in err and "test budget exceeded" in err
    assert main_tip(repo) == before


def test_missing_verifier_is_reported_without_traceback(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    declare_verifier(repo, json.dumps(["atc-verifier-command-that-does-not-exist"]))
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    assert integrate(repo, "topic", tip) == 2
    error = capsys.readouterr().err
    assert refusal(error)[0] == "ERROR VERIFIER_NOT_FOUND the verifier executable atc-verifier-command-that-does-not-exist is not on PATH"
    assert "Traceback" not in error
    assert main_tip(repo) == before
    assert run_git(repo, "worktree", "list").count("\n") == 0


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False,
        ).stdout
        return str(pid) in listing.split()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def descendant_verifier(pid_file: Path, *, reparent: bool, wrapper_exits: bool) -> str:
    """A verifier whose deepest descendant records its pid and then sleeps.

    reparent: an intermediate process starts the sleeper and exits, so the
    sleeper's parent pid no longer names a live process.
    wrapper_exits: the verifier itself returns 0 right away instead of sleeping.
    """
    sleeper = "import os,time,sys; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)"
    spawn_sleeper = f"subprocess.Popen([sys.executable,'-c',{sleeper!r},{str(pid_file)!r}])"
    if reparent:
        middle = f"import subprocess,sys; {spawn_sleeper}"
        spawn = f"subprocess.run([sys.executable,'-c',{middle!r}])"
    else:
        spawn = spawn_sleeper
    # The verifier only ends after the sleeper has recorded its pid, so the
    # test observes a descendant that was alive when the verifier returned.
    wait_for_sleeper = f"\nwhile not os.path.exists({str(pid_file)!r}): time.sleep(0.05)\n"
    tail = "" if wrapper_exits else "time.sleep(60)"
    return json.dumps([sys.executable, "-c", f"import os,subprocess,sys,time; {spawn}{wait_for_sleeper}{tail}"])


def wait_for_pid(pid_file: Path) -> int:
    deadline = time.monotonic() + 5
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    return int(pid_file.read_text())


def assert_pid_ends(pid: int) -> None:
    deadline = time.monotonic() + 5
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not pid_alive(pid), "verifier descendant survived"


@pytest.mark.parametrize("reparent", [False, True], ids=["direct-child", "reparented"])
def test_verifier_timeout_kills_descendant_processes(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], reparent: bool
) -> None:
    pid_file = tmp_path / "sleeper.pid"
    declare_verifier(repo, descendant_verifier(pid_file, reparent=reparent, wrapper_exits=False))
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    started = time.monotonic()
    assert integrate(repo, "topic", tip, "--verify-timeout", "1") == 3
    elapsed = time.monotonic() - started
    assert "candidate verification timed out" in capsys.readouterr().err
    assert elapsed < 20, f"timeout was not enforced while a descendant held the verifier open ({elapsed:.1f}s)"
    assert main_tip(repo) == before
    assert_pid_ends(wait_for_pid(pid_file))


def test_verifier_descendants_end_when_verifier_returns(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file = tmp_path / "sleeper.pid"
    declare_verifier(repo, descendant_verifier(pid_file, reparent=True, wrapper_exits=True))
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip) == 0
    assert main_tip(repo) == tip
    assert_pid_ends(wait_for_pid(pid_file))
    assert "candidate worktree retained" not in capsys.readouterr().err


SEEN_BY_THE_VERIFIER = (
    "import json, os, sys, tempfile; "
    "print(json.dumps({'cwd': os.getcwd(), 'report': os.environ['ATC_VERIFICATION_REPORT'], "
    "'tmp': os.environ.get('ATC_TMP'), 'temp': tempfile.gettempdir()})); sys.exit(1)"
)


def verifier_view(repo: Path, capsys: pytest.CaptureFixture[str]) -> dict:
    """What a verifier that prints its locations and refuses saw of an integration of a new topic."""
    declare_verifier(repo, json.dumps([sys.executable, "-c", SEEN_BY_THE_VERIFIER]))
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate_with(repo, "topic", tip) == 3
    err = capsys.readouterr().err
    return json.loads(next(line.strip() for line in err.splitlines() if line.strip().startswith('{"cwd"')))


@pytest.mark.integration
def test_the_candidate_and_its_report_lie_in_candidates_under_a_twelve_digit_id(
    repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    seen = verifier_view(repo, capsys)
    tree, report = Path(seen["cwd"]), Path(seen["report"])
    assert tree.parent == atc_tmp / "candidates" and re.fullmatch(r"[0-9a-f]{12}", tree.name)
    assert report == tree.with_name(tree.name + ".report.json")
    assert seen["tmp"] == str(atc_tmp) and Path(seen["temp"]) == atc_tmp / "systmp"
    assert list((atc_tmp / "candidates").iterdir()) == [tree.with_name(tree.name + ".verify.log")]


@pytest.mark.integration
def test_the_candidate_path_keeps_the_bound_at_the_longest_root(
    repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    room = locations.TMP_NAME_BOUND - len(str(atc_tmp)) - 1
    if room < 1:
        pytest.skip(f"the home directory leaves {atc_tmp} no room below the bound")
    longest = atc_tmp / ("r" * room)
    longest.mkdir()
    monkeypatch.setenv("ATC_TMP", str(longest))
    assert len(str(longest)) == locations.TMP_NAME_BOUND
    tree = Path(verifier_view(repo, capsys)["cwd"])
    assert len(str(tree)) == CANDIDATE_PATH_BOUND
    assert not tree.exists()


@pytest.mark.integration
def test_the_verifier_output_is_captured_under_systmp(
    repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = []
    temporary_file = gi.tempfile.TemporaryFile

    def recording(*args, **kwargs):
        created.append(kwargs.get("dir"))
        return temporary_file(*args, **kwargs)
    monkeypatch.setattr(gi.tempfile, "TemporaryFile", recording)
    verifier_view(repo, capsys)
    assert created == [atc_tmp / "systmp"] * 2


@pytest.mark.integration
def test_under_ci_the_candidate_keeps_candidates_below_the_platform_directory(
    repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_directory = atc_tmp / "platform"
    platform_directory.mkdir()
    monkeypatch.setenv("CI", "1")
    monkeypatch.delenv("ATC_TMP")
    monkeypatch.setattr(gi.locations.tempfile, "tempdir", str(platform_directory))
    assert Path(verifier_view(repo, capsys)["cwd"]).parent == platform_directory / "candidates"


@pytest.mark.integration
@pytest.mark.parametrize("value", ["relative", "file"])
def test_an_unsuitable_root_exits_nine_before_anything_is_created(
    repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    if value == "file":
        (atc_tmp / "file").write_text("x", encoding="utf-8")
        value = str(atc_tmp / "file")
    monkeypatch.setenv("ATC_TMP", value)
    before = sorted(atc_tmp.iterdir())
    assert integrate(repo, "topic", tip) == 9
    assert capsys.readouterr().err.splitlines()[-1].startswith(locations.UNSUITABLE)
    assert sorted(atc_tmp.iterdir()) == before and main_tip(repo) != tip


@pytest.mark.integration
def test_a_candidates_directory_that_cannot_be_created_exits_nine(
    repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    mkdir = Path.mkdir

    def refusing(self: Path, *args, **kwargs) -> None:
        if self == atc_tmp / "candidates":
            raise PermissionError("simulated")
        mkdir(self, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", refusing)
    assert integrate(repo, "topic", tip) == 9
    assert capsys.readouterr().err.splitlines()[-1].startswith(locations.UNSUITABLE + " cannot be created")


@pytest.mark.integration
def test_a_linked_candidates_directory_exits_nine(
    repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    target = tmp_path / "elsewhere"
    target.mkdir()
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(atc_tmp / "candidates"), str(target)], check=True, capture_output=True)
    else:
        (atc_tmp / "candidates").symlink_to(target, target_is_directory=True)
    assert integrate(repo, "topic", tip) == 9
    assert capsys.readouterr().err.splitlines()[-1].startswith(locations.UNSUITABLE) and list(target.iterdir()) == []


def test_text_mode_warns_when_candidate_worktree_is_retained(
    repo: Path, atc_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    real_git = gi.git

    def refuse_worktree_remove(repo_arg, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(args, 1, "", "simulated: directory in use")
        return real_git(repo_arg, *args, **kwargs)

    monkeypatch.setattr(gi, "git", refuse_worktree_remove)
    assert integrate(repo, "topic", tip) == 0
    captured = capsys.readouterr()
    assert main_tip(repo) == tip
    assert "WARNING candidate worktree retained at" in captured.err
    assert "simulated: directory in use" in captured.err
    assert str(atc_tmp / "candidates") in captured.err


@pytest.mark.skipif(sys.platform != "win32", reason="the Job Object fallback exists only on Windows")
def test_job_object_fallback_is_reported(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(gi, "_create_kill_on_close_job", lambda: None)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "FF"
    assert "not isolated by a Job Object (job creation failed)" in data["verification_warning"]

    tip2 = topic(repo, {"b.txt": "b2\n"}, branch="topic2")
    assert integrate(repo, "topic2", tip2) == 0
    assert "WARNING verifier process tree is not isolated by a Job Object" in capsys.readouterr().err


@pytest.mark.integration
def test_declared_argv_file_with_bom_is_accepted(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # PowerShell 5.1's Set-Content -Encoding utf8 and Out-File write a BOM.
    (repo / "verify.json").write_bytes(b"\xef\xbb\xbf" + verify_command().encode("utf-8"))
    run_git(repo, "add", "--", "verify.json")
    run_git(repo, "commit", "-q", "-m", "argv with a BOM")
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip) == 0
    assert main_tip(repo) == tip


@pytest.mark.integration
def test_empty_declared_argv_file_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_verifier(repo, "\n")
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    assert integrate(repo, "topic", tip) == 2
    first, _ = refusal(capsys.readouterr().err)
    assert first.startswith("ERROR VERIFIER_ARGV_INVALID AGENTS.md declares the verifier argv file verify.json")
    assert main_tip(repo) == before


@pytest.mark.integration
def test_malformed_declared_argv_is_refused_before_any_candidate_work(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # PowerShell 5.1 hands a native command '[python,-c,pass]' for '["python","-c","pass"]'.
    declare_verifier(repo, "[python,-c,pass]")
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    assert integrate(repo, "topic", tip) == 2
    assert "must be a JSON array" in capsys.readouterr().err
    assert main_tip(repo) == before
    assert run_git(repo, "worktree", "list").count("\n") == 0


@pytest.mark.integration
@pytest.mark.parametrize("flag", ["--verify-command", "--verify-command-file"])
def test_the_verifier_flags_are_unknown_options(repo: Path, capsys: pytest.CaptureFixture[str], flag: str) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    base = ["--repo", str(repo), "--branch", "topic", "--reviewed", tip, "--authority-reference", "x"]
    assert gi.main([*base, flag, verify_command()]) == 2
    err = capsys.readouterr().err
    assert err.startswith("ERROR USAGE ")
    assert len(err.splitlines()) == 1 and flag in err
    assert main_tip(repo) != tip
    assert gi.main(base) == 0
    assert main_tip(repo) == tip


def test_other_request_keeps_recovery_ref_while_checkout_needs_reconciliation(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first_tip = topic(repo, {"a.txt": "first\n"}, branch="topic-one")
    second_tip = topic(repo, {"b.txt": "second\n"}, branch="topic-two")
    real_git = gi.git

    def dirty_after_ff(repo_arg, *args, **kwargs):
        result = real_git(repo_arg, *args, **kwargs)
        if args[:2] == ("merge", "--ff-only"):
            (repo / "a.txt").write_text("unexpected checkout mutation\n", encoding="utf-8")
        return result

    monkeypatch.setattr(gi, "git", dirty_after_ff)
    assert integrate(repo, "topic-one", first_tip, "--json") == 3
    monkeypatch.setattr(gi, "git", real_git)
    recovery_ref = json.loads(capsys.readouterr().out)["candidate_ref"]
    assert run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atc/integration-candidates") == recovery_ref

    assert integrate(repo, "topic-two", second_tip) == 1
    captured = capsys.readouterr()
    assert refusal(captured.err)[0].startswith("STOP CHECKOUT_DIRTY: ")
    assert "main_retains_commit_ref_retained_checkout_inconsistent" in captured.err
    assert run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atc/integration-candidates") == recovery_ref
    assert main_tip(repo) == first_tip


def test_candidate_verification_failure_leaves_main_unchanged(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_verifier(repo, json.dumps([sys.executable, "-c", "import sys; print('verifier diagnostic: 3 failed', file=sys.stderr); raise SystemExit(9)"]))
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    assert integrate(repo, "topic", tip) == 3
    error = capsys.readouterr().err
    assert "candidate verification failed" in error
    assert "(exit 9): verifier diagnostic: 3 failed" in error
    assert main_tip(repo) == before
    assert run_git(repo, "rev-parse", "HEAD") == before
    assert run_git(repo, "status", "--porcelain") == ""
    refs = run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atc/integration-candidates")
    assert refs == ""



def test_post_integration_checkout_change_keeps_recovery_ref(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    real_git = gi.git

    def dirty_after_ff(repo_arg, *args, **kwargs):
        result = real_git(repo_arg, *args, **kwargs)
        if args[:2] == ("merge", "--ff-only"):
            (repo / "a.txt").write_text("unexpected checkout mutation\n", encoding="utf-8")
        return result

    monkeypatch.setattr(gi, "git", dirty_after_ff)
    assert integrate(repo, "topic", tip, "--json") == 3
    data = json.loads(capsys.readouterr().out)
    assert data["recovery_required"] is True
    assert data["refusal"]["reason"] == "RECOVERY_REQUIRED"
    assert data["main_after"] == tip
    assert data["candidate_ref"] == f"refs/atc/integration-candidates/{tip}"
    assert run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atc/integration-candidates") == data["candidate_ref"]
    monkeypatch.setattr(gi, "git", real_git)



def test_no_ff_parents_mismatch_fails_verification(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    publish(repo, "topic")
    commit(repo, "c.txt", "c\n")
    real = gi.git
    moved = []

    def advance_before_merge(repo_arg, *args, **kwargs):
        if args[:2] == ("merge", "--no-ff"):
            moved.append(commit(repo, "d.txt", "d\n"))
        return real(repo_arg, *args, **kwargs)

    monkeypatch.setattr(gi, "git", advance_before_merge)
    assert integrate(repo, "topic", tip) == 3
    assert "main drifted after candidate verification" in capsys.readouterr().err
    assert main_tip(repo) == moved[0]
    assert run_git(repo, "status", "--porcelain") == ""


def test_failed_merge_is_aborted(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "topic\n"})
    before = commit(repo, "a.txt", "main\n")
    real = gi.merge_preflight

    def claims_compatible(*args):
        report = real(*args)
        report.verdict, report.reason = "NO_FF", "COMPATIBLE"
        return report

    monkeypatch.setattr(gi, "merge_preflight", claims_compatible)
    assert integrate(repo, "topic", tip) == 2
    assert "ERROR CANDIDATE_MERGE_FAILED candidate merge failed" in capsys.readouterr().err
    assert main_tip(repo) == before
    assert run_git(repo, "status", "--porcelain") == ""


def test_json_fields(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    keys = {"command", "project", "task", "tasks", "authority", "records_only", "verdict", "reason", "main_before", "main_after", "reviewed", "source_before", "candidate_sha", "first_parent", "second_parent", "message", "verification", "verification_seconds", "evidence", "evidence_reason", "candidate_ref", "integration_record", "warnings", "push"}
    before = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"}, branch="first")
    assert integrate(repo, "first", tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data.pop("verification_seconds") >= 0
    assert Path(data.pop("integration_record")) == repo / ".git" / "atc" / "integrations.jsonl"
    assert [w for w in data.pop("warnings") if "atc tool checkout" not in w] == [gi.ALIAS_NOTICE]
    assert data.pop("push") == {"main": "main", "main_tip": tip, "origin_main_tip": None, "commits": []}
    assert data == {
        "command": "integrate", "project": "repo", "task": "test-owner-authorization",
        "tasks": ["test-owner-authorization"], "authority": {"kind": "unclassified"}, "records_only": False, "verdict": "FF", "reason": "FAST_FORWARD", "main_before": before,
        "main_after": tip, "reviewed": tip, "source_before": tip, "candidate_sha": tip,
        "first_parent": None, "second_parent": None, "message": None, "verification": "PASS",
        "evidence": None, "evidence_reason": "NO_REPORT", "candidate_ref": None,
    }

    second = topic(repo, {"b.txt": "b2\n"}, branch="topic/next")
    publish(repo, "topic/next")
    base = commit(repo, "c.txt", "c\n")
    assert integrate(repo, "topic/next", second, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == keys
    assert (data["verdict"], data["reason"], data["main_before"]) == ("NO_FF", "COMPATIBLE", base)
    assert (data["main_after"], data["candidate_sha"], data["first_parent"], data["second_parent"]) == (main_tip(repo), main_tip(repo), base, second)
    assert data["verification"] == "PASS"
    assert data["message"] == "merge(next): integrate reviewed changes"


def test_json_stop_carries_preflight(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = stop_conflict(repo)
    before = main_tip(repo)
    assert integrate(repo, "topic", tip, "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert (data["verdict"], data["reason"], data["main_before"], data["main_after"]) == ("STOP", "CONFLICT", before, before)
    assert data["preflight"]["evidence"]["conflict_files"] == ["a.txt"]


REPORT = {
    "schema": "atc-verification-report/1",
    "selector": {"name": "atc-testkit", "version": "0.6.0"},
    "selection": {"full": False, "paths": ["packages/a/tests"]},
    "stages": [{"name": "parallel", "marker": "not slow", "ignore": []}],
    "dependency_inputs": {"uv.lock": "0" * 64},
    "python": "3.13.14",
}


def report_command(text: str | None, *, fail: bool = False) -> str:
    """A verifier that writes ``text`` as its report, outside the candidate."""
    script = (
        "import os,pathlib,sys; "
        "report=pathlib.Path(os.environ['ATC_VERIFICATION_REPORT']).resolve(); "
        "assert not report.is_relative_to(pathlib.Path(os.environ['ATC_CANDIDATE_DIR']).resolve()); "
        f"text={text!r}; "
        "text is None or report.write_text(text, encoding='utf-8'); "
        f"sys.exit({int(fail)})"
    )
    return json.dumps([sys.executable, "-c", script])


def integrate_with(repo: Path, branch: str, reviewed: str, *extra: str) -> int:
    """Integrate with the verifier main declares; callers declare a report command before topic branches."""
    return gi.main([
        "--repo", str(repo), "--branch", branch, "--reviewed", reviewed,
        "--authority-reference", "test-owner-authorization", *extra,
    ])


def evidence_dir(repo: Path) -> Path:
    return repo / ".git" / "atc" / "verification-evidence"


def records(repo: Path) -> list[str]:
    return sorted(path.name for path in evidence_dir(repo).glob("*")) if evidence_dir(repo).is_dir() else []


def leftover_reports() -> list[Path]:
    return list((Path(os.environ["ATC_TMP"]) / "candidates").rglob("*.report.json"))


def test_valid_report_writes_evidence_record(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import platform

    command = report_command(json.dumps(REPORT))
    declare_verifier(repo, command)
    before = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate_with(repo, "topic", tip) == 0
    path = evidence_dir(repo) / f"{tip}.json"
    line = capsys.readouterr().out.strip()
    assert line.startswith(f"FF repo main {before[:12]} -> {tip[:12]} task=")
    assert records(repo) == [f"{tip}.json"]
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record.pop("duration_seconds") >= 0
    assert record.pop("verified_at").endswith("+00:00")
    assert record == {
        "schema": "atc-verification-evidence/2", "candidate_sha": tip,
        "tree": run_git(repo, "rev-parse", f"{tip}^{{tree}}"), "clean": True,
        "expected_main": before, "reviewed": tip, "verdict": "FF",
        "task": "test-owner-authorization", "tasks": ["test-owner-authorization"],
        "authority": {"kind": "unclassified"}, "verify_argv": json.loads(command),
        "platform": {"system": sys.platform, "machine": platform.machine()},
        "report": REPORT,
    }
    assert leftover_reports() == []
    assert run_git(repo, "status", "--porcelain") == ""


def test_no_ff_record_names_the_merge_commit_only(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_verifier(repo, report_command(json.dumps(REPORT)))
    tip = topic(repo, {"a.txt": "a2\n"})
    publish(repo, "topic")
    base = commit(repo, "c.txt", "c\n")
    assert integrate_with(repo, "topic", tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    merged = main_tip(repo)
    assert merged not in (tip, base)
    assert records(repo) == [f"{merged}.json"]
    assert Path(data["evidence"]).resolve() == (evidence_dir(repo) / f"{merged}.json").resolve()
    assert data["evidence_reason"] is None
    record = json.loads((evidence_dir(repo) / f"{merged}.json").read_text(encoding="utf-8"))
    assert (record["candidate_sha"], record["verdict"], record["expected_main"], record["reviewed"]) == (
        merged, "NO_FF", base, tip)
    assert record["tree"] == run_git(repo, "rev-parse", f"{merged}^{{tree}}")
    assert record["tree"] != run_git(repo, "rev-parse", f"{tip}^{{tree}}")


def test_verifier_without_report_integrates_and_writes_no_record(
    repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    declare_verifier(repo, report_command(None))
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate_with(repo, "topic", tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["verification"], data["evidence"], data["evidence_reason"]) == ("PASS", None, "NO_REPORT")
    assert main_tip(repo) == tip
    assert records(repo) == []


@pytest.mark.parametrize("text", [
    "not json",
    json.dumps(REPORT)[:40],
    json.dumps([REPORT]),
    json.dumps({**REPORT, "schema": "atc-verification-report/2"}),
    json.dumps({key: value for key, value in REPORT.items() if key != "selection"}),
    json.dumps({**REPORT, "selection": {"full": "yes", "paths": []}}),
    json.dumps({**REPORT, "selection": {"full": True, "paths": [1]}}),
    json.dumps({**REPORT, "selector": {"name": "atc-testkit"}}),
    json.dumps({**REPORT, "stages": [{"name": "parallel", "marker": "not slow"}]}),
    json.dumps({**REPORT, "dependency_inputs": {"uv.lock": None}}),
    "",
], ids=["not-json", "truncated", "not-object", "schema", "no-selection", "full-type", "paths-type",
        "selector", "stage-ignore", "input-digest", "empty"])
def test_invalid_report_integrates_and_writes_no_record(
    repo: Path, capsys: pytest.CaptureFixture[str], text: str,
) -> None:
    declare_verifier(repo, report_command(text))
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate_with(repo, "topic", tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["verification"], data["evidence"], data["evidence_reason"]) == ("PASS", None, "INVALID_REPORT")
    assert main_tip(repo) == tip
    assert records(repo) == []


def test_failed_verification_writes_no_record(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_verifier(repo, report_command(json.dumps(REPORT), fail=True))
    before = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate_with(repo, "topic", tip, "--json") == 3
    data = json.loads(capsys.readouterr().out)
    assert (data["verification"], data["evidence"], data["evidence_reason"]) == ("NOT_RUN", None, None)
    assert main_tip(repo) == before
    assert records(repo) == []
    assert leftover_reports() == []


def test_record_write_failure_keeps_the_integration(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(*_args):
        raise OSError("read-only")
    monkeypatch.setattr(gi, "_write_evidence", refuse)
    declare_verifier(repo, report_command(json.dumps(REPORT)))
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate_with(repo, "topic", tip, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["verification"], data["evidence"], data["evidence_reason"]) == ("PASS", None, "WRITE_FAILED")
    assert main_tip(repo) == tip


def test_expired_records_are_removed_after_a_write(repo: Path) -> None:
    directory = evidence_dir(repo)
    directory.mkdir(parents=True)
    expired, recent, foreign = directory / ("1" * 40 + ".json"), directory / ("2" * 40 + ".json"), directory / "notes.txt"
    day = 24 * 3600
    for path, age in ((expired, 15 * day), (recent, 13 * day), (foreign, 15 * day)):
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (time.time() - age,) * 2)
    declare_verifier(repo, report_command(json.dumps(REPORT)))
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate_with(repo, "topic", tip) == 0
    assert records(repo) == sorted([recent.name, foreign.name, f"{tip}.json"])


# Declarations, refusal forms and target inference (`shared/governance/GIT_WORKFLOW.md` § Integrate, § Integration verifier source with § Authority reference, and § Command results).


def run(repo: Path, *args: str, root: Path | None = None, root_given: bool = False, cwd: Path | None = None) -> int:
    """`atc integrate` with only the arguments given, from ``cwd``."""
    return gi.main([*args], root=root or repo, root_given=root_given, cwd=str(cwd or repo))


def stderr_lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return capsys.readouterr().err.splitlines()


@pytest.mark.integration
def test_the_resolved_values_are_the_first_stderr_line(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip, "--json") == 0
    lines = stderr_lines(capsys)
    assert lines[0].startswith("resolved: repo main=main@")
    assert f"branch=topic (--branch) reviewed={tip} " in lines[0]
    assert 'task="test-owner-authorization" authority=unclassified ' in lines[0]
    assert lines[0].endswith("(AGENTS.md verify.json) timeout=300s (default)")
    assert f"verify: candidate {tip} timeout 300s (default)" in lines
    assert f"WARNING {gi.ALIAS_NOTICE}" in lines


@pytest.mark.integration
def test_the_declared_argv_and_timeout_are_the_defaults(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_verifier(repo, verify_command(), timeout="1234")
    tip = topic(repo, {"a.txt": "a2\n"})
    assert run(repo, "--repo", str(repo), "--reviewed", tip, "--authority-reference", "x", "--json") == 0
    lines = stderr_lines(capsys)
    assert lines[0].endswith("(AGENTS.md verify.json) timeout=1234s (AGENTS.md)")
    assert not [line for line in lines if line.startswith("WARNING") and "atc tool checkout" not in line
                and gi.ALIAS_NOTICE not in line]
    assert main_tip(repo) == tip


@pytest.mark.integration
def test_an_explicit_timeout_that_differs_is_a_warning(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_verifier(repo, verify_command(), timeout="1234")
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip, "--verify-timeout", "60") == 0
    warnings = [line for line in stderr_lines(capsys) if line.startswith("WARNING")]
    assert "WARNING --verify-timeout 60 differs from the 1234 seconds of AGENTS.md at main" in warnings


@pytest.mark.integration
@pytest.mark.parametrize("agents", [
    "# AGENTS.md\n\nNo section.\n",
    "# AGENTS.md\n\n## Integration verification\n\n- Verifier timeout: 60\n",
])
def test_a_main_without_the_verification_section_stops(
    repo: Path, capsys: pytest.CaptureFixture[str], agents: str,
) -> None:
    commit(repo, "AGENTS.md", agents)
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    assert integrate(repo, "topic", tip) == 1
    first, after = refusal(capsys.readouterr().err)
    assert first.startswith(f"STOP VERIFICATION_SECTION_MISSING: AGENTS.md at main {before[:12]}")
    assert after.startswith("next: obtain the section from the owner")
    assert main_tip(repo) == before
    assert run_git(repo, "worktree", "list").count("\n") == 0


@pytest.mark.integration
def test_the_authority_sha_is_appended_and_checked(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    other = "0" * 40
    args = ["--repo", str(repo), "--reviewed", tip]
    assert run(repo, *args, "--authority-reference", f"a.txt @ {other}", "--json") == 2
    captured = capsys.readouterr()
    assert refusal(captured.err)[0] == f"ERROR AUTHORITY_SHA_MISMATCH the authority reference ends with @ {other}, but --reviewed is {tip}"
    assert json.loads(captured.out) == {"command": "integrate", "refusal": {
        "verdict": "ERROR", "reason": "AUTHORITY_SHA_MISMATCH",
        "detail": f"the authority reference ends with @ {other}, but --reviewed is {tip}", "next": None,
    }}
    assert run(repo, *args, "--authority-reference", f"a.txt @ {tip.upper()}", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["task"], data["authority"]) == ("a.txt", {"kind": "unclassified"})
    assert [w for w in data["warnings"] if "atc tool checkout" not in w] == [gi.ALIAS_NOTICE]


@pytest.mark.integration
@pytest.mark.parametrize(("reference", "path"), [
    ("docs/PLAN.md §2 step 3", "docs/PLAN.md"),
    ("owner/repo topic per docs/RULES.md § Section", "docs/RULES.md"),
])
def test_an_authority_record_the_reviewed_commit_lacks_is_a_warning(
    repo: Path, capsys: pytest.CaptureFixture[str], reference: str, path: str,
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    assert run(repo, "--repo", str(repo), "--reviewed", tip, "--authority-reference", reference) == 0
    assert f"WARNING the authority record {path} is not in the reviewed commit {tip[:12]}" in stderr_lines(capsys)


@pytest.mark.integration
def test_the_one_branch_at_the_reviewed_sha_is_inferred(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"}, branch="worktree-claude+demo")
    publish(repo, "worktree-claude+demo")
    commit(repo, "c.txt", "c\n")
    assert run(repo, "--repo", str(repo), "--reviewed", tip, "--authority-reference", "x", "--json") == 0
    assert "branch=worktree-claude+demo (inferred)" in stderr_lines(capsys)[0]
    assert run_git(repo, "log", "-1", "--format=%s", "main") == "merge(demo): integrate reviewed changes"


@pytest.mark.integration
def test_several_branches_at_the_reviewed_sha_stop(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "branch", "copy", tip)
    before = main_tip(repo)
    assert run(repo, "--repo", str(repo), "--reviewed", tip, "--authority-reference", "x", "--json") == 1
    captured = capsys.readouterr()
    first, after = refusal(captured.err)
    assert first == f"STOP BRANCH_AMBIGUOUS: several local branches point at the reviewed SHA {tip}: copy, topic"
    assert after == "next: run again with `--branch` naming the reviewed topic branch among those listed"
    assert json.loads(captured.out)["refusal"]["reason"] == "BRANCH_AMBIGUOUS"
    assert main_tip(repo) == before
    assert run(repo, "--repo", str(repo), "--branch", "topic", "--reviewed", tip, "--authority-reference", "x") == 0


@pytest.mark.integration
def test_no_branch_at_the_reviewed_sha_stops(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "branch", "-D", "topic")
    assert run(repo, "--repo", str(repo), "--reviewed", tip, "--authority-reference", "x") == 1
    first, after = refusal(capsys.readouterr().err)
    assert first == f"STOP NO_BRANCH_AT_REVIEWED: no local branch points at or contains the reviewed SHA: {tip[:12]}"
    assert after == "next: restore the reviewed topic branch at the reviewed SHA, or run again with the full SHA the formal review named"


@pytest.mark.integration
def test_a_branch_past_the_reviewed_sha_is_unreviewed_commits(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reviewed = stop_unreviewed(repo)
    assert run(repo, "--repo", str(repo), "--reviewed", reviewed, "--authority-reference", "x") == 1
    first, after = refusal(capsys.readouterr().err)
    assert first.startswith(f"STOP UNREVIEWED_COMMITS: no local branch is at the reviewed SHA {reviewed[:12]}; these have commits after it: topic")
    assert after == "next: review the delta, or re-review"


@pytest.mark.integration
def test_a_reviewed_sha_on_main_is_already_merged(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = stop_already_merged(repo)
    assert run(repo, "--repo", str(repo), "--reviewed", tip, "--authority-reference", "x") == 1
    first, after = refusal(capsys.readouterr().err)
    assert first.startswith("STOP ALREADY_MERGED: ")
    assert after == "next: nothing to integrate; continue with GIT_WORKFLOW.md § Cleanup trigger"


@pytest.mark.integration
def test_checkout_stops_name_the_branch_and_the_paths(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = stop_dirty(repo)
    assert integrate(repo, "topic", tip) == 1
    assert refusal(capsys.readouterr().err)[0].endswith("uncommitted changes to tracked files: b.txt")
    run_git(repo, "checkout", "--", "b.txt")
    run_git(repo, "switch", "-q", "-c", "elsewhere")
    assert integrate(repo, "topic", tip) == 1
    assert refusal(capsys.readouterr().err)[0] == "STOP CHECKOUT_NOT_ON_MAIN: the integration checkout is on elsewhere, not main"


@pytest.mark.integration
def test_every_integration_appends_its_record(repo: Path) -> None:
    first = topic(repo, {"a.txt": "a2\n"}, branch="first")
    assert integrate(repo, "first", first) == 0
    second = topic(repo, {"b.txt": "b2\n"}, branch="second")
    publish(repo, "second")
    commit(repo, "c.txt", "c\n")
    assert integrate(repo, "second", second) == 0
    lines = (repo / ".git" / "atc" / "integrations.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert [set(record) for record in records] == [
        {"candidate_sha", "reviewed", "main_before", "task", "authority", "records_only", "verifier", "integrated_at"}] * 2
    assert [record["verifier"] for record in records] == [
        {"argv_file": "verify.json", "timeout": 300, "timeout_overridden": False}] * 2
    assert records[1]["main_before"] == run_git(repo, "rev-parse", "main^1")
    assert (records[0]["candidate_sha"], records[0]["reviewed"]) == (first, first)
    assert (records[1]["candidate_sha"], records[1]["reviewed"]) == (main_tip(repo), second)
    assert (records[1]["task"], records[1]["authority"], records[1]["records_only"]) == (
        "test-owner-authorization", {"kind": "unclassified"}, False)


@pytest.mark.integration
def test_a_failed_verification_records_no_integration_and_names_its_log(
    repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    declare_verifier(repo, json.dumps([sys.executable, "-c", "import sys; print('out line'); print('err line', file=sys.stderr); sys.exit(4)"]))
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip) == 3
    lines = stderr_lines(capsys)
    first, after = refusal("\n".join(lines))
    assert first == f"STOP VERIFICATION_FAILED: candidate verification failed for {tip[:12]} (exit 4): err line"
    assert after.startswith("next: correct the cause on the topic branch")
    named = lines[lines.index(after) + 1]
    log = Path(named.removeprefix("log "))
    assert named.startswith("log ") and log.parent == atc_tmp / "candidates" and log.name.endswith(".verify.log")
    text = log.read_text(encoding="utf-8")
    assert "--- stdout ---\nout line\n--- stderr ---\nerr line\n" in text and "exit: 4" in text
    # The verifier's first stderr line, then, from a log without FAILED or ERROR lines, its last twenty lines.
    assert lines[lines.index(named) + 1:] == ["verifier: err line", *(f"  {line}" for line in text.splitlines())]
    assert not (repo / ".git" / "atc" / "integrations.jsonl").exists()


FAILING_VERIFIER = (
    "import sys; print('collected 30 items'); "
    "[print(f'FAILED tests/test_x.py::test_{n} - assert 0') for n in range(25)]; "
    "print('TESTS_FAILED', file=sys.stderr); print('ERROR tests/test_y.py::test_z', file=sys.stderr); sys.exit(1)"
)


@pytest.mark.integration
def test_a_failed_verification_repeats_the_last_twenty_failure_lines_of_its_log(
    repo: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    declare_verifier(repo, json.dumps([sys.executable, "-c", FAILING_VERIFIER]))
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip) == 3
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    first, after = refusal(captured.err)
    assert first == f"STOP VERIFICATION_FAILED: candidate verification failed for {tip[:12]} (exit 1): ERROR tests/test_y.py::test_z"
    at = lines.index(after)
    assert lines[at + 1].startswith("log ") and lines[at + 1].endswith(".log")
    assert lines[at + 2:] == [
        "verifier: TESTS_FAILED",
        *(f"  FAILED tests/test_x.py::test_{n} - assert 0" for n in range(6, 25)),
        "  ERROR tests/test_y.py::test_z",
    ]
    assert captured.out == ""


@pytest.mark.integration
def test_a_later_run_finds_the_required_recovery(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    real_git = gi.git

    def dirty_after_ff(repo_arg, *args, **kwargs):
        result = real_git(repo_arg, *args, **kwargs)
        if args[:2] == ("merge", "--ff-only"):
            (repo / "a.txt").write_text("unexpected checkout mutation\n", encoding="utf-8")
        return result

    monkeypatch.setattr(gi, "git", dirty_after_ff)
    assert integrate(repo, "topic", tip) == 3
    monkeypatch.setattr(gi, "git", real_git)
    capsys.readouterr()
    # main holds the candidate, so the stopped run already recorded the integration.
    record_file = repo / ".git" / "atc" / "integrations.jsonl"
    assert [json.loads(line)["candidate_sha"] for line in record_file.read_text(encoding="utf-8").splitlines()] == [tip]
    refs = run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atc/integration-candidates")
    args = ["--repo", str(repo), "--reviewed", tip, "--authority-reference", "x"]
    assert run(repo, *args, "--check") == 3
    assert refusal(capsys.readouterr().err)[0].startswith("STOP RECOVERY_REQUIRED: ")
    assert run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atc/integration-candidates") == refs
    assert run(repo, *args) == 3
    first, after = refusal(capsys.readouterr().err)
    assert first.startswith("STOP RECOVERY_REQUIRED: ")
    assert after.startswith("next: reconcile main and its checkout with the retained candidate ref")
    run_git(repo, "checkout", "--", "a.txt")
    assert run(repo, *args, "--check") == 0
    assert capsys.readouterr().out.startswith("CHECK RECOVERED CANDIDATE_ALREADY_ON_MAIN: ")
    assert run(repo, *args) == 0
    assert run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atc/integration-candidates") == ""
    assert len(record_file.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.integration
def test_a_stopped_update_is_recorded_when_another_request_drops_its_ref(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    first_tip = topic(repo, {"a.txt": "first\n"}, branch="topic-one")
    second_tip = topic(repo, {"b.txt": "second\n"}, branch="topic-two")
    publish(repo, "topic-two")
    real_git = gi.git

    def head_moves_after_ff(repo_arg, *args, **kwargs):
        result = real_git(repo_arg, *args, **kwargs)
        if args[:2] == ("merge", "--ff-only"):
            real_git(repo, "switch", "-q", "--detach", "main~1")
        return result

    monkeypatch.setattr(gi, "git", head_moves_after_ff)
    assert integrate(repo, "topic-one", first_tip) == 3
    monkeypatch.setattr(gi, "git", real_git)
    run_git(repo, "switch", "-q", "main")
    assert integrate(repo, "topic-two", second_tip) == 0
    assert "main_retains_commit_ref_removed_request_not_matched" in capsys.readouterr().err
    records = [json.loads(line) for line in (repo / ".git" / "atc" / "integrations.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["candidate_sha"], r["reviewed"]) for r in records] == [(first_tip, first_tip), (main_tip(repo), second_tip)]


@pytest.mark.integration
def test_main_moved_since_its_declaration_was_read_is_ref_drift(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    publish(repo, "topic")
    real = gi.resolve
    moved = []

    def resolve_then_advance(*args):
        target = real(*args)
        moved.append(commit(repo, "c.txt", "c\n"))
        return target

    monkeypatch.setattr(gi, "resolve", resolve_then_advance)
    assert integrate(repo, "topic", tip) == 1
    first, after = refusal(capsys.readouterr().err)
    assert first.startswith("STOP REF_DRIFT: main moved from ")
    assert after == "next: run again; the preflight reassesses the table against the new refs"
    assert main_tip(repo) == moved[0]
    assert run_git(repo, "worktree", "list").count("\n") == 0


@pytest.mark.integration
def test_the_declaration_comes_from_main_not_the_topic(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_verifier(repo, verify_command(), timeout="1234")
    tip = topic(repo, {"AGENTS.md": "# AGENTS.md\n\n## Integration verification\n\n- Verifier argv file: other.json\n- Verifier timeout: 5\n"})
    assert run(repo, "--repo", str(repo), "--reviewed", tip, "--authority-reference", "x", "--json") == 0
    assert stderr_lines(capsys)[0].endswith("(AGENTS.md verify.json) timeout=1234s (AGENTS.md)")
    assert main_tip(repo) == tip


@pytest.mark.integration
def test_a_verifier_that_cannot_start_is_an_error(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_verifier(repo, json.dumps(["missing/atc-verifier-that-does-not-exist"]))
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    assert integrate(repo, "topic", tip) == 2
    error = capsys.readouterr().err
    assert refusal(error)[0].startswith("ERROR VERIFIER_NOT_STARTED could not start candidate verifier: ")
    assert "Traceback" not in error
    assert main_tip(repo) == before
    assert run_git(repo, "worktree", "list").count("\n") == 0


@pytest.mark.integration
@pytest.mark.parametrize("args", [
    ["--authority-reference", "x"],
    ["--reviewed", "0" * 40, "--authority-reference", "x", "--verify-command", "[]"],
])
def test_argument_errors_take_the_error_form(repo: Path, capsys: pytest.CaptureFixture[str], args: list[str]) -> None:
    assert gi.main(["--repo", str(repo), *args]) == 2
    lines = capsys.readouterr().err.splitlines()
    assert lines[0].startswith("ERROR USAGE ")
    assert len(lines) == 1


@pytest.mark.integration
def test_check_resolves_and_preflights_without_a_candidate(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    before = main_tip(repo)
    assert run(repo, "--repo", str(repo), "--reviewed", tip, "--authority-reference", "x", "--check") == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("CHECK FF FAST_FORWARD: ")
    assert "resolved: " not in captured.err
    assert main_tip(repo) == before
    assert run_git(repo, "worktree", "list").count("\n") == 0
    assert not (repo / ".git" / "atc").exists()

    commit(repo, "a.txt", "main\n")
    run_git(repo, "switch", "-q", "topic")
    commit(repo, "a.txt", "topic\n")
    conflicting = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "switch", "-q", "main")
    assert run(repo, "--repo", str(repo), "--reviewed", conflicting, "--authority-reference", "x", "--check", "--json") == 1
    captured = capsys.readouterr()
    assert refusal(captured.err)[0].startswith("STOP CONFLICT: ")
    data = json.loads(captured.out)
    assert (data["check"], data["refusal"]["reason"], data["resolved"]["branch"]) == (True, "CONFLICT", "topic")


def not_warnings(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.startswith("WARNING ")]


@pytest.mark.integration
def test_the_default_check_output_is_the_check_line(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip, "--check") == 0
    captured = capsys.readouterr()
    assert captured.out == (
        f'CHECK FF FAST_FORWARD: repo branch=topic tip={tip[:12]} task="test-owner-authorization" authority=unclassified\n'
    )
    assert f"WARNING {gi.ALIAS_NOTICE}" in captured.err.splitlines()
    assert not_warnings(captured.err) == []


@pytest.mark.integration
def test_a_default_check_stop_is_the_stop_and_next_lines(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "topic\n"})
    commit(repo, "a.txt", "main\n")
    assert run(repo, "--repo", str(repo), "--reviewed", tip, "--authority-reference", "x", "--check") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert not_warnings(captured.err) == [
        "STOP CONFLICT: main advanced and the merge conflicts; reconcile on the topic branch and re-review",
        f"next: {gi.gp.NEXT_UNPUBLISHED['CONFLICT']}",
    ]


@pytest.mark.integration
def test_the_default_integration_output_is_one_line_naming_the_log(
    repo: Path, atc_tmp: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    before = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip) == 0
    captured = capsys.readouterr()
    match = re.fullmatch(
        rf'FF repo main {before[:12]} -> {tip[:12]} task="test-owner-authorization" authority=unclassified '
        r"log (?P<log>.+[0-9a-f]{12}\.verify\.log)\n", captured.out,
    )
    assert match, captured.out
    log = Path(match["log"])
    assert log.parent == atc_tmp / "candidates"
    assert "exit: 0\n--- stdout ---\n" in log.read_text(encoding="utf-8")
    assert not_warnings(captured.err) == []


@pytest.fixture
def workspace(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A workspace.yaml at ``tmp_path`` registering the fixture repository as `demo`."""
    monkeypatch.delenv("ATC_ROOT", raising=False)
    (tmp_path / "workspace.yaml").write_text(
        f"version: 1\nworkspace_root: '{tmp_path}'\nprojects:\n  demo:\n"
        "    repo: repo\n    worktrees: worktrees/demo\n    state: state/demo-data\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.integration
def test_project_resolves_through_the_workspace_map(repo: Path, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    assert run(repo, "--project", "demo", "--reviewed", tip, "--authority-reference", "x", root=workspace, cwd=workspace) == 0
    assert capsys.readouterr().out.startswith("FF demo main ")
    assert main_tip(repo) == tip


@pytest.mark.unit
def test_a_root_worktree_names_the_workspace_root(workspace: Path) -> None:
    args = gi.build_parser().parse_args(["--reviewed", "a" * 40, "--authority-reference", "x"])
    inside = workspace / "worktrees" / "root" / "claude+topic" / "adapters"
    assert gi.resolve_repo(args, workspace, False, str(inside)) == ("root", workspace)
    named = gi.build_parser().parse_args(["--reviewed", "a" * 40, "--project", "root", "--authority-reference", "x"])
    assert gi.resolve_repo(named, workspace, False, str(workspace / "shared")) == ("root", workspace)
    with pytest.raises(gi.Refused) as refused:
        gi.resolve_repo(args, workspace, False, str(workspace / "shared"))
    assert refused.value.token == "UNKNOWN_PROJECT"


@pytest.mark.integration
def test_the_working_directory_names_the_project(repo: Path, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    args = ["--reviewed", tip, "--authority-reference", "x"]
    assert run(repo, *args, root=workspace, cwd=workspace) == 2
    assert refusal(capsys.readouterr().err)[0].startswith("ERROR UNKNOWN_PROJECT no --repo or --project")
    assert run(repo, *args, root=workspace, cwd=repo) == 0
    assert main_tip(repo) == tip


@pytest.mark.integration
def test_an_unknown_project_is_refused(repo: Path, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    assert run(repo, "--project", "other", "--reviewed", tip, "--authority-reference", "x", root=workspace) == 2
    assert refusal(capsys.readouterr().err)[0].startswith("ERROR UNKNOWN_PROJECT other is not in")


@pytest.mark.integration
def test_an_explicit_root_beside_repo_is_refused(repo: Path, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    args = ["--repo", str(repo), "--reviewed", tip, "--authority-reference", "x"]
    assert run(repo, *args, root=workspace, root_given=True) == 2
    assert refusal(capsys.readouterr().err)[0].startswith("ERROR ROOT_WITH_REPO ")
    assert main_tip(repo) != tip


@pytest.mark.integration
def test_atc_hands_its_root_to_integrate(repo: Path, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import atc_cli

    tip = topic(repo, {"a.txt": "a2\n"})
    args = ["--reviewed", tip, "--authority-reference", "x"]
    assert atc_cli.main(["--root", str(workspace), "integrate", "--repo", str(repo), *args]) == 2
    assert refusal(capsys.readouterr().err)[0].startswith("ERROR ROOT_WITH_REPO ")
    assert atc_cli.main(["--root", str(workspace), "integrate", "--project", "demo", *args]) == 0
    assert main_tip(repo) == tip


@pytest.mark.integration
def test_the_tool_checkout_warning_names_its_branch_and_sha(repo: Path) -> None:
    assert gi.tool_checkout_warning(repo) is None
    run_git(repo, "switch", "-q", "-c", "elsewhere")
    head = main_tip(repo)
    assert gi.tool_checkout_warning(repo) == f"the atc tool checkout {repo} is on elsewhere at {head}; this run uses its code"
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    assert gi.tool_checkout_warning(repo).endswith(f"at {head} with uncommitted changes; this run uses its code")


@pytest.mark.integration
def test_a_relative_repo_keeps_every_dot_in_the_output(repo: Path, capsys: pytest.CaptureFixture[str],
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    declare_verifier(repo, verify_command(), timeout="1234")
    tip = topic(repo, {"a.txt": "a2\n"})
    monkeypatch.chdir(repo)
    assert run(repo, "--repo", ".", "--reviewed", tip, "--authority-reference", "x", cwd=repo) == 0
    log = capsys.readouterr().out.strip().rsplit(" log ", 1)[1]
    assert log.endswith(".log") and Path(log).is_file()


def verify(repo: Path, branch: str, reviewed: str) -> int:
    return gi.verify_main(["--repo", str(repo), "--branch", branch, "--reviewed", reviewed])


@pytest.mark.integration
def test_atc_verify_builds_the_merge_for_a_no_ff_and_moves_nothing(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"}, branch="worktree-claude+demo-topic")
    publish(repo, "worktree-claude+demo-topic")
    before = commit(repo, "c.txt", "c\n")
    refs = run_git(repo, "for-each-ref")
    seen = []
    real = gi._verify_candidate

    def record_candidate(argv, candidate_dir, candidate_sha, *rest):
        seen.append(run_git(candidate_dir, "rev-parse", "HEAD^1", "HEAD^2").split())
        return real(argv, candidate_dir, candidate_sha, *rest)

    gi_patch = pytest.MonkeyPatch()
    gi_patch.setattr(gi, "_verify_candidate", record_candidate)
    try:
        assert verify(repo, "worktree-claude+demo-topic", tip) == 0
    finally:
        gi_patch.undo()
    assert seen == [[before, tip]]
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.startswith("VERIFY PASS ") and " NO_FF" in last
    assert main_tip(repo) == before and run_git(repo, "for-each-ref") == refs


@pytest.mark.integration
def test_atc_verify_takes_a_reviewed_prefix(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    assert verify(repo, "topic", tip[:12]) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith(f"VERIFY PASS {tip[:12]} FF log ")


@pytest.mark.integration
def test_atc_verify_stops_when_main_moved_since_its_declaration_was_read(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    publish(repo, "topic")
    real = gi.resolve
    moved = []

    def resolve_then_advance(*args):
        target = real(*args)
        moved.append(commit(repo, "c.txt", "c\n"))
        return target

    monkeypatch.setattr(gi, "resolve", resolve_then_advance)
    assert verify(repo, "topic", tip) == 1
    first, _ = refusal(capsys.readouterr().err)
    assert first.startswith("STOP REF_DRIFT: main moved from ")
    assert main_tip(repo) == moved[0] and run_git(repo, "worktree", "list").count("\n") == 0

from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

import pytest

import git_preflight as gp


def run_git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
    )
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True)
    return result.stdout.strip()


def commit(repo: Path, path: str, content: str) -> str:
    (repo / path).write_text(content, encoding="utf-8")
    run_git(repo, "add", "--", path)
    run_git(repo, "commit", "-q", "-m", f"edit {path}")
    return run_git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit(root, "a.txt", "a\n")
    commit(root, "b.txt", "b\n")
    return root


def topic(repo: Path, edits: dict[str, str]) -> str:
    run_git(repo, "switch", "-q", "-c", "topic")
    for path, content in edits.items():
        commit(repo, path, content)
    tip = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "switch", "-q", "main")
    return tip


def publish(repo: Path, *refs: str) -> Path:
    """Push ``refs`` to a bare `origin` beside the repository, added on first use."""
    origin = repo.parent / "origin.git"
    if not origin.exists():
        run_git(repo.parent, "init", "-q", "--bare", "-b", "main", str(origin))
        run_git(repo, "remote", "add", "origin", str(origin))
    run_git(repo, "push", "-q", "origin", *refs)
    return origin


def test_fast_forward_when_main_is_merge_base(repo: Path) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    report = gp.merge_preflight(repo, "topic", tip)
    assert (report.verdict, report.reason) == ("FF", "FAST_FORWARD")
    assert report.evidence["topic_tip"] == tip
    assert report.evidence["main_advanced_by"] == 0
    assert report.evidence["reviewed_state"] == "tip"


def test_no_ff_when_main_advanced_on_other_files(repo: Path) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    publish(repo, "topic")
    commit(repo, "c.txt", "c\n")
    report = gp.merge_preflight(repo, "topic", tip[:12])
    assert (report.verdict, report.reason) == ("NO_FF", "COMPATIBLE")
    assert report.evidence["main_advanced_by"] == 1
    assert report.evidence["reviewed_sha"] == tip
    assert report.evidence["overlap_files"] == []


def test_overlap_stops_without_conflict(repo: Path) -> None:
    (repo / "a.txt").write_text("1\n2\n3\n4\n5\n6\n", encoding="utf-8")
    run_git(repo, "commit", "-q", "-am", "base")
    tip = topic(repo, {"a.txt": "one\n2\n3\n4\n5\n6\n"})
    commit(repo, "a.txt", "1\n2\n3\n4\n5\nsix\n")
    report = gp.merge_preflight(repo, "topic", tip)
    assert (report.verdict, report.reason) == ("STOP", "OVERLAP")
    assert report.evidence["conflict"] is False
    assert report.evidence["overlap_files"] == ["a.txt"]


def test_conflict_stops(repo: Path) -> None:
    tip = topic(repo, {"a.txt": "topic\n"})
    commit(repo, "a.txt", "main\n")
    report = gp.merge_preflight(repo, "topic", tip)
    assert (report.verdict, report.reason) == ("STOP", "CONFLICT")
    assert report.evidence["conflict_files"] == ["a.txt"]


def test_missing_review_stops(repo: Path) -> None:
    topic(repo, {"a.txt": "a2\n"})
    report = gp.merge_preflight(repo, "topic", None)
    assert (report.verdict, report.reason) == ("STOP", "NO_REVIEWED_SHA")


def test_commits_after_review_stop(repo: Path) -> None:
    reviewed = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "switch", "-q", "topic")
    commit(repo, "a.txt", "a3\n")
    run_git(repo, "switch", "-q", "main")
    report = gp.merge_preflight(repo, "topic", reviewed)
    assert (report.verdict, report.reason) == ("STOP", "UNREVIEWED_COMMITS")
    assert report.evidence["unreviewed_commits"] == 1


def test_rewritten_review_stops(repo: Path) -> None:
    reviewed = topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "switch", "-q", "topic")
    run_git(repo, "commit", "-q", "--amend", "-m", "reworded")
    run_git(repo, "switch", "-q", "main")
    report = gp.merge_preflight(repo, "topic", reviewed)
    assert (report.verdict, report.reason) == ("STOP", "REVIEW_REWRITTEN")


@pytest.mark.parametrize("reviewed", ["topic", "topic~0", "HEAD", "abc123", "x" * 12, "abcdef12345"])
def test_non_sha_review_is_invalid(repo: Path, reviewed: str) -> None:
    topic(repo, {"a.txt": "a2\n"})
    report = gp.merge_preflight(repo, "topic", reviewed)
    assert (report.verdict, report.reason) == ("STOP", "REVIEWED_SHA_INVALID")
    assert report.evidence["reviewed_state"] == "invalid"


def test_full_and_uppercase_reviewed_sha_still_work(repo: Path) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    for reviewed in (tip, tip[:12].upper()):
        report = gp.merge_preflight(repo, "topic", reviewed)
        assert (report.verdict, report.reason) == ("FF", "FAST_FORWARD")
        assert report.evidence["reviewed_sha"] == tip


def test_unknown_sha_stops(repo: Path) -> None:
    topic(repo, {"a.txt": "a2\n"})
    report = gp.merge_preflight(repo, "topic", "0123456789abcdef0123456789abcdef01234567")
    assert (report.verdict, report.reason) == ("STOP", "REVIEWED_SHA_UNKNOWN")


def test_hex_named_branch_does_not_stand_in_for_a_sha(repo: Path) -> None:
    topic(repo, {"a.txt": "a2\n"})
    run_git(repo, "branch", "deadbeefdead", "topic")
    report = gp.merge_preflight(repo, "topic", "deadbeefdead")
    assert (report.verdict, report.reason) == ("STOP", "REVIEWED_SHA_UNKNOWN")


def test_dirty_checkout_stops(repo: Path) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    (repo / "b.txt").write_text("dirty\n", encoding="utf-8")
    report = gp.merge_preflight(repo, "topic", tip)
    assert (report.verdict, report.reason) == ("STOP", "CHECKOUT_DIRTY")


def test_cleanup_reports_unmerged_then_removable(repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    run_git(repo, "worktree", "add", "-q", str(wt), "-b", "topic", "HEAD")
    tip = commit(wt, "a.txt", "a2\n")
    report = gp.cleanup_preflight(repo, wt)
    assert (report.verdict, report.reason) == ("KEEP", "NOT_MERGED")
    run_git(repo, "merge", "-q", "--ff-only", "topic")
    (wt / "scratch.txt").write_text("x\n", encoding="utf-8")
    assert gp.cleanup_preflight(repo, wt).reason == "WORKTREE_DIRTY"
    (wt / "scratch.txt").unlink()
    report = gp.cleanup_preflight(repo, wt)
    assert (report.verdict, report.reason) == ("REMOVABLE", "SAFE_TO_REMOVE")
    assert report.evidence["tip"] == tip


def test_cleanup_refuses_main_checkout_and_unknown_path(repo: Path, tmp_path: Path) -> None:
    assert gp.cleanup_preflight(repo, repo).reason == "MAIN_CHECKOUT"
    assert gp.cleanup_preflight(repo, tmp_path / "nowhere").reason == "NOT_A_WORKTREE"


@pytest.mark.integration
def test_every_stop_names_its_next_step(repo: Path) -> None:
    assert set(gp.NEXT) == {reason for reason in gp.REASONS if reason not in (
        "FAST_FORWARD", "COMPATIBLE", "NOT_A_WORKTREE", "MAIN_CHECKOUT", "NOT_MERGED", "WORKTREE_DIRTY", "SAFE_TO_REMOVE",
        "PUSH_LISTED",
    )}
    tip = topic(repo, {"a.txt": "a2\n"})
    (repo / "b.txt").write_text("dirty\n", encoding="utf-8")
    lines = gp.render(gp.merge_preflight(repo, "topic", tip)).splitlines()
    assert lines[0] == "STOP CHECKOUT_DIRTY: the integration checkout has uncommitted changes to tracked files: b.txt"
    assert lines[1] == "hint: have the owner of the listed paths commit or remove them, then run again"


# The per-invocation memo (docs/design/ATC-0072.md § 3)

SHA_A = "a" * 40
SHA_B = "b" * 40


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """The argv after `-C <repo>` of every git process started from now on."""
    calls: list[tuple[str, ...]] = []
    real = subprocess.run

    def counting(argv, *args, **kwargs):
        if argv and argv[0] == "git":
            calls.append(tuple(argv[3:] if argv[1:2] == ["-C"] else argv[1:]))
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    return calls


@pytest.mark.unit
@pytest.mark.parametrize(("args", "expected"), [
    (("rev-parse", "--verify", "--quiet", f"{SHA_A}^{{commit}}"), gp.OBJECT),
    (("rev-parse", "--verify", "--quiet", f"{SHA_A}^{{tree}}"), gp.OBJECT),
    (("rev-parse", f"{SHA_A}^1", f"{SHA_A}^2"), gp.OBJECT),
    (("show", f"{SHA_A}:.atc/tasks/ATC-0001.yaml"), gp.OBJECT),
    (("cat-file", "-p", f"{SHA_A}:vendor/atc-testkit.json"), gp.OBJECT),
    (("merge-base", SHA_A, SHA_B), gp.OBJECT),
    (("merge-base", "--is-ancestor", SHA_A, SHA_B), gp.OBJECT),
    (("rev-list", "--count", f"{SHA_A}..{SHA_B}"), gp.OBJECT),
    (("rev-list", "--parents", "-n", "1", SHA_A), gp.OBJECT),
    (("log", "--reverse", "--format=%H %s", f"{SHA_A}..{SHA_B}"), gp.OBJECT),
    (("diff", "--name-only", "--no-renames", "-z", SHA_A, SHA_B), gp.OBJECT),
    (("ls-tree", "-z", SHA_A, "--", ".atc/tasks/"), gp.OBJECT),
    (("merge-tree", "--write-tree", "--name-only", f"--merge-base={SHA_A}^", SHA_A, SHA_B), gp.OBJECT),
    (("rev-parse", "--verify", "--quiet", "main^{commit}"), gp.REF),
    (("rev-parse", "--verify", "--quiet", f"{SHA_A}a^{{commit}}"), gp.REF),
    (("show", "refs/heads/main:AGENTS.md"), gp.REF),
    (("merge-base", "main", SHA_A), gp.REF),
    (("merge-tree", "--write-tree", f"--merge-base={SHA_A}^", "main", SHA_B), gp.REF),
    (("diff", "--check", SHA_A, SHA_B), gp.REF),
    (("rev-parse", f"--disambiguate={SHA_A[:12]}"), gp.REF),
    (("diff", "--name-only", f"{SHA_A}..{SHA_B}"), gp.OBJECT),
    (("diff", SHA_A), gp.REF),
    (("diff", "--quiet", SHA_A, "--", "a.txt"), gp.REF),
    (("diff", "--cached", "--name-only", SHA_A), gp.REF),
    (("diff", "--cached", SHA_A, SHA_B), gp.REF),
    (("diff", "--no-index", SHA_A, SHA_B), gp.REF),
    (("diff", "--name-only"), gp.REF),
    (("rev-list", SHA_A, "--not", "--all"), gp.REF),
    (("rev-list", SHA_A, "--not", "--branches=topic*"), gp.REF),
    (("log", "--all", SHA_A), gp.REF),
    (("log", "-g", SHA_A), gp.REF),
    (("log", "--glob=refs/atc/*", SHA_A), gp.REF),
    (("rev-list", "--count", SHA_A, "--exclude=refs/x", "--tags"), gp.REF),
    (("merge-base", "--fork-point", SHA_A, SHA_B), gp.REF),
    (("log", "-1", "--format=%H"), gp.REF),
    (("rev-list", "--count"), gp.REF),
    (("for-each-ref", "--format=%(refname)", "refs/atc/integration-candidates"), gp.REF),
    (("status", "--porcelain", "--untracked-files=no"), gp.REF),
    (("branch", "--show-current"), gp.REF),
    (("worktree", "list", "--porcelain"), gp.REF),
    (("rev-parse", "--show-toplevel"), gp.REPOSITORY),
    (("rev-parse", "--path-format=absolute", "--git-common-dir"), gp.REPOSITORY),
    (("rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"), gp.REPOSITORY),
    (("remote",), gp.REPOSITORY),
    (("cat-file", "--batch"), gp.READ),
    (("rev-list", "--ignore-missing", "--stdin"), gp.READ),
    (("diff-tree", "--stdin", "-p"), gp.READ),
    (("patch-id", "--stable"), gp.READ),
    (("update-ref", "refs/x", SHA_A), None),
    (("merge", "--ff-only", SHA_A), None),
    (("fetch", "--quiet", "origin"), None),
    (("worktree", "add", "--detach", "x", SHA_A), None),
    (("branch", "-f", "topic", SHA_A), None),
    (("commit-tree", SHA_A, "-p", SHA_B, "-m", "m"), None),
    (("remote", "add", "origin", "x"), None),
])
def test_the_memo_tier_of_each_form(args: tuple[str, ...], expected: str | None) -> None:
    assert gp.tier(args) == expected


@pytest.mark.integration
def test_without_a_memo_every_query_runs(repo: Path, ran: list) -> None:
    gp.rev(repo, "main")
    gp.rev(repo, "main")
    assert len(ran) == 2


@pytest.mark.integration
@pytest.mark.parametrize("query", [
    lambda repo: gp.rev(repo, gp.rev(repo, "main")),
    lambda repo: gp.git(repo, "rev-parse", "--show-toplevel"),
    lambda repo: gp.rev(repo, "main"),
])
def test_a_memo_runs_each_query_once(repo: Path, ran: list, query) -> None:
    with gp.memo():
        first = query(repo)
        count = len(ran)
        assert query(repo) == first
    assert len(ran) == count


@pytest.mark.integration
def test_the_memo_ends_with_its_block(repo: Path, ran: list) -> None:
    with gp.memo():
        gp.rev(repo, "main")
    gp.rev(repo, "main")
    assert len(ran) == 2


@pytest.mark.integration
def test_a_write_drops_the_ref_answers_and_keeps_the_others(repo: Path, ran: list) -> None:
    main = run_git(repo, "rev-parse", "main")
    with gp.memo():
        gp.rev(repo, "main")
        gp.rev(repo, main)
        gp.git(repo, "rev-parse", "--show-toplevel")
        gp.git(repo, "tag", "marker")
        ran.clear()
        gp.rev(repo, "main")
        gp.rev(repo, main)
        gp.git(repo, "rev-parse", "--show-toplevel")
    assert ran == [("rev-parse", "--verify", "--quiet", "main^{commit}")]


@pytest.mark.integration
def test_a_streamed_command_drops_the_ref_answers(repo: Path, ran: list) -> None:
    with gp.memo():
        gp.rev(repo, "main")
        gp.git_streamed(repo, "tag", "marker", out=io.StringIO())
        ran.clear()
        gp.rev(repo, "main")
    assert len(ran) == 1


@pytest.mark.integration
def test_a_stdin_read_runs_every_time(repo: Path, ran: list) -> None:
    main = run_git(repo, "rev-parse", "main")
    ran.clear()
    with gp.memo():
        gp.rev(repo, "main")
        gp.git(repo, "cat-file", "--batch", input=f"{main}\n")
        gp.git(repo, "cat-file", "--batch", input=f"{main}\n")
        gp.rev(repo, "main")
    assert len(ran) == 3


@pytest.mark.integration
def test_memo_invalidate_drops_the_ref_answers(repo: Path, ran: list) -> None:
    gp.memo_invalidate()  # no memo: nothing to drop
    with gp.memo():
        gp.rev(repo, "main")
        gp.memo_invalidate()
        gp.rev(repo, "main")
    assert len(ran) == 2


@pytest.mark.integration
def test_a_missing_object_is_asked_again_after_a_write(repo: Path, ran: list) -> None:
    with gp.memo():
        assert gp.rev(repo, SHA_A) is None
        assert gp.rev(repo, SHA_A) is None
        assert len(ran) == 1
        gp.git(repo, "tag", "marker")
        gp.rev(repo, SHA_A)
    assert len(ran) == 3


@pytest.mark.integration
def test_an_is_ancestor_no_is_kept_across_a_write(repo: Path, ran: list) -> None:
    first = run_git(repo, "rev-parse", "main~1")
    main = run_git(repo, "rev-parse", "main")
    with gp.memo():
        assert gp.is_ancestor(repo, main, first) is False
        gp.git(repo, "tag", "marker")
        ran.clear()
        assert gp.is_ancestor(repo, main, first) is False
    assert ran == []


@pytest.mark.integration
def test_a_fresh_read_asks_git_and_keeps_its_answer(repo: Path, ran: list) -> None:
    with gp.memo():
        before = gp.rev(repo, "main")
        after = commit(repo, "c.txt", "c\n")
        assert gp.rev(repo, "main") == before
        assert gp.rev(repo, "main", fresh=True) == after
        ran.clear()
        assert gp.rev(repo, "main") == after
    assert ran == []


@pytest.mark.integration
def test_a_different_git_environment_is_a_different_answer(repo: Path, ran: list) -> None:
    with gp.memo():
        gp.git(repo, "rev-parse", "--show-toplevel")
        gp.git(repo, "rev-parse", "--show-toplevel", extra_env={"GIT_TRACE": "0"})
    assert len(ran) == 2


@pytest.mark.integration
def test_a_failed_repository_query_is_asked_again(tmp_path: Path, ran: list) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    ceiling = {"GIT_CEILING_DIRECTORIES": str(tmp_path)}
    with gp.memo():
        assert gp.git(outside, "rev-parse", "--show-toplevel", check=False, extra_env=ceiling).returncode
        assert gp.git(outside, "rev-parse", "--show-toplevel", check=False, extra_env=ceiling).returncode
    assert len(ran) == 2


@pytest.mark.integration
def test_the_other_git_wrappers_share_the_memo(repo: Path, ran: list) -> None:
    import integration_slot
    import task_records
    with gp.memo():
        task_records.main_prefix(repo)
        task_records.main_prefix(repo)
        integration_slot.common_dir(repo)
        integration_slot.common_dir(repo)
        assert len(ran) == 2
        task_records.git(repo, "tag", "marker")
        task_records.main_prefix(repo)
    assert len(ran) == 4


@pytest.mark.integration
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # Windows decodes in a thread
def test_each_wrapper_keeps_its_own_decoding(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import task_records
    (repo / "bin").write_bytes(b"\xff\n")
    blob = run_git(repo, "hash-object", "-w", "bin")
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name)  # both wrappers then pass the same git environment
    with gp.memo():
        assert task_records.git(repo, "show", blob).stdout == "�\n"
        try:
            strict = gp.git(repo, "show", blob).stdout
        except UnicodeDecodeError:
            strict = None
    assert strict != "�\n"

"""One `atc integrate` asks git each question once, and its drift checks still read git (docs/design/ATC-0072.md
§ 3, § 5)."""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

import git_integrate as gi
import git_preflight as gp
from test_git_integrate import (  # noqa: F401  (repo is a fixture)
    commit, declare_verifier, integrate, main_tip, publish, refusal, repo, run_git, task_id, topic, verify,
)
from test_integrate_task import on_branch, record, run, tasks_repo  # noqa: F401  (tasks_repo is a fixture)

pytestmark = pytest.mark.integration

# The reads `integrate` makes with `fresh=True` (the drift checks) and the ones that follow its own writes.
FRESH = {
    ("rev-parse", "--verify", "--quiet", "main^{commit}"),
    ("rev-parse", "--verify", "--quiet", "HEAD^{commit}"),
    ("rev-parse", "--verify", "--quiet", "topic^{commit}"),
    ("status", "--porcelain", "--untracked-files=no"),
}
# The git processes allowed to bypass `gp.run_git`, all read-only (§ 3.1).
BYPASS = {"read_blob_bytes": ("cat-file",), "_checkout_digest": ("cat-file",), "project_of": ("rev-parse",),
          "common_directory": ("rev-parse",)}


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[str, ...], int | None]]:
    """Every git process the command starts: its repository, argv after `-C <repo>`, and the memo's generation."""
    calls: list[tuple[str, tuple[str, ...], int | None]] = []
    real = subprocess.run

    def counting(argv, *args, **kwargs):
        if argv and argv[0] == "git" and argv[1:2] == ["-C"]:
            current = gp._MEMO.get()
            calls.append((str(argv[2]), tuple(argv[3:]), None if current is None else current.generation))
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    return calls


def repeats(calls: list[tuple[str, tuple[str, ...], int | None]]) -> list[tuple[str, ...]]:
    """The memo-tier queries asked twice within one generation, other than the fresh reads."""
    seen = Counter((repo, args, generation) for repo, args, generation in calls
                   if generation is not None and gp.tier(args) in (gp.OBJECT, gp.REPOSITORY, gp.REF))
    return sorted({args for (_repo, args, _gen), count in seen.items() if count > 1 and args not in FRESH})


def test_one_integration_asks_each_question_once(repo: Path, asked: list) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    asked.clear()
    assert integrate(repo, "topic", tip) == 0
    assert repeats(asked) == []
    assert all(generation is not None for _repo, _args, generation in asked)
    record_reads = [args for _repo, args, _gen in asked if args == ("show", f"{tip}:.atc/tasks/{task_id('topic')}.yaml")]
    assert len(record_reads) == 1
    # Once per wrapper: `integration_slot` and `git_preflight` pass different environments.
    common = [args for _repo, args, _gen in asked if args == ("rev-parse", "--path-format=absolute", "--git-common-dir")]
    assert len(common) == 2


def test_one_check_asks_each_question_once(repo: Path, asked: list) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    asked.clear()
    assert integrate(repo, "topic", tip, "--check") == 0
    assert repeats(asked) == []
    assert all(generation is not None for _repo, _args, generation in asked)


def test_one_candidate_run_asks_each_question_once(repo: Path, asked: list) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    asked.clear()
    assert verify(repo, "topic", tip) == 0
    assert repeats(asked) == []
    assert all(generation is not None for _repo, _args, generation in asked)


def test_a_check_reads_main_anew_after_resolve(repo: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    real = gi.resolve
    moved = []

    def resolve_then_advance(*args):
        target = real(*args)
        moved.append(commit(repo, "c.txt", "c\n"))
        return target

    monkeypatch.setattr(gi, "resolve", resolve_then_advance)
    integrate(repo, "topic", tip, "--check", "--json")
    assert json.loads(capsys.readouterr().out)["preflight"]["evidence"]["main_tip"] == moved[0]


def test_only_read_only_git_bypasses_the_memo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    bypassed: list[tuple[str, tuple[str, ...]]] = []
    real = subprocess.Popen

    class Recording(real):  # type: ignore[misc, valid-type]
        def __init__(self, argv, *args, **kwargs):
            if isinstance(argv, (list, tuple)) and argv and Path(str(argv[0])).stem == "git":
                frames, frame = [], sys._getframe(1)
                while frame is not None:
                    frames.append(frame.f_code.co_name)
                    frame = frame.f_back
                if "run_git" not in frames:
                    caller = next((name for name in frames if name in BYPASS), "?")
                    words = [str(word) for word in argv[1:]]
                    bypassed.append((caller, tuple(words[2:] if words[:1] == ["-C"] else words)))
            super().__init__(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    assert verify(repo, "topic", tip) == 0
    assert integrate(repo, "topic", tip) == 0
    assert [(caller, args) for caller, args in bypassed
            if caller not in BYPASS or args[:1] != BYPASS[caller]] == []


def test_a_forgotten_candidate_drops_the_ref_answers(repo: Path, tmp_path: Path, asked: list) -> None:
    with gp.memo():
        gp.rev(repo, "main")
        assert gi._remove_candidate(repo, tmp_path / "gone") is None
        gp.rev(repo, "main")
    assert [args for _repo, args, _gen in asked].count(("rev-parse", "--verify", "--quiet", "main^{commit}")) == 2


def test_main_moved_while_waiting_for_the_slot_is_ref_drift(repo: Path, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    publish(repo, "topic")
    real = gi.IntegrationSlot
    moved = []

    class AdvancingSlot(real):  # type: ignore[misc, valid-type]
        def __enter__(self):
            entered = super().__enter__()
            moved.append(commit(repo, "c.txt", "c\n"))
            return entered

    monkeypatch.setattr(gi, "IntegrationSlot", AdvancingSlot)
    assert integrate(repo, "topic", tip) == 1
    first, _ = refusal(capsys.readouterr().err)
    assert first.startswith("STOP REF_DRIFT: main moved from ") and "whose verification section was read" in first
    assert main_tip(repo) == moved[0]


def remembered(repo: Path, *refs: str, status: bool = False) -> None:
    """Put the answers a drift check reads into the memo's current generation, as if the run had just read them,
    so only the check's `fresh=True` makes it see a change made outside `gp.run_git` afterwards."""
    for ref in refs:
        gp.rev(repo, ref)
    if status:
        gp.git(repo, "status", "--porcelain", "--untracked-files=no")


def move_main_only(repo: Path) -> None:
    run_git(repo, "switch", "-q", "--detach")
    run_git(repo, "update-ref", "refs/heads/main", "main~1")


@pytest.mark.parametrize("change", [move_main_only, lambda repo: run_git(repo, "switch", "-q", "--detach", "main~1")],
                         ids=["main", "head"])
def test_a_move_after_preflight_is_ref_drift(repo: Path, monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str], change) -> None:
    tip = topic(repo, {"a.txt": "a2\n"})
    real = gi.merge_preflight

    def preflight_then_change(at: Path, branch: str, reviewed: str, main: str):
        report = real(at, branch, reviewed, main)
        remembered(at, main, "HEAD")
        change(repo)
        return report

    monkeypatch.setattr(gi, "merge_preflight", preflight_then_change)
    assert integrate(repo, "topic", tip) == 1
    assert "moved after preflight" in capsys.readouterr().err
    assert main_tip(repo) != tip and run_git(repo, "rev-parse", "HEAD") != tip


@pytest.mark.parametrize(("change", "detail"), [
    (lambda repo: run_git(repo, "branch", "-f", "work", "main"), "source tip changed after candidate verification"),
    (move_main_only, "main drifted after candidate verification"),
    (lambda repo: run_git(repo, "switch", "-q", "--detach", "main~1"), "main drifted after candidate verification"),
    (lambda repo: (repo / "a.txt").write_text("dirty\n", encoding="utf-8"), "integration checkout became dirty"),
], ids=["source-branch", "main", "head", "dirty"])
def test_a_change_before_the_fast_forward_is_ref_drift(tasks_repo: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str], change, detail: str) -> None:
    tip = on_branch(tasks_repo, "work", {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "work")})
    real = gi._step

    def step(steps, name, start=None):
        if name == "fast-forward":
            run_locals = sys._getframe(1).f_locals  # `integrate`, the caller
            remembered(run_locals["repo"], run_locals["branch"], run_locals["main"], "HEAD", status=True)
            change(tasks_repo)
        return real(steps, name, start)

    monkeypatch.setattr(gi, "_step", step)
    assert run(tasks_repo, tip) == 3
    first, _ = refusal(capsys.readouterr().err)
    assert first == f"STOP REF_DRIFT: {detail}; main unchanged"
    assert main_tip(tasks_repo) != tip


@pytest.mark.parametrize(("script", "detail"), [
    ("import subprocess; subprocess.run(['git','-c','user.name=t','-c','user.email=t@example.com','-c','commit.gpgsign=false','commit','-q',"
     "'--allow-empty','-m','moved'],check=True)", "verification command changed candidate HEAD"),
    ("open('a.txt','w').write('dirty\\n')", "verification command dirtied candidate checkout"),
], ids=["head", "dirty"])
def test_a_verifier_that_changes_the_candidate_is_caught(repo: Path, capsys: pytest.CaptureFixture[str],
                                                         script: str, detail: str) -> None:
    declare_verifier(repo, json.dumps([sys.executable, "-c", script]))
    before = main_tip(repo)
    tip = topic(repo, {"a.txt": "a2\n"})
    assert integrate(repo, "topic", tip) == 3
    first, _ = refusal(capsys.readouterr().err)
    assert first.startswith("STOP CANDIDATE_CHANGED: ") and detail in first
    assert main_tip(repo) == before

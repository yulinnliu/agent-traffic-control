"""`atc task new` and `atc task list` against live Git repositories (`docs/TASKS.md`).

`atc task check` is covered by `shared/conformance/task-records/`.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

import task_records

pytestmark = pytest.mark.integration

PROJECT = "demo"
GOVERNANCE_SOURCE = "example/governance"
PINNED = "a" * 40
TASKS_DIR = ".atc/tasks"  # the record directory; select_tests.py selects this file by it


def run_git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env,
                          check=True).stdout.strip()


def commit(repo: Path, files: dict[str, str]) -> str:
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        run_git(repo, "add", "--", relative)
    run_git(repo, "commit", "-q", "-m", "edit")
    return run_git(repo, "rev-parse", "HEAD")


def agents(prefix: str) -> str:
    return f"# AGENTS.md\n\n## Task records\n\n- Task prefix: {prefix}\n\nProse after the line.\n"


def record(task_id: str, branch: str, scope: str = "plan § 1", **extra: object) -> str:
    data = {"task_id": task_id, "repository": "example/demo", "base_sha": PINNED, "scope": scope,
            "governance_source": GOVERNANCE_SOURCE, "governance_revision": PINNED, "branch": branch,
            "authority": {"edit": True, "commit": True, "push_topic": False, "integrate_main": False,
                          "push_main": False, "deploy": False}, **extra}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "projects" / PROJECT

    def worktree(self, repo: Path, name: str, branch: str) -> Path:
        path = self.root / "worktrees" / (PROJECT if repo == self.repo else "root") / name
        run_git(repo, "worktree", "add", "-q", "-b", branch, str(path), "main")
        return path

    def integrate(self, repo: Path, **line: object) -> None:
        common = Path(run_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        path = common / "atc" / "integrations.jsonl"
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")

    def cli(self, capsys: pytest.CaptureFixture[str], *argv: str, cwd: Path) -> tuple[int, str, str]:
        status = task_records.main(list(argv), root=self.root, environ={}, cwd=str(cwd))
        captured = capsys.readouterr()
        return status, captured.out, captured.err

    def listing(self, capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, dict]:
        status, out, err = self.cli(capsys, "list", "--project", PROJECT, "--json", *argv, cwd=self.root)
        assert status == 0, err
        return {row["task_id"]: row for row in json.loads(out)["tasks"]}


@pytest.fixture(autouse=True)
def git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The identity of the backlog commits the tool itself writes."""
    for key, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.com"),
                       ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(key, value)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    root = tmp_path / "workspace"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit(root, {"AGENTS.md": agents("ATC"), ".gitignore": "workspace.yaml\nprojects/\nworktrees/\n"})
    (root / "workspace.yaml").write_text(
        f"version: 1\nworkspace_root: '{root}'\ngovernance_source: {GOVERNANCE_SOURCE}\nprojects:\n"
        f"  {PROJECT}:\n    repo: projects/{PROJECT}\n    worktrees: worktrees/{PROJECT}\n    state: state/{PROJECT}-data\n",
        encoding="utf-8")
    workspace = Workspace(root)
    workspace.repo.mkdir(parents=True)
    run_git(workspace.repo, "init", "-q", "-b", "main")
    commit(workspace.repo, {"AGENTS.md": agents("DEMO"), ".atc/atc.lock": LOCK})
    return workspace


LOCK = f'version = 1\nsource = "{GOVERNANCE_SOURCE}"\nrevision = "{PINNED}"\n'
LEGACY = f"managed-by: agent-traffic-control\nsource: {GOVERNANCE_SOURCE}\nrevision: '{'b' * 40}'\n"


def bound(ws: Workspace, capsys: pytest.CaptureFixture[str], files: dict[str, str | None]) -> tuple[int, str]:
    """`task new` in a topic whose `.atc/` holds ``files`` (``None`` deletes one); the revision it binds or the refusal."""
    topic = ws.worktree(ws.repo, "claude+widget", "topic-widget")
    for name, content in files.items():
        if content is None:
            (topic / name).unlink()
        else:
            (topic / name).parent.mkdir(parents=True, exist_ok=True)
            (topic / name).write_text(content, encoding="utf-8")
    status, out, err = ws.cli(capsys, "new", "--scope", "s", cwd=topic)
    if status:
        return status, err
    return status, yaml.safe_load((topic / TASKS_DIR / "DEMO-0001.yaml").read_text(encoding="utf-8"))["governance_revision"]


def test_new_binds_the_lock_over_the_previous_layouts_pin(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    assert bound(ws, capsys, {".atc/governance.yaml": LEGACY}) == (0, PINNED)


def test_new_falls_back_to_a_marked_previous_layout_pin_without_a_lock(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    assert bound(ws, capsys, {".atc/atc.lock": None, ".atc/governance.yaml": LEGACY}) == (0, "b" * 40)


@pytest.mark.parametrize("files, detail", [
    ({".atc/atc.lock": None, ".atc/governance.yaml": LEGACY.replace("managed-by: agent-traffic-control\n", "")}, "atc.lock is missing"),
    ({".atc/atc.lock": None}, "atc.lock is missing"),
    ({".atc/atc.lock": 'version = 2\nsource = "a/b"\nrevision = "c"\n'}, "version 2 is not 1"),
])
def test_new_refuses_without_a_readable_binding(ws: Workspace, capsys: pytest.CaptureFixture[str], files: dict, detail: str) -> None:
    status, err = bound(ws, capsys, files)
    assert status == 1 and "STOP NO_GOVERNANCE_BINDING:" in err and detail in err


# new

def test_new_writes_a_valid_record_bound_to_the_topic(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.repo, "claude+widget", "topic-widget")
    status, out, err = ws.cli(capsys, "new", "--scope", "plan § 2: the widget", cwd=topic)
    assert status == 0, err
    assert out.startswith("CREATED DEMO-0001 ")
    path = topic / TASKS_DIR / "DEMO-0001.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data == {
        "task_id": "DEMO-0001", "repository": PROJECT,
        "repository_identity_status": "no origin remote; local repository name",
        "base_sha": run_git(ws.repo, "rev-parse", "main"), "scope": "plan § 2: the widget",
        "governance_source": GOVERNANCE_SOURCE, "governance_revision": PINNED, "branch": "topic-widget",
        "authority": {"edit": True, "commit": True, "push_topic": False, "integrate_main": False,
                      "push_main": False, "deploy": False},
    }
    assert task_records.problems(topic) == []


def test_new_takes_the_repository_from_origin(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    run_git(ws.repo, "remote", "add", "origin", "git@github.com:example/demo.git")
    topic = ws.worktree(ws.repo, "claude+widget", "topic-widget")
    assert ws.cli(capsys, "new", "--scope", "s", "--project", PROJECT, cwd=topic)[0] == 0
    data = yaml.safe_load((topic / TASKS_DIR / "DEMO-0001.yaml").read_text(encoding="utf-8"))
    assert data["repository"] == "example/demo"
    assert "repository_identity_status" not in data


def test_new_numbers_past_every_branch_remote_ref_and_integration(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    commit(ws.repo, {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "old")})
    other = ws.worktree(ws.repo, "claude+other", "other")
    commit(other, {".atc/tasks/DEMO-0004.yaml": record("DEMO-0004", "other")})
    run_git(ws.repo, "update-ref", "refs/remotes/origin/far", "other")
    topic = ws.worktree(ws.repo, "claude+widget", "topic-widget")
    assert ws.cli(capsys, "new", "--scope", "s", cwd=topic)[1].startswith("CREATED DEMO-0005 ")
    ws.integrate(ws.repo, candidate_sha=PINNED, task="DEMO-0009")
    second = ws.worktree(ws.repo, "claude+second", "topic-second")
    assert ws.cli(capsys, "new", "--scope", "s", cwd=second)[1].startswith("CREATED DEMO-0010 ")


def test_new_counts_a_number_only_a_remote_tracking_branch_holds(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    other = ws.worktree(ws.repo, "claude+other", "other")
    commit(other, {".atc/tasks/DEMO-0007.yaml": record("DEMO-0007", "other")})
    run_git(ws.repo, "update-ref", "refs/remotes/origin/other", "other")
    run_git(ws.repo, "worktree", "remove", "--force", str(other))
    run_git(ws.repo, "branch", "-D", "other")
    topic = ws.worktree(ws.repo, "claude+widget", "topic-widget")
    assert ws.cli(capsys, "new", "--scope", "s", cwd=topic)[1].startswith("CREATED DEMO-0008 ")


def test_new_refuses_on_main(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status, _, err = ws.cli(capsys, "new", "--scope", "s", cwd=ws.repo)
    assert status == 1
    assert err.startswith("STOP ON_MAIN: ")
    assert not (ws.repo / TASKS_DIR).exists()


def test_new_refuses_in_the_canonical_checkout_on_any_branch(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    run_git(ws.root, "switch", "-q", "-c", "topic-in-place")
    status, _, err = ws.cli(capsys, "new", "--scope", "s", "--project", "root", cwd=ws.root)
    assert status == 1
    assert err.startswith("STOP CHECKOUT_CANONICAL: ")
    assert "Run it inside the new worktree: atc task new --project <name> --scope <text>, root for the workspace root" in err


def test_new_takes_project_root_from_a_root_worktree(
        ws: Workspace, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    topic = ws.worktree(ws.root, "claude+relative", "topic-relative")
    monkeypatch.chdir(topic)
    with pytest.raises(SystemExit) as exc:
        ws.cli(capsys, "new", "--scope", "s", "--repo", ".", cwd=topic)
    assert exc.value.code == 2
    status, _, err = ws.cli(capsys, "new", "--scope", "s", "--project", "nope", cwd=topic)
    assert status == 2 and err.splitlines()[-1].startswith("ERROR UNKNOWN_PROJECT nope")


def test_new_in_a_root_worktree_binds_the_root_main(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.root, "claude+contract", "topic-contract")
    status, _, err = ws.cli(capsys, "new", "--scope", "s", cwd=topic)
    assert status == 0, err
    data = yaml.safe_load((topic / TASKS_DIR / "ATC-0001.yaml").read_text(encoding="utf-8"))
    assert (data["governance_source"], data["governance_revision"]) == (GOVERNANCE_SOURCE, run_git(ws.root, "rev-parse", "main"))


def test_new_refuses_a_worktree_of_another_project(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.root, "claude+contract", "topic-contract")
    status, _, err = ws.cli(capsys, "new", "--scope", "s", "--project", PROJECT, cwd=topic)
    assert status == 1
    assert err.startswith("STOP WRONG_REPOSITORY: ")


# list

def test_list_derives_each_status(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    main_records = {
        ".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "t-closed", closure={"state": "closed", "note": "done"}),
        ".atc/tasks/DEMO-0003.yaml": record("DEMO-0003", "t-integrated"),
        ".atc/tasks/DEMO-0004.yaml": record("DEMO-0004", "t-bound"),
        ".atc/tasks/DEMO-0005.yaml": record("DEMO-0005", "t-unknown"),
    }
    bound_at = commit(ws.repo, main_records)
    ws.integrate(ws.repo, candidate_sha=bound_at, task="DEMO-0004", records_only=True, main_before=PINNED)
    integrated = ws.worktree(ws.repo, "claude+integrated", "t-integrated")
    done = commit(integrated, {"work.txt": "done\n"})
    run_git(ws.repo, "merge", "-q", "--ff-only", "t-integrated")
    ws.integrate(ws.repo, candidate_sha=done, task="DEMO-0003")
    opened = ws.worktree(ws.repo, "claude+open", "t-open")
    commit(opened, {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "t-open")})
    abandoned = ws.worktree(ws.repo, "claude+abandoned", "t-abandoned")
    commit(abandoned, {".atc/tasks/DEMO-0006.yaml": record("DEMO-0006", "t-abandoned",
                                                           closure={"state": "abandoned", "note": "dropped"})})

    rows = ws.listing(capsys)
    assert {task_id: row["status"] for task_id, row in rows.items()} == {
        "DEMO-0001": "closed", "DEMO-0002": "open", "DEMO-0003": "integrated",
        "DEMO-0004": "bound", "DEMO-0005": "unknown", "DEMO-0006": "abandoned",
    }
    assert rows["DEMO-0002"]["branch"] == "t-open"
    assert rows["DEMO-0004"]["authority"]["edit"] is True
    assert set(ws.listing(capsys, "--status", "bound")) == {"DEMO-0004"}


def test_list_reads_unknown_for_a_record_on_main_without_integrations(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    commit(ws.repo, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "gone")})
    assert ws.listing(capsys)["DEMO-0001"]["status"] == "unknown"


def test_list_shows_main_authority_and_the_branch_copy_for_the_rest(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    commit(ws.repo, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "t-step", scope="first scope")})
    step = ws.worktree(ws.repo, "claude+step", "t-step")
    granted = yaml.safe_load(record("DEMO-0001", "t-step", scope="second scope"))
    granted["authority"]["integrate_main"] = True
    granted["authority_basis"] = "quoted words"
    commit(step, {".atc/tasks/DEMO-0001.yaml": yaml.safe_dump(granted, sort_keys=False)})
    row = ws.listing(capsys)["DEMO-0001"]
    assert (row["status"], row["scope"], row["authority"]["integrate_main"]) == ("open", "second scope", False)


def test_list_marks_one_id_bound_to_two_branches_as_conflict(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    for branch in ("t-one", "t-two"):
        topic = ws.worktree(ws.repo, f"claude+{branch}", branch)
        commit(topic, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", branch)})
    assert ws.listing(capsys)["DEMO-0001"]["conflict"] is True
    status, out, _ = ws.cli(capsys, "list", "--project", PROJECT, cwd=ws.root)
    assert status == 0
    assert out.rstrip().endswith("CONFLICT")


def test_list_human_line_and_all(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    long_scope = "x" * 70
    commit(ws.repo, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "gone", scope=long_scope)})
    commit(ws.root, {".atc/tasks/ATC-0001.yaml": record("ATC-0001", "gone-too")})
    status, out, _ = ws.cli(capsys, "list", "--project", PROJECT, cwd=ws.root)
    assert (status, out.strip()) == (0, f"DEMO-0001  unknown  gone  {'x' * 60}")
    status, out, _ = ws.cli(capsys, "list", "--all", cwd=ws.root)
    assert status == 0
    assert [line.split()[:2] for line in out.splitlines()] == [["root", "ATC-0001"], [PROJECT, "DEMO-0001"]]


def test_list_folds_invalid_record_warnings_per_repository(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    commit(ws.repo, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "gone"),
                     ".atc/tasks/DEMO-0002.yaml": "- not\n- a mapping\n",
                     ".atc/tasks/DEMO-0003.yaml": "just a string\n"})
    status, out, err = ws.cli(capsys, "list", "--all", cwd=ws.root)
    assert status == 0
    assert err.splitlines() == [f"WARNING INVALID_RECORD {PROJECT}: 2 record(s) are not a YAML mapping; atc task check {PROJECT}"]
    status, out, err = ws.cli(capsys, "list", "--project", PROJECT, "--json", cwd=ws.root)
    assert err.count("WARNING INVALID_RECORD main:.atc/tasks/DEMO-000") == 2
    assert [row["task_id"] for row in json.loads(out)["tasks"]] == ["DEMO-0001"]


def test_list_infers_the_project_from_a_worktree(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.repo, "claude+widget", "topic-widget")
    commit(topic, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "topic-widget")})
    status, out, _ = ws.cli(capsys, "list", cwd=topic)
    assert status == 0
    assert out.startswith("DEMO-0001  open  topic-widget")


def test_new_takes_base_sha_from_the_merge_base_not_the_main_tip(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    fork = run_git(ws.repo, "rev-parse", "main")
    topic = ws.worktree(ws.repo, "claude+widget", "topic-widget")
    moved = commit(ws.repo, {"later.txt": "later\n"})
    assert ws.cli(capsys, "new", "--scope", "s", cwd=topic)[0] == 0
    data = yaml.safe_load((topic / TASKS_DIR / "DEMO-0001.yaml").read_text(encoding="utf-8"))
    assert (data["base_sha"], moved != fork) == (fork, True)


def test_list_reads_unknown_for_an_integration_whose_candidate_is_off_main(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    commit(ws.repo, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "t-lost")})
    lost = ws.worktree(ws.repo, "claude+lost", "t-lost")
    candidate = commit(lost, {"work.txt": "never on main\n"})
    run_git(ws.repo, "worktree", "remove", "--force", str(lost))
    run_git(ws.repo, "branch", "-D", "t-lost")
    ws.integrate(ws.repo, candidate_sha=candidate, task="DEMO-0001")
    assert ws.listing(capsys)["DEMO-0001"]["status"] == "unknown"


def test_a_broken_schema_fails_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jsonschema.exceptions import SchemaError
    broken = tmp_path / "task.schema.json"
    broken.write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": 5}), encoding="utf-8")
    monkeypatch.setattr(task_records, "SCHEMA", broken)
    (tmp_path / "AGENTS.md").write_text(agents("DEMO"), encoding="utf-8")
    (tmp_path / TASKS_DIR).mkdir(parents=True)
    (tmp_path / TASKS_DIR / "DEMO-0001.yaml").write_text(record("DEMO-0001", "b"), encoding="utf-8")
    with pytest.raises(SchemaError):
        task_records.problems(tmp_path)


# --project root and the prefix of a record --task


def test_project_root_names_the_workspace_root(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    commit(ws.root, {".atc/tasks/ATC-0001.yaml": record("ATC-0001", "gone")})
    status, out, err = ws.cli(capsys, "list", "--project", "root", "--json", cwd=ws.root)
    assert status == 0, err
    assert [(row["project"], row["task_id"]) for row in json.loads(out)["tasks"]] == [("root", "ATC-0001")]
    import managed_worktree
    assert managed_worktree.main(["audit", "--project", "root"], root=ws.root, environ={},
                              home=str(ws.root)) == 0
    assert capsys.readouterr().out.strip() == "OK root/ (no worktrees) - continue"


def test_a_record_task_names_its_repository_by_prefix(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    import git_integrate
    monkeypatch.delenv("ATC_ROOT", raising=False)

    def resolved(task: str) -> tuple[str, Path]:
        args = git_integrate.build_parser().parse_args(["--reviewed", "a" * 40, "--task", task, "--owner-words", "w"])
        return git_integrate.resolve_repo(args, ws.root, str(ws.root))

    assert resolved("DEMO-0001") == (PROJECT, ws.repo)
    assert resolved("ATC-0001") == ("root", ws.root)
    for other in ("ZZZ-0001", "plan § 1 step 2"):
        with pytest.raises(git_integrate.Refused) as refused:
            resolved(other)
        assert refused.value.token == "USAGE"


# backlog (docs/TASKS.md § Backlog)

def backlog(ws: Workspace) -> dict[str, dict]:
    """The records on the backlog branch of the demo project."""
    listing = run_git(ws.repo, "ls-tree", "--name-only", "backlog", f"{TASKS_DIR}/")
    return {Path(p).stem: yaml.safe_load(run_git(ws.repo, "show", f"backlog:{p}")) for p in listing.splitlines()}


def plan(ws: Workspace, capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    status, out, err = ws.cli(capsys, "plan", "--project", PROJECT, *argv, cwd=ws.root)
    assert status == 0, err
    assert out.startswith("PLANNED ")
    return out.split()[1]


def test_plan_commits_a_record_that_grants_nothing_to_the_backlog_only(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    head = run_git(ws.repo, "rev-parse", "main")
    task_id = plan(ws, capsys, "--scope", "the gadget", "--rank", "20", "--notes", "after the widget")
    assert task_id == "DEMO-0001"
    assert backlog(ws) == {"DEMO-0001": {
        "task_id": "DEMO-0001", "repository": PROJECT,
        "repository_identity_status": "no origin remote; local repository name", "scope": "the gadget",
        "governance_source": GOVERNANCE_SOURCE,
        "authority": {"edit": False, "commit": False, "push_topic": False, "integrate_main": False,
                      "push_main": False, "deploy": False},
        "rank": 20, "notes": "after the widget"}}
    assert run_git(ws.repo, "rev-parse", "main") == head
    assert run_git(ws.repo, "status", "--porcelain") == ""
    assert run_git(ws.repo, "rev-list", "--count", "backlog") == "1"


def test_list_orders_planned_tasks_by_rank_and_marks_unmet_prerequisites(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    first = plan(ws, capsys, "--scope", "unranked")
    second = plan(ws, capsys, "--scope", "second", "--rank", "20", "--after", first)
    third = plan(ws, capsys, "--scope", "first", "--rank", "10")
    status, out, err = ws.cli(capsys, "list", "--project", PROJECT, "--status", "planned", "--json", cwd=ws.root)
    assert status == 0, err
    rows = json.loads(out)["tasks"]
    assert [row["task_id"] for row in rows] == [third, second, first]
    assert {row["task_id"]: row["blocked_by"] for row in rows} == {third: [], second: [first], first: []}
    assert all(row["status"] == "planned" and row["branch"] is None for row in rows)
    assert ws.cli(capsys, "rank", second, "5", "--project", PROJECT, cwd=ws.root)[0] == 0
    status, out, _ = ws.cli(capsys, "list", "--project", PROJECT, "--status", "planned", cwd=ws.root)
    assert out.splitlines()[0] == f"{second}  planned  -  second  rank 5  BLOCKED by {first}"


def test_start_moves_the_planned_record_into_the_topic_and_off_the_backlog(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    task_id = plan(ws, capsys, "--scope", "the gadget", "--rank", "10", "--notes", "n")
    topic = ws.worktree(ws.repo, "claude+gadget", "topic-gadget")
    status, out, err = ws.cli(capsys, "start", task_id, "--scope", "the gadget, narrowed", cwd=topic)
    assert status == 0, err
    assert out.startswith(f"STARTED {task_id} ")
    data = yaml.safe_load((topic / TASKS_DIR / f"{task_id}.yaml").read_text(encoding="utf-8"))
    assert data["branch"] == "topic-gadget"
    assert data["base_sha"] == run_git(ws.repo, "rev-parse", "main")
    assert data["governance_revision"] == PINNED
    assert data["scope"] == "the gadget, narrowed"
    assert data["authority"] == {"edit": True, "commit": True, "push_topic": False, "integrate_main": False,
                                 "push_main": False, "deploy": False}
    assert data["notes"] == "n" and "rank" not in data
    assert task_records.problems(topic) == []
    assert backlog(ws) == {}
    run_git(topic, "add", TASKS_DIR)
    run_git(topic, "commit", "-q", "-m", "record")
    assert ws.listing(capsys)[task_id]["status"] == "open"


def test_an_id_stays_taken_once_started_or_dropped(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    started = plan(ws, capsys, "--scope", "a")
    dropped = plan(ws, capsys, "--scope", "b")
    topic = ws.worktree(ws.repo, "claude+a", "topic-a")
    assert ws.cli(capsys, "start", started, cwd=topic)[0] == 0
    status, out, err = ws.cli(capsys, "drop", dropped, "--note", "Owner dropped it", "--project", PROJECT, cwd=ws.root)
    assert (status, out.strip()) == (0, f"ABANDONED {dropped}"), err
    assert backlog(ws)[dropped]["closure"] == {"state": "abandoned", "note": "Owner dropped it"}
    assert ws.listing(capsys)[dropped]["status"] == "abandoned"
    (topic / TASKS_DIR / f"{started}.yaml").unlink()
    assert plan(ws, capsys, "--scope", "c") == "DEMO-0003"


def test_new_issues_its_id_through_the_backlog(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.repo, "claude+widget", "topic-widget")
    assert ws.cli(capsys, "new", "--scope", "s", cwd=topic)[1].startswith("CREATED DEMO-0001 ")
    assert backlog(ws) == {}
    assert run_git(ws.repo, "log", "--format=%s", "backlog").splitlines() == ["start DEMO-0001 on topic-widget",
                                                                              "plan DEMO-0001"]
    (topic / TASKS_DIR / "DEMO-0001.yaml").unlink()
    assert plan(ws, capsys, "--scope", "next") == "DEMO-0002"


def test_start_and_rank_refuse_a_task_that_is_not_planned(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.repo, "claude+gadget", "topic-gadget")
    status, _, err = ws.cli(capsys, "start", "DEMO-0007", cwd=topic)
    assert status == 1 and err.startswith("STOP NOT_PLANNED: ")
    dropped = plan(ws, capsys, "--scope", "b")
    assert ws.cli(capsys, "drop", dropped, "--note", "n", "--project", PROJECT, cwd=ws.root)[0] == 0
    status, _, err = ws.cli(capsys, "rank", dropped, "3", "--project", PROJECT, cwd=ws.root)
    assert status == 1 and err.startswith("STOP NOT_PLANNED: ")
    planned_id = plan(ws, capsys, "--scope", "c")
    status, _, err = ws.cli(capsys, "start", planned_id, cwd=ws.repo)
    assert status == 1 and err.startswith("STOP ON_MAIN: ")
    assert planned_id in backlog(ws)


def test_plan_refuses_an_unknown_prerequisite(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status, _, err = ws.cli(capsys, "plan", "--project", PROJECT, "--scope", "s", "--after", "DEMO-0042", cwd=ws.root)
    assert status == 2 and err.startswith("ERROR UNKNOWN_TASK ")
    assert run_git(ws.repo, "branch", "--list", "backlog") == ""


def test_a_backlog_write_against_a_moved_tip_retries_on_the_new_tip(ws: Workspace, capsys: pytest.CaptureFixture[str],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    plan(ws, capsys, "--scope", "a")
    stale = run_git(ws.repo, "rev-parse", "backlog")
    plan(ws, capsys, "--scope", "b")
    assert task_records.backlog_commit(ws.repo, stale, "DEMO-0009", None, "late") is False
    real, calls = task_records.backlog_tip, []

    def first_stale(repo: Path) -> str | None:
        calls.append(repo)
        return stale if len(calls) == 1 else real(repo)

    monkeypatch.setattr(task_records, "backlog_tip", first_stale)
    task_records.backlog_write(ws.repo, lambda tip: ("DEMO-0001", None, "remove a"))
    assert len(calls) == 2
    assert set(backlog(ws)) == {"DEMO-0002"}


def test_start_refuses_a_record_the_worktree_already_holds(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    task_id = plan(ws, capsys, "--scope", "a")
    topic = ws.worktree(ws.repo, "claude+a", "topic-a")
    (topic / TASKS_DIR).mkdir(parents=True)
    (topic / TASKS_DIR / f"{task_id}.yaml").write_text("kept\n", encoding="utf-8")
    status, _, err = ws.cli(capsys, "start", task_id, cwd=topic)
    assert status == 1 and err.startswith("STOP RECORD_EXISTS: ")
    assert (topic / TASKS_DIR / f"{task_id}.yaml").read_text(encoding="utf-8") == "kept\n"
    assert task_id in backlog(ws)


def test_a_backlog_write_gives_up_after_every_attempt_meets_a_moved_tip(ws: Workspace, capsys: pytest.CaptureFixture[str],
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []
    monkeypatch.setattr(task_records, "backlog_commit", lambda *args: attempts.append(args) and False)
    status, _, err = ws.cli(capsys, "plan", "--project", PROJECT, "--scope", "s", cwd=ws.root)
    assert status == 1 and err.startswith("STOP BACKLOG_BUSY: ")
    assert len(attempts) == task_records.CAS_ATTEMPTS
    assert run_git(ws.repo, "branch", "--list", "backlog") == ""


@pytest.mark.parametrize("argv", [("--rank", "0"), ("--notes", "")], ids=["rank-0", "empty-notes"])
def test_plan_refuses_a_value_the_schema_refuses(ws: Workspace, capsys: pytest.CaptureFixture[str], argv: tuple[str, str]) -> None:
    status, _, err = ws.cli(capsys, "plan", "--project", PROJECT, "--scope", "s", *argv, cwd=ws.root)
    assert status == 2 and err.startswith("ERROR RECORD_INVALID ")
    assert run_git(ws.repo, "branch", "--list", "backlog") == ""


def test_rank_refuses_zero(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    task_id = plan(ws, capsys, "--scope", "s", "--rank", "10")
    status, _, err = ws.cli(capsys, "rank", task_id, "0", "--project", PROJECT, cwd=ws.root)
    assert status == 2 and err.startswith("ERROR RECORD_INVALID ")
    assert backlog(ws)[task_id]["rank"] == 10


def test_start_undoes_the_written_record_when_the_backlog_removal_fails(
        ws: Workspace, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = plan(ws, capsys, "--scope", "a")
    topic = ws.worktree(ws.repo, "claude+a", "topic-a")
    written = topic / TASKS_DIR / f"{task_id}.yaml"
    seen = []

    def busy(repo: Path, change) -> None:
        seen.append(written.is_file())
        raise task_records.Refused("BACKLOG_BUSY", "moved")

    monkeypatch.setattr(task_records, "backlog_write", busy)
    status, _, err = ws.cli(capsys, "start", task_id, cwd=topic)
    assert status == 1 and err.startswith("STOP BACKLOG_BUSY: ")
    assert seen == [True]
    assert not written.exists()
    assert task_id in backlog(ws)

    def broken(repo: Path, change) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(task_records, "backlog_write", broken)
    status, _, err = ws.cli(capsys, "start", task_id, cwd=topic)
    assert status == 2 and err.startswith("ERROR WRITE_FAILED ")
    assert not written.exists()


def test_start_is_partial_when_the_written_record_cannot_be_undone(
        ws: Workspace, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = plan(ws, capsys, "--scope", "a")
    topic = ws.worktree(ws.repo, "claude+a", "topic-a")
    written = topic / TASKS_DIR / f"{task_id}.yaml"

    def busy(repo: Path, change) -> None:
        raise task_records.Refused("BACKLOG_BUSY", "moved")

    def stuck(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr(task_records, "backlog_write", busy)
    monkeypatch.setattr(Path, "unlink", stuck)
    status, out, err = ws.cli(capsys, "start", task_id, "--json", cwd=topic)
    assert status == 3
    assert err.startswith("STOP START_INCOMPLETE: ")
    assert str(written) in err and "BACKLOG_BUSY" in err
    assert json.loads(out)["refusal"]["verdict"] == "PARTIAL"
    assert written.is_file()
    assert task_id in backlog(ws)


def test_a_dropped_prerequisite_no_longer_blocks_and_is_marked(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    dropped = plan(ws, capsys, "--scope", "dropped")
    waiting = plan(ws, capsys, "--scope", "waiting")
    task_id = plan(ws, capsys, "--scope", "next", "--rank", "1", "--after", dropped, waiting)
    assert ws.cli(capsys, "drop", dropped, "--note", "n", "--project", PROJECT, cwd=ws.root)[0] == 0
    row = ws.listing(capsys, "--status", "planned")[task_id]
    assert (row["blocked_by"], row["after_dropped"]) == ([waiting], [dropped])
    status, out, _ = ws.cli(capsys, "list", "--project", PROJECT, "--status", "planned", cwd=ws.root)
    assert out.splitlines()[0] == f"{task_id}  planned  -  next  rank 1  BLOCKED by {waiting}  after {dropped} (dropped)"

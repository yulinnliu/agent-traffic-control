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


HARNESS = {"CLAUDECODE": "1"}


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
        """A worktree `<a>+<b>` locked as `<a>:<b>`, as `atc task create` leaves it."""
        path = self.root / "worktrees" / (PROJECT if repo == self.repo else "root") / name
        run_git(repo, "worktree", "add", "-q", "-b", branch, str(path), "main")
        run_git(repo, "worktree", "lock", "--reason", name.replace("+", ":", 1), str(path))
        return path

    def integrate(self, repo: Path, **line: object) -> None:
        common = Path(run_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        path = common / "atc" / "integrations.jsonl"
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")

    def cli(self, capsys: pytest.CaptureFixture[str], *argv: str, cwd: Path) -> tuple[int, str, str]:
        status = task_records.main(list(argv), root=self.root, environ=dict(HARNESS), cwd=str(cwd))
        captured = capsys.readouterr()
        return status, captured.out, captured.err

    def listing(self, capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, dict]:
        status, out, err = self.cli(capsys, "list", "--project", PROJECT, "--json", *argv, cwd=self.root)
        assert status == 0, err
        return {row["task_id"]: row for row in json.loads(out)["tasks"]}


@pytest.fixture(autouse=True)
def git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The identity of the commits the tool itself writes."""
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
    commit(workspace.repo, {"AGENTS.md": agents("DEMO"), ".atc/atc.lock": LOCK, "pyproject.toml": TESTKIT})
    return workspace


TESTKIT = '[tool.atc-testkit]\ntimeout = 60\n'  # a project with tests: its records take the standard level
LOCK = f'version = 1\nsource = "{GOVERNANCE_SOURCE}"\nrevision = "{PINNED}"\n'


def created(ws: Workspace, project: str = PROJECT, task: str = "DEMO-0001") -> Path:
    """The worktree `create` makes for ``task`` under the session harness (CLAUDECODE)."""
    return ws.root / "worktrees" / project / f"claude+{task}"


def bound(ws: Workspace, capsys: pytest.CaptureFixture[str], files: dict[str, str | None]) -> tuple[int, str]:
    """`create` from main after ``files`` (``None`` deletes one) are committed to main; the revision it binds or the refusal."""
    for name, content in files.items():
        if content is None:
            run_git(ws.repo, "rm", "-q", "--", name)
        else:
            (ws.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (ws.repo / name).write_text(content, encoding="utf-8")
            run_git(ws.repo, "add", "--", name)
    if files:
        run_git(ws.repo, "commit", "-q", "-m", "binding")
    status, out, err = ws.cli(capsys, "create", "widget", "--scope", "s", cwd=ws.repo)
    if status:
        return status, err
    return status, yaml.safe_load((created(ws) / TASKS_DIR / "DEMO-0001.yaml").read_text(encoding="utf-8"))["governance_revision"]


def test_create_binds_the_lock(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    assert bound(ws, capsys, {}) == (0, PINNED)


@pytest.mark.parametrize("files, detail", [
    ({".atc/atc.lock": None}, "atc.lock is missing"),
    ({".atc/atc.lock": 'version = 2\nsource = "a/b"\nrevision = "c"\n'}, "version 2 is not 1"),
])
def test_create_refuses_without_a_readable_binding(ws: Workspace, capsys: pytest.CaptureFixture[str], files: dict, detail: str) -> None:
    status, err = bound(ws, capsys, files)
    assert status == 1 and "STOP NO_GOVERNANCE_BINDING:" in err and detail in err


# create

def test_create_writes_a_valid_record_on_its_own_branch(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status, out, err = ws.cli(capsys, "create", "widget", "--scope", "plan § 2: the widget", cwd=ws.repo)
    assert status == 0, err
    assert out.startswith("CREATED DEMO-0001 ")
    path = created(ws) / TASKS_DIR / "DEMO-0001.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data == {
        "task_id": "DEMO-0001", "title": "widget", "repository": PROJECT,
        "repository_identity_status": "no origin remote; local repository name",
        "base_sha": run_git(ws.repo, "rev-parse", "main"), "scope": "plan § 2: the widget", "tests": "standard",
        "governance_source": GOVERNANCE_SOURCE, "governance_revision": PINNED, "branch": "claude+DEMO-0001",
        "authority": {"edit": True, "commit": True, "push_topic": False, "integrate_main": False,
                      "push_main": False, "deploy": False},
    }
    assert task_records.problems(created(ws)) == []


def test_create_without_a_testkit_records_tests_none_with_its_reason(ws: Workspace,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    run_git(ws.repo, "rm", "-q", "--", "pyproject.toml")
    run_git(ws.repo, "commit", "-q", "-m", "no testkit")
    status, out, err = ws.cli(capsys, "create", "widget", "--scope", "s", cwd=ws.repo)
    assert status == 0, err
    data = yaml.safe_load((created(ws) / TASKS_DIR / "DEMO-0001.yaml").read_text(encoding="utf-8"))
    assert data["tests"] == "none" and data["decisions"] == [task_records.NO_TESTKIT_DECISION]
    assert task_records.problems(created(ws)) == []


def test_create_takes_the_repository_from_origin(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    run_git(ws.repo, "remote", "add", "origin", "git@github.com:example/demo.git")
    assert ws.cli(capsys, "create", "widget", "--scope", "s", "--project", PROJECT, cwd=ws.repo)[0] == 0
    data = yaml.safe_load((created(ws) / TASKS_DIR / "DEMO-0001.yaml").read_text(encoding="utf-8"))
    assert data["repository"] == "example/demo"
    assert "repository_identity_status" not in data


def test_create_numbers_past_every_branch_remote_ref_and_integration(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    commit(ws.repo, {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "old")})
    other = ws.worktree(ws.repo, "claude+other", "other")
    commit(other, {".atc/tasks/DEMO-0004.yaml": record("DEMO-0004", "other")})
    run_git(ws.repo, "update-ref", "refs/remotes/origin/far", "other")
    assert ws.cli(capsys, "create", "widget", "--scope", "s", cwd=ws.repo)[1].startswith("CREATED DEMO-0005 ")
    ws.integrate(ws.repo, candidate_sha=PINNED, task="DEMO-0009")
    assert ws.cli(capsys, "create", "second", "--scope", "s", cwd=ws.repo)[1].startswith("CREATED DEMO-0010 ")


def test_create_counts_a_number_only_a_remote_tracking_branch_holds(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    other = ws.worktree(ws.repo, "claude+other", "other")
    commit(other, {".atc/tasks/DEMO-0007.yaml": record("DEMO-0007", "other")})
    run_git(ws.repo, "update-ref", "refs/remotes/origin/other", "other")
    run_git(ws.repo, "worktree", "remove", "--force", "--force", str(other))
    run_git(ws.repo, "branch", "-D", "other")
    assert ws.cli(capsys, "create", "widget", "--scope", "s", cwd=ws.repo)[1].startswith("CREATED DEMO-0008 ")


def test_numbers_come_from_worktree_names_never_their_parent_paths(tmp_path: Path) -> None:
    """A checkout under a directory named after a task (`atc test ID` runs tests under `~/.atc/tmp/<ID>-...`)
    takes no number; a linked worktree's own name and branch do, even detached."""
    repo = tmp_path / "DEMO-0042-sha" / "ATC-0001" / "repo"
    repo.mkdir(parents=True)
    run_git(repo, "init", "-q", "-b", "main")
    commit(repo, {"AGENTS.md": agents("DEMO")})
    assert task_records.taken_numbers(repo, "DEMO") == set()
    run_git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "DEMO-0050" / "claude+DEMO-0003"), "main")
    run_git(repo, "worktree", "add", "-q", "-b", "claude+DEMO-0006", str(tmp_path / "plain"), "main")
    assert task_records.taken_numbers(repo, "DEMO") == {3, 6}


def test_create_takes_the_workspace_root_as_a_project(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status, out, err = ws.cli(capsys, "create", "contract", "--scope", "s", "--project", "root", cwd=ws.root)
    assert status == 0, err
    data = yaml.safe_load((created(ws, "root", "ATC-0001") / TASKS_DIR / "ATC-0001.yaml").read_text(encoding="utf-8"))
    assert (data["governance_source"], data["governance_revision"]) == (GOVERNANCE_SOURCE, run_git(ws.root, "rev-parse", "main"))


def test_create_refuses_an_unknown_project(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status, _, err = ws.cli(capsys, "create", "widget", "--scope", "s", "--project", "nope", cwd=ws.repo)
    assert status == 2 and "ERROR UNKNOWN_PROJECT nope" in err


def test_create_refuses_a_missing_harness(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status = task_records.main(["create", "widget", "--scope", "s"], root=ws.root, environ={}, cwd=str(ws.repo))
    err = capsys.readouterr().err
    assert status != 0 and "HARNESS_UNKNOWN" in err


def test_create_bases_the_task_on_the_main_tip(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    commit(ws.repo, {"later.txt": "later\n"})
    tip = run_git(ws.repo, "rev-parse", "main")
    assert ws.cli(capsys, "create", "widget", "--scope", "s", cwd=ws.repo)[0] == 0
    data = yaml.safe_load((created(ws) / TASKS_DIR / "DEMO-0001.yaml").read_text(encoding="utf-8"))
    assert data["base_sha"] == tip


# list

def test_create_prints_one_verdict_line(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    status, out, err = ws.cli(capsys, "create", "widget", "--scope", "s", cwd=ws.repo)
    assert status == 0, err
    lines = out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("CREATED DEMO-0001 ")


def test_invalid_task_id_is_a_usage_error(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    for task in ("bad-id", "Upper-1"):
        status = task_records.main(["close", task], root=ws.root, environ=dict(HARNESS), cwd=str(ws.repo))
        err = capsys.readouterr().err
        assert status == 2 and err.startswith("ERROR TASK_INVALID "), (task, err)


def test_uninferable_cwd_lists_the_registered_names(ws: Workspace, tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    status = task_records.main(["list"], root=ws.root, environ=dict(HARNESS), cwd=str(tmp_path))
    err = capsys.readouterr().err
    assert status == 2 and "UNKNOWN_PROJECT" in err and f"registered: {PROJECT}" in err


def test_list_derives_each_status(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    main_records = {
        ".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "t-closed"),
        ".atc/tasks/DEMO-0003.yaml": record("DEMO-0003", "t-integrated"),
        ".atc/tasks/DEMO-0004.yaml": record("DEMO-0004", "t-bound"),
        ".atc/tasks/DEMO-0005.yaml": record("DEMO-0005", "t-unknown"),
    }
    bound_at = commit(ws.repo, main_records)
    ws.integrate(ws.repo, candidate_sha=bound_at, task="DEMO-0004", records_only=True, main_before=PINNED)
    ws.integrate(ws.repo, candidate_sha=bound_at, task="DEMO-0001")
    integrated = ws.worktree(ws.repo, "claude+integrated", "t-integrated")
    done = commit(integrated, {"work.txt": "done\n"})
    run_git(ws.repo, "merge", "-q", "--ff-only", "t-integrated")
    ws.integrate(ws.repo, candidate_sha=done, task="DEMO-0003")
    opened = ws.worktree(ws.repo, "claude+open", "t-open")
    commit(opened, {".atc/tasks/DEMO-0002.yaml": record("DEMO-0002", "t-open")})
    abandoned = ws.worktree(ws.repo, "claude+abandoned", "t-abandoned")
    commit(abandoned, {".atc/tasks/DEMO-0006.yaml": record("DEMO-0006", "t-abandoned")})
    run_git(ws.repo, "tag", "archive/DEMO-0006", "t-abandoned")
    run_git(ws.repo, "worktree", "remove", "--force", "--force", str(abandoned))
    run_git(ws.repo, "branch", "-D", "t-abandoned")

    rows = ws.listing(capsys)
    assert {task_id: row["status"] for task_id, row in rows.items()} == {
        "DEMO-0001": "closed", "DEMO-0002": "open", "DEMO-0003": "integrated",
        "DEMO-0004": "bound", "DEMO-0005": "unknown", "DEMO-0006": "abandoned",
    }
    assert rows["DEMO-0002"]["branch"] == "t-open"
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
    assert (row["status"], row["title"]) == ("open", "second scope")


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
    assert err.splitlines() == [f"WARNING INVALID_RECORD {PROJECT}: 2 record(s) are not a YAML mapping"]
    status, out, err = ws.cli(capsys, "list", "--project", PROJECT, "--json", cwd=ws.root)
    assert err.count("WARNING INVALID_RECORD") == 1
    assert [row["task_id"] for row in json.loads(out)["tasks"]] == ["DEMO-0001"]


def test_list_infers_the_project_from_a_worktree(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    topic = ws.worktree(ws.repo, "claude+widget", "topic-widget")
    commit(topic, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "topic-widget")})
    status, out, _ = ws.cli(capsys, "list", cwd=topic)
    assert status == 0
    assert out.startswith("DEMO-0001  open  topic-widget")


def test_list_reads_unknown_for_an_integration_whose_candidate_is_off_main(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    commit(ws.repo, {".atc/tasks/DEMO-0001.yaml": record("DEMO-0001", "t-lost")})
    lost = ws.worktree(ws.repo, "claude+lost", "t-lost")
    candidate = commit(lost, {"work.txt": "never on main\n"})
    run_git(ws.repo, "worktree", "remove", "--force", "--force", str(lost))
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


def test_the_check_of_a_tree_finds_what_the_check_of_its_checkout_finds(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    commit(repo, {"AGENTS.md": agents("DEMO"), f"{TASKS_DIR}/DEMO-0001.yaml": record("DEMO-0001", "b"),
                  f"{TASKS_DIR}/DEMO-0002.yaml": record("DEMO-0003", "b"), f"{TASKS_DIR}/notes.txt": "not a record\n",
                  f"{TASKS_DIR}/nested/DEMO-0004.yaml": "{"})
    (repo / TASKS_DIR / "DEMO-0005.yaml").write_bytes(b"scope: \xff\n")
    run_git(repo, "add", "--", TASKS_DIR)
    head = commit(repo, {"AGENTS.md": "# AGENTS.md\n"})
    tree = run_git(repo, "rev-parse", f"{head}^{{tree}}")
    found = task_records.problems(repo)
    assert found[0].startswith("no-declaration: .atc/tasks/ holds 3 record(s)")
    assert any(p.startswith("id-mismatch: DEMO-0002.yaml") for p in found)
    assert any(p.startswith("invalid-yaml: DEMO-0005.yaml: not UTF-8") for p in found)
    assert task_records.tree_problems(repo, tree) == found


@pytest.mark.parametrize("extra, flagged", [
    ({"tests": "none"}, True),
    ({"tests": "none", "decisions": []}, True),
    ({"tests": "none", "decisions": ["Owner: text only, no tests"]}, False),
    ({"tests": "standard"}, False),
    ({}, False),
], ids=["none-missing", "none-empty", "none-decided", "standard", "no-tests-field"])
def test_tests_none_needs_a_decision_line(extra: dict, flagged: bool) -> None:
    found = task_records.record_problems("DEMO-0001.yaml", record("DEMO-0001", "claude+DEMO-0001", **extra),
                                         "DEMO", task_records.validator())
    assert any(p.startswith("tests-none-undecided: DEMO-0001.yaml:") for p in found) == flagged


# --project root and the prefix of a record --task


def test_project_root_names_the_workspace_root(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    commit(ws.root, {".atc/tasks/ATC-0001.yaml": record("ATC-0001", "gone")})
    status, out, err = ws.cli(capsys, "list", "--project", "root", "--json", cwd=ws.root)
    assert status == 0, err
    assert [(row["project"], row["task_id"]) for row in json.loads(out)["tasks"]] == [("root", "ATC-0001")]


def test_list_at_the_canonical_checkout_lists_the_root(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    commit(ws.root, {".atc/tasks/ATC-0001.yaml": record("ATC-0001", "gone")})
    (ws.root / "docs").mkdir()
    for cwd in (ws.root, ws.root / "docs"):
        status, out, err = ws.cli(capsys, "list", cwd=cwd)
        assert (status, out.split()[:1]) == (0, ["ATC-0001"]), err
    status, _out, err = ws.cli(capsys, "list", cwd=ws.root.parent)
    assert status == 2 and "UNKNOWN_PROJECT" in err


def test_list_shows_the_files_two_open_tasks_both_change(ws: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    shared = {f"src/{n}.py": "x\n" for n in range(7)}
    for task_id, branch, files in (("DEMO-0001", "t-one", {**shared, "a.py": "a\n", ".atc/tasks/notes.md": "1\n"}),
                                   ("DEMO-0002", "t-two", {**shared, "b.py": "b\n"}),
                                   ("DEMO-0003", "t-three", {"c.py": "c\n", ".atc/tasks/notes.md": "3\n"}),
                                   ("DEMO-0004", "t-four", shared)):
        topic = ws.worktree(ws.repo, f"claude+{branch}", branch)
        closure = {"closure": {"state": "closed", "note": "done"}} if task_id == "DEMO-0004" else {}
        commit(topic, {f".atc/tasks/{task_id}.yaml": record(task_id, branch, **closure), **files})
    status, out, _ = ws.cli(capsys, "list", "--project", PROJECT, cwd=ws.root)
    assert status == 0
    overlaps_lines = [line for line in out.splitlines() if line.startswith("OVERLAP ")]
    assert "OVERLAP DEMO-0001 DEMO-0002: 7 files: src/0.py, src/1.py, src/2.py, src/3.py, src/4.py (+2)" in overlaps_lines
    status, out, _ = ws.cli(capsys, "list", "--project", PROJECT, "--json", cwd=ws.root)
    assert {"project": PROJECT, "tasks": ["DEMO-0001", "DEMO-0002"], "files": sorted(shared)} in json.loads(out)["overlaps"]

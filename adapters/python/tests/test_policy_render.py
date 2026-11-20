"""`atc policy render ID` (src/atc_cli/scripts/policy_render.py, docs/ENFORCEMENT.md § Outputs) outside the
conformance cases: rewrites, modes, refusals, the settings copy, the task's worktree and `atc sync --check`."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import git_history
import policy_render
import validate_workspace

CASES = Path(__file__).resolve().parents[3] / "shared/conformance/generate-enforcement"
POLICY = (CASES / "filesystem-modes/input/policies/hard-deny.yaml").read_text(encoding="utf-8")
TASK = "ATC-0001"
BRANCH = "claude+ATC-0001"


def tree_with(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    tree = tmp_path / "tree"
    for rel, text in {"policies/hard-deny.yaml": POLICY, **(files or {})}.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text, encoding="utf-8", newline="\n")
    return tree


def generated(tree: Path, rel: str = "claude-code/settings.json") -> Path:
    return tree / "generated/enforcement" / rel


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="Windows has no executable bit")
def test_both_hooks_are_written_executable(tmp_path):
    tree = tree_with(tmp_path, {"scripts/git-hooks/pre-commit": "#!/bin/sh\n"})
    assert policy_render.render(tree, check=False)
    for path in (generated(tree, "git-hooks/pre-commit"), tree / "scripts/git-hooks/pre-commit"):
        assert path.stat().st_mode & stat.S_IXUSR


@pytest.mark.unit
def test_a_rerun_leaves_a_crlf_file_with_the_same_content_untouched(tmp_path):
    tree = tree_with(tmp_path)
    policy_render.render(tree, check=False)
    crlf = generated(tree).read_bytes().replace(b"\n", b"\r\n")
    generated(tree).write_bytes(crlf)
    assert policy_render.render(tree, check=False) == []
    assert generated(tree).read_bytes() == crlf


@pytest.mark.unit
def test_a_rerun_rewrites_a_file_whose_content_differs_as_lf(tmp_path):
    tree = tree_with(tmp_path)
    policy_render.render(tree, check=False)
    expected = generated(tree).read_bytes()
    generated(tree).write_bytes(expected.replace(b"Read(**/.env)", b"Read(**/.stale)").replace(b"\n", b"\r\n"))
    assert policy_render.render(tree, check=True) == ["generated/enforcement/claude-code/settings.json"]
    assert policy_render.render(tree, check=False) == ["generated/enforcement/claude-code/settings.json"]
    assert generated(tree).read_bytes() == expected


@pytest.mark.unit
def test_check_writes_nothing(tmp_path, capsys):
    tree = tree_with(tmp_path)
    assert policy_render.report(tree, check=True, verbose=False, as_json=False, again="x") == 1
    assert "DRIFT 3 files" in capsys.readouterr().out
    assert not (tree / "generated").exists()


@pytest.mark.unit
def test_a_copy_that_is_absent_is_not_created(tmp_path):
    tree = tree_with(tmp_path)
    policy_render.render(tree, check=False)
    assert not (tree / ".claude").exists() and not (tree / "scripts").exists()


@pytest.mark.unit
def test_a_settings_copy_whose_deny_matches_is_left_as_it_is(tmp_path):
    tree = tree_with(tmp_path)
    policy_render.render(tree, check=False)
    deny = json.loads(generated(tree).read_text(encoding="utf-8"))["permissions"]["deny"]
    text = "﻿" + json.dumps({"model": "x", "permissions": {"deny": deny}}, indent=4) + "\n"
    (tree / ".claude").mkdir()
    (tree / ".claude/settings.json").write_text(text, encoding="utf-8")
    assert policy_render.render(tree, check=False) == []
    assert (tree / ".claude/settings.json").read_text(encoding="utf-8") == text


@pytest.mark.unit
def test_a_settings_copy_without_permissions_gets_them(tmp_path):
    tree = tree_with(tmp_path, {".claude/settings.json": '{"model": "x"}\n'})
    assert ".claude/settings.json" in policy_render.render(tree, check=False)
    doc = json.loads((tree / ".claude/settings.json").read_text(encoding="utf-8"))
    assert doc["model"] == "x"
    assert doc["permissions"]["deny"] == json.loads(generated(tree).read_text(encoding="utf-8"))["permissions"]["deny"]


@pytest.mark.unit
@pytest.mark.parametrize("text", ["{not json", "[]\n", '{"permissions": []}\n'])
def test_an_invalid_settings_copy_stops_and_writes_nothing(tmp_path, capsys, text):
    tree = tree_with(tmp_path, {".claude/settings.json": text})
    assert policy_render.report(tree, check=False, verbose=False, as_json=False, again="x") == 1
    assert capsys.readouterr().err.startswith("STOP SETTINGS_INVALID: .claude/settings.json: ")
    assert not (tree / "generated").exists()
    assert (tree / ".claude/settings.json").read_text(encoding="utf-8") == text


@pytest.mark.unit
def test_a_tree_without_a_policy_stops(tmp_path, capsys):
    (tmp_path / "tree").mkdir()
    assert policy_render.report(tmp_path / "tree", check=False, verbose=False, as_json=True, again="x") == 1
    assert json.loads(capsys.readouterr().out)["refusal"]["reason"] == "NO_POLICY"


@pytest.mark.unit
def test_an_invalid_policy_lists_each_problem_and_writes_nothing(tmp_path, capsys):
    tree = tree_with(tmp_path, {"policies/hard-deny.yaml": POLICY.replace('"**/.env"', '"${ROOT}/**"')})
    assert policy_render.report(tree, check=False, verbose=False, as_json=False, again="atc policy render X") == 1
    err = capsys.readouterr().err
    assert "STOP POLICY_INVALID: 1 problems" in err and "unresolved-token: filesystem.deny[0]" in err
    assert "hint: fix policies/hard-deny.yaml, then run atc policy render X" in err
    assert not (tree / "generated").exists()


# The task's worktree


def git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=git_env(),
                          check=True).stdout.strip()


def built_workspace(root: Path) -> None:
    git_history.new_repository(root, [git_history.Commit({"AGENTS.md": b"# AGENTS.md\n"}, "edit")], env=git_env())
    git_history.new_repository(root / "projects" / "demo", [git_history.Commit({
        "AGENTS.md": b"# AGENTS.md\n\n## Task records\n\n- Task prefix: ATC\n",
        "policies/hard-deny.yaml": POLICY.encode(),
    }, "edit")], env=git_env())


@pytest.fixture()
def worktree(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path / "workspace"
    git_history.copied(tmp_path_factory, "policy-render", root, built_workspace)
    (root / "workspace.yaml").write_text(
        f"version: 1\nworkspace_root: '{root}'\ngovernance_source: example/governance\nprojects:\n"
        "  demo:\n    repo: projects/demo\n    worktrees: worktrees/demo\n    state: state/demo-data\n",
        encoding="utf-8")
    project = root / "projects" / "demo"
    tree = root / "worktrees" / "demo" / BRANCH
    git(project, "worktree", "add", "-q", "-b", BRANCH, str(tree), "main")
    (tree / ".atc/tasks").mkdir(parents=True)
    (tree / ".atc/tasks" / f"{TASK}.yaml").write_text(f"task_id: {TASK}\nbranch: {BRANCH}\n", encoding="utf-8")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "open")
    return tree


@pytest.mark.integration
def test_render_writes_the_tasks_worktree_and_never_main(worktree: Path, capsys):
    root = worktree.parents[2]
    assert policy_render.main(["render", TASK, "-v"], root=root, environ={}) == 0
    assert capsys.readouterr().out.startswith("RENDERED 3 files\n  generated/enforcement/")
    assert generated(worktree).is_file()
    assert not (root / "projects/demo/generated").exists()
    assert policy_render.main(["render", TASK, "--check"], root=root, environ={}) == 0
    assert capsys.readouterr().out == "CURRENT enforcement files\n"


@pytest.mark.integration
def test_a_task_without_a_worktree_stops(worktree: Path, capsys):
    root = worktree.parents[2]
    git(root / "projects/demo", "worktree", "remove", "--force", str(worktree))
    assert policy_render.main(["render", TASK], root=root, environ={}) == 1
    assert capsys.readouterr().err.startswith(f"STOP NO_WORKTREE: no worktree has {BRANCH} checked out")


@pytest.mark.unit
def test_a_root_task_stops(monkeypatch, tmp_path, capsys):
    import task_records
    from managed_worktree import Project
    monkeypatch.setattr(task_records, "workspace", lambda root, environ: None)
    monkeypatch.setattr(task_records, "project_of_task",
                        lambda ws, task_id: Project(validate_workspace.ROOT_TOPICS_NAME, str(tmp_path), "", None))
    assert policy_render.main(["render", TASK], root=tmp_path, environ={}) == 1
    assert capsys.readouterr().err.startswith(f"STOP ROOT_POLICY: {TASK} is a task of the workspace root")


# atc sync --check


@pytest.mark.unit
def test_sync_check_fails_a_drifted_copy_and_names_the_command(tmp_path):
    tree = tree_with(tmp_path, {"scripts/git-hooks/pre-commit": "#!/bin/sh\n"})
    policy_render.render(tree, check=False)
    (tree / "scripts/git-hooks/pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    failures = list(validate_workspace.FAILURES)
    try:
        validate_workspace.check_project_enforcement(tree, "projects.demo.enforcement", "demo")
        added = validate_workspace.FAILURES[len(failures):]
    finally:
        validate_workspace.FAILURES[:] = failures
    assert added == ["projects.demo.enforcement: scripts/git-hooks/pre-commit: not the render of "
                     "policies/hard-deny.yaml — run atc policy render <task ID> in a task for demo"]


@pytest.mark.unit
@pytest.mark.parametrize("entry, reported", [({}, True), ({"state": "state/demo-data"}, False)])
def test_sync_check_reports_a_state_glob_without_a_state_root(tmp_path, capsys, entry, reported):
    state = POLICY.replace('filesystem:\n  deny:\n', 'filesystem:\n  deny:\n    - "${state}/demo/**"\n')
    tree = tree_with(tmp_path, {"policies/hard-deny.yaml": state})
    failures = list(validate_workspace.FAILURES)
    validate_workspace.report_state_rules(tree, "projects.demo.enforcement", "demo", entry)
    out = capsys.readouterr().out
    assert validate_workspace.FAILURES == failures
    assert (out == "sync --check: projects.demo.enforcement: filesystem.deny[0] names ${state} but workspace.yaml "
                   "gives demo no state; the hook skips it\n") is reported
    assert reported or out == ""


@pytest.mark.unit
def test_the_conformance_trees_render_without_a_workspace(tmp_path):
    tree = tmp_path / "copy"
    shutil.copytree(CASES / "copies/input", tree)
    assert policy_render.tree_main([str(tree)]) == 0

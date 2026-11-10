"""`scripts/select_tests.py`: this adapter's test selection, read from this checkout's code."""
from __future__ import annotations

import pytest

import select_tests

pytestmark = pytest.mark.integration

ADAPTER = "adapters/python"


@pytest.fixture(scope="module")
def graph() -> select_tests.Graph:
    return select_tests.Graph()


def chosen(graph: select_tests.Graph, *paths: str) -> list[str] | None:
    return select_tests.pytest_args(select_tests.select(paths, graph))


def test_a_script_selects_its_own_tests(graph):
    assert chosen(graph, f"{ADAPTER}/scripts/worktree_cd_guard.py") == [
        "tests/test_select_tests.py", "tests/test_worktree_cd_guard.py"]
    assert chosen(graph, f"{ADAPTER}/scripts/build_testkit_release.py") == [
        "tests/test_select_tests.py", "tests/test_testkit_release.py"]


def test_a_script_selects_the_tests_that_reach_it_through_another_module(graph):
    selected = chosen(graph, f"{ADAPTER}/scripts/git_preflight.py")
    assert {"tests/test_git_preflight.py", "tests/test_hami_worktree.py", "tests/test_git_integrate.py"} <= set(selected)
    assert "tests/test_worktree_cd_guard.py" not in selected


def test_a_testkit_module_selects_the_tests_that_reach_it(graph):
    selected = chosen(graph, f"{ADAPTER}/atc-testkit/src/atc_testkit/verifier.py")
    assert {"tests/test_integration_verify.py", "tests/test_testkit_gate.py", "tests/test_testkit_verifier.py"} <= set(selected)
    assert "tests/test_worktree_cd_guard.py" not in selected


def test_a_testkit_module_selects_the_tests_that_scan_the_whole_package(graph):
    selected = chosen(graph, f"{ADAPTER}/atc-testkit/src/atc_testkit/gate.py")
    assert {"tests/test_testkit_tmp.py", "tests/test_testkit_release.py"} <= set(selected)


def test_a_declared_reader_must_be_a_test_file(monkeypatch):
    monkeypatch.setitem(select_tests.DIRECTORY_READERS, f"{ADAPTER}/tests/test_missing.py", (select_tests.SCRIPTS,))
    with pytest.raises(ValueError, match="test_missing.py"):
        select_tests.Graph()


def test_a_test_file_selects_itself_and_the_test_files_that_import_it(graph):
    assert chosen(graph, f"{ADAPTER}/tests/test_tooling.py") == [
        "tests/test_bootstrap.py", "tests/test_onboard_prune.py", "tests/test_onboard_sync_commit.py",
        "tests/test_select_tests.py", "tests/test_shell_install.py", "tests/test_tooling.py"]
    assert "tests/test_integrate_moving_main.py" in chosen(graph, f"{ADAPTER}/tests/test_git_integrate.py")


def test_a_removed_test_file_selects_nothing_unless_another_module_imports_it(graph):
    assert chosen(graph, f"{ADAPTER}/tests/test_gone.py") == ["tests/test_select_tests.py"]
    graph.modules["test_tooling_helpers_user"] = select_tests.Module(
        "test_tooling_helpers_user", f"{ADAPTER}/tests/test_tooling_helpers_user.py", None, imports={"test_gone"})
    try:
        assert chosen(graph, f"{ADAPTER}/tests/test_gone.py") == []
    finally:
        del graph.modules["test_tooling_helpers_user"]


def test_the_selector_and_its_guard_select_the_full_suite(graph):
    """`conftest.py` loads the reader guard, which imports the selector and its testkit modules."""
    for path in (f"{ADAPTER}/scripts/select_tests.py", f"{ADAPTER}/tests/root_reads.py",
                 f"{ADAPTER}/atc-testkit/src/atc_testkit/selection.py", f"{ADAPTER}/atc-testkit/src/atc_testkit/git.py"):
        assert chosen(graph, path) == [], path


@pytest.mark.parametrize(("path", "readers"), [
    ("shared/governance/VERIFICATION.md", ["tests/test_shell_install.py"]),
    ("shared/skills/atc-review/SKILL.md", ["tests/test_shell_install.py", "tests/test_tooling.py"]),
    (".claude/skills/atc-review/SKILL.md", ["tests/test_shell_install.py"]),
    (".dsh/skills/atc-worktree/SKILL.md", ["tests/test_shell_install.py", "tests/test_tooling.py"]),
    ("AGENTS.md", ["tests/test_integration_verify.py", "tests/test_shell_install.py"]),
    ("CLAUDE.md", ["tests/test_shell_install.py"]),
    (r"shared\governance\CORE.md", ["tests/test_shell_install.py"]),
])
def test_a_guarded_path_selects_its_declared_readers_only(graph, path, readers):
    assert chosen(graph, path) == readers


def test_a_root_file_entry_selects_its_reader_and_nothing_beside_it(graph, monkeypatch):
    monkeypatch.setattr(select_tests, "DIRECTORY_READERS", {f"{ADAPTER}/tests/test_integration_verify.py": ("AGENTS.md",)})
    assert chosen(graph, "AGENTS.md") == ["tests/test_integration_verify.py"]
    assert select_tests.covers("AGENTS.md", "AGENTS.md") and not select_tests.covers("AGENTS.md", "AGENTS.mdx")


@pytest.mark.parametrize(("entry", "path"), [
    ("shared/skills", "Shared/Skills/atc-review/SKILL.md"),
    ("shared/skills/", "shared/skills/atc-review/SKILL.md"),
    (r"shared\skills", "shared/skills/atc-review/SKILL.md"),
    ("AGENTS.md", "agents.md"),
])
def test_an_entry_matches_whatever_its_case_separators_and_trailing_slash(graph, monkeypatch, entry, path):
    monkeypatch.setattr(select_tests, "DIRECTORY_READERS", {f"{ADAPTER}/tests/test_tooling.py": (entry,)})
    assert chosen(graph, path) == ["tests/test_tooling.py"]


def test_a_rename_selects_for_the_old_and_the_new_path(graph):
    assert chosen(graph, "shared/skills/atc-review/SKILL.md", "docs/NO_TEST_NAMES_THIS.md") == [
        "tests/test_shell_install.py", "tests/test_tooling.py"]
    assert chosen(graph, "docs/NO_TEST_NAMES_THIS.md", "AGENTS.md") == [
        "tests/test_integration_verify.py", "tests/test_shell_install.py"]


def test_a_guarded_file_other_than_markdown_adds_the_tests_that_name_it(graph):
    named = graph.naming(("validate_workspace.py",))
    assert "tests/test_bootstrap.py" in {test.removeprefix(f"{ADAPTER}/") for test in named}
    assert set(chosen(graph, "shared/skills/demo/validate_workspace.py")) == {
        test.removeprefix(f"{ADAPTER}/") for test in named} | {"tests/test_shell_install.py", "tests/test_tooling.py"}


def test_a_suffixless_guarded_file_adds_the_tests_that_name_it(graph):
    named = {test.removeprefix(f"{ADAPTER}/") for test in graph.naming(("pytest",))}
    assert named and set(chosen(graph, "shared/skills/demo/pytest")) == named | {
        "tests/test_shell_install.py", "tests/test_tooling.py"}


@pytest.mark.parametrize(("path", "readers"), [
    ("README.md", ["tests/test_shell_install.py"]),
    ("GLOSSARY.md", ["tests/test_shell_install.py"]),
    ("AGENTS.md", ["tests/test_integration_verify.py", "tests/test_shell_install.py"]),
])
def test_top_level_markdown_is_guarded(graph, path, readers):
    assert chosen(graph, path) == readers
    assert select_tests.covers(select_tests.TOP_MARKDOWN, path)
    assert not select_tests.covers(select_tests.TOP_MARKDOWN, f"docs/{path}")


def test_the_run_starts_no_stage_for_an_empty_selection_and_the_full_suite_for_a_widened_one(monkeypatch):
    started = []
    monkeypatch.setattr(select_tests.subprocess, "run",
                        lambda argv, cwd: started.append(argv) or select_tests.subprocess.CompletedProcess(argv, 0))
    runner = [select_tests.sys.executable, "-m", "atc_testkit.ci", "--stages"]
    assert select_tests.main(["--paths", "docs/NO_TEST_NAMES_THIS.md", "--run", "--", "-x"]) == 0
    assert started == []
    assert select_tests.main(["--paths", "somewhere/unknown.bin", "--run", "--", "-x"]) == 0
    assert started == [[*runner, "-x"]]
    started.clear()
    assert select_tests.main(["--paths", f"{select_tests.TESTS}/{select_tests.SELF_TEST}", "--run", "--workers", "16"]) == 0
    assert started == [[*runner, "--workers", "16", f"tests/{select_tests.SELF_TEST}"]]


@pytest.mark.parametrize("path", ["shared/governance/", "shared/governance", ".claude/skills/atc-review/"])
def test_a_guarded_directory_selects_its_declared_readers_only(graph, path):
    assert chosen(graph, path) == ["tests/test_shell_install.py"]


def test_a_guarded_path_no_declared_test_reads_selects_by_kind(graph, monkeypatch):
    monkeypatch.setattr(select_tests, "DIRECTORY_READERS", {})
    assert chosen(graph, "shared/skills/atc-review/SKILL.md") is None
    assert chosen(graph, ".agents/skills/atc-review/no_test_names_this.py") == []


@pytest.mark.parametrize("path", [
    f"{ADAPTER}/tests/conftest.py",
    f"{ADAPTER}/pyproject.toml",
    f"{ADAPTER}/uv.lock",
    f"{ADAPTER}/atc-testkit/pyproject.toml",
    f"{ADAPTER}/tests/unmarked_baseline.txt",
    f"{ADAPTER}/atc-testkit/src/atc_testkit/plugin.py",
    f"{ADAPTER}/atc-testkit/src/atc_testkit/markers.py",
    f"{ADAPTER}/src/atc_cli/__init__.py",
])
def test_a_full_trigger_selects_the_full_suite(graph, path):
    assert chosen(graph, path) == []
    assert chosen(graph, f"{ADAPTER}/scripts/worktree_cd_guard.py", path) == []


@pytest.mark.parametrize("path", [
    "somewhere/unknown.bin",
    f"{ADAPTER}/scripts/removed_script.py",
    f"{ADAPTER}/src/atc_cli/__main__.py",
    "shared/unknown-area/file.txt",
])
def test_an_unmapped_path_falls_back_to_the_full_suite(graph, path):
    assert chosen(graph, path) == []


def test_shared_paths_select_the_tests_that_read_their_area(graph):
    selected = chosen(graph, "shared/conformance/generate-enforcement/example-policies/hard-deny.yaml")
    assert {"tests/test_conformance.py", "tests/test_codex_rules.py", "tests/test_generate_enforcement.py"} <= set(selected)
    assert "tests/test_worktree_cd_guard.py" not in selected


@pytest.mark.parametrize("path", [
    ".atc/tasks/ATC-0004.yaml",
    r".atc\tasks\ATC-0004.yaml",
    ".atc/tasks/ATC-9999.yaml",
])
def test_a_task_record_selects_the_tests_that_name_the_record_directory(graph, path):
    assert chosen(graph, path) == ["tests/test_integrate_task.py", "tests/test_task_records.py"]


def test_a_task_record_no_test_names_falls_back_to_the_full_suite(graph, monkeypatch):
    monkeypatch.setattr(select_tests, "TASK_RECORDS", ".atc/no-test-names-this")
    assert chosen(graph, ".atc/no-test-names-this/ATC-0004.yaml") == []


def test_markdown_no_test_names_selects_nothing(graph):
    assert chosen(graph, "docs/NO_TEST_NAMES_THIS.md") is None


def test_the_command_prints_each_selected_test_with_its_cause(capsys):
    assert select_tests.main(["--paths", f"{ADAPTER}/scripts/worktree_cd_guard.py", "docs/NO_TEST_NAMES_THIS.md"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("test selection: 2 of ")
    assert f"  tests/test_worktree_cd_guard.py  <- {ADAPTER}/scripts/worktree_cd_guard.py" in out
    assert "  docs/NO_TEST_NAMES_THIS.md: Markdown no test file names" in out

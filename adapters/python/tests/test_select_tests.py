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


def test_the_selector_selects_its_own_tests(graph):
    assert "tests/test_select_tests.py" in chosen(graph, f"{ADAPTER}/scripts/select_tests.py")


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

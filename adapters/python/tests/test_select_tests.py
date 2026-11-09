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
    assert chosen(graph, f"{ADAPTER}/scripts/worktree_cd_guard.py") == ["tests/test_worktree_cd_guard.py"]
    assert chosen(graph, f"{ADAPTER}/scripts/build_testkit_release.py") == ["tests/test_testkit_release.py"]


def test_a_script_selects_the_tests_that_reach_it_through_another_module(graph):
    selected = chosen(graph, f"{ADAPTER}/scripts/git_preflight.py")
    assert {"tests/test_git_preflight.py", "tests/test_hami_worktree.py", "tests/test_git_integrate.py"} <= set(selected)
    assert "tests/test_worktree_cd_guard.py" not in selected


def test_a_testkit_module_selects_the_tests_that_reach_it(graph):
    selected = chosen(graph, f"{ADAPTER}/atc-testkit/src/atc_testkit/verifier.py")
    assert selected == ["tests/test_integration_verify.py", "tests/test_testkit_gate.py", "tests/test_testkit_verifier.py"]


def test_a_test_file_selects_itself_and_a_removed_one_selects_nothing(graph):
    assert chosen(graph, f"{ADAPTER}/tests/test_tooling.py") == ["tests/test_tooling.py"]
    assert chosen(graph, f"{ADAPTER}/tests/test_gone.py") is None


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


def test_markdown_no_test_names_selects_nothing(graph):
    assert chosen(graph, "docs/NO_TEST_NAMES_THIS.md") is None


def test_the_command_prints_each_selected_test_with_its_cause(capsys):
    assert select_tests.main(["--paths", f"{ADAPTER}/scripts/worktree_cd_guard.py", "docs/NO_TEST_NAMES_THIS.md"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("test selection: 1 of ")
    assert f"  tests/test_worktree_cd_guard.py  <- {ADAPTER}/scripts/worktree_cd_guard.py" in out
    assert "  docs/NO_TEST_NAMES_THIS.md: Markdown no test file names" in out

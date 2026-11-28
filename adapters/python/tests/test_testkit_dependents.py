"""docs/TESTKIT.md § Declared dependents: the table, its patterns and checks, and the selections that read it."""
from __future__ import annotations

import dataclasses
from pathlib import Path
import re

import pytest

from atc_testkit import dependents, stages
from atc_testkit.locations import Refusal
from atc_testkit.project import Project
from atc_testkit.selection import Selection

pytestmark = pytest.mark.unit


def write_tree(root: Path, table: str, files: tuple[str, ...] = ("tests/test_reader.py", "data/a.txt")) -> Path:
    """A test directory whose ``pyproject.toml`` carries ``table`` under ``[tool.atc-testkit.dependents]``."""
    for relative in files:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text(f"[tool.atc-testkit]\n[tool.atc-testkit.dependents]\n{table}",
                                         encoding="utf-8")
    return root


@pytest.mark.parametrize(("pattern", "path", "matched"), [
    ("scripts/verify_*_mutations.py", "scripts/verify_drift_mutations.py", True),
    ("scripts/verify_*_mutations.py", "scripts/sub/verify_drift_mutations.py", False),
    ("packages/*/src/**/*.py", "packages/cli/src/pkg/a.py", True),
    ("packages/*/src/**/*.py", "packages/cli/src/a.py", True),
    ("packages/*/src/**/*.py", "packages/cli/tests/a.py", False),
    ("data/?.txt", "data/a.txt", True),
    ("data/?.txt", "data/ab.txt", False),
    ("*.md", "AGENTS.md", True),
    ("*.md", "docs/AGENTS.md", False),
    ("shared/skills", "shared/skills/atc-review/SKILL.md", True),
    ("shared/skills", "shared/skills", True),
    ("shared/skills", "shared/skillset/x.md", False),
    ("shared/skills", "Shared/Skills/x.md", False),
    ("a/**", "a/b/c", True),
])
def test_a_pattern_matches_whole_segments_in_its_own_case(pattern: str, path: str, matched: bool) -> None:
    assert dependents.matches(pattern, path) is matched


@pytest.mark.parametrize("pattern", ["", "/abs", "a\\b", "a//b", "a/./b", "../a", "a/", "C:/x", "a/[ab].py", "a**b"])
def test_a_malformed_pattern_is_a_problem(pattern: str) -> None:
    assert dependents.pattern_problem(pattern) == "invalid pattern"


def test_a_valid_declaration_has_no_problem_and_selects_by_changed_path(tmp_path: Path) -> None:
    tree = write_tree(tmp_path, '"tests/test_reader.py" = ["data/*.txt"]\n"tests" = ["data"]\n')
    declared = dependents.load(tree, tree)
    assert declared.select(["data/a.txt", "data/b.txt", "src/x.py"]) == {
        "tests/test_reader.py": ["data/a.txt", "data/b.txt"], "tests": ["data/a.txt", "data/b.txt"]}
    assert declared.select([r"data\a.txt"]) == {"tests/test_reader.py": ["data/a.txt"], "tests": ["data/a.txt"]}
    assert declared.select(["src/x.py"]) == {}


@pytest.mark.parametrize(("table", "problem"), [
    ('"tests/test_reader.py" = "data/a.txt"\n', "dependents: tests/test_reader.py: not a list of path patterns"),
    ('"tests/test_reader.py" = []\n', "dependents: tests/test_reader.py: not a list of path patterns"),
    ('"tests/test_absent_reader.py" = ["data/a.txt"]\n',
     "dependents: tests/test_absent_reader.py: no such test file or directory"),
    ('"data/a.txt" = ["data/a.txt"]\n', "dependents: data/a.txt: no such test file or directory"),
    ('"../tests/test_reader.py" = ["data/a.txt"]\n',
     "dependents: ../tests/test_reader.py: no such test file or directory"),
    ('"tests/test_reader.py" = ["data\\\\a.txt"]\n', "dependents: tests/test_reader.py: data\\a.txt: invalid pattern"),
    ('"tests/test_reader.py" = ["data/gone_*.txt"]\n',
     "dependents: tests/test_reader.py: data/gone_*.txt: matches no file"),
    ('"tests/test_reader.py" = ["gone"]\n', "dependents: tests/test_reader.py: gone: matches no file"),
])
def test_each_problem_is_named_and_refused(tmp_path: Path, table: str, problem: str) -> None:
    tree = write_tree(tmp_path, table)
    assert dependents.problems(tree, tree) == [problem]
    with pytest.raises(dependents.DependentsInvalid, match=f"^{re.escape(problem)}$"):
        dependents.load(tree, tree)


def test_a_dependents_value_that_is_no_table_is_a_problem(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.atc-testkit]\ndependents = ["x"]\n', encoding="utf-8")
    assert dependents.problems(tmp_path, tmp_path) == ["dependents: not a table of test paths"]


def test_load_names_the_first_problem_and_how_many_more(tmp_path: Path) -> None:
    tree = write_tree(tmp_path, '"tests/test_reader.py" = ["gone", "also_gone"]\n')
    with pytest.raises(dependents.DependentsInvalid, match=r"gone: matches no file \(\+1 more\)$"):
        dependents.load(tree, tree)


def test_no_table_declares_nothing(tmp_path: Path) -> None:
    assert dependents.load(tmp_path, tmp_path).entries == {}


def test_patterns_are_checked_against_the_repository_top(tmp_path: Path) -> None:
    (tmp_path / "shared/skills").mkdir(parents=True)
    tree = write_tree(tmp_path / "adapters/python", '"tests/test_reader.py" = ["shared/skills"]\n')
    assert dependents.problems(tree, tmp_path) == []
    assert dependents.problems(tree, tree) == ["dependents: tests/test_reader.py: shared/skills: matches no file"]


@pytest.mark.parametrize(("chosen", "extra", "merged", "added"), [
    (["tests/test_a.py"], ["tests/test_b.py"], ["tests/test_a.py", "tests/test_b.py"], ["tests/test_b.py"]),
    (["tests/test_a.py"], ["tests/test_a.py"], ["tests/test_a.py"], []),
    (["tests/test_a.py"], ["tests"], ["tests"], ["tests"]),
    (["tests"], ["tests/test_a.py"], ["tests"], []),
    ([], ["tests/test_a.py", "tests"], ["tests"], ["tests"]),
])
def test_merge_keeps_each_path_once_and_none_below_another(chosen, extra, merged, added) -> None:
    assert dependents.merge(chosen, extra) == (merged, added)


def test_a_selection_runs_its_declared_dependents_after_its_groups() -> None:
    selection = Selection(test_order=("packages/a/tests", "scripts/tests"))
    assert selection.pytest_paths() is None
    selection.declare({"scripts/tests/test_targets.py": ["packages/a/src/x.py"]})
    assert selection.pytest_paths() == ["scripts/tests/test_targets.py"]
    assert selection.reasons == ["packages/a/src/x.py: declared dependent scripts/tests/test_targets.py"]
    selection.add("packages/a/src/x.py", {"packages/a/tests"}, "package")
    assert selection.pytest_paths() == ["packages/a/tests", "scripts/tests/test_targets.py"]
    selection.add("scripts/tests/test_x.py", {"scripts/tests"}, "root tests")
    assert selection.pytest_paths() == []


def test_a_declared_test_below_a_selected_group_runs_once() -> None:
    selection = Selection(test_order=("packages/a/tests", "scripts/tests", "tests"))
    selection.add("scripts/x.py", {"scripts/tests"}, "root")
    selection.declare({"scripts/tests/test_targets.py": ["scripts/x.py"]})
    assert selection.pytest_paths() == ["scripts/tests"]


class RewritingSelection(Selection):
    """A project's selection that runs a file subset in place of its root group, as hami-quant-trading's does."""

    def __init__(self) -> None:
        super().__init__(test_order=("packages/a/tests", "packages/b/tests", "scripts/tests"))
        self.root_files = ["scripts/tests/test_y.py"]

    def pytest_paths(self):
        paths = super().pytest_paths()
        if paths and "scripts/tests" in paths:
            paths = [path for path in paths if path != "scripts/tests"] + self.root_files
        return paths


def test_a_declared_test_survives_a_subclass_that_rewrites_a_group_into_files() -> None:
    selection = RewritingSelection()
    selection.add("packages/a/src/x.py", {"packages/a/tests", "scripts/tests"}, "package")
    selection.declare({"scripts/tests/test_mutation_targets.py": ["packages/a/src/x.py"]})
    assert selection.pytest_paths() == ["packages/a/tests", "scripts/tests/test_y.py",
                                        "scripts/tests/test_mutation_targets.py"]


def test_a_widened_selection_stays_the_full_suite() -> None:
    selection = Selection(test_order=("tests",))
    selection.widen("pyproject.toml", "config")
    selection.declare({"tests/test_reader.py": ["pyproject.toml"]})
    assert selection.pytest_paths() == []


def project(root: Path) -> Project:
    def select(paths, _deltas):
        selection = Selection(test_order=("tests", "other"))
        for path in paths:
            if path.startswith("other/"):
                selection.add(path, {"other"}, "other")
        return selection
    return Project(root=root, argv_file=root / "verify.json", select=select)


def test_a_project_adds_the_declared_dependents_to_every_selection(tmp_path: Path) -> None:
    tree = write_tree(tmp_path, '"tests/test_reader.py" = ["data/*.txt"]\n',
                      ("tests/test_reader.py", "data/a.txt", "other/test_o.py"))
    chosen = project(tree).select(["data/a.txt", "other/test_o.py"], None)
    assert chosen.pytest_paths() == ["other", "tests/test_reader.py"]
    assert "data/a.txt: declared dependent tests/test_reader.py" in chosen.reasons
    assert project(tree).select(["other/test_o.py"], None).pytest_paths() == ["other"]


def test_a_copied_project_adds_them_once(tmp_path: Path) -> None:
    tree = write_tree(tmp_path, '"tests/test_reader.py" = ["data/*.txt"]\n')
    copied = dataclasses.replace(project(tree), missing_remote="full")
    assert copied.select(["data/a.txt"], None).declared == {"tests/test_reader.py": ["data/a.txt"]}


def test_a_project_refuses_a_stale_declaration_at_its_first_selection(tmp_path: Path) -> None:
    tree = write_tree(tmp_path, '"tests/test_reader.py" = ["data/gone.txt"]\n')
    with pytest.raises(Refusal) as refused:
        project(tree).select(["data/a.txt"], None)
    assert (refused.value.token, refused.value.code) == (stages.CONFIG_INVALID, 9)
    assert refused.value.detail.endswith("[tool.atc-testkit] dependents: tests/test_reader.py: data/gone.txt: "
                                         "matches no file")

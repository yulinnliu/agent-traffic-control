"""docs/TESTKIT.md § ``atc test ID --mutants FILE``: the lines a mutant edits and the tests that ran them."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from atc_testkit import lines

SOURCE = (
    "import os\n"                                  # 1
    "\n"                                           # 2
    "\n"                                           # 3
    "@staticmethod\n"                              # 4
    "def inside(link, directory):\n"               # 5
    "    try:\n"                                   # 6
    "        return (link == directory\n"          # 7
    "                or _within(link, directory))\n"  # 8
    "    except (OSError, ValueError):\n"          # 9
    "        return False\n"                       # 10
    "    # a comment\n"                            # 11
    "    if link: return 1\n"                      # 12
)


@pytest.mark.unit
@pytest.mark.parametrize(("old", "new", "expected"), [
    ("or _within(link, directory))", "or True)", (8, 8)),
    ("        return (link == directory\n                or _within", "        return (link != directory\n                or _within", (7, 7)),
    ("    except (OSError, ValueError):\n        return False", "    except (OSError, ValueError):\n        return True", (10, 10)),
    ("    # a comment\n", "", (11, 11)),
    ("absent", "x", 0),
    ("link", "x", 4),
    ("", "x", 0),
])
def test_the_edited_lines_are_those_old_and_new_differ_in(old: str, new: str, expected) -> None:
    assert lines.edited_lines(SOURCE, old, new) == expected


@pytest.mark.unit
@pytest.mark.parametrize(("first", "last", "expected"), [
    (8, 8, set(range(7, 9))),        # a continuation line stands for its whole statement
    (5, 5, {4, 5}),                  # a def by its header, decorator included
    (9, 9, {9}),                     # an except clause by its header
    (10, 10, {10}),
    (11, 11, {11}),                  # a comment is in no statement
    (12, 12, {12}),                  # a one-line if: header and body share the line
    (6, 6, {6}),
    (1, 2, {1, 2}),
])
def test_edited_lines_widen_to_their_innermost_statement(first: int, last: int, expected: set[int]) -> None:
    assert lines.statement_lines(SOURCE, first, last) == expected


@pytest.mark.unit
def test_an_unparsable_file_keeps_the_edited_lines() -> None:
    assert lines.statement_lines("def (:\n", 1, 1) == {1}


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src/mod.py").write_text(SOURCE, encoding="utf-8")
    (root / "tests/test_a.py").write_text("", encoding="utf-8")
    (root / "tests/test_b.py").write_text("", encoding="utf-8")
    return root


def record_of(tree: Path, place: Path, rows: list[tuple[str, int]], order: list[str], outside=()) -> lines.Record:
    data = place / lines.DIRECTORY / lines.DATA
    module = lines.real(tree / "src/mod.py")
    text = "".join(f"{context}\t{module}\t{line}\n" for context, line in rows)
    text += "".join(f"\t{module}\t{line}\n" for line in outside)
    (data / "1-a.tsv").write_text(text + f"cut\t{module}\t1", encoding="utf-8")  # the last row was cut short
    (data / "started-1").write_text("", encoding="utf-8")
    (data / "order-1.txt").write_text("".join(f"{name}\n" for name in order), encoding="utf-8")
    found = lines.read(data)
    assert found is not None
    return found


def mutant(old: str, new: str = "X", path: str = "src/mod.py", **extra) -> dict:
    return {"label": "m", "edits": [{"path": path, "old": old, "new": new}], **extra}


A1, A2, B1 = "tests/test_a.py::test_one", "tests/test_a.py::test_two", "tests/test_b.py::test_one"
FILES = ["tests/test_a.py", "tests/test_b.py"]


@pytest.mark.unit
def test_a_mutant_runs_the_tests_that_ran_its_statement_in_baseline_order(tree: Path, tmp_path: Path) -> None:
    prepared = lines.prepare(tmp_path, tree, [mutant("or _within(link, directory))")])
    record = record_of(tree, tmp_path, [(B1, 7), (A2, 8), (A1, 12)], [A1, A2, B1])
    choice = lines.choose(mutant("or _within(link, directory))"), FILES, tree, prepared, record)
    assert (choice.kind, choice.tests, choice.lines) == (lines.BY_LINES, [A2, B1], {"src/mod.py": [[7, 8]]})
    assert record.files == {lines.real(tree / "src/mod.py")} and (lines.real(tree / "src/mod.py"), 1) not in record.tests


@pytest.mark.unit
def test_a_mutant_no_test_ran_is_not_covered_and_a_named_test_still_runs(tree: Path, tmp_path: Path) -> None:
    edit = "        return False"
    prepared = lines.prepare(tmp_path, tree, [mutant(edit)])
    record = record_of(tree, tmp_path, [(A1, 7)], [A1])
    choice = lines.choose(mutant(edit), FILES, tree, prepared, record)
    assert (choice.kind, choice.tests) == (lines.UNCOVERED, [])
    assert lines.uncovered(choice) == "no baseline test runs src/mod.py:10"
    named = lines.choose(mutant(edit), [*FILES, B1, "-k", "tests/test_b.py"], tree, prepared, record)
    assert (named.kind, named.tests) == (lines.BY_LINES, [B1, "-k", "tests/test_b.py"])


@pytest.mark.unit
def test_files_and_directories_narrow_and_other_items_stay(tree: Path, tmp_path: Path) -> None:
    prepared = lines.prepare(tmp_path, tree, [mutant("    if link: return 1")])
    record = record_of(tree, tmp_path, [(A1, 12), (B1, 12)], [A1, B1])
    choice = lines.choose(mutant("    if link: return 1"), ["tests", "-x", "tests/test_b.py"], tree, prepared, record)
    assert choice.tests == [A1, B1, "-x", "tests/test_b.py"]  # a path after an option stays as given
    only_b = lines.choose(mutant("    if link: return 1"), ["tests/test_b.py"], tree, prepared, record)
    assert only_b.tests == [B1]


@pytest.mark.unit
@pytest.mark.parametrize(("entry", "note"), [
    (mutant("a", path="README.md"), "README.md is not a Python file"),
    (mutant("x", path="src/other.py"), "src/other.py cannot be read as UTF-8 before the run"),
    (mutant("link"), "`old` occurs 4 times in src/mod.py before the run"),
    ({"label": "m", "edits": [{"path": "src/mod.py", "old": "import os", "new": "import sys"},
                              {"path": "src/mod.py", "old": "import sys", "new": "import re"}]},
     "`old` occurs 0 times in src/mod.py before the run"),  # an old the edit before it makes
    (mutant("import os"), "lines of src/mod.py run outside a test"),
])
def test_a_mutant_the_record_cannot_judge_runs_its_file_level_tests(tree: Path, tmp_path: Path, entry: dict,
                                                                    note: str) -> None:
    prepared = lines.prepare(tmp_path, tree, [entry])
    record = record_of(tree, tmp_path, [(A1, 7)], [A1], outside=[1])
    choice = lines.choose(entry, FILES, tree, prepared, record)
    assert (choice.kind, choice.tests, choice.note) == (lines.FALLBACK, FILES, note)


@pytest.mark.unit
def test_a_file_without_any_record_runs_its_file_level_tests(tree: Path, tmp_path: Path) -> None:
    (tree / "src/unseen.py").write_text("X = 1\n", encoding="utf-8")
    entry = mutant("X = 1", path="src/unseen.py")
    prepared = lines.prepare(tmp_path, tree, [entry])
    choice = lines.choose(entry, FILES, tree, prepared, record_of(tree, tmp_path, [(A1, 7)], [A1]))
    assert (choice.kind, choice.note) == (lines.FALLBACK, "no line record of src/unseen.py")


@pytest.mark.unit
def test_without_a_started_mark_there_is_no_record_and_a_rerun_starts_empty(tree: Path, tmp_path: Path) -> None:
    prepared = lines.prepare(tmp_path, tree, [mutant("import os")])
    (prepared.data / "1-old.tsv").write_text("t\tx\t1\n", encoding="utf-8")
    assert lines.read(prepared.data) is None
    prepared = lines.prepare(tmp_path, tree, [mutant("import os")])
    assert sorted(path.name for path in prepared.data.iterdir()) == []
    assert sorted(path.name for path in prepared.hook.iterdir()) == sorted(lines.HOOK_FILES)


@pytest.mark.unit
def test_the_recording_environment_and_a_nested_run_keep_the_hook(tree: Path, tmp_path: Path) -> None:
    prepared = lines.prepare(tmp_path, tree, [mutant("import os"), mutant("a", path="README.md")])
    env = lines.environment(prepared, tree, {"PYTHONPATH": "src", "OTHER": "1"})
    assert env["PYTHONPATH"] == os.pathsep.join([str(prepared.hook), "src"])
    assert env[lines.FILES_VARIABLE] == str(tree / "src/mod.py") and env["OTHER"] == "1"
    assert lines.keep_hook(env, "tree/src") == os.pathsep.join([str(prepared.hook), "tree/src"])
    assert lines.keep_hook({"PYTHONPATH": "x"}, "tree/src") == "tree/src"


@pytest.mark.unit
def test_long_test_lists_go_through_an_argfile(tmp_path: Path) -> None:
    short = ["tests/test_a.py::t"]
    assert lines.arguments(short, tmp_path, "001") == short
    many = [f"tests/test_a.py::t[{index}]" for index in range(lines.ARGFILE_THRESHOLD // 20)]
    [argfile] = lines.arguments(many, tmp_path, "002")
    assert argfile == f"@{tmp_path / lines.DIRECTORY / '002.args'}"
    assert (tmp_path / lines.DIRECTORY / "002.args").read_text(encoding="utf-8").splitlines() == many


@pytest.mark.integration
def test_pytest_runs_nodeids_with_spaces_and_quotes_from_an_argfile(tmp_path: Path) -> None:
    (tmp_path / "test_p.py").write_text(
        "import pytest\n\n\n@pytest.mark.parametrize('x', [1, 2, 3], ids=['a b', 'q\"t', 'other'])\n"
        "def test_p(x):\n    print('RAN', x)\n", encoding="utf-8")
    names = ["test_p.py::test_p[a b]", 'test_p.py::test_p[q"t]']
    argfile = tmp_path / "a.args"
    argfile.write_text("\n".join(names) + "\n", encoding="utf-8")
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider", f"@{argfile}"],
                         cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
                         env={key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")})
    assert run.returncode == 0 and "2 passed" in run.stdout, run.stdout + run.stderr
    assert "RAN 1" in run.stdout and "RAN 2" in run.stdout and "RAN 3" not in run.stdout

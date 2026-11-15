"""docs/TESTKIT.md § ``atc test ID --mutants FILE``: the lines a mutant edits and the tests that ran them."""
from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from atc_testkit import lines, testrun
from test_testkit_tmp import link_directory, unlink_directory

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
    ("    except (OSError, ValueError):\n        return False",
     "    except (OSError, ValueError):\n        log()\n        return False", (9, 10)),  # a line added: both sides
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
    (11, 11, {11, 12}),              # a comment between statements also stands for the next one
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
    gone = lines.choose(mutant("    if link: return 1"), ["tests/test_absent_from_tree.py", "tests/test_b.py"], tree, prepared,
                        record)
    assert gone.tests == [B1, "tests/test_absent_from_tree.py"]  # a path that is not in the tree stays as given


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


CLAUSES = (
    "def f(x):\n"          # 1
    "    try:\n"           # 2
    "        a = 1\n"      # 3
    "    except E:\n"      # 4
    "        a = 2\n"      # 5
    "    else:\n"          # 6
    "        a = 3\n"      # 7
    "    finally:\n"       # 8
    "        a = 4\n"      # 9
    "    if x:\n"          # 10
    "        b = 1\n"      # 11
    "    else:\n"          # 12
    "        b = 2\n"      # 13
)


@pytest.mark.unit
@pytest.mark.parametrize(("line", "expected"), [(6, {6, 7}), (8, {8, 9}), (12, {12, 13}), (4, {4})])
def test_a_clause_keyword_stands_for_the_first_statement_of_its_clause(line: int, expected: set[int]) -> None:
    """``else:`` and ``finally:`` run no line event, so a mutant of one alone is judged by its clause's body."""
    assert lines.statement_lines(CLAUSES, line, line) == expected
    assert lines.edited_lines(CLAUSES, "    finally:\n        a = 4", "    except Exception:\n        a = 4") == (8, 8)


@pytest.mark.unit
def test_a_recorder_that_never_started_marks_nothing_and_enters_no_test(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("atc_lines_recorder_probe")
    exec(compile(lines.RECORDER, "atc_lines_recorder", "exec"), module.__dict__)
    monkeypatch.setenv(lines.CONTEXT_VARIABLE, "before")
    module.mark_started()
    module.enter("tests/test_a.py::test_one")
    module.leave()
    assert not module.active() and os.environ[lines.CONTEXT_VARIABLE] == "before"


@pytest.mark.unit
def test_a_baseline_whose_recorder_did_not_start_judges_every_mutant_by_its_file_level_tests(
        tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    prepared = lines.prepare(tmp_path, tree, [mutant("import os")])
    (prepared.data / "1-a.tsv").write_text(f"t\t{lines.real(tree / 'src/mod.py')}\t1\n", encoding="utf-8")
    assert testrun._choices([mutant("import os")], FILES, tree, tmp_path, prepared) == {}
    assert capsys.readouterr().out == lines.NOT_RECORDED + "\n"
    assert not (tmp_path / lines.COVERED).exists()


@pytest.mark.unit
def test_the_lines_directory_is_cleared_by_the_one_recursive_delete(tree: Path, tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    lines.prepare(tmp_path, tree, [])
    seen = []
    monkeypatch.setattr(lines.scratch, "remove_tree", lambda directory, expected, say: seen.append(directory) or "x")
    with pytest.raises(lines.Refusal) as caught:
        lines.prepare(tmp_path, tree, [])
    assert caught.value.token == "LINES_NOT_CLEARED" and seen == [tmp_path / lines.DIRECTORY]


@pytest.mark.integration
def test_a_link_in_the_lines_directory_stops_the_run_and_its_target_survives(tree: Path, tmp_path: Path) -> None:
    prepared = lines.prepare(tmp_path, tree, [])
    target = tmp_path / "outside"
    (target / "kept.txt").parent.mkdir()
    (target / "kept.txt").write_text("x", encoding="utf-8")
    link = prepared.data / "out"
    link_directory(link, target)
    try:
        with pytest.raises(lines.Refusal) as caught:
            lines.prepare(tmp_path, tree, [])
        assert caught.value.token == "LINES_NOT_CLEARED" and (target / "kept.txt").read_text(encoding="utf-8") == "x"
    finally:
        if os.path.lexists(link):
            unlink_directory(link)


def _python(tmp_path: Path, pythonpath: list[Path], code: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTEST_", "ATC_LINES_"))}
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in pythonpath)
    return subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True,
                          encoding="utf-8")


@pytest.mark.integration
def test_the_hook_hands_over_to_the_sitecustomize_it_shadows_and_lets_its_errors_show(tree: Path,
                                                                                    tmp_path: Path) -> None:
    hook = lines.prepare(tmp_path, tree, []).hook
    shadowed = tmp_path / "shadowed"
    shadowed.mkdir()
    (shadowed / "sitecustomize.py").write_text("import builtins\nbuiltins.SHADOWED_RAN = True\n", encoding="utf-8")
    run = _python(tmp_path, [hook, shadowed], "import builtins, sys; print(getattr(builtins, 'SHADOWED_RAN', False), "
                                              "'atc_lines_recorder' in sys.modules)")
    assert run.stdout.split() == ["True", "True"] and "Error in sitecustomize" not in run.stderr, run.stderr
    (shadowed / "sitecustomize.py").write_text("import a_module_that_is_not_there\n", encoding="utf-8")
    run = _python(tmp_path, [hook, shadowed], "print('ran')")
    assert run.stdout.strip() == "ran" and "a_module_that_is_not_there" in run.stderr, run.stderr
    run = _python(tmp_path, [hook], "import sys; print(sys.modules['sitecustomize'].__file__)")
    assert Path(run.stdout.strip()) == hook / "sitecustomize.py", run.stderr  # nothing shadowed: ours stays


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

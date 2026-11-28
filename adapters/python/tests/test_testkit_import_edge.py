"""docs/TESTKIT.md § Default import edge: the related tests of a tree with no ``selector``."""
from __future__ import annotations

from pathlib import Path

import pytest

from atc_testkit import import_edge

pytestmark = pytest.mark.unit

PACKAGE = {
    "src/pkg/__init__.py": "from .ledger import Ledger\n",
    "src/pkg/ledger.py": "class Ledger:\n    pass\n",
    "src/pkg/other.py": "Y = 1\n",
    "tests/test_direct.py": "from pkg.ledger import Ledger\n",
    "tests/test_root.py": "from pkg import Ledger\n",
    "tests/test_star.py": "from pkg import *\n",
    "tests/test_inner.py": "def test_inner():\n    import pkg.other\n",
    "tests/test_deleted_module.py": "import pkg.deleted\n",
    "tests/test_none.py": "import json\n",
    "tests/test_relative_outside.py": "from .ledger import Ledger\n",
    "tests/helper.py": "H = 1\n",
    "tests/test_helper.py": "import helper\n",
    "other/test_far.py": "import helper\n",
    "src/pkg/tests/__init__.py": "",
    "src/pkg/tests/test_relative.py": "from ..ledger import Ledger\n",
    ".hidden/test_hidden.py": "from pkg.ledger import Ledger\n",
    "_private/test_private.py": "from pkg.ledger import Ledger\n",
    "node_modules/test_module.py": "from pkg.ledger import Ledger\n",
}


def make(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(content, encoding="utf-8")
    return root


def test_the_roots_are_the_source_roots_and_pytest_pythonpath_inside_the_tree(tmp_path: Path) -> None:
    tree = make(tmp_path / "tree", {
        "packages/b/src/b.py": "", "packages/a/src/a.py": "", "packages/c/x.py": "", "src/s.py": "",
        "scripts/run.py": "", "pyproject.toml":
            '[tool.pytest.ini_options]\npythonpath = ["scripts", ".", "../outside", "missing", "src"]\n'})
    (tmp_path / "outside").mkdir()
    assert import_edge.roots(tree) == ["packages/a/src", "packages/b/src", "src", "scripts", ""]
    (tree / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = "scripts src"\n', encoding="utf-8")
    assert import_edge.roots(tree) == ["packages/a/src", "packages/b/src", "src", "scripts"]


@pytest.mark.parametrize(("path", "name"), [
    ("src/pkg/__init__.py", "pkg"),
    ("src/pkg/ledger.py", "pkg.ledger"),
    ("scripts/tests/helper.py", "helper"),
    ("scripts/run.py", "run"),
    ("top.py", "top"),
    ("src/pkg/data.json", None),
    ("src/my-pkg/m.py", None),
])
def test_a_python_path_names_its_module_under_the_deepest_root(path: str, name: str | None) -> None:
    assert import_edge.module_name(path, ["src", "", "scripts", "scripts/tests"]) == name
    assert import_edge.module_name("docs/x.py", ["src"]) is None


@pytest.mark.parametrize(("changed", "expected"), [
    (["src/pkg/ledger.py"], ["src/pkg/tests/test_relative.py", "tests/test_direct.py", "tests/test_root.py"]),
    (["src/pkg/__init__.py"], ["src/pkg/tests/test_relative.py", "tests/test_deleted_module.py",
                               "tests/test_direct.py", "tests/test_inner.py", "tests/test_root.py",
                               "tests/test_star.py"]),
    (["src/pkg/other.py"], ["tests/test_inner.py"]),
    (["src/pkg/deleted.py"], ["tests/test_deleted_module.py"]),
    (["tests/helper.py"], ["tests/test_helper.py"]),
    (["tests/test_none.py", "README.md", "src/pkg/data.json"], ["tests/test_none.py"]),
])
def test_a_changed_module_selects_the_test_files_one_import_from_it(tmp_path: Path, changed: list[str],
                                                                      expected: list[str]) -> None:
    """Parents count, imports inside functions count, a package's ``__init__.py`` re-export counts one hop by its
    explicit names (``import *`` is not expanded), a deleted module still selects, a same-directory helper selects
    only its own directory, and ``.``/``_``/``node_modules`` directories are not scanned."""
    tree = make(tmp_path / "tree", PACKAGE)
    edge = import_edge.direct(tree, changed)
    assert (edge.tests, edge.full, edge.notes) == (expected, False, (import_edge.DEFAULT_NOTE,))


def test_a_test_file_that_does_not_parse_is_selected_with_a_line(tmp_path: Path) -> None:
    tree = make(tmp_path / "tree", {**PACKAGE, "tests/test_broken.py": "def (\n"})
    edge = import_edge.direct(tree, ["src/pkg/other.py"])
    assert edge.tests == ["tests/test_broken.py", "tests/test_inner.py"]
    assert edge.notes == (import_edge.DEFAULT_NOTE, "UNPARSED tests/test_broken.py")


CONFTESTS = {
    "src/pkg/__init__.py": "", "src/pkg/ledger.py": "", "scripts/guard.py": "",
    "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["scripts"]\n',
    "tests/sub/test_a.py": "", "tests/sub/test_b.py": "", "tests/test_c.py": "",
    "tests/fixtures/conftest.py": "from pkg import ledger\n", "tests/fixtures/test_d.py": "",
}


@pytest.mark.parametrize(("files", "changed", "expected"), [
    ({"tests/sub/conftest.py": ""}, ["tests/sub/conftest.py", "tests/sub/test_a.py"], ["tests/sub"]),
    ({}, ["src/pkg/ledger.py"], ["tests/fixtures"]),
    ({}, ["gone/conftest.py"], []),
])
def test_a_changed_subdirectory_conftest_selects_its_directory(tmp_path: Path, files: dict[str, str],
                                                               changed: list[str], expected: list[str]) -> None:
    """Paths below it drop; a module a conftest imports counts as that conftest; a removed directory selects
    nothing."""
    tree = make(tmp_path / "tree", {**CONFTESTS, **files})
    edge = import_edge.direct(tree, changed)
    assert (edge.tests, edge.full) == (expected, False)


@pytest.mark.parametrize(("root", "changed"), [
    ("", ["conftest.py"]),
    ("pytest_plugins = ('atc_testkit.plugin', 'guard')\n", ["scripts/guard.py"]),
    ("pytest_plugins = 'guard'\n", ["scripts/guard.py"]),
])
def test_the_tree_root_conftest_and_the_modules_it_loads_run_the_full_suite(tmp_path: Path, root: str,
                                                                            changed: list[str]) -> None:
    tree = make(tmp_path / "tree", {**CONFTESTS, "conftest.py": root})
    edge = import_edge.direct(tree, changed)
    assert (edge.tests, edge.full) == ([], True)
    assert edge.notes == (import_edge.DEFAULT_NOTE, import_edge.FULL_NOTE)
    assert import_edge.direct(tree, ["scripts/other.py"]).full is False

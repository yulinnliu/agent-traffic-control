"""The tests of this adapter that a change can affect, and why each one runs.

Writers and reviewers run this selection; the integration verifier runs the
full suite (`README.md` § Selected tests). Paths are relative to the workspace
root. Selection is by test file and reads the static structure of the code:

- A Python module of `scripts/`, `src/atc_cli/` or `atc-testkit/src/` selects
  every test file that reaches it, through imports or by naming it in a string
  (a script's module name or file name), transitively.
- `conftest.py`, the pytest plugin and everything they import at load time,
  `pyproject.toml`, `uv.lock` and the unmarked baseline select the full suite.
- A path under `shared/<area>/` selects the test files whose reach names
  `<area>` or `shared/<area>`; another path selects the test files whose reach
  names its file name.
- A path no rule maps selects the full suite: a module no test reaches, a
  removed module, a `shared/` area no test reads, and a non-Markdown file no
  test names. Markdown no test names selects nothing.

Usage, from `adapters/python`::

    uv run --locked python scripts/select_tests.py                  # this checkout against main
    uv run --locked python scripts/select_tests.py --base REF       # against another base
    uv run --locked python scripts/select_tests.py --paths A B ...  # explain given paths
    uv run --locked python scripts/select_tests.py --run [-- PYTEST ARGS]
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from atc_testkit.selection import Selection, string_literals

ADAPTER_DIR = Path(__file__).resolve().parents[1]
WORKSPACE = ADAPTER_DIR.parents[1]
ADAPTER = "adapters/python"
TESTS = f"{ADAPTER}/tests"
CONFTEST = f"{TESTS}/conftest.py"
PLUGIN_MODULE = "atc_testkit.plugin"
SELF_TEST = "test_select_tests.py"
# Files that name other modules as data, not as inputs: only their imports are edges.
IMPORTS_ONLY = frozenset({"select_tests.py", SELF_TEST})
FULL_FILES = frozenset({
    CONFTEST,
    f"{ADAPTER}/pyproject.toml",
    f"{ADAPTER}/uv.lock",
    f"{ADAPTER}/atc-testkit/pyproject.toml",
    f"{TESTS}/unmarked_baseline.txt",
})
# Source roots and the package each holds; a top-level file of `scripts/` is its own module.
SOURCE_ROOTS = {
    f"{ADAPTER}/scripts": None,
    f"{ADAPTER}/src/atc_cli": "atc_cli",
    f"{ADAPTER}/atc-testkit/src/atc_testkit": "atc_testkit",
}
SHARED = "shared"


@dataclass
class Module:
    name: str
    path: str
    package: str | None
    imports: set[str] = field(default_factory=set)
    load_imports: set[str] = field(default_factory=set)
    literals: list[str] = field(default_factory=list)


def module_name(path: str) -> tuple[str, str | None] | None:
    """The importable name and package of a source file, or None outside the source roots."""
    pure = PurePosixPath(path)
    if pure.suffix != ".py":
        return None
    if str(pure.parent) == TESTS:
        return (pure.stem, None) if pure.name != "conftest.py" else ("conftest", None)
    for root, package in SOURCE_ROOTS.items():
        if str(pure.parent) != root:
            continue
        if package is None:
            return pure.stem, None
        return (package if pure.stem == "__init__" else f"{package}.{pure.stem}"), package
    return None


def _resolve(node: ast.ImportFrom, package: str | None) -> str:
    if node.level and package:
        return package if not node.module else f"{package}.{node.module}"
    return node.module or ""


def _at_load(tree: ast.Module) -> set[int]:
    """Ids of import statements that run when the module loads, outside any function."""
    found: set[int] = set()
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.add(id(node))
        stack.extend(ast.iter_child_nodes(node))
    return found


def parse(path: Path, name: str, rel: str, package: str | None) -> Module:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    module = Module(name, rel, package, literals=string_literals(source).split("\n"))
    at_load = _at_load(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(node, package)
            names = {base, *(f"{base}.{alias.name}" for alias in node.names)}
        else:
            continue
        module.imports |= names
        if id(node) in at_load:
            module.load_imports |= names
    return module


class Graph:
    """The adapter's modules and test files with the edges between them."""

    def __init__(self, workspace: Path = WORKSPACE):
        self.workspace = workspace
        self.modules: dict[str, Module] = {}
        for rel_dir in (TESTS, *SOURCE_ROOTS):
            for file in sorted((workspace / rel_dir).glob("*.py")):
                rel = f"{rel_dir}/{file.name}"
                name, package = module_name(rel)
                self.modules[name] = parse(file, name, rel, package)
        self.by_path = {m.path: m for m in self.modules.values()}
        self.tests = tuple(sorted(m.path for m in self.modules.values()
                                  if m.path.startswith(f"{TESTS}/test_")))
        self.edges = {name: self._edges(module) for name, module in self.modules.items()}
        self.load_set = self._load_set()
        self.reach = {test: self._closure(self.by_path[test].name) for test in self.tests}

    def _edges(self, module: Module) -> set[str]:
        found = {name for name in module.imports if name in self.modules}
        if PurePosixPath(module.path).name in IMPORTS_ONLY:
            return found - {module.name}
        literals = set(module.literals)
        text = "\n".join(module.literals)
        for other in self.modules.values():
            if other is module or other.name == "conftest":
                continue
            if other.name in literals:
                found.add(other.name)
            elif other.package is None and PurePosixPath(other.path).name in text:
                found.add(other.name)
            elif other.package is not None and other.package == module.package \
                    and other.name.rsplit(".", 1)[-1] in literals:
                found.add(other.name)
        return found - {module.name}

    def _closure(self, start: str) -> set[str]:
        seen, stack = {start}, [start]
        while stack:
            for name in self.edges[stack.pop()] - seen:
                seen.add(name)
                stack.append(name)
        return seen

    def _load_set(self) -> set[str]:
        """Modules every test session loads: conftest and the plugin with all their imports, then load-time imports."""
        roots = {"conftest", PLUGIN_MODULE}
        seen = set(roots)
        stack = [n for r in roots for n in self.modules[r].imports if n in self.modules]
        while stack:
            name = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            stack.extend(n for n in self.modules[name].load_imports if n in self.modules)
        return seen

    def literals_of(self, test: str) -> tuple[set[str], str]:
        lits = [lit for name in self.reach[test] for lit in self.modules[name].literals
                if PurePosixPath(self.modules[name].path).name not in IMPORTS_ONLY]
        return set(lits), "\n".join(lits)

    def naming(self, needles: Iterable[str], exact: Iterable[str] = ()) -> set[str]:
        """Test files whose reach names one of ``needles`` in a string, or equals one of ``exact``."""
        needles, exact = tuple(needles), set(exact)
        found = set()
        for test in self.tests:
            literals, text = self.literals_of(test)
            if literals & exact or any(n in text for n in needles):
                found.add(test)
        return found


class TestSelection(Selection):
    """The shared selection result, keyed by test file, remembering why each test file runs."""

    def __init__(self, tests: tuple[str, ...]):
        super().__init__(test_order=tests)
        self.causes: dict[str, list[str]] = {}

    def add(self, path: str, dirs: Iterable[str], why: str) -> None:
        dirs = set(dirs)
        super().add(path, dirs, why)
        for test in dirs:
            self.causes.setdefault(test, []).append(path)


def classify(path: str, selection: TestSelection, graph: Graph) -> None:
    """Add ``path``'s test impact to ``selection``."""
    pure = PurePosixPath(path)
    if path in FULL_FILES:
        selection.widen(path, "collection, dependency or marker input of every test")
        return
    if str(pure.parent) == TESTS and pure.name.startswith("test_") and pure.suffix == ".py":
        if path in graph.tests:
            selection.add(path, {path}, "the test file itself")
        else:
            selection.add(path, (), "removed test file")
        return
    named = module_name(path)
    if named is not None:
        name = named[0]
        if name not in graph.modules:
            selection.widen(path, "removed module")
        elif name in graph.load_set:
            selection.widen(path, "loaded by conftest.py or the pytest plugin in every test")
        else:
            reached = {test for test in graph.tests if name in graph.reach[test]}
            if reached:
                selection.add(path, reached, f"test files that reach {name}")
            else:
                selection.widen(path, f"no test file reaches {name}")
        return
    if pure.parts[0] == SHARED and len(pure.parts) >= 3:
        area = pure.parts[1]
        readers = graph.naming((f"{SHARED}/{area}",), exact=(area,))
        if readers:
            selection.add(path, readers, f"test files that read {SHARED}/{area}")
        else:
            selection.widen(path, f"no test file reads {SHARED}/{area}")
        return
    readers = graph.naming((pure.name,))
    if readers:
        selection.add(path, readers, f"test files that name {pure.name}")
    elif pure.suffix.lower() == ".md":
        selection.add(path, (), "Markdown no test file names")
    else:
        selection.widen(path, "no selection rule")


def select(paths: Iterable[str], graph: Graph | None = None) -> TestSelection:
    graph = graph or Graph()
    selection = TestSelection(graph.tests)
    for path in paths:
        classify(path.replace("\\", "/"), selection, graph)
    return selection


def changed_paths(base: str, workspace: Path = WORKSPACE) -> list[str]:
    """Paths changed from the merge base of ``base`` and HEAD to the working tree, untracked files included."""
    def git(*args: str) -> list[str]:
        out = subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True,
                             text=True, encoding="utf-8").stdout
        return [line for line in out.splitlines() if line]
    merge_base = git("merge-base", base, "HEAD")[0]
    return sorted(set(git("diff", "--name-only", "--no-renames", merge_base))
                  | set(git("ls-files", "--others", "--exclude-standard")))


def pytest_args(selection: TestSelection) -> list[str] | None:
    """Pytest paths relative to the adapter, [] for the full suite, None for no tests."""
    paths = selection.pytest_paths()
    if paths is None or paths == []:
        return paths
    return [path.removeprefix(f"{ADAPTER}/") for path in paths]


def describe(selection: TestSelection) -> str:
    args = pytest_args(selection)
    total = len(selection.test_order)
    if args is None:
        head = "no test file selected"
    elif args == []:
        head = f"full suite, {total} test files"
    else:
        head = f"{len(args)} of {total} test files"
    lines = [f"test selection: {head}"]
    if args:
        lines.append("tests:")
        lines += [f"  {arg}  <- {', '.join(selection.causes[f'{ADAPTER}/{arg}'])}" for arg in args]
    lines.append("paths:")
    lines += [f"  {reason}" for reason in selection.reasons] or ["  (no changed path)"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    extra: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, extra = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--base", default="main", help="compare HEAD and the working tree with this base (default main)")
    source.add_argument("--paths", nargs="+", help="explain these workspace-relative paths instead")
    parser.add_argument("--run", action="store_true", help="run pytest on the selection; arguments after -- go to pytest")
    args = parser.parse_args(argv)
    paths = args.paths if args.paths else changed_paths(args.base)
    selection = select(paths)
    print(describe(selection), flush=True)
    if not args.run:
        return 0
    targets = pytest_args(selection)
    if targets is None:
        return 0
    return subprocess.run([sys.executable, "-m", "pytest", *targets, *extra], cwd=ADAPTER_DIR).returncode


if __name__ == "__main__":
    sys.exit(main())

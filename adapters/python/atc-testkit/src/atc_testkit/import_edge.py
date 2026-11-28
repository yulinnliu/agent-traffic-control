"""The related tests of a tree that declares no ``selector``: the test files one import from a changed module
(docs/TESTKIT.md § Default import edge). Read with ``ast``; no project code is imported."""
from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath

from . import config, dependents

DEFAULT_NOTE = "SELECTOR default import edge"
FULL_NOTE = "FULL conftest.py at the tree root"
UNPARSED = "UNPARSED"
CONFTEST = "conftest.py"
SKIPPED_DIRECTORY = "node_modules"


@dataclass(frozen=True)
class Edge:
    """The test paths a selection picked, relative to the tree; ``full`` when the related run is the full suite;
    and the lines to print before ``RELATED``."""
    tests: list[str]
    full: bool = False
    notes: tuple[str, ...] = ()


@dataclass
class _Source:
    names: set[str] = field(default_factory=set)
    imports: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    plugins: set[str] = field(default_factory=set)


def _prefixes(name: str) -> set[str]:
    parts = name.split(".")
    return {".".join(parts[:end]) for end in range(1, len(parts) + 1) if all(parts[:end])}


def _package(tree: Path, relative: PurePosixPath) -> list[str] | None:
    """The package parts of a file whose directories hold ``__init__.py`` up from it; ``None`` when its own
    directory holds none."""
    parts: list[str] = []
    directory = relative.parent
    while str(directory) not in ("", ".") and (tree / directory / "__init__.py").is_file():
        parts.insert(0, directory.name)
        directory = directory.parent
    return parts or None


def _parse(tree: Path, relative: str) -> _Source:
    """Every import of the file, wherever it stands, with its parent packages; the names each ``from`` import takes;
    and the string entries of a ``pytest_plugins`` assignment. A file that does not parse raises."""
    path = PurePosixPath(relative)
    module = ast.parse((tree / path).read_bytes())
    source = _Source()
    package = None
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                source.names |= _prefixes(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = package if package is not None else (_package(tree, path) or [])
                if not package or node.level - 1 > len(package):
                    continue
                base = ".".join([*package[:len(package) - (node.level - 1)], *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            taken = tuple(alias.name for alias in node.names if alias.name != "*")
            source.names |= _prefixes(base) | {name for alias in taken for name in _prefixes(f"{base}.{alias}")}
            source.imports.append((base, taken))
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "pytest_plugins"
                                                  for target in node.targets):
            values = node.value.elts if isinstance(node.value, (ast.Tuple, ast.List)) else [node.value]
            source.plugins |= {value.value for value in values
                               if isinstance(value, ast.Constant) and isinstance(value.value, str)}
    return source


def roots(tree: Path) -> list[str]:
    """The module roots of the tree, relative to it: ``packages/*/src``, ``src`` and the directories of pytest's
    ``pythonpath`` inside the tree ("" for the tree itself)."""
    found = [path.relative_to(tree).as_posix() for path in sorted(tree.glob("packages/*/src")) if path.is_dir()]
    found += ["src"] if (tree / "src").is_dir() else []
    pythonpath = config.read_pyproject(tree).get("tool", {}).get("pytest", {}).get("ini_options", {}).get("pythonpath")
    entries = pythonpath.split() if isinstance(pythonpath, str) else pythonpath if isinstance(pythonpath, list) else []
    resolved_tree = tree.resolve()
    for entry in entries:
        if not isinstance(entry, str):
            continue
        directory = (tree / entry).resolve()
        if directory == resolved_tree:
            found.append("")
        elif directory.is_dir() and resolved_tree in directory.parents:
            found.append(directory.relative_to(resolved_tree).as_posix())
    return list(dict.fromkeys(found))


def module_name(path: str, module_roots: Sequence[str]) -> str | None:
    """The module a ``.py`` path under the deepest of ``module_roots`` names (its package for ``__init__.py``)."""
    pure = PurePosixPath(path)
    if pure.suffix != ".py":
        return None
    holding = [root for root in module_roots if root == "" or path.startswith(f"{root}/")]
    if not holding:
        return None
    root = max(holding, key=lambda item: len(item))
    parts = list(pure.relative_to(root).parts) if root else list(pure.parts)
    parts[-1] = pure.stem
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts and all(part.isidentifier() for part in parts) else None


def scan(tree: Path) -> tuple[list[str], list[str]]:
    """The tree's ``test_*.py`` and ``conftest.py`` files, relative to it, outside directories that start with ``.``
    or ``_`` and ``node_modules``."""
    tests, conftests = [], []
    for directory, names, files in os.walk(tree):
        names[:] = sorted(name for name in names if not name.startswith((".", "_")) and name != SKIPPED_DIRECTORY)
        relative = Path(directory).relative_to(tree).as_posix()
        for name in sorted(files):
            path = name if relative == "." else f"{relative}/{name}"
            if name.startswith("test_") and name.endswith(".py"):
                tests.append(path)
            elif name == CONFTEST:
                conftests.append(path)
    return tests, conftests


def _directory(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


class _Reexports:
    """The submodule each name of a package's ``__init__.py`` comes from, read once per package."""

    def __init__(self, tree: Path, module_roots: Sequence[str]):
        self.tree, self.roots, self.cache = tree, module_roots, {}

    def of(self, package: str) -> dict[str, str]:
        if package not in self.cache:
            self.cache[package] = self._read(package)
        return self.cache[package]

    def _read(self, package: str) -> dict[str, str]:
        for root in self.roots:
            relative = "/".join([*([root] if root else []), *package.split("."), "__init__.py"])
            if (self.tree / relative).is_file():
                try:
                    module = ast.parse((self.tree / relative).read_bytes())
                except (SyntaxError, ValueError):
                    return {}
                names = {}
                for node in ast.walk(module):
                    if not isinstance(node, ast.ImportFrom) or not node.module:
                        continue
                    if node.level == 1:
                        origin = f"{package}.{node.module}"
                    elif node.level == 0 and node.module.startswith(f"{package}."):
                        origin = node.module
                    else:
                        continue
                    names.update({alias.asname or alias.name: origin for alias in node.names if alias.name != "*"})
                return names
        return {}


def changed_tests(tree: Path, paths: Iterable[str]) -> list[str]:
    """The ``test_*.py`` files among ``paths`` (relative to the tree) that exist in it."""
    return [path for path in paths if PurePosixPath(path).name.startswith("test_") and path.endswith(".py")
            and (tree / path).is_file()]


def direct(tree: Path, paths: Iterable[str]) -> Edge:
    """The changed test files, the test files one import from a changed module and the directories of changed
    ``conftest.py`` files, for ``paths`` relative to the tree (docs/TESTKIT.md § Default import edge)."""
    paths = [path.replace("\\", "/") for path in paths]
    module_roots = roots(tree)
    tests, conftests = scan(tree)
    test_directories = {_directory(test) for test in tests}
    changed: set[str] = set()
    helpers: set[tuple[str, str]] = set()
    for path in paths:
        name = module_name(path, module_roots)
        if name is not None:
            changed.add(name)
        pure = PurePosixPath(path)
        directory = _directory(path)
        if (pure.suffix == ".py" and not pure.name.startswith("test_") and pure.name != CONFTEST
                and directory in test_directories and not (tree / directory / "__init__.py").is_file()):
            helpers.add((directory, pure.stem))
    reexports = _Reexports(tree, module_roots)
    chosen = changed_tests(tree, paths)
    notes = [DEFAULT_NOTE]
    for test in tests:
        if test in chosen:
            continue
        try:
            source = _parse(tree, test)
        except (OSError, SyntaxError, ValueError):
            chosen.append(test)
            notes.append(f"{UNPARSED} {test}")
            continue
        names = set(source.names)
        for base, taken in source.imports:
            names |= {origin for name, origin in reexports.of(base).items() if name in taken} if base else set()
        if names & changed or any(directory == _directory(test) and stem in names for directory, stem in helpers):
            chosen.append(test)
    affected = {path for path in paths if PurePosixPath(path).name == CONFTEST}
    for conftest in conftests:
        if conftest in affected:
            continue
        try:
            source = _parse(tree, conftest)
        except (OSError, SyntaxError, ValueError):
            continue
        loaded = source.names | source.plugins
        if loaded & changed or any(directory == _directory(conftest) and stem in loaded
                                   for directory, stem in helpers):
            affected.add(conftest)
    if any(_directory(conftest) == "" for conftest in affected):
        return Edge([], True, (*notes, FULL_NOTE))
    directories = [_directory(conftest) for conftest in sorted(affected) if (tree / _directory(conftest)).is_dir()]
    merged, _added = dependents.merge(sorted(chosen), directories)
    return Edge(merged, False, tuple(notes))

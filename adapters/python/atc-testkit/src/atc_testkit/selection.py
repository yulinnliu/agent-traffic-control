"""Policy-driven selection with conservative fallback and explainable results."""
from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .git import BaselineDelta


@dataclass(frozen=True)
class SelectionPolicy:
    dependencies: Mapping[str, tuple[str, ...]]
    test_order: tuple[str, ...]
    root_tests: str
    full_files: frozenset[str]
    full_prefixes: tuple[str, ...]
    root_prefixes: tuple[str, ...]
    root_files: frozenset[str]
    ci_files: frozenset[str]
    referenced_prefixes: tuple[str, ...]
    referenced_files: frozenset[str]
    baseline_files: frozenset[str] = frozenset()
    package_inputs: Mapping[str, str] = field(default_factory=dict)
    fixture_inputs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    critical_prefixes: tuple[str, ...] = ()
    full_reason: str = "shared test/dependency/runtime input"
    shared_test_reason: str = "shared pytest infrastructure"

    def __post_init__(self) -> None:
        if not self.test_order or len(set(self.test_order)) != len(self.test_order):
            raise ValueError("test_order must contain unique test groups")
        if self.baseline_files & self.full_files:
            raise ValueError("baseline files are classified by their delta, not as full-suite inputs")
        if self.root_tests not in self.test_order:
            raise ValueError(f"unknown root test group: {self.root_tests}")
        for package, dependencies in self.dependencies.items():
            unknown = set(dependencies) - self.dependencies.keys()
            if unknown:
                raise ValueError(f"unknown dependencies for {package}: {sorted(unknown)}")
        for prefix, package in self.package_inputs.items():
            self._validate_consumers(prefix, (package,))
        for prefix, packages in self.fixture_inputs.items():
            self._validate_consumers(prefix, packages)

    def _validate_consumers(self, prefix: str, packages: tuple[str, ...]) -> None:
        if not packages:
            raise ValueError(f"input mapping has no test consumers: {prefix}")
        unknown = {package for package in packages
                   if package not in self.dependencies or package_tests(package) not in self.test_order}
        if unknown:
            raise ValueError(f"unknown test consumers for {prefix}: {sorted(unknown)}")

    @property
    def tested_packages(self) -> frozenset[str]:
        return frozenset(p for p in self.dependencies if package_tests(p) in self.test_order)


@dataclass
class Selection:
    test_order: tuple[str, ...]
    full: bool = False
    test_dirs: set[str] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)

    def add(self, path: str, dirs: Iterable[str], why: str) -> None:
        selected = set(dirs)
        self._validate_groups(selected)
        self.test_dirs.update(selected)
        self.reasons.append(f"{path}: {why}")

    def _validate_groups(self, groups: set[str]) -> None:
        unknown = groups - set(self.test_order)
        if unknown:
            raise ValueError(f"unknown selected test groups: {sorted(unknown)}")

    def widen(self, path: str, why: str) -> None:
        self.full = True
        self.reasons.append(f"{path}: {why} -> full suite")

    def pytest_paths(self) -> list[str] | None:
        """Explicit paths, [] for the default suite, or None for no test inputs."""
        self._validate_groups(self.test_dirs)
        if self.full or set(self.test_order) <= self.test_dirs:
            return []
        return [d for d in self.test_order if d in self.test_dirs] or None


def package_tests(package: str) -> str:
    return f"packages/{package}/tests"


def dependents(package: str, dependencies: Mapping[str, tuple[str, ...]]) -> set[str]:
    found = {package}
    changed = True
    while changed:
        changed = False
        for name, deps in dependencies.items():
            if name not in found and found.intersection(deps):
                found.add(name)
                changed = True
    return found


def fan_out(package: str, policy: SelectionPolicy) -> set[str]:
    return {package_tests(p) for p in dependents(package, policy.dependencies)
            if p in policy.tested_packages} | {policy.root_tests}


def classify(path: str, selection: Selection, referenced_by: Callable[[str], set[str]],
             policy: SelectionPolicy,
             baseline_deltas: Mapping[str, BaselineDelta] | None = None) -> None:
    """Add ``path``'s test impact to ``selection``.

    A baseline file only shrinks without widening: each removed entry selects
    the tests of its test file. A grown baseline, or one whose delta is unknown,
    widens to the full suite.
    """
    parts = PurePosixPath(path).parts
    name = parts[-1]
    if path in policy.baseline_files:
        delta = (baseline_deltas or {}).get(path)
        if delta is None:
            selection.widen(path, "unmarked-test baseline change is unknown")
        elif delta.added:
            selection.widen(path, "unmarked-test baseline grew")
        else:
            selection.add(path, (), "unmarked-test baseline shrank")
            for removed in sorted(delta.removed_files):
                classify(removed, selection, referenced_by, policy, baseline_deltas)
        return
    if path in policy.full_files or path.startswith(policy.full_prefixes):
        selection.widen(path, policy.full_reason)
        return
    if parts[0] == "packages" and len(parts) >= 3:
        package, area = parts[1], parts[2]
        if package not in policy.dependencies:
            selection.widen(path, "unknown package")
        elif name in {"pyproject.toml", "conftest.py"} and area != "tests":
            selection.widen(path, "package build/test configuration")
        elif area == "tests_archived":
            selection.add(path, (), "archived tests, outside the default suite")
        elif area == "tests":
            if package in policy.tested_packages:
                selection.add(path, {package_tests(package)}, "package tests")
            else:
                selection.add(path, (), "tests outside the default testpaths")
        else:
            selection.add(path, fan_out(package, policy), f"{package} and its dependents")
        return
    for prefix, package in policy.package_inputs.items():
        if path.startswith(prefix):
            selection.add(path, fan_out(package, policy), "research governance input")
            return
    if path.startswith(f"{policy.root_tests}/"):
        if name == "conftest.py":
            selection.widen(path, policy.shared_test_reason)
        else:
            selection.add(path, {policy.root_tests}, "root tests")
        return
    if path in policy.root_files or path.startswith(policy.root_prefixes):
        selection.add(path, {policy.root_tests} | referenced_by(name), "root app/tooling")
        return
    if path in policy.ci_files:
        selection.add(path, {policy.root_tests} | referenced_by(name), "CI gate definition")
        return
    for prefix, packages in policy.fixture_inputs.items():
        if path.startswith(prefix):
            selection.add(path, {package_tests(p) for p in packages}, "fixture release data")
            return
    if path.startswith(policy.critical_prefixes):
        selection.widen(path, "important input requires conservative verification")
        return
    if path.startswith(policy.referenced_prefixes) or path in policy.referenced_files:
        dirs = referenced_by(name)
        selection.add(path, dirs, "named by tests" if dirs else "not a test input")
        return
    selection.widen(path, "no selection rule")


def string_literals(source: str) -> str:
    """Every string constant in ``source`` except docstrings, newline-joined.

    A test names a file it reads in code; prose in comments and docstrings is
    not an input reference.
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return "\n".join(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    )


def validate_test_paths(root: Path, test_order: tuple[str, ...]) -> dict[str, list[Path]]:
    """Require each declared test path to exist and contain a test module."""
    root = root.resolve()
    groups = {}
    for group in test_order:
        path = (root / group).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"test group escapes repository: {group}")
        if not path.exists():
            raise ValueError(f"required test group is missing: {group}")
        files = [path] if path.is_file() and path.suffix == '.py' else sorted(path.rglob('*.py'))
        if not any(f.name.startswith('test_') for f in files):
            raise ValueError(f"required test group contains no test modules: {group}")
        groups[group] = files
    return groups


def test_sources(root: Path, test_order: tuple[str, ...], *,
                 excluded_names: frozenset[str] = frozenset({"test_prepush_select.py"})) -> dict[str, str]:
    """Read validated test inputs, excluding selector examples and docstrings."""
    return {
        group: "\n".join(string_literals(path.read_text(encoding="utf-8"))
                         for path in files if path.name not in excluded_names)
        for group, files in validate_test_paths(root, test_order).items()
    }


def reference_lookup(sources: dict[str, str]) -> Callable[[str], set[str]]:
    return lambda basename: {d for d, text in sources.items() if basename in text}


def describe(selection: Selection) -> str:
    paths = selection.pytest_paths()
    head = (
        "skip: no test inputs changed" if paths is None
        else "full default suite" if paths == []
        else "targeted: " + " ".join(paths)
    )
    return "\n".join([f"pre-push selection: {head}", *(f"  {r}" for r in selection.reasons)])

"""What a project passes to the gate and the verifier (docs/TESTKIT.md § What a project supplies)."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import config, stages, toolchain as toolchains
from .evidence import Stage
from .git import BaselineDelta
from .selection import Selection
from .toolchain import Toolchain

Select = Callable[[Sequence[str], Mapping[str, BaselineDelta] | None], Selection]


def always(_paths: Sequence[str] | None) -> bool:
    return True


@dataclass(frozen=True)
class StaticCheck:
    """A project's static check: its token, argv, path condition and position.

    ``applies`` receives the changed paths, ``None`` for an unknown push range. ``first`` runs it before the
    verifier's shared checks; ``cwd`` is relative to the repository root.
    """
    name: str
    token: str
    argv: tuple[str, ...]
    applies: Callable[[Sequence[str] | None], bool] = always
    first: bool = False
    cwd: str = "."


@dataclass(frozen=True)
class Project:
    """One repository's data; a project with no ``select`` is the workspace root of the verifier's root form.

    ``toolchain`` defaults to the one the tree's ``[tool.atc-testkit] toolchain`` names; ``runner`` is the argv that
    starts a stage, the toolchain's entry by default.

    The root form's ``test_paths`` maps the range's paths to the pytest paths its stages run, relative to
    ``tests_dir``: ``[]`` for the default suite, ``None`` for no test.
    """
    root: Path
    argv_file: Path
    select: Select | None = None
    baseline_files: frozenset[str] = frozenset()
    missing_remote: str = "full"
    checks: tuple[StaticCheck, ...] = ()
    documentation_check: StaticCheck | None = None
    runner: tuple[str, ...] | None = None
    toolchain: Toolchain | None = None
    source_roots: tuple[str, ...] = ("packages/*/src", "src")
    product_prefixes: tuple[str, ...] | None = None
    credentials: tuple[str, ...] | None = None
    data_roots: tuple[str, ...] | None = None
    automation_prefixes: tuple[str, ...] | None = None
    lint_scope: tuple[str, ...] | None = None
    ignore: tuple[str, ...] | None = None
    test_paths: Callable[[Sequence[str]], list[str] | None] | None = None
    tests_dir: str = "."
    documentation_only: Callable[[Sequence[str]], bool] | None = None
    table: dict = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "table", config.tool_table(self.root))

    def _data(self, value: tuple[str, ...] | None, key: str) -> tuple[str, ...]:
        return tuple(self.table.get(key, ())) if value is None else value

    @property
    def product_module_prefixes(self) -> tuple[str, ...]:
        return self._data(self.product_prefixes, "product_module_prefixes")

    @property
    def credential_variables(self) -> tuple[str, ...]:
        return self._data(self.credentials, "credential_variables")

    @property
    def data_root_variables(self) -> tuple[str, ...]:
        return self._data(self.data_roots, "data_root_variables")

    @property
    def automation_markdown_prefixes(self) -> tuple[str, ...]:
        return self._data(self.automation_prefixes, "automation_markdown_prefixes")

    @property
    def lint_paths(self) -> tuple[str, ...]:
        return self._data(self.lint_scope, "lint_scope")

    def tests_toolchain(self) -> Toolchain:
        """The toolchain this project's tests run with; ``TOOLCHAIN_UNKNOWN`` for a name no toolchain has."""
        return toolchains.for_tree(self.root) if self.toolchain is None else self.toolchain

    def stage_entry(self) -> tuple[str, ...]:
        return tuple(self.tests_toolchain().entry_argv) if self.runner is None else tuple(self.runner)

    def stage_list(self) -> tuple[Stage, ...]:
        """The stage definition the gate and the verifier share."""
        return stages.stages(config.ignored_paths(self.root) if self.ignore is None else self.ignore)

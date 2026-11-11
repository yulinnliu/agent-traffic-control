"""The toolchain a tree's tests run with, and the registry of toolchains (docs/TESTKIT.md § Toolchain).

A neutral module imports a toolchain only through ``resolve``, so pytest and uv code loads when a run needs it.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import importlib
from pathlib import Path
from typing import Protocol

from . import config
from .evidence import Stage
from .locations import Refusal

DEFAULT = "python"
KEY = "toolchain"
MODULES = {"python": "atc_testkit.python_toolchain"}
UNKNOWN = "TOOLCHAIN_UNKNOWN"
UNKNOWN_EXIT = 9


class Toolchain(Protocol):
    """What the gate, the verifier and the ``atc test`` and ``atc tmp`` commands ask of a toolchain."""
    name: str
    entry_argv: tuple[str, ...]
    no_tests_exit: int
    dependency_files: tuple[str, ...]
    environment_directories: tuple[str, ...]
    lock_check_argv: tuple[str, ...]
    system_temp_runs: tuple[str, ...]

    def search_path(self, root: Path) -> list[Path]: ...

    def test_paths(self, root: Path) -> list[str]: ...

    def stage_log(self, stage: str) -> str: ...

    def stage_command(self, entry: Sequence[str], stage: Stage, workers: int | None, options: Sequence[str],
                      paths: Sequence[str]) -> list[str]: ...

    def run_options(self, basetemp: Path, cache_dir: Path) -> list[str]: ...

    def collect_command(self, options: Sequence[str]) -> list[str]: ...

    def pointed_variables(self, work: Path) -> dict[str, Path]: ...

    def prepare(self, directory: Path, environ: Mapping[str, str], run: Callable | None = None) -> Path: ...

    def interpreter(self, tree: Path) -> str: ...


def resolve(name: str = DEFAULT) -> Toolchain:
    """The toolchain registered under ``name``; ``TOOLCHAIN_UNKNOWN`` (exit 9) for any other name."""
    module = MODULES.get(name) if isinstance(name, str) else None
    if module is None:
        raise Refusal(UNKNOWN, f"{name!r} is no toolchain; the known ones are {', '.join(sorted(MODULES))}",
                      UNKNOWN_EXIT)
    return importlib.import_module(module).TOOLCHAIN


def for_tree(root: Path) -> Toolchain:
    """The toolchain ``[tool.atc-testkit] toolchain`` names in the tree at ``root``, ``python`` when it names none."""
    return resolve(config.tool_table(root).get(KEY, DEFAULT))


def environment_directories() -> frozenset[str]:
    """The environment directory names of every registered toolchain; a scratch may hold a link by any of them."""
    return frozenset(name for key in MODULES for name in resolve(key).environment_directories)


def system_temp_runs() -> tuple[str, ...]:
    """The patterns of ``<tmp>/systmp`` entries that a registered toolchain's runner expires itself."""
    return tuple(sorted({pattern for key in MODULES for pattern in resolve(key).system_temp_runs}))

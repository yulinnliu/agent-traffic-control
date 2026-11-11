"""The Python toolchain: pytest, uv and the ``.venv`` (docs/TESTKIT.md § Toolchain).

``pytest_arguments`` is the one place that builds pytest's options for a stage, ``atc test run``, the verifier's
collection and ``atc test mutants``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import config, scratch
from .evidence import Stage
from .locations import Refusal

NO_TESTS_EXIT = 5  # pytest's exit code when it collects no test
LOCK_FILE = "uv.lock"
VENV = ".venv"
PYCACHE = "pyc"  # the subdirectory of a run's working directory that every child compiles into
SYSTEM_TEMP_RUNS = ("pytest-of-*",)  # pytest's own base directories, which pytest itself expires
SYNC_TAIL = 20
SYNC_SHADOWING = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
SyncRun = Callable[[Sequence[str], Path, Mapping[str, str]], subprocess.CompletedProcess]


def combine(expression: str | None, extra: str) -> str:
    """``extra`` added to a marker expression with ``and``; ``extra`` alone when there is none."""
    return f"({expression}) and {extra}" if expression else extra


def split_marker(arguments: Sequence[str]) -> tuple[str | None, list[str]]:
    """The last ``-m`` expression of a caller's pytest arguments and the arguments without it."""
    rest, expression, index = [], None, 0
    arguments = list(arguments)
    while index < len(arguments):
        argument = arguments[index]
        if argument == "-m" and index + 1 < len(arguments):
            expression = arguments[index + 1]
            index += 2
            continue
        if argument.startswith("-m") and not argument.startswith("--") and len(argument) > 2:
            expression = argument[2:].removeprefix("=")
        else:
            rest.append(argument)
        index += 1
    return expression, rest


def pytest_arguments(*, quiet: bool = True, select: str | None = None, workers: int | None = None,
                     xdist: bool = True, ignore: Sequence[str] = (), no_cache: bool = False,
                     basetemp: Path | str | None = None, cache_dir: Path | str | None = None,
                     collect_only: bool = False, junit: Path | str | None = None) -> list[str]:
    """pytest's options, always in this order.

    ``workers`` is the parallel stage's count, spread with ``--dist loadgroup``; ``None`` or 0 runs without
    workers (``-n 0``). ``xdist`` false passes no worker option at all, for a Python without pytest-xdist.
    """
    arguments = ["-q"] if quiet else []
    if select:
        arguments += ["-m", select]
    if xdist:
        arguments += ["-n", str(workers), "--dist", "loadgroup"] if workers else ["-n", "0"]
    arguments += [f"--ignore={path}" for path in ignore]
    if no_cache:
        arguments += ["-p", "no:cacheprovider"]
    if basetemp is not None:
        arguments += ["--basetemp", str(basetemp)]
    if cache_dir is not None:
        arguments += ["-o", f"cache_dir={cache_dir}"]
    if collect_only:
        arguments.append("--collect-only")
    if junit is not None:
        arguments.append(f"--junitxml={junit}")
    return arguments


def ini_options(root: Path) -> dict:
    """``[tool.pytest.ini_options]`` of the tree at ``root``."""
    options = config.read_pyproject(root).get("tool", {}).get("pytest", {}).get("ini_options", {})
    return options if isinstance(options, dict) else {}


def _run_sync(argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), cwd=cwd, env=dict(env), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, creationflags=scratch.WINDOW_HIDDEN)


@dataclass(frozen=True)
class PythonToolchain:
    name: str = "python"
    entry_argv: tuple[str, ...] = (sys.executable, "-m", "atc_testkit.ci")
    no_tests_exit: int = NO_TESTS_EXIT
    dependency_files: tuple[str, ...] = (LOCK_FILE, ".python-version")
    environment_directories: tuple[str, ...] = (VENV,)
    lock_check_argv: tuple[str, ...] = ("uv", "lock", "--check")
    system_temp_runs: tuple[str, ...] = SYSTEM_TEMP_RUNS

    def search_path(self, root: Path) -> list[Path]:
        """The directories pytest puts on ``sys.path``: ``pythonpath`` of the tree's ini options."""
        return [Path(root) / entry for entry in ini_options(root).get("pythonpath", [])]

    def test_paths(self, root: Path) -> list[str]:
        """Where the tree's tests live: ``testpaths`` of its ini options."""
        return list(ini_options(root).get("testpaths", []))

    def stage_log(self, stage: str) -> str:
        return f"pytest-{stage}.log"

    def stage_command(self, entry: Sequence[str], stage: Stage, workers: int | None, options: Sequence[str],
                      paths: Sequence[str]) -> list[str]:
        return [*entry, *pytest_arguments(select=stage.marker, workers=workers, ignore=stage.ignore), *options,
                *paths]

    def run_options(self, basetemp: Path, cache_dir: Path) -> list[str]:
        return pytest_arguments(quiet=False, xdist=False, basetemp=basetemp, cache_dir=cache_dir)

    def collect_command(self, options: Sequence[str]) -> list[str]:
        return [sys.executable, "-m", "pytest", *pytest_arguments(collect_only=True), *options]

    def pointed_variables(self, work: Path) -> dict[str, Path]:
        return {"PYTHONPYCACHEPREFIX": Path(work) / PYCACHE}

    def prepare(self, directory: Path, environ: Mapping[str, str], run: SyncRun | None = None) -> Path:
        """``uv sync --locked`` in ``directory``; the environment's directory, or a refusal naming the preparation."""
        if not (directory / LOCK_FILE).is_file():
            raise Refusal("LOCK_MISSING", f"{directory} has no {LOCK_FILE}; prepare the tree's environment as the "
                                          "project's AGENTS.md says, or name the directory that holds it with "
                                          "--lock-dir")
        uv = shutil.which("uv", path=environ.get("PATH"))
        if uv is None:
            raise Refusal("UV_NOT_FOUND", "uv is not on PATH")
        env = {key: value for key, value in environ.items() if key not in SYNC_SHADOWING}
        done = (run or _run_sync)([uv, "sync", "--locked"], directory, env)
        if done.returncode != 0:
            tail = (done.stdout + done.stderr).strip().splitlines()[-SYNC_TAIL:]
            raise Refusal("SYNC_FAILED", "\n".join([f"uv sync --locked in {directory} exited {done.returncode}:",
                                                    *tail]), 1)
        return directory / VENV

    def interpreter(self, tree: Path) -> str:
        """The tree's ``.venv`` interpreter; ``VENV_MISSING`` names how to make one."""
        python = tree / VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            raise Refusal("VENV_MISSING", f"{tree} has no .venv; make the scratch with `atc tmp checkout`, prepare "
                                          "the environment as the project's AGENTS.md says, or name one with --python")
        return str(python)


TOOLCHAIN = PythonToolchain()

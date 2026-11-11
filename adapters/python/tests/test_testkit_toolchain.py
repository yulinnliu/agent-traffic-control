"""docs/TESTKIT.md § Toolchain: the registry, the neutral modules and the one pytest command builder."""
from __future__ import annotations

from dataclasses import dataclass
import subprocess
import sys
from pathlib import Path

import pytest

from atc_testkit import isolation, python_toolchain, stages, toolchain
from atc_testkit.locations import Refusal

NEUTRAL = ("stages", "scratch", "gate", "verifier", "evidence", "isolation", "project", "toolchain")


@pytest.mark.unit
def test_neutral_modules_load_no_toolchain_until_a_run_asks_for_one() -> None:
    program = ("import sys, importlib\n"
               f"for name in {NEUTRAL!r}: importlib.import_module('atc_testkit.' + name)\n"
               "print('atc_testkit.python_toolchain' in sys.modules)")
    done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "False"


@pytest.mark.unit
def test_the_registry_resolves_python_and_refuses_any_other_name(tmp_path: Path) -> None:
    assert toolchain.resolve() is python_toolchain.TOOLCHAIN
    assert toolchain.for_tree(tmp_path) is python_toolchain.TOOLCHAIN
    (tmp_path / "pyproject.toml").write_text('[tool.atc-testkit]\ntoolchain = "node"\n', encoding="utf-8")
    with pytest.raises(Refusal) as refused:
        toolchain.for_tree(tmp_path)
    assert (refused.value.token, refused.value.code) == ("TOOLCHAIN_UNKNOWN", 9)
    assert toolchain.environment_directories() == frozenset({".venv"})


@dataclass(frozen=True)
class Fake:
    """A second toolchain, as a later Node one would plug in."""
    name: str = "fake"
    entry_argv: tuple[str, ...] = ("fake-runner",)
    no_tests_exit: int = 7
    dependency_files: tuple[str, ...] = ("fake.lock",)
    environment_directories: tuple[str, ...] = ("env",)
    lock_check_argv: tuple[str, ...] = ("fake", "lock")

    def stage_command(self, entry, stage, workers, options, paths):
        return [*entry, "--select", stage.marker, f"--workers={workers}", *options, *paths]

    def run_options(self, basetemp, cache_dir):
        return [f"--base={basetemp}"]

    def pointed_variables(self, work):
        return {"FAKE_CACHE": Path(work) / "fake"}


@pytest.mark.unit
def test_the_stages_and_the_isolation_take_their_commands_from_the_toolchain(tmp_path: Path) -> None:
    calls = []

    def run(command, **_options):
        calls.append(command)
        return 7
    isolated = isolation.IsolatedRun(tmp_path, environ={}, toolchain=Fake())
    outcome = stages.run_stages(["fake-runner"], ["t"], cwd=tmp_path, stage_list=stages.stages(),
                                env_for=lambda stage: isolated.environment(stage.name),
                                options_for=lambda stage: isolated.run_options(stage.name),
                                log_for=lambda stage: tmp_path / f"{stage.name}.log", workers=3, run=run,
                                toolchain=Fake())
    assert calls[0] == ["fake-runner", "--select", stages.PARALLEL.marker, "--workers=3",
                        f"--base={tmp_path / 'parallel' / 'bt'}", "t"]
    assert calls[1][3] == "--workers=None"
    assert (outcome.token, outcome.code) == ("NO_TESTS_COLLECTED", 7)
    env = isolated.environment("parallel")
    assert env["FAKE_CACHE"] == str(tmp_path / "fake") and "PYTHONPYCACHEPREFIX" not in env


@pytest.mark.unit
@pytest.mark.parametrize(("options", "expected"), [
    ({"select": "serial", "ignore": ("a",)}, ["-q", "-m", "serial", "-n", "0", "--ignore=a"]),
    ({"select": "s", "workers": 4}, ["-q", "-m", "s", "-n", "4", "--dist", "loadgroup"]),
    ({"select": "s", "xdist": False, "no_cache": True, "basetemp": "b"},
     ["-q", "-m", "s", "-p", "no:cacheprovider", "--basetemp", "b"]),
    ({"collect_only": True}, ["-q", "-n", "0", "--collect-only"]),
    ({"quiet": False, "xdist": False, "basetemp": "b", "cache_dir": "c"}, ["--basetemp", "b", "-o", "cache_dir=c"]),
    ({"select": "s", "junit": "j.xml"}, ["-q", "-m", "s", "-n", "0", "--junitxml=j.xml"]),
])
def test_the_builder_puts_every_option_in_one_order(options: dict, expected: list[str]) -> None:
    assert python_toolchain.pytest_arguments(**options) == expected


@pytest.mark.unit
def test_the_collection_and_the_stages_share_the_builder(tmp_path: Path) -> None:
    python = python_toolchain.TOOLCHAIN
    options = python.run_options(tmp_path / "bt", tmp_path / "cache")
    assert python.collect_command(options) == [sys.executable, "-m", "pytest", "-q", "-n", "0", "--collect-only",
                                               "--basetemp", str(tmp_path / "bt"), "-o",
                                               f"cache_dir={tmp_path / 'cache'}"]
    assert python.stage_command(["r"], stages.SERIAL, None, options, ["t"]) == [
        "r", "-q", "-m", stages.SERIAL.marker, "-n", "0", *options, "t"]


@pytest.mark.unit
def test_the_consumer_check_knows_the_registered_toolchains() -> None:
    from atc_testkit import check
    assert check.TOOLCHAINS == tuple(sorted(toolchain.MODULES))


@pytest.mark.unit
def test_a_category_expression_selects_what_pytest_m_selects() -> None:
    from itertools import combinations

    from _pytest.mark.expression import Expression
    names = ("slow", "serial", "interactive_desktop", "unit")
    expressions = [stage.marker for stage in (*stages.STAGE_ORDER, stages.SLOW)]
    expressions += ["not (slow or serial) and unit", "unit or slow and serial", "not not unit"]
    for size in range(len(names) + 1):
        for held in combinations(names, size):
            for expression in expressions:
                expected = Expression.compile(expression).evaluate(lambda name, **_: name in held)
                assert stages.selects(expression, held) is expected, (expression, held)
    for broken in ("", "slow and", "(slow", "slow)", "and slow", "slow - serial"):
        with pytest.raises(ValueError):
            stages.selects(broken, ())


@pytest.mark.unit
def test_the_python_toolchain_reads_pytests_own_settings(tmp_path: Path) -> None:
    python = python_toolchain.TOOLCHAIN
    assert python.search_path(tmp_path) == [] and python.test_paths(tmp_path) == []
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["src", "packages/a/src"]\n'
                                             'testpaths = ["tests"]\n', encoding="utf-8")
    assert python.search_path(tmp_path) == [tmp_path / "src", tmp_path / "packages/a/src"]
    assert python.test_paths(tmp_path) == ["tests"]
    assert python.stage_log("serial") == "pytest-serial.log"
    assert toolchain.system_temp_runs() == ("pytest-of-*",)

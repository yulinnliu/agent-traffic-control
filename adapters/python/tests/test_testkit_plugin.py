"""docs/TESTKIT.md § Adoption, § Markers and the plugin's walk from a direct pytest to its temporary directory."""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYPROJECT = """[project]
name = "fixture-project"
version = "1"
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--strict-markers"
[tool.atc-testkit]
unmarked_baseline = "baseline.txt"
"""
CONFTEST = 'pytest_plugins = ("atc_testkit.plugin",)\n'
REPORTING_TEST = """import os, tempfile
import pytest

@pytest.mark.unit
def test_reports(tmp_path):
    print("TEMPDIR", tempfile.gettempdir())
    print("TMP_PATH", tmp_path)
    print("ATC_TMP", os.environ.get("ATC_TMP"))
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (root / "baseline.txt").write_text("# none\n", encoding="utf-8")
    (root / "tests/test_a.py").write_text(REPORTING_TEST, encoding="utf-8")
    return root


def pytest_in(root: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    environment.update(env, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-s", "-n", "0", *args], cwd=root,
                          env=environment, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)


def reported(output: str, name: str) -> str:
    """The value a test printed; pytest's progress prefix can share the line under ``-s``."""
    return next(line.split(name + " ", 1)[1] for line in output.splitlines() if name + " " in line)


def test_a_direct_pytest_ends_up_below_systmp(project: Path, atc_tmp: Path) -> None:
    run = pytest_in(project)
    assert run.returncode == 0, run.stdout + run.stderr
    systmp = atc_tmp / "systmp"
    assert reported(run.stdout, "TEMPDIR") == str(systmp)
    assert reported(run.stdout, "ATC_TMP") == str(atc_tmp)
    assert Path(reported(run.stdout, "TMP_PATH")).is_relative_to(systmp / f"pytest-of-{getpass.getuser()}" / "pytest-0")


def test_a_temporary_directory_below_the_root_stays(project: Path, atc_tmp: Path) -> None:
    scratch = atc_tmp / "scratch"
    scratch.mkdir()
    run = pytest_in(project, TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch))
    assert reported(run.stdout, "TEMPDIR") == str(scratch)


def test_a_basetemp_changes_nothing(project: Path, atc_tmp: Path, tmp_path: Path) -> None:
    run = pytest_in(project, "--basetemp", str(tmp_path / "given"))
    assert run.returncode == 0, run.stdout + run.stderr
    assert reported(run.stdout, "TEMPDIR") != str(atc_tmp / "systmp")
    assert not (atc_tmp / "systmp").exists()


def test_a_debug_temproot_changes_nothing(project: Path, atc_tmp: Path, tmp_path: Path) -> None:
    run = pytest_in(project, PYTEST_DEBUG_TEMPROOT=str(tmp_path))
    assert run.returncode == 0, run.stdout + run.stderr
    assert reported(run.stdout, "TEMPDIR") != str(atc_tmp / "systmp")
    assert Path(reported(run.stdout, "TMP_PATH")).is_relative_to(tmp_path)


def test_ci_keeps_the_platform_location(project: Path, atc_tmp: Path) -> None:
    run = pytest_in(project, CI="1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert reported(run.stdout, "TEMPDIR") != str(atc_tmp / "systmp")


def test_an_unsuitable_root_stops_the_run_with_its_token(project: Path, atc_tmp: Path) -> None:
    run = pytest_in(project, ATC_TMP="relative")
    assert run.returncode == 9, run.stdout + run.stderr
    assert "ATC_TMP_UNSUITABLE" in run.stdout + run.stderr


def test_xdist_workers_inherit_the_location(project: Path, atc_tmp: Path) -> None:
    run = pytest_in(project, "-n", "2")
    assert run.returncode == 0, run.stdout + run.stderr
    bases = list((atc_tmp / "systmp").glob("pytest-of-*/pytest-0/popen-gw*"))
    assert bases


def test_the_plugin_loads_no_other_plugin_into_a_project(project: Path, atc_tmp: Path) -> None:
    assert "pytester" not in pytest_in(project, "--fixtures").stdout


def test_every_marker_is_registered_with_one_description(project: Path, atc_tmp: Path) -> None:
    run = pytest_in(project, "--markers")
    for name in ("unit", "integration", "render", "statistical", "slow", "serial", "interactive_desktop"):
        assert run.stdout.count(f"@pytest.mark.{name}:") == 1


@pytest.mark.parametrize(("body", "expected"), [
    ("def test_x(): pass\n", "unmarked test is not in the legacy baseline"),
    ("import pytest\n@pytest.mark.slow\ndef test_x(): pass\n", "slow requires one semantic marker"),
    ("import pytest\n@pytest.mark.unit\n@pytest.mark.render\ndef test_x(): pass\n", "multiple semantic markers"),
])
def test_the_marker_contract_is_enforced(project: Path, atc_tmp: Path, body: str, expected: str) -> None:
    (project / "tests/test_b.py").write_text(body, encoding="utf-8")
    run = pytest_in(project)
    assert run.returncode != 0 and expected in run.stdout + run.stderr


def test_the_named_baseline_allows_a_listed_test(project: Path, atc_tmp: Path) -> None:
    (project / "tests/test_b.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (project / "baseline.txt").write_text("tests/test_b.py::test_x\n", encoding="utf-8")
    run = pytest_in(project)
    assert run.returncode == 0, run.stdout + run.stderr


CHILD_TEST = """import subprocess, sys, time
import pytest
from atc_testkit import children

SLEEPER = "import sys,time; time.sleep(120)"

@pytest.mark.unit
def test_starts(tmp_path):
    children.start([sys.executable, "-c", SLEEPER, str(tmp_path)])
"""
ESCAPED_TEST = """import pathlib, subprocess, sys
import pytest

@pytest.mark.unit
def test_escapes(tmp_path):
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", str(tmp_path)], creationflags=flags)
    (pathlib.Path(__file__).resolve().parents[2] / "pid.txt").write_text(str(child.pid))
"""


def test_a_child_started_through_the_testkit_ends_with_its_test(project: Path, atc_tmp: Path) -> None:
    (project / "tests/test_a.py").write_text(CHILD_TEST, encoding="utf-8")
    run = pytest_in(project)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "CHILD_PROCESSES_ALIVE" not in run.stdout


@pytest.mark.parametrize("workers", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_a_surviving_child_fails_the_run_with_its_token(project: Path, tmp_path: Path, atc_tmp: Path,
                                                        workers: tuple[str, ...]) -> None:
    (project / "tests/test_a.py").write_text(ESCAPED_TEST, encoding="utf-8")
    base = tmp_path / "base"
    run = pytest_in(project, "--basetemp", str(base), "-o", "atc_testkit_reap_seconds=1", *workers)
    pid = int((tmp_path / "pid.txt").read_text())
    try:
        assert run.returncode == 1, run.stdout + run.stderr
        assert "CHILD_PROCESSES_ALIVE" in run.stdout and str(pid) in run.stdout
    finally:
        from atc_testkit import desktop
        desktop.kill_tree(pid)


def test_sessions_with_other_base_directories_do_not_fail_one_another(project: Path, tmp_path: Path, atc_tmp: Path) -> None:
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", str(tmp_path / "other-base")],
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        run = pytest_in(project, "--basetemp", str(tmp_path / "base"))
        assert run.returncode == 0, run.stdout + run.stderr
    finally:
        other.kill()
        other.wait()


GROUPED_TEST = """import os, pathlib
import pytest

pytestmark = pytest.mark.unit
BUILT = pathlib.Path(__file__).parent / "built.txt"


@pytest.fixture(scope="module")
def shared():
    with BUILT.open("a", encoding="utf-8") as sink:
        sink.write(f"{os.getpid()}\\n")
    return 1


@pytest.mark.parametrize("n", range(8))
def test_shared(shared, n):
    assert shared == 1


@pytest.mark.parametrize("n", range(8))
def test_alone(n):
    pass


@pytest.mark.xdist_group("mine")
def test_marked():
    pass


class TestKind:
    @pytest.fixture(scope="class")
    def kind(self):
        return 2

    def test_kind(self, kind):
        assert kind == 2
"""


@pytest.mark.integration
def test_tests_sharing_a_wider_fixture_run_on_one_worker(project: Path, atc_tmp: Path) -> None:
    (project / "tests/test_g.py").write_text(GROUPED_TEST, encoding="utf-8")
    run = pytest_in(project, "-n", "2", "--dist", "loadgroup", "-v", "tests/test_g.py")
    assert run.returncode == 0, run.stdout + run.stderr
    assert len((project / "tests/built.txt").read_text(encoding="utf-8").splitlines()) == 1
    assert "tests/test_g.py::test_shared[0]@tests/test_g.py" in run.stdout
    assert "tests/test_g.py::test_marked@mine" in run.stdout
    assert "tests/test_g.py::TestKind::test_kind@tests/test_g.py::TestKind" in run.stdout
    assert "tests/test_g.py::test_alone[0] " in run.stdout or "tests/test_g.py::test_alone[0]\n" in run.stdout


@pytest.mark.integration
@pytest.mark.parametrize("value", ["0", "-1", "11", "soon"])
def test_a_bad_reap_window_is_a_usage_error(project: Path, atc_tmp: Path, value: str) -> None:
    run = pytest_in(project, "-o", f"atc_testkit_reap_seconds={value}")
    assert run.returncode == 4, run.stdout + run.stderr
    assert "REAP_SECONDS_INVALID" in run.stdout + run.stderr


GUARDED_TEST = '''import tkinter
import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def shared_root():
    root = tkinter.Tk()
    yield root
    root.destroy()


def test_unmarked():
    tkinter.Tk().destroy()


def test_swallowed():
    try:
        tkinter.Tk().destroy()
    except Exception:
        pass


def test_tcl_only():
    tkinter.Tcl()


@pytest.mark.interactive_desktop
def test_marked(shared_root):
    assert shared_root.winfo_exists()
'''


def guarded_run(project: Path, *args: str, **env: str) -> tuple[int, str]:
    """A child pytest on a private desktop, so a window the guard lets through shows nowhere."""
    from atc_testkit import desktop
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    environment.update(env, PYTHONIOENCODING="utf-8")
    process, _kind = desktop.start([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-n", "0", "-rA", *args],
                                   cwd=project, env=environment, private=True)
    output = process.stdout.read().decode("utf-8", errors="replace")
    return process.wait(), output


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="the interactive desktop is a Windows notion")
def test_the_desktop_guard_refuses_unmarked_tk_roots(project: Path, atc_tmp: Path) -> None:
    pytest.importorskip("tkinter")
    (project / "tests/test_tk.py").write_text(GUARDED_TEST, encoding="utf-8")
    code, output = guarded_run(project, "tests/test_tk.py", ATC_DESKTOP_GUARD="1")
    assert code == 1, output
    assert "FAILED tests/test_tk.py::test_unmarked" in output and "ERROR tests/test_tk.py::test_swallowed" in output
    assert "PASSED tests/test_tk.py::test_tcl_only" in output and "PASSED tests/test_tk.py::test_marked" in output
    assert "INTERACTIVE_DESKTOP_WINDOW tests/test_tk.py::test_swallowed" in output
    code, output = guarded_run(project, "tests/test_tk.py::test_unmarked", ATC_DESKTOP_GUARD="0")
    assert code == 0, output


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="the interactive desktop is a Windows notion")
def test_a_tk_root_at_collection_is_refused(project: Path, atc_tmp: Path) -> None:
    pytest.importorskip("tkinter")
    (project / "tests/test_early.py").write_text("import tkinter\nROOT = tkinter.Tk()\n", encoding="utf-8")
    code, output = guarded_run(project, "tests/test_early.py", ATC_DESKTOP_GUARD="1")
    assert code == 2 and "INTERACTIVE_DESKTOP_WINDOW collection or session start" in output, output


@pytest.mark.integration
def test_a_bad_desktop_guard_value_is_a_usage_error(project: Path, atc_tmp: Path) -> None:
    run = pytest_in(project, ATC_DESKTOP_GUARD="yes")
    assert run.returncode == 4 and "DESKTOP_GUARD_INVALID" in run.stdout + run.stderr

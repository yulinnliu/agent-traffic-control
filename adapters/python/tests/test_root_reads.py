"""`tests/root_reads.py`: a test file that reads a guarded path undeclared fails its session."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
CONFTEST = f"""import sys
from pathlib import Path
sys.path[:0] = [{str(TESTS)!r}, {str(SCRIPTS)!r}]
import root_reads

def pytest_configure(config):
    guard = root_reads.RootReads(Path(__file__).parent, ("shared/governance", "shared/skills"),
                                 {{"tests/test_declared.py": ("shared/skills",)}})
    config.pluginmanager.register(guard, "root_reads")
"""
TEST_FILES = {
    "test_open.py": "from pathlib import Path\n\ndef test_reads():\n"
                    "    Path(__file__).parents[1].joinpath('shared/governance/core.md').read_text()\n",
    "test_shell.py": "import subprocess, sys\nfrom pathlib import Path\n\ndef test_runs():\n"
                     "    stand_in = Path(__file__).parents[1] / 'scripts' / 'stand-in.sh'\n"
                     "    subprocess.run([sys.executable, '-c', 'pass', str(stand_in)], check=True)\n",
    "test_relative_shell.py": "import subprocess, sys\nfrom pathlib import Path\n\ndef test_runs():\n"
                              "    subprocess.run([sys.executable, '-c', 'pass', 'scripts/stand-in.sh'],\n"
                              "                   cwd=Path(__file__).parents[1], check=True)\n",
    "test_declared.py":"from pathlib import Path\n\ndef test_reads():\n"
                        "    Path(__file__).parents[1].joinpath('shared/skills/demo/skill.md').read_text()\n",
}


@pytest.fixture()
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    for rel, text in {"pytest.ini": "[pytest]\n", "conftest.py": CONFTEST, "shared/governance/core.md": "# core\n",
                      "shared/skills/demo/skill.md": "# demo\n", "scripts/stand-in.sh": "exit 0\n",
                      **{f"tests/{name}": body for name, body in TEST_FILES.items()}}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def run(root: Path, test: str, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args, f"tests/{test}"], cwd=root,
                          env=environment, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)


@pytest.mark.parametrize("workers", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_an_undeclared_open_fails_the_session(checkout: Path, workers: tuple[str, ...]) -> None:
    result = run(checkout, "test_open.py", *workers)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ROOT_READ_UNDECLARED: tests/test_open.py reads shared/governance/core.md" in result.stdout


@pytest.mark.parametrize("test", ["test_shell.py", "test_relative_shell.py"])
def test_an_undeclared_shell_script_child_reads_every_guarded_path(checkout: Path, test: str) -> None:
    result = run(checkout, test)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"ROOT_READ_UNDECLARED: tests/{test} reads shared/governance, shared/skills" in result.stdout


def test_a_declared_reader_passes(checkout: Path) -> None:
    result = run(checkout, "test_declared.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ROOT_READ_UNDECLARED" not in result.stdout

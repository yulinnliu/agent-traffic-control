"""The `atc` wheel installed the way `uv tool install` installs it, not editable: every module and schema a command
reads ships in it (docs/WORKFLOW.md § Installing a release). Built and installed offline from uv's cache."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ADAPTER = Path(__file__).resolve().parents[1]
TESTKIT = ADAPTER / "atc-testkit"
# The variables `uv run` sets for this suite's own environment, which would point the child uv at it.
SHADOWING = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PYTHONHOME")
CACHE_MISS = ("uv found no package or build backend in its cache for an offline build or install; run "
              "`uv sync --locked` in adapters/python online once, then rerun:\n")


def uv(*args: str | Path, cwd: Path = ADAPTER) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if key not in SHADOWING}
    return subprocess.run(["uv", *map(str, args)], cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600, check=False)


def offline(*args: str | Path) -> None:
    run = uv(*args, "--offline")
    assert run.returncode == 0, CACHE_MISS + run.stdout + run.stderr


@pytest.fixture(scope="module")
def installed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The `atc` launcher of a fresh environment holding the two wheels and nothing else."""
    base = tmp_path_factory.mktemp("wheel")
    dist = base / "dist"
    offline("build", "--wheel", "--out-dir", dist, ADAPTER)
    offline("build", "--wheel", "--out-dir", dist, TESTKIT)
    venv = base / "venv"
    offline("venv", "--python", "3.12", venv)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    offline("pip", "install", "--python", python, *sorted(dist.glob("*.whl")))
    return python.parent / ("atc.exe" if os.name == "nt" else "atc")


def run_atc(atc: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if key not in SHADOWING and key != "ATC_ROOT"}
    return subprocess.run([str(atc), *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120, check=False)


def test_the_wheel_runs_version_doctor_and_hook(installed: Path, tmp_path: Path) -> None:
    version = run_atc(installed, "--version", cwd=tmp_path)
    assert version.returncode == 0 and version.stdout.startswith("atc "), version.stderr
    root = tmp_path / "workspace"
    (root / "shared" / "governance").mkdir(parents=True)
    (root / "shared" / "governance" / "INDEX.md").write_text("# INDEX.md\n", encoding="utf-8")
    (root / "workspace.yaml").write_text(f"version: 1\nworkspace_root: {root.as_posix()}\nprojects: {{}}\n",
                                         encoding="utf-8")
    doctor = run_atc(installed, "doctor", "--root", str(root), cwd=tmp_path)
    output = doctor.stdout + doctor.stderr
    assert "Traceback" not in output and "MISSING_DEPENDENCY" not in output, output
    assert "atc install" in output and "records no installed commit" in output, output
    hook = run_atc(installed, "hook", "--help", cwd=tmp_path)
    assert hook.returncode == 0 and hook.stdout.startswith("usage: atc hook"), hook.stderr


def test_the_wheel_validates_policies_against_its_packaged_schemas(installed: Path, tmp_path: Path) -> None:
    """`atc guard` composes from the schemas the wheel carries: the workspace's own policy passes, a policy missing
    a required key is a schema violation."""
    root = tmp_path / "workspace"
    (root / "policies").mkdir(parents=True)
    (root / "workspace.yaml").write_text(f"version: 1\nworkspace_root: {root.as_posix()}\nprojects: {{}}\n",
                                         encoding="utf-8")
    policy = root / "policies" / "hard-deny.yaml"
    policy.write_text((ADAPTER.parents[1] / "policies" / "hard-deny.yaml").read_text(encoding="utf-8"),
                      encoding="utf-8")
    guard = run_atc(installed, "guard", "--root", str(root), cwd=tmp_path)
    output = guard.stdout + guard.stderr
    assert "Traceback" not in output and "cannot compose" not in output, output
    policy.write_text("version: 1\ncommands:\n  deny: []\n", encoding="utf-8")
    guard = run_atc(installed, "guard", "--root", str(root), cwd=tmp_path)
    assert "schema-violation" in guard.stdout + guard.stderr

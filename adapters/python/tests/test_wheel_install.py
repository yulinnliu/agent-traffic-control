"""The `atc` wheel installed the way `uv tool install` installs it, not editable: every module and schema a command
reads ships in it (docs/WORKFLOW.md § Installing a release). The wheels are built without build isolation by the
backends the dev group locks into this environment and installed without their dependencies, which this environment
already holds, so nothing reads uv's cache or the network."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ADAPTER = Path(__file__).resolve().parents[1]
TESTKIT = ADAPTER / "atc-testkit"
# The variables `uv run` sets for this suite's own environment, which would point the child uv at it.
SHADOWING = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PYTHONHOME")
CACHE_MISS = ("an offline build or install failed; `uv sync --locked` in adapters/python installs the build "
              "backends:\n")


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
    # setuptools builds inside its source tree; a copy keeps parallel workers' builds of the testkit apart.
    testkit = base / "atc-testkit"
    shutil.copytree(TESTKIT, testkit, ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__", "*.md"))
    for source in (ADAPTER, testkit):
        offline("build", "--wheel", "--no-build-isolation", "--python", sys.executable, "--out-dir", dist, source)
    venv = base / "venv"
    offline("venv", "--python", sys.executable, venv)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    offline("pip", "install", "--no-deps", "--python", python, *sorted(dist.glob("*.whl")))
    # The dependencies come from this suite's environment, after the wheels on sys.path. A .pth line only appends a
    # directory, so the editable atc_cli that environment's own .pth files name stays out.
    site = next(venv.glob("**/site-packages"))
    (site / "dependencies.pth").write_text(sysconfig.get_paths()["purelib"] + "\n", encoding="utf-8")
    return python.parent / ("atc.exe" if os.name == "nt" else "atc")


def run_atc(atc: Path, *args: str, cwd: Path, home: Path | None = None) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if key not in SHADOWING and key != "ATC_ROOT"}
    if home is not None:
        env.update(HOME=str(home), USERPROFILE=str(home))
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
    assert "installed atc" in output and "records no installed commit" in output, output
    hook = run_atc(installed, "hook", "--help", cwd=tmp_path)
    assert hook.returncode == 0 and hook.stdout.startswith("usage: atc hook"), hook.stderr


def test_the_wheel_validates_policies_against_its_packaged_schemas(installed: Path, tmp_path: Path) -> None:
    """`atc guard --install` composes the hook table from the schemas the wheel carries, into a home of its own: the
    workspace's own policy installs, a policy missing a required key is a schema violation."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "workspace"
    (root / "policies").mkdir(parents=True)
    (root / "workspace.yaml").write_text(f"version: 1\nworkspace_root: {root.as_posix()}\nprojects: {{}}\n",
                                         encoding="utf-8")
    policy = root / "policies" / "hard-deny.yaml"
    policy.write_text((ADAPTER.parents[1] / "policies" / "hard-deny.yaml").read_text(encoding="utf-8"),
                      encoding="utf-8")
    guard = run_atc(installed, "guard", "--root", str(root), "--install", cwd=tmp_path, home=home)
    assert guard.returncode == 0 and guard.stdout.startswith("INSTALLED guard"), guard.stdout + guard.stderr
    assert (home / ".atc" / "enforcement" / "pretooluse.json").is_file()
    policy.write_text("version: 1\ncommands:\n  deny: []\n", encoding="utf-8")
    guard = run_atc(installed, "guard", "--root", str(root), "--install", cwd=tmp_path, home=home)
    output = guard.stdout + guard.stderr
    assert "POLICY_INVALID" in output and "schema-violation" in output, output

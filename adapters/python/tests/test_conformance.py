"""Run every case under shared/conformance/ against this implementation.

A case directory holds `input/`, `args.json` and `expected/`. The runner
copies `input/` to a temporary directory, substitutes `{input}`, `{out}` and
`{root}` in the arguments, runs the tool as a subprocess from inside the
input copy, and compares exit code, required output substrings and any
expected files. See shared/conformance/README.md for the contract.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONFORMANCE = ROOT / "shared" / "conformance"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

TOOLS = {
    "validate-adapters": "validate_workspace.py",
    "load-policies": "policies.py",
    "generate-enforcement": "generate_enforcement.py",
    "classify-change": "classify_change.py",
}


def cases() -> list[pytest.param]:
    found = []
    for tool, script in TOOLS.items():
        tool_dir = CONFORMANCE / tool
        if not tool_dir.is_dir():
            continue
        for case in sorted(p for p in tool_dir.iterdir() if (p / "args.json").is_file()):
            found.append(pytest.param(script, case, id=f"{tool}/{case.name}"))
    return found


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n")


@pytest.mark.parametrize("script,case", cases())
def test_conformance_case(script: str, case: Path, tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    out_dir = tmp_path / "out"
    source = case / "input"
    if source.is_dir():
        shutil.copytree(source, input_dir)
    else:
        input_dir.mkdir()
    out_dir.mkdir()
    subs = {"input": input_dir.as_posix(), "out": out_dir.as_posix(), "root": ROOT.as_posix()}
    args = [a.format(**subs) for a in json.loads((case / "args.json").read_text(encoding="utf-8"))]
    env = {k: v for k, v in os.environ.items() if k != "ATC_ROOT"}
    env["PYTHONIOENCODING"] = "utf-8"
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=input_dir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = (run.stdout + run.stderr).replace("\\", "/")
    expected = json.loads((case / "expected" / "result.json").read_text(encoding="utf-8"))
    assert run.returncode == expected["exit_code"], output
    for needle in expected.get("output_contains", []):
        assert needle in output, f"missing {needle!r} in:\n{output}"
    for golden in sorted((case / "expected").rglob("*")):
        if golden.is_dir() or golden.name == "result.json":
            continue
        rel = golden.relative_to(case / "expected")
        produced = out_dir / rel
        assert produced.is_file(), f"{rel} was not produced"
        assert _normalise(produced.read_text(encoding="utf-8")) == _normalise(golden.read_text(encoding="utf-8")), rel

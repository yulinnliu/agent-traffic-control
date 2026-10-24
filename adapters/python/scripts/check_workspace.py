#!/usr/bin/env python3
"""Local wrapper: run validate_workspace.py and capture failure evidence.

`validate_workspace.py` is the contract test suite and stays untouched — its
job is to pass or fail, not to manage friction state. This wrapper just runs
it and forwards its exit code. Exit 1 is a real contract-validation failure:
the wrapper saves the full reproducible output to
`state/workspace-friction/validator/<timestamp>.log` and appends a
VALIDATOR_DRIFT event via friction.py. Any other non-zero exit (e.g. an
invalid CLI argument) is propagated as-is with no event recorded — it is
wrapper/invocation misuse, not workspace contract drift. See
docs/FRICTION_EVIDENCE.md.

Local workflow only — not run in CI. CI keeps calling
`uv run --locked python scripts/validate_workspace.py` directly, which produces no
machine-local runtime state.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import friction

ROOT = Path(__file__).resolve().parents[3]
def run_check(
    *,
    root: Path = ROOT,
    validator_script: Path | None = None,
    events_file: Path = friction.EVENTS_FILE,
    forwarded_args: list[str] | None = None,
    python_executable: str = sys.executable,
) -> int:
    forwarded_args = forwarded_args or []
    validator_script = validator_script or root / "scripts" / "validate_workspace.py"
    cmd = [python_executable, str(validator_script), *forwarded_args]

    result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)

    if result.returncode == 0:
        return 0

    if result.returncode != 1:
        # Not a contract-validation failure (e.g. invalid CLI argument to
        # validate_workspace.py) — propagate the exit code without recording
        # a VALIDATOR_DRIFT event.
        return result.returncode

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    validator_log_dir = events_file.parent / "validator"
    validator_log_dir.mkdir(parents=True, exist_ok=True)
    log_path = validator_log_dir / f"{timestamp}.log"
    log_path.write_text(
        f"command: {' '.join(cmd)}\n"
        f"cwd: {root}\n"
        f"exit_code: {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n",
        encoding="utf-8",
    )

    event = friction.append_incident(
        kind="VALIDATOR_DRIFT",
        project="agent-traffic-control",
        tool="workspace-validator",
        expected="python scripts/validate_workspace.py exits 0",
        observed=f"exited {result.returncode}",
        evidence=str(log_path.relative_to(root)),
        recurrence_key="workspace.validator-contract-failure",
        events_file=events_file,
    )
    print(f"check_workspace: validator failed — logged {event['kind']} event, evidence at {log_path}")

    return result.returncode


def main() -> int:
    return run_check(forwarded_args=sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())

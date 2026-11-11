"""``atc test status``: the active test runs from the host pool's ledger rows (docs/TESTKIT.md § Commands).

It reads the rows and the tail of each run's log; it removes no row and takes no token.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import time

from . import stages, workers as host_workers

PROGRESS = re.compile(rb"\[\s*(\d{1,3})%\]")
TAIL_BYTES = 8192


def progress(log: str) -> int | None:
    """The last ``[ NN%]`` pytest printed in the last ``TAIL_BYTES`` of ``log``; ``None`` when there is none."""
    if not log:
        return None
    try:
        with Path(log).open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - TAIL_BYTES))
            found = PROGRESS.findall(handle.read())
    except OSError:
        return None
    return int(found[-1]) if found else None


def _run(row: host_workers.Row, now: float) -> dict:
    return {"label": row.label, "pid": row.pid, "stage": row.stage, "tokens": row.tokens, "want": row.want,
            "floor": row.floor, "held_seconds": round(now - row.granted_at) if row.granted_at else None,
            "waiting_seconds": round(now - row.waiting_since) if row.waiting_since else None,
            "progress": progress(row.log), "log": row.log or None, "limit": row.limit or None}


def collect(environ: Mapping[str, str] | None = None) -> dict:
    """The pool's state: ``state`` ``ci``, ``off``, ``unused`` or ``on``, its size, free tokens, runs and waiters."""
    environ = os.environ if environ is None else environ
    if environ.get("CI"):
        return {"state": "ci", "runs": [], "waiters": []}
    size = stages.pool_size(stages.host_table(environ))
    directory = stages.pool_directory(environ)
    if size == 0:
        return {"state": "off", "size": 0, "runs": [], "waiters": []}
    if not (directory / host_workers.POOL_FILE).exists():
        return {"state": "unused", "directory": str(directory), "size": size, "free": size, "runs": [],
                "waiters": []}
    found, now = host_workers.Pool(directory, size).status(prune=False), time.time()
    return {"state": "on", "directory": str(directory), "size": found.size, "free": found.free,
            "runs": [_run(row, now) for row in found.runs], "waiters": [_run(row, now) for row in found.waiters]}


def lines(state: dict) -> list[str]:
    if state["state"] == "ci":
        return ["CI worker pool: CI is set, runs use no host pool"]
    if state["state"] == "off":
        return ["OFF worker pool: test_worker_pool = 0 in ~/.atc/config.toml"]
    if not state["runs"] and not state["waiters"]:
        return [f"IDLE worker pool: {state['free']} of {state['size']} free"]
    found = [f"worker pool: {state['free']} of {state['size']} free"]
    for run in state["runs"]:
        held = _duration(run["held_seconds"])
        done = "-" if run["progress"] is None else f"{run['progress']}%"
        found.append(f"RUN {run['label']} pid {run['pid']} {run['stage']} {run['tokens']}/{run['want']} workers "
                     f"held {held} progress {done} log {run['log'] or '-'}")
        if run["limit"]:
            found.append(f"  {run['limit']}")
    for run in state["waiters"]:
        found.append(f"WAIT {run['label']} pid {run['pid']} {run['stage']} waiting "
                     f"{_duration(run['waiting_seconds'])} for {run['floor']}")
    return found


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def show(as_json: bool = False, environ: Mapping[str, str] | None = None) -> int:
    state = collect(environ)
    print(json.dumps(state, indent=2) if as_json else "\n".join(lines(state)), flush=True)
    return 0

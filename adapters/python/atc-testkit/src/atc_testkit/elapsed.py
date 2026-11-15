"""Elapsed time per stage: one ``ELAPSED`` line and one record in ``timings.jsonl`` (docs/TESTKIT.md § Stage times)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time

FILE = "timings.jsonl"
TASK_VARIABLE = "ATC_TASK"
PREFIX = "ELAPSED"


def line(stage: str, seconds: float, workers: int, result: str) -> str:
    """``ELAPSED <stage> <seconds>s workers <n> <result>``."""
    return f"{PREFIX} {stage} {seconds:.1f}s workers {workers} {result}"


@dataclass(frozen=True)
class Clock:
    """Where one command's stage times go: its task, commit and command, and the directory holding ``FILE``
    (``None`` keeps no record)."""
    task: str
    sha: str
    command: str
    directory: Path | None

    def stage(self, stage: str, seconds: float, workers: int, result: str) -> str:
        """Print the stage's line and append its record; a record that cannot be written is skipped."""
        shown = line(stage, seconds, workers, result)
        print(shown, flush=True)
        if self.directory is not None:
            append(self.directory / FILE, {
                "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "task": self.task, "sha": self.sha,
                "command": self.command, "stage": stage, "seconds": round(seconds, 1), "workers": workers,
                "result": result,
            })
        return shown


def append(path: Path, record: Mapping[str, object]) -> None:
    """One JSON line, written in a single call."""
    data = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
        try:
            os.write(descriptor, data)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def records(directory: Path) -> list[dict]:
    """The records of ``directory``'s ``FILE`` in order; a line that is not a JSON object is skipped."""
    try:
        text = (directory / FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = []
    for raw in text.splitlines():
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def task_of(environ: Mapping[str, str]) -> str:
    return environ.get(TASK_VARIABLE, "")

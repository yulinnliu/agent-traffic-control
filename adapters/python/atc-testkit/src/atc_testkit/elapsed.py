"""Elapsed time per stage: one ``ELAPSED`` line and one record in ``timings.jsonl`` (docs/TESTKIT.md § Stage times)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import time

from . import __version__

FILE = "timings.jsonl"
TASK_VARIABLE = "ATC_TASK"
PROJECT_VARIABLE = "ATC_PROJECT"
VERSION_VARIABLE = "ATC_VERSION"
PREFIX = "ELAPSED"
TASK_SEPARATOR = ","
# The `selection` values of a record.
FULL, RELATED, EXPLICIT, NONE, DOCUMENTATION_ONLY = "full", "related", "explicit", "none", "documentation-only"
REUSED = "REUSED"
REUSED_STAGE = "reused"
FULL_SUITE_MARK = "-> full suite"
# A pytest summary line's counts; `error` and `errors` count as failed.
COUNT = re.compile(r"\b(\d+) (passed|failed|errors?|skipped)\b")
SUMMARY_LINES = 200
GIT_TIMEOUT = 10


def line(stage: str, seconds: float, workers: int, result: str) -> str:
    """``ELAPSED <stage> <seconds>s workers <n> <result>``."""
    return f"{PREFIX} {stage} {seconds:.1f}s workers {workers} {result}"


def counts(text: str) -> dict[str, int | None]:
    """``passed``, ``failed`` and ``skipped`` from the last line of ``text`` that carries a pytest count; ``None``
    each when no line does."""
    for raw in reversed(text.splitlines()):
        found = COUNT.findall(raw)
        if found:
            tally = {"passed": 0, "failed": 0, "skipped": 0}
            for number, word in found:
                tally["failed" if word.startswith("error") else word] += int(number)
            return tally
    return {"passed": None, "failed": None, "skipped": None}


def log_counts(log: Path | None) -> dict[str, int | None]:
    """``counts`` of the last ``SUMMARY_LINES`` lines of ``log``."""
    try:
        text = log.read_text(encoding="utf-8", errors="replace") if log is not None else ""
    except OSError:
        text = ""
    return counts("\n".join(text.splitlines()[-SUMMARY_LINES:]))


def selection_reason(kind: str, reasons: Sequence[str], changed: int | None) -> str:
    """Why a selection is what it is: for ``full`` its first full-suite reason and how many more there are, else the
    count of changed paths."""
    if kind == FULL:
        widening = [reason for reason in reasons if reason.endswith(FULL_SUITE_MARK)]
        if widening:
            more = f" (+{len(widening) - 1} more)" if len(widening) > 1 else ""
            return widening[0] + more
    return "" if changed is None else f"{changed} changed paths"


def kind_of(pytest_paths: Sequence[str] | None) -> str:
    """The `selection` of a selection's pytest paths: ``[]`` the full suite, ``None`` no test."""
    if pytest_paths is None:
        return NONE
    return FULL if not pytest_paths else RELATED


def project_of(environ: Mapping[str, str], root: Path | None) -> str:
    """``ATC_PROJECT``, else the name of the directory holding ``root``'s Git common directory (a worktree and a
    candidate name their main checkout), else ``""``."""
    named = environ.get(PROJECT_VARIABLE, "")
    if named or root is None:
        return named
    try:
        done = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=root,
                              capture_output=True, text=True, timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    common = done.stdout.strip() if done.returncode == 0 else ""
    return Path(common).parent.name if common else ""


@dataclass
class Clock:
    """Where one command's stage times go: its task, commit and command, and the directory holding ``FILE``
    (``None`` keeps no record); ``fields`` are the run's own record fields (project, versions, selection), and
    ``notes`` a stage's fields known before it runs (its reused and run files)."""
    task: str
    sha: str
    command: str
    directory: Path | None
    fields: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)

    @classmethod
    def create(cls, task: str, sha: str, command: str, directory: Path | None, environ: Mapping[str, str],
               root: Path | None = None) -> Clock:
        return cls(task, sha, command, directory, {
            "project": project_of(environ, root), "atc_version": environ.get(VERSION_VARIABLE, ""),
            "testkit_version": __version__, "selection": "", "selection_reason": "",
        })

    def select(self, kind: str, reason: str) -> None:
        self.fields.update(selection=kind, selection_reason=reason)

    def note(self, stage: str, **values: object) -> None:
        self.notes.setdefault(stage, {}).update(values)

    def stage(self, stage: str, seconds: float, workers: int, result: str, log: Path | None = None,
              summary: str | None = None, show: bool = True) -> str:
        """Print the stage's line (unless not ``show``) and append its record; a record that cannot be written is
        skipped. The test counts come from ``summary``, else from ``log``."""
        shown = line(stage, seconds, workers, result)
        if show:
            print(shown, flush=True)
        if self.directory is not None:
            tally = counts(summary) if summary is not None else log_counts(log)
            append(self.directory / FILE, {
                "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "task": self.task, "sha": self.sha,
                "command": self.command, "stage": stage, "seconds": round(seconds, 1), "workers": workers,
                "result": result, "reused_files": None, "run_files": None, **self.fields, **tally,
                **self.notes.get(stage, {}),
            })
        return shown

    def reused(self, show: bool = True) -> str:
        """The record of a run another run's evidence stood in for as a whole."""
        return self.stage(REUSED_STAGE, 0.0, 0, REUSED, summary="", show=show)


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


def tasks_value(tasks: Sequence[str]) -> str:
    """``ATC_TASK`` for a run of several tasks: their IDs joined by ``TASK_SEPARATOR``."""
    return TASK_SEPARATOR.join(dict.fromkeys(task for task in tasks if task))

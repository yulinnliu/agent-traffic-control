"""`atc test --timings`: test efficiency per project and version, from the host's stage-time records
(docs/TESTKIT.md § Stage times)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import json

from atc_testkit import elapsed

UNKNOWN = "-"
# Stages that run no pytest: their seconds are not test seconds.
NO_TEST_STAGES = frozenset({"checks", elapsed.REUSED_STAGE})
COLUMNS = ("project", "atc", "testkit", "runs", "tests", "test-s", "s/test", "full", "full-s", "reuse",
           "reused-runs")


@dataclass
class Group:
    """The records of one project, atc version and testkit version, or of one command within them."""
    runs: set = field(default_factory=set)
    full_runs: set = field(default_factory=set)
    reused_runs: set = field(default_factory=set)
    tests: int = 0
    counted_seconds: float = 0.0
    test_seconds: float = 0.0
    full_seconds: float = 0.0
    reused_files: int = 0
    run_files: int = 0

    def add(self, record: dict) -> None:
        run = (record.get("command"), record.get("task"), record.get("sha"))
        self.runs.add(run)
        seconds = float(record.get("seconds") or 0)
        stage = record.get("stage")
        if stage == elapsed.REUSED_STAGE:
            self.reused_runs.add(run)
        if stage not in NO_TEST_STAGES:
            self.test_seconds += seconds
            counts = [record.get(key) for key in ("passed", "failed", "skipped")]
            if all(isinstance(value, int) for value in counts):
                self.tests += sum(counts)
                self.counted_seconds += seconds
            if record.get("selection") == elapsed.FULL:
                self.full_runs.add(run)
                self.full_seconds += seconds
        if isinstance(record.get("reused_files"), int) and isinstance(record.get("run_files"), int):
            self.reused_files += record["reused_files"]
            self.run_files += record["run_files"]

    def row(self) -> dict:
        files = self.reused_files + self.run_files
        return {
            "runs": len(self.runs), "tests": self.tests, "test_seconds": round(self.test_seconds, 1),
            "seconds_per_test": round(self.counted_seconds / self.tests, 3) if self.tests else None,
            "full_runs": len(self.full_runs),
            "full_seconds": round(self.full_seconds / len(self.full_runs), 1) if self.full_runs else None,
            "reuse": round(self.reused_files / files, 3) if files else None, "reused_runs": len(self.reused_runs),
        }


def project_of(record: dict, by_task: Callable[[str], str | None]) -> str:
    """The record's project; a record written before it carried one, the project its first task's prefix names."""
    named = record.get("project")
    if isinstance(named, str) and named:
        return named
    task = str(record.get("task") or "").split(elapsed.TASK_SEPARATOR)[0]
    return (by_task(task) if task else None) or UNKNOWN


def summarize(records: Iterable[dict], by_task: Callable[[str], str | None], project: str | None = None,
              since: str | None = None) -> list[dict]:
    """One row per project, atc version and testkit version, each with one row per command under ``commands``."""
    groups: dict[tuple[str, str, str], Group] = {}
    commands: dict[tuple[str, str, str, str], Group] = {}
    for record in records:
        if since and str(record.get("time", "")) < since:
            continue
        key = (project_of(record, by_task), record.get("atc_version") or UNKNOWN,
               record.get("testkit_version") or UNKNOWN)
        if project and key[0] != project:
            continue
        groups.setdefault(key, Group()).add(record)
        commands.setdefault((*key, str(record.get("command") or UNKNOWN)), Group()).add(record)
    return [
        {"project": key[0], "atc": key[1], "testkit": key[2], **group.row(),
         "commands": [{"command": command[3], **commands[command].row()}
                      for command in sorted(commands) if command[:3] == key]}
        for key, group in sorted(groups.items())
    ]


def _cell(value: object) -> str:
    if value is None:
        return UNKNOWN
    return f"{value:g}" if isinstance(value, float) else str(value)


def render(rows: list[dict]) -> list[str]:
    """The table: one line per group, its commands indented under it."""
    def cells(head: list[str], row: dict) -> list[str]:
        reuse = UNKNOWN if row["reuse"] is None else f"{row['reuse']:.0%}"
        return [*head, str(row["runs"]), str(row["tests"] or UNKNOWN), f"{row['test_seconds']:.1f}",
                _cell(row["seconds_per_test"]), str(row["full_runs"]), _cell(row["full_seconds"]), reuse,
                str(row["reused_runs"])]
    table = [list(COLUMNS)]
    for row in rows:
        table.append(cells([row["project"], row["atc"], row["testkit"]], row))
        for command in row["commands"]:
            table.append(cells([f"  {command['command']}", "", ""], command))
    widths = [max(len(line[index]) for line in table) for index in range(len(COLUMNS))]
    return ["  ".join(cell.ljust(width) if index < 3 else cell.rjust(width)
                      for index, (cell, width) in enumerate(zip(line, widths))).rstrip() for line in table]


def report(records: Iterable[dict], by_task: Callable[[str], str | None], *, project: str | None = None,
           since: str | None = None, as_json: bool = False) -> int:
    rows = summarize(records, by_task, project, since)
    if as_json:
        print(json.dumps({"groups": rows}, indent=2))
        return 0
    if not rows:
        print("no stage-time records")
        return 0
    print("\n".join(render(rows)))
    return 0

#!/usr/bin/env python3
"""Append-only friction incident log with replayed resolution state."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[3]
FRICTION_DIR = ROOT / "state" / "workspace-friction"
EVENTS_FILE = FRICTION_DIR / "events.jsonl"

ALLOWED_KINDS = (
    "RULE_NOT_FOUND",
    "ADAPTER_NOT_DISCOVERED",
    "VALIDATOR_DRIFT",
    "POLICY_MISUNDERSTANDING",
    "WORKSPACE_RESOLUTION_FAILURE",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def _legacy_id(event: dict, line_number: int) -> str:
    raw = json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"legacy_{line_number}_{hashlib.sha256(raw).hexdigest()[:12]}"


def append_record(record: dict, events_file: Path = EVENTS_FILE) -> dict:
    events_file.parent.mkdir(parents=True, exist_ok=True)
    with events_file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def append_incident(
    *,
    kind: str,
    project: str,
    tool: str,
    expected: str,
    observed: str,
    evidence: str,
    recurrence_key: str,
    policy_key: str | None = None,
    skill: str | None = None,
    session_id: str | None = None,
    events_file: Path = EVENTS_FILE,
) -> dict:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"invalid kind {kind!r} — must be one of {', '.join(ALLOWED_KINDS)}")
    return append_record(
        {
            "event_id": new_event_id(),
            "event_type": "INCIDENT",
            "time": now_iso(),
            "kind": kind,
            "project": project,
            "tool": tool,
            "policy_key": policy_key,
            "skill": skill,
            "expected": expected,
            "observed": observed,
            "evidence": evidence,
            "recurrence_key": recurrence_key,
            "session_id": session_id,
        },
        events_file,
    )


# Narrow compatibility API for check_workspace.py and older local callers.
append_event = append_incident


def load_records(events_file: Path = EVENTS_FILE) -> list[dict]:
    if not events_file.exists():
        return []
    records: list[dict] = []
    for line_number, line in enumerate(events_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{events_file}:{line_number}: invalid JSON ({exc})") from exc
        if not isinstance(event, dict):
            raise ValueError(f"{events_file}:{line_number}: event must be an object")
        if "event_type" not in event:  # Replay compatibility for v0.1 incidents.
            event = dict(event)
            event["event_id"] = _legacy_id(event, line_number)
            event["event_type"] = "INCIDENT"
        records.append(event)
    return records


def project_state(records: Iterable[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    incidents: dict[str, dict] = {}
    resolutions: dict[str, dict] = {}
    for record in records:
        if record.get("event_type") == "INCIDENT":
            incidents[record["event_id"]] = record
        elif record.get("event_type") == "RESOLUTION":
            target = record.get("resolves_event_id")
            if target:
                resolutions[target] = record
    return incidents, resolutions


def resolve_incident(event_id: str, evidence: str, events_file: Path = EVENTS_FILE) -> dict:
    incidents, resolutions = project_state(load_records(events_file))
    if event_id not in incidents:
        raise ValueError(f"incident {event_id!r} does not exist")
    if event_id in resolutions:
        raise ValueError(f"incident {event_id!r} is already resolved")
    return append_record(
        {
            "event_id": new_event_id(),
            "event_type": "RESOLUTION",
            "resolves_event_id": event_id,
            "time": now_iso(),
            "evidence": evidence,
        },
        events_file,
    )


def threshold(group: list[dict]) -> str:
    projects = {event.get("project") for event in group}
    tools = {event.get("tool") for event in group}
    sessions = {event.get("session_id") for event in group if event.get("session_id")}
    if len(group) >= 3 or len(projects) > 1 or len(tools) > 1:
        return "GOVERNANCE_CHANGE_CANDIDATE"
    if len(group) >= 2 and len(sessions) >= 2:
        return "INVESTIGATE"
    if len(group) >= 2:
        return "RECORD_ONLY_SESSION_EVIDENCE_MISSING"
    return "RECORD_ONLY"


def print_open(events_file: Path) -> None:
    incidents, resolutions = project_state(load_records(events_file))
    open_events = [event for event_id, event in incidents.items() if event_id not in resolutions]
    if not open_events:
        print("No open friction incidents.")
        return
    for event in sorted(open_events, key=lambda item: item.get("time", "")):
        print(
            f"{event['event_id']}  {event.get('kind', '-')}  "
            f"{event.get('project', '-')}  {event.get('recurrence_key', '-')}"
        )


def print_summary(events_file: Path, group_field: str) -> None:
    incidents, resolutions = project_state(load_records(events_file))
    groups: dict[str, list[dict]] = defaultdict(list)
    for event in incidents.values():
        groups[str(event.get(group_field) or "(none)")].append(event)
    if not groups:
        print("No friction incidents recorded.")
        return
    print(f"group={group_field}  total  open  projects  tools  threshold")
    for key in sorted(groups):
        group = groups[key]
        open_count = sum(event["event_id"] not in resolutions for event in group)
        projects = len({event.get("project") for event in group})
        tools = len({event.get("tool") for event in group})
        print(f"{key}  {len(group)}  {open_count}  {projects}  {tools}  {threshold(group)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add = subparsers.add_parser("add", help="append one incident")
    add.add_argument("--kind", required=True, choices=ALLOWED_KINDS)
    add.add_argument("--project", required=True)
    add.add_argument("--tool", required=True)
    add.add_argument("--policy-key")
    add.add_argument("--skill")
    add.add_argument("--session-id")
    add.add_argument("--expected", required=True)
    add.add_argument("--observed", required=True)
    add.add_argument("--evidence", required=True)
    add.add_argument("--recurrence-key", required=True)

    list_parser = subparsers.add_parser("list", help="list projected incidents")
    list_parser.add_argument("--open", action="store_true", dest="open_only", required=True)

    summary = subparsers.add_parser("summary", help="summarize incident recurrence")
    summary.add_argument("--group", choices=("recurrence_key",), default="recurrence_key")

    resolve = subparsers.add_parser("resolve", help="append a resolution event")
    resolve.add_argument("event_id")
    resolve.add_argument("--evidence", required=True)
    return parser


def main(argv: list[str] | None = None, events_file: Path = EVENTS_FILE) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "add":
            event = append_incident(
                kind=args.kind,
                project=args.project,
                tool=args.tool,
                policy_key=args.policy_key,
                skill=args.skill,
                session_id=args.session_id,
                expected=args.expected,
                observed=args.observed,
                evidence=args.evidence,
                recurrence_key=args.recurrence_key,
                events_file=events_file,
            )
            print(f"friction: appended incident {event['event_id']} to {events_file}")
        elif args.command == "list":
            print_open(events_file)
        elif args.command == "summary":
            print_summary(events_file, args.group)
        elif args.command == "resolve":
            event = resolve_incident(args.event_id, args.evidence, events_file)
            print(f"friction: appended resolution {event['event_id']} for {args.event_id}")
        return 0
    except ValueError as exc:
        print(f"friction: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

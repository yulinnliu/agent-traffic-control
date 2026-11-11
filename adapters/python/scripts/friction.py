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

import cli_conventions

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


class AlreadyResolved(ValueError):
    """The incident already has a resolution event."""


def resolve_incident(event_id: str, evidence: str, events_file: Path = EVENTS_FILE) -> dict:
    incidents, resolutions = project_state(load_records(events_file))
    if event_id not in incidents:
        raise ValueError(f"incident {event_id!r} does not exist")
    if event_id in resolutions:
        raise AlreadyResolved(f"incident {event_id!r} is already resolved")
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


def print_open(events_file: Path, every: bool = False, as_json: bool = False) -> None:
    incidents, resolutions = project_state(load_records(events_file))
    shown = [event for event_id, event in incidents.items() if every or event_id not in resolutions]
    shown.sort(key=lambda item: item.get("time", ""))
    if as_json:
        print(json.dumps({"incidents": [{**event, "resolved": event["event_id"] in resolutions} for event in shown]},
                         indent=2))
        return
    if not shown:
        print("No friction incidents." if every else "No open friction incidents.")
        return
    for event in shown:
        state = ("resolved  " if event["event_id"] in resolutions else "open  ") if every else ""
        print(
            f"{event['event_id']}  {state}{event.get('kind', '-')}  "
            f"{event.get('project', '-')}  {event.get('recurrence_key', '-')}"
        )


def print_summary(events_file: Path, group_field: str, as_json: bool = False) -> None:
    incidents, resolutions = project_state(load_records(events_file))
    groups: dict[str, list[dict]] = defaultdict(list)
    for event in incidents.values():
        groups[str(event.get(group_field) or "(none)")].append(event)
    if as_json:
        print(json.dumps({"group": group_field, "groups": [{
            "key": key, "total": len(group), "open": sum(e["event_id"] not in resolutions for e in group),
            "projects": len({e.get("project") for e in group}), "tools": len({e.get("tool") for e in group}),
            "threshold": threshold(group)} for key, group in sorted(groups.items())]}, indent=2))
        return
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


FRICTION_EXIT = "0 OK; 1 the incident is already resolved; 2 usage error, an invalid kind, an unknown event or an unreadable log"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc friction", allow_abbrev=False, **cli_conventions.form(
        "Append-only friction incident log with replayed resolution state.",
        exit=FRICTION_EXIT, see="README.md § Commands"))
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    add = subparsers.add_parser("add", allow_abbrev=False, help="append one incident",
                                **cli_conventions.form("Append one friction incident to the log.", exit=FRICTION_EXIT, see="README.md § Commands"))
    add.add_argument("--kind", required=True, choices=ALLOWED_KINDS, help="the incident's kind")
    add.add_argument("--project", required=True, metavar="NAME",
                     help="the repository where it happened (free text; need not be registered)")
    add.add_argument("--tool", required=True, metavar="NAME", help="the agent tool, for example claude or codex")
    add.add_argument("--policy-key", metavar="KEY", help="the governance or policy rule involved")
    add.add_argument("--skill", metavar="NAME", help="the skill involved")
    add.add_argument("--session-id", metavar="ID", help="the session that met it")
    add.add_argument("--expected", required=True, metavar="TEXT", help="what should have happened")
    add.add_argument("--observed", required=True, metavar="TEXT", help="what happened")
    add.add_argument("--evidence", required=True, metavar="TEXT", help="where to see it: a path, SHA or quote")
    add.add_argument("--recurrence-key", required=True, metavar="KEY", help="groups incidents of the same cause")

    list_parser = subparsers.add_parser("list", allow_abbrev=False, help="list incidents, open ones by default",
                                        **cli_conventions.form("List friction incidents, oldest first.", exit=FRICTION_EXIT, see="README.md § Commands"))
    shown = list_parser.add_mutually_exclusive_group()
    shown.add_argument("--open", action="store_true", dest="open_only", help="only unresolved incidents (default)")
    shown.add_argument("--all", action="store_true", help="resolved incidents too")

    summary = subparsers.add_parser("summary", allow_abbrev=False, help="summarize incident recurrence",
                                    **cli_conventions.form("Summarize incidents by recurrence, with each group's "
                                                           "escalation threshold.", exit=FRICTION_EXIT, see="README.md § Commands"))
    summary.add_argument("--group", choices=("recurrence_key",), default="recurrence_key",
                         help="the field incidents are grouped by")

    resolve = subparsers.add_parser("resolve", allow_abbrev=False, help="append a resolution event",
                                    **cli_conventions.form("Append a resolution event for one incident.",
                                                           exit=FRICTION_EXIT, see="README.md § Commands"))
    resolve.add_argument("event_id", metavar="EVENT_ID", help="the incident to resolve")
    resolve.add_argument("--evidence", required=True, metavar="TEXT", help="where the fix can be seen")
    for p in (add, list_parser, summary, resolve):
        cli_conventions.add_json(p)
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
            print(json.dumps({"verdict": "APPENDED", "event": event}, indent=2) if args.json
                  else f"friction: appended incident {event['event_id']} to {events_file}")
        elif args.command == "list":
            print_open(events_file, every=args.all, as_json=args.json)
        elif args.command == "summary":
            print_summary(events_file, args.group, as_json=args.json)
        elif args.command == "resolve":
            event = resolve_incident(args.event_id, args.evidence, events_file)
            print(json.dumps({"verdict": "APPENDED", "event": event}, indent=2) if args.json
                  else f"friction: appended resolution {event['event_id']} for {args.event_id}")
        return 0
    except AlreadyResolved as exc:
        print(f"STOP ALREADY_RESOLVED: {exc}", file=sys.stderr)
        return cli_conventions.STOP
    except ValueError as exc:
        print(f"ERROR FRICTION_INPUT {exc}", file=sys.stderr)
        return cli_conventions.ERROR


if __name__ == "__main__":
    sys.exit(main())

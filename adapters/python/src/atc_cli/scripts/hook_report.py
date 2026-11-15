#!/usr/bin/env python3
"""What `atc guard`, `atc doctor` and `sync --check` report about the PreToolUse hook: its harness entries, its
baseline, whether its table composes, what the baseline keeps (docs/ENFORCEMENT.md § PreToolUse hook). Composes in
memory; writes nothing."""

from __future__ import annotations

from pathlib import Path

import cli_conventions
import generate_enforcement as ge
import hook_state

INSTALL_HINT = "the Owner runs atc guard --install from their own terminal"
RESET_HINT = "the Owner runs atc guard --reset from their own terminal"
HARNESSES = ("claude", "codex")


def settings_files(home: Path) -> dict[str, Path]:
    return {"claude": home / ".claude" / "settings.json", "codex": home / ".codex" / "hooks.json"}


def groups(workspace: Path, home: Path, atc_home: Path) -> dict[str, dict]:
    return {h: ge.hook_group(h, ge.engine_command(workspace, h, home, atc_home)) for h in HARNESSES}


def entries(workspace: Path, home: Path, atc_home: Path) -> dict[str, str]:
    """Each harness's entry: `current`, `missing` or `outdated`."""
    wanted = groups(workspace, home, atc_home)
    return {h: ge.entry_state(path, wanted[h]) for h, path in settings_files(home).items()}


def compose(workspace: Path, home: Path, atc_home: Path) -> dict:
    """The composing child's result, its failure under `failure`."""
    try:
        return hook_state.run_child(workspace, home, atc_home, hook_state.OWNER_TIMEOUT)
    except hook_state.Failure as failure:
        return {"failure": failure.as_dict()}


def _table_of(path: Path) -> dict | None:
    """The table held by a baseline, a cache record or a legacy table file; None when it holds none."""
    try:
        data = hook_state.read_json(path)
    except (OSError, ValueError):
        return None
    if hook_state.valid_table(data):
        return data
    table = data.get("table") if isinstance(data, dict) else None
    return table if hook_state.valid_table(table) else None


def report(workspace: Path, home: Path, atc_home: Path) -> dict:
    """What `atc guard` and `atc doctor` report; composes in memory and writes nothing."""
    paths = hook_state.state_paths(atc_home, workspace)
    found: dict = {"entries": entries(workspace, home, atc_home)}
    try:
        baseline = hook_state.read_baseline(paths["baseline"])
        found["baseline"] = {"status": "READY", "written_by": baseline.get("written_by"), "at": baseline.get("at")}
    except hook_state.Failure as failure:
        baseline = None
        found["baseline"] = {"status": failure.token, "detail": failure.detail}
    result = compose(workspace, home, atc_home)
    record = hook_state._read_or_none(paths["cache"])
    cached = isinstance(record, dict) and record.get("key") == result.get("key")
    if "failure" in result:
        found["table"] = {"composes": False, **result["failure"]}
    else:
        found["table"] = {"composes": True, "rules": len(result["table"]["commands"]) + len(result["table"]["filesystem"]),
                          "inputs": result.get("inputs"), "cached": bool(cached and record.get("table"))}
    found["retained"] = (hook_state.ratchet(baseline["table"], result["table"])[1]
                         if baseline is not None and "table" in result else {"rules": [], "write_roots": []})
    found["legacy"] = (str(paths["legacy"]) if paths["legacy"].is_file()
                       and all(state == "current" for state in found["entries"].values()) else None)
    return found


def stop_of(found: dict) -> cli_conventions.Refusal | None:
    """The first problem of a report, as the refusal `atc guard` prints."""
    for harness, state in found["entries"].items():
        if state != "current":
            return cli_conventions.Refusal(f"ENTRY_{state.upper()}", f"the {harness} hook entry is {state}",
                                           INSTALL_HINT)
    if found["baseline"]["status"] != "READY":
        return cli_conventions.Refusal(found["baseline"]["status"], found["baseline"]["detail"], RESET_HINT)
    if not found["table"]["composes"]:
        return cli_conventions.Refusal(found["table"]["token"], found["table"]["detail"],
                                       "fix the input it names; every session is blocked until then")
    return None


def warnings(found: dict) -> list[str]:
    lines = []
    retained = found["retained"]
    if retained["rules"] or retained["write_roots"]:
        lines.append(f"{len(retained['rules'])} rules kept from earlier inputs"
                     + (f" ({', '.join(retained['rules'])})" if retained["rules"] else "")
                     + (f"; write roots not in effect: {', '.join(retained['write_roots'])}"
                        if retained["write_roots"] else "")
                     + "; the Owner runs atc guard --reset to drop them")
    if found["legacy"]:
        lines.append(f"{found['legacy']} is the static table of an older entry; atc guard --reset removes it")
    return lines

#!/usr/bin/env python3
"""Load a policy directory, validate each file against its schema, and refuse
contradictions.

    policies.py check <dir> [--schemas <dir>]

<dir> holds any of hard-deny.yaml, approval-required.yaml,
permission-presets.yaml and escalation.yaml. Each present file must match its
schema under shared/policies/schemas/. A permission preset that grants a
capability or a write path hard deny denies for edit or write is refused
(`preset-regrants-hard-deny`), as is an approval-required id that hard deny
already makes impossible (`approval-overlaps-hard-deny`) and a glob listed
twice in `filesystem.deny` (`duplicate-glob`). Prints `ok` and
exits 0, or one problem per line and exits 1. A loader that proceeds past a
problem would turn a policy file into prose; this one fails closed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "shared" / "policies" / "schemas"
KINDS = ("hard-deny", "approval-required", "permission-presets", "escalation")
MODES = ("read", "edit", "write")


def filesystem_rules(hard: dict) -> list[tuple[str, tuple[str, ...]]]:
    """Each `filesystem.deny` entry as (glob, modes in MODES order); a bare glob denies every mode."""
    rules = []
    for entry in hard.get("filesystem", {}).get("deny", []):
        if isinstance(entry, str):
            rules.append((entry, MODES))
        else:
            modes = set(entry.get("modes", MODES))
            rules.append((entry["glob"], tuple(m for m in MODES if m in modes)))
    return rules


class PolicyError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def _read(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError([f"schema-violation: {path.name}: not valid YAML ({exc})"]) from exc


def _validate(kind: str, data: object, name: str, schemas: Path) -> list[str]:
    schema = json.loads((schemas / f"{kind}.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    problems = []
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"schema-violation: {name}: {where}: {error.message}")
    return problems


def load(directory: Path, schemas: Path = SCHEMAS) -> dict[str, dict]:
    """Return {kind: data} for the policy files present, or raise PolicyError."""
    directory = Path(directory)
    problems: list[str] = []
    loaded: dict[str, dict] = {}
    for kind in KINDS:
        path = directory / f"{kind}.yaml"
        if not path.is_file():
            continue
        data = _read(path)
        kind_problems = _validate(kind, data, path.name, schemas)
        if kind_problems:
            problems.extend(kind_problems)
        else:
            loaded[kind] = data
    if problems:
        raise PolicyError(problems)
    problems.extend(contradictions(loaded))
    if problems:
        raise PolicyError(problems)
    return loaded


def contradictions(loaded: dict[str, dict]) -> list[str]:
    hard = loaded.get("hard-deny")
    if hard is None:
        return []
    denied_caps = {c["id"] for c in hard["capabilities"]["deny"]}
    rules = filesystem_rules(hard)
    denied_paths = {glob for glob, modes in rules if {"edit", "write"} & set(modes)}
    problems: list[str] = []
    seen: set[str] = set()
    for glob, _ in rules:
        if glob in seen:
            problems.append(f"duplicate-glob: filesystem.deny lists '{glob}' more than once")
        seen.add(glob)
    for name, preset in loaded.get("permission-presets", {}).get("presets", {}).items():
        for cap in preset["capabilities"]:
            if cap in denied_caps:
                problems.append(f"preset-regrants-hard-deny: preset '{name}' grants capability '{cap}'")
        for glob in preset["filesystem"]["write"]:
            if glob in denied_paths:
                problems.append(f"preset-regrants-hard-deny: preset '{name}' writes denied path '{glob}'")
    for item in loaded.get("approval-required", {}).get("approval_required", []):
        if item["id"] in denied_caps:
            problems.append(f"approval-overlaps-hard-deny: '{item['id']}' is hard-denied, approval cannot grant it")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate a policy directory")
    check.add_argument("directory", type=Path)
    check.add_argument("--schemas", type=Path, default=SCHEMAS)
    args = parser.parse_args(argv)
    try:
        loaded = load(args.directory, args.schemas)
    except PolicyError as exc:
        for problem in exc.problems:
            print(problem)
        return 1
    if not loaded:
        print(f"no policy files found in {args.directory}")
        return 1
    print("ok: " + ", ".join(sorted(loaded)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

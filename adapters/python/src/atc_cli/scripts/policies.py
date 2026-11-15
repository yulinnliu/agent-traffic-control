#!/usr/bin/env python3
"""Load a policy directory, validate each file against its schema, and refuse contradictions.

A permission preset that grants a capability or a write path hard deny denies for edit or write is refused
(`preset-regrants-hard-deny`), as is an approval-required id that hard deny already makes impossible
(`approval-overlaps-hard-deny`) and a glob listed twice in `filesystem.deny` (`duplicate-glob`). A loader
that proceeds past a problem would turn a policy file into prose; this one fails closed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"  # the packaged copy of shared/policies/schemas
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


class StrictLoader(yaml.SafeLoader):
    """SafeLoader without anchors and aliases, so parsing time follows the text's size."""

    def compose_node(self, parent, index):  # noqa: ANN001 - PyYAML's signature
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise yaml.YAMLError("YAML anchors and aliases are not allowed")
        return super().compose_node(parent, index)


def strict_load(text: str | bytes) -> object:
    """``text`` as YAML with SafeLoader's types and no anchor or alias; raises yaml.YAMLError."""
    return yaml.load(text, Loader=StrictLoader)  # noqa: S506 - StrictLoader is a SafeLoader


def _parse(text: str, name: str) -> object:
    try:
        return strict_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError([f"schema-violation: {name}: not valid YAML ({exc})"]) from exc


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
    return load_texts({kind: (directory / f"{kind}.yaml").read_text(encoding="utf-8") for kind in KINDS
                       if (directory / f"{kind}.yaml").is_file()}, schemas)


def load_texts(texts: dict[str, str], schemas: Path = SCHEMAS) -> dict[str, dict]:
    """`load` of policy texts by kind, read elsewhere (a commit's Git objects)."""
    problems: list[str] = []
    loaded: dict[str, dict] = {}
    for kind in KINDS:
        if kind not in texts:
            continue
        name = f"{kind}.yaml"
        data = _parse(texts[kind], name)
        kind_problems = _validate(kind, data, name, schemas)
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
    import cli_conventions
    parser = argparse.ArgumentParser(prog="atc-maintain policies", allow_abbrev=False, **cli_conventions.form(
        "Load a policy directory, validate each file against its schema, and refuse contradictions.",
        exit="0 ok; 1 problems found; 2 usage error", see="README.md § Commands"))
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    check = sub.add_parser("check", allow_abbrev=False, help="validate a policy directory", **cli_conventions.form(
        "Validate the policy files in DIR: hard-deny.yaml, approval-required.yaml, permission-presets.yaml,\n"
        "escalation.yaml. Prints `OK <kinds>`, or `PROBLEMS N found` and one problem per line.",
        exit="0 ok; 1 problems found, or no policy file in DIR; 2 usage error",
        see="README.md § Commands"))
    check.add_argument("directory", type=Path, metavar="DIR", help="the policy directory to check")
    check.add_argument("--schemas", type=Path, default=SCHEMAS, metavar="DIR",
                       help="policy schema directory (default: the copy packaged with atc)")
    cli_conventions.add_json(check)
    args = parser.parse_args(argv)

    def report(verdict: str, problems: list[str], kinds: list[str]) -> int:
        if args.json:
            print(json.dumps({"verdict": verdict, "directory": str(args.directory), "kinds": kinds,
                              "problems": problems}, indent=2))
        elif problems:
            print(f"PROBLEMS {len(problems)} found")
            print("\n".join(f"  {problem}" for problem in problems))
            print("STOP POLICY_INVALID: each problem line names the policy file and the rule", file=sys.stderr)
            print("hint: fix them, then run atc-maintain policies check again", file=sys.stderr)
        else:
            print("OK " + ", ".join(kinds))
        return cli_conventions.STOP if problems else cli_conventions.OK

    try:
        loaded = load(args.directory, args.schemas)
    except PolicyError as exc:
        return report("PROBLEMS", list(exc.problems), [])
    if not loaded:
        return report("PROBLEMS", [f"no policy files found in {args.directory}"], [])
    return report("OK", [], sorted(loaded))


if __name__ == "__main__":
    sys.exit(main())

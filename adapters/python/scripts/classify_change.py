#!/usr/bin/env python3
"""Classify a delegated change LOW or HIGH under an escalation policy.

LOW means the topic is ready for a human fast-forward; HIGH means an independent
review runs first. HIGH wins whenever any `high_if_any` trigger fires; otherwise
the change is LOW only when every path matches `low_only_if`, and HIGH by default.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cli_conventions
import globs
import policies

SEVERITY = {"low": 0, "med": 1, "high": 2}


def classify(policy: dict, changed: list[str], severity: str | None) -> tuple[str, str]:
    high = policy["high_if_any"]
    for path in changed:
        for glob in high.get("path_globs", []):
            if globs.matches(path, glob):
                return "HIGH", f"{path} matches {glob}"
    limit = high.get("changed_files_over")
    if limit is not None and len(changed) > limit:
        return "HIGH", f"{len(changed)} files changed, over {limit}"
    threshold = high.get("review_severity_at_least")
    if threshold and severity and SEVERITY[severity] >= SEVERITY[threshold]:
        return "HIGH", f"review severity {severity} is at least {threshold}"
    low = policy["low_only_if"]
    cap = low.get("changed_files_at_most")
    if cap is not None and len(changed) > cap:
        return "HIGH", f"{len(changed)} files changed, LOW allows at most {cap}"
    for path in changed:
        if not any(globs.matches(path, glob) for glob in low["every_changed_file_matches"]):
            return "HIGH", f"{path} is outside the LOW surfaces"
    return "LOW", "every changed file is a LOW surface and no HIGH trigger fired"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atc classify", allow_abbrev=False, **cli_conventions.form(
        "Classify a delegated change LOW or HIGH under an escalation policy.",
        "HIGH wins whenever a high_if_any trigger fires; otherwise LOW only when every path matches low_only_if.",
        exit="0 either verdict; 2 ERROR (unreadable or invalid policy or change list)"))
    parser.add_argument("--policy", type=Path, required=True, metavar="FILE", help="the escalation.yaml to apply")
    parser.add_argument("--changed", type=Path, required=True, metavar="FILE",
                        help="a file listing one repo-relative changed path per line")
    parser.add_argument("--severity", choices=sorted(SEVERITY, key=SEVERITY.get),
                        help="the change's declared severity, compared with the policy's severity trigger")
    parser.add_argument("--schemas", type=Path, default=policies.SCHEMAS, metavar="DIR",
                        help="policy schema directory (default: shared/policies/schemas)")
    cli_conventions.add_json(parser)
    args = parser.parse_args(argv)

    def refuse(problems: list[str]) -> int:
        if args.json:
            print(json.dumps({"verdict": "ERROR", "problems": problems}, indent=2))
        else:
            print("\n".join(problems))
        return cli_conventions.ERROR

    try:
        loaded = policies.load(args.policy.parent, args.schemas)
    except policies.PolicyError as exc:
        return refuse(list(exc.problems))
    if "escalation" not in loaded or args.policy.name != "escalation.yaml":
        return refuse([f"{args.policy}: expected an escalation.yaml"])
    try:
        changed = [line.strip() for line in args.changed.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as exc:
        return refuse([f"{args.changed}: {exc.strerror or exc}"])
    verdict, reason = classify(loaded["escalation"], changed, args.severity)
    if args.json:
        print(json.dumps({"verdict": verdict, "reason": reason, "changed": len(changed)}, indent=2))
    else:
        print(f"{verdict}: {reason}")
    return cli_conventions.OK


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Classify a delegated change LOW or HIGH under an escalation policy.

    classify_change.py --policy escalation.yaml --changed <file> [--severity low|med|high]

<file> lists one repo-relative changed path per line. LOW means the topic is
ready for a human fast-forward; HIGH means an independent review runs first.
HIGH wins whenever any `high_if_any` trigger fires; otherwise the change is
LOW only when every path matches `low_only_if`, and HIGH by default. Prints
the verdict and its reason; exits 0 for either verdict, 1 on a bad input.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--changed", type=Path, required=True)
    parser.add_argument("--severity", choices=sorted(SEVERITY, key=SEVERITY.get))
    parser.add_argument("--schemas", type=Path, default=policies.SCHEMAS)
    args = parser.parse_args(argv)
    try:
        loaded = policies.load(args.policy.parent, args.schemas)
    except policies.PolicyError as exc:
        for problem in exc.problems:
            print(problem)
        return 1
    if "escalation" not in loaded or args.policy.name != "escalation.yaml":
        print(f"{args.policy}: expected an escalation.yaml")
        return 1
    changed = [line.strip() for line in args.changed.read_text(encoding="utf-8").splitlines() if line.strip()]
    verdict, reason = classify(loaded["escalation"], changed, args.severity)
    print(f"{verdict}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

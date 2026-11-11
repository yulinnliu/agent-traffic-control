#!/usr/bin/env python3
"""Diagnose local Agent Traffic Control runtime readiness without changing state."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import time

import yaml
from atc_testkit import locations, stages, workers as host_workers

import checkout_guard
import cli_conventions
import friction
import onboard_project
import validate_workspace as validator

try:
    import managed_worktree
except (ImportError, SyntaxError) as exc:
    # Doctor must still start and report the gap rather than fail at import time.
    managed_worktree = None
    WORKTREE_IMPORT_ERROR = str(exc)
else:
    WORKTREE_IMPORT_ERROR = ""

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def checkout_check(checkout: Path) -> Check:
    """The canonical checkout guard's conditions (`checkout_guard`), as a problem doctor lists."""
    current = checkout_guard.state(checkout)
    if not current.canonical:
        return Check("canonical checkout", "READY", f"{checkout} is not the canonical checkout")
    if current.unreadable:
        return Check("canonical checkout", "ERROR", f"{checkout} Git state cannot be read ({current.unreadable}); every other atc command refuses")
    if current.off_main:
        return Check("canonical checkout", "ERROR", f"{checkout_guard.describe(checkout, current)}; every other atc command refuses")
    if current.dirty:
        return Check("canonical checkout", "ERROR", f"{checkout} has tracked modifications; atc runs them")
    return Check("canonical checkout", "READY", f"{checkout} is on main and clean")


def worker_pool_checks(environ: dict[str, str]) -> list[Check]:
    """The host worker pool (docs/TESTKIT.md § Host worker budget): its tokens, the runs holding them and the runs
    waiting; it reads the pool and removes nothing."""
    try:
        if environ.get("CI"):
            return [Check("worker pool", "READY", "CI is set: runs take the CPU and memory ceiling, no host pool")]
        size = stages.pool_size(stages.host_table(environ))
        if size == 0:
            return [Check("worker pool", "READY", "off (test_worker_pool = 0 in ~/.atc/config.toml)")]
        directory = stages.pool_directory(environ)
        if not (directory / host_workers.POOL_FILE).exists():
            return [Check("worker pool", "READY", f"{size} tokens, no run has used it yet ({directory})")]
        status = host_workers.Pool(directory, size).status(prune=False)
    except locations.Refusal as refusal:
        return [Check("worker pool", "ERROR", f"{refusal.token} {refusal.detail}")]
    now = time.time()
    checks = [Check("worker pool", "READY", f"{status.free} of {status.size} tokens free, {len(status.runs)} run"
                                            f"{'' if len(status.runs) == 1 else 's'} active, {len(status.waiters)} "
                                            f"waiting ({directory})")]
    checks += [Check("worker pool run", "READY", row.describe(now)) for row in status.runs]
    checks += [Check("worker pool waiter", "READY", row.describe(now)) for row in status.waiters]
    return checks


def diagnose(root: Path = ROOT, environ: dict[str, str] | None = None, checkout: Path = ROOT) -> list[Check]:
    environ = environ if environ is not None else dict(os.environ)
    checks = [Check("workspace root", "READY", str(root.resolve())), checkout_check(checkout)]

    env_problems = validator.workspace_env_problems(root, environ)
    if "ATC_ROOT" not in env_problems:
        checks.append(Check("ATC_ROOT", "READY", "unset (optional override; tools locate this checkout themselves)"))
    elif env_problems["ATC_ROOT"]:
        checks.append(Check("ATC_ROOT", "ERROR", env_problems["ATC_ROOT"]))
    else:
        checks.append(Check("ATC_ROOT", "READY", f"{environ['ATC_ROOT']} (same checkout)"))
    atc_path = shutil.which("atc", path=environ.get("PATH", os.environ.get("PATH", "")))
    checks.append(
        Check(
            "atc command",
            "READY" if atc_path else "ERROR",
            atc_path or f"not on PATH; skills call it: uv tool install -e \"{root / 'adapters' / 'python'}\"",
        )
    )
    checks += worker_pool_checks(environ)

    workspace_file = root / "workspace.yaml"
    validator.FAILURES.clear()
    configured_root, projects = validator.load_and_check_workspace_yaml(workspace_file)
    if validator.FAILURES:
        checks.append(Check("workspace.yaml", "ERROR", "; ".join(validator.FAILURES)))
        projects = {}
    elif configured_root and Path(configured_root).expanduser().resolve() != root.resolve():
        checks.append(Check("workspace.yaml", "ERROR", "workspace_root does not match checkout"))
    else:
        checks.append(Check("workspace.yaml", "READY", f"{len(projects)} registered projects"))

    uv_path = shutil.which("uv")
    checks.append(Check("uv", "READY" if uv_path else "ERROR", uv_path or "not found on PATH"))
    python_ok = sys.version_info[:2] == (3, 12)
    checks.append(
        Check(
            "Python",
            "READY" if python_ok else "ERROR",
            f"{sys.version.split()[0]} at {sys.executable}; tooling requires 3.12",
        )
    )
    checks.append(Check("PyYAML", "READY", f"{yaml.__version__}"))

    validator.FAILURES.clear()
    original_root = validator.ROOT
    try:
        validator.ROOT = root
        canonical = validator.load_canonical_skills()
        validator.check_adapter_tree(root, canonical, label="workspace root", required=set(canonical))
    finally:
        validator.ROOT = original_root
    if validator.FAILURES:
        checks.append(Check("workspace-managed adapters", "ERROR", f"{len(validator.FAILURES)} problem(s)"))
    else:
        adapters = sum(len(validator.adapter_dirs(fm)) for fm in canonical.values())
        checks.append(Check("workspace-managed adapters", "READY", f"{adapters} adapters current"))

    if managed_worktree is None:
        checks.append(Check("atc-worktree", "ERROR", f"the shared capability could not be loaded: {WORKTREE_IMPORT_ERROR}"))
    else:
        status, detail = managed_worktree.readiness(root, environ, os.path.expanduser("~"))
        checks.append(Check("atc-worktree", status, detail))

    for name, entry in projects.items():
        for field in ("repo", "worktrees", "state"):
            path = (root / entry[field]).resolve()
            exists = path.is_dir()
            if field == "repo":
                status = "READY" if exists else "ERROR"
            else:
                status = "READY" if exists else "NOT_INITIALIZED"
            checks.append(Check(f"projects.{name}.{field}", status, str(path)))
        project_path = (root / entry["repo"]).resolve()
        if not project_path.is_dir():
            continue
        import testkit_contract
        if testkit_contract.record(project_path) is not None:
            problems = testkit_contract.problems(project_path, hooks=True)
            checks.append(Check(f"projects.{name}.testkit", "ERROR" if problems else "READY",
                                '; '.join(problems) if problems else 'version, artifact, hook and CI contract current'))
        try:
            plan = onboard_project.build_plan(project_path, root)
        except ValueError as exc:
            checks.append(Check(f"projects.{name}.integration", "ERROR", str(exc)))
            continue
        collisions = [item for item in plan if item.action == "collision"]
        drift = [item for item in plan if item.action in ("create", "update")]
        if collisions:
            checks.append(
                Check(
                    f"projects.{name}.integration",
                    "ERROR",
                    f"{len(collisions)} ownership/policy collisions",
                )
            )
        elif drift:
            checks.append(
                Check(f"projects.{name}.integration", "ERROR", f"{len(drift)} missing or drifted items")
            )
        else:
            managed = sum(1 for item in plan if "managed adapter" in item.detail)
            checks.append(Check(f"projects.{name}.integration", "READY", f"{managed} managed adapters current"))

    try:
        incidents, resolutions = friction.project_state(friction.load_records(root / "state/workspace-friction/events.jsonl"))
        open_count = sum(event_id not in resolutions for event_id in incidents)
        checks.append(Check("open friction incidents", "READY", str(open_count)))
    except ValueError as exc:
        checks.append(Check("friction event log", "ERROR", str(exc)))
    return checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc doctor", allow_abbrev=False, **cli_conventions.form(
        "Diagnose local Agent Traffic Control runtime readiness; changes nothing.",
        exit="0 every check READY or NOT_INITIALIZED; 1 a check reports ERROR; 2 usage error",
        see="README.md § Commands"))
    cli_conventions.add_json(parser)
    return parser


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = build_parser().parse_args(argv)
    checks = diagnose(root)
    errors = sum(check.status == "ERROR" for check in checks)
    not_initialized = sum(check.status == "NOT_INITIALIZED" for check in checks)
    code = cli_conventions.STOP if errors else cli_conventions.OK
    if args.json:
        print(json.dumps({"verdict": "NOT_READY" if errors else "READY", "errors": errors,
                          "not_initialized": not_initialized,
                          "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks]}, indent=2))
        return code
    for check in checks:
        print(f"{check.status:15} {check.name}: {check.detail}")
    print(f"\nDoctor: {errors} error(s), {not_initialized} not initialized.")
    return code


if __name__ == "__main__":
    sys.exit(main())

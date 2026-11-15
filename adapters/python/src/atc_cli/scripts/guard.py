#!/usr/bin/env python3
"""`atc guard`: check the PreToolUse hook (its harness entries, its baseline, whether its table composes), or, the
Owner's, `--install` it on this host or `--reset` the table to the current inputs (docs/ENFORCEMENT.md
§ PreToolUse hook).

The hook composes its own table from the workspace map and each repository's `main`; the generator's other
outputs are `atc-maintain guard`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cli_conventions
import generate_enforcement as ge
import hook_state

ROOT = Path(__file__).resolve().parents[5]
INSTALL_HINT = "the Owner runs atc guard --install from their own terminal"
RESET_HINT = "the Owner runs atc guard --reset from their own terminal"
HARNESSES = ("claude", "codex")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atc guard", allow_abbrev=False, **cli_conventions.form(
        "Check the PreToolUse hook: its harness entries, its baseline, and whether its table composes.",
        "The hook composes its table from the workspace map and each repository's main and caches it; a\n"
        "composition only tightens the baseline. --install (once per host) writes the baseline and the hook\n"
        "entries of ~/.claude/settings.json and ~/.codex/hooks.json; --reset replaces the baseline with the\n"
        "current inputs after listing what it drops: the one way to loosen the table. Both are the Owner's.",
        exit="0 current, installed or reset; 1 STOP (an entry, the baseline, a table that does not compose, "
             "a refused or declined reset); 2 ERROR",
        see="docs/ENFORCEMENT.md § PreToolUse hook"))
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--install", action="store_true",
                        help="write the baseline and the hook entries (the Owner's, once per host)")
    action.add_argument("--reset", action="store_true",
                        help="replace the baseline with the current inputs, after confirmation (the Owner's)")
    parser.add_argument("--yes", action="store_true", help="with --reset: write without asking")
    cli_conventions.add_json(parser)
    return parser


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


def _git_lines(repo: Path, args: list[str], home: Path) -> list[str]:
    found = hook_state.git(repo, args, home)
    return found.stdout.decode("utf-8", "replace").splitlines() if found.returncode == 0 else []


def inspect_sources(sources: list[dict], home: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Refusals (replace refs, alternates, a `main` diverged from its upstream) and the lines naming each Git
    input against its upstream, `refs/remotes/origin/main` as this checkout last fetched it: informative only,
    since an agent can move that ref too."""
    refusals: list[tuple[str, str]] = []
    lines: list[str] = []
    for source in sources or []:
        if source["kind"] != "git":
            lines.append(f"  {source['label']} {source['path']} (files)")
            continue
        repo = Path(source["path"])
        main = hook_state.main_sha(repo, home) or "?"
        if _git_lines(repo, ["for-each-ref", "--format=%(refname)", "refs/replace"], home):
            refusals.append(("REPLACE_REFS_PRESENT", f"{source['label']} {repo} has refs/replace/*; remove them"))
        common = _git_lines(repo, ["rev-parse", "--git-common-dir"], home)
        alternates = (repo / common[0] if common else repo / ".git") / "objects" / "info" / "alternates"
        if alternates.is_file() and alternates.read_text(encoding="utf-8", errors="replace").strip():
            refusals.append(("ALTERNATES_PRESENT", f"{source['label']} {repo} has {alternates}; remove it"))
        upstream = _git_lines(repo, ["rev-parse", "--verify", "-q", "refs/remotes/origin/main^{commit}"], home)
        if not upstream:
            lines.append(f"  {source['label']} {repo}@{main} (no upstream to compare)")
            continue
        if hook_state.git(repo, ["merge-base", "--is-ancestor", upstream[0], "refs/heads/main"], home).returncode:
            refusals.append(("MAIN_DIVERGED", f"{source['label']} {repo}: main {main[:12]} does not descend from "
                                              f"origin/main {upstream[0][:12]}"))
            continue
        ahead = _git_lines(repo, ["rev-list", "--count", f"{upstream[0]}..refs/heads/main"], home)
        count = int(ahead[0]) if ahead else 0
        lines.append(f"  {source['label']} {repo}@{main}" + (f" ({count} commits ahead of origin/main)" if count
                                                            else " (= origin/main)"))
        lines += [f"    changes policies/: {line}" for line in
                  _git_lines(repo, ["log", "--format=%h %s", f"{upstream[0]}..refs/heads/main", "--", "policies/"],
                             home)]
    return refusals, lines


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


def skew_hint(root: Path) -> str | None:
    """The hint a table that does not compose carries when the installed atc is older than main (doctor's root
    line)."""
    import doctor
    try:
        state = doctor.skew_check(root)[1]
    except Exception:  # noqa: BLE001 - the hint is extra; the refusal stands without it
        return None
    return "the installed atc is older than main; installing the latest release may fix it" if state else None


def check(workspace: Path, home: Path, atc_home: Path, as_json: bool, root: Path | None = None) -> int:
    found = report(workspace, home, atc_home)
    refusal = stop_of(found)
    if refusal is not None and not found["table"]["composes"] and refusal.token == found["table"]["token"]:
        hint = skew_hint(root or workspace.parent)
        if hint:
            refusal.detail = f"{refusal.detail}; {hint}"
    if refusal is not None:
        return cli_conventions.refuse(refusal, as_json, **found)
    if as_json:
        print(json.dumps({"verdict": "CURRENT", **found}, indent=2))
    else:
        print("CURRENT guard")
        for line in warnings(found):
            print(f"  WARN {line}")
    return cli_conventions.OK


def _refuse(token: str, detail: str, hint: str, as_json: bool) -> int:
    return cli_conventions.refuse(cli_conventions.Refusal(token, detail, hint), as_json)


def _composed(workspace: Path, home: Path, atc_home: Path, as_json: bool, command: str) -> dict | int:
    result = compose(workspace, home, atc_home)
    if "failure" in result:
        return _refuse(result["failure"]["token"], result["failure"]["detail"],
                       f"fix it, then run atc guard {command} again", as_json)
    refusals, lines = inspect_sources(result.get("sources"), home)
    if refusals:
        return _refuse(refusals[0][0], "; ".join(detail for _, detail in refusals),
                       f"fix them, then run atc guard {command} again", as_json)
    result["lines"] = lines
    return result


def install(workspace: Path, home: Path, atc_home: Path, as_json: bool) -> int:
    result = _composed(workspace, home, atc_home, as_json, "--install")
    if isinstance(result, int):
        return result
    paths = hook_state.state_paths(atc_home, workspace)
    table = result["table"]
    for earlier in (paths["baseline"], paths["legacy"]):
        before = _table_of(earlier)
        if before is not None:
            table = hook_state.ratchet(before, table)[0]
    lock = hook_state.Lock(paths["lock"])
    try:
        lock.acquire(float("inf"))
        hook_state.write_json(paths["baseline"], {"version": hook_state.FORMAT, "table": table,
                                                  "inputs": result.get("inputs"), "written_by": "install",
                                                  "at": hook_state._now()})
        hook_state.remove(paths["cache"])
    finally:
        lock.release()
    installed = []
    wanted = groups(workspace, home, atc_home)
    for harness, path in settings_files(home).items():
        try:
            ge.install_hook(path, wanted[harness])
        except (OSError, ValueError) as exc:
            return cli_conventions.refuse(cli_conventions.Refusal(
                "INSTALL_FAILED", f"{path}: {exc}", "fix the settings file, then run it again",
                cli_conventions.ERROR), as_json)
        installed.append(str(path))
    if as_json:
        print(json.dumps({"verdict": "INSTALLED", "baseline": str(paths["baseline"]), "installed": installed,
                          "inputs": result.get("inputs")}, indent=2))
    else:
        print(f"INSTALLED guard (baseline and {len(installed)} hook entries; Codex asks to trust the hook once in "
              "/hooks)")
        print(*result["lines"], sep="\n")
    return cli_conventions.OK


def reset(workspace: Path, home: Path, atc_home: Path, as_json: bool, yes: bool, answer=input) -> int:
    for harness, state in entries(workspace, home, atc_home).items():
        if state != "current":
            return _refuse(f"ENTRY_{state.upper()}", f"the {harness} hook entry is {state}; a reset with it would "
                           "leave its static table behind", "the Owner runs atc guard --install first", as_json)
    result = _composed(workspace, home, atc_home, as_json, "--reset")
    if isinstance(result, int):
        return result
    paths = hook_state.state_paths(atc_home, workspace)
    fresh = result["table"]
    try:
        before, against = hook_state.read_baseline(paths["baseline"])["table"], "the baseline"
    except hook_state.Failure:
        before, against = _table_of(paths["cache"]), "no baseline: compared with the cache"
        if before is None:
            before, against = _table_of(paths["legacy"]), "no baseline: compared with the legacy table"
        if before is None:
            against = "no baseline: compared with nothing"
    dropped = hook_state.ratchet(before, fresh)[1]["rules"] if before else []
    dropped_roots = [r for r in (before or {}).get("write_roots", []) if r not in fresh["write_roots"]]
    added_roots = [r for r in fresh["write_roots"] if before and r not in before["write_roots"]]
    plan = {"inputs": result.get("inputs"), "against": against, "drops": dropped, "drops_write_roots": dropped_roots,
            "adds_write_roots": added_roots}
    if as_json and not yes:
        print(json.dumps({"verdict": "PLANNED", **plan}, indent=2))
        return _refuse("DECLINED", "nothing was written", "pass --yes to write it", False)
    if not as_json:
        print("RESET plan for the PreToolUse table", *result["lines"], f"  {against}",
              *(f"  drop rule {rule}" for rule in dropped), *(f"  drop write root {r}" for r in dropped_roots),
              *(f"  add write root {r}" for r in added_roots), sep="\n")
        if not yes:
            try:
                reply = answer("reset the table to these inputs? [y/N] ")
            except EOFError:
                reply = ""
            if reply.strip().lower() not in ("y", "yes"):
                return _refuse("DECLINED", "the table was not reset", "run atc guard --reset again and answer y",
                               False)
    lock = hook_state.Lock(paths["lock"])
    try:
        lock.acquire(float("inf"))
        hook_state.write_json(paths["baseline"], {"version": hook_state.FORMAT, "table": fresh,
                                                  "inputs": result.get("inputs"), "written_by": "reset",
                                                  "at": hook_state._now()})
        hook_state.remove(paths["cache"])
        hook_state.remove(paths["legacy"])
    finally:
        lock.release()
    if as_json:
        print(json.dumps({"verdict": "RESET", **plan}, indent=2))
    else:
        print(f"RESET guard ({len(dropped)} rules dropped)")
    return cli_conventions.OK


def main(argv: list[str] | None = None, root: Path = ROOT, home: Path | None = None, answer=input) -> int:
    args = build_parser().parse_args(argv)
    home = Path(home) if home is not None else ge.default_home()
    atc_home = ge.default_atc_home(home)
    workspace = (root / "workspace.yaml").absolute()
    if not workspace.is_file():
        return cli_conventions.refuse(cli_conventions.Refusal("WORKSPACE_MISSING", f"{workspace} is missing",
                                                              "atc init"), args.json)
    if args.yes and not args.reset:
        return cli_conventions.refuse(cli_conventions.Refusal("USAGE", "--yes goes with --reset", "drop --yes",
                                                              cli_conventions.ERROR), args.json)
    if args.install:
        return install(workspace, home, atc_home, args.json)
    if args.reset:
        return reset(workspace, home, atc_home, args.json, args.yes, answer)
    return check(workspace, home, atc_home, args.json, root)


if __name__ == "__main__":
    sys.exit(main())

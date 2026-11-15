"""The PreToolUse hook composes its own table from each repository's `main`, keeps a baseline that only tightens,
and fails closed; `atc guard` installs, checks and resets it (docs/ENFORCEMENT.md § PreToolUse hook).

Each test names the guard of docs/design/ATC-0053.md § 9 it pins (G1-G56, G75-G80).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import doctor
import generate_enforcement as ge
import guard
import hook_state
import pretooluse_hook as hook

pytestmark = pytest.mark.integration

ROOT_POLICY = """version: 1
capabilities:
  deny: []
filesystem:
  deny:
    - glob: "**/.env"
    - glob: "~/.ssh/**"
commands:
  deny:
    - id: atc-integrate
      argv_prefix: [atc, integrate]
      unless_args: [--check]
    - id: git-reset-hard
      argv_prefix: [git, reset, --hard]
"""

PROJECT_POLICY = """version: 1
capabilities:
  deny: []
filesystem:
  deny:
    - glob: "authorizations/**"
      reason: Owner-signed.
    - glob: "~/.ssh/**"
commands:
  deny:
    - id: git-reset-hard
      argv_prefix: [git, reset, --hard]
    - id: gh-secret
      argv_prefix: [gh, secret]
"""

# The retired cd guard's file name, joined at run time so the test selector reads no edge to that module.
CD_GUARD = "_".join(("worktree", "cd", "guard.py"))
CWD = "C:\\hami\\projects\\x"


def git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c",
                           "core.autocrlf=false", "-C", str(repo), *args], capture_output=True, text=True, env=env,
                          check=True)
    return done.stdout.strip()


def commit(repo: Path, files: dict[str, str | None], message: str = "change") -> str:
    for name, text in files.items():
        path = repo / name
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def new_repo(path: Path, files: dict[str, str | None] | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    commit(path, files or {}, "start")
    return path


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("USERPROFILE", str(path))
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setattr(ge, "tool_environment", lambda: path / "uv-tools" / "agent-traffic-control")
    return path


MAP = ("version: 1\nworkspace_root: {root}\n"
       "agent_write_roots:\n  - ~/.atc\n  - \"%SCRATCH_ROOT%\"\n"
       "projects:\n"
       "  p:\n    repo: projects/p\n    worktrees: worktrees/p\n    state: state/p-data\n"
       "  q:\n    repo: projects/q\n    worktrees: worktrees/q\n    state: state/q-data\n")


@pytest.fixture
def workspace(tmp_path, home) -> Path:
    root = tmp_path / "ws"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "hard-deny.yaml").write_text(ROOT_POLICY, encoding="utf-8")
    new_repo(root / "projects" / "p", {"policies/hard-deny.yaml": PROJECT_POLICY})
    new_repo(root / "projects" / "q")
    path = root / "workspace.yaml"
    path.write_text(MAP.format(root=root.as_posix()), encoding="utf-8")
    return path


def atc_home(home: Path) -> Path:
    return home / ".atc"


def compose(workspace: Path, home: Path) -> dict:
    return json.loads(ge.fresh_table(workspace, home, atc_home(home)))


def paths(workspace: Path, home: Path) -> dict[str, Path]:
    return hook_state.state_paths(atc_home(home), workspace)


def norm(path: Path) -> str:
    return ge._norm(path, path)


def payload(command: str, tool: str = "Bash") -> dict:
    return {"tool_name": tool, "tool_input": {"command": command}, "cwd": CWD}


def table_now(workspace: Path, home: Path, seconds: float = 30.0) -> dict:
    return hook_state.table_for(workspace, home, atc_home(home), time.monotonic() + seconds)


def verdict(workspace: Path, home: Path, command: str, harness: str = "claude") -> str | None:
    """The decision of a hook call through the composed (cached) table, in this process."""
    try:
        table = table_now(workspace, home)
    except hook_state.Failure as failure:
        return hook_state.deny_reason(failure)
    return hook.decide(payload(command), table, harness)


def engine(workspace: Path, home: Path, command: str, harness: str = "claude", env: dict | None = None,
           extra: list[str] | None = None, tmp: Path | None = None) -> str | None:
    """The hook as the harness runs it: its deny reason, or None."""
    args = [sys.executable, hook.__file__, "--harness", harness]
    args += extra if extra is not None else ["--workspace", str(workspace), "--home", str(home),
                                             "--atc-home", str(atc_home(home))]
    ran = subprocess.run(args, input=json.dumps(payload(command)), capture_output=True, text=True, encoding="utf-8",
                         env={**(env if env is not None else os.environ), "PYTHONIOENCODING": "utf-8"}, timeout=60)
    assert ran.returncode == 0, ran.stderr
    return json.loads(ran.stdout)["hookSpecificOutput"]["permissionDecisionReason"] if ran.stdout else None


def install(workspace: Path, home: Path, *args: str) -> int:
    return guard.main(["--install", *args], root=workspace.parent, home=home)


def snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.glob("*")) if p.is_file()} if directory.is_dir() else {}


# Composition: what reaches the table


def test_the_table_composes_the_workspace_policy_first_then_each_project(workspace, home):
    table = compose(workspace, home)
    root = workspace.parent
    assert [(c["id"], c["source"]) for c in table["commands"]] == [
        ("atc-integrate", "policies/hard-deny.yaml"),
        ("git-reset-hard", "policies/hard-deny.yaml"),
        ("gh-secret", "projects.p"),
    ]
    rules = {(r["source"], r["glob"]): r for r in table["filesystem"]}
    env = rules[("policies/hard-deny.yaml", "**/.env")]
    assert env["scope"] == [{"base": norm(root), "depth": 0}, {"base": norm(root / "worktrees" / "root"), "depth": 1}]
    auth = rules[("projects.p", "authorizations/**")]
    assert auth["scope"] == [{"base": norm(root / "projects" / "p"), "depth": 0},
                             {"base": norm(root / "worktrees" / "p"), "depth": 1}]
    ssh = [r for r in table["filesystem"] if r["glob"].endswith("/.ssh/**")]
    assert len(ssh) == 1 and ssh[0]["glob"] == norm(home / ".ssh").replace("\\", "/") + "/**"
    states = [r for r in table["filesystem"] if r["id"].startswith("state.")]
    assert [(r["id"], r["modes"]) for r in states] == [("state.p", ["edit", "write"]), ("state.q", ["edit", "write"])]
    assert table["worktrees"] == [norm(root / "worktrees")]
    assert table["write_roots"] == [norm(root), norm(home / ".atc"), norm(home.parent / "scratch")]


def test_g38_g39_g40_the_table_protects_the_map_the_file_policy_the_state_and_the_tool_environment(workspace, home):
    table = compose(workspace, home)
    root = workspace.parent
    for path in (atc_home(home) / "enforcement", workspace, root / "policies",
                 home / "uv-tools" / "agent-traffic-control", ge.ENGINE_DIR / "pretooluse_hook.py",
                 ge.ENGINE_DIR / "hook_state.py", ge.ENGINE_DIR / "compose_child.py"):
        assert norm(path) in table["protected"]
    for command in (f'Set-Content "{workspace}" x', f'echo x > "{workspace}"',
                    f'Set-Content "{root / "policies" / "hard-deny.yaml"}" x'):
        tool = "PowerShell" if command.startswith("Set-") else "Bash"
        assert hook.decide(payload(command, tool), table, "claude") is not None
    edit = {"tool_name": "Edit", "tool_input": {"file_path": str(workspace)}, "cwd": CWD}
    assert hook.decide(edit, table, "claude") is not None


def test_g40_the_tool_environment_is_a_prefix_holding_uv_receipt(tmp_path, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert ge.tool_environment() is None
    (tmp_path / "uv-receipt.toml").write_text("", encoding="utf-8")
    assert ge.tool_environment() == tmp_path


def test_g1_only_main_objects_count(workspace, home):
    p = workspace.parent / "projects" / "p"
    commit(p, {"policies/hard-deny.yaml": PROJECT_POLICY + "    - id: gh-auth\n      argv_prefix: [gh, auth]\n"})
    (p / "policies" / "hard-deny.yaml").unlink()
    assert "gh-auth" in [c["id"] for c in compose(workspace, home)["commands"]]


def test_g2_a_topic_branch_does_not_count(workspace, home):
    p = workspace.parent / "projects" / "p"
    before = compose(workspace, home)
    git(p, "checkout", "-q", "-b", "topic")
    commit(p, {"policies/hard-deny.yaml": PROJECT_POLICY.replace("argv_prefix: [gh, secret]", "argv_prefix: [gh, x]")})
    assert compose(workspace, home) == before


def test_g36_2a_reads_the_governance_checkout_at_main_and_2b_the_file(workspace, home):
    root = workspace.parent
    gov = new_repo(root / "gov", {"policies/hard-deny.yaml": ROOT_POLICY.replace("[git, reset, --hard]",
                                                                                  "[git, clean]")})
    (gov / "policies" / "hard-deny.yaml").write_text(ROOT_POLICY, encoding="utf-8")  # uncommitted: ignored
    workspace.write_text(workspace.read_text(encoding="utf-8") + "governance_checkout: gov\n", encoding="utf-8")
    root_rules = [c["argv_prefix"] for c in compose(workspace, home)["commands"]
                  if c["source"] == "policies/hard-deny.yaml"]
    assert root_rules == [["atc", "integrate"], ["git", "clean"]]
    assert norm(root / "policies") not in compose(workspace, home)["protected"]


def test_g35_a_governance_checkout_without_the_key_fails_closed(workspace, home):
    root = workspace.parent
    (root / "atc-manifest.yaml").write_text("version: 1\n", encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    result = ge.compose_result(workspace, home, atc_home(home))
    assert result["failure"]["token"] == "GOVERNANCE_CHECKOUT_UNSET"


def test_g34_a_project_without_main_fails_closed(workspace, home):
    p = workspace.parent / "projects" / "p"
    git(p, "checkout", "-q", "-b", "other")
    git(p, "branch", "-q", "-D", "main")
    result = ge.compose_result(workspace, home, atc_home(home))
    assert result["failure"]["token"] == "MAIN_UNREADABLE" and "projects.p" in result["failure"]["detail"]


def test_g28_a_policy_over_the_cap_fails_closed(workspace, home):
    p = workspace.parent / "projects" / "p"
    commit(p, {"policies/hard-deny.yaml": PROJECT_POLICY + "#" * (300 * 1024) + "\n"})
    assert ge.compose_result(workspace, home, atc_home(home))["failure"]["token"] == "POLICY_TOO_LARGE"


def test_g29_a_policy_with_an_alias_fails_closed(workspace, home):
    p = workspace.parent / "projects" / "p"
    commit(p, {"policies/hard-deny.yaml": PROJECT_POLICY.replace("argv_prefix: [gh, secret]",
                                                                 "argv_prefix: &a [gh, secret]\n    - id: y\n"
                                                                 "      argv_prefix: *a")})
    failure = ge.compose_result(workspace, home, atc_home(home))["failure"]
    assert failure["token"] == "POLICY_INVALID" and "alias" in failure["detail"]


def test_g8_policies_are_read_at_the_sha_the_key_names(workspace, home, monkeypatch):
    p = workspace.parent / "projects" / "p"
    real = hook_state.make_key
    moved = []

    def make_key(*args, **kwargs):
        key = real(*args, **kwargs)
        if args[2] is not None and not moved:
            moved.append(commit(p, {"policies/hard-deny.yaml": PROJECT_POLICY.replace("[gh, secret]", "[gh, moved]")}))
        return key
    monkeypatch.setattr(hook_state, "make_key", make_key)
    result = ge.compose_result(workspace, home, atc_home(home))
    assert moved and ["gh", "secret"] in [c["argv_prefix"] for c in result["table"]["commands"]]


def test_g9_g12_main_is_read_from_a_gitdir_file_and_from_packed_refs(tmp_path, home):
    plain = new_repo(tmp_path / "plain")
    sha = git(plain, "rev-parse", "main")
    git(plain, "pack-refs", "--all")
    assert not (plain / ".git" / "refs" / "heads" / "main").exists()
    assert hook_state.main_sha(plain, home) == sha
    linked = tmp_path / "linked"
    linked.mkdir()
    git(linked, "init", "-q", "-b", "main", "--separate-git-dir", str(tmp_path / "linked.git"))
    commit(linked, {}, "x")
    assert (linked / ".git").is_file()
    assert hook_state.main_sha(linked, home) == git(linked, "rev-parse", "main")


def test_g10_a_declared_ref_storage_reads_through_git(tmp_path, home, monkeypatch):
    repo = new_repo(tmp_path / "r")
    with open(repo / ".git" / "config", "a", encoding="utf-8") as config:
        config.write("[extensions]\n\trefStorage = files\n")
    calls = []
    real = hook_state.git
    monkeypatch.setattr(hook_state, "git", lambda *a, **k: calls.append(a) or real(*a, **k))
    hook_state.main_sha(repo, home)
    assert calls


def test_g11_a_symbolic_main_reads_through_git(tmp_path, home):
    repo = new_repo(tmp_path / "r")
    other = commit(repo, {"a": "1"})
    git(repo, "branch", "-q", "other")
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    (repo / ".git" / "refs" / "heads" / "main").write_text("ref: refs/heads/other\n", encoding="ascii")
    assert hook_state.main_sha(repo, home) == other


def test_g13_replace_refs_are_not_applied(workspace, home):
    p = workspace.parent / "projects" / "p"
    original = git(p, "rev-parse", "main:policies/hard-deny.yaml")
    other = commit(p, {"x.yaml": PROJECT_POLICY.replace("[gh, secret]", "[gh, replaced]")}, "other")
    git(p, "reset", "-q", "--hard", "HEAD~1")
    git(p, "replace", original, git(p, "rev-parse", f"{other}:x.yaml"))
    assert ["gh", "secret"] in [c["argv_prefix"] for c in compose(workspace, home)["commands"]]


def test_g14_the_callers_git_environment_does_not_reach_git(workspace, home, monkeypatch, tmp_path):
    decoy = new_repo(tmp_path / "decoy", {"policies/hard-deny.yaml": PROJECT_POLICY.replace("[gh, secret]",
                                                                                            "[gh, decoy]")})
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.bare'='true'")
    assert ["gh", "secret"] in [c["argv_prefix"] for c in compose(workspace, home)["commands"]]
    p = workspace.parent / "projects" / "p"
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_CONFIG_PARAMETERS")
    assert hook_state.git(p, ["rev-parse", "main"], home).stdout.decode().strip() == git(p, "rev-parse", "main")


def test_g15_the_map_is_read_once(workspace, home, monkeypatch):
    reads = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self) or real(self))
    ge.compose_result(workspace, home, atc_home(home))
    assert [p for p in reads if p == workspace] == [workspace]


def test_g16_the_entry_names_the_homes_not_the_environment(workspace, home, tmp_path, monkeypatch):
    assert install(workspace, home) == 0
    elsewhere = tmp_path / "elsewhere"
    env = {**os.environ, "HOME": str(elsewhere), "USERPROFILE": str(elsewhere), "ATC_HOME": str(elsewhere)}
    assert engine(workspace, home, "git status", env=env) is None
    assert not elsewhere.exists() and paths(workspace, home)["cache"].is_file()
    for name in ("HOME", "USERPROFILE", "ATC_HOME"):
        monkeypatch.setenv(name, str(elsewhere))
    table = compose(workspace, home)
    assert [r["glob"] for r in table["filesystem"] if r["glob"].endswith("/.ssh/**")] == [
        norm(home / ".ssh").replace("\\", "/") + "/**"]
    assert norm(home / ".atc") in table["write_roots"]


# The key


def test_g3_g4_a_map_or_main_change_reaches_the_next_call(workspace, home):
    assert install(workspace, home) == 0
    assert verdict(workspace, home, "gh pr list") is None
    r = new_repo(workspace.parent / "projects" / "r", {"policies/hard-deny.yaml": PROJECT_POLICY.replace(
        "[gh, secret]", "[gh, pr]")})
    workspace.write_text(workspace.read_text(encoding="utf-8")
                         + "  r:\n    repo: projects/r\n    worktrees: worktrees/r\n    state: state/r-data\n",
                         encoding="utf-8")
    assert "gh-secret" in (verdict(workspace, home, "gh pr list") or "")
    commit(r, {}, "noop")
    commit(workspace.parent / "projects" / "q", {"policies/hard-deny.yaml": PROJECT_POLICY.replace(
        "[gh, secret]", "[gh, issue]").replace("id: gh-secret", "id: gh-issue")})
    assert "gh-issue" in (verdict(workspace, home, "gh issue list") or "")


def test_g5_the_workspace_policy_file_and_checkout_reach_the_next_call(workspace, home):
    assert install(workspace, home) == 0
    policy = workspace.parent / "policies" / "hard-deny.yaml"
    policy.write_text(ROOT_POLICY + "    - id: gh-api\n      argv_prefix: [gh, api]\n", encoding="utf-8")
    assert "gh-api" in (verdict(workspace, home, "gh api x") or "")
    gov = new_repo(workspace.parent / "gov", {"policies/hard-deny.yaml": ROOT_POLICY})
    workspace.write_text(workspace.read_text(encoding="utf-8") + "governance_checkout: gov\n", encoding="utf-8")
    verdict(workspace, home, "gh x")
    commit(gov, {"policies/hard-deny.yaml": ROOT_POLICY + "    - id: gh-run\n      argv_prefix: [gh, run]\n"})
    assert "gh-run" in (verdict(workspace, home, "gh run list") or "")


def test_g6_the_composer_files_are_in_the_key(workspace, home, monkeypatch, tmp_path):
    copy = tmp_path / "scripts"
    copy.mkdir()
    for name in hook_state.COMPOSER_FILES:
        (copy / name).write_bytes((hook_state.SCRIPTS / name).read_bytes())
    monkeypatch.setattr(hook_state, "SCRIPTS", copy)
    first = hook_state.make_key(workspace, "m", [], [], home)
    os.utime(copy / "policies.py", ns=(1, 1))
    assert hook_state.make_key(workspace, "m", [], [], home) != first


def test_g7_the_values_of_named_variables_are_in_the_key(workspace, home, monkeypatch, tmp_path):
    first = ge.compose_result(workspace, home, atc_home(home))["key"]
    assert "SCRATCH_ROOT" in first["env"]
    monkeypatch.setenv("SCRATCH_ROOT", str(tmp_path / "other"))
    assert ge.compose_result(workspace, home, atc_home(home))["key"] != first


def test_g22_a_cache_is_taken_only_under_an_equal_key(workspace, home):
    assert install(workspace, home) == 0
    table_now(workspace, home)
    cache = paths(workspace, home)["cache"]
    record = json.loads(cache.read_text(encoding="utf-8"))
    record["table"]["commands"] = []
    record["key"]["composer"] = "0" * 64
    cache.write_text(json.dumps(record), encoding="utf-8")
    assert "git-reset-hard" in (verdict(workspace, home, "git reset --hard") or "")
    cache.write_text("{not json", encoding="utf-8")
    assert "git-reset-hard" in (verdict(workspace, home, "git reset --hard") or "")


# The ratchet and the baseline


def test_g17_a_rule_deleted_on_main_still_denies(workspace, home):
    assert install(workspace, home) == 0
    commit(workspace.parent / "projects" / "p", {"policies/hard-deny.yaml": PROJECT_POLICY.replace(
        "    - id: gh-secret\n      argv_prefix: [gh, secret]\n", "")})
    assert "gh-secret" in (verdict(workspace, home, "gh secret list") or "")
    found = guard.report(workspace, home, atc_home(home))
    assert len(found["retained"]["rules"]) == 1 and "gh-secret" in found["retained"]["rules"][0]


def test_g18_a_write_root_added_to_the_map_waits_for_a_reset(workspace, home, tmp_path):
    assert install(workspace, home) == 0
    extra = tmp_path / "extra"
    workspace.write_text(workspace.read_text(encoding="utf-8").replace("  - ~/.atc\n", f"  - ~/.atc\n  - {extra.as_posix()}\n"),
                         encoding="utf-8")
    assert verdict(workspace, home, f'touch "{(extra / "f").as_posix()}"') is not None
    assert norm(extra) in guard.report(workspace, home, atc_home(home))["retained"]["write_roots"]
    lines = [c for c in doctor.hook_checks(workspace, home) if c.name == "pretooluse retained"]
    assert lines and norm(extra) in lines[0].detail


def test_g19_a_failure_leaves_the_baseline(workspace, home):
    assert install(workspace, home) == 0
    p = workspace.parent / "projects" / "p"
    commit(p, {"policies/hard-deny.yaml": "version: [\n"})
    assert "POLICY_INVALID" in (verdict(workspace, home, "git status") or "")
    commit(p, {"policies/hard-deny.yaml": PROJECT_POLICY.replace("    - id: gh-secret\n      argv_prefix: [gh, secret]\n",
                                                                 "")})
    assert "gh-secret" in (verdict(workspace, home, "gh secret list") or "")


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_g20_g21_a_missing_or_invalid_baseline_denies(workspace, home, harness):
    assert install(workspace, home) == 0
    baseline, cache = paths(workspace, home)["baseline"], paths(workspace, home)["cache"]
    baseline.unlink()
    hook_state.remove(cache)
    assert "BASELINE_MISSING" in (engine(workspace, home, "git status", harness) or "")
    assert not baseline.exists()
    baseline.write_text("{not json", encoding="utf-8")
    assert "BASELINE_INVALID" in (engine(workspace, home, "git status", harness) or "")
    assert baseline.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("change", ["version", "table-version", "table-field"])
def test_g20_a_baseline_of_another_version_or_with_a_broken_table_denies(workspace, home, change):
    assert install(workspace, home) == 0
    baseline, cache = paths(workspace, home)["baseline"], paths(workspace, home)["cache"]
    record = json.loads(baseline.read_text(encoding="utf-8"))
    if change == "version":
        record["version"] = hook_state.FORMAT + 1
    elif change == "table-version":
        record["table"]["version"] = 2
    else:
        del record["table"]["write_roots"]
    baseline.write_text(json.dumps(record), encoding="utf-8")
    hook_state.remove(cache)
    with pytest.raises(hook_state.Failure) as raised:
        hook_state.read_baseline(baseline)
    assert raised.value.token == "BASELINE_INVALID"
    assert "BASELINE_INVALID" in (engine(workspace, home, "git status") or "")
    assert json.loads(baseline.read_text(encoding="utf-8")) == record


def test_g30_another_map_never_touches_this_maps_state(workspace, home, tmp_path):
    assert install(workspace, home) == 0
    before = snapshot(paths(workspace, home)["dir"])
    other = tmp_path / "other.yaml"
    other.write_text(MAP.format(root=workspace.parent.as_posix()), encoding="utf-8")
    with pytest.raises(hook_state.Failure):
        table_now(other, home)
    after = snapshot(paths(workspace, home)["dir"])
    assert {k: v for k, v in after.items() if k in before} == before


def test_g31_a_first_install_takes_the_fresh_write_roots(workspace, home):
    assert install(workspace, home) == 0
    baseline = hook_state.read_baseline(paths(workspace, home)["baseline"])
    assert baseline["table"]["write_roots"] == compose(workspace, home)["write_roots"]
    assert baseline["written_by"] == "install"


def test_g32_a_legacy_table_seeds_the_baseline(workspace, home):
    legacy = paths(workspace, home)["legacy"]
    table = compose(workspace, home)
    table["commands"].append({"id": "gh-legacy", "argv_prefix": ["gh", "legacy"], "source": "old"})
    hook_state.write_json(legacy, table)
    assert install(workspace, home) == 0
    assert "gh-legacy" in (verdict(workspace, home, "gh legacy x") or "")


def test_g43_install_keeps_what_the_baseline_holds(workspace, home):
    assert install(workspace, home) == 0
    commit(workspace.parent / "projects" / "p", {"policies/hard-deny.yaml": PROJECT_POLICY.replace(
        "    - id: gh-secret\n      argv_prefix: [gh, secret]\n", "")})
    assert install(workspace, home) == 0
    assert "gh-secret" in (verdict(workspace, home, "gh secret list") or "")


def test_g76_install_deletes_the_cache(workspace, home):
    assert install(workspace, home) == 0
    table_now(workspace, home)
    assert paths(workspace, home)["cache"].is_file()
    assert install(workspace, home) == 0
    assert not paths(workspace, home)["cache"].exists()


# The miss path: lock, cache by key, deadline


class Counter:
    def __init__(self, monkeypatch, child: Path | None = None):
        self.calls = 0
        real = hook_state.run_child

        def run(workspace, home, atc_home, timeout, *rest):
            self.calls += 1
            return real(workspace, home, atc_home, timeout, child or hook_state.CHILD)
        monkeypatch.setattr(hook_state, "run_child", run)


def test_g23_a_failure_is_cached_by_its_key(workspace, home, monkeypatch):
    assert install(workspace, home) == 0
    p = workspace.parent / "projects" / "p"
    commit(p, {"policies/hard-deny.yaml": "version: [\n"})
    counter = Counter(monkeypatch)
    for _ in range(3):
        assert "POLICY_INVALID" in (verdict(workspace, home, "git status") or "")
    assert counter.calls == 1
    commit(p, {"policies/hard-deny.yaml": PROJECT_POLICY})
    assert verdict(workspace, home, "git status") is None and counter.calls == 2


def test_g24_the_cache_is_reread_under_the_lock(workspace, home, monkeypatch):
    assert install(workspace, home) == 0
    table_now(workspace, home)
    cache = paths(workspace, home)["cache"]
    written = cache.read_bytes()
    cache.unlink()
    real = hook_state.Lock.acquire

    def acquire(self, until):
        cache.write_bytes(written)  # another process finished while this one waited
        return real(self, until)
    monkeypatch.setattr(hook_state.Lock, "acquire", acquire)
    counter = Counter(monkeypatch)
    table_now(workspace, home)
    assert counter.calls == 0


def test_g25_a_busy_lock_denies_without_a_cached_failure(workspace, home):
    assert install(workspace, home) == 0
    held = hook_state.Lock(paths(workspace, home)["lock"])
    assert held.acquire(time.monotonic() + 5)
    try:
        with pytest.raises(hook_state.Failure) as raised:
            table_now(workspace, home, seconds=0.5)
        assert raised.value.token == "COMPOSE_BUSY"
    finally:
        held.release()
    assert not paths(workspace, home)["cache"].exists()
    assert verdict(workspace, home, "git status") is None


SLEEPER = """import json, sys, time
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
Path(request["key_out"]).write_text(json.dumps({"key": {"slept": True}, "sources": None, "env_names": None}))
time.sleep(60)
"""

SPAWNER = """import json, os, subprocess, sys, time
from pathlib import Path
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(__file__).with_name("pids.json").write_text(json.dumps([os.getpid(), grandchild.pid]))
time.sleep(60)
"""


def alive(pid: int) -> bool:
    """Whether process ``pid`` still runs; never signals it."""
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stat:
            return stat.read().rsplit(")", 1)[-1].split()[0] != "Z"
    except OSError:
        return True


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_g26_g27_the_deadline_denies_and_the_timeout_is_cached(workspace, home, monkeypatch, tmp_path, harness,
                                                                capsys):
    assert install(workspace, home) == 0
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text(SLEEPER, encoding="utf-8")
    counter = Counter(monkeypatch, sleeper)
    monkeypatch.setattr(hook_state, "DEADLINE", 2.5)
    monkeypatch.setattr(hook, "STARTED", time.monotonic())
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps(payload("git status")), encoding="utf-8")
    capsys.readouterr()
    argv = ["--harness", harness, "--workspace", str(workspace), "--home", str(home),
            "--atc-home", str(atc_home(home)), "--payload", str(payload_file)]
    started = time.monotonic()
    assert hook.main(argv) == 0
    assert time.monotonic() - started < 6
    reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "COMPOSE_TIMEOUT" in reason
    record = json.loads(paths(workspace, home)["cache"].read_text(encoding="utf-8"))
    assert record["failure"]["token"] == "COMPOSE_TIMEOUT"


KEYED_SLEEPER = """import json, sys, time
from pathlib import Path
sys.path.insert(0, {scripts!r})
import generate_enforcement as ge
request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
def sleep(*args, **kwargs):
    time.sleep(60)
ge._git_texts = ge._file_texts = sleep
ge.compose_result(Path(request["workspace"]), Path(request["home"]), Path(request["atc_home"]),
                  key_out=Path(request["key_out"]))
"""


def test_g27_a_timeout_under_the_current_key_denies_at_once(workspace, home, monkeypatch, tmp_path):
    assert install(workspace, home) == 0
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text(KEYED_SLEEPER.format(scripts=str(hook_state.SCRIPTS)), encoding="utf-8")
    counter = Counter(monkeypatch, sleeper)
    with pytest.raises(hook_state.Failure):
        table_now(workspace, home, seconds=3.0)
    record = json.loads(paths(workspace, home)["cache"].read_text(encoding="utf-8"))
    assert record["failure"]["token"] == "COMPOSE_TIMEOUT" and record["sources"]
    started = time.monotonic()
    with pytest.raises(hook_state.Failure) as raised:
        table_now(workspace, home, seconds=3.0)
    assert raised.value.token == "COMPOSE_TIMEOUT" and counter.calls == 1 and time.monotonic() - started < 1


def test_g27_a_timeout_before_the_child_wrote_its_key_is_not_cached(workspace, home, monkeypatch, tmp_path):
    """Its key would be the map's alone, which a fix on a repository's main never changes."""
    assert install(workspace, home) == 0
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text(SLEEPER.replace('{"key": {"slept": True}, "sources": None, "env_names": None}', "None"),
                       encoding="utf-8")
    real = hook_state.run_child
    counter = Counter(monkeypatch, sleeper)
    for _ in range(2):
        with pytest.raises(hook_state.Failure) as raised:
            table_now(workspace, home, seconds=2.0)
        assert raised.value.token == "COMPOSE_TIMEOUT"
    assert counter.calls == 2 and not paths(workspace, home)["cache"].exists()
    monkeypatch.setattr(hook_state, "run_child", real)
    assert verdict(workspace, home, "git status") is None


def test_g75_a_grandchild_holding_a_handle_does_not_hold_the_deadline(workspace, home, tmp_path):
    spawner = tmp_path / "spawner.py"
    spawner.write_text(SPAWNER, encoding="utf-8")
    started = time.monotonic()
    with pytest.raises(hook_state.Failure) as raised:
        hook_state.run_child(workspace, home, atc_home(home), 1.0, spawner)
    assert raised.value.token == "COMPOSE_TIMEOUT" and time.monotonic() - started < 5
    pids = json.loads((tmp_path / "pids.json").read_text(encoding="utf-8"))
    until = time.monotonic() + 5
    while any(alive(pid) for pid in pids) and time.monotonic() < until:
        time.sleep(0.1)
    assert not [pid for pid in pids if alive(pid)], "the timeout leaves the child or its grandchild running"


# Fail closed, as the harness runs it


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_g33_a_bad_policy_fails_closed_naming_where(workspace, home, harness):
    assert install(workspace, home) == 0
    p = workspace.parent / "projects" / "p"
    sha = commit(p, {"policies/hard-deny.yaml": PROJECT_POLICY.replace("argv_prefix: [gh, secret]",
                                                                       "argv_prefix: [gh, secret]\n      bogus: 1")})
    reason = engine(workspace, home, "git status", harness) or ""
    assert "POLICY_INVALID" in reason and f"{p}@{sha}" in reason and "atc doctor" in reason
    assert reason.startswith("Refused: the PreToolUse hook cannot compose its table:")


def test_g37_workspace_and_table_together_are_a_usage_deny(workspace, home, tmp_path):
    reason = engine(workspace, home, "git status", extra=["--workspace", str(workspace), "--home", str(home),
                                                          "--atc-home", str(atc_home(home)),
                                                          "--table", str(tmp_path / "t.json")])
    assert reason is not None and "USAGE" in reason


def test_g41_the_static_table_still_decides_and_a_bad_one_denies(tmp_path):
    table = tmp_path / "t.json"
    table.write_text(json.dumps({"version": 1, "commands": [{"id": "git-reset-hard",
                                                             "argv_prefix": ["git", "reset", "--hard"]}],
                                 "filesystem": [], "worktrees": [], "write_roots": ["c:\\hami"], "protected": []}),
                     encoding="utf-8")
    assert "git-reset-hard" in engine(tmp_path, tmp_path, "git reset --hard", extra=["--table", str(table)])
    assert engine(tmp_path, tmp_path, "git status", extra=["--table", str(table)]) is None
    table.write_text("{not json", encoding="utf-8")
    reason = engine(tmp_path, tmp_path, "git status", extra=["--table", str(table)])
    assert "could not decide" in reason and "atc doctor" in reason


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_helper_that_cannot_load_fails_closed(tmp_path, harness):
    scripts = Path(hook.__file__).parent
    for name in ge.ENGINE_FILES:
        (tmp_path / name).write_bytes((scripts / name).read_bytes())
    (tmp_path / "shell_words.py").write_text("raise ImportError('broken helper')\n", encoding="utf-8")
    ran = subprocess.run([sys.executable, str(tmp_path / ge.ENGINE), "--harness", harness],
                         input=json.dumps(payload("git status")), capture_output=True, text=True, encoding="utf-8",
                         timeout=60)
    reason = json.loads(ran.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "could not decide" in reason and "broken helper" in reason


def test_the_engine_allows_silently_through_the_composed_table(workspace, home):
    assert install(workspace, home) == 0
    assert engine(workspace, home, "git status") is None
    assert "gh-secret" in (engine(workspace, home, "gh secret list", "codex") or "")


# atc guard: install


def test_install_writes_the_baseline_and_replaces_the_retired_guards(workspace, home):
    claude = home / ".claude" / "settings.json"
    claude.parent.mkdir()
    claude.write_text("\ufeff" + json.dumps({"model": "x", "hooks": {"PreToolUse": [
        {"matcher": "Bash|PowerShell", "hooks": [{"type": "command", "command": f'python "C:/x/{CD_GUARD}"'}]},
        {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "python \"C:/h/write_scope_guard.py\""},
                                            {"type": "command", "command": "bash keep.sh"}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "atc hook --harness claude"}]},
    ], "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python ctx.py"}]}]}}), encoding="utf-8")
    assert install(workspace, home) == 0
    command = ge.engine_command(workspace, "claude", home, atc_home(home))
    assert command == (f'atc hook --harness claude --workspace "{workspace.absolute().as_posix()}" '
                       f'--home "{home.as_posix()}" --atc-home "{atc_home(home).as_posix()}"')
    settings = json.loads(claude.read_text(encoding="utf-8"))
    assert settings["model"] == "x" and settings["hooks"]["UserPromptSubmit"]
    assert settings["hooks"]["PreToolUse"] == [
        {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "bash keep.sh"}]},
        {"matcher": "Bash|PowerShell|Edit|Write|NotebookEdit", "hooks": [{
            "type": "command", "command": command, "timeout": 10, "onFailure": "block"}]},
    ]
    codex = json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert codex["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == ge.engine_command(workspace, "codex", home,
                                                                                       atc_home(home))
    assert paths(workspace, home)["baseline"].is_file()
    assert install(workspace, home) == 0
    assert json.loads(claude.read_text(encoding="utf-8")) == settings
    assert guard.main([], root=workspace.parent, home=home) == 0


def test_g42_install_composes_before_writing(workspace, home):
    commit(workspace.parent / "projects" / "p", {"policies/hard-deny.yaml": "version: [\n"})
    assert install(workspace, home) == 1
    assert not (home / ".claude" / "settings.json").exists() and not paths(workspace, home)["baseline"].exists()


def _no_writes(workspace, home):
    return snapshot(paths(workspace, home)["dir"]), [(home / f).exists() for f in (".claude/settings.json",
                                                                                     ".codex/hooks.json")]


@pytest.mark.parametrize("damage", ["replace", "alternates", "diverged"])
def test_g46_g47_g48_g80_install_and_reset_refuse_a_doubtful_main(workspace, home, tmp_path, damage, capsys):
    assert install(workspace, home) == 0
    p = workspace.parent / "projects" / "p"
    if damage == "replace":
        blob = git(p, "rev-parse", "main:policies/hard-deny.yaml")
        other = commit(p, {"y": "1"}, "y")
        git(p, "reset", "-q", "--hard", "HEAD~1")
        git(p, "replace", blob, git(p, "rev-parse", f"{other}:y"))
        token = "REPLACE_REFS_PRESENT"
    elif damage == "alternates":
        (p / ".git" / "objects" / "info").mkdir(parents=True, exist_ok=True)
        (p / ".git" / "objects" / "info" / "alternates").write_text(str(tmp_path / "objects") + "\n", encoding="utf-8")
        token = "ALTERNATES_PRESENT"
    else:
        side = commit(p, {"z": "1"}, "side")
        git(p, "update-ref", "refs/remotes/origin/main", side)
        git(p, "reset", "-q", "--hard", "HEAD~1")
        commit(p, {"w": "1"}, "rewritten")
        token = "MAIN_DIVERGED"
    before = _no_writes(workspace, home)
    capsys.readouterr()
    assert install(workspace, home) == 1
    assert token in capsys.readouterr().err
    assert guard.main(["--reset", "--yes"], root=workspace.parent, home=home) == 1
    assert token in capsys.readouterr().err
    assert _no_writes(workspace, home) == before


# atc guard: reset


def drop_gh_secret(workspace):
    return commit(workspace.parent / "projects" / "p", {"policies/hard-deny.yaml": PROJECT_POLICY.replace(
        "    - id: gh-secret\n      argv_prefix: [gh, secret]\n", "")}, "drop gh-secret\n\nloosen")


def test_g49_g51_reset_lists_what_it_drops_and_writes_fresh(workspace, home, capsys, tmp_path):
    p = workspace.parent / "projects" / "p"
    git(p, "update-ref", "refs/remotes/origin/main", "main")
    assert install(workspace, home) == 0
    table_now(workspace, home)
    hook_state.write_json(paths(workspace, home)["legacy"], compose(workspace, home))
    sha = drop_gh_secret(workspace)
    workspace.write_text(workspace.read_text(encoding="utf-8").replace("  - ~/.atc\n", ""), encoding="utf-8")
    capsys.readouterr()
    assert guard.main(["--reset"], root=workspace.parent, home=home, answer=lambda _: "y") == 0
    out = capsys.readouterr().out
    assert "drop rule gh-secret" in out and f"drop write root {norm(home / '.atc')}" in out
    assert f"{p}@{sha}" in out and "1 commits ahead of origin/main" in out and "drop gh-secret" in out
    baseline = hook_state.read_baseline(paths(workspace, home)["baseline"])
    assert baseline["table"] == guard.compose(workspace, home, atc_home(home))["table"]
    assert baseline["written_by"] == "reset"
    assert not paths(workspace, home)["cache"].exists() and not paths(workspace, home)["legacy"].exists()
    assert verdict(workspace, home, "gh secret list") is None


def test_g50_reset_writes_only_on_yes(workspace, home, capsys):
    assert install(workspace, home) == 0
    drop_gh_secret(workspace)
    before = paths(workspace, home)["baseline"].read_bytes()
    assert guard.main(["--reset"], root=workspace.parent, home=home, answer=lambda _: "n") == 1
    assert "STOP DECLINED" in capsys.readouterr().err
    assert guard.main(["--reset", "--json"], root=workspace.parent, home=home) == 1
    planned = json.loads(capsys.readouterr().out)
    assert planned["verdict"] == "PLANNED" and any("gh-secret" in rule for rule in planned["drops"])
    assert paths(workspace, home)["baseline"].read_bytes() == before
    assert guard.main(["--reset", "--json", "--yes"], root=workspace.parent, home=home) == 0
    assert paths(workspace, home)["baseline"].read_bytes() != before


def test_g45_g77_reset_needs_current_entries_with_this_home(workspace, home, capsys, tmp_path):
    assert install(workspace, home) == 0
    before = paths(workspace, home)["baseline"].read_bytes()
    codex = home / ".codex" / "hooks.json"
    ge.install_hook(codex, ge.hook_group("codex", ge.engine_command(workspace, "codex", home, tmp_path / "other")))
    assert guard.main(["--reset", "--yes"], root=workspace.parent, home=home) == 1
    assert "ENTRY_OUTDATED" in capsys.readouterr().err
    assert guard.main([], root=workspace.parent, home=home) == 1
    ge.install_hook(codex, ge.hook_group("codex", "atc hook --harness codex"))
    assert guard.report(workspace, home, atc_home(home))["entries"]["codex"] == "outdated"
    assert paths(workspace, home)["baseline"].read_bytes() == before


def test_g44_an_entry_without_the_homes_is_outdated(workspace, home, capsys):
    assert install(workspace, home) == 0
    ge.install_hook(home / ".claude" / "settings.json",
                    ge.hook_group("claude", f'atc hook --harness claude --workspace "{workspace.as_posix()}"'))
    assert guard.main([], root=workspace.parent, home=home) == 1
    assert "STOP ENTRY_OUTDATED" in capsys.readouterr().err


def test_g78_without_a_baseline_reset_compares_with_the_cache(workspace, home, capsys):
    assert install(workspace, home) == 0
    table_now(workspace, home)
    drop_gh_secret(workspace)
    paths(workspace, home)["baseline"].unlink()
    capsys.readouterr()
    assert guard.main(["--reset"], root=workspace.parent, home=home, answer=lambda _: "n") == 1
    out = capsys.readouterr().out
    assert "no baseline: compared with the cache" in out and "drop rule gh-secret" in out


# atc guard and atc doctor: the report


def test_g52_check_and_doctor_write_nothing(workspace, home):
    assert install(workspace, home) == 0
    drop_gh_secret(workspace)
    before = snapshot(paths(workspace, home)["dir"])
    guard.main([], root=workspace.parent, home=home)
    doctor.hook_checks(workspace, home)
    assert snapshot(paths(workspace, home)["dir"]) == before


def test_g53_retained_rules_are_a_warning(workspace, home, capsys):
    assert install(workspace, home) == 0
    drop_gh_secret(workspace)
    capsys.readouterr()
    assert guard.main([], root=workspace.parent, home=home) == 0
    out = capsys.readouterr().out
    assert out.startswith("CURRENT guard") and "WARN 1 rules kept from earlier inputs" in out


def test_g54_a_legacy_table_is_listed_only_beside_new_entries(workspace, home):
    hook_state.write_json(paths(workspace, home)["legacy"], compose(workspace, home))
    ge.install_hook(home / ".claude" / "settings.json", ge.hook_group("claude", "atc hook --harness claude"))
    assert guard.report(workspace, home, atc_home(home))["legacy"] is None
    assert install(workspace, home) == 0
    hook_state.write_json(paths(workspace, home)["legacy"], compose(workspace, home))
    assert guard.report(workspace, home, atc_home(home))["legacy"] == str(paths(workspace, home)["legacy"])
    assert [c.status for c in doctor.hook_checks(workspace, home) if c.name == "pretooluse legacy"] == ["WARN"]


def test_g79_doctor_reports_the_baseline(workspace, home):
    assert [c.status for c in doctor.hook_checks(workspace, home)] == ["NOT_INITIALIZED"]
    assert install(workspace, home) == 0
    lines = {c.name: c for c in doctor.hook_checks(workspace, home)}
    assert lines["pretooluse baseline"].status == "READY" and "written by install" in lines["pretooluse baseline"].detail
    assert lines["pretooluse entries"].status == "READY" and lines["pretooluse table"].status == "READY"
    baseline = paths(workspace, home)["baseline"]
    baseline.write_text("{not json", encoding="utf-8")
    line = {c.name: c for c in doctor.hook_checks(workspace, home)}["pretooluse baseline"]
    assert line.status == "ERROR" and line.detail.startswith("BASELINE_INVALID")
    baseline.unlink()
    line = {c.name: c for c in doctor.hook_checks(workspace, home)}["pretooluse baseline"]
    assert line.status == "ERROR" and line.detail.startswith("BASELINE_MISSING")


def test_guard_reports_a_table_that_does_not_compose(workspace, home, capsys):
    assert install(workspace, home) == 0
    commit(workspace.parent / "projects" / "p", {"policies/hard-deny.yaml": "version: [\n"})
    capsys.readouterr()
    assert guard.main(["--json"], root=workspace.parent, home=home) == 1
    assert json.loads(capsys.readouterr().out)["refusal"]["reason"] == "POLICY_INVALID"
    line = {c.name: c for c in doctor.hook_checks(workspace, home)}["pretooluse table"]
    assert line.status == "ERROR" and line.detail.startswith("POLICY_INVALID")


def test_guard_refuses_a_missing_workspace_and_a_bare_yes(tmp_path, home, capsys):
    assert guard.main([], root=tmp_path, home=home) == 1
    assert capsys.readouterr().err.startswith("STOP WORKSPACE_MISSING: ")
    (tmp_path / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    assert guard.main(["--yes"], root=tmp_path, home=home) == 2


def test_g55_sync_check_only_reports_the_hook(workspace, home, capsys, monkeypatch):
    import validate_workspace as validator
    monkeypatch.setattr(ge, "default_home", lambda: home)
    validator.FAILURES.clear()
    validator.report_hook(workspace)
    assert "pretooluse hook ENTRY_MISSING" in capsys.readouterr().out
    assert install(workspace, home) == 0
    commit(workspace.parent / "projects" / "p", {"policies/hard-deny.yaml": "version: [\n"})
    capsys.readouterr()
    validator.report_hook(workspace)
    assert "pretooluse hook POLICY_INVALID" in capsys.readouterr().out and not validator.FAILURES


def test_install_replaces_the_entry_of_an_engine_named_by_path(tmp_path):
    """An entry that runs the engine of a release copy by path is managed; install keeps one managed entry."""
    settings = tmp_path / "settings.json"
    old = {"type": "command", "command": 'python "C:/x/.atc/releases/v0.6.0/adapters/python/scripts/pretooluse_hook.py"'
                                        " --harness claude"}
    mine = {"type": "command", "command": "my-own-hook"}
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [old, mine]}]}}),
                        encoding="utf-8")
    ge.install_hook(settings, ge.hook_group("claude", "atc hook --harness claude"))
    ge.install_hook(settings, ge.hook_group("claude", "atc hook --harness claude"))
    entries = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    commands = [hook["command"] for entry in entries for hook in entry["hooks"]]
    assert sorted(commands) == ["atc hook --harness claude", "my-own-hook"]
    assert all(ge._managed({"command": c}) for c in ("atc hook --harness codex", '"C:/bin/atc.exe" hook', "atc.exe hook"))
    assert not any(ge._managed({"command": c}) for c in ("my-own-hook", "hatc hook", "atc hooks", "atc doctor"))


def test_live_drift_compares_the_composed_table(workspace, home, capsys):
    table = home / "t" / ge.HOOK_TABLE
    assert ge.main(["--workspace", str(workspace), "--hook-out", str(table.parent)]) == 0
    assert ge.main(["--workspace", str(workspace), "--check", "--live-hook-table", str(table)]) == 0
    drop_gh_secret(workspace)
    capsys.readouterr()
    assert ge.main(["--workspace", str(workspace), "--check", "--live-hook-table", str(table)]) == 1
    assert "live-hook-table" in capsys.readouterr().out


@pytest.mark.parametrize("args, message", [
    (["--install-hooks"], "unrecognized arguments: --install-hooks"),
    (["--workspace", "w.yaml", "--live-hook-table", "t.json"], "require --check"),
    (["--policies", "p"], "go together"),
    ([], "are required unless"),
])
def test_usage_errors(args, message, capsys):
    with pytest.raises(SystemExit) as raised:
        ge.main(args)
    assert raised.value.code == 2 and message in capsys.readouterr().err

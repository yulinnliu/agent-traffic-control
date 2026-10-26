"""The PreToolUse guard refuses a persistent directory change into the worktrees root."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

import worktree_cd_guard as guard

ROOT = r"C:\Users\Some One\Hami"
WORKTREES = PureWindowsPath(ROOT, "worktrees")
TREE = "worktrees/hami-quant-trading/claude+exp007-demo-confirm"
WIN = rf"{ROOT}\worktrees\hami-quant-trading\claude+exp007-demo-confirm"
FWD = WIN.replace("\\", "/")
POSIX = "/c" + FWD[2:]


def check(tool: str, command: str, cwd: str = ROOT) -> str | None:
    return guard.blocked(tool, command, cwd, WORKTREES)


@pytest.mark.parametrize("command", [
    f'cd "{POSIX}"',
    f"cd '{POSIX}'",
    f'cd "{FWD}"',
    f'cd "{WIN}"',
    f"cd {POSIX.replace(' ', chr(92) + ' ')}",
    f"cd {WIN.replace(' ', chr(92) + ' ')}",
    f'cd "{POSIX}" && git status',
    f'git status; cd "{POSIX}"',
    f'pushd "{POSIX}"',
    f'cd -P "{POSIX}"',
    f'cd "{ROOT}/worktrees"',
    f"cd {TREE}",
    f'if true; then cd "{POSIX}"; fi',
    f'{{ cd "{POSIX}"; git status; }}',
    f'cd "{POSIX.upper()}"',
])
def test_bash_top_level_cd_into_worktrees_is_refused(command):
    assert check("Bash", command) is not None


@pytest.mark.parametrize("command", [
    f'( cd "{POSIX}" && git status )',
    f'(cd "{WIN}"; git log -1)',
    f'git -C "{POSIX}" status',
    f'git -C "{WIN}" log --oneline -3',
    f'cd "{ROOT}" && git status',
    f'cd "{ROOT}/adapters/python" && uv run --locked pytest',
    f'cd "{ROOT}/worktrees-old"',
    f'echo "$(cd "{POSIX}" && pwd)"',
    f'echo "cd {POSIX}"',
    f"git commit -F - <<'EOF'\ncd {POSIX}\nEOF\ngit status",
    f"# cd {POSIX}\ngit status",
    "cd -",
    "cd",
    'cd "$WT"',
])
def test_bash_subshell_git_c_and_other_paths_are_allowed(command):
    assert check("Bash", command) is None


@pytest.mark.parametrize("command", [
    f'Set-Location "{WIN}"',
    f"Set-Location -Path '{WIN}'",
    f'Set-Location -LiteralPath "{FWD}"',
    f'sl "{WIN}"',
    f'cd "{WIN}"',
    f'Push-Location "{WIN}"; git status; Pop-Location',
    f'& {{ cd "{WIN}" }}',
    f'git status; set-location "{WIN}"',
    f"cd {TREE}",
])
def test_powershell_location_change_into_worktrees_is_refused(command):
    assert check("PowerShell", command) is not None


@pytest.mark.parametrize("command", [
    f'git -C "{WIN}" status',
    f'Set-Location "{ROOT}"',
    f'Set-Location "{ROOT}\\adapters\\python"; uv run atc validate',
    f'Get-ChildItem "{WIN}"',
    f'Write-Output "cd {WIN}"',
    f"git commit -m @'\ncd {WIN}\n'@",
])
def test_powershell_other_commands_are_allowed(command):
    assert check("PowerShell", command) is None


def test_relative_target_resolves_against_the_session_cwd():
    assert check("Bash", "cd ..", cwd=WIN) is not None
    assert check("Bash", "cd ../../..", cwd=WIN) is None


def test_other_tools_are_ignored():
    assert check("Read", f'cd "{POSIX}"') is None


def run_hook(payload: dict) -> subprocess.CompletedProcess:
    script = Path(guard.__file__)
    return subprocess.run([sys.executable, str(script)], input=json.dumps(payload),
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_hook_prints_a_deny_naming_the_alternatives():
    target = guard.WORKTREES / "some-project" / "claude+topic"
    ran = run_hook({"tool_name": "Bash", "tool_input": {"command": f'cd "{target}"'}, "cwd": str(guard.ROOT)})
    assert ran.returncode == 0, ran.stderr
    decision = json.loads(ran.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    for alternative in ("git -C <path>", "absolute", "( cd <path> && ... )"):
        assert alternative in decision["permissionDecisionReason"]


def test_hook_allows_silently_and_fails_open():
    allowed = run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(guard.ROOT)})
    assert (allowed.returncode, allowed.stdout) == (0, "")
    broken = subprocess.run([sys.executable, guard.__file__], input="not json",
                            capture_output=True, text=True, timeout=60)
    assert (broken.returncode, broken.stdout) == (0, "")

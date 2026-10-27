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
    f"git commit -F - <<'EOF'\ncd \"{POSIX}\"\nEOF\ngit status",
    f'git status # then; cd "{POSIX}"',
    f'echo `cd "{POSIX}"; pwd`',
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
    f"git commit -m @'\nDon't run:\nSet-Location \"{WIN}\"\n'@",
    f'<#\nSet-Location "{WIN}"\n#>\ngit status',
    f'git status # then; Set-Location "{WIN}"',
])
def test_powershell_other_commands_are_allowed(command):
    assert check("PowerShell", command) is None


@pytest.mark.parametrize("command", [
    f'Set-Location -ErrorAction Stop -Path "{WIN}"',
    f'Set-Location -ea 0 "{WIN}"',
])
def test_powershell_common_parameter_values_are_skipped(command):
    assert check("PowerShell", command) is not None


QUANT = "/c" + ROOT[2:].replace("\\", "/") + "/worktrees/hami-quant/claude+signal-feed-job-object"


@pytest.mark.parametrize("command, cwd", [
    (f'W="{QUANT}"; cd "$W" && git log --oneline -1 && for f in a b; do echo "===== $f"; cat -n "$f"; done',
     rf"{ROOT}\projects\hami-quant"),
    (f'W="{QUANT}"; cd "$W"; wc -l docs/runbooks/COLLECTOR_INSTALLED_RUNTIME.md', ROOT),
    (f'export W="{QUANT}"; cd "${{W}}"', ROOT),
    (f"W={QUANT.replace(' ', chr(92) + ' ')}; cd $W", ROOT),
    (f"declare -x W='{ROOT}'; cd \"$W/worktrees\"", ROOT),
    (f'W="{ROOT}"; W="{QUANT}"; pushd ${{W}}/docs', ROOT),
    (f'W="{ROOT}"; W+=/worktrees/x; cd "$W"', ROOT),
    (f'if true; then export W="{QUANT}"; fi; cd "$W"', ROOT),
])
def test_bash_cd_to_a_literal_variable_is_refused(command, cwd):
    assert check("Bash", command, cwd) is not None


def test_refusal_names_the_resolved_path_with_later_variables_left_as_written():
    target = check("Bash", f'W="{QUANT}"; cd "$W/$Y"')
    assert target == f"{QUANT}/$Y"
    assert f"{QUANT}/$Y" in guard.reason("Bash", target)


@pytest.mark.parametrize("command", [
    f'W="{QUANT}"; ( cd "$W" && git status )',
    f'( W="{QUANT}" ); cd "$W"',
    f'W="{QUANT}"; W="$OTHER"; cd "$W"',
    f'W+="{QUANT}"; cd "$W"',
    'W="$HOME/Hami/worktrees/x"; cd "$W"',
    f"W={TREE}`date`; cd \"$W\"",
    f'W="{QUANT}`date`"; cd "$W"',
    f"W={TREE}$(date); cd \"$W\"",
    f'W="{QUANT}" git status; cd "$W"',
])
def test_bash_cd_to_a_nested_or_expanded_variable_is_allowed(command):
    assert check("Bash", command) is None


@pytest.mark.parametrize("command", [
    f"$w = '{WIN}'; Set-Location $w",
    f'$w = "{WIN}"\ncd $w',
    f"$W='{WIN}'; Set-Location -Path $w",
    f'$w ="{ROOT}"; sl "$W\\worktrees"',
    f"[string]$w = '{WIN}'; Set-Location $w",
])
def test_powershell_location_change_to_a_literal_variable_is_refused(command):
    assert check("PowerShell", command) is not None


@pytest.mark.parametrize("command", [
    f'$env:W = "{WIN}"; Set-Location $env:W',
    f'$w = "$(Get-Location)\\worktrees"; Set-Location $w',
    f'$w = "{WIN}"; $w = "$HOME"; Set-Location $w',
    f'$w = "{ROOT}" + "\\worktrees"; Set-Location $w',
    f'$w = "{WIN}$suffix"; Set-Location $w',
    f'$w = "{WIN}" * 0; Set-Location $w',
])
def test_powershell_variable_from_an_expression_is_allowed(command):
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
    for alternative in ("git -C <path>", "absolute", "( cd <path> && ... )", "`PYTHONPATH`"):
        assert alternative in decision["permissionDecisionReason"]


def test_powershell_refusal_names_the_import_alternative():
    assert "`PYTHONPATH` to its `packages/*/src`" in guard.reason("PowerShell", WIN)


def test_hook_allows_silently_and_fails_open():
    allowed = run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(guard.ROOT)})
    assert (allowed.returncode, allowed.stdout) == (0, "")
    broken = subprocess.run([sys.executable, guard.__file__], input="not json",
                            capture_output=True, text=True, timeout=60)
    assert (broken.returncode, broken.stdout) == (0, "")

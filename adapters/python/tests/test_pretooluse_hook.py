"""The PreToolUse hook's engine against fixed tables (docs/ENFORCEMENT.md § PreToolUse hook).

Roots C:\\hami and C:\\tmpok need not exist; the write-scope cases are those of the retired write guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pretooluse_hook as hook

pytestmark = pytest.mark.unit

CWD = "C:\\hami\\projects\\x"


def table(**overrides) -> dict:
    base = {
        "version": 1,
        "commands": [
            {"id": "atc-integrate-owner-voice", "argv_prefix": ["atc", "integrate"],
             "with_args": ["--owner-run", "--owner-words"], "unless_args": ["--check"]},
            {"id": "atc-integrate", "argv_prefix": ["atc", "integrate"], "unless_args": ["--check"]},
            {"id": "git-push-main", "argv_prefix": ["git", "push"], "push_targets": ["main"]},
            {"id": "atc-verify-background", "argv_prefix": ["atc", "verify"], "background": True},
            {"id": "atc-test-run-background", "argv_prefix": ["atc", "test", "run"], "background": True},
            {"id": "git-reset-hard", "argv_prefix": ["git", "reset", "--hard"]},
        ],
        "filesystem": [
            {"id": "env", "glob": "**/.env", "modes": ["read", "edit", "write"],
             "scope": [{"base": "c:\\hami\\projects\\x", "depth": 0}, {"base": "c:\\hami\\worktrees\\x", "depth": 1}]},
            {"id": "pem-read", "glob": "**/*.pem", "modes": ["read"], "scope": None},
            {"id": "state", "glob": "c:/hami/state/x-data/**", "modes": ["edit", "write"], "scope": None},
        ],
        "worktrees": ["c:\\hami\\worktrees"],
        "write_roots": ["c:\\hami", "c:\\tmpok"],
        "protected": ["c:\\hami\\adapters\\python\\scripts\\pretooluse_hook.py"],
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def temp_env(monkeypatch):
    monkeypatch.setenv("TEMP", "C:\\tmpok")


def decide(tool: str, harness: str = "claude", cwd: str = CWD, rules: dict | None = None, **tool_input) -> str | None:
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": cwd}
    return hook.decide(payload, rules or table(), harness)


# Command rules


@pytest.mark.parametrize("tool, command, rule", [
    ("Bash", "git -C /c/hami/x reset --hard", "git-reset-hard"),
    ("Bash", "git -c core.x=1 --no-pager reset --hard HEAD", "git-reset-hard"),
    ("Bash", "cd /c/hami && git reset --hard", "git-reset-hard"),
    ("Bash", "X=1 env Y=2 git reset --hard", "git-reset-hard"),
    ("Bash", "bash -lc 'git reset --hard'", "git-reset-hard"),
    ("Bash", "cmd //c git reset --hard", "git-reset-hard"),
    ("PowerShell", "& 'C:\\Program Files\\Git\\cmd\\git.exe' reset --hard", "git-reset-hard"),
    ("PowerShell", "pwsh -Command \"git reset --hard\"", "git-reset-hard"),
    ("Bash", "atc integrate --task ATC-1 --reviewed abc", "atc-integrate"),
    ("Bash", "uv run --locked --project adapters/python atc integrate --task ATC-1", "atc-integrate"),
    ("Bash", "python -m atc_cli integrate --task ATC-1", "atc-integrate"),
    ("PowerShell", "atc integrate --task ATC-1 --owner-words 'yes'", "atc-integrate-owner-voice"),
    ("PowerShell", "atc integrate --task ATC-1 --owner-run", "atc-integrate-owner-voice"),
    ("Bash", "git push origin topic:main", "git-push-main"),
    ("Bash", "git push origin +topic:refs/heads/main", "git-push-main"),
    ("Bash", "git push origin main", "git-push-main"),
    ("Bash", "git push origin --delete main", "git-push-main"),
    ("Bash", "git push origin 'refs/heads/*:refs/heads/*'", "git-push-main"),
    ("Bash", "git push --mirror origin", "git-push-main"),
    ("Bash", "atc verify --task ATC-1 &", "atc-verify-background"),
    ("PowerShell", "atc verify --task ATC-1 &", "atc-verify-background"),
    ("PowerShell", "Start-ThreadJob { atc test run --scratch s -- t.py }", "atc-test-run-background"),
    ("PowerShell", "Start-Process atc -ArgumentList 'verify','--task','ATC-1'", "atc-verify-background"),
])
def test_a_denied_command_names_its_rule(tool, command, rule):
    reason = decide(tool, command=command)
    assert reason is not None and f"`{rule}`" in reason


@pytest.mark.parametrize("tool, command", [
    ("Bash", "atc integrate --check --task ATC-1 --reviewed abc"),
    ("Bash", "atc integrate --check --task ATC-1 --owner-words 'Owner run: approve integration'"),
    ("PowerShell", "atc integrate --check=1 --owner-run --task ATC-1"),
    ("Bash", "git push origin topic"),
    ("Bash", "git push origin main:topic"),
    ("Bash", "git reset --soft HEAD~1"),
    ("Bash", "echo 'git reset --hard'"),
    ("Bash", "git commit -m 'atc integrate --task X'"),
    ("Bash", "atc verify --task ATC-1"),
    ("Bash", "atc verify --task ATC-1 2>&1 | tail -5"),
    ("Bash", "atc verify --task ATC-1 && echo done"),
    ("PowerShell", "Start-Process atc -ArgumentList 'verify' -Wait"),
    ("PowerShell", "atc test run --scratch s -- t.py; atc verify --task ATC-1"),
])
def test_an_allowed_command_passes(tool, command):
    assert decide(tool, command=command) is None


def test_the_background_flag_of_the_call_counts():
    assert "`atc-test-run-background`" in decide("PowerShell", command="atc test run --scratch s -- t.py",
                                                 run_in_background=True)
    assert decide("PowerShell", command="atc test run --scratch s -- t.py", run_in_background=False) is None


def git_repo(path: Path, branch: str) -> Path:
    (path / ".git").mkdir(parents=True)
    (path / ".git" / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("command", ["git push", "git push origin", "git push origin HEAD", "git push -u origin HEAD"])
def test_a_push_without_a_refspec_reads_the_checked_out_branch(tmp_path, command):
    main = git_repo(tmp_path / "main", "main")
    topic = git_repo(tmp_path / "topic", "topic")
    rules = table(write_roots=[str(tmp_path).lower()])
    assert "`git-push-main`" in decide("Bash", cwd=str(main), rules=rules, command=command)
    assert decide("Bash", cwd=str(topic), rules=rules, command=command) is None
    assert "`git-push-main`" in decide("Bash", cwd=str(topic), rules=rules, command=f"git -C '{main}' {command[4:]}")


@pytest.mark.parametrize("option", ["--all", "--mirror", "--branches"])
def test_a_push_of_every_branch_is_refused_from_a_topic_branch(tmp_path, option):
    topic = git_repo(tmp_path / "topic", "topic")
    assert "`git-push-main`" in decide("Bash", cwd=str(topic), command=f"git push {option} origin")
    assert decide("Bash", cwd=str(topic), command="git push --tags origin") is None


def test_a_linked_worktree_reads_its_own_head(tmp_path):
    gitdir = tmp_path / "repo" / ".git" / "worktrees" / "t"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    assert "`git-push-main`" in decide("Bash", cwd=str(tree), rules=table(), command="git push")


def test_a_push_from_an_unresolvable_directory_counts_as_main(tmp_path):
    assert "`git-push-main`" in decide("Bash", cwd=str(tmp_path / "nowhere"), command="git push")


# Paths (the retired write guard's cases)


@pytest.mark.parametrize("path", ["C:\\out\\f.txt", "/c/out/f.txt", "C:/out/f.txt", "..\\..\\..\\out\\f"])
def test_a_file_tool_outside_the_roots_is_refused(path):
    assert "outside the directories" in decide("Write", file_path=path)


@pytest.mark.parametrize("path", ["C:\\hami\\a.py", "/c/hami/a.py", "rel\\a.py", "C:\\TmpOK\\q\\a"])
def test_a_file_tool_inside_the_roots_is_allowed(path):
    assert decide("Edit", file_path=path) is None


def test_a_notebook_path_is_checked():
    assert "outside the directories" in decide("NotebookEdit", notebook_path="C:\\out\\n.ipynb")


@pytest.mark.parametrize("command", [
    "mkdir -p /c/q/rv",
    "rm -rf C:/out",
    "( cd /c/hami && rm -rf /c/out )",
    "cp /c/hami/a /c/out/",
    "echo x > /c/out/f",
    "echo x >>/c/out/f",
    'W="/c/out"; mkdir "$W/x"',
    "cmd //c rmdir //s //q C:\\\\out",
    "python -m pytest --basetemp C:/out/bt",
    "python -m pytest --basetemp=C:/out/bt",
    "git archive HEAD | tar -x -C /c/out",
    "git worktree add /c/out/wt",
    "touch ~/../../out.txt",
    "mkdir /c/out/$NAME",
])
def test_a_bash_write_outside_the_roots_is_refused(command):
    assert decide("Bash", command=command) is not None


@pytest.mark.parametrize("command", [
    "ls /c/",
    "git -C /c/out status",
    "cp /c/out/a /c/hami/b",
    "echo x 2>/dev/null",
    "cmd //c rmdir //s //q C:\\\\hami\\\\x",
    "mkdir -p \"$TEMP/q/rv\"",
    "python -m pytest --basetemp=C:/tmpok/q/bt",
    "cat > /c/hami/f <<'EOF'\nmkdir /c/out\nEOF",
    "rm -rf \"$UNKNOWN_DIR\"",
    "mkdir rel/dir",
])
def test_a_bash_command_inside_the_roots_or_read_only_is_allowed(command):
    assert decide("Bash", command=command) is None


@pytest.mark.parametrize("command", [
    "New-Item -ItemType Directory -Path C:\\out",
    "Remove-Item -LiteralPath 'C:\\out' -Recurse -Force",
    "Copy-Item C:\\hami\\a -Destination C:\\out",
    "Copy-Item -Path C:\\hami\\a C:\\out",
    "Copy-Item C:\\hami\\a C:\\out",
    "'x' | Out-File C:\\out\\f -Encoding utf8",
    "Write-Output x > C:\\out\\f",
    "$p = 'C:\\out'; Remove-Item $p",
    "[System.IO.Directory]::Delete('C:\\out')",
    "cmd /c rmdir /s /q C:\\out",
    "Set-Content -Path:C:\\out\\f -Value x",
])
def test_a_powershell_write_outside_the_roots_is_refused(command):
    assert decide("PowerShell", command=command) is not None


@pytest.mark.parametrize("command", [
    "Get-ChildItem C:\\ -Force",
    "Copy-Item C:\\out\\a C:\\hami\\b",
    "Write-Output 'a > C:\\out'",
    "Get-Item x 2>$null",
    "Remove-Item C:\\hami\\x -Recurse",
    "New-Item -ItemType Junction -Path C:\\hami\\v -Target C:\\out",
    "New-Item -ItemType Directory -Force $env:TEMP\\q",
    "foreach ($d in 'C:\\x') { Remove-Item $d }",
    "Remove-Item -Path C:\\hami\\x -ErrorAction SilentlyContinue",
])
def test_a_powershell_command_inside_the_roots_or_read_only_is_allowed(command):
    assert decide("PowerShell", command=command) is None


@pytest.mark.parametrize("tool, command", [
    ("Bash", 'mkdir "$OUTSIDE/x"'),
    ("PowerShell", "New-Item -ItemType Directory $env:OUTSIDE\\x"),
    ("PowerShell", "cmd /c rmdir /s /q %OUTSIDE%"),
])
def test_an_environment_variable_naming_an_outside_dir_is_resolved(monkeypatch, tool, command):
    monkeypatch.setenv("OUTSIDE", "C:\\out")
    assert decide(tool, command=command) is not None


@pytest.mark.parametrize("tool, field, value, rule", [
    ("Write", "file_path", "C:\\hami\\projects\\x\\.env", "`env`"),
    ("Edit", "file_path", "C:\\hami\\worktrees\\x\\claude+t\\sub\\.env", "`env`"),
    ("Write", "file_path", "C:\\hami\\state\\x-data\\raw\\a.parquet", "`state`"),
    ("Edit", "file_path", "C:\\hami\\adapters\\python\\scripts\\pretooluse_hook.py", "part of the PreToolUse hook"),
    ("Bash", "command", "echo k > /c/hami/projects/x/.env", "`env`"),
    ("Bash", "command", "rm -rf /c/hami/state/x-data", "`state`"),
    ("PowerShell", "command", "Remove-Item C:\\hami\\state\\x-data\\raw -Recurse", "`state`"),
])
def test_a_write_matching_a_rule_or_the_hook_files_is_refused(tool, field, value, rule):
    assert rule in decide(tool, **{field: value})


@pytest.mark.parametrize("path", [
    "C:\\hami\\projects\\other\\.env",
    "C:\\hami\\worktrees\\x\\.env",
    "C:\\hami\\projects\\x\\key.pem",
    "C:\\hami\\worktrees\\root\\claude+t\\adapters\\python\\scripts\\pretooluse_hook.py",
])
def test_a_write_outside_a_rule_scope_or_mode_is_allowed(path):
    assert decide("Write", file_path=path) is None


def test_other_tools_are_not_checked():
    assert decide("Read", file_path="C:\\out\\f") is None


# Worktree cd, Codex


def test_a_top_level_cd_into_a_worktree_is_refused_for_claude_only():
    command = "cd /c/hami/worktrees/x/claude+t && git status"
    assert "changing directory to /c/hami/worktrees/x/claude+t" in decide("Bash", command=command)
    assert decide("Bash", harness="codex", command=command) is None
    assert decide("Bash", command="( cd /c/hami/worktrees/x/claude+t && git status )") is None


def test_codex_reads_a_shell_call_as_bash_and_as_powershell():
    assert "outside the directories" in decide("Bash", harness="codex", command="Remove-Item C:\\out -Recurse")
    assert "outside the directories" in decide("Bash", harness="codex", command="rm -rf /c/out")
    assert "`git-reset-hard`" in decide("Bash", harness="codex", command="& git reset --hard")
    assert "`git-reset-hard`" in decide("Bash", harness="codex", command="Start-Job { git reset --hard }")
    assert decide("Bash", command="Start-Job { git reset --hard }") is None


def test_codex_apply_patch_paths_are_checked():
    patch = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** Move to: ../../../out/a.py\n*** End Patch\n"
    assert "outside the directories" in decide("apply_patch", harness="codex", command=patch)
    assert "`env`" in decide("apply_patch", harness="codex", command="*** Begin Patch\n*** Add File: .env\n+K=V\n")
    assert decide("apply_patch", harness="codex", command="*** Begin Patch\n*** Add File: src/b.py\n+x\n") is None

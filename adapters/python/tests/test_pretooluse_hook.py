"""The PreToolUse hook's engine against fixed tables (docs/ENFORCEMENT.md § PreToolUse hook).

Roots C:\\hami and C:\\tmpok need not exist; the write-scope cases are those of the retired write guard.
"""

from __future__ import annotations

import io
import json
import sys
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
    ("PowerShell", "atc test run --scratch s -- t.py 2>&1 | Select-Object -Last 5"),
    ("PowerShell", "atc verify --task ATC-1 *>&1 | Out-String"),
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


@pytest.mark.parametrize("command", ["git push origin $(git branch --show-current)",
                                     "git push origin `git branch --show-current`"])
def test_a_push_to_a_substituted_refspec_reads_the_checked_out_branch(tmp_path, command):
    main = git_repo(tmp_path / "main", "main")
    topic = git_repo(tmp_path / "topic", "topic")
    assert "`git-push-main`" in decide("Bash", cwd=str(main), command=command)
    assert decide("Bash", cwd=str(topic), command=command) is None
    assert "`git-push-main`" in decide("PowerShell", cwd=str(main), command="git push origin (git branch --show-current)")


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
    "sed -i 's/a/b/' /c/out/f",
    "sed -i.bak -e 's/a/b/' /c/out/f",
    "perl -pi -e 's/a/b/' /c/out/f",
    "curl -sSo /c/out/f https://example.invalid",
    "curl --output=/c/out/f https://example.invalid",
    "wget -O /c/out/f https://example.invalid",
    "mv /c/out/a /c/hami/a",
    "cp -t /c/out /c/hami/a",
    "cp --target-directory=/c/out /c/hami/a",
    "install -d /c/out/d",
    "cp $(ls /c/hami/a) /c/out/dest",
    "bash -c 'echo x > /c/out/f'",
    'sh -c "rm -rf /c/out"',
    "pwsh -Command \"Set-Content C:\\\\out\\\\f 'v'\"",
    'cmd //c "echo x > C:\\\\out\\\\f"',
    'cmd //c "cd C:\\\\hami && del C:\\\\out\\\\f"',
    'D="$OUTSIDE_DIR/x"; mkdir -p "$D"',
    "echo x > /c/hami/a 2> /c/out/err",
    "cp /c/hami/a /c/out/b 2> /c/hami/log",
])
def test_a_bash_write_outside_the_roots_is_refused(monkeypatch, command):
    monkeypatch.setenv("OUTSIDE_DIR", "C:\\out")
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
    "mkdir rel/dir",
    'echo "a > /c/out/b"',
    "git commit -m \"$(cat <<'EOF'\nfix: a -> $b\nEOF\n)\"",
    'D="$TEMP/x"; mkdir -p "$D" && echo ok > "$D/f"',
    "echo x > $PWD/f",
    "sed -n 's/a/b/p' /c/out/f",
    "curl -o /c/hami/f https://example.invalid",
    "ls 2>&1 | head",
    'cmd //c "dir C:\\\\out > nul"',
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
    "$o=\"$env:OUTSIDE_DIR\\rev-diff.txt\"; git -C C:\\hami diff a b > $o",
    "git log >> C:\\out\\log.txt",
    "git log 2> C:\\out\\err.txt",
    "git log *> C:\\out\\all.txt",
    "git log 3>&1 > C:\\out\\log.txt",
    "Out-File -FilePath C:\\out\\f",
    "'x' | Out-File -Fi C:\\out\\f",
    "Set-Content C:\\out\\f 'v'",
    "Set-Content -Va v C:\\out\\f",
    "Set-Content -Va v -Pa C:\\out\\f",
    "Add-Content -LiteralPath C:\\out\\f -Value v",
    "Set-Content -Value (Get-Date) -Path C:\\out\\d.txt",
    "Get-Process | Tee-Object -FilePath C:\\out\\t.txt",
    "Get-Process | Export-Csv C:\\out\\p.csv",
    "New-Item -Path C:\\hami -Name ..\\..\\out -ItemType Directory",
    "Move-Item C:\\out\\a C:\\hami\\a",
    "Copy-Item -Path C:\\hami\\a -Destination C:\\out\\b",
    "Expand-Archive C:\\hami\\a.zip -DestinationPath C:\\out",
    "$p = Join-Path $env:OUTSIDE_DIR 'q'; Remove-Item $p -Recurse",
    "Set-Content (Join-Path $env:OUTSIDE_DIR 'x') 'v'",
    "$env:W = 'C:\\out'; New-Item -ItemType Directory $env:W",
    "[System.IO.File]::WriteAllText(\"$env:OUTSIDE_DIR\\x.txt\", 'hi')",
    "[IO.File]::AppendAllLines('C:\\out\\x', $lines)",
    "[IO.File]::Copy('C:\\hami\\a', 'C:\\out\\b')",
    "[IO.File]::Move('C:\\out\\a', 'C:\\hami\\b')",
    "[IO.Directory]::CreateDirectory('C:\\out\\d')",
    "$w = [IO.StreamWriter]::new('C:\\out\\x')",
    "$w = New-Object System.IO.StreamWriter('C:\\out\\x')",
    "New-Object -TypeName IO.FileStream -ArgumentList 'C:\\out\\x', 'Create'",
    "$xml.Save('C:\\out\\x.xml')",
    "Invoke-WebRequest https://example.invalid -OutFile C:\\out\\f",
    "Start-Process git -ArgumentList 'log' -RedirectStandardOutput C:\\out\\o.txt -Wait",
    "pwsh -Command \"Set-Content C:\\out\\x 'v'\"",
    "powershell -EncodedCommand UgBlAG0AbwB2AGUALQBJAHQAZQBtACAAQwA6AFwAbwB1AHQA",
    "bash -c 'echo x > /c/out/f'",
    "cmd /c \"echo x > C:\\out\\f\"",
    "Remove-Item \\\\?\\C:\\out\\f",
])
def test_a_powershell_write_outside_the_roots_is_refused(monkeypatch, command):
    monkeypatch.setenv("OUTSIDE_DIR", "C:\\out")
    assert "outside the directories" in decide("PowerShell", command=command)


@pytest.mark.parametrize("command", [
    "Get-ChildItem C:\\ -Force",
    "Copy-Item C:\\out\\a C:\\hami\\b",
    "Write-Output 'a > C:\\out'",
    "Get-Item x 2>$null",
    "Remove-Item C:\\hami\\x -Recurse",
    "New-Item -ItemType Junction -Path C:\\hami\\v -Target C:\\out",
    "New-Item -ItemType Directory -Force $env:TEMP\\q",
    "Remove-Item -Path C:\\hami\\x -ErrorAction SilentlyContinue",
    "$o=\"$env:TEMP\\rev-diff.txt\"; git -C C:\\hami diff a b > $o",
    "$o = 'C:\\hami\\o.txt'; git log > $o",
    "git log *> $null",
    "git log > NUL",
    "$p = Join-Path $env:TEMP 'q'; Remove-Item $p -Recurse",
    "$p = Join-Path -Path C:\\hami -ChildPath 'q'; Remove-Item $p",
    "Set-Content (Join-Path $env:TEMP 'x') \"line $i\"",
    "[IO.File]::WriteAllText(\"$env:TEMP\\x.txt\", \"hi $name\")",
    "[IO.File]::Copy('C:\\out\\a', 'C:\\hami\\b')",
    "$text = [IO.File]::ReadAllText('C:\\out\\a')",
    "Get-Process | Tee-Object -Variable procs",
    "Write-Host @args",
    "New-Item -ItemType File -Name x.txt",
    "Copy-Item C:\\out\\a",
    "'x' > C:\\hami\\f.txt 2>&1",
    "[regex]::Replace($s, 'a', 'b')",
])
def test_a_powershell_command_inside_the_roots_or_read_only_is_allowed(command):
    assert decide("PowerShell", command=command) is None


@pytest.mark.parametrize("tool, command, target", [
    ("Bash", 'rm -rf "$UNKNOWN_DIR"', "$UNKNOWN_DIR"),
    ("Bash", 'x=$(mktemp); echo hi > "$x"', "$x"),
    ("Bash", "echo hi > $(mktemp)", "(...)"),
    ("Bash", 'for f in a b; do rm "$f"; done', "$f"),
    ("Bash", 'f=/c/hami/x; for f in /c/out; do rm -rf "$f"; done', "$f"),
    ("Bash", 'TEMP=$(mktemp -d); rm -rf "$TEMP"', "$TEMP"),
    ("Bash", "read d; mkdir $d", "$d"),
    ("PowerShell", "git diff > $o", "$o"),
    ("PowerShell", "$o = Get-Location; Set-Content $o x", "$o"),
    ("PowerShell", "$o = \"$env:TEMP\\x\" + $suffix; Remove-Item $o", "$o"),
    ("PowerShell", "foreach ($d in 'C:\\x') { Remove-Item $d }", "$d"),
    ("PowerShell", "$d = 'C:\\hami\\x'; foreach ($d in 'C:\\out') { Remove-Item $d }", "$d"),
    ("PowerShell", "Get-ChildItem C:\\out | Remove-Item", "$_"),
    ("PowerShell", "Get-ChildItem | ForEach-Object { Remove-Item $_.FullName }", "$_.FullName"),
    ("PowerShell", "$f = Get-Item x; Remove-Item $f.FullName", "$f.FullName"),
    ("PowerShell", "Remove-Item (Get-Item C:\\out)", "(...)"),
    ("PowerShell", "git diff > (Join-Path $unknown 'x')", "(...)"),
    ("PowerShell", "Copy-Item @params", "$@splatted parameters"),
    ("PowerShell", "[IO.File]::Delete($f.FullName)", "$f.FullName"),
    ("PowerShell", "(Get-Item C:\\out\\f).Delete()", "$(object).Delete()"),
    ("PowerShell", "pwsh -EncodedCommand !!!", "$(encoded command)"),
    ("PowerShell", "Set-Content \"$env:NOT_SET_ANYWHERE\\x\" v", "$env:NOT_SET_ANYWHERE\\x"),
])
def test_a_write_target_the_text_does_not_resolve_is_refused(monkeypatch, tool, command, target):
    monkeypatch.delenv("UNKNOWN_DIR", raising=False)
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    reason = decide(tool, command=command)
    assert reason is not None and "cannot resolve" in reason and f"`{target}`" in reason


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


# Payload decoding


def run_main(monkeypatch, capsys, tmp_path, raw: bytes, encoding: str = "cp950") -> str:
    """The hook's stdout for `raw` piped on stdin, read by a console whose code page is `encoding`."""
    path = tmp_path / "table.json"
    path.write_text(json.dumps(table()), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding=encoding))
    assert hook.main(["--table", str(path)]) == 0
    return capsys.readouterr().out


# "\u4e2d" ends in the byte 0xAD, which cp950 pairs with the backslash after it.
CJK_WRITE = {"tool_name": "Write", "cwd": CWD,
             "tool_input": {"file_path": "C:\\hami\\notes.md", "content": "\u4e2d\\x C:\\hami\\x"}}


def test_a_utf8_payload_with_cjk_and_backslashes_is_read_on_a_cp950_console(monkeypatch, capsys, tmp_path):
    raw = json.dumps(CJK_WRITE, ensure_ascii=False).encode("utf-8")
    with pytest.raises(json.JSONDecodeError):  # the console code page's reading: the reported failure
        json.loads(raw.decode("cp950"))
    assert run_main(monkeypatch, capsys, tmp_path, raw) == ""
    outside = dict(CJK_WRITE, tool_input=dict(CJK_WRITE["tool_input"], file_path="C:\\out\\\u4e2d\u6587.md"))
    assert "outside the directories" in run_main(
        monkeypatch, capsys, tmp_path, json.dumps(outside, ensure_ascii=False).encode("utf-8"))


@pytest.mark.parametrize("raw, words", [
    (b'{"tool_name": "Write", "tool_input": {"file_path": "\xff\xfe"}}', "is not UTF-8"),
    (b'{"tool_name": "Write", "tool_input": ', "is not valid JSON"),
    (b'["Write"]', "is not a JSON object"),
])
def test_a_payload_that_cannot_be_read_is_refused_with_its_cause(monkeypatch, capsys, tmp_path, raw, words):
    out = json.loads(run_main(monkeypatch, capsys, tmp_path, raw, encoding="utf-8"))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "the tool call's payload" in reason and words in reason


def test_a_payload_file_is_read_as_utf8(tmp_path, capsys):
    path = tmp_path / "table.json"
    path.write_text(json.dumps(table()), encoding="utf-8")
    payload = tmp_path / "payload.json"
    payload.write_bytes(json.dumps(CJK_WRITE, ensure_ascii=False).encode("utf-8"))
    assert hook.main(["--table", str(path), "--payload", str(payload)]) == 0
    assert capsys.readouterr().out == ""


def test_codex_apply_patch_paths_are_checked():
    patch = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** Move to: ../../../out/a.py\n*** End Patch\n"
    assert "outside the directories" in decide("apply_patch", harness="codex", command=patch)
    assert "`env`" in decide("apply_patch", harness="codex", command="*** Begin Patch\n*** Add File: .env\n+K=V\n")
    assert decide("apply_patch", harness="codex", command="*** Begin Patch\n*** Add File: src/b.py\n+x\n") is None

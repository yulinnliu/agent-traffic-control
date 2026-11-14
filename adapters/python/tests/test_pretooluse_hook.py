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
    "New-Item -ItemType Junction -Path C:\\hami\\v -Target C:\\hami\\w",
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
    ("PowerShell", "Copy-Item @params", "$(splatted parameters)"),
    ("PowerShell", "[IO.File]::Delete($f.FullName)", "$f.FullName"),
    ("PowerShell", "(Get-Item C:\\out\\f).Delete()", "$(object).Delete()"),
    ("PowerShell", "pwsh -EncodedCommand !!!", "$(script read from stdin or not decodable)"),
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



# The review payloads of ATC-0045: each bypass is refused, each common form stays allowed.
REVIEW_BYPASSES = [
    ('PowerShell', 'git diff > C:\\out\\o.txt'),
    ('PowerShell', 'git diff >C:\\out\\o.txt'),
    ('PowerShell', 'git diff >> C:\\out\\o.txt'),
    ('PowerShell', 'git diff 2> C:\\out\\o.txt'),
    ('PowerShell', 'git diff 2>C:\\out\\o.txt'),
    ('PowerShell', 'git diff *> C:\\out\\o.txt'),
    ('PowerShell', 'git diff 3>&1 > C:\\out\\o.txt'),
    ('PowerShell', 'git diff 2>&1 >C:\\out\\o.txt'),
    ('PowerShell', 'git diff 1>C:\\out\\o.txt'),
    ('PowerShell', 'git diff 5>>C:\\out\\o.txt'),
    ('PowerShell', 'git diff > "$env:TEMP\\o.txt"'),
    ('PowerShell', 'git diff > ${env:TEMP}\\o.txt'),
    ('PowerShell', 'git diff > "${env:TEMP}\\o.txt"'),
    ('PowerShell', '$o="$env:TEMP\\hq.txt"; git -C C:\\hami\\repo diff a b > $o'),
    ('PowerShell', 'git diff > ~\\o.txt'),
    ('PowerShell', 'git diff > ~/o.txt'),
    ('PowerShell', 'git diff > $HOME\\o.txt'),
    ('PowerShell', 'git diff > C:\\hami\\..\\x\\o.txt'),
    ('PowerShell', 'git diff > C:\\WS\\..\\X\\o.txt'),
    ('PowerShell', 'git diff > ..\\..\\x\\o.txt'),
    ('PowerShell', 'Set-Location C:\\out; git diff > o.txt'),
    ('PowerShell', 'cd C:\\out; git diff > o.txt'),
    ('PowerShell', 'Push-Location C:\\out; git diff > o.txt'),
    ('PowerShell', 'sl C:\\out; git diff > o.txt'),
    ('PowerShell', 'pushd C:\\out; Out-File o.txt -InputObject 1'),
    ('PowerShell', 'git diff > \\\\server\\share\\o.txt'),
    ('PowerShell', 'git diff > \\\\?\\C:\\out\\o.txt'),
    ('PowerShell', 'git diff > \\\\?\\UNC\\server\\share\\o.txt'),
    ('PowerShell', 'git diff > C:\\Users\\ANDYLI~1\\o.txt'),
    ('PowerShell', 'git diff | Out-File C:\\out\\o.txt'),
    ('PowerShell', 'git diff | Out-File -FilePath C:\\out\\o.txt'),
    ('PowerShell', 'git diff | Out-File -FileP C:\\out\\o.txt'),
    ('PowerShell', 'git diff | Out-File -LiteralPath C:\\out\\o.txt'),
    ('PowerShell', 'git diff | out-file -fi C:\\out\\o.txt'),
    ('PowerShell', 'Set-Content -Path C:\\out\\o.txt -Value 1'),
    ('PowerShell', 'Set-Content C:\\out\\o.txt 1'),
    ('PowerShell', 'sc C:\\out\\o.txt 1'),
    ('PowerShell', 'ac C:\\out\\o.txt 1'),
    ('PowerShell', 'Add-Content -LiteralPath C:\\out\\o.txt 1'),
    ('PowerShell', 'Set-Content -Value 1 -Path C:\\out\\o.txt'),
    ('PowerShell', 'Set-Content -Value 1 C:\\out\\o.txt'),
    ('PowerShell', 'Set-Content -Va 1 C:\\out\\o.txt'),
    ('PowerShell', 'Set-Content -Encoding utf8 C:\\out\\o.txt 1'),
    ('PowerShell', 'Set-Content -NoNewline C:\\out\\o.txt 1'),
    ('PowerShell', 'Set-Content -Force C:\\out\\o.txt 1'),
    ('PowerShell', 'Set-Content -Path:C:\\out\\o.txt -Value 1'),
    ('PowerShell', 'Set-Content -Path C:\\hami\\a.txt,C:\\out\\o.txt -Value 1'),
    ('PowerShell', "Set-Content -Path @('C:\\hami\\a.txt','C:\\out\\o.txt') -Value 1"),
    ('PowerShell', "Set-Content -Path ('C:\\out\\o.txt') -Value 1"),
    ('PowerShell', 'Set-Content -Path ("C:\\out" + "\\o.txt") -Value 1'),
    ('PowerShell', 'Set-Content -Path "C:\\hami\\a.txt", "C:\\out\\o.txt" -Value 1'),
    ('PowerShell', 'git diff | tee C:\\out\\o.txt'),
    ('PowerShell', 'git diff | Tee-Object -FilePath C:\\out\\o.txt'),
    ('PowerShell', 'git diff | Tee-Object -Append C:\\out\\o.txt'),
    ('PowerShell', 'git diff | Tee-Object -LiteralPath C:\\out\\o.txt'),
    ('PowerShell', 'New-Item C:\\out\\o.txt'),
    ('PowerShell', 'New-Item -Path C:\\out -Name o.txt'),
    ('PowerShell', 'New-Item -ItemType File -Path C:\\out\\o.txt -Value hi'),
    ('PowerShell', 'ni C:\\out\\o.txt -Force'),
    ('PowerShell', 'Copy-Item C:\\hami\\a C:\\out\\o.txt'),
    ('PowerShell', 'Copy-Item -Path C:\\hami\\a -Destination C:\\out'),
    ('PowerShell', 'Copy-Item C:\\hami\\a -Destination C:\\out'),
    ('PowerShell', 'Copy-Item -Destination C:\\out C:\\hami\\a'),
    ('PowerShell', 'Copy-Item -Recurse C:\\hami\\a C:\\out'),
    ('PowerShell', 'cp C:\\hami\\a C:\\out'),
    ('PowerShell', 'cpi C:\\hami\\a C:\\out'),
    ('PowerShell', 'Move-Item C:\\hami\\a C:\\out'),
    ('PowerShell', 'mv C:\\hami\\a C:\\out'),
    ('PowerShell', 'move C:\\hami\\a C:\\out'),
    ('PowerShell', 'Copy-Item -Path C:\\hami\\a -Dest C:\\out'),
    ('PowerShell', 'Copy-Item -Path C:\\hami\\a -Container C:\\out'),
    ('PowerShell', "[IO.File]::WriteAllText('C:\\out\\o.txt', 'a')"),
    ('PowerShell', '[System.IO.File]::WriteAllText("C:\\out\\o.txt", "a")'),
    ('PowerShell', "[System.IO.File]::AppendAllText('C:\\out\\o.txt','a')"),
    ('PowerShell', "[io.file]::writeallbytes('C:\\out\\o.txt', $b)"),
    ('PowerShell', "[IO.File]::Copy('C:\\hami\\a', 'C:\\out\\o.txt')"),
    ('PowerShell', "[IO.File]::Copy('C:\\hami\\a', 'C:\\out\\o.txt', $true)"),
    ('PowerShell', "[IO.File]::WriteAllText( 'C:\\out\\o.txt' , 'a' )"),
    ('PowerShell', "[IO.File]::WriteAllText('C:\\out\\o.txt', ('a' + 'b'))"),
    ('PowerShell', "$p='C:\\out\\o.txt'; [IO.File]::WriteAllText($p, 'a')"),
    ('PowerShell', "[System.IO.File]::OpenWrite('C:\\out\\o.txt').Close()"),
    ('PowerShell', "[IO.File]::Create('C:\\out\\o.txt')"),
    ('PowerShell', "[IO.StreamWriter]::new('C:\\out\\o.txt')"),
    ('PowerShell', "New-Object IO.StreamWriter 'C:\\out\\o.txt'"),
    ('PowerShell', "New-Object System.IO.StreamWriter('C:\\out\\o.txt')"),
    ('PowerShell', "New-Object -TypeName System.IO.StreamWriter -ArgumentList 'C:\\out\\o.txt'"),
    ('PowerShell', '[IO.File]::"WriteAllText"(\'C:\\out\\o.txt\',\'a\')'),
    ('PowerShell', "(Get-Item C:\\hami\\a).CopyTo('C:\\out\\o.txt')"),
    ('PowerShell', "[IO.Directory]::CreateDirectory('C:\\out\\d')"),
    ('PowerShell', 'Start-Process git -ArgumentList diff -RedirectStandardOutput C:\\out\\o.txt'),
    ('PowerShell', 'Start-Process git -RedirectStandardError C:\\out\\o.txt'),
    ('PowerShell', 'Start-Process git -RedirectStandardInput C:\\hami\\a -RedirectStandardOutput C:\\out\\o.txt'),
    ('PowerShell', 'Invoke-WebRequest http://a -OutFile C:\\out\\o.txt'),
    ('PowerShell', 'iwr http://a -OutF C:\\out\\o.txt'),
    ('PowerShell', "$d='C:\\out'; $f = Join-Path $d 'o.txt'; 'a' > $f"),
    ('PowerShell', '$d=$env:TEMP; \'a\' > "$d\\o.txt"'),
    ('PowerShell', "$d=$env:TEMP; 'a' > $d\\o.txt"),
    ('PowerShell', '$d=$env:USERPROFILE; \'a\' > "$($d)\\o.txt"'),
    ('PowerShell', '\'a\' > "$($env:TEMP)\\o.txt"'),
    ('PowerShell', "'a' > (Join-Path $env:TEMP o.txt)"),
    ('PowerShell', "'a' > $([IO.Path]::GetTempPath() + 'o.txt')"),
    ('PowerShell', '$x = Get-Item .; \'a\' > "$x\\..\\..\\..\\x.txt"'),
    ('PowerShell', "& { 'a' > C:\\out\\o.txt }"),
    ('PowerShell', "& { param($p) 'a' > $p } C:\\out\\o.txt"),
    ('PowerShell', "$sb = { 'a' > C:\\out\\o.txt }; & $sb"),
    ('PowerShell', '. { Set-Content C:\\out\\o.txt 1 }'),
    ('PowerShell', "1..2 | ForEach-Object { 'a' > C:\\out\\o.txt }"),
    ('PowerShell', "if ($true) { 'a' > C:\\out\\o.txt }"),
    ('PowerShell', 'Invoke-Expression "\'a\' > C:\\out\\o.txt"'),
    ('PowerShell', 'iex "\'a\' > C:\\out\\o.txt"'),
    ('PowerShell', "iex ('''a'' > ' + 'C:\\out\\o.txt')"),
    ('PowerShell', "Invoke-Expression -Command 'Set-Content C:\\out\\o.txt 1'"),
    ('PowerShell', 'powershell -c "\'a\' > C:\\out\\o.txt"'),
    ('PowerShell', 'pwsh -Command "\'a\' > C:\\out\\o.txt"'),
    ('PowerShell', 'pwsh -NoProfile -Command "Set-Content C:\\out\\o.txt 1"'),
    ('PowerShell', 'pwsh.exe -nop -c "\'a\' > C:\\out\\o.txt"'),
    ('PowerShell', "pwsh -Command { 'a' > C:\\out\\o.txt }"),
    ('PowerShell', '& pwsh -c "\'a\' > C:\\out\\o.txt"'),
    ('PowerShell', '& "pwsh" -c "\'a\' > C:\\out\\o.txt"'),
    ('PowerShell', 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe -c "\'a\' > C:\\out\\o.txt"'),
    ('PowerShell', 'pwsh -co "\'a\' > C:\\out\\o.txt"'),
    ('PowerShell', 'pwsh -comm "\'a\' > C:\\out\\o.txt"'),
    ('PowerShell', 'pwsh /c "\'a\' > C:\\out\\o.txt"'),
    ('PowerShell', 'pwsh -ec JwBhACcAIAA+ACAAQwA6AFwAeABcAG8ALgB0AHgAdAA='),
    ('PowerShell', 'bash -c "echo a > /c/out/o.txt"'),
    ('PowerShell', 'cmd /c "echo a > C:\\out\\o.txt"'),
    ('PowerShell', 'cmd /c echo a > C:\\out\\o.txt'),
    ('PowerShell', 'cmd.exe /c "echo a > C:\\out\\o.txt"'),
    ('PowerShell', 'cmd /c "echo a>C:\\out\\o.txt"'),
    ('PowerShell', 'cmd /s /c "echo a > C:\\out\\o.txt"'),
    ('PowerShell', "$s = @'\n'a' > C:\\out\\o.txt\n'@; iex $s"),
    ('PowerShell', '$s = @"\nx\n"@; \'a\' > C:\\out\\o.txt'),
    ('PowerShell', 'Export-Csv -Path C:\\out\\o.csv'),
    ('PowerShell', 'Get-Process | Export-Csv C:\\out\\o.csv'),
    ('PowerShell', 'Start-Transcript C:\\out\\o.txt'),
    ('PowerShell', 'Out-File -InputObject a -FilePath C:\\out\\o.txt'),
    ('PowerShell', 'Out-File -Inp a C:\\out\\o.txt'),
    ('PowerShell', 'Rename-Item C:\\hami\\a -NewName C:\\out\\b'),
    ('PowerShell', 'Expand-Archive a.zip -DestinationPath C:\\out'),
    ('PowerShell', 'Compress-Archive -Path C:\\hami\\a -DestinationPath C:\\out\\a.zip'),
    ('PowerShell', 'git diff ># C:\\out\\o.txt'),
    ('PowerShell', 'Set-Content -Path C:\\out\\o.txt -Va 1 -Confirm:$false'),
    ('PowerShell', 'Set-Content -Path:"C:\\out\\o.txt" -Value 1'),
    ('PowerShell', 'Set-Content -Pa C:\\out\\o.txt -Value 1'),
    ('PowerShell', 'Set-Content -P C:\\out\\o.txt -Value 1'),
    ('PowerShell', 'Add-Content -Value 1 -Path C:\\out\\o.txt'),
    ('PowerShell', 'Clear-Content C:\\out\\o.txt'),
    ('PowerShell', 'Remove-Item C:\\out\\o.txt'),
    ('PowerShell', 'Remove-Item -Recurse -Force C:\\out'),
    ('PowerShell', 'ri C:\\out -r'),
    ('PowerShell', '$env:X=\'C:\\out\'; Set-Content "$env:X\\o.txt" 1'),
    ('PowerShell', "${function:f} = { 'a' > C:\\out\\o.txt }; f"),
    ('PowerShell', "function f { 'a' > C:\\out\\o.txt }; f"),
    ('PowerShell', 'function f($p) { Set-Content $p 1 }; f C:\\out\\o.txt'),
    ('PowerShell', "'a' | Set-Content -Path { 'C:\\out\\o.txt' }"),
    ('PowerShell', 'Get-ChildItem C:\\hami | Copy-Item -Destination C:\\out'),
    ('PowerShell', "'C:\\out\\o.txt' | Out-File C:\\hami\\ok.txt; 'C:\\out\\o.txt' | New-Item"),
    ('PowerShell', "[PSCustomObject]@{Path='C:\\out\\o.txt'} | New-Item"),
    ('PowerShell', "$PSDefaultParameterValues['Out-File:FilePath']='C:\\out\\o.txt'; 'a' | Out-File"),
    ('PowerShell', 'Set-Alias w Set-Content; w C:\\out\\o.txt 1'),
    ('PowerShell', 'New-Alias w Set-Content; w C:\\out\\o.txt 1'),
    ('PowerShell', "$c='Set-Content'; & $c C:\\out\\o.txt 1"),
    ('PowerShell', "& 'Set-Content' C:\\out\\o.txt 1"),
    ('PowerShell', "& ('Set-' + 'Content') C:\\out\\o.txt 1"),
    ('PowerShell', '. Set-Content C:\\out\\o.txt 1'),
    ('PowerShell', 'Microsoft.PowerShell.Management\\Set-Content C:\\out\\o.txt 1'),
    ('PowerShell', 'Microsoft.PowerShell.Utility\\Out-File C:\\out\\o.txt'),
    ('PowerShell', 'Set-Content -Path C:\\hami\\a.txt -Value 1 > C:\\out\\o.txt'),
    ('PowerShell', 'Write-Output a >C:\\out\\o.txt 2>&1'),
    ('PowerShell', '"a">"C:\\out\\o.txt"'),
    ('PowerShell', "'a'>'C:\\out\\o.txt'"),
    ('PowerShell', 'echo a>C:\\out\\o.txt'),
    ('PowerShell', 'Get-Content C:\\hami\\a | Set-Content C:\\out\\o.txt'),
    ('PowerShell', 'Set-Content C:\\hami\\..\\x\\o.txt 1'),
    ('PowerShell', 'Set-Content C:/out/o.txt 1'),
    ('PowerShell', 'Set-Content filesystem::C:\\out\\o.txt 1'),
    ('PowerShell', 'Set-Content Microsoft.PowerShell.Core\\FileSystem::C:\\out\\o.txt 1'),
    ('PowerShell', 'New-PSDrive -Name Q -PSProvider FileSystem -Root C:\\out; Set-Content Q:\\o.txt 1'),
    ('PowerShell', 'subst Q: C:\\out; Set-Content Q:\\o.txt 1'),
    ('PowerShell', 'xcopy C:\\hami\\a C:\\out'),
    ('PowerShell', 'robocopy C:\\hami C:\\out a'),
    ('PowerShell', 'Export-Clixml -Path C:\\out\\o.xml'),
    ('PowerShell', '$null = New-Item C:\\out\\o.txt'),
    ('PowerShell', '[void](New-Item C:\\out\\o.txt)'),
    ('PowerShell', '$r = Set-Content C:\\out\\o.txt 1'),
    ('PowerShell', "$a = 'C:\\hami\\a'; $a = 'C:\\out\\o'; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; $a += '\\..\\..\\x\\o'; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\out\\o'; Set-Content $A 1"),
    ('PowerShell', "$global:a = 'C:\\out\\o'; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; Set-Variable a 'C:\\out\\o'; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; New-Variable a 'C:\\out\\o' -Force; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; sv a 'C:\\out\\o'; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; [ref]$r = [ref]$a; $r.Value = 'C:\\out\\o'; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; Get-Variable a | % { $_.Value = 'C:\\out\\o' }; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; $ExecutionContext.SessionState.PSVariable.Set('a','C:\\out\\o'); Set-Content $a 1"),
    ('PowerShell', "$a, $b = 'C:\\hami\\a', 'C:\\out\\o'; Set-Content $b 1"),
    ('PowerShell', "$a = $b = 'C:\\out\\o'; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; $a = $a -replace 'ws','x'; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; $a = $a.Replace('ws','x'); Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; if (1) { $a = 'C:\\out\\o' }; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; & { $script:a = 'C:\\out\\o' }; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; foreach ($a in 'C:\\out\\o') {}; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; for ($a = 'C:\\out\\o'; $false;) {}; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; Get-Item C:\\out -OutVariable a; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; Get-Item C:\\out -ov a; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; 'C:\\out\\o' | Tee-Object -Variable a; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; Get-ChildItem C:\\out -PipelineVariable a | % { Set-Content $a 1 }"),
    ('PowerShell', "$a = 'C:\\hami\\a'; $null = 'C:\\out\\o' -match '(.*)'; $a = $Matches[1]; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; ($a = 'C:\\out\\o'); Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; [string]$a = 'C:\\out\\o'; Set-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; $a='C:\\out\\o'\nSet-Content $a 1"),
    ('PowerShell', "$a = 'C:\\hami\\a'; Read-Host -Prompt x | Set-Variable a; Set-Content $a 1"),
    ('PowerShell', '$a = \'C:\\hami\\a\'; Clear-Variable a; Set-Content "C:\\out$a" 1'),
    ('PowerShell', '$a = \'C:\\hami\\a\'; Remove-Variable a; Set-Content "C:\\out\\$a" 1'),
    ('PowerShell', '$a = \'C:\\hami\\a\'; rv a; Set-Content "C:\\out\\o$a" 1'),
    ('PowerShell', '$a = \'C:\\hami\\a\'; $a = $null; Set-Content "C:\\out\\o$a" 1'),
    ('PowerShell', '$a = \'C:\\hami\'; Set-Content "$a\\..\\x\\o" 1'),
    ('PowerShell', 'Set-Content "C:\\hami\\$()..\\..\\x\\o" 1'),
    ('PowerShell', '$a = \'C:\\hami\'; Set-Content "$a`\\..\\x\\o" 1'),
    ('PowerShell', "$PWD = 'C:\\hami'; Set-Location C:\\out; Set-Content o 1"),
    ('PowerShell', "$HOME='C:\\hami'; Set-Content ~\\o 1"),
    ('PowerShell', "[Environment]::SetEnvironmentVariable('TEMP','C:\\hami'); Set-Content $env:TEMP\\o 1"),
    ('PowerShell', 'Set-Item env:TEMP C:\\hami; Set-Content $env:TEMP\\o 1'),
    ('PowerShell', "$env:TEMP += '\\..\\..\\..\\..\\..'; Set-Content $env:TEMP\\x\\o 1"),
    ('Bash', 'echo a > /c/out/o.txt'),
    ('Bash', 'echo a >/c/out/o.txt'),
    ('Bash', 'echo a >> /c/out/o.txt'),
    ('Bash', 'echo a &> /c/out/o.txt'),
    ('Bash', 'echo a &>> /c/out/o.txt'),
    ('Bash', 'echo a 2> /c/out/o.txt'),
    ('Bash', 'echo a 2>>/c/out/o.txt'),
    ('Bash', 'echo a >| /c/out/o.txt'),
    ('Bash', 'echo a 1<>/c/out/o.txt'),
    ('Bash', 'echo a <> /c/out/o.txt'),
    ('Bash', 'echo a >& /c/out/o.txt'),
    ('Bash', 'exec 3> /c/out/o.txt'),
    ('Bash', 'exec {fd}> /c/out/o.txt'),
    ('Bash', 'echo a | tee /c/out/o.txt'),
    ('Bash', 'echo a | tee -a /c/out/o.txt'),
    ('Bash', 'echo a | tee /c/hami/a /c/out/o.txt'),
    ('Bash', 'cp /c/hami/a /c/out/o.txt'),
    ('Bash', 'cp -t /c/out /c/hami/a'),
    ('Bash', 'cp --target-directory=/c/out /c/hami/a'),
    ('Bash', 'mv /c/hami/a /c/out'),
    ('Bash', 'install /c/hami/a /c/out/o'),
    ('Bash', 'install -D /c/hami/a /c/out/o'),
    ('Bash', 'install -t /c/out /c/hami/a'),
    ('Bash', 'dd if=/c/hami/a of=/c/out/o'),
    ('Bash', 'sed -i s/a/b/ /c/out/o'),
    ('Bash', 'sed -i.bak s/a/b/ /c/out/o'),
    ('Bash', 'sed --in-place s/a/b/ /c/out/o'),
    ('Bash', 'sed -ie s/a/b/ /c/out/o'),
    ('Bash', 'sed -n -i s/a/b/ /c/out/o'),
    ('Bash', 'sed -E -i s/a/b/ /c/out/o'),
    ('Bash', 'perl -pi -e s/a/b/ /c/out/o'),
    ('Bash', 'echo a > $TMPDIR/o'),
    ('Bash', 'echo a > "$TMPDIR/o"'),
    ('Bash', 'echo a > ${TMPDIR}/o'),
    ('Bash', 'echo a > $TEMP/o'),
    ('Bash', 'echo a > ~/o'),
    ('Bash', 'echo a > "$HOME/o"'),
    ('Bash', 'echo a > /tmp/o'),
    ('Bash', 'echo a > C:/out/o'),
    ('Bash', "echo a > 'C:\\out\\o'"),
    ('Bash', 'echo a > C:\\\\x\\\\o'),
    ('Bash', 'cd /c/out && echo a > o'),
    ('Bash', 'cd /c/out; echo a > o'),
    ('Bash', 'pushd /c/out; echo a > o'),
    ('Bash', '(cd /c/out && echo a > o)'),
    ('Bash', 'cd /c/out && touch o'),
    ('Bash', 'f=/c/out/o; echo a > $f'),
    ('Bash', 'f=/c/hami/o; f=/c/out/o; echo a > $f'),
    ('Bash', 'export f=/c/out/o; echo a > "$f"'),
    ('Bash', 'f=$(mktemp); echo a > $f'),
    ('Bash', 'echo a > $(echo /c/out/o)'),
    ('Bash', 'echo a > `echo /c/out/o`'),
    ('Bash', 'echo a > /c/hami/$(echo ../x/o)'),
    ('Bash', 'eval "echo a > /c/out/o"'),
    ('Bash', 'bash -c "echo a > /c/out/o"'),
    ('Bash', "sh -c 'echo a > /c/out/o'"),
    ('Bash', "bash -lc 'echo a > /c/out/o'"),
    ('Bash', "bash <<< 'echo a > /c/out/o'"),
    ('Bash', "echo 'echo a > /c/out/o' | bash"),
    ('Bash', "bash -s <<'EOF'\necho a > /c/out/o\nEOF"),
    ('Bash', "cat > /c/out/o <<'EOF'\nhi\nEOF"),
    ('Bash', "cat <<'EOF' > /c/out/o\nhi\nEOF"),
    ('Bash', 'powershell -c "\'a\' > C:\\out\\o"'),
    ('Bash', 'pwsh -c "Set-Content C:\\out\\o 1"'),
    ('Bash', 'cmd //c "echo a > C:\\out\\o"'),
    ('Bash', 'cmd /c "echo a > C:\\out\\o"'),
    ('Bash', 'echo a > /c/hami/../x/o'),
    ('Bash', 'echo a > /C/WS/../x/o'),
    ('Bash', 'echo a > //server/share/o'),
    ('Bash', 'echo a > /c/Users/ANDYLI~1/o'),
    ('Bash', 'for f in /c/out/o; do echo a > $f; done'),
    ('Bash', 'echo a > /c/out/o{,}'),
    ('Bash', 'echo a > /c/out/[o]'),
    ('Bash', 'echo a > /c/out/o*'),
    ('Bash', 'touch /c/out/o'),
    ('Bash', 'mkdir -p /c/out/d'),
    ('Bash', 'rm -rf /c/out'),
    ('Bash', 'truncate -s0 /c/out/o'),
    ('Bash', 'rsync -a /c/hami/a /c/out/'),
    ('Bash', 'curl -o /c/out/o http://a'),
    ('Bash', 'curl -so /c/out/o http://a'),
    ('Bash', 'curl --output=/c/out/o http://a'),
    ('Bash', 'wget -O /c/out/o http://a'),
    ('Bash', 'tar -xf a.tar -C /c/out'),
    ('Bash', 'tar -xf a.tar --directory=/c/out'),
    ('Bash', 'unzip a.zip -d /c/out'),
    ('Bash', 'git clone u /c/out/r'),
    ('Bash', 'git worktree add /c/out/w'),
    ('Bash', 'xargs -a f cp -t /c/out'),
    ('Bash', 'find . -exec cp {} /c/out \\;'),
    ('Bash', 'find . -fprint /c/out/o'),
    ('Bash', 'gzip -c a > /c/out/o.gz'),
    ('Bash', 'time echo a > /c/out/o'),
    ('Bash', 'env echo a > /c/out/o'),
    ('Bash', 'nohup echo a > /c/out/o'),
    ('Bash', 'command cp /c/hami/a /c/out'),
    ('Bash', 'sudo cp /c/hami/a /c/out'),
    ('Bash', 'env cp /c/hami/a /c/out'),
    ('Bash', 'xargs cp /c/hami/a /c/out'),
    ('Bash', 'nice cp /c/hami/a /c/out'),
    ('Bash', 'timeout 5 cp /c/hami/a /c/out'),
    ('Bash', '/usr/bin/cp /c/hami/a /c/out'),
    ('Bash', '\\cp /c/hami/a /c/out'),
    ('Bash', "'cp' /c/hami/a /c/out"),
    ('Bash', "c''p /c/hami/a /c/out"),
    ('Bash', '"/usr/bin/tee" /c/out/o'),
    ('Bash', 'echo a >"/c/out/o"'),
    ('Bash', 'echo a>/c/out/o'),
    ('Bash', '{ echo a; } > /c/out/o'),
    ('Bash', 'if true; then echo a; fi > /c/out/o'),
    ('Bash', 'while false; do :; done > /c/out/o'),
    ('Bash', 'f() { echo a > /c/out/o; }; f'),
    ('Bash', 'echo a > /c/out/o &'),
    ('Bash', 'echo a >/c/out/o 2>&1'),
    ('Bash', 'exec > /c/out/o'),
    ('Bash', 'echo a 2>&1 > /c/out/o'),
    ('Bash', 'echo a >/dev/null >/c/out/o'),
    ('Bash', 'printf a | dd of=/c/out/o'),
    ('Bash', 'echo a | sudo tee /c/out/o'),
    ('Bash', 'cp -r /c/hami/a /c/out/'),
    ('Bash', 'mv -t /c/out /c/hami/a'),
    ('Bash', 'cp /c/hami/a /c/hami/b /c/out'),
    ('PowerShell', '"a" >C:\\out\\o.txt'),
    ('PowerShell', 'Write-Output "a">C:\\out\\o.txt'),
    ('PowerShell', 'git log --format="%h">C:\\out\\o.txt'),
    ('PowerShell', 'git diff "HEAD">"$env:TEMP\\o.txt"'),
    ('PowerShell', "$a='C:\\hami\\a'; $script:a='C:\\out\\o'; Set-Content $a 1"),
    ('PowerShell', "$a='C:\\hami\\a'; $global:a='C:\\out\\o'; Set-Content $a 1"),
    ('PowerShell', 'Microsoft.PowerShell.Management\\New-Item C:\\out\\o.txt'),
    ('PowerShell', 'Microsoft.PowerShell.Management\\Copy-Item C:\\hami\\a C:\\out'),
    ('PowerShell', 'Microsoft.PowerShell.Management\\Remove-Item -Recurse C:\\out'),
    ('PowerShell', 'Set-Content FileSystem::C:\\out\\o.txt 1'),
    ('PowerShell', 'Remove-Item FileSystem::C:\\out -Recurse'),
    ('PowerShell', 'robocopy C:\\hami C:\\out /MIR'),
    ('PowerShell', 'cmd /d /c "echo a > C:\\out\\o.txt"'),
    ('PowerShell', 'Copy-Item C:\\hami\\a "C:\\hami\\b", "C:\\out\\o"'),
    ('PowerShell', 'Out-File -FilePath "C:\\hami\\a", "C:\\out\\o"'),
    ('PowerShell', 'Remove-Item "C:\\hami\\a", "C:\\out\\o"'),
    ('PowerShell', 'Remove-Item C:\\hami\\a,C:\\out\\o'),
    ('PowerShell', 'Remove-Item -Path "C:\\hami\\a", "C:\\out\\o"'),
    ('PowerShell', 'Set-Location C:\\out; Remove-Item -Recurse o'),
    ('PowerShell', 'Push-Location ..\\..\\x; Remove-Item -Recurse *'),
    ('Bash', "eval 'rm -rf /c/out'"),
    ('Bash', 'eval "cp /c/hami/a /c/out"'),
    ('Bash', 'robocopy C:/hami C:/out'),
    ('Bash', 'echo a >| /c/out/o'),
    ('Bash', 'cd /c/out && rm -rf o'),
    ('Bash', 'env rm -rf /c/out'),
]

REVIEW_LEGITIMATE = [
    ('PowerShell', 'git -C C:\\hami\\repo diff > "$env:TEMP\\claude\\x.txt"'),
    ('PowerShell', 'git -C C:\\hami\\repo diff > $env:TEMP\\claude\\x.txt'),
    ('PowerShell', 'git diff 2>$null'),
    ('PowerShell', 'git diff 2>&1'),
    ('PowerShell', 'git diff 2>&1 | Select-Object -First 5'),
    ('PowerShell', 'git diff *>&1 | Out-String'),
    ('PowerShell', 'git diff > $null'),
    ('PowerShell', 'git diff | Out-Null'),
    ('PowerShell', 'git diff > NUL'),
    ('PowerShell', '$null = git diff'),
    ('PowerShell', 'git diff > out.txt'),
    ('PowerShell', "Set-Content -Path C:\\hami\\a.txt -Value 'x > y'"),
    ('PowerShell', "Write-Output 'a > b'"),
    ('PowerShell', 'Write-Output "a > b"'),
    ('PowerShell', "if ($a -gt 1) { 'x' }"),
    ('PowerShell', 'Get-ChildItem | Where-Object { $_.Length -gt 1 }'),
    ('PowerShell', 'Get-Content C:\\out\\a.txt | Select-String foo'),
    ('PowerShell', "$o = Join-Path $env:TEMP 'claude\\x.txt'; git diff > $o"),
    ('PowerShell', '$o = "$env:TEMP\\claude\\x.txt"; git diff | Out-File -Encoding utf8 $o'),
    ('PowerShell', "git log --format='%h %s' -5"),
    ('PowerShell', 'Get-Content C:\\out\\a | ForEach-Object { $_ }'),
    ('PowerShell', 'atc test run --scratch s --task ATC-1 -- -q 2>&1 | Select-Object -Last 5'),
    ('PowerShell', '& "C:\\Users\\Andy Liu\\.local\\bin\\atc.exe" verify --task ATC-1 2>&1 | Tee-Object -FilePath "$env:TEMP\\claude\\v.log"'),
    ('PowerShell', '$r = git rev-parse HEAD; Write-Output $r'),
    ('PowerShell', 'Copy-Item C:\\out\\a C:\\hami\\b'),
    ('PowerShell', 'New-Item -ItemType Directory -Force C:\\hami\\d | Out-Null'),
    ('PowerShell', 'python -c "print(1 > 0)"'),
    ('PowerShell', 'git commit -m "a -> b"'),
    ('PowerShell', '$x = 1; $x > 0'),
    ('PowerShell', 'Get-Process | Sort-Object CPU -Descending | Select -First 3'),
    ('PowerShell', 'Remove-Item C:\\hami\\tmp\\a.txt'),
    ('PowerShell', 'foreach ($f in Get-ChildItem C:\\hami) { $f.Name }'),
    ('PowerShell', 'Get-ChildItem C:\\hami -Filter *.py | ForEach-Object { Get-Content $_.FullName | Measure-Object -Line }'),
    ('Bash', 'git diff > /dev/null'),
    ('Bash', 'git diff 2>/dev/null'),
    ('Bash', 'git diff 2>&1 | head'),
    ('Bash', "echo 'a > b'"),
    ('Bash', 'echo "a > b"'),
    ('Bash', 'git commit -m "a -> b"'),
    ('Bash', '[ 1 -gt 0 ] && echo ok'),
    ('Bash', 'git -C "/c/Users/Andy Liu/Hami" log --oneline | head'),
    ('Bash', 'f=$(git rev-parse HEAD); echo $f'),
    ('Bash', "python - <<'EOF'\nprint(1 > 0)\nEOF"),
    ('Bash', "cat > /c/hami/a <<'EOF'\nx > y\nEOF"),
    ('Bash', "awk '$1 > 0' f"),
    ('Bash', "grep -c '>' f"),
    ('Bash', 'echo a > /c/hami/b.txt'),
    ('Bash', 'mktemp'),
]


@pytest.mark.parametrize("tool, command", REVIEW_BYPASSES)
def test_a_review_bypass_is_refused(monkeypatch, tool, command):
    monkeypatch.setenv("TEMP", "C:\\out")  # the review's temporary directory lies outside the roots
    assert decide(tool, command=command, cwd="C:\\hami\\repo") is not None


@pytest.mark.parametrize("tool, command", REVIEW_LEGITIMATE)
def test_a_common_command_stays_allowed(tool, command):
    assert decide(tool, command=command, cwd="C:\\hami\\repo") is None


@pytest.mark.parametrize("tool, command", [
    ("Bash", 'eval "$UNKNOWN_DIR"'),
    ("PowerShell", "$a = 'C:\\out\\o'; if ($x) { $a = 'C:\\hami\\a' }; Set-Content $a 1"),
    ("PowerShell", "Set-Alias w $c; w C:\\out\\o.txt 1"),
    ("PowerShell", "$a = 'C:\\hami\\a'; Set-Content variable:a 'C:\\out\\o'; Set-Content $a 1"),
    ("PowerShell", "Start-Process pwsh -ArgumentList '-c', 'Set-Content C:\\out\\o 1'"),
    ("PowerShell", "Set-Content Out:\\o.txt 1"),
])
def test_what_the_shell_model_cannot_follow_is_refused(monkeypatch, tool, command):
    monkeypatch.delenv("UNKNOWN_DIR", raising=False)
    assert decide(tool, command=command, cwd="C:\\hami\\repo") is not None


def test_cmd_switches_before_c_still_read_its_line():
    assert decide("PowerShell", command='cmd /d /c "echo a > C:\\hami\\o.txt"', cwd="C:\\hami\\repo") is None


@pytest.mark.parametrize("tool, command", [
    ("PowerShell", "Set-Content `\n-Path C:\\out\\o.txt 1"),
    ("PowerShell", "Set-Content -Path `\r\nC:\\out\\o.txt 1"),
    ("Bash", "cp -t/c/out /c/hami/a"),
    ("Bash", "cp -rt /c/out /c/hami/a"),
    ("Bash", "cp -at/c/out /c/hami/a"),
    ("Bash", "mv -t/c/out /c/hami/a"),
    ("Bash", "curl -o/c/out/o https://example.invalid"),
    ("Bash", "curl -sSLo/c/out/o https://example.invalid"),
    ("Bash", "wget -O/c/out/o https://example.invalid"),
    ("Bash", "tar xf a.tar -C/c/out"),
    ("Bash", "unzip -d/c/out a.zip"),
    ("Bash", "git -C /c/out init"),
    ("Bash", "git -C/c/out commit -m x"),
    ("Bash", "git -C /c/out checkout main"),
    ("Bash", "git worktree add -b t /c/out/w"),
    ("Bash", "git --work-tree=/c/out checkout HEAD -- ."),
    ("PowerShell", "git worktree add -B t C:\\out\\w main"),
    ("PowerShell", "New-Item -ItemType Junction -Path C:\\hami\\j -Value C:\\out"),
    ("PowerShell", "New-Item -ItemType SymbolicLink -Path C:\\hami\\l -Target C:\\out"),
    ("PowerShell", "ni -Type HardLink -Path C:\\hami\\h -Target C:\\out\\f"),
    ("PowerShell", "cmd /c mklink /J C:\\hami\\j C:\\out"),
    ("Bash", "ln -s /c/out /c/hami/l"),
])
def test_ordinary_spellings_of_an_outside_write_are_refused(tool, command):
    assert decide(tool, command=command, cwd="C:\\hami\\repo") is not None


@pytest.mark.parametrize("tool, command", [
    ("PowerShell", "Set-Content `\n-Path C:\\hami\\ok.txt `\n-Value 1"),
    ("PowerShell", "git -C C:\\hami\\repo log `\n --oneline"),
    ("Bash", "cp -rt /c/hami/b /c/out/a"),
    ("Bash", "curl -sSLo/c/hami/o https://example.invalid"),
    ("Bash", "git -C /c/out status"),
    ("Bash", "git -C /c/out log --oneline"),
    ("Bash", "git -C /c/hami/repo commit -m x"),
    ("Bash", 'git diff > "$TMPDIR/x"'),
    ("Bash", 'mkdir -p "$TMP/q"'),
    ("PowerShell", "New-Item -ItemType SymbolicLink -Path C:\\hami\\l -Target C:\\hami\\x"),
    ("Bash", "ln -s /c/hami/x /c/hami/l"),
])
def test_ordinary_spellings_inside_the_roots_stay_allowed(monkeypatch, tool, command):
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.delenv("TMP", raising=False)
    assert decide(tool, command=command, cwd="C:\\hami\\repo") is None

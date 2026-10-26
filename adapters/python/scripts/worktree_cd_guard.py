#!/usr/bin/env python3
"""Claude Code PreToolUse hook: refuse a top-level directory change into the worktrees root.

A Bash `cd`/`pushd` outside a subshell, or any PowerShell `cd`/`Set-Location`/
`sl`/`Push-Location`, carries into every later call of the session; a shell
parked inside a managed worktree keeps it busy and `atc worktree cleanup`
stops halfway. A target naming a variable assigned a literal earlier in the
same command is resolved first. Reads the hook payload on stdin and prints a deny decision;
anything it cannot parse is allowed.
"""

from __future__ import annotations

import json
import ntpath
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKTREES = ROOT / "worktrees"

BASH_CD = {"cd", "pushd"}
BASH_PREFIX = {"then", "do", "else", "elif", "if", "while", "until", "!", "{", "time", "builtin", "command"}
PWSH_CD = {"cd", "chdir", "sl", "set-location", "push-location", "pushd"}
PWSH_PATH_PARAMS = {"-path", "-literalpath", "-lp", "-pspath"}
PWSH_VALUE_PARAMS = {
    "-erroraction", "-ea", "-warningaction", "-wa", "-informationaction", "-infa",
    "-errorvariable", "-ev", "-warningvariable", "-wv", "-informationvariable", "-iv",
    "-outvariable", "-ov", "-outbuffer", "-ob", "-pipelinevariable", "-pv", "-stackname",
}
BASH_DECLARE = {"export", "declare", "typeset", "local", "readonly"}

# Lexer marks: EXPAND stands for a `$` that expands, QUOTED opens a PowerShell quoted string.
EXPAND = "\0"
QUOTED = "\1"

HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w-]*)\1")
ASSIGNMENT = re.compile(r"^([A-Za-z_]\w*)(\+?)=")
VARIABLE = re.compile(r"\0(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
PWSH_ASSIGNMENT = re.compile(r"\0([A-Za-z_]\w*) ?([^\s\w]?)= ?")


def bash_commands(text: str) -> list[tuple[int, list[str]]]:
    """Split Bash source into simple commands, each with its subshell depth."""
    commands: list[tuple[int, list[str]]] = []
    words: list[str] = []
    word: list[str] = []
    in_word = False
    depth = 0
    backtick = False
    heredocs: list[str] = []
    i, n = 0, len(text)

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            words.append("".join(word))
            word.clear()
            in_word = False

    def end_command() -> None:
        end_word()
        if words:
            commands.append((depth, words.copy()))
            words.clear()

    while i < n:
        c = text[i]
        if c == "\n":
            end_command()
            i += 1
            for delimiter in heredocs:
                while i < n:
                    eol = text.find("\n", i)
                    line = text[i:] if eol < 0 else text[i:eol]
                    i = n if eol < 0 else eol + 1
                    if line.strip() == delimiter:
                        break
            heredocs.clear()
            continue
        if c == "'":
            end = text.find("'", i + 1)
            end = n if end < 0 else end
            word.append(text[i + 1:end])
            in_word = True
            i = end + 1
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in '$`"\\\n':
                    i += 1
                    word.append(text[i])
                else:
                    word.append(EXPAND if text[i] == "$" else text[i])
                i += 1
            in_word = True
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            # A backslash before an ordinary character stays literal so a
            # Windows path survives; before a space, quote or operator it escapes.
            nxt = text[i + 1]
            word.append(nxt if nxt in " \t'\"\\;&|()<>$`\n" else c + nxt)
            in_word = True
            i += 2
            continue
        if c == "<" and text.startswith("<<", i) and not text.startswith("<<<", i):
            match = HEREDOC.match(text, i)
            if match:
                end_word()
                heredocs.append(match.group(2))
                i = match.end()
                continue
        if c == "#" and not in_word:
            eol = text.find("\n", i)
            i = n if eol < 0 else eol
            continue
        if c == "`":
            if in_word and not backtick:
                word.append(EXPAND)
            end_command()
            backtick = not backtick
            depth = depth + 1 if backtick else max(depth - 1, 0)
            i += 1
            continue
        if c == "(" or (c == "$" and text.startswith("$(", i)):
            if in_word and c == "$":
                word.append(EXPAND)
            end_command()
            depth += 1
            i += 2 if c == "$" else 1
            continue
        if c == ")":
            end_command()
            depth = max(depth - 1, 0)
            i += 1
            continue
        if c in ";&|":
            end_command()
            i += 1
            continue
        if c in " \t\r":
            end_word()
            i += 1
            continue
        word.append(EXPAND if c == "$" else c)
        in_word = True
        i += 1
    end_command()
    return commands


def expand(word: str, variables: dict[str, str], fold: bool = False) -> str:
    """Substitute a leading known variable, then restore every other `$`."""
    match = VARIABLE.match(word)
    if match:
        name = match.group(1) or match.group(2)
        value = variables.get(name.lower() if fold else name)
        if value is not None:
            word = value + word[match.end():]
    return word.replace(EXPAND, "$")


def bash_assign(variables: dict[str, str], words: list[str]) -> None:
    """Record each `NAME=literal` or `NAME+=literal` onto a known NAME; any other assignment forgets NAME."""
    for word in words:
        match = ASSIGNMENT.match(word)
        if not match:
            continue
        name, append, value = match.group(1), match.group(2), word[match.end():]
        if EXPAND in value or "`" in value or (append and name not in variables):
            variables.pop(name, None)
        else:
            variables[name] = variables[name] + value if append else value


def bash_targets(text: str) -> list[str]:
    """Directories a top-level Bash `cd`/`pushd` moves to."""
    targets = []
    variables: dict[str, str] = {}
    for depth, words in bash_commands(text):
        if depth:
            continue
        rest = words
        while rest and rest[0] in BASH_PREFIX:
            rest = rest[1:]
        if rest and rest[0] in BASH_DECLARE:
            bash_assign(variables, rest[1:])
            continue
        if all(ASSIGNMENT.match(w) for w in rest):
            bash_assign(variables, rest)
            continue
        while words and (words[0] in BASH_PREFIX or ASSIGNMENT.match(words[0])):
            words = words[1:]
        if not words or words[0] not in BASH_CD:
            continue
        args = [w for w in words[1:] if not (w.startswith("-") and w != "-")]
        if args and args[0] != "-":
            targets.append(expand(args[0], variables))
    return targets


def pwsh_commands(text: str) -> list[list[str]]:
    """Split PowerShell source into commands; any `cd` persists, so nesting is ignored."""
    commands: list[list[str]] = []
    words: list[str] = []
    word: list[str] = []
    in_word = False
    i, n = 0, len(text)

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            words.append("".join(word))
            word.clear()
            in_word = False

    def end_command() -> None:
        end_word()
        if words:
            commands.append(words.copy())
            words.clear()

    while i < n:
        c = text[i]
        if c == "@" and text[i + 1:i + 2] in ("'", '"') and text[i + 2:i + 3] in ("\n", "\r"):
            close = "\n" + text[i + 1] + "@"
            end = text.find(close, i)
            i = n if end < 0 else end + len(close)
            continue
        if c == "<" and text.startswith("<#", i):
            end = text.find("#>", i)
            i = n if end < 0 else end + 2
            continue
        if c == "#" and not in_word:
            eol = text.find("\n", i)
            i = n if eol < 0 else eol
            continue
        if c == "'":
            word.append(QUOTED)
            i += 1
            while i < n:
                if text[i] == "'":
                    if text[i + 1:i + 2] == "'":
                        word.append("'")
                        i += 2
                        continue
                    break
                word.append(text[i])
                i += 1
            in_word = True
            i += 1
            continue
        if c == '"':
            word.append(QUOTED)
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "`" and i + 1 < n:
                    i += 1
                    word.append(text[i])
                else:
                    word.append(EXPAND if text[i] == "$" else text[i])
                i += 1
            in_word = True
            i += 1
            continue
        if c == "`" and i + 1 < n:
            word.append(text[i + 1])
            in_word = True
            i += 2
            continue
        if c in ";|&(){}\n":
            end_command()
            i += 1
            continue
        if c in " \t\r":
            end_word()
            i += 1
            continue
        word.append(EXPAND if c == "$" else c)
        in_word = True
        i += 1
    end_command()
    return commands


def pwsh_assign(variables: dict[str, str], words: list[str]) -> bool:
    """Handle a `$name = 'literal'` or `"literal"` command; any other assignment forgets the name."""
    joined = " ".join(words)
    match = PWSH_ASSIGNMENT.match(joined)
    if not match:
        return False
    name, value = match.group(1).lower(), joined[match.end():]
    literal = value.startswith(QUOTED) and value.count(QUOTED) == 1 and EXPAND not in value
    if match.group(2) or not literal or not words[-1].endswith(value):
        variables.pop(name, None)
    else:
        variables[name] = value[1:]
    return True


def pwsh_targets(text: str) -> list[str]:
    """Directories a PowerShell location change moves to."""
    targets = []
    variables: dict[str, str] = {}
    for words in pwsh_commands(text):
        if pwsh_assign(variables, words):
            continue
        words = [w.replace(QUOTED, "") for w in words]
        if words[0].lower() not in PWSH_CD:
            continue
        args = iter(words[1:])
        for arg in args:
            lower = arg.lower()
            name, sep, value = lower.partition(":")
            if name in PWSH_PATH_PARAMS:
                path = arg[len(name) + 1:] if sep else next(args, None)
                if path:
                    targets.append(expand(path, variables, fold=True))
                break
            if lower in PWSH_VALUE_PARAMS:
                next(args, None)
                continue
            if arg.startswith("-"):
                continue
            targets.append(expand(arg, variables, fold=True))
            break
    return targets


def windows(path: str) -> str:
    """Rewrite a Bash `/c/...` or `~` path in Windows form."""
    path = path.replace("/", "\\")
    if path.startswith("~"):
        path = str(Path.home()) + path[1:]
    drive = re.match(r"^\\([A-Za-z])(\\|$)", path)
    if drive:
        path = f"{drive.group(1)}:\\{path[3:]}"
    return path


def native(path: str, cwd: str) -> str:
    """Normalise a path, relative ones against `cwd`, to a case-folded absolute Windows path."""
    path = windows(path)
    if not ntpath.isabs(path):
        path = ntpath.join(windows(cwd), path)
    return ntpath.normcase(ntpath.normpath(path))


def within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("\\") + "\\")


def blocked(tool: str, command: str, cwd: str, worktrees: Path = WORKTREES) -> str | None:
    """The first location-change target under `worktrees`, or None."""
    targets = bash_targets(command) if tool == "Bash" else pwsh_targets(command) if tool == "PowerShell" else []
    root = native(str(worktrees), cwd)
    for target in targets:
        if within(native(target, cwd), root):
            return target
    return None


def reason(tool: str, target: str) -> str:
    where = "at the top level of a Bash call" if tool == "Bash" else "in a PowerShell call"
    subshell = (
        ", or wrap the commands that need it in a subshell: `( cd <path> && ... )`"
        if tool == "Bash"
        else "; for commands that must run inside the tree, use the Bash tool with a subshell `( cd <path> && ... )`"
    )
    return (
        f"Refused: changing directory to {target} {where} makes the worktree the session's "
        f"working directory for every later call, which keeps it busy and makes "
        f"`atc worktree cleanup` stop halfway. Run Git as `git -C <path> ...` and pass absolute "
        f"paths{subshell}."
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_name", "")
        command = payload.get("tool_input", {}).get("command", "")
        cwd = payload.get("cwd") or os.getcwd()
        target = blocked(tool, command, cwd)
    except Exception:
        return 0
    if target is not None:
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason(tool, target),
            }
        }, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())

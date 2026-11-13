"""The paths a tool call writes to, as far as its text names them.

Edit/Write/NotebookEdit name one path. Bash and PowerShell are read best-effort: the targets of file-writing
commands (mkdir, rm, cp/mv destinations, New-Item, Remove-Item, Out-File, redirections, `cmd /c rmdir`,
pytest `--basetemp`, .NET file writes, ...). Codex `apply_patch` names its files in the patch headers. Writes
a program makes on its own are not visible. Standard library only.
"""

from __future__ import annotations

import ntpath
import os
import re

import shell_words as g

BASH_ALL = {"mkdir", "rm", "rmdir", "touch", "tee", "truncate", "unlink", "shred"}
BASH_LAST = {"cp", "mv", "ln", "install", "rsync", "scp"}
CMD_ALL = {"rmdir", "rd", "del", "erase", "mkdir", "md"}
CMD_LAST = {"copy", "move", "xcopy", "robocopy"}
PWSH_ALL = {
    "new-item", "ni", "md", "mkdir", "remove-item", "ri", "rm", "del", "erase", "rd", "rmdir",
    "set-content", "sc", "add-content", "ac", "clear-content", "clc", "out-file", "rename-item",
    "rni", "ren", "tee-object", "tee", "export-csv", "epcsv",
}
PWSH_DEST = {"copy-item", "cpi", "copy", "cp", "move-item", "mi", "move", "mv", "expand-archive", "compress-archive"}
PWSH_PATH_PARAMS = {"-path", "-literalpath", "-lp", "-pspath", "-filepath"}
PWSH_DEST_PARAMS = {"-destination", "-destinationpath"}
PWSH_OTHER_VALUE_PARAMS = {
    "-itemtype", "-type", "-target", "-value", "-name", "-newname", "-encoding", "-filter",
    "-include", "-exclude", "-credential", "-stream", "-delimiter", "-width", "-inputobject",
    "-compressionlevel",
}
DOTNET_WRITE = re.compile(r"\]::(Delete|WriteAll\w*|AppendAll\w*|Create\w*|Move|Copy)$", re.IGNORECASE)
CMD_FLAG = re.compile(r"^//?[A-Za-z?]{1,2}$")
PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.MULTILINE)


def long_form(path: str) -> str:
    """Expand 8.3 short names in the longest existing prefix of an absolute path, case-folded."""
    head, tail = path, []
    while not os.path.exists(head):
        head, part = ntpath.split(head)
        if not part:
            return ntpath.normcase(path)
        tail.append(part)
    if os.name == "nt":
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetLongPathNameW(head, buffer, len(buffer)):
            head = buffer.value
    return ntpath.normcase(ntpath.join(head, *reversed(tail)))


def redirects(words: list[str]) -> list[str]:
    """Files a `>` or `>>` redirection writes to."""
    targets = []
    for i, word in enumerate(words):
        cut = word.find(">")
        quote = word.find(g.QUOTED)
        if cut < 0 or (0 <= quote < cut):
            continue
        target = word[cut + 1:].lstrip(">")
        if not target and i + 1 < len(words):
            target = words[i + 1]
        if target and not target.startswith("&"):
            targets.append(target)
    return targets


def basetemp(words: list[str]) -> list[str]:
    targets = []
    for i, word in enumerate(words):
        if word == "--basetemp" and i + 1 < len(words):
            targets.append(words[i + 1])
        elif word.startswith("--basetemp="):
            targets.append(word.split("=", 1)[1])
    return targets


def cmd_writes(words: list[str]) -> list[str]:
    """Targets of a `cmd /c <verb> ...` command."""
    if not words:
        return []
    verb = words[0].lower()
    args = [w for w in words[1:] if not CMD_FLAG.match(w)]
    if verb in CMD_ALL:
        return args
    if verb in CMD_LAST:
        return args[1:2] if verb == "robocopy" else args[-1:]
    if verb == "mklink":
        return args[:1]
    return []


def bash_command_writes(words: list[str]) -> list[str]:
    """Targets a single Bash command writes to, from its words."""
    targets = redirects(words) + basetemp(words)
    verb = words[0]
    if verb.lower() in ("cmd", "cmd.exe") and len(words) > 1 and words[1].lower() in ("/c", "//c", "/k", "//k"):
        return targets + cmd_writes(words[2:])
    args = [w for w in words[1:] if not w.startswith("-") and ">" not in w]
    if verb in BASH_ALL:
        targets += args
    elif verb in BASH_LAST:
        targets += args[-1:]
    elif verb == "tar":
        for i, word in enumerate(words):
            if word == "-C" and i + 1 < len(words):
                targets.append(words[i + 1])
            elif word.startswith("--directory="):
                targets.append(word.split("=", 1)[1])
    elif verb == "unzip":
        for i, word in enumerate(words):
            if word == "-d" and i + 1 < len(words):
                targets.append(words[i + 1])
    elif verb == "dd":
        targets += [w[3:] for w in words if w.startswith("of=")]
    elif verb == "git" and args:
        if args[0] == "clone":
            targets += args[-1:] if len(args) > 2 else []
        elif args[0] == "init":
            targets += args[1:2]
        elif args[:2] == ["worktree", "add"]:
            targets += args[2:3]
    return targets


def bash_writes(text: str) -> list[str]:
    targets = []
    variables: dict[str, str] = {}
    for _depth, words in g.bash_commands(text):
        rest = words
        while rest and rest[0] in g.BASH_PREFIX:
            rest = rest[1:]
        if rest and rest[0] in g.BASH_DECLARE:
            g.bash_assign(variables, rest[1:])
            continue
        if rest and all(g.ASSIGNMENT.match(w) for w in rest):
            g.bash_assign(variables, rest)
            continue
        while rest and g.ASSIGNMENT.match(rest[0]):
            rest = rest[1:]
        if rest:
            targets += [g.expand(w, variables) for w in bash_command_writes(rest)]
    return targets


def pwsh_command_writes(marked: list[str]) -> list[str]:
    """Targets a single PowerShell command writes to; quote marks keep a quoted `>` from counting."""
    targets = [t.replace(g.QUOTED, "") for t in redirects(marked)]
    words = [w.replace(g.QUOTED, "") for w in marked]
    targets += basetemp(words)
    verb = words[0].lower()
    if verb in ("cmd", "cmd.exe") and len(words) > 1 and words[1].lower() in ("/c", "/k"):
        return targets + cmd_writes(words[2:])
    if verb not in PWSH_ALL and verb not in PWSH_DEST:
        return targets
    paths, dests, positional = [], [], []
    args = iter(words[1:])
    for arg in args:
        name, sep, _ = arg.lower().partition(":")
        if name in PWSH_PATH_PARAMS or name in PWSH_DEST_PARAMS:
            value = arg[len(name) + 1:] if sep else next(args, None)
            if value:
                (dests if name in PWSH_DEST_PARAMS else paths).append(value)
        elif name in g.PWSH_VALUE_PARAMS or name in PWSH_OTHER_VALUE_PARAMS:
            if not sep:
                next(args, None)
        elif not arg.startswith("-") and ">" not in arg:
            positional.append(arg)
    if verb in PWSH_ALL:
        return targets + paths + positional
    if dests:
        return targets + dests
    # Without -Destination, the destination is the first positional after -Path, else the second.
    return targets + (positional[:1] if paths else positional[1:2])


def pwsh_writes(text: str, home: str) -> list[str]:
    targets = []
    variables: dict[str, str] = {"home": home}
    dotnet = False
    for words in g.pwsh_commands(text):
        if dotnet:
            dotnet = False
            targets += [g.expand(w.replace(g.QUOTED, "").rstrip(","), variables, fold=True) for w in words]
            continue
        if g.pwsh_assign(variables, words):
            continue
        if DOTNET_WRITE.search(words[-1]):
            dotnet = True
            continue
        targets += [g.expand(t, variables, fold=True) for t in pwsh_command_writes(words)]
    return targets


def patch_writes(patch: str) -> list[str]:
    """Files a Codex `apply_patch` adds, updates, deletes or moves to."""
    return PATCH_FILE.findall(patch)


def resolve(target: str, cwd: str) -> str | None:
    """The case-folded long absolute path a target names, or None when it cannot be told."""
    target = target.replace(g.EXPAND, "$").strip()
    target = re.sub(r"\$env:(\w+)", lambda m: os.environ.get(m.group(1), m.group(0)), target, flags=re.IGNORECASE)
    target = ntpath.expandvars(target)
    # A name such as NUL, $null or an unresolved $VAR is relative, so it resolves under the working directory.
    if not target or target.lower().startswith("/dev/"):
        return None
    if target.startswith("\\\\.\\") or target.startswith("\\\\?\\") or "://" in target:
        return None
    return long_form(g.native(target, cwd))

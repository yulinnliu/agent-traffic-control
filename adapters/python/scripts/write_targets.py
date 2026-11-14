"""The paths a tool call writes to, as far as its text names them.

Edit/Write/NotebookEdit name one path. Bash and PowerShell are read from their text: redirections, the
targets of file-writing commands (mkdir, rm, cp/mv, sed -i, curl -o, New-Item, Remove-Item, Out-File,
Set-Content, Copy-Item/Move-Item, -OutFile, ...), `cmd /c`, nested `bash -c` and `pwsh -Command`, pytest
`--basetemp`, and .NET file writes. Variables assigned earlier in the call, environment variables and
`Join-Path` are substituted; a target still holding a variable, an expression, a command's output or
pipeline input keeps its lexer marks, and `resolve` raises `Unresolved` for it. Codex `apply_patch` names
its files in the patch headers. Writes a program makes on its own are not visible. Standard library only.
"""

from __future__ import annotations

import ntpath
import os
import re
from collections.abc import Callable
from pathlib import Path

import shell_words as g

BASH_ALL = {"mkdir", "rm", "rmdir", "touch", "tee", "truncate", "unlink", "shred"}
BASH_LAST = {"cp", "ln", "install", "rsync", "scp"}
CMD_ALL = {"rmdir", "rd", "del", "erase", "mkdir", "md"}
CMD_LAST = {"copy", "move", "xcopy", "robocopy"}
PWSH_ALL = {"new-item", "ni", "md", "mkdir", "remove-item", "ri", "rm", "del", "erase", "rd", "rmdir",
            "clear-content", "clc"}
PWSH_FIRST = {"set-content", "sc", "add-content", "ac", "out-file", "rename-item", "rni", "ren", "tee-object",
              "tee", "export-csv", "epcsv", "export-clixml", "start-transcript"}
PWSH_DEST = {"copy-item", "cpi", "copy", "cp", "move-item", "mi", "move", "mv", "expand-archive", "compress-archive"}
PWSH_MOVE = {"move-item", "mi", "move", "mv"}
PWSH_PATH_PARAMS = ("-path", "-literalpath", "-pspath", "-filepath")
PWSH_DEST_PARAMS = ("-destination", "-destinationpath")
PWSH_ANY_PARAMS = ("-outfile", "-redirectstandardoutput", "-redirectstandarderror")
PWSH_OTHER_VALUE_PARAMS = (
    "-itemtype", "-type", "-target", "-value", "-name", "-newname", "-encoding", "-filter", "-include",
    "-exclude", "-credential", "-stream", "-delimiter", "-width", "-inputobject", "-compressionlevel",
    "-variable", "-typename", "-argumentlist", "-childpath", "-additionalchildpath",
)
# .NET writes: [System.IO.File]::<method>(...) and friends, by the argument positions they write.
DOTNET_STATIC = re.compile(r"(?:^|[.\[])(File|Directory|FileStream|StreamWriter)\]::(\w+)" + g.PLACEHOLDER.pattern,
                           re.IGNORECASE)
DOTNET_INSTANCE = re.compile(r"\.(Save|CopyTo|MoveTo|Delete)" + g.PLACEHOLDER.pattern + "$", re.IGNORECASE)
DOTNET_FILE_METHODS = {
    "copy": (1,), "move": (0, 1), "replace": (0, 1, 2),
    **{m: (0,) for m in ("delete", "writealltext", "writealllines", "writeallbytes", "appendalltext",
                         "appendalllines", "appendtext", "create", "createtext", "openwrite", "open",
                         "setattributes", "encrypt", "decrypt")},
}
DOTNET_DIRECTORY_METHODS = {"createdirectory": (0,), "delete": (0,), "move": (0, 1)}
DOTNET_STREAMS = ("streamwriter", "filestream")
CMD_FLAG = re.compile(r"^//?[A-Za-z?]{1,2}$")
PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.MULTILINE)
BASH_VARIABLE = re.compile(g.EXPAND + r"(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
PWSH_VARIABLE = re.compile(g.EXPAND + r"(?:\{([^}]+)\}|((?:[A-Za-z_]\w*:)?[A-Za-z_]\w*))")
PWSH_ASSIGNMENT = re.compile(r"^(?:\[[^\]\s]+\])*" + g.EXPAND + r"((?:[A-Za-z_]\w*:)?[A-Za-z_]\w*) ?([^\s\w]?)= ?")
PWSH_SCOPES = ("script:", "global:", "local:", "private:", "using:")
PIPELINE = g.EXPAND + "_"  # the target of a writing cmdlet fed only by the pipeline

Lookup = Callable[[str], "str | None"]


class Unresolved(Exception):
    """A write target the call's text does not determine."""


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


def marked(word: str) -> bool:
    return g.EXPAND in word or g.GROUP in word


# Substitution


def substitute(word: str, lookup: Lookup, pattern: re.Pattern[str] = BASH_VARIABLE,
               groups: dict[int, str] | None = None) -> str:
    """Replace each variable `lookup` knows and each group in `groups`; anything else keeps its marks."""
    def variable(match: re.Match[str]) -> str:
        value = lookup(match.group(1) or match.group(2))
        return match.group(0) if value is None else value

    word = pattern.sub(variable, word)
    if groups:
        word = g.PLACEHOLDER.sub(lambda m: groups.get(int(m.group(1)), m.group(0)), word)
    return word


def bash_lookup(variables: dict[str, str | None], cwd: str) -> Lookup:
    def lookup(name: str) -> str | None:
        if name in variables:
            return variables[name]
        if name == "PWD":
            return cwd or None
        if name == "HOME":
            return os.environ.get("HOME") or str(Path.home())
        return os.environ.get(name)
    return lookup


def pwsh_lookup(variables: dict[str, str | None], home: str, cwd: str) -> Lookup:
    def lookup(name: str) -> str | None:
        lower = name.lower()
        for scope in PWSH_SCOPES:
            if lower.startswith(scope):
                lower = lower[len(scope):]
        if lower in variables:
            return variables[lower]
        if lower.startswith("env:"):
            return os.environ.get(name[4:]) if name[4:] else None
        if lower == "home":
            return home
        if lower == "pwd":
            return cwd or None
        return None
    return lookup


# Redirections and shared options


def redirects(words: list[str], operator: str) -> list[str]:
    """Files a `>` or `>>` redirection writes to; a PowerShell quoted `>` (after QUOTED) does not count."""
    targets = []
    for i, word in enumerate(words):
        cut = word.find(operator)
        quote = word.find(g.QUOTED)
        if cut < 0 or (0 <= quote < cut):
            continue
        target = word[cut + 1:].lstrip(operator)
        if not target and i + 1 < len(words):
            target = words[i + 1]
        if target and not target.startswith("&"):
            targets.append(target)
    return targets


def operands(words: list[str], operator: str) -> list[str]:
    """The words of a command other than its redirections and their targets."""
    kept, skip = [], False
    for word in words:
        if skip:
            skip = False
        elif operator in word and not (0 <= word.find(g.QUOTED) < word.find(operator)):
            skip = word.endswith(operator)
        else:
            kept.append(word)
    return kept


def basetemp(words: list[str]) -> list[str]:
    targets = []
    for i, word in enumerate(words):
        if word == "--basetemp" and i + 1 < len(words):
            targets.append(words[i + 1])
        elif word.startswith("--basetemp="):
            targets.append(word.split("=", 1)[1])
    return targets


def option_values(words: list[str], names: set[str]) -> list[str]:
    """Values of the options `names`, written `-o v`, `--long v` or `--long=v`."""
    values = []
    for i, word in enumerate(words):
        if word in names and i + 1 < len(words):
            values.append(words[i + 1])
        elif word.startswith("--") and word.partition("=")[0] in names and "=" in word:
            values.append(word.partition("=")[2])
    return values


# cmd /c


def cmd_writes(text: str) -> list[str]:
    """Targets of a `cmd /c` command line: redirections and the writing verbs of each `&`/`|` segment."""
    tokens = re.findall(r'"[^"]*"|&&|\|\||[&|]|[^\s"&|]+(?:"[^"]*"[^\s"&|]*)*', text)
    targets: list[str] = []
    segment: list[str] = []
    for token in [*tokens, "&"]:
        if token in ("&", "&&", "|", "||"):
            targets += cmd_segment_writes(segment)
            segment = []
        else:
            segment.append(token.replace('"', ""))
    return targets


def cmd_segment_writes(words: list[str]) -> list[str]:
    if not words:
        return []
    targets = redirects(words, ">")
    words = operands(words, ">")
    verb = words[0].lower() if words else ""
    args = [w for w in words[1:] if not CMD_FLAG.match(w)]
    if verb in CMD_ALL:
        targets += args
    elif verb in CMD_LAST:
        targets += args[1:2] if verb == "robocopy" else args[-1:]
    elif verb == "mklink":
        targets += args[:1]
    return targets


# Bash


def bash_command_writes(words: list[str]) -> list[str]:
    """Targets a single Bash command writes to, from its words."""
    targets = redirects(words, g.REDIR) + basetemp(words)
    words = operands(words, g.REDIR)
    if not words:
        return targets
    verb = g.program(words[0])
    if verb == "cmd" and len(words) > 1 and words[1].lower() in ("/c", "//c", "/k", "//k"):
        rest = words[2:]
        return targets + (cmd_writes(rest[0]) if len(rest) == 1 else cmd_segment_writes(rest))
    args = [w for w in words[1:] if not w.startswith("-")]
    if verb in BASH_ALL:
        targets += args
    elif verb == "install" and "-d" in words:
        targets += args
    elif verb in BASH_LAST or verb == "mv":
        directory = option_values(words, {"-t", "--target-directory"})
        if verb == "mv":  # the sources leave their directory too
            targets += [a for a in args if a not in directory]
        targets += directory or args[-1:]
    elif verb in ("sed", "perl") and any(re.match(r"-[^-]*i|--in-place", w) for w in words[1:]):
        files, script_given, rest = [], verb == "perl", iter(words[1:])
        for word in rest:
            if word in ("-e", "-f", "--expression", "--file"):
                script_given = True
                next(rest, None)
            elif word.startswith(("--expression=", "--file=")):
                script_given = True
            elif not word.startswith("-"):
                files.append(word)
        targets += files if script_given else files[1:]
    elif verb == "curl":
        targets += option_values(words, {"-o", "--output", "--output-dir", "-D", "--dump-header", "-c",
                                         "--cookie-jar"})
        targets += [words[i + 1] for i, w in enumerate(words[:-1]) if re.fullmatch(r"-[a-zA-Z]+o", w)]
    elif verb == "wget":
        targets += option_values(words, {"-O", "--output-document", "-P", "--directory-prefix", "-o",
                                         "--output-file"})
    elif verb == "tar":
        targets += option_values(words, {"-C", "--directory"})
    elif verb == "unzip":
        targets += option_values(words, {"-d"})
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


def bash_assign(variables: dict[str, str | None], words: list[str], lookup: Lookup) -> None:
    """Record each `NAME=value` whose value resolves; any other assignment makes NAME unknown."""
    for word in words:
        match = g.ASSIGNMENT.match(word)
        if not match:
            continue
        name, append = match.group(1), match.group(2)
        value = substitute(word[match.end():], lookup)
        if marked(value) or "`" in value or (append and variables.get(name) is None):
            variables[name] = None
        else:
            variables[name] = (variables[name] or "") + value if append else value


def bash_writes(text: str, cwd: str = "", home: str = "") -> list[str]:
    """Write targets of a Bash call, variables substituted; an unresolved one keeps its marks."""
    targets: list[str] = []
    variables: dict[str, str | None] = {}
    lookup = bash_lookup(variables, cwd)
    for _depth, words in g.bash_commands(text):
        rest = words
        while rest and rest[0] in g.BASH_PREFIX:
            rest = rest[1:]
        if rest and rest[0] in g.BASH_DECLARE:
            bash_assign(variables, rest[1:], lookup)
            continue
        if rest and all(g.ASSIGNMENT.match(w) for w in rest):
            bash_assign(variables, rest, lookup)
            continue
        while rest and g.ASSIGNMENT.match(rest[0]):
            rest = rest[1:]
        if not rest:
            continue
        if rest[0] in ("for", "select") and len(rest) > 1:
            variables[rest[1]] = None
        elif rest[0] in ("read", "unset", "mapfile", "readarray"):
            for name in rest[1:]:
                if re.fullmatch(r"[A-Za-z_]\w*", name):
                    variables[name] = None
        resolved = [substitute(w, lookup) for w in rest]
        script = g.shell_script([g.plain(w) for w in resolved])
        if script is not None:
            targets += nested_writes(script, cwd, home)
            continue
        targets += [substitute(t, lookup) for t in bash_command_writes(rest)]
    return targets


def nested_writes(script: tuple[str, str | None], cwd: str, home: str) -> list[str]:
    dialect, text = script
    if text is None:
        return [g.EXPAND + "(encoded command)"]
    if dialect == "bash":
        return bash_writes(text, cwd, home)
    return pwsh_writes(text, home or str(Path.home()), cwd)


# PowerShell


def pwsh_param(word: str) -> str | None:
    """'path', 'dest', 'any' (writes on every command), 'value' or 'switch' for a `-Name` word, else None.

    PowerShell binds any unambiguous prefix of a parameter name; a prefix of three characters or more
    (the dash included) counts here, path parameters first.
    """
    name = word.lower().partition(":")[0]
    if not name.startswith("-") or len(name) < 2:
        return None
    if name == "-lp":
        return "path"
    for kind, names in (("path", PWSH_PATH_PARAMS), ("dest", PWSH_DEST_PARAMS), ("any", PWSH_ANY_PARAMS),
                        ("value", PWSH_OTHER_VALUE_PARAMS + tuple(sorted(g.PWSH_VALUE_PARAMS)))):
        if any(full == name or (len(name) >= 3 and full.startswith(name)) for full in names):
            return kind
    return "switch"


def pwsh_command_writes(words: list[str]) -> list[str]:
    """Targets a single PowerShell command writes to; quote marks keep a quoted `>` from counting."""
    targets = redirects(words, ">") + basetemp(words)
    words = operands(words, ">")
    if not words:
        return targets
    verb = words[0].replace(g.QUOTED, "").lower()
    if verb in ("cmd", "cmd.exe") and len(words) > 1 and words[1].lower() in ("/c", "/k"):
        rest = [w.replace(g.QUOTED, "") for w in words[2:]]
        return targets + (cmd_writes(rest[0]) if len(rest) == 1 else cmd_segment_writes(rest))
    paths, dests, names, positional = [], [], [], []
    args = iter(words[1:])
    for arg in args:
        kind = pwsh_param(arg)
        if kind is None:
            positional.append(arg)
            continue
        sep = ":" in arg
        value = arg.partition(":")[2] if sep else None
        is_name = kind == "value" and len(arg.partition(":")[0]) >= 3 and "-name".startswith(arg.lower().partition(":")[0])
        if kind in ("path", "dest", "any") or is_name:
            value = value if sep else next(args, None)
            if value is None:
                continue
            {"path": paths, "dest": dests, "any": targets, "value": names}[kind].append(value)
        elif kind == "value" and not sep:
            next(args, None)
    if verb not in PWSH_ALL and verb not in PWSH_FIRST and verb not in PWSH_DEST:
        return targets
    if any(re.match(r"@\w", w) for w in words[1:]):  # splatted parameters: any of them may name a path
        return targets + [g.EXPAND + "@splatted parameters"]
    if verb in PWSH_ALL:
        found = paths + positional
        if verb in ("new-item", "ni") and names:
            found = [f"{p.rstrip(chr(92) + '/')}\\{n}" for p in found for n in names] or names
        return targets + [t.rstrip(",") for t in found] if found else targets + [PIPELINE]
    if verb in PWSH_FIRST:
        if not (paths or positional):
            if verb in ("tee-object", "tee") and any(w.lower().startswith("-var") for w in words):
                return targets
            return targets + [PIPELINE]
        found = list(paths)
        if not found:
            for word in positional:
                found.append(word)
                if not word.endswith(","):
                    break
        return targets + [t.rstrip(",") for t in found]
    # Without -Destination, the destination is the first positional after -Path, else the second, else cwd.
    found = list(dests) or (positional[:1] if paths else positional[1:2]) or ["."]
    if verb in PWSH_MOVE:  # the source leaves its directory too
        found += paths or positional[:1] or [PIPELINE]
    return targets + found


def split_arguments(words: list[str]) -> list[str]:
    """A method call's comma-separated arguments; an argument of several words is an unresolved expression."""
    arguments: list[list[str]] = [[]]
    separator = re.compile(",(?=[" + g.QUOTED + g.EXPAND + g.GROUP + "]|$)")
    for word in words:
        pieces = separator.split(word) if g.QUOTED in word else word.split(",")
        for k, piece in enumerate(pieces):
            if k:
                arguments.append([])
            if piece:
                arguments[-1].append(piece)
    found = []
    for parts in arguments:
        if len(parts) == 1:
            found.append(parts[0])
        elif parts:
            found.append(g.EXPAND + "(" + " ".join(parts) + ")")
    return found


def dotnet_writes(words: list[str], groups: dict[int, list[str]]) -> list[str]:
    """Targets of .NET file writes in a command: static File/Directory methods, streams, `.Save()` and kin."""
    targets = []
    for word in words:
        bare = word.replace(g.QUOTED, "")
        for match in DOTNET_STATIC.finditer(bare):
            kind, method, number = match.group(1).lower(), match.group(2).lower(), int(match.group(3))
            arguments = split_arguments(groups.get(number, []))
            if kind == "file":
                positions = DOTNET_FILE_METHODS.get(method, ())
            elif kind == "directory":
                positions = DOTNET_DIRECTORY_METHODS.get(method, ())
            else:
                positions = (0,) if method == "new" else ()
            targets += [arguments[p] if p < len(arguments) else PIPELINE for p in positions]
        match = DOTNET_INSTANCE.search(bare)
        if match:
            arguments = split_arguments(groups.get(int(match.group(2)), []))
            if match.group(1).lower() == "delete":  # the object's own path, which the text does not tell
                targets.append(g.EXPAND + "(object).Delete()")
            else:
                targets += arguments[:1]
    if words and words[0].replace(g.QUOTED, "").lower() == "new-object":
        rest = [w.replace(g.QUOTED, "") for w in words[1:]]
        for i, word in enumerate(rest):
            if not g.PLACEHOLDER.sub("", word).lower().rstrip(",").endswith(DOTNET_STREAMS):
                continue
            values = [w for w in rest[i + 1:] if not w.startswith("-")]
            group = g.PLACEHOLDER.search(word) or (g.PLACEHOLDER.fullmatch(values[0]) if values else None)
            arguments = split_arguments(groups.get(int(group.group(1)), [])) if group else split_arguments(values)
            targets += arguments[:1] or [PIPELINE]
            break
    return targets


def join_path(words: list[str], resolve_word: Callable[[str], str | None]) -> str | None:
    """`Join-Path <parent> <child>...` when every part resolves."""
    parts: list[str] = []
    rest = iter(words)
    for word in rest:
        if word.startswith("-") and not word.startswith(g.QUOTED):
            continue
        value = resolve_word(word)
        if value is None:
            return None
        parts.append(value)
    if len(parts) < 2:
        return None
    return "\\".join([parts[0].rstrip("\\/")] + [p.strip("\\/") for p in parts[1:]])


def pwsh_value(words: list[str], resolve_word: Callable[[str], str | None]) -> str | None:
    """The path a PowerShell assignment's value names: a string, a variable or `Join-Path` of those."""
    if len(words) == 1 and (words[0].startswith(g.QUOTED) or words[0].startswith(g.EXPAND)
                            or g.PLACEHOLDER.fullmatch(words[0])):
        return resolve_word(words[0])
    if words and words[0].lower() == "join-path":
        return join_path(words[1:], resolve_word)
    return None


def pwsh_writes(text: str, home: str, cwd: str = "") -> list[str]:
    """Write targets of a PowerShell call, variables substituted; an unresolved one keeps its marks."""
    targets: list[str] = []
    variables: dict[str, str | None] = {"home": home}
    lookup = pwsh_lookup(variables, home, cwd)
    members: dict[int, list[str]] = {}
    values: dict[int, str] = {}

    def resolve_word(word: str) -> str | None:
        value = substitute(word, lookup, PWSH_VARIABLE, values)
        if word.count(g.QUOTED) > 1 or marked(value):
            return None
        return value.replace(g.QUOTED, "")

    for words, _background, group in g.pwsh_parse(text):
        if group is not None:
            members.setdefault(group, []).extend(words)
            if words[0].lower() == "join-path":
                value = join_path(words[1:], resolve_word)
                if value is not None:
                    values[group] = value
        joined = " ".join(words)
        match = PWSH_ASSIGNMENT.match(joined)
        if match:
            start, length = match.end(), 0
            for k, word in enumerate(words):
                if length + len(word) >= start:
                    value_words = ([word[start - length:]] if start - length < len(word) else []) + words[k + 1:]
                    break
                length += len(word) + 1
            else:
                value_words = []
            value_words = [w for w in value_words if w]
            found = (pwsh_command_writes(value_words) + dotnet_writes(value_words, members)) if value_words else []
            targets += [substitute(t, lookup, PWSH_VARIABLE, values).replace(g.QUOTED, "") for t in found]
            variables[match.group(1).lower()] = None if match.group(2) else pwsh_value(value_words, resolve_word)
            continue
        plain = [w.replace(g.QUOTED, "") for w in words]
        script = g.shell_script([g.plain(substitute(w, lookup, PWSH_VARIABLE)) for w in words])
        if script is not None:
            targets += nested_writes(script, cwd, home)
            continue
        loop = g.PLACEHOLDER.fullmatch(words[1]) if plain[0].lower() == "foreach" and len(words) > 1 else None
        head = members.get(int(loop.group(1)), []) if loop else []
        if head and PWSH_VARIABLE.fullmatch(head[0]):  # `foreach ($d in ...)` rebinds $d
            variables[head[0][1:].lower()] = None
        found = pwsh_command_writes(words) + dotnet_writes(words, members)
        targets += [substitute(t, lookup, PWSH_VARIABLE, values).replace(g.QUOTED, "") for t in found]
    return targets


def patch_writes(patch: str) -> list[str]:
    """Files a Codex `apply_patch` adds, updates, deletes or moves to."""
    return PATCH_FILE.findall(patch)


def resolve(target: str, cwd: str) -> str | None:
    """The case-folded long absolute path a target names, or None for a device such as NUL or $null.

    Raises Unresolved for a target that still holds a variable, an expression or a command's output.
    """
    target = target.replace(g.QUOTED, "").strip()
    if target.lower() == g.EXPAND + "null":
        return None
    if marked(target):
        raise Unresolved(target)
    target = re.sub(r"%(\w+)%", lambda m: os.environ.get(m.group(1), m.group(0)), target)
    lower = target.lower()
    if not target or lower.startswith("/dev/") or lower in ("nul", "nul:"):
        return None
    if lower.startswith("\\\\?\\unc\\"):
        target = "\\\\" + target[8:]
    elif target.startswith("\\\\?\\"):
        target = target[4:]
    if target.startswith("\\\\.\\") or "://" in target:
        return None
    return long_form(g.native(target, cwd))

"""The paths a tool call writes to, as far as its text names them.

Edit/Write/NotebookEdit name one path; Codex `apply_patch` names its files in the patch headers. A Bash or
PowerShell call is read command by command with a model of the shell: the variables it assigns (per
subshell in Bash), the directories it may be in after `cd`/`Set-Location` (every directory a relative
target could land in), aliases it defines, and code it evaluates from a literal string (`eval`, `iex`,
`bash -c`, `pwsh -Command`, `cmd /c`). Where the model cannot follow the call (a variable bound by a
command, a loop or an indirect rebinding, a computed command name, code read from stdin or a variable,
pipeline input, a location it cannot tell), the target keeps a lexer mark and `resolve` raises
`Unresolved`, so the hook refuses it. Writes a program makes on its own are not visible.
docs/ENFORCEMENT.md § PreToolUse hook lists what is read. Standard library only.
"""

from __future__ import annotations

import ntpath
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

import shell_words as g

BASH_ALL = {"mkdir", "rm", "rmdir", "touch", "tee", "truncate", "unlink", "shred"}
BASH_LAST = {"cp", "ln", "install", "rsync", "scp"}
BASH_CD = {"cd", "pushd"}
BASH_POP = {"popd"}
BASH_FORGET = {"read", "unset", "mapfile", "readarray"}
BASH_SOURCE = {"source", "."}
# Wrappers that run the command after them: name -> options taking a value.
BASH_WRAPPERS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-P", "-u"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "nohup": set(), "time": set(), "command": set(), "exec": set(), "builtin": set(), "setsid": set(),
    "chrt": set(), "taskset": set(), "flock": {"-w", "-E"},
}
CMD_ALL = {"rmdir", "rd", "del", "erase", "mkdir", "md"}
CMD_LAST = {"copy", "move"}
XCOPY_FLAG = re.compile(r"^//?(?:[A-Za-z?]|-Y|[A-Za-z]+:\S*)$", re.IGNORECASE)
PWSH_ALL = {"new-item", "ni", "md", "mkdir", "remove-item", "ri", "rm", "del", "erase", "rd", "rmdir",
            "clear-content", "clc"}
PWSH_FIRST = {"set-content", "sc", "add-content", "ac", "out-file", "rename-item", "rni", "ren", "tee-object",
              "tee", "export-csv", "epcsv", "export-clixml", "start-transcript"}
PWSH_DEST = {"copy-item", "cpi", "copy", "cp", "move-item", "mi", "move", "mv", "expand-archive", "compress-archive"}
PWSH_MOVE = {"move-item", "mi", "move", "mv"}
PWSH_CD = {"cd", "chdir", "sl", "set-location", "push-location", "pushd"}
PWSH_POP = {"pop-location", "popd"}
PWSH_EVAL = {"invoke-expression", "iex"}
PWSH_SET_VARIABLE = {"set-variable", "sv", "new-variable", "nv", "clear-variable", "clv", "remove-variable", "rv"}
PWSH_ALIAS = {"set-alias", "sal", "new-alias", "nal"}
PWSH_INDIRECT = re.compile(r"\[ref\]|psvariable|executioncontext|setenvironmentvariable|\[scriptblock\]::create",
                           re.IGNORECASE)
PWSH_PATH_PARAMS = ("-path", "-literalpath", "-pspath", "-filepath")
PWSH_DEST_PARAMS = ("-destination", "-destinationpath")
PWSH_ANY_PARAMS = ("-outfile", "-redirectstandardoutput", "-redirectstandarderror")
PWSH_VARIABLE_PARAMS = ("-outvariable", "-ov", "-pipelinevariable", "-pv", "-errorvariable", "-ev",
                        "-warningvariable", "-wv", "-informationvariable", "-iv", "-variable")
PWSH_OTHER_VALUE_PARAMS = (
    "-itemtype", "-type", "-target", "-value", "-name", "-newname", "-encoding", "-filter", "-include",
    "-exclude", "-credential", "-stream", "-delimiter", "-width", "-inputobject", "-compressionlevel",
    "-typename", "-argumentlist", "-childpath", "-additionalchildpath", "-stackname", "-command",
    "-scope", "-option", "-description", "-workingdirectory",
)
# Providers whose paths are not files; `Temp:` is the temporary directory.
NON_FILE_DRIVES = {"env", "variable", "function", "alias", "cert", "hkcu", "hklm", "wsman", "registry"}
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
PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.MULTILINE)
BASH_VARIABLE = re.compile(g.EXPAND + r"(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
PWSH_VARIABLE = re.compile(g.EXPAND + r"(?:\{([^}]+)\}|((?:[A-Za-z_]\w*:)?[A-Za-z_]\w*))")
PWSH_ASSIGNMENT = re.compile(r"^(?:\[[^\]\s]+\])*" + g.EXPAND + r"((?:[A-Za-z_]\w*:)?[A-Za-z_]\w*) ?([^\s\w]?)= ?")
PWSH_OPERATORS = {f"-{c}{op}" for op in ("eq", "ne", "gt", "ge", "lt", "le", "like", "notlike", "match", "notmatch",
                                          "contains", "notcontains", "in", "notin", "replace", "split")
                  for c in ("", "c", "i")} | {
    "-join", "-is", "-isnot", "-as", "-and", "-or", "-xor", "-not", "-band", "-bor", "-bxor", "-shl", "-shr", "-f",
    "in", "+", "-", "*", "/", "%", "..", "=", "+=", "-=", "*=", "/=", "%=", "??", "??=", "?"}
PWSH_SCOPES = ("script:", "global:", "local:", "private:", "using:")
MAX_DIRECTORIES = 32

Lookup = Callable[[str], "str | None"]


class Unresolved(Exception):
    """A write target the call's text does not determine."""


def unresolved(what: str) -> str:
    """A target standing for something the text does not tell, shown as `$(what)`."""
    return g.EXPAND + f"({what})"


PIPELINE = g.EXPAND + "_"  # the target of a writing cmdlet fed only by the pipeline


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


def unquote(word: str) -> str:
    return word.replace(g.QUOTED, "")


# The shell model


class Shell:
    """What a call has done so far: variables per scope, possible directories per scope, aliases, taint.

    A Bash scope is the tuple of subshells holding a command; PowerShell keeps one scope. Directories only
    grow: a relative target is checked against every directory the call may be in, None standing for one
    the text does not tell.
    """

    def __init__(self, cwd: str, home: str, pwsh: bool, shadowed: frozenset[str] = frozenset(),
                 tainted: bool = False, cwds: list[str | None] | None = None):
        self.home = home
        self.pwsh = pwsh
        self.variables: dict[tuple[int, ...], dict[str, str | None]] = {(): {}}
        self.directories: dict[tuple[int, ...], list[str | None]] = {(): cwds or [g.native(cwd, cwd) if cwd else None]}
        self.shadowed = shadowed  # names the calling shell assigned: a nested shell cannot tell their value
        self.tainted = tainted  # an indirect rebinding happened: no variable is known any more
        self.aliases: dict[str, str | None] = {}
        self.unknown_alias = False
        self.pushes = 0

    def chain(self, scope: tuple[int, ...]) -> list[tuple[int, ...]]:
        return [scope[:k] for k in range(len(scope), -1, -1)]

    def get(self, scope: tuple[int, ...], name: str) -> str | None:
        key = name.lower() if self.pwsh else name
        if self.tainted:  # an indirect rebinding may have changed any variable, before or after it
            return None
        for level in self.chain(scope):
            values = self.variables.get(level, {})
            if key in values:
                return values[key]
        directories = self.cwds(scope)
        if key in ("pwd", "PWD"):
            return directories[0] if len(directories) == 1 and directories[0] else None
        if self.pwsh and key == "home":
            return self.home
        if self.tainted or key in self.shadowed:
            return None
        if self.pwsh:
            return os.environ.get(name[4:]) if key.startswith("env:") and name[4:] else None
        if name == "HOME":
            return os.environ.get("HOME") or self.home
        return os.environ.get(name)

    def set(self, scope: tuple[int, ...], name: str, value: str | None) -> None:
        self.variables.setdefault(scope, {})[name.lower() if self.pwsh else name] = value

    def assigned(self) -> frozenset[str]:
        return frozenset(k for level in self.variables.values() for k in level) | self.shadowed

    def cwds(self, scope: tuple[int, ...]) -> list[str | None]:
        for level in self.chain(scope):
            if level in self.directories:
                return self.directories[level]
        return [None]

    def change_directory(self, scope: tuple[int, ...], target: str | None) -> None:
        """Add where `cd <target>` may lead from each possible directory; None when the text does not tell."""
        current = list(self.cwds(scope))
        added: list[str | None] = []
        if target is None or marked(target):
            added = [None]
        else:
            for base in current:
                if base is None and not absolute(target):
                    added.append(None)
                else:
                    added.append(g.native(target, base or ""))
        for path in added:
            if path not in current:
                current.append(path)
        if len(current) > MAX_DIRECTORIES:
            current.append(None)
        self.directories[scope] = current

    def lookup(self, scope: tuple[int, ...]) -> Lookup:
        def find(name: str) -> str | None:
            lower = name.lower()
            for prefix in PWSH_SCOPES if self.pwsh else ():
                if lower.startswith(prefix):
                    name = name[len(prefix):]
                    break
            return self.get(scope, name)
        return find


def absolute(target: str) -> bool:
    target = unquote(target)
    if target.startswith("~") or "::" in target or re.match(r"^[A-Za-z][\w.-]*:", target):
        return True
    return ntpath.isabs(g.windows(target))


def device(target: str) -> bool:
    lower = unquote(target).strip().lower()
    return (lower in ("nul", "nul:", g.EXPAND + "null") or lower.startswith("/dev/") or lower.startswith("\\\\.\\")
            or "://" in lower)


def anchor(targets: list[str], directories: list[str | None]) -> list[str]:
    """Each target as written, a relative one once per directory the call may be in."""
    found = []
    for target in targets:
        target = re.sub(r"%(\w+)%", lambda m: os.environ.get(m.group(1), m.group(0)), target)
        if marked(target) and unquote(target).strip().lower() != g.EXPAND + "null":
            found.append(target)
        elif device(target) or absolute(target):
            found.append(target)
        else:
            for base in directories:
                found.append(unresolved("relative path after a directory change") if base is None
                             else g.native(unquote(target), base))
    return found


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


# Redirections and shared options


def redirects(words: list[str], operator: str = g.REDIR) -> list[str]:
    """Files a redirection writes to: `>`, `>>`, `>|`, `>&file`, `&>`; `>&2` and `2>&1` duplicate a stream."""
    targets = []
    for i, word in enumerate(words):
        cut = word.find(operator)
        if cut < 0:
            continue
        target = word[cut + 1:].lstrip(operator + "|")
        duplicate = target.startswith("&")
        target = target[1:] if duplicate else target
        if not target and i + 1 < len(words):
            target = words[i + 1]
        if duplicate and re.fullmatch(r"\d*-?", target):
            continue
        if target.startswith("#"):
            targets.append(unresolved("redirection target " + target))
        elif target:
            targets.append(target)
        else:
            targets.append(unresolved("redirection without a target"))
    return targets


def operands(words: list[str], operator: str = g.REDIR) -> list[str]:
    """The words of a command other than its redirections and their targets."""
    kept, skip = [], False
    for word in words:
        if skip:
            skip = False
        elif operator in word:
            rest = word[word.find(operator) + 1:].lstrip(operator + "|&")
            skip = not rest
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


# cmd, robocopy, xcopy


def cmd_script(words: list[str]) -> str | None:
    """The command line of `cmd [/switches] /c|/k <line>`, None when cmd reads its commands from stdin."""
    for j, word in enumerate(words[1:], 1):
        lower = word.lower()
        if lower in ("/c", "//c", "/k", "//k", "/r", "//r"):
            rest = words[j + 1:]
            return rest[0] if len(rest) == 1 else " ".join(f'"{w}"' if " " in w else w for w in rest)
        if not word.startswith("/"):
            break
    return None


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
            segment.append(token.replace('"', "").replace(">", g.REDIR))
    return targets


def cmd_segment_writes(words: list[str]) -> list[str]:
    if not words:
        return []
    targets = redirects(words)
    words = operands(words)
    if not words:
        return targets
    verb = g.program(words[0])
    if verb == "cmd":
        script = cmd_script(words)
        return targets + ([unresolved("cmd reading its commands from stdin")] if script is None else cmd_writes(script))
    args = [w for w in words[1:] if not re.match(r"^//?[A-Za-z?]", w) or re.match(r"^/[A-Za-z]/", w)]
    if verb in CMD_ALL:
        targets += args
    elif verb in CMD_LAST:
        targets += args[1:] if len(args) > 1 else ["."]
    else:
        targets += copy_tool_writes(words)
    if verb == "mklink":
        targets += args[:1]
    return targets


def copy_tool_writes(words: list[str]) -> list[str]:
    """The destination of a native robocopy or xcopy; its sources and flags follow it."""
    verb = g.program(words[0])
    args = words[1:]
    if verb == "robocopy":
        return args[1:2] or ["."]
    if verb == "xcopy":
        positional = [w for w in args if not XCOPY_FLAG.match(w)]
        return positional[1:2] or ["."]
    return []


# Bash


def unwrap_bash(words: list[str]) -> list[str]:
    """The command a chain of wrappers (env, sudo, nice, timeout, nohup, ...) runs."""
    while words:
        head = g.program(words[0])
        if head in g.BASH_PREFIX or head in ("{", "!"):
            words = words[1:]
            continue
        if head not in BASH_WRAPPERS:
            return words
        valued, rest = BASH_WRAPPERS[head], words[1:]
        while rest and (rest[0].startswith("-") or (head == "env" and g.ASSIGNMENT.match(rest[0]))):
            rest = rest[2:] if rest[0] in valued else rest[1:]
        if head == "timeout" and rest:
            rest = rest[1:]  # the duration
        if head in ("chrt", "taskset", "flock") and rest:
            rest = rest[1:]
        words = rest
    return words


def git_writes(words: list[str]) -> list[str]:
    directory, i = "", 1
    while i < len(words) and words[i].startswith("-"):
        if words[i] == "-C" and i + 1 < len(words):
            directory = words[i + 1] if absolute(words[i + 1]) or not directory else f"{directory}/{words[i + 1]}"
        i += 2 if words[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
    args = [w for w in words[i:] if not w.startswith("-")]
    targets: list[str] = []
    if args[:1] == ["clone"]:
        targets = args[-1:] if len(args) > 2 else []
    elif args[:1] == ["init"]:
        targets = args[1:2]
    elif args[:2] == ["worktree", "add"]:
        targets = args[2:3]
    return [t if not directory or absolute(t) else f"{directory}/{t}" for t in targets]


def find_writes(words: list[str]) -> list[str]:
    """`find` deletes, -fprint files, and the writes of its -exec commands, `{}` standing under a start path."""
    starts, i = [], 1
    while i < len(words) and not words[i].startswith(("-", "(", "!")):
        starts.append(words[i])
        i += 1
    starts = starts or ["."]
    targets: list[str] = []
    while i < len(words):
        word = words[i]
        if word == "-delete":
            targets += starts
        elif word in ("-fprint", "-fprint0", "-fprintf", "-fls") and i + 1 < len(words):
            targets.append(words[i + 1])
        elif word in ("-exec", "-execdir", "-ok", "-okdir"):
            end = next((k for k in range(i + 1, len(words)) if words[k] in (";", "+", "\\;")), len(words))
            inner = words[i + 1:end]
            for start in starts:
                targets += bash_command_writes([w.replace("{}", f"{start}/found") for w in inner])
            i = end
        i += 1
    return targets


def bash_command_writes(words: list[str]) -> list[str]:
    """Targets a single Bash command writes to, from its words (wrappers already removed)."""
    targets = redirects(words) + basetemp(words)
    words = unwrap_bash(operands(words))
    if not words:
        return targets
    verb = g.program(words[0])
    if verb == "cmd":
        script = cmd_script(words)
        return targets + ([unresolved("cmd reading its commands from stdin")] if script is None else cmd_writes(script))
    if verb in ("robocopy", "xcopy"):
        return targets + copy_tool_writes(words)
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
    elif verb == "git":
        targets += git_writes(words)
    elif verb == "find":
        targets += find_writes(words)
    elif verb == "xargs":
        rest = unwrap_bash(xargs_command(words))
        if rest and (bash_command_writes(rest) or g.program(rest[0]) in BASH_ALL | BASH_LAST | {"mv", "cmd"}):
            targets.append(unresolved("xargs input"))
    return targets


def xargs_command(words: list[str]) -> list[str]:
    valued = {"-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s", "-e", "-i", "-l", "--arg-file", "--delimiter",
              "--max-args", "--max-procs", "--max-chars", "--max-lines", "--replace", "--eof"}
    rest = words[1:]
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in valued else rest[1:]
    return rest


def bash_assign(shell: Shell, scope: tuple[int, ...], words: list[str]) -> None:
    """Record each `NAME=value` whose value resolves; any other assignment makes NAME unknown."""
    lookup = shell.lookup(scope)
    for word in words:
        match = g.ASSIGNMENT.match(word)
        if not match:
            continue
        name, append = match.group(1), match.group(2)
        value = substitute(word[match.end():], lookup)
        old = shell.get(scope, name)
        if marked(value) or "`" in value or (append and old is None):
            shell.set(scope, name, None)
        else:
            shell.set(scope, name, old + value if append else value)


def bash_writes(text: str, cwd: str = "", home: str = "", shell: Shell | None = None,
                base: tuple[int, ...] = ()) -> list[str]:
    """Write targets of a Bash call; an unresolved one keeps its marks."""
    shell = shell or Shell(cwd, home or str(Path.home()), pwsh=False)
    targets: list[str] = []
    for inner, words, _background in g.bash_parse(text):
        scope = base + inner
        lookup = shell.lookup(scope)
        rest = words
        while rest and rest[0] in g.BASH_PREFIX:
            rest = rest[1:]
        if rest and rest[0] in g.BASH_DECLARE:
            if any(re.fullmatch(r"-\w*n\w*", w) for w in rest[1:]):  # a nameref rebinds another name
                shell.tainted = True
            bash_assign(shell, scope, rest[1:])
            continue
        if rest and all(g.ASSIGNMENT.match(w) for w in rest):
            bash_assign(shell, scope, rest)
            continue
        while rest and g.ASSIGNMENT.match(rest[0]):
            rest = rest[1:]
        rest = unwrap_bash(rest)
        if not rest:
            continue
        resolved = [substitute(w, lookup) for w in rest]
        plain = [g.plain(w) for w in resolved]
        verb = g.program(rest[0])
        if verb in ("for", "select") and len(rest) > 1:
            shell.set(scope, rest[1], None)
        elif verb in BASH_FORGET:
            for name in rest[1:]:
                if re.fullmatch(r"[A-Za-z_]\w*", name):
                    shell.set(scope, name, None)
        elif verb == "printf" and "-v" in rest:
            shell.set(scope, rest[rest.index("-v") + 1] if rest.index("-v") + 1 < len(rest) else "", None)
        elif verb in BASH_SOURCE and len(rest) > 1:
            shell.tainted = True  # a sourced file may assign anything and change directory
            shell.change_directory(scope, None)
        elif verb in BASH_CD:
            arguments = [w for w in resolved[1:] if not re.fullmatch(r"-[LPe@]+", w)]
            target = arguments[0] if arguments else shell.get(scope, "HOME")
            shell.change_directory(scope, None if target in (None, "-") or target.startswith(("+", "-")) else target)
            if verb == "pushd":
                shell.pushes += 1
        elif verb in BASH_POP:
            if shell.pushes:
                shell.pushes -= 1
            else:
                shell.change_directory(scope, None)
        elif verb == "eval":
            if any(marked(w) for w in resolved[1:]):
                targets.append(unresolved("eval of a string the call does not spell out"))
            else:
                targets += bash_writes(" ".join(plain[1:]), shell=shell, base=scope)
            continue
        script = g.shell_script(plain)
        if script is not None:
            targets += nested_writes(script, shell, scope)
            continue
        found = [substitute(t, lookup) for t in bash_command_writes(rest)]
        targets += anchor(found, shell.cwds(scope))
    return targets


def nested_writes(script: tuple[str, str | None], parent: Shell, scope: tuple[int, ...]) -> list[str]:
    dialect, text = script
    if text is None:
        return [unresolved("script read from stdin or not decodable")]
    if marked(text):
        return [unresolved("script holding values the call does not spell out")]
    child = Shell("", parent.home, pwsh=dialect == "pwsh", shadowed=parent.assigned(), tainted=parent.tainted,
                  cwds=list(parent.cwds(scope)))
    if dialect == "bash":
        return bash_writes(text, shell=child)
    return pwsh_writes(text, parent.home, shell=child)


# PowerShell


def pwsh_param(word: str) -> str | None:
    """'path', 'dest', 'any' (writes on every command), 'variable', 'value' or 'switch' for a `-Name` word.

    PowerShell binds any unambiguous prefix of a parameter name; a prefix of three characters or more
    (the dash included) counts here, path parameters first. None for a word that is not a parameter.
    """
    name = word.lower().partition(":")[0]
    if not name.startswith("-") or len(name) < 2 or re.fullmatch(r"-[\d.]+", name):
        return None
    if name == "-lp":
        return "path"
    for kind, names in (("path", PWSH_PATH_PARAMS), ("dest", PWSH_DEST_PARAMS), ("any", PWSH_ANY_PARAMS),
                        ("variable", PWSH_VARIABLE_PARAMS),
                        ("value", PWSH_OTHER_VALUE_PARAMS + tuple(sorted(g.PWSH_VALUE_PARAMS)))):
        if any(full == name or (len(name) >= 3 and full.startswith(name)) for full in names):
            return kind
    return "switch"


def elements(words: list[str]) -> list[str]:
    """The items of a comma list spread over `words`."""
    return [item for word in words for item in word.split(g.COMMA) if item]


def pwsh_arguments(words: list[str]) -> list[tuple[str, str, list[str]]]:
    """(kind, parameter, items) per argument of a command; a positional argument has kind 'positional'.

    An argument continues over the next word while a comma joins them, so `-Path "a", "b"` is one argument.
    """
    found: list[tuple[str, str, list[str]]] = []
    i = 1
    while i < len(words):
        word = words[i]
        kind = pwsh_param(word)
        if kind is None:
            taken, i = [word], i + 1
        elif kind == "switch":
            found.append(("switch", word.lower(), []))
            i += 1
            continue
        elif ":" in word:
            taken, i = [word.partition(":")[2]], i + 1
        elif i + 1 < len(words) and pwsh_param(words[i + 1]) is None:
            taken, i = [words[i + 1]], i + 2
        else:
            found.append((kind, word.lower(), []))
            i += 1
            continue
        while i < len(words) and (taken[-1].endswith(g.COMMA) or words[i].startswith(g.COMMA)):
            taken.append(words[i])
            i += 1
        found.append((kind or "positional", word.lower().partition(":")[0] if kind else "", elements(taken)))
    return found


def pwsh_command_writes(words: list[str]) -> list[str]:
    """Targets a single PowerShell command writes to, from its words (call operators already removed)."""
    targets = redirects(words) + basetemp([unquote(w) for w in words])
    words = operands(words)
    if not words:
        return targets
    verb = g.program(unquote(words[0]))
    if verb == "cmd":
        script = cmd_script([unquote(w) for w in words])
        return targets + ([unresolved("cmd reading its commands from stdin")] if script is None else cmd_writes(script))
    if verb in ("robocopy", "xcopy"):
        return targets + copy_tool_writes([unquote(w) for w in words])
    arguments = pwsh_arguments(words)
    paths, dests, names, newnames, positional = [], [], [], [], []
    for kind, parameter, items in arguments:
        if kind == "any":
            targets += items
        elif kind == "path":
            paths += items
        elif kind == "dest":
            dests += items
        elif kind == "value" and len(parameter) >= 3 and "-name".startswith(parameter):
            names += items
        elif kind == "value" and len(parameter) >= 4 and "-newname".startswith(parameter):
            newnames += items
        elif kind == "positional":
            positional.append(items)
    if verb not in PWSH_ALL and verb not in PWSH_FIRST and verb not in PWSH_DEST:
        return targets
    if any(re.match(r"@\w", unquote(w)) for w in words[1:]):  # splatted parameters: any of them may name a path
        return targets + [unresolved("splatted parameters")]
    if verb in PWSH_ALL:
        found = paths + [item for items in positional for item in items]
        if verb in ("new-item", "ni") and names:
            found = [f"{p.rstrip(chr(92) + '/')}\\{n}" for p in found for n in names] or names
        return targets + (found or [PIPELINE])
    if verb in PWSH_FIRST:
        found = paths or (positional[0] if positional else [])
        if not found:
            variable = any(kind == "variable" for kind, _, _ in arguments)
            return targets if verb in ("tee-object", "tee") and variable else targets + [PIPELINE]
        return targets + found + [n for n in newnames if re.search(r"[\\/]", unquote(n))]
    # Without -Destination, the destination is the first positional after -Path, else the second, else cwd.
    found = dests or (positional[0] if paths and positional else positional[1] if len(positional) > 1 else ["."])
    if verb in PWSH_MOVE:  # the source leaves its directory too
        found = found + (paths or (positional[0] if positional else [PIPELINE]))
    return targets + found


def split_arguments(words: list[str]) -> list[str]:
    """A method call's comma-separated arguments; an argument of several words is an unresolved expression."""
    arguments: list[list[str]] = [[]]
    for word in words:
        for k, piece in enumerate(word.split(g.COMMA)):
            if k:
                arguments.append([])
            if piece:
                arguments[-1].append(piece)
    found = []
    for parts in arguments:
        if len(parts) == 1:
            found.append(parts[0])
        elif parts:
            found.append(unresolved(" ".join(g.plain(p) for p in parts)))
    return found


def dotnet_writes(words: list[str], groups: dict[int, list[str]]) -> list[str]:
    """Targets of .NET file writes in a command: static File/Directory methods, streams, `.Save()` and kin."""
    targets = []
    for word in words:
        bare = unquote(word)
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
                targets.append(unresolved("object).Delete("))
            else:
                targets += arguments[:1]
    if words and unquote(words[0]).lower() == "new-object":
        rest = [unquote(w) for w in words[1:]]
        for i, word in enumerate(rest):
            if not g.PLACEHOLDER.sub("", word).lower().rstrip(g.COMMA).endswith(DOTNET_STREAMS):
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
    for word in words:
        if word.startswith("-"):
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


def strip_scope(name: str) -> str:
    lower = name.lower()
    for prefix in PWSH_SCOPES:
        if lower.startswith(prefix):
            return name[len(prefix):]
    return name


def pwsh_writes(text: str, home: str, cwd: str = "", shell: Shell | None = None) -> list[str]:
    """Write targets of a PowerShell call; an unresolved one keeps its marks."""
    shell = shell or Shell(cwd, home, pwsh=True)
    scope: tuple[int, ...] = ()
    lookup = shell.lookup(scope)
    targets: list[str] = []
    members: dict[int, list[str]] = {}
    values: dict[int, str] = {}

    def resolve_word(word: str) -> str | None:
        value = substitute(word, lookup, PWSH_VARIABLE, values)
        if word.count(g.QUOTED) > 1 or marked(value) or g.COMMA in value:
            return None
        return unquote(value)

    def add(found: list[str]) -> None:
        resolved = [unquote(substitute(t, lookup, PWSH_VARIABLE, values)) for t in found]
        for target in resolved:
            lower = target.lower()
            if lower.startswith(("env:", "variable:")) or "::" in lower and lower.split("::")[0].endswith(
                    ("environment", "variable")):
                shell.tainted = True  # a write to an environment variable or variable through its provider
        targets.extend(anchor(resolved, shell.cwds(scope)))

    for words, _background, group, blocks in g.pwsh_parse(text):
        if group is not None:
            members.setdefault(group, []).extend(words)
            if words[0].lower() == "join-path":
                value = join_path(words[1:], resolve_word)
                if value is not None:
                    values[group] = value
        joined = " ".join(words)
        if PWSH_INDIRECT.search(joined) or g.program(unquote(words[0])) in ("get-variable", "gv"):
            shell.tainted = True
        # -OutVariable and kin rebind the variable they name.
        for kind, _parameter, items in pwsh_arguments(words):
            if kind == "variable":
                for item in items:
                    shell.set(scope, strip_scope(unquote(item).lstrip("+")), None)
        match = PWSH_ASSIGNMENT.match(joined)
        if match:
            start, length, value_words = match.end(), 0, []
            for k, word in enumerate(words):
                if length + len(word) >= start:
                    value_words = ([word[start - length:]] if start - length < len(word) else []) + words[k + 1:]
                    break
                length += len(word) + 1
            value_words = [w for w in value_words if w]
            if value_words:
                add(pwsh_command_writes(value_words) + dotnet_writes(value_words, members))
            name = strip_scope(match.group(1))
            # A `{...}` block may not run, or runs in a child scope: its assignments leave the name unknown.
            known = not match.group(2) and not blocks
            shell.set(scope, name, pwsh_value(value_words, resolve_word) if known else None)
            continue
        while len(words) > 1 and words[0] in (".", "&"):  # dot-source and call operators
            words = words[1:]
        head = substitute(words[0], lookup, PWSH_VARIABLE, values)
        # A command name held in a group or a variable runs through `&`, which the lexer has already dropped.
        # `$a -gt 1` and `$f in ...` are expressions, not calls.
        rest = operands(words)[1:]
        computed = g.PLACEHOLDER.fullmatch(head) or PWSH_VARIABLE.fullmatch(head)
        if computed and rest and unquote(rest[0]).lower() not in PWSH_OPERATORS:
            targets.append(unresolved("computed command name"))
            continue
        verb = g.program(unquote(head))
        if verb in shell.aliases:
            if shell.aliases[verb] is None:
                targets.append(unresolved(f"alias {verb}"))
                continue
            verb = shell.aliases[verb] or verb
            words = [verb, *words[1:]]
        elif shell.unknown_alias and verb not in PWSH_ALL | PWSH_FIRST | PWSH_DEST:
            targets.append(unresolved(f"command {verb} after an alias the call does not spell out"))
        arguments = pwsh_arguments(words)
        positional = [items for kind, _, items in arguments if kind == "positional"]
        if verb in PWSH_ALIAS:
            name = next((items for kind, p, items in arguments if p.startswith("-n") and kind != "switch"),
                        positional[0] if positional else [])
            value = next((items for kind, p, items in arguments if p.startswith("-va")),
                         positional[1] if len(positional) > 1 else positional[0][1:] if positional else [])
            literal = [resolve_word(w) for w in name + value]
            if len(name) == 1 and len(value) == 1 and None not in literal:
                shell.aliases[literal[0].lower()] = g.program(literal[1])
            else:
                shell.unknown_alias = True
            continue
        if verb in PWSH_SET_VARIABLE:
            name = next((items for kind, p, items in arguments if p.startswith("-n") and kind != "switch"),
                        positional[0] if positional else [])
            literal = [resolve_word(w) for w in name]
            if name and None not in literal:
                for item in literal:
                    shell.set(scope, strip_scope(item), None)
            else:
                shell.tainted = True
        if verb in PWSH_CD:
            path = next((items for kind, _, items in arguments if kind == "path"), positional[0] if positional else [])
            if len(path) == 1 and unquote(path[0]) not in ("-", "+"):
                value = substitute(path[0], lookup, PWSH_VARIABLE, values)
                shell.change_directory(scope, None if marked(value) else unquote(value))
            else:
                shell.change_directory(scope, home if not path else None)
            if verb in ("push-location", "pushd"):
                shell.pushes += 1
            continue
        if verb in PWSH_POP:
            if shell.pushes:
                shell.pushes -= 1
            else:
                shell.change_directory(scope, None)
            continue
        if verb in PWSH_EVAL:
            code = next((items for kind, p, items in arguments if p.startswith("-c")),
                        positional[0] if positional else [])
            literal = resolve_word(code[0]) if len(code) == 1 and code[0].startswith(g.QUOTED) else None
            if literal is None:
                targets.append(unresolved("Invoke-Expression of a string the call does not spell out"))
            else:
                targets += pwsh_writes(literal, home, shell=shell)
            continue
        if verb == "start-process":
            file = next((items for kind, _, items in arguments if kind == "path"), positional[0] if positional else [])
            argument_list = next((items for kind, p, items in arguments if p.startswith("-arg")), [])
            launched = [g.plain(substitute(w, lookup, PWSH_VARIABLE, values)) for w in file + argument_list]
            script = g.shell_script(launched) if file else None
            if script is not None:
                targets += nested_writes(script, shell, scope)
            elif launched and g.program(launched[0]) == "cmd":
                script_line = cmd_script(launched)
                targets += [unresolved("cmd reading its commands from stdin")] if script_line is None \
                    else anchor(cmd_writes(script_line), shell.cwds(scope))
        script = g.shell_script([g.plain(substitute(w, lookup, PWSH_VARIABLE, values)) for w in words])
        if script is not None:
            add(redirects(words))
            targets += nested_writes(script, shell, scope)
            continue
        loop = g.PLACEHOLDER.fullmatch(words[1]) if verb == "foreach" and len(words) > 1 else None
        head_words = members.get(int(loop.group(1)), []) if loop else []
        if head_words and PWSH_VARIABLE.fullmatch(head_words[0]):  # `foreach ($d in ...)` rebinds $d
            shell.set(scope, strip_scope(head_words[0][1:]), None)
        add(pwsh_command_writes([verb, *words[1:]] if verb != g.program(unquote(words[0])) else words)
            + dotnet_writes(words, members))
    return targets


def patch_writes(patch: str) -> list[str]:
    """Files a Codex `apply_patch` adds, updates, deletes or moves to."""
    return PATCH_FILE.findall(patch)


def resolve(target: str, cwd: str) -> str | None:
    """The case-folded long absolute path a target names, or None for a device or a provider that holds no files.

    Raises Unresolved for a target that still holds a variable, an expression or a command's output.
    """
    target = unquote(target).strip()
    if target.lower() == g.EXPAND + "null":
        return None
    if marked(target):
        raise Unresolved(target)
    target = re.sub(r"%(\w+)%", lambda m: os.environ.get(m.group(1), m.group(0)), target)
    if "::" in target:  # a provider-qualified path: FileSystem::C:\x
        provider, _, rest = target.partition("::")
        if not provider.lower().rsplit("\\", 1)[-1].endswith("filesystem"):
            return None
        target = rest
    drive = re.match(r"^([A-Za-z][\w.-]+):", target)
    if drive:  # a PowerShell drive longer than one letter
        name = drive.group(1).lower()
        if name in NON_FILE_DRIVES:
            return None
        if name != "temp":
            raise Unresolved(target)
        target = tempfile.gettempdir() + "\\" + target[drive.end():].lstrip("\\/")
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

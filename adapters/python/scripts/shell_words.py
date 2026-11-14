"""Shell lexers shared by the PreToolUse hook: Bash and PowerShell source split into simple commands.

Heredocs, quotes, subshells, `$(...)`, backticks, PowerShell here-strings and comments are handled;
a `$` that expands is marked EXPAND, a PowerShell quoted string opens with QUOTED, an unquoted Bash `>`
is REDIR, and a command substitution or PowerShell `(...)` group stands in its command's word as the
placeholder GROUP<n>GROUP while its own commands are listed separately. Standard library only.
"""

from __future__ import annotations

import base64
import binascii
import ntpath
import re
from pathlib import Path

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

# Lexer marks: EXPAND stands for a `$` that expands, QUOTED opens a PowerShell quoted string, GROUP
# brackets the number of a substitution or group, REDIR is an unquoted `>`, COMMA an unquoted PowerShell `,`.
EXPAND = "\0"
QUOTED = "\1"
GROUP = "\2"
REDIR = "\3"
COMMA = "\4"
PLACEHOLDER = re.compile(GROUP + r"(\d+)" + GROUP)


def placeholder(number: int) -> str:
    return f"{GROUP}{number}{GROUP}"


def plain(word: str) -> str:
    """A marked word as the user wrote it, a group shown as `(...)`."""
    word = PLACEHOLDER.sub("(...)", word.replace(QUOTED, ""))
    return word.replace(EXPAND, "$").replace(REDIR, ">").replace(COMMA, ",")


def cd_reason(tool: str, target: str) -> str:
    where = "at the top level of a Bash call" if tool == "Bash" else "in a PowerShell call"
    subshell = (
        ", or wrap the commands that need it in a subshell: `( cd <path> && ... )`"
        if tool == "Bash"
        else "; for commands that must run inside the tree, use the Bash tool with a subshell `( cd <path> && ... )`"
    )
    return (
        f"Refused: changing directory to {target} {where} makes the worktree the session's "
        f"working directory for every later call, which keeps it busy and makes "
        f"`atc task close` stop halfway. Run Git as `git -C <path> ...` and pass absolute "
        f"paths{subshell}. To prove tests import the worktree's code, set `PYTHONPATH` to its "
        f"`packages/*/src` directories instead of entering it."
    )


HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w-]*)\1")
ASSIGNMENT = re.compile(r"^([A-Za-z_]\w*)(\+?)=")
VARIABLE = re.compile(r"\0(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
PWSH_ASSIGNMENT = re.compile(r"(?:\[[^\]\s]+\])*\0([A-Za-z_]\w*) ?([^\s\w]?)= ?")


def bash_commands(text: str) -> list[tuple[int, list[str]]]:
    """Split Bash source into simple commands, each with its subshell depth."""
    return [(len(scope), words) for scope, words, _ in bash_parse(text)]


def bash_simple_commands(text: str) -> list[tuple[int, list[str], bool]]:
    """Split Bash source into simple commands: subshell depth, words, and whether a single `&` backgrounds it."""
    return [(len(scope), words, background) for scope, words, background in bash_parse(text)]


def bash_parse(text: str) -> list[tuple[tuple[int, ...], list[str], bool]]:
    """Bash simple commands: the numbers of the subshells and substitutions holding each, words, backgrounded.

    A command's scope tuple names each enclosing `(...)`, `$(...)` or backtick pair, outermost first, so two
    sibling subshells have different scopes.
    """
    commands: list[tuple[tuple[int, ...], list[str], bool]] = []
    words: list[str] = []
    word: list[str] = []
    in_word = False
    backtick = False
    heredocs: list[str] = []
    # One entry per open `(`: None for a subshell, else the substituting command's words, word and in_word.
    stack: list[tuple[list[str], list[str], bool] | None] = []
    scope: list[int] = []
    groups = 0
    scopes = 0
    i, n = 0, len(text)

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            words.append("".join(word))
            word.clear()
            in_word = False

    def end_command(background: bool = False) -> None:
        end_word()
        if words:
            commands.append((tuple(scope), words.copy(), background))
            words.clear()

    def open_scope() -> None:
        nonlocal scopes
        scopes += 1
        scope.append(scopes)

    def open_substitution() -> None:
        nonlocal in_word
        stack.append((words.copy(), word.copy(), in_word))
        words.clear()
        word.clear()
        in_word = False
        open_scope()

    def close_group() -> None:
        nonlocal in_word, groups
        end_command()
        if scope:
            scope.pop()
        saved = stack.pop() if stack else None
        if saved is not None:
            words[:], word[:] = saved[0], saved[1]
            groups += 1
            word.append(placeholder(groups))
            in_word = True

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
                elif text[i] == "`":
                    word.append(EXPAND + "`")
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
            if backtick:
                close_group()
            else:
                open_substitution()
            backtick = not backtick
            i += 1
            continue
        if c == "$" and text.startswith("$(", i):
            open_substitution()
            i += 2
            continue
        if c == "(":
            end_command()
            stack.append(None)
            open_scope()
            i += 1
            continue
        if c == ")":
            close_group()
            i += 1
            continue
        if c == ">":
            # `>|` and `>&` belong to the redirection; the `>>` of an append is two marks.
            word.append(REDIR + (text[i + 1] if text[i + 1:i + 2] in ("|", "&") else ""))
            in_word = True
            i += 2 if text[i + 1:i + 2] in ("|", "&") else 1
            continue
        if c in ";&|":
            # A lone `&` backgrounds the command before it; `&&`, `>&`, `&>` and `|&` do not.
            lone = c == "&" and text[i + 1:i + 2] not in ("&", ">") and (i == 0 or text[i - 1] not in "&|<>")
            end_command(background=lone)
            i += 1
            continue
        if c in " \t\r":
            end_word()
            i += 1
            continue
        word.append(EXPAND if c == "$" else c)
        in_word = True
        i += 1
    while stack:  # an unclosed group still ends its command's words
        close_group()
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
        if EXPAND in value or GROUP in value or "`" in value or (append and name not in variables):
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
    return [words for words, _ in pwsh_simple_commands(text)]


def pwsh_simple_commands(text: str) -> list[tuple[list[str], bool]]:
    """Split PowerShell source into commands, each with whether a trailing `&` runs it as a background job."""
    return [(words, background) for words, background, _, _ in pwsh_parse(text)]


def pwsh_parse(text: str) -> list[tuple[list[str], bool, int | None, int]]:
    """PowerShell commands, inner ones first: words, backgrounded, the number of the `(...)` group holding it,
    and how many `{...}` blocks hold it.

    A `(...)`, `$(...)` or `@(...)` group stands in its command's word as `placeholder(<number>)`; an
    unquoted `>` is REDIR.
    """
    commands: list[tuple[list[str], bool, int | None, int]] = []
    words: list[str] = []
    word: list[str] = []
    in_word = False
    stack: list[tuple[list[str], list[str], bool, int]] = []
    groups = 0
    blocks = 0
    i, n = 0, len(text)

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            words.append("".join(word))
            word.clear()
            in_word = False

    def end_command(background: bool = False) -> None:
        end_word()
        if words:
            commands.append((words.copy(), background, stack[-1][3] if stack else None, blocks))
            words.clear()

    def close_group() -> None:
        nonlocal in_word
        end_command()
        saved_words, saved_word, _, number = stack.pop()
        words[:], word[:] = saved_words, saved_word
        word.append(placeholder(number))
        in_word = True

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
        if c == "`" and text[i + 1:i + 2] in ("\n", "\r"):  # a line continuation separates words
            end_word()
            i += 3 if text[i + 1:i + 3] == "\r\n" else 2
            continue
        if c == "`" and i + 1 < n:
            word.append(text[i + 1])
            in_word = True
            i += 2
            continue
        if c == "&" and i and text[i - 1] == ">":  # `2>&1`, `*>&1`: a redirection, not a job
            word.append(c)
            i += 1
            continue
        if c == "(":
            if word and word[-1] in (EXPAND, "@") and text[i - 1] in "$@":
                word.pop()
                in_word = bool(word)
            groups += 1
            stack.append((words.copy(), word.copy(), in_word, groups))
            words.clear()
            word.clear()
            in_word = False
            i += 1
            continue
        if c == ")" and stack:
            close_group()
            i += 1
            continue
        if c == ">":
            word.append(REDIR)
            in_word = True
            i += 1
            continue
        if c in ";|&(){}\n":
            # `&` after a command's words starts it as a job; before them it is the call operator.
            end_word()
            end_command(background=c == "&" and bool(words) and text[i + 1:i + 2] != "&")
            blocks = blocks + 1 if c == "{" else max(blocks - 1, 0) if c == "}" else blocks
            i += 1
            continue
        if c in " \t\r":
            end_word()
            i += 1
            continue
        word.append(EXPAND if c == "$" else COMMA if c == "," else c)
        in_word = True
        i += 1
    while stack:  # an unclosed group still ends its command's words
        close_group()
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


SHELLS_BASH = {"bash", "sh", "zsh", "dash"}
SHELLS_PWSH = {"pwsh", "powershell"}


def program(word: str) -> str:
    """A command word by basename, case-folded, without `.exe`."""
    base = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


BASH_OPTION_VALUES = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
PWSH_OPTION_VALUES = ("-executionpolicy", "-ep", "-ex", "-workingdirectory", "-wd", "-outputformat", "-of",
                      "-inputformat", "-if", "-settingsfile", "-configurationname", "-config", "-custompipename",
                      "-version", "-v", "-psconsolefile", "-windowstyle", "-w")


def shell_script(words: list[str]) -> tuple[str, str | None] | None:
    """("bash" or "pwsh", script) of a shell argv of plain words that runs code given in the call itself.

    `bash -c`, `pwsh -Command`, `-EncodedCommand` and Windows PowerShell's positional command give their
    script; a shell reading its script from stdin (`| bash`, `bash <<< ...`, `bash -s`, `pwsh -Command -`)
    or an encoded command that does not decode gives None as the script. A shell running a script file
    gives None as a whole: that is a program's own writing.
    """
    head = program(words[0]) if words else ""
    if len(words) == 2 and words[1].lower() in ("--version", "--help", "-version", "-v", "-help", "-?", "/?"):
        return None
    if head in SHELLS_BASH:
        operands, rest = [], iter(words[1:])
        for word in rest:
            if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", word):
                return "bash", next(rest, None)
            if word in BASH_OPTION_VALUES:
                next(rest, None)
            elif word == "-s":
                return "bash", None
            elif not word.startswith(("-", "+", "<")) and REDIR not in word and ">" not in word:
                operands.append(word)
        return None if operands else ("bash", None)
    if head in SHELLS_PWSH:
        rest = iter(words[1:])
        for word in rest:
            lower = "-" + word[1:].lower() if word.startswith("/") else word.lower()
            name = lower.partition(":")[0]
            if name == "-c" or (len(name) >= 3 and ("-command".startswith(name) or "-commandwithargs".startswith(name))) \
                    or name == "-cwa":
                text = " ".join(rest)
                return "pwsh", None if text in ("", "-") else text
            if name in ("-e", "-ec") or (len(name) >= 3 and "-encodedcommand".startswith(name)):
                try:
                    return "pwsh", base64.b64decode(next(rest), validate=True).decode("utf-16-le")
                except (StopIteration, binascii.Error, UnicodeDecodeError, ValueError):
                    return "pwsh", None
            if name in ("-f", "-file") or (len(name) >= 3 and "-file".startswith(name)):
                value = next(rest, "-")
                return ("pwsh", None) if value == "-" else None
            if lower.startswith("-"):
                if any(name == full or (len(name) >= 3 and full.startswith(name)) for full in PWSH_OPTION_VALUES):
                    next(rest, None)
                continue
            if word.startswith("<") or REDIR in word:
                continue
            # A positional is the command in Windows PowerShell and the script file in pwsh.
            if head == "powershell":
                text = " ".join([word, *rest])
                return "pwsh", None if text == "-" else text
            return ("pwsh", None) if word == "-" else None
        return "pwsh", None
    return None


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

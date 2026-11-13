#!/usr/bin/env python3
"""PreToolUse hook for Claude Code and Codex: deny a tool call the composed hard-deny table forbids.

Reads the hook payload on stdin (or --payload) and the table `atc enforce --hook-out` writes, then prints one
deny decision or nothing. Command rules match every simple command of a Bash or PowerShell call after
normalisation; file rules, the write roots and the hook's own files bind the paths a call writes; a top-level
`cd` into a worktrees root is refused (Claude Code only). An error of the hook itself denies the call.
Standard library only: docs/ENFORCEMENT.md § PreToolUse hook.
"""

from __future__ import annotations

import json
import ntpath
import os
import re
import shlex
import sys
from pathlib import Path

try:  # a helper that cannot load makes main deny every call
    import globs
    import shell_words as g
    import write_targets as w
except Exception as exc:
    IMPORT_ERROR: Exception | None = exc
else:
    IMPORT_ERROR = None

TABLE_VERSION = 1
DEFAULT_TABLE = Path.home() / ".atc" / "enforcement" / "pretooluse.json"
REPAIR = "atc enforce --install-hooks"
FILE_TOOLS = {"Edit", "Write", "NotebookEdit"}
WRITE_MODES = {"edit", "write"}

WRAPPERS = {"command", "exec", "builtin", "nohup", "time", "then", "do", "else", "elif", "if", "while", "until",
            "!", "{", "&", "."}
ENV_VALUE = {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}
GIT_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--exec-path", "--super-prefix"}
UV_VALUE = {
    "--project", "--directory", "--with", "--with-editable", "--with-requirements", "--python", "-p", "--package",
    "--extra", "--group", "--only-group", "--no-group", "--env-file", "--index", "--default-index", "--index-url",
    "--extra-index-url", "-w", "--from", "--cache-dir", "--config-file", "--color", "--script",
}
PYTHON_VALUE = {"-X", "-W", "-c"}
PYTHONS = {"python", "python3", "py", "pythonw"}
MODULE_COMMANDS = {"atc_cli": "atc"}
SHELLS_BASH = {"bash", "sh", "zsh", "dash"}
SHELLS_PWSH = {"pwsh", "powershell"}
PWSH_JOBS = {"start-job", "sajb", "start-threadjob"}
PUSH_VALUE = {"--repo", "-o", "--push-option", "--receive-pack", "--exec"}
PUSH_EVERYTHING = {"--all", "--mirror", "--branches"}


class TableError(Exception):
    pass


def load_table(path: Path) -> dict:
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TableError(f"the hook table {path} cannot be read ({exc.strerror or exc})") from exc
    except ValueError as exc:
        raise TableError(f"the hook table {path} is not valid JSON ({exc})") from exc
    if not isinstance(table, dict) or table.get("version") != TABLE_VERSION:
        raise TableError(f"the hook table {path} is not version {TABLE_VERSION}")
    for key in ("commands", "filesystem", "worktrees", "write_roots", "protected"):
        if not isinstance(table.get(key), list):
            raise TableError(f"the hook table {path} lacks the list {key!r}")
    return table


# Commands


def command_name(word: str) -> str:
    """A command word by basename, case-folded, without `.exe`."""
    base = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def skip_options(words: list[str], valued: set[str]) -> list[str]:
    """Drop leading options, and the value of each option in `valued` written without `=`."""
    i = 0
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in valued else 1
    return words[i:]


def start_process(words: list[str]) -> list[str]:
    """The argv a PowerShell `Start-Process` launches: -FilePath (or the first positional) and -ArgumentList."""
    file, arguments = None, []
    rest = iter(words[1:])
    for word in rest:
        lower = word.lower()
        if lower in ("-filepath", "-file", "-path"):
            file = next(rest, None)
        elif lower in ("-argumentlist", "-args", "-arguments"):
            value = next(rest, "")
            arguments += [part for part in re.split(r"[\s,]+", value) if part]
        elif lower.startswith("-"):
            if lower in ("-workingdirectory", "-verb", "-windowstyle", "-redirectstandardoutput",
                         "-redirectstandarderror", "-redirectstandardinput", "-credential"):
                next(rest, None)
        elif file is None:
            file = word
        else:
            arguments += [part for part in re.split(r"[\s,]+", word) if part]
    return [file, *arguments] if file else []


def normalise(words: list[str], cwd: str, nested: bool = False) -> list[tuple[list[str], str]]:
    """The argvs a simple command runs, each with its directory; argv[0] by `command_name`."""
    words = [word.replace(g.QUOTED, "").replace(g.EXPAND, "$") for word in words]
    i = 0
    while i < len(words):
        word = words[i]
        if g.ASSIGNMENT.match(word) or word in WRAPPERS:
            i += 1
        elif command_name(word) == "env":
            i += 1
            while i < len(words) and (g.ASSIGNMENT.match(words[i]) or words[i].startswith("-")):
                i += 2 if words[i] in ENV_VALUE else 1
        else:
            break
    words = words[i:]
    if not words:
        return []
    head = command_name(words[0])
    if head == "uv" and words[1:2] == ["run"]:
        return normalise(skip_options(words[2:], UV_VALUE), cwd, nested)
    if head == "uvx":
        return normalise(skip_options(words[1:], UV_VALUE), cwd, nested)
    if head in PYTHONS:
        j = 1
        while j < len(words) and words[j].startswith("-") and words[j] != "-m":
            j += 2 if words[j] in PYTHON_VALUE else 1
        if words[j:j + 1] == ["-m"] and j + 1 < len(words):
            module = words[j + 1]
            return normalise([MODULE_COMMANDS.get(module, module), *words[j + 2:]], cwd, nested)
    if head == "cmd" and len(words) > 1 and words[1].lower() in ("/c", "//c", "/k", "//k"):
        return normalise(words[2:], cwd, nested)
    if head == "start-process":
        launched = start_process(words)
        return normalise(launched, cwd, nested) if launched else []
    if not nested and head in SHELLS_BASH:
        for j, word in enumerate(words[1:], 1):
            if re.fullmatch(r"-[a-z]*c[a-z]*", word) and j + 1 < len(words):
                return [hit for _, sub, _ in g.bash_simple_commands(words[j + 1]) for hit in normalise(sub, cwd, True)]
    if not nested and head in SHELLS_PWSH:
        for j, word in enumerate(words[1:], 1):
            if word.lower() in ("-command", "-c") and j + 1 < len(words):
                text = " ".join(words[j + 1:])
                return [hit for sub, _ in g.pwsh_simple_commands(text) for hit in normalise(sub, cwd, True)]
    if head == "git":
        directory, j = cwd, 1
        while j < len(words) and words[j].startswith("-"):
            option = words[j]
            if option == "-C" and j + 1 < len(words):
                directory = ntpath.join(directory, words[j + 1]) if os.name == "nt" else os.path.join(directory, words[j + 1])
            j += 2 if option in GIT_VALUE else 1
        return [(["git", *words[j:]], directory)]
    return [([head, *words[1:]], cwd)]


def has_arg(args: list[str], names: list[str]) -> bool:
    return any(arg == name or arg.startswith(name + "=") for arg in args for name in names)


def git_dir(directory: str) -> Path | None:
    path = Path(g.native(directory, directory) if os.name == "nt" else directory)
    for candidate in (path, *path.parents):
        dot = candidate / ".git"
        if dot.is_dir():
            return dot
        if dot.is_file():
            text = dot.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                target = Path(text[7:].strip())
                return target if target.is_absolute() else candidate / target
            return None
    return None


def current_branch(directory: str) -> str | None:
    """The checked-out branch of `directory`'s repository, "" when detached, None when it cannot be told."""
    try:
        found = git_dir(directory)
        if found is None:
            return None
        head = (found / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else ""


def pushes_to(args: list[str], branches: list[str], directory: str) -> bool:
    """Whether `git push <args>` run in `directory` may update one of `branches`."""
    operands: list[str] = []
    rest = iter(args)
    for arg in rest:
        if arg == "--":
            operands += list(rest)
            break
        if arg.startswith("-"):
            if arg in PUSH_EVERYTHING:
                return True
            if arg in PUSH_VALUE:
                next(rest, None)
            continue
        operands.append(arg)
    specs = operands[1:]

    def checked_out() -> bool:
        branch = current_branch(directory)
        return branch is None or branch in branches

    if not specs:
        return checked_out()
    for spec in specs:
        if "*" in spec:
            return True
        source, colon, destination = spec.lstrip("+").partition(":")
        target = destination if colon and destination else source
        if target == "HEAD" or target.startswith("HEAD~") or target.startswith("HEAD^"):
            if checked_out():
                return True
            continue
        if target.removeprefix("refs/heads/") in branches:
            return True
    return False


def command_rule_hit(rule: dict, argv: list[str], directory: str, background: bool) -> bool:
    prefix = rule["argv_prefix"]
    if len(argv) < len(prefix) or argv[0] != command_name(prefix[0]) or argv[1:len(prefix)] != prefix[1:]:
        return False
    rest = argv[len(prefix):]
    if rule.get("with_args") and not has_arg(rest, rule["with_args"]):
        return False
    if rule.get("unless_args") and has_arg(rest, rule["unless_args"]):
        return False
    if rule.get("background") and not background:
        return False
    if rule.get("push_targets") and not pushes_to(rest, rule["push_targets"], directory):
        return False
    return True


def simple_commands(tool: str, text: str, harness: str) -> list[tuple[list[str], bool]]:
    """(words, backgrounded) per simple command; a Codex shell call is read both as Bash and as PowerShell."""
    found: list[tuple[list[str], bool]] = []
    if tool == "Bash" or harness == "codex":
        found += [(words, background) for _, words, background in g.bash_simple_commands(text)]
    if tool == "PowerShell" or harness == "codex":
        commands = g.pwsh_simple_commands(text)
        job = any(command_name(words[0].replace(g.QUOTED, "")) in PWSH_JOBS for words, _ in commands)
        for words, background in commands:
            detached = command_name(words[0].replace(g.QUOTED, "")) == "start-process" and not any(
                word.replace(g.QUOTED, "").lower() == "-wait" for word in words)
            found.append((words, background or job or detached))
    return found


def command_denial(table: dict, tool: str, text: str, harness: str, cwd: str, background: bool) -> str | None:
    for words, backgrounded in simple_commands(tool, text, harness):
        for argv, directory in normalise(words, cwd):
            for rule in table["commands"]:
                if command_rule_hit(rule, argv, directory, backgrounded or background):
                    return (f"Refused: `{' '.join(argv)}` matches hard-deny rule `{rule['id']}` "
                            f"({rule.get('source', 'policy')}). {rule.get('reason', '')}".rstrip())
    return None


# Paths


def _rule_regex(glob: str) -> re.Pattern[str]:
    """A rule glob as a regex; `<dir>/**` also matches `<dir>` itself, which a recursive delete removes."""
    pattern = globs.path_regex(glob.lower()).pattern
    if glob.endswith("/**"):
        pattern = globs.path_regex(glob[:-3].lower()).pattern[:-1] + "(?:/.*)?$"
    return re.compile(pattern, re.IGNORECASE)


def path_denial(table: dict, target: str, path: str) -> str | None:
    """Why writing `path` (case-folded, long, absolute) is refused, or None."""
    for protected in table["protected"]:
        if g.within(path, protected):
            return (f"Refused: {target} is part of the PreToolUse hook (its table or engine); the Owner "
                    f"changes it with `atc enforce` from their own terminal.")
    slash = path.replace("\\", "/")
    for rule in table["filesystem"]:
        if not WRITE_MODES & set(rule.get("modes", [])):
            continue
        regex = _rule_regex(rule["glob"])
        scopes = rule.get("scope")
        if scopes is None:
            hit = regex.match(slash) is not None
        else:
            hit = False
            for scope in scopes:
                base = scope["base"]
                if path != base and g.within(path, base):
                    parts = path[len(base.rstrip("\\")) + 1:].split("\\")[scope.get("depth", 0):]
                    if parts and regex.match("/".join(parts)):
                        hit = True
                        break
        if hit:
            return (f"Refused: {target} matches hard-deny rule `{rule['id']}` ({rule.get('source', 'policy')}). "
                    f"{rule.get('reason', '')}".rstrip())
    if not any(g.within(path, root) for root in table["write_roots"]):
        return (f"Refused: {target} is outside the directories an agent may write to "
                f"({', '.join(table['write_roots'])}). Put scratch copies, basetemps and test output under "
                f"one of them. Any other location is the Owner's decision: stop and ask.")
    return None


def write_denial(table: dict, targets: list[str], cwd: str) -> str | None:
    for target in targets:
        path = w.resolve(target, cwd)
        if path is not None:
            reason = path_denial(table, target, path)
            if reason:
                return reason
    return None


def cd_denial(table: dict, tool: str, text: str, cwd: str) -> str | None:
    targets = g.bash_targets(text) if tool == "Bash" else g.pwsh_targets(text)
    for target in targets:
        path = g.native(target, cwd)
        if any(g.within(path, root) for root in table["worktrees"]):
            return g.cd_reason(tool, target)
    return None


def decide(payload: dict, table: dict, harness: str) -> str | None:
    """The reason the call is refused, or None to let it through."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    if tool in FILE_TOOLS:
        target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        return write_denial(table, [target], cwd) if target else None
    if tool == "apply_patch":
        patch = tool_input.get("command") or tool_input.get("patch") or tool_input.get("input") or ""
        return write_denial(table, w.patch_writes(patch if isinstance(patch, str) else "\n".join(patch)), cwd)
    if tool not in ("Bash", "PowerShell"):
        return None
    text = tool_input.get("command", "")
    if isinstance(text, list):  # an argv list keeps each word's boundary through POSIX quoting
        text = shlex.join(str(word) for word in text)
    background = bool(tool_input.get("run_in_background"))
    reason = command_denial(table, tool, text, harness, cwd, background)
    if reason:
        return reason
    if harness == "claude":
        reason = cd_denial(table, tool, text, cwd)
        if reason:
            return reason
    targets: list[str] = []
    if tool == "Bash" or harness == "codex":
        targets += w.bash_writes(text)
    if tool == "PowerShell" or harness == "codex":
        targets += w.pwsh_writes(text, str(Path.home()))
    return write_denial(table, targets, cwd)


# Entry point


def deny(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}})


def parse(argv: list[str]) -> dict[str, str]:
    options = {"--harness": "claude", "--table": str(DEFAULT_TABLE), "--payload": ""}
    rest = iter(argv)
    for arg in rest:
        name, sep, value = arg.partition("=")
        if name not in options:
            raise ValueError(f"unknown argument {arg!r}")
        options[name] = value if sep else next(rest, "")
    if options["--harness"] not in ("claude", "codex"):
        raise ValueError(f"--harness must be claude or codex, got {options['--harness']!r}")
    return options


def main(argv: list[str] | None = None) -> int:
    try:
        if IMPORT_ERROR is not None:
            raise IMPORT_ERROR
        options = parse(sys.argv[1:] if argv is None else argv)
        raw = Path(options["--payload"]).read_text(encoding="utf-8") if options["--payload"] else sys.stdin.read()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("the payload is not a JSON object")
        reason = decide(payload, load_table(Path(options["--table"])), options["--harness"])
    except Exception as exc:  # the hook fails closed: its own error refuses the call
        reason = (f"Refused: the PreToolUse hook could not decide ({type(exc).__name__}: {exc}). "
                  f"The Owner repairs it from their own terminal with `{REPAIR}`.")
    if reason is None:
        return 0
    try:
        sys.stdout.write(deny(reason))
        sys.stdout.flush()
    except Exception:
        sys.stderr.write(reason)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

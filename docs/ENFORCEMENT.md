# Enforcement generation

`hard-deny.yaml` states what an agent must be technically unable to do. Prose
does not enforce it; each harness's own configuration does. The generator
reads the policy and writes that configuration, deterministically, so a
project commits the output and its CI diffs it. If the generated file and the
policy disagree, the policy wins and the file is regenerated.

Reference implementation: `adapters/python/scripts/generate_enforcement.py`
(`atc enforce`). Golden outputs: `shared/conformance/generate-enforcement/`.

## Outputs

| File | Consumer | Derived from |
| --- | --- | --- |
| `claude-code/settings.json` | a project's `.claude/settings.json`, key `permissions.deny` | `commands.deny`, `filesystem.deny` |
| `codex/hard-deny.rules` | the block between the markers, spliced at the top of `~/.codex/rules/default.rules` | `commands.deny` |
| `git-hooks/pre-commit` | a project's `core.hooksPath` directory | `filesystem.deny` |

Capability ids (`capabilities.deny`) have no harness-native form; they bind
through review, project CI and the runtime adapter.

A command entry carrying `with_args`, `unless_args`, `push_targets` or
`background` is hook-only: none of these outputs renders it, since a prefix
rule would deny more than it means. The PreToolUse hook below enforces it.

## Filesystem rules

A `filesystem.deny` entry is a bare glob, which denies every access mode, or
an object `{glob, modes, reason}` whose `modes` is a non-empty subset of
`read`, `edit` and `write`. A glob appears once; listing it twice is the
policy error `duplicate-glob`. A permission preset re-grants a denied path
only when that path's modes include `edit` or `write`.

Globs are repository-relative or rooted at `~`. A glob holding a `${...}`
token is refused with `unresolved-token`, naming the entry, and the generator
writes nothing: Claude Code has no form for the token, so the rule would look
like protection without being any. Paths that only the workspace map can name,
such as a project's state directory, come from the machine-local segment
below.

## Machine-local segment

`--workspace <workspace.yaml>` adds a deny the policy cannot carry: every
registered project's `state` root, resolved against `workspace_root` to an
absolute path. `atc enforce` passes `--workspace` itself when the checkout
root holds `workspace.yaml`. A project without `state`, or no map at all,
leaves every output and check exactly as without the option.

| File | Consumer | Entries |
| --- | --- | --- |
| `claude-code/settings.local.json` under `--local-out` | merged into a project's untracked `.claude/settings.local.json`, key `permissions.deny` | `Edit(<state>/**)`, per root in map order |

Paths use Claude Code's absolute form: `/srv/x` becomes `//srv/x`, and a
Windows path `C:/Users/x` becomes `//c/Users/x`. That `//` prefix marks the
segment: policy globs never take it, so a live deny list splits into a policy
segment and a machine-local segment without any other marker. The output
holds absolute paths, so `--local-out` must lie outside `--out`;
`generated/enforcement/` never receives it.

Only `Edit` is rendered. Claude Code checks path rules only for `Edit(path)`
and `Read(path)`; `Write(path)`, `NotebookEdit(path)` and `MultiEdit(path)`
rules are accepted but never match, and an `Edit` deny already covers
`Write` and `NotebookEdit` (see "Write(path), NotebookEdit(path), and
Glob(path) rules never match" in https://code.claude.com/docs/en/permissions).

On Windows, Claude Code loads `.claude/settings.local.json` from the current
working directory, not the repository root, so a session inside a worktree
does not read the copy at the project root. To cover worktrees, merge the
same deny entries into the user-level `~/.claude/settings.json`;
`--live-claude-local` compares against whichever file it is given.

Only the file tools are covered. A command deny is an argv prefix, which
cannot name a path argument in every position, a relative path, or a shell
redirection, so `rm`, `mv` and `>` into a state root stay outside this
segment; the PreToolUse hook covers them from the command text. For the same reason Codex, whose rules are argv prefixes only, gets
no machine-local block.

## Mapping

**Claude Code.** In policy order: each command becomes
`Bash(<argv joined by single spaces>:*)` followed by
`PowerShell(<argv joined by single spaces> *)`. On Windows with Git Bash a
`Bash` deny alone silently turns off the PowerShell tool, and the `PowerShell`
rule both keeps that tool on and binds it to the same deny (see "Bash deny
rules also turn off the PowerShell tool" in
https://code.claude.com/docs/en/tools-reference). Each filesystem rule becomes one
entry per tool, in the order `Read(<glob>)`, `Edit(<glob>)`: `read` renders
`Read`, and `edit` or `write` renders one `Edit`, not repeated when both are
present. Claude Code checks path rules only for `Read(path)` and `Edit(path)`,
and an `Edit` deny covers `Write` and `NotebookEdit`, so a `Write(<glob>)`
entry would never match (see "Write(path), NotebookEdit(path), and Glob(path)
rules never match" in https://code.claude.com/docs/en/permissions).
JSON, two-space indent, trailing newline.

**Codex.** Codex rules are user-global and key on a tokenised argv, so only
commands translate; filesystem rules and their modes do not affect this
output. Each command becomes
`prefix_rule(pattern=<argv as a JSON array>, decision="forbidden")` between
`# BEGIN atc hard-deny` and `# END atc hard-deny`. The header comment above
the block is fixed text. Codex accepts only `allow`, `prompt` and `forbidden`
as decisions, and one rule it cannot parse makes it reject the whole rules
file, so a spliced file is verified with
`codex execpolicy check --rules <file> <argv>`, not by comparing text.

**Git pre-commit.** The hook refuses a staged addition or modification whose
path matches a repository-relative glob denied for `edit` or `write`:

- `**/<name>` with no further slash: a basename alternative. `*` becomes
  `[^/]*`, `?` becomes `[^/]`, everything else is escaped. All basenames join
  into one group `(^|/)(a|b|c)$`.
- `<dir>/**` with no wildcard in `<dir>`: a prefix alternative `^<dir>/`.
- A glob rooted at `~`, a read-only rule, or any other shape is listed in the
  hook's "Not git-enforced here" comment, one per line, followed by
  `[<modes>]` when the rule names fewer than all three, and left to the
  runtime adapter.

The hook is plain POSIX `sh`, uses only `git diff --cached` and `grep -E`,
and exits 1 with the offending paths. With no repository-relative globs it
exits 0 and says so. The generator writes it with mode 0755; an installed
copy must stay executable (commit it with `git update-index --chmod=+x`), or
Git skips it on Linux and macOS.

## PreToolUse hook

One hook binds every Claude Code and Codex session on the machine: the engine
`adapters/python/scripts/pretooluse_hook.py` in the canonical checkout,
standard library only, reading one composed table. The hook sees the text of
a tool call, not what a program does once it runs; it turns a mistaken
command into a refusal with a reason and is not a security boundary.

**Table.** `atc enforce --workspace <map> --hook-out <dir>` writes
`<dir>/pretooluse.json` (LF, deterministic) from, in order:

1. the workspace policy, `policies/hard-deny.yaml` of this repository; its
   repository-relative globs apply under `workspace_root` and
   `worktrees/root/*`;
2. each registered project's `policies/hard-deny.yaml`, read from its
   checkout; its repository-relative globs apply under its `repo` and its
   `worktrees/*`;
3. the workspace map: each `state` root (an edit/write deny), the
   `worktrees` roots, and the write roots: `workspace_root` plus each
   `agent_write_roots` entry, with `%VAR%`, `$VAR` and `~` expanded.

Command rules apply to every session, whatever their source; `~`-rooted globs
apply everywhere. A rule already composed from an earlier source is not
repeated. Paths are stored absolute, long-form and case-folded. The engine
files and the table directory are write-protected in every call.

**Command entries.** The schema adds four hook-only fields to a
`commands.deny` entry; all the conditions of one entry hold for it to deny:

| Field | Denies when |
| --- | --- |
| `with_args` | one of these arguments, or `<arg>=...`, follows the prefix |
| `unless_args` | none of these arguments, or `<arg>=...`, follows the prefix |
| `push_targets` | the `git push` may update one of these branches |
| `background` | the command runs in the background |

**Matching.** Each Bash or PowerShell call splits into simple commands at
any depth, with the lexers of `shell_words.py`. Before an argv compares, token
by token, with a rule's `argv_prefix`:

- leading assignments and `env`, `command`, `exec`, `builtin`, `nohup`,
  `time`, PowerShell's `&` and `.` are dropped;
- `uv run [options]`, `uvx`, `python -m <module>` (`atc_cli` reads as
  `atc`), `cmd /c` and `Start-Process -FilePath ... -ArgumentList ...` resolve
  to the command they launch;
- `bash -c`, `sh -c`, `pwsh -Command` and `powershell -Command` strings, and
  a decoded `-EncodedCommand`, are lexed again, one level deep;
- a command substitution (`$(...)`, backticks) or PowerShell group (`(...)`,
  `$(...)`, `@(...)`) is its own command, and the command around it keeps
  its later words; a push refspec that is a substitution reads the
  checked-out branch;
- `argv[0]` compares by basename, case-folded, without `.exe`;
- git's options before the subcommand are dropped; `-C <path>` becomes the
  command's directory.

A push target is the destination of each refspec (after `:`, else the whole
refspec, without `+` and `refs/heads/`); a glob refspec, `--all`, `--mirror`
and `--branches` always match, and `HEAD` or no refspec reads the branch from
the directory's `HEAD` file, counting a directory it cannot resolve as on
a listed branch. A command runs in the background when the call sets
`run_in_background`, when a lone `&` ends it (not the `&` of `2>&1`), or when PowerShell starts it
with `Start-Job`, `Start-ThreadJob` or `Start-Process` without `-Wait`.

A Codex `Bash` call carries no shell name, so it is read both as Bash and as
PowerShell, and a write target either reading refuses refuses the call. Codex `apply_patch` targets come from its `*** Add File:`,
`*** Update File:`, `*** Delete File:` and `*** Move to:` lines.

**Paths.** A file tool's path, an `apply_patch` target, and each write target
of a shell call is refused when it lies in a protected path, matches a
filesystem rule denying `edit` or `write`, or lies outside every write root.
The write targets of a shell call (`write_targets.py`) are:

- redirections outside quotes: Bash `>`, `>>`, `>|`, `2>`, `&>`, `>& file`;
  PowerShell `>`, `>>`, `2>`, `3>` and `*>`, also glued to a quoted word
  (`"a">file`); `cmd /c` redirections. `>&2` and `2>&1` duplicate a stream;
- Bash `mkdir`, `rm`, `rmdir`, `touch`, `tee`, `truncate`, `unlink`,
  `shred`, every operand of `mv` and `install -d`, the destination of `cp`,
  `ln`, `install`, `rsync` and `scp` (`-t`/`--target-directory` included),
  the files of `sed -i` and `perl -i`, `curl -o`/`--output`/`--output-dir`,
  `wget -O`/`-P`, `tar -C`, `unzip -d`, `dd of=`, `git clone`, `git init`
  and `git worktree add` (under `git -C`), `find -delete`, `-fprint` and the
  writes of its `-exec` commands. The wrappers `env`, `sudo`, `doas`, `nice`,
  `ionice`, `timeout`, `stdbuf`, `nohup`, `time`, `command`, `exec`,
  `builtin`, `setsid`, `chrt`, `taskset` and `flock` are looked through; `xargs`
  running a writing command is refused, since its operands come from input;
- PowerShell `New-Item` (joined with `-Name`), `Remove-Item`, `Clear-Content`
  and their aliases on every path; `Set-Content`, `Add-Content`, `Out-File`,
  `Tee-Object`, `Export-Csv`, `Export-Clixml`, `Rename-Item` and
  `Start-Transcript` on their path; `Copy-Item`, `Move-Item`,
  `Expand-Archive` and `Compress-Archive` on the destination (the working
  directory when none is named) and, for a move, the source; `-OutFile`,
  `-RedirectStandardOutput` and `-RedirectStandardError` on any command.
  A parameter matches by any prefix of three characters or more, the dash
  included; a comma list (`-Path "a", "b"`, `a,b`) counts every item. A
  module-qualified name (`Microsoft.PowerShell.Management\Set-Content`) and
  the dot-source and call operators count as the cmdlet; an alias the call
  defines from literals with `Set-Alias`/`New-Alias` counts as its command,
  and any other alias definition, or a command name held in a group or a
  variable (`& ('Set-' + 'Content')`), is refused;
- .NET writes: `[IO.File]` and `[IO.Directory]` methods by the arguments
  they write (`Copy` its destination, `Move` both, `WriteAll*`, `AppendAll*`,
  `Create*`, `Open*`, `Delete` the first), `[IO.StreamWriter]::new`,
  `[IO.FileStream]::new` and `New-Object` of either, `.Save()`, `.CopyTo()`
  and `.MoveTo()` on their argument, and `.Delete()` on its object. A type
  held in a variable, `.Invoke`, a method named by a variable and
  `[IO.FileInfo]` objects are not matched;
- native `robocopy` and `xcopy` on their destination, in any shell; `cmd`
  verbs (`rmdir`, `del`, `mkdir`, `copy`, `move`, `mklink`) after `/c`, `/k`
  or `/r`, with any switches before it; pytest `--basetemp`;
- code the call evaluates from a literal string, read again with the call's
  state: Bash `eval`, `bash -c`/`sh -c`, PowerShell `Invoke-Expression`/`iex`,
  `pwsh`/`powershell` `-Command` (and its prefixes and `/` spelling, and the
  positional command of Windows PowerShell), `-EncodedCommand`, and
  `Start-Process` of a shell with its `-ArgumentList`. Code the text does not
  spell out (a shell reading stdin, `bash <<<`, `| bash`, `iex $s`,
  `[scriptblock]::Create`, an encoded command that does not decode) is
  refused.

A target substitutes the variables assigned earlier in the same call from a
string, another variable or `Join-Path` of those; environment variables
(`$NAME` in Bash, `$env:NAME` in PowerShell, `%NAME%`); `$HOME`, `$PWD` and
a `Join-Path` group. Bash variables follow subshell scope; a nested shell does
not see the calling shell's assignments. A variable becomes unknown when the
call rebinds it any other way: a scope-qualified or compound assignment, an
assignment inside a `{...}` block, a `for`/`foreach`/`read`/`mapfile`/`printf
-v` variable, `Set-Variable`/`New-Variable`/`Clear-Variable`/`Remove-Variable`,
or `-OutVariable`, `-PipelineVariable`, `-ErrorVariable`, `Tee-Object
-Variable` and their kin. An indirect rebinding (`[ref]`, `Get-Variable`,
`.PSVariable`, `$ExecutionContext`, `SetEnvironmentVariable`, a write to the
`env:` or `variable:` drive, `declare -n`, `source`) makes every variable
unknown for the rest of the call.

A relative target is checked against every directory the call may be in:
`cd`, `pushd`, `Set-Location`, `sl`, `chdir` and `Push-Location` to a
literal path add a directory; a change the text does not tell (`cd -`, a
variable, an unmatched `popd`, `source`) makes later relative targets
unresolved.

A target that still holds anything else (a variable assigned from a command,
a member such as `$f.FullName`, a command substitution or `(...)` group,
splatted parameters, pipeline input to a writing cmdlet) is refused, since
the hook cannot tell where it lands. `$null`, `NUL`, `/dev/null` and `\\.\`
devices write nothing; `\\?\` paths are checked without the prefix; a
`FileSystem::` provider path is checked as its file path, `Temp:` as the
temporary directory, and the `Env:`, `Variable:`, `Function:`, `Alias:`,
`Cert:` and registry drives hold no files. Any other multi-letter drive is
refused. Paths are compared lexically: a write through a symbolic link or
junction under a root is checked at the link's path. Files a program names
in its own options (`git diff --output`, `git format-patch -o`, `sort -o`,
`split`, `patch -o`, `python -c`, `powershell -File`) are writes the program
makes and are not read.

**Worktree cd.** In Claude Code, a top-level Bash `cd`/`pushd`, or any
PowerShell location change, into a worktrees root is refused. Codex starts
each command in the session's `cwd`.

**Failure.** The payload is read as UTF-8 bytes, whatever the console code
page; one that is not UTF-8, not JSON or not an object prints a deny naming
that cause. Any other error of the engine (a missing or invalid table)
prints a deny naming the error and the Owner's repair,
`atc enforce --install-hooks`. The Claude Code entry also sets
`"onFailure": "block"` (Claude Code 2.1.295 or later) and `"timeout": 10`,
so a hook that cannot start, crashes or stalls blocks the call. Codex offers
no such option: there a hook that cannot start lets the call run.

**Installation.** `atc enforce --install-hooks`, run by the Owner, writes the
table (to `--hook-out`, default `~/.atc/enforcement`) and replaces, in
`~/.claude/settings.json` and `~/.codex/hooks.json`, every PreToolUse entry
running the engine or one of the retired guards (`worktree_cd_guard.py`, `write_scope_guard.py`) with:

| Harness | Matcher | Command |
| --- | --- | --- |
| Claude Code | `Bash\|PowerShell\|Edit\|Write\|NotebookEdit` | `python "<root>/adapters/python/scripts/pretooluse_hook.py" --harness claude` |
| Codex | `Bash\|apply_patch` | the same with `--harness codex` |

A table outside the default directory adds `--table "<file>"`. Codex then
asks the Owner to trust the entry once in `/hooks`. The workspace policy
denies agents `atc enforce --hook-out` and `--install-hooks`, so both run
from the Owner's terminal, which no hook sees.

## Drift

`--check` regenerates in memory and exits 1, printing `drift:` and the
differing files, when a file under `--out` differs. With `--check`, three
options also compare the installed copies, each after line-ending
normalisation:

| Option | Compares |
| --- | --- |
| `--live-claude PATH` | the file's `permissions.deny` list, in order; other settings are ignored; with a machine-local segment, only the policy segment |
| `--live-claude-local PATH` | the machine-local segment of the file's `permissions.deny` list, in order, against the `--workspace` render |
| `--live-codex PATH` | the `# BEGIN atc hard-deny` ... `# END atc hard-deny` block, markers included; a file without exactly one block drifts |
| `--live-hook PATH` | the whole hook file |
| `--live-hook-table PATH` | the whole PreToolUse hook table, against a fresh compose (needs `--workspace`) |
| `--live-claude-hooks PATH` | the PreToolUse entries of a Claude settings file that run the engine or a retired guard: exactly the rendered entry |
| `--live-codex-hooks PATH` | the same for a Codex `hooks.json` |

`--check` compares content only, never file modes. A consuming project runs
it in CI against its committed copy, and `atc validate --projects` fails a
project whose `generated/enforcement/` differs from its
`policies/hard-deny.yaml`, so the enforcement surface changes only through a
reviewed diff of the policy. It also reports, without failing or writing, a
project whose `.claude/settings.local.json` lacks the machine-local segment
or holds one that differs from the map, and an installed hook table or entry
that differs from a fresh compose; `atc doctor` lists the same as its
`pretooluse hook` check.

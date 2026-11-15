# Enforcement generation

`hard-deny.yaml` states what an agent must be technically unable to do. Prose
does not enforce it; each harness's own configuration does. The generator
reads the policy and writes that configuration, deterministically, so a
project commits the output and its CI diffs it. If the generated file and the
policy disagree, the policy wins and the file is regenerated.

Reference implementation: `adapters/python/src/atc_cli/scripts/generate_enforcement.py`
(`atc-maintain guard`). Golden outputs: `shared/conformance/generate-enforcement/`.

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
absolute path. `atc-maintain guard` passes `--workspace` itself when the checkout
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
`pretooluse_hook.py` of the installed `atc`, which `atc hook` runs
(`WORKFLOW.md` § Installing a release), standard library only, reading a
table it composes itself from reviewed `main` inputs. The hook sees the text of
a tool call, not what a program does once it runs; it turns a mistaken
command into a refusal with a reason and is not a security boundary.

**Table.** The entry names the workspace map (`--workspace`), the home
directory (`--home`) and the ATC home (`--atc-home`); the hook reads no
environment variable for them. It composes its table from, in order:

1. the workspace policy: on a host whose map sets `governance_checkout`,
   `policies/hard-deny.yaml` at that checkout's `refs/heads/main`; a
   workspace directory that is a governance checkout without it stops at
   `GOVERNANCE_CHECKOUT_UNSET`; elsewhere the workspace's own
   `policies/hard-deny.yaml` file. Its repository-relative globs apply under
   `workspace_root` and `worktrees/root/*`;
2. each registered project's `policies/hard-deny.yaml` at its
   `refs/heads/main`; its repository-relative globs apply under its `repo`
   and its `worktrees/*`. A project whose `main` holds none adds no policy
   rule;
3. the workspace map: each `state` root (an edit/write deny), the
   `worktrees` roots, and the write roots: `workspace_root` plus each
   `agent_write_roots` entry, with `%VAR%`, `$VAR` and `~` expanded.

Edits in a worktree, uncommitted edits and topic branches never reach the
table. Policy files are read from the Git objects of the commit the key names
(`git --no-replace-objects cat-file --batch`), each at most 256 KiB, as YAML
without anchors or aliases. Git runs with an environment built from scratch
(no inherited `GIT_*`, `GIT_CONFIG_NOSYSTEM=1`).

Command rules apply to every session, whatever their source; `~`-rooted globs
apply everywhere. A rule already composed from an earlier source is not
repeated. Paths are stored absolute, long-form and case-folded. The engine
files, the state directory `<atc-home>/enforcement/`, the map, a workspace
policy read from a file, and uv's tool environment of the running `atc` (its
`sys.prefix` when it holds `uv-receipt.toml`) are write-protected in every
call.

**State.** Under `<atc-home>/enforcement/`, one set per map, named by the
first 16 hex digits `<m>` of the sha256 of the map's absolute path:

| File | Holds | Written by |
| --- | --- | --- |
| `baseline-<m>.json` | the effective table, which only tightens | `atc guard --install` and `--reset`; the hook writes baseline ⊕ fresh |
| `table-<m>.json` | the key and the effective table, or the failure, of the last composition | the hook |
| `compose-<m>.lock` | a one-byte lock | the hook, `--install`, `--reset` |

The key holds the map's sha256, each Git input's `main` SHA, a workspace
policy file's sha256, the values of the variables `agent_write_roots`
names, and a fingerprint of the composing code (size and mtime of its files),
so a new install, a commit on a `main` or a map change recomposes on the next
call. On a hit the hook reads the refs and stats the files, about 1 ms, without
starting Git. On a miss it takes the lock, rereads the cache, reads the
baseline and composes in a child process; the whole miss stays within 6 s of
the hook's start, so Codex, which runs a call whose hook times out, never
reaches its own timeout. The child process tree is killed at the deadline.

**Ratchet.** A composition never loosens the effective table. On success the
hook writes baseline ⊕ fresh: the union of `commands`, `filesystem`,
`worktrees` and `protected`, and the intersection of `write_roots` (a root is
kept when it lies within a root of the other side). What only the baseline
holds is `retained`, which `atc doctor` and `atc guard` report. A failure, a
missing or invalid baseline or a damaged cache never touches the baseline.
Loosening takes the Owner's `atc guard --reset`.

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

The shell check guards against accidental writes outside the roots by
ordinary commands; it is not a sandbox against deliberate evasion. It reads
the call's text statically, which cannot be complete for a shell, so what it
does not model either refuses (the forms below) or is a known limit listed
at the end of this section. The write targets of a shell call
(`write_targets.py`) are:

- redirections outside quotes: Bash `>`, `>>`, `>|`, `2>`, `&>`, `>& file`;
  PowerShell `>`, `>>`, `2>`, `3>` and `*>`, also glued to a quoted word
  (`"a">file`); `cmd /c` redirections. `>&2` and `2>&1` duplicate a stream;
- Bash `mkdir`, `rm`, `rmdir`, `touch`, `tee`, `truncate`, `unlink`,
  `shred`, every operand of `mv` and `install -d`, the destination of `cp`,
  `ln`, `install`, `rsync` and `scp` (`-t`/`--target-directory` included),
  the files of `sed -i` and `perl -i`, `curl -o`/`--output`/`--output-dir`,
  `wget -O`/`-P`, `tar -C`, `unzip -d`, `dd of=`, every path of `ln` (a
  link pointing outside the roots opens them to every later write),
  `find -delete`, `-fprint` and the writes of its `-exec` commands. These
  verbs' short options also count bundled or glued to their value
  (`cp -rt dir`, `cp -t/dir`, `curl -sSo/file`). Git, in both shells: the
  directory of `git clone`, `git init` and `git worktree add`, and the
  `-C`, `--work-tree` or `--git-dir` directory of any subcommand that is not
  read-only (`status`, `log`, `diff`, `show`, `rev-parse` and the like). The
  wrappers `env`, `sudo`, `doas`, `nice`,
  `ionice`, `timeout`, `stdbuf`, `nohup`, `time`, `command`, `exec`,
  `builtin`, `setsid`, `chrt`, `taskset` and `flock` are looked through; `xargs`
  running a writing command is refused, since its operands come from input;
- PowerShell `New-Item` (joined with `-Name`, and the `-Target`/`-Value` of
  a `SymbolicLink`, `Junction` or `HardLink`), `Remove-Item`, `Clear-Content`
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
  `[IO.FileInfo]` objects are not matched; .NET coverage is this fixed list;
- native `robocopy` and `xcopy` on their destination, in any shell; `cmd`
  verbs (`rmdir`, `del`, `mkdir`, `copy`, `move`, and both paths of
  `mklink`) after `/c`, `/k`
  or `/r`, with any switches before it; pytest `--basetemp`;
- code the call evaluates from a literal string, read again with the call's
  state: Bash `eval`, `bash -c`/`sh -c`, PowerShell `Invoke-Expression`/`iex`,
  `pwsh`/`powershell` `-Command` (and its prefixes and `/` spelling, and the
  positional command of Windows PowerShell), `-EncodedCommand`, and
  `Start-Process` of a shell with its `-ArgumentList`. Code the text does not
  spell out (a shell reading stdin, `bash <<<`, `| bash`, `iex $s`, an
  encoded command that does not decode) is refused.

A target substitutes the variables assigned earlier in the same call from a
string, another variable or `Join-Path` of those; environment variables
(`$NAME` in Bash, `$env:NAME` in PowerShell, `%NAME%`); `$HOME`, `$PWD`,
`$TMPDIR` (the Windows temporary directory, as Git Bash sets it) and a
`Join-Path` group. A PowerShell backtick at a line's end continues the
command on the next line. Bash variables follow subshell scope; a nested shell does
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
`Cert:`, `WSMan:` and registry drives, and any provider but `FileSystem::`,
hold no files, so a target that starts with one literally is no write
target, whatever follows it (`Remove-Item "env:$($_.Name)"`). Any other
multi-letter drive is refused.

Known limits, which the hook allows:

- Paths are compared lexically: a write through a symbolic link or junction
  that already exists under a root is checked at the link's path.
- Files a program names in its own options or writes on its own (`git diff
  --output`, `git format-patch -o`, `git archive -o`, `sort -o`, `split`,
  `patch -o`, `tar -c`, `zip`, `7z -o`, `pip --target`, `npm --prefix`,
  `python -m venv`, `python -c`, `powershell -File`, a script file run by
  path) are not read; neither are `awk -i inplace`, `chmod` and `chown`.
- .NET APIs outside the fixed list (`Net.WebClient.DownloadFile`,
  `Microsoft.VisualBasic.FileIO.FileSystem`, `Add-Type` code), a .NET type
  or member that is not literal (`$t::`, `([IO.File])::`, `::$m`,
  `.Invoke`), and `[IO.Directory]::SetCurrentDirectory` or
  `[Environment]::CurrentDirectory` before a relative .NET path.
- Deliberate evasion: variables rebound through `${a}`, `$variable:a` or
  `Set-Item variable:`; script blocks built or run from a string
  (`[scriptblock]::Create` followed by `&`, `.Invoke`, `InvokeScript`,
  `[PowerShell]::Create().AddScript`); aliases from `Set-Item alias:` or
  `Import-Alias`; `$PSDefaultParameterValues`; `Start-Process` of a native
  writer such as `robocopy`; Bash command names escaped, computed or held in
  a variable (`r\m`, `$c`, `$(echo rm)`), `function` and `alias` bodies,
  `CDPATH`, and wrappers outside the list above (`winpty`, `busybox`,
  `parallel`, `watch`, `command -p`).

**Worktree cd.** In Claude Code, a top-level Bash `cd`/`pushd`, or any
PowerShell location change, into a worktrees root is refused. Codex starts
each command in the session's `cwd`.

**Failure.** The payload is read as UTF-8 bytes, whatever the console code
page; one that is not UTF-8, not JSON or not an object prints a deny naming
that cause. When the table cannot be composed the hook denies every call,
in both harnesses, with `Refused: the PreToolUse hook cannot compose its
table: <TOKEN>: <detail>` and the Owner's next step, `atc doctor`:

| Token | Cause | Cached by key |
| --- | --- | --- |
| `WORKSPACE_MISSING`, `WORKSPACE_INVALID` | the map cannot be read, or is not a valid map | yes |
| `POLICY_INVALID` | a policy fails its schema, contradicts itself or uses a YAML alias; names `<label> <repo>@<sha>` | yes |
| `POLICY_TOO_LARGE` | a policy file over 256 KiB | yes |
| `MAIN_UNREADABLE` | a repository without a readable `refs/heads/main` | yes |
| `GOVERNANCE_CHECKOUT_UNSET` | the workspace directory is a governance checkout the map does not declare | yes |
| `COMPOSE_TIMEOUT`, `COMPOSE_FAILED` | composing took past the deadline, or its process exited without a result | yes, once the key names its sources; not before |
| `COMPOSE_BUSY` | another process held the lock past the deadline | no |
| `BASELINE_MISSING`, `BASELINE_INVALID` | no baseline, or one that cannot be read; the Owner runs `atc guard --reset` | no |
| `USAGE` | an entry with both `--workspace` and `--table` | no |

A failure cached by key denies at once until an input changes. Any other
error of the engine prints a deny naming the error and `atc doctor`. The
Claude Code entry also sets
`"onFailure": "block"` (Claude Code 2.1.295 or later) and `"timeout": 10`,
so a hook that cannot start, crashes or stalls blocks the call. Codex offers
no such option: there a hook that cannot start lets the call run.

**Installation.** `atc guard --install`, run once per host by the Owner,
composes the table in the Owner's terminal, refuses on the checks of
`--reset` below (replace refs, alternates, a diverged `main`), writes the
baseline (the old baseline ⊕ fresh ⊕ a legacy `pretooluse.json`), deletes
the map's cache, and replaces, in `~/.claude/settings.json` and
`~/.codex/hooks.json`, every PreToolUse entry running the engine or one of
the retired guards (`worktree_cd_guard.py`, `write_scope_guard.py`) with:

| Harness | Matcher | Command |
| --- | --- | --- |
| Claude Code | `Bash\|PowerShell\|Edit\|Write\|NotebookEdit` | `atc hook --harness claude --workspace "<map>" --home "<home>" --atc-home "<atc-home>"` |
| Codex | `Bash\|apply_patch` | `atc hook --harness codex --workspace "<map>" --home "<home>" --atc-home "<atc-home>"` |

`<home>` is the Owner's home directory and `<atc-home>` is `<home>/.atc`. An entry whose values differ from those the Owner's terminal
resolves is outdated. `atc hook` runs the engine of whatever `atc` is
installed and composes from `main`, so a new install, a policy change on
`main` or `atc add` needs no new entry; an entry that runs an engine by path,
or without `--workspace`, is a managed entry `--install` replaces. The harness finds `atc`
on its own PATH, so the entry checks a call only where `atc` is on that PATH.
When the harness cannot run `atc`, because it is not on PATH or because
`uv tool install` is replacing the environment, the Claude Code entry asks to
block the call (`onFailure: block`), while Codex blocks only on exit 2 and
runs the call unchecked. So the Owner installs when no session runs and checks
that `atc` resolves in the shell each harness starts from. Codex then
asks the Owner to trust the entry once in `/hooks`. `atc hook --table FILE`
(or neither option, `~/.atc/enforcement/pretooluse.json`) reads a static
table instead; `--workspace` with `--table` is a `USAGE` deny.

**Reset.** `atc guard --reset`, from the Owner's terminal, is the one way to
loosen the table. It refuses unless both entries are current; refuses on
`refs/replace/*` (`REPLACE_REFS_PRESENT`), `objects/info/alternates`
(`ALTERNATES_PRESENT`) or a `main` that does not descend from
`refs/remotes/origin/main` (`MAIN_DIVERGED`; nothing is fetched, so this
check informs and does not prove); prints each input as `<label>
<repo>@<sha>` and the rules and write roots the baseline would drop (against
the cache, else the legacy table, labelled `no baseline: compared with ...`,
when there is no baseline); asks `[y/N]`, or takes `--yes` (`--json` writes
only with `--yes`); then, under the lock, writes the baseline as the fresh
table and deletes the cache and the legacy table.

**Agents.** The workspace policy denies agents `atc guard --install` and
`--reset`, `atc hook --workspace`, `atc init --force` and `uv tool
install`/`upgrade`/`uninstall`, so they run from the Owner's terminal, which
no hook sees. `atc guard` without an option reports the entries, the
baseline, whether the table composes, what is retained and a legacy table,
and writes nothing; `atc doctor` prints the same lines.

**Matching uv.** uv's options before the subcommand are dropped, with the
value of those that take one, so `uv --quiet tool install x` matches
`[uv, tool, install]`.

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
| `--live-hook-table PATH` | the whole PreToolUse hook table, against a fresh compose from each repository's `main` (needs `--workspace`) |
| `--live-claude-hooks PATH` | the PreToolUse entries of a Claude settings file that run the engine or a retired guard: exactly the rendered entry |
| `--live-codex-hooks PATH` | the same for a Codex `hooks.json` |

`--check` compares content only, never file modes. A consuming project runs
it in CI against its committed copy, and `atc sync --check` fails a
project whose `generated/enforcement/` differs from its
`policies/hard-deny.yaml`, so the enforcement surface changes only through a
reviewed diff of the policy. It also reports, without failing or writing, a
project whose `.claude/settings.local.json` lacks the machine-local segment
or holds one that differs from the map, and an installed hook table or entry
that differs from a fresh compose; `atc guard` and `atc doctor` list the same as its
`pretooluse hook` check.

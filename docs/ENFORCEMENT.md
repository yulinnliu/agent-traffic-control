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
segment. For the same reason Codex, whose rules are argv prefixes only, gets
no machine-local block.

## Mapping

**Claude Code.** In policy order: each command becomes
`Bash(<argv joined by single spaces>:*)`; each filesystem rule becomes one
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

`--check` compares content only, never file modes. A consuming project runs
it in CI against its committed copy, and `atc validate --projects` fails a
project whose `generated/enforcement/` differs from its
`policies/hard-deny.yaml`, so the enforcement surface changes only through a
reviewed diff of the policy. It also reports, without failing or writing, a
project whose `.claude/settings.local.json` lacks the machine-local segment
or holds one that differs from the map.

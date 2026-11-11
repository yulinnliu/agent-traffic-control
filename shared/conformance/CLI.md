# CLI conventions

The rules every `atc` command follows, in any implementation. The Python
reference implementation checks them in `adapters/python/tests/test_cli_conventions.py`.

## Root resolution

The ATC checkout a command acts on is, in order: `--root DIR`, `$ATC_ROOT`,
the nearest parent of the working directory that holds `workspace.yaml` and
`shared/governance/`, else the checkout the installed `atc` comes from.
`--root` is one global flag and may stand before or after the command
(`atc --root DIR validate` and `atc validate --root DIR` are the same call);
everything after a `--` belongs to the command, and `atc test` takes `--root`
only before the command.

`atc onboard --root-only` renders the ATC checkout that holds the working
directory, which in a linked worktree has no `workspace.yaml`; `--root` and
`$ATC_ROOT` still win. Every command names a repository with `--project`,
resolved in that root's `workspace.yaml`; `root` names the workspace root.

## Canonical checkout guard

When `atc` runs from the canonical checkout (it holds `workspace.yaml` and is
no linked worktree), every command but `doctor`, `--help` and `--version`
stops with `ERROR CHECKOUT_NOT_MAIN` while that checkout is off `main`, stops
with `ERROR CHECKOUT_UNREADABLE` when its Git state cannot be read, and warns
with `WARNING CHECKOUT_DIRTY` while it has tracked modifications.
`onboard --root-only` stops with `ERROR CHECKOUT_CANONICAL` when the checkout
it would render is the canonical one. A module the installed `atc` lacks stops
the command with `ERROR MISSING_DEPENDENCY` and the reinstall command.

## Command groups

| Group | Commands |
| --- | --- |
| Task lifecycle | `task`, `worktree`, `verify`, `integrate`, `close-out`, `preflight` |
| Testing | `test`, `tmp` |
| Workspace | `bootstrap`, `onboard`, `doctor`, `validate` |
| Policy | `policies`, `enforce`, `classify` |
| Log | `friction` |

`atc --help` lists them by group, one line each; `atc --version` prints the
`atc` and `atc-testkit` versions.

## Help

`atc <command> [<subcommand>] --help` prints, in this order:

1. the usage line;
2. one sentence saying what the command does;
3. at most a short paragraph on what it reads and writes;
4. every subcommand with a one-line summary, or every option and positional
   with help text;
5. `Exit:` with the codes the command uses, as § Exit codes defines them;
6. `See:` with the document and section that specify the command.

Implementation detail, CI notes and dependencies stay out of help. A value an
option takes is named by its type in capitals:

| Metavar | Value |
| --- | --- |
| `NAME` | a project, skill, scratch, tool or run name |
| `ID` | a task ID, session ID or event ID (`EVENT_ID`) |
| `SHA` | a commit: full, or a unique prefix where the help says so |
| `DIR`, `FILE`, `PATH` | a directory, a file, either or an interpreter |
| `SECONDS` | a duration |
| `N` | a positive integer, such as a rank |
| `TEXT`, `QUOTE` | free text; `QUOTE` is someone's exact words |
| `URL`, `OWNER/REPO`, `REF`, `TAG`, `KEY`, `ITEM`, `TOPIC`, `ARG` | as named |

An option with fixed choices shows the choices instead.

An argument error prints the usage line, the error, and
`next: atc <command> [<subcommand>] --help`, and exits 2.

## Exit codes

| Exit | Name | Meaning |
| --- | --- | --- |
| 0 | OK | Done; a check found nothing; or a verdict was printed (`classify` LOW or HIGH, `preflight` PUSH, `integrate --check` CHECK FF or CHECK NO_FF) |
| 1 | STOP | A valid call was refused by the state it found, before it changed anything; or a check or validation found problems or drift |
| 2 | ERROR | A usage error, invalid input, or a Git or runtime fault |
| 3 | PARTIAL | Stopped after the action began: `integrate` after its candidate exists, `close-out` after an item was deleted |
| 8 | GUARD | An `atc-testkit` guard refused (`docs/TESTKIT.md`) |
| 9 | REFUSED | `atc-testkit` refused before its run |
| 124 | TIMEOUT | The run hit its time limit, or its wait for host test workers passed its limit (`WORKER_WAIT_TIMEOUT`, `docs/TESTKIT.md` § Host worker budget) |
| 130 | INTERRUPTED | The run was interrupted |

`atc test run` exits with pytest's own code (0 to 5), which is why the
`atc-testkit` refusals take 8 and 9; `atc test private` exits with its
command's code. Codes 4 to 7 are reserved. The first stderr line of a refusal
is `STOP <TOKEN>: <detail>` for 1 and 3, `ERROR <TOKEN> <detail>` for 2, and
`<TOKEN> <detail>` for an `atc-testkit` refusal. `atc integrate` and
`atc preflight` add the `next:` lines of governance `GIT_WORKFLOW.md`
§ Command results.

## Output

The default output is the verdict and the next action; detail sits behind a
flag or in a log file whose path the command prints. Every command that
prints a verdict or a report takes `--json` and then prints one JSON object on
stdout:

```json
{"command": "atc close-out", "verdict": "DONE", "exit_code": 0, "next": null, "...": "the command's own fields"}
```

- `command` is `atc <command>` plus the subcommand.
- `verdict` is the command's verdict token; a command without one gives the
  exit code's name.
- `next` is the next step as text, or `null`.
- A result that is a list sits under a named key (`tasks`, `scratches`,
  `items`).

Human lines stay on stderr, and `--json` never changes the exit code. A usage
error is always text. `atc test` takes no `--json`: `test run` leaves its
evidence record and log for machines, and `test private` and `test mutants`
pass their output through.

## Flags and names

- `--check` rehearses the command's write: it writes nothing, takes no lock and
  builds no candidate, and answers whether the gate would pass: exit 0 when
  it would (the files are current, or the integration would proceed), 1 when
  it would not, each reason on its own line.
- `--project NAME` is the only way to name a project. A positional argument is
  the operand of its verb: a directory to check or clean, an event to resolve.
  `validate --project-checkout NAME=PATH` maps a project to a checkout.
- `clean` deletes the targets you name, after its safety checks
  (`worktree clean`, `tmp clean`); `prune` deletes what the command finds
  orphaned (`tmp prune`, `onboard --sync --prune`). `close-out` is the
  task-lifecycle step that cleans one integrated task.
- `onboard --sync` is the one command that converges projects' ATC-owned
  files.

## Versions and compatibility

`atc` (the Python adapter, `adapters/python/pyproject.toml`) and
`atc-testkit` (`adapters/python/atc-testkit/`) carry separate versions. A
GitHub Release is named for the `atc` version (tag `v<version>`), and its
notes give the `atc-testkit` version released with it, every breaking change,
every deprecation and every removal.

A **breaking change** changes any of these:

- a command, subcommand, option or positional of the CLI, an exit code, a
  verdict token, or a `--json` field's presence or meaning (a new field is
  not breaking);
- the task record schema;
- the `atc.lock` format;
- a governance contract under `shared/governance/`;
- a conformance fixture's `args.json` or `expected/`.

Help text, a new command, a new option and a new `--json` field are not.

Before 1.0, a breaking change lands only in a minor version (0.3 to 0.4) and a
patch version only fixes; from 1.0 on, versions follow Semantic Versioning.

A rename that can keep the old spelling keeps it as an alias for exactly one
minor version: the version that introduces the new name accepts the old one
and prints `WARNING DEPRECATED <old>: use <new>; removed in atc <next minor>`
on stderr, with the exit code and `--json` unchanged, and the next minor
version removes it. The rule covers commands, subcommands and options; the
adapter's internal module and script paths are not part of the CLI. A change
that cannot keep an alias (an exit code, a `--json` field's meaning, the
record or lock format) lands in a minor version and the release notes mark it
BREAKING; `atc` reads the `atc.lock` and task records of the previous minor
version, and `onboard --sync` migrates them. A breaking change to a governance
contract also goes through governance `GOVERNANCE_CHANGES.md`.

## Deprecated forms

| Form | Use | Removed in |
| --- | --- | --- |
| `atc worktree cleanup` | `atc worktree clean` | 0.4.0 |
| `atc onboard --check\|--sync NAME` | `atc onboard --check\|--sync --project NAME` | 0.4.0 |
| `atc bootstrap add NAME` | `atc bootstrap add --project NAME` | 0.4.0 |
| `atc bootstrap sync [NAME] [--prune]` | `atc onboard --sync --all\|--project NAME [--prune]` | 0.4.0 |

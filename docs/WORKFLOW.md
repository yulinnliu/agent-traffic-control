# Task lifecycle

How one task moves from start to push, with the command each step runs, who
runs it and the refusals met most often. This page says how; the rules are
governance `DELIVERY.md`, `GIT_WORKFLOW.md` and `VERIFICATION.md`, and the
protocols are the skills `atc-coordinator`, `atc-review` and `atc-worktree`.
Where this page and those differ, they win.

`<ID>` is the task ID, `<project>` a registered project (`root` for the
workspace root) and `<sha>` a full 40-character commit SHA. Every command
prints one verdict line on success; a refusal prints `STOP <TOKEN>: ...` or
`ERROR <TOKEN> ...` and a `hint:` line with the fix
(`shared/conformance/CLI.md` § Output).

## The flow

| Step | Who | Command |
| --- | --- | --- |
| 1. Open the task | coordinator | `atc task create "<title>" --project <project> [--tests <level>]` |
| 2. Work and test | writer | `atc test <ID>`; under `strict` also `atc test <ID> --mutants <file>` |
| 3. Check and report | writer | `atc integrate <ID> --sha <sha> --check`; step report to `~/.atc/reports/<ID>-<sha>.md` |
| 4. Review | reviewer | `atc test <ID> --sha <sha>`, `atc test <ID> --candidate --sha <sha>`; verdict to `~/.atc/reports/<ID>-<sha>-review.md` |
| 5. Integrate | the Owner | `atc integrate <ID> --sha <sha>` |
| 6. Close | coordinator | `atc task close <ID> --check`, then `atc task close <ID>` |
| 7. Push | the Owner | `atc push <project>` after the coordinator's `atc push <project> --check` |

The record's test level decides steps 2 and 4 (`VERIFICATION.md` § Testing,
Test levels): `none` runs no test and the reviewer reads the diff,
`standard` runs the commands above, `strict` adds the mutants for the writer
and the reviewer.

## 1. Open the task

The coordinator, on the Owner's word to start:

```text
atc task create "<title>" --project <project> --tests standard
atc task create --file <brief.md> --project root
```

`create` allocates the ID, adds the worktree
`worktrees/<project>/<harness>+<ID>` on the branch `<harness>+<ID>` from
`main`, and commits the record `.atc/tasks/<ID>.yaml` on it. `--file` reads
`# title`, `## Scope`, `## Decisions` and `## Tests` from a Markdown brief.
The Owner's later rulings go in with `atc task update <ID> --decision "..."`.
A new session continues the task from `atc task resume <ID>`.

- `HARNESS_UNKNOWN`: the harness is read from the environment it sets:
  `CLAUDECODE` names `claude`, a `CODEX_*` variable `codex`, a `DSH_*`
  variable `dsh`. With none of them, or more than one harness's, `create`
  stops; run it from a Claude Code, Codex or DSH session.
- `CHECKOUT_NOT_MAIN`: the workspace root's canonical checkout is off
  `main`; switch it back before any other `atc` command.

## 2. Writer: work and test

The writer, in the worktree:

```text
atc test <ID>
atc test <ID> -- tests/test_one.py
atc test <ID> --mutants <mutants.json>
```

`atc test <ID>` runs the tests related to the task's changes since its
`base_sha`, in the worktree's test directory, isolated in the scratch
`<ID>`; it prints `LOG <path>` and one summary line. `--all` runs the full
suite and `-v` streams pytest's output. A command that opens windows runs
under `atc test --private -- <command>` (`docs/TESTKIT.md` § Private
desktop). A root writer also runs the checks its change calls for, from the
worktree's `adapters/python` with `uv run --locked atc-maintain`: `sync
--root` then `validate` after a skill change, `policies check` after a schema
or example change (root `AGENTS.md` § Changing this repo).

- `NO_TESTKIT`: the project has no `[tool.atc-testkit]`; its tasks use
  `tests: none`.
- `VENV_MISSING`: the worktree has no `.venv`; prepare it as the project's
  `AGENTS.md` says.

## 3. Writer: check and report

```text
atc integrate <ID> --sha <sha> --check
```

The writer reports the verdict in the step report as `atc-review` "Writer:
the step report" says.

- `REBASE_REQUIRED` or `OVERLAP`: rebase onto `main` and report the new tip.

## 4. Reviewer

The reviewer, as `atc-review` "Reviewer: the review" says:

```text
atc test <ID> --sha <sha>
atc test <ID> --candidate --sha <sha>
atc test <ID> --mutants <mutants.json> --sha <sha>
```

`--sha` exports the reviewed commit into the scratch `<ID>-<sha7>` and builds
its environment once. `--candidate` runs the integration verifier on the
candidate `atc integrate` would build, without a slot, a ref update or the
Owner's run; its PASS is recorded, and `atc integrate` reuses it while the
candidate and its inputs are unchanged
(`shared/conformance/verification-evidence/README.md` § Integration reuse).
Run it while no integration runs. A refusal is a blocker finding. The
verdict's first line is its token; a PASS ends with
`atc integrate <ID> --sha <sha>`.

## 5. The Owner integrates

The coordinator runs `atc integrate <ID> --sha <sha> --check --json` and
gives the Owner the command (`GIT_WORKFLOW.md` § Owner-run commands):

```text
atc integrate <ID> --sha <sha>
```

The ID names the repository by its prefix and the branch by the record's
`branch`; without `--sha` the newest PASS review report names the reviewed
SHA. A branch rebased onto `main` after the review carries the approval over
when every patch-id matches. The check's `verification_reuse` tells the Owner
how long it takes: `REUSED` takes seconds, any other value runs the verifier.
Only the Owner runs it; the hard-deny policy refuses it to an agent except
with `--check`.

The coordinator reads completion from Git at its next turn: the task's line
in `<git common dir>/atc/integrations.jsonl` with the reviewed SHA, and
`main` at or past its `candidate_sha`. A stop the Owner shows follows its
`hint`; `VERIFICATION_FAILED` names the failing tests in the verifier's log,
and the fix goes back to review.

## 6. Coordinator: close

```text
atc task close <ID> --check
atc task close <ID>
```

Run from outside the task's worktree. `--check` lists each item and deletes
nothing. `close` removes the worktree, the branch, the task's registered
scratches and its reports; items that need the Owner's consent are listed,
never deleted, and the record's `keep` keeps its items. It finds the
worktree from the record's `branch`, so a task whose branch predates the
`<harness>+<ID>` naming closes the same way, and a topic integrated by a
recorded, Owner-authorized squash closes plainly too, its tip kept as the tag
`archive/<ID>` (governance `GIT_WORKFLOW.md` § Explicitly authorized squash).
`atc task close <ID> --abandon`
closes a task that will not be integrated and keeps its tip as the tag
`archive/<ID>`.

- `PERMISSION_DENIED`: a shell still sits in the worktree; leave it and run
  the same command again.

## 7. The Owner pushes

```text
atc push <project> --check
atc push <project>
```

The coordinator runs `--check`, which fetches `origin` and lists each commit
of `origin/main..main`, and gives the Owner the second command. `atc push`
refuses a commit that no integration or sync recorded, pushes exactly the
checked `main` tip and reads `origin/main` back. `atc push --all` asks y/N
per repository. The coordinator then archives the task's sessions
(`DELIVERY.md` § Close-out and § 10).

## Build trees

```text
atc checkout <project> <sha>
atc checkout <project> <sha> --remove
```

A build runs from a clean tree of a project at an exact commit on its
`main`, never from a task worktree. `atc checkout` makes a detached worktree
`worktrees/<project>/build+<sha7>/`, locked so no worktree cleanup removes
it; it has no task record and no branch, so no commit comes from it.
`--no-git` exports the commit into a scratch under the ATC home instead.
Both are registered scratches, so `atc test --list` shows them; a commit not
in the history of `main` stops with `NOT_ON_MAIN`, and a path that holds
something else with `PATH_OCCUPIED`. `--remove` removes the tree and its
registry line.

## Installing a release

The installed `atc` runs a release copy, never `main`: integrating a root
task, even one that changes the CLI, changes nothing a host runs, and every
step of the flow runs the commands of the installed release. A release
reaches a host when the Owner, after tagging it on `main`, runs from their
own terminal:

```text
atc install <tag> --check
atc install <tag>
atc guard --install
```

On a host whose workspace directory is itself the ATC checkout, its
`workspace.yaml` names it with `governance_checkout: .` before a release
from 0.7.0 on is installed. Without that line such a release stops `atc
sync` and `atc add` with `GOVERNANCE_CHECKOUT_UNSET`; releases before 0.7.0
ignore the key, so the Owner adds it first.

`atc install` creates `~/.atc/releases/<tag>` (`ATC_HOME.md` § Layout), a
detached worktree of the workspace root repository at the tag, locked so no
worktree cleanup removes it, unless it already exists clean at that tag. It
then runs `uv tool install -e <copy>/adapters/python --force` and prints the
installed version and copy and the ones before them. `--check` reports the
same and changes nothing; agents run only `--check` (hard-deny rule
`atc-install`). `atc guard --install` points the PreToolUse hook at the new
copy's engine; until then the hook runs the previous copy and `atc guard`
reports the drift. `atc doctor` names the installed version and the copy it
runs from.

An unknown tag (fetch the tags first), a tag not on `main` and an existing
copy that is not a clean detached worktree at the tag stop the command. On
Windows, `atc install` renames the running `atc.exe` aside so uv can write
the new one; when uv still fails, the hint names the uv command to run from a
terminal where no `atc` runs. Earlier copies stay: a rollback is
`atc install <earlier tag>`, and the Owner removes a copy no longer installed
with `git worktree remove --force --force <copy>`.

A release acts only on projects whose `.atc/atc.lock` pins it. While a
project pins another revision, or none, the commands that run the task flow
under its contract stop with `STOP RELEASE_MISMATCH` and a hint. A project
behind the installed release, or with no lock, takes `atc sync <project>`:
run from a release copy, sync with no `--revision` targets the installed
release, not the latest tag. A project ahead of it, which sync never moves
back without `--revision`, takes the Owner's `atc install` of the release
it pins. These are `task create`, `update`, `resume` and `close`,
`test ID` (but `--clean`, `--list` and `--private`), `integrate` and `push`,
their `--check` rehearsals included, since a rehearsal answers whether the
command would pass. `doctor`, `guard` and `task list` read and stay usable;
`sync` and `install` are the fix; `init`, `add` and `remove` manage the map.
The workspace root is never compared: its `main` is the governance itself.
An `atc` that does not run from a release copy (`uv run atc` in a topic
worktree, or one still installed from the canonical checkout) has no
installed release, so nothing is compared. `atc doctor` lists each project's
pin against the installed release, and reports a release copy that has
tracked modifications or whose HEAD is not its tag's commit; commands do not
refuse for that, since the hook write-protects the copy and only the Owner's
own hand changes it.

The canonical checkout stays the workspace that `atc` and every session
read: task records, policies, governance and skills. Off `main` every `atc`
command but `doctor` refuses with `CHECKOUT_NOT_MAIN`. A topic worktree runs
its own code with `uv run atc` and `uv run --locked atc-maintain` from its
`adapters/python`.

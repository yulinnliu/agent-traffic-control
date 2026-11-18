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
| 2. Work and test | writer | `atc test <ID>`; under `strict` the step report lists the guards |
| 3. Check and report | writer | `atc integrate <ID> --sha <sha> --check`; step report to `~/.atc/reports/<ID>-<sha>.md` |
| 4. Review | reviewer | `atc test <ID> --sha <sha>`, `atc test <ID> --candidate --sha <sha>`; under `strict` also `atc test <ID> --mutants <file> --sha <sha>`; verdict to `~/.atc/reports/<ID>-<sha>-review.md` |
| 5. Integrate | the Owner | `atc integrate <ID> --sha <sha>` |
| 6. Close | coordinator | `atc task close <ID> --check`, then `atc task close <ID>` |
| 7. Push | the Owner | `atc push <project>` after the coordinator's `atc push <project> --check` |

The record's test level decides steps 2 and 4 (`VERIFICATION.md` § Testing,
Test levels): `none` runs no test and the reviewer reads the diff,
`standard` runs the commands above, `strict` adds the mutants, which the
reviewer writes and runs from the guards the writer's step report lists.

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
```

`atc test <ID>` runs the tests related to the task's changes since its
`base_sha`, in the worktree's test directory, isolated in the scratch
`<ID>`; it prints `LOG <path>` and one summary line. `--all` runs the full
suite and `-v` streams pytest's output. A command that opens windows runs
under `atc test --private -- <command>` (`docs/TESTKIT.md` § Private
desktop). A root writer also runs the checks root `AGENTS.md` § Changing
this repo names for its change.

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

While it runs, stderr shows one
`STEP <step> <project> <seconds>s <result>` line per step: `fetch`,
`preflight`, `candidate`, `verify`, `fast-forward` and `ledger`, the result
a refusal's token where the step stopped. The `verify` step's `START` line
names the verifier's log, and the verifier's lines follow as `  verify: `
lines while it runs (`TESTKIT.md` § Running and logging). Stdout keeps the
verdict lines and the `--json` object; `--check` prints no step line.

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
checked `main` tip and reads `origin/main` back. A commit counts as recorded
when a line of the integration ledger names it in its `commits`, also after
the Owner reset `main` below that integration's candidate; the listing marks
such a group `candidate off main`. `atc integrate` adds the `commits` of
older ledger lines while their commits still exist. `atc push --all` asks y/N
per repository, also under `--json`, where the listing and the question go to
stderr; no answer is a no. The coordinator then archives the task's sessions
(`DELIVERY.md` § Close-out and § 10).

`atc push` shows its progress on stderr, one line as each step of a
repository ends, `STEP <step> <name> <seconds>s <result>`, with one decimal
like the `ELAPSED` lines (`TESTKIT.md` § Stage times). A step whose own
output follows also has a line as it starts, `0.0s START <what>`:

```text
STEP fetch hami-quant 2.5s OK
STEP ledger hami-quant 0.1s 3 commits in origin/main..main, 3 recorded
STEP gate hami-quant 0.0s START 1a2b3c4d5e6f
<the pre-push hook's output, as it runs>
STEP gate hami-quant 362.4s OK
STEP push hami-quant 0.0s START 1a2b3c4d5e6f -> origin/main
<git push's output, as it runs>
STEP push hami-quant 4.1s OK
STEP read-back hami-quant 2.4s origin/main 1a2b3c4d5e6f
PUSHED hami-quant 1a2b3c4 (3 commits)
```

- `fetch` is `git fetch origin`; `ledger` checks each commit of
  `origin/main..main` against the integration ledger, and its result is the
  count and how many are recorded, or the stop that ends the run.
- `gate` runs the repository's pre-push hook with `git hook run`, giving it
  what `git push` would: the remote's name and push URL as arguments and
  `refs/heads/main <sha> refs/heads/main <origin/main>` on stdin. It runs
  before git connects to origin: git runs the hook only after connecting,
  and a remote drops a connection left idle through a long gate. A failing
  hook is `GATE_FAILED` (exit 2), its output streamed once above, and
  nothing connects.
- `push` streams `git push --no-verify` as it runs, with git's transfer
  progress when stderr is a terminal; the hook has run in `gate`. If origin
  moved in between, git refuses the push as not a fast-forward. A failed
  push is `PUSH_FAILED`. Its refusal names git's exit code, with its meaning
  when git prints nothing of its own (141: the connection closed), and
  repeats only git's and the transport's own lines (`error:`, `fatal:`,
  `remote:`, `To`, `ssh:`, `Connection to … closed`).
- `read-back` is `git ls-remote origin refs/heads/main`, the remote's own
  answer that it names the pushed SHA (governance `GIT_WORKFLOW.md`
  § Publication boundary). The push itself moves the remote-tracking
  `origin/main`, so no second fetch follows.
- Under `--json` the progress stays on stderr and stdout holds only the JSON
  object, as for every human line (`shared/conformance/CLI.md` § Output).

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

The installed `atc` is the Owner's `uv tool install` of a release tag, never
`main` and never a working tree: integrating a root task, even one that
changes the CLI, changes nothing a host runs, and every step of the flow runs
the commands of the installed release. Installing is the Owner's act, run
from their own terminal after the release is tagged on `main`; agents never
install `atc`.

Before installing, no `atc test`, verifier or other `atc` command runs:
`atc doctor` reports its `atc environment` line READY. While a process runs
from the installed environment, `uv tool install --force` cannot replace it
(os error 5, or `WinError 32` on `atc.exe`) and can leave an `atc.exe` that
cannot start, so every session's PreToolUse hook fails closed until an install
succeeds. `ATC_ENV_IN_USE` lists each such process with its PID and command.

On the host whose workspace directory is the ATC checkout, the source is
that checkout:

```text
uv tool install --force "git+file:///<checkout>@<tag>#subdirectory=adapters/python"
```

Every other host installs the same tag from GitHub:

```text
uv tool install --force "git+https://github.com/<owner>/<repo>@<tag>#subdirectory=adapters/python"
```

Once per host, after the first install, the Owner also runs:

```text
atc guard --install
```

The `<checkout>` URL uses forward slashes and `%20` for a space. uv builds
the wheel at the tag, keeps it in its own tool environment and records the
installed commit and the requested tag in the distribution's
`direct_url.json` (PEP 610); `atc doctor` reports the version, commit, tag
and source from it, and compares it with the workspace's `main` first: an
installed `atc` that is not on `main`, older than `min_atc` of
`atc-manifest.yaml`, or older than the latest release tag on `main` is one
ERROR line naming the install command, and the drift that follows from it
(workspace-managed adapters, a project's integration or testkit, a pin ahead
of the installed release) is listed beneath it as NOTE, which still exits 1.
Commits on `main` after the installed release that change what `atc` runs or
delivers are a WARN. `atc guard --install` writes the baseline of the
PreToolUse hook's table and the hook entry `atc hook`, which runs the engine
of whatever `atc` is installed and composes its table from `main`, so a later
install, a policy change on `main` or `atc add` needs no new entry
(`ENFORCEMENT.md` § PreToolUse hook). A rollback is the same command with an
earlier tag; it does not loosen the hook's table, which only
`atc guard --reset` does. On Windows, uv
cannot replace `atc.exe` while it runs: the Owner installs when no session
runs, and reruns the command after a `WinError 32`. Cutting a release is the
maintainer's `docs/development/RELEASING.md`; building and publishing the
docs site is `docs/development/DOCS_SITE.md`.

On a host whose workspace directory is itself the ATC checkout, its
`workspace.yaml` names it with `governance_checkout: .`. Without that line
`atc sync` and `atc add` stop with `GOVERNANCE_CHECKOUT_UNSET`, and the
PreToolUse hook denies every call with the same token.

A release acts only on projects whose `.atc/atc.lock` pins it. While a
project pins another revision, or none, the commands that run the task flow
under its contract stop with `STOP RELEASE_MISMATCH` and a hint. A project
behind the installed release, or with no lock, takes `atc sync <project>`:
sync with no `--revision` targets the installed release, not the latest tag.
A project ahead of it, which sync never moves back without `--revision`,
takes the Owner's `uv tool install` of the release it pins. These are
`task create`, `update`, `resume` and `close`, `test ID` (but `--clean`,
`--list` and `--private`), `integrate` and `push`, their `--check`
rehearsals included, since a rehearsal answers whether the command would
pass. `doctor`, `guard` and `task list` read and stay usable; `sync` is the
fix; `init`, `add` and `remove` manage the map. The workspace root is never
compared: its `main` is the governance itself. An `atc` that records no
installed commit (`uv run atc` in a topic worktree, an editable or local
install) has no installed release, so nothing is compared. `atc doctor`
lists each project's pin against the installed release.

The canonical checkout stays the workspace that `atc` and every session
read: task records, policies, governance and skills. Off `main` every `atc`
command but `doctor` refuses with `CHECKOUT_NOT_MAIN`.

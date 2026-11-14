---
name: atc-worktree
description: Create, resume, list or close a task's git worktree for a project registered in the workspace map, or for the workspace root repo itself, through `atc task`. Not for worktrees another tool manages.
contract: 2
---

# Hami Managed Worktree

Hami Managed Worktree, machine contract 2. Each task gets one worktree, one
branch and one task record, named after the task ID, at a path the workspace
map decides. `atc task` creates, resumes, lists and closes them the same way
in every registered project.

## Identity

`atc task create` allocates the task ID (`<prefix>-<four digits>`, the prefix
the repository's `AGENTS.md` declares under `## Task records`) and derives
everything else; you never pass a path or a branch:

| | Value |
| --- | --- |
| directory | `<project worktrees root>/<harness>+<ID>` |
| branch | `<harness>+<ID>` |
| lock reason | `<harness>:<ID>` |
| record | `.atc/tasks/<ID>.yaml`, committed on the branch |

The **harness** (`claude`, `codex` or `dsh`) comes from the environment the
harness sets; your adapter names it. A session the CLI cannot place stops
with `HARNESS_UNKNOWN`. Never set those variables yourself.

## Running the CLI

The CLI is the `atc` command on PATH. It finds the workspace map itself (the
nearest `workspace.yaml` above the working directory, or the checkout it was
installed from). If `workspace.yaml` exists but `atc` is not on PATH, ask the
user to install it from their Agent Traffic Control checkout with
`uv tool install -e <checkout>/adapters/python`. If no `workspace.yaml`
exists in the current directory or any parent and `atc` is not on PATH, this
is an independent clone: the managed-worktree lifecycle does not apply. Use
the `GIT_WORKFLOW.md` topic-branch procedure in this clone, and report the
branch, tip SHA and base SHA instead of a worktree path. Never substitute raw
`git worktree` commands.

`create` takes `--project <name>`; without it the CLI uses the project whose
repository or worktrees root holds the working directory, and the rest of
the workspace root's checkout is `--project root`. Every other command takes
the task ID, whose prefix names the repository. `--json` prints one object:
`command`, `verdict`, `exit_code`, `hint`, then the command's own fields.

## Commands

| Command | Use | Changes |
| --- | --- | --- |
| `atc task create [TITLE] [--scope S] [--tests none\|standard\|strict] [--file F] [--project P]` | start a new task | adds the worktree on a new branch from local `main`, locked, and commits the record |
| `atc task update ID [--title T] [--decision TEXT]... [--scope S] [--tests L] [--file F]` | record an Owner decision or a changed scope or test level | one commit on the task's branch |
| `atc task resume ID` | continue a task in a new session | nothing; prints the worktree and how many commits `main` has moved since its tip |
| `atc task list [ID] [--project P \| --all] [-v]` | list tasks with their status (`open`, `integrated`, `closed`, `abandoned`) | nothing; `-v` adds each worktree's lock, clean and path findings |
| `atc task close ID [--check]` | the task is integrated and its worktree is no longer needed | re-checks identity, merged and clean, then removes the worktree, its branch (a topic integrated by a recorded, Owner-authorized squash keeps its tip as `archive/<ID>`), the task's registered scratches and its reports; lists the items that need the Owner's consent and deletes none of them |
| `atc task close ID --abandon` | the task authority explicitly abandons the task | tags the tip `archive/<ID>`, then removes the worktree and compare-and-deletes the branch, keeping the tag |

`--check` lists what `close` would delete and deletes nothing. The record's
`keep: [worktree, scratches, reports]` keeps those items on every close.
`close` finds the worktree from the record's `branch`, so a task created
under another naming is closed the same way.

## Results

Success is one verdict line; `-v` adds detail lines. A refusal is
`STOP <CODE>: <detail>` or `ERROR <CODE> <detail>` on stderr, then a `hint:`
line with the concrete next step. Exit status is `0` OK, `1` STOP, `2` ERROR,
`3` PARTIAL (`close` deleted some items and refused another).

| Code | Next step |
| --- | --- |
| `ALREADY_EXISTS` | `atc task resume ID` |
| `NOT_FOUND` | `atc task list -v` shows the task's worktree, if any |
| `HARNESS_UNKNOWN` | run from a harness session; never set its variables by hand |
| `IDENTITY_MISMATCH` | stop and hand to the user — the directory, branch or lock disagree about who owns it |
| `IDENTITY_AMBIGUOUS` | stop and hand to the user — the worktree has no lock, so nothing names its holder |
| `PATH_UNSAFE` | stop and hand to the user; `detail` is one of `outside-root`, `protected`, `reparse`, `unregistered`, `resolved-mismatch`, `external-root-overlap` |
| `DIRTY` | commit or discard the worktree's changes, then run it again |
| `UNMERGED` | integrate the task first; an explicitly authorized abandonment uses `--abandon` |
| `ARCHIVE_MISMATCH` | the tag `archive/<ID>` exists at another commit; ask the user |
| `MAP_INVALID` | fix `workspace.yaml` at the root `atc` resolved (or the `ATC_ROOT` override) |
| `UNKNOWN_PROJECT` | name a registered project with `--project` |
| `PERMISSION_DENIED` | the sandbox, an ACL or a busy directory blocked Git metadata or the worktrees root; leave the directory or get approval, then run it again once |
| `UNSUPPORTED_ENVIRONMENT` | a local capability is missing (Git, `main`, lock-reason support); stop retrying |
| `CANDIDATE_IN_USE` | an `atc integrate` or `atc test --candidate` candidate whose owner still runs; leave it |
| `CANDIDATE_LEFTOVER` | a candidate whose owner is gone; the next `atc test` prunes it |

In `list -v`, `CANDIDATE_IN_USE` is informational; identity conflicts, a
missing lock, `PATH_UNSAFE` and `CANDIDATE_LEFTOVER` are findings.

## Working in the worktree

- Use the absolute path `create` or `resume` prints, and run Git as
  `git -C <path> ...`.
- The Owner integrates the reviewed task with `atc integrate ID` against the
  main checkout; integration does not close the task. Run `atc task close ID`
  after it.
- Run `close` from a working directory outside the worktree, as its own call
  after the last command that ran inside it. A shell still sitting in the
  tree keeps the directory busy and `close` reports `PERMISSION_DENIED`; leave
  the directory and run the same `close` again, which finishes a removal Git
  left half-done.
- The lock stays from `create` until `close`. It guards against removal and
  records identity; it is not an edit lock. Never lock, unlock, move, prune or
  remove a worktree by hand.
- Revision lifecycle and integration follow the bound governance revision's
  `GIT_WORKFLOW.md`; verification follows `VERIFICATION.md`; publication
  follows `DELIVERY.md`.
- Only a top-level session that can run commands drives the CLI. A subagent
  without a shell edits files but takes no part in worktree identity.
- The workspace root repo's canonical checkout stays on `main`, since the
  installed `atc` runs its code; each root task gets its worktree from
  `atc task create --project root`.

## Never

- Retry a refused command unchanged, or work around a refusal with raw Git.
- Touch a worktree another harness created, or a worktree another tool
  manages under its own root.
- Remove a worktree with `--force`.

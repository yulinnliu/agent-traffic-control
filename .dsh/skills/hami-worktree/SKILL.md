---
name: hami-worktree
description: Create, resume, audit or clean up a per-topic git worktree for a project registered in the workspace map, through the shared hami-worktree CLI. Not for the workspace root repo itself, nor for worktrees another tool manages.
requires-contract: 1
managed-by: agent-traffic-control
canonical: hami-worktree
---

# Hami Managed Worktree

Hami Managed Worktree, machine contract 1. Each task gets one worktree at a path the
workspace map decides, and the same four commands create, resume, audit and
remove it in every registered project. Your tool's adapter supplies the two
values you never choose yourself: the **owner** token and the **session** id.

## Inputs

- **project** — the logical name under `projects:` in the workspace map
  (`workspace.yaml` at the root the `atc` command serves; `atc worktree audit`
  lists what it knows).
- **owner** — fixed by your adapter (`claude`, `codex` or `dsh`). Never infer
  it from a model name, an environment variable or a directory name.
- **topic** — the task's durable name, decided by the task authority (the
  user's request, or the topic an earlier session recorded). Lowercase
  letters, digits and hyphens, at most 48 characters, starting with a letter
  or digit, no `+`. Ask when it is unclear instead of coining a near-duplicate.
- **session** — the id your adapter names, passed as `--session`. When no
  verified source exists, omit it (it is recorded as `-`). Never substitute a
  PID, a cwd or "the newest session file".

These derive everything else — you never pass a path:

| | Value |
| --- | --- |
| directory | `<project worktrees root>/<owner>+<topic>` |
| branch | `worktree-<owner>+<topic>` |
| lock reason | `<owner>:<topic>:<session>` |

## Running the CLI

The CLI is the `atc` command on PATH:

```
atc worktree <command> ...
```

It finds the workspace map itself (the nearest `workspace.yaml` above the
working directory, or the checkout it was installed from). If `atc` is not
on PATH, ask the user to install it from their Agent Traffic Control
checkout with `uv tool install -e <checkout>/adapters/python`; if there is
no such checkout, report that this environment lacks the hami-worktree
capability and stop. Never substitute raw `git worktree` commands.

Every call passes `--project <name> --contract 1`; add `--json` for a
machine-readable result: `contract`, `command`, and per target `verdict`
(`OK`, `STOP` for a refusal or finding, `ERROR` for an environment problem),
`reason` (the code below), `target`, `detail`, `next` and `evidence`.

## Commands

Pick the command from the task: a new task is `create`, continuing one is
`resume`. There is deliberately no "create or resume".

| Command | Use | Changes |
| --- | --- | --- |
| `create --owner O --topic T [--session S]` | start a new task | adds the worktree on a new branch from local `main`, locked with the reason above |
| `resume --owner O --topic T [--session S]` | continue a task in a new session | nothing; reports how many commits `main` has moved since the worktree's tip |
| `resume ... --rebind-session` | the task authority explicitly asked to resume this topic from this session | rewrites the lock's session field (unlock then lock; not atomic) |
| `audit [--owner O] [--topic T]` | list a project's worktrees with lock, clean and merged state | nothing; read-only |
| `cleanup --owner O --topic T` | the task is integrated and the worktree is no longer needed | re-checks identity, merged and clean, then removes the worktree and deletes its branch |
| `cleanup --owner O --topic T --abandon --archive-tag archive/NAME --expected-head FULL_SHA` | task authority explicitly abandons this exact topic tip | requires a clean tree and a local archive tag at the exact tip; removes the worktree and compare-and-deletes its branch while retaining the tag |
| `cleanup --owner O --topic T --squash-integrated --expected-head FULL_SHA --reviewed-sha FULL_SHA --expected-main FULL_SHA --integrated-as FULL_SHA --retained-ref refs/tags/NAME` | the topic was integrated by an authorized same-tree squash and cleanup is authorized | requires a clean tree, a single-parent squash commit on the recorded main tip with the topic tip's tree, reachable from current `main`, and an existing retained ref that reaches the tip; removes the worktree and compare-and-deletes its branch while retaining the ref |

## Results

Human output is one line per target: `<CODE> <target> [(<detail>)] - <next step>`.
Exit status is `0` for OK, `1` for a refusal or an audit finding, `2` for a
usage, environment, permission or contract problem.

| Code | Next step |
| --- | --- |
| `OK` | continue |
| `ALREADY_EXISTS` | use `resume` |
| `NOT_FOUND` | `create` it if this is a new task; otherwise confirm the topic |
| `IDENTITY_MISMATCH` | stop and hand to the user — the directory, branch, lock or caller disagree about who owns it |
| `IDENTITY_AMBIGUOUS` | the lock is missing or belongs to another session; use `--rebind-session` only if the task authority asked to resume this topic, otherwise ask the user |
| `LEGACY_TARGET` | a worktree outside the map's worktrees root; leave it to its original owner and the project's current procedure |
| `PATH_UNSAFE` | stop and hand to the user; `detail` is one of `outside-root`, `protected`, `reparse`, `unregistered`, `resolved-mismatch`, `external-root-overlap` |
| `DIRTY` | commit or clean the worktree, then retry |
| `UNMERGED` | integrate it per the lifecycle first, otherwise keep the worktree; explicitly authorized abandonment uses the archive-bound cleanup option; under `--squash-integrated`, `detail` names the recorded integration fact that does not hold |
| `ALREADY_INTEGRATED` | the topic tip is already reachable from `main`; run `cleanup` without `--squash-integrated` |
| `ARCHIVE_MISMATCH` | keep the worktree; verify the local archive tag or retained ref preserves the expected HEAD |
| `HEAD_MISMATCH` | keep the worktree; confirm the exact HEAD authorized for abandonment or squash cleanup |
| `MAP_INVALID` | fix `workspace.yaml` at the root `atc` resolved (or the `ATC_ROOT` override, if set) |
| `UNKNOWN_PROJECT` | fix `--project` |
| `CONTRACT_INCOMPATIBLE` | stop; report the contract your adapter expects and the one the workspace provides |
| `PERMISSION_DENIED` | the sandbox, an ACL or a busy directory blocked Git metadata or the worktrees root; leave the directory or get approval, then retry once |
| `UNSUPPORTED_ENVIRONMENT` | a local capability is missing or unverifiable (Git, `main`, lock-reason support, another tool's worktree root that cannot be read); stop retrying |

In `audit`, `LEGACY_TARGET` is informational; only identity conflicts, a
missing lock and `PATH_UNSAFE` are findings.

## Working in the worktree

- Use the absolute path the CLI printed, and run Git as `git -C <path> ...`.
- The lock stays from `create` until `cleanup`. It guards against removal and
  records identity; it is not an edit lock. Never lock, unlock, move, prune or
  remove a worktree by hand.
- Revision lifecycle/integration follow the bound `shared/governance/GIT_WORKFLOW.md`; verification follows `VERIFICATION.md`; publication/delivery follows `DELIVERY.md`. Default `cleanup` requires
  integration. Explicit task-authorized abandonment requires all three options
  `--abandon`, `--archive-tag archive/NAME` and `--expected-head FULL_SHA`; the
  existing local tag must resolve exactly to the authorized 40-character tip.
  Both modes preserve identity, clean-tree and path-safety checks. Archived
  cleanup retains the tag and uses a ref transaction to verify that tag and
  compare-and-delete the exact topic branch. If refs change after removal, the
  branch is retained and reported as `branch_kept: true`. Successful archived
  branch deletion also clears its local branch settings when the branch remains
  absent; a recreated branch or configuration failure retains settings with an
  evidence warning. A removal Git aborts partway can leave files deleted from a
  clean tree whose commits are retained by `main` or the bound archive tag; the CLI restores the lock
  and reports the lock state it actually observed.
- Squash cleanup (`--squash-integrated`) takes `--expected-head`,
  `--reviewed-sha`, `--expected-main`, `--integrated-as` and `--retained-ref`
  together and never with `--abandon` or `--archive-tag`. The CLI verifies Git
  facts only: the topic tip equals the reviewed SHA, the squash commit has
  exactly one parent equal to the recorded main tip, its tree equals the tip's
  tree, and it is reachable from current `main` (checked again before the
  unlock). `--retained-ref` is an existing local `refs/tags/` ref or
  `refs/heads/` branch that reaches the tip; it is never the topic's own branch
  or another `worktree-*` branch. The ref transaction verifies the retained ref
  and compare-and-deletes the topic branch; it does not protect `main` or keep
  the retained ref in place afterwards.
- Before a squash cleanup the caller establishes, from the current task contract
  or owner instruction read in the same turn, that cleanup of this repository,
  topic and tip is authorized and not revoked; that no other writer is active on
  the topic; that the squash commit message and the recorded integration
  decision match the five SHA arguments; and who owns the retained ref and keeps
  it in place. A CLI `OK` is not authority. If any of these cannot be
  established, do not run it.
- A cleanup `OK` means the worktree was removed. `cleanup_complete: true`
  means the worktree and the branch were both observed gone; `false` means one
  is still present (`branch_kept: true` for the branch); the field is absent
  when the state could not be observed. Report the branch as cleaned only when
  `cleanup_complete` is `true`. After a partial cleanup the next call returns
  `NOT_FOUND` with `branch_present`; the remaining branch is handed to the
  owner, not deleted with raw Git.
- A topic belongs to the tool that created it. Another tool's session gets
  `IDENTITY_MISMATCH`, which `--rebind-session` does not resolve.
- Only a top-level session that can run commands drives the CLI. A subagent
  without a shell edits files but takes no part in worktree identity.
- The workspace root repo itself has no worktrees; it is edited on a topic
  branch in its own checkout.

## Never

- Retry a refused command unchanged, or work around a refusal with raw Git.
- Use `--rebind-session` without an explicit request from the task authority.
- Touch a legacy worktree, another owner's worktree, or a worktree another
  tool manages under its own root.
- Remove with `--force`.

## DSH constants

This tool's values for the two inputs you never choose yourself:

- **owner:** `dsh`
- **session:** `--session $env:DSH_SESSION_ID` on `create` and `resume` only; `audit` and `cleanup` reject it.
- **contract:** pass `--contract 1` on every call.
- The shell is Windows PowerShell 5.1: for example
  `atc worktree audit --project <name> --contract 1`. There is no `&&`;
  chain with `; if ($?) { ... }`.
- If `atc` is missing and the user points you at a checkout, the fallback is
  `uv run --locked --project "<checkout>\adapters\python" atc worktree ...`
  with `--no-cache`; the default uv cache is outside the writable workspace.

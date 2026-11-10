---
name: atc-worktree
description: Create, resume, audit or clean up a per-topic git worktree for a project registered in the workspace map, or for the workspace root repo itself, through the shared `atc worktree` CLI. Not for worktrees another tool manages.
requires-contract: 1
managed-by: agent-traffic-control
canonical: atc-worktree
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
working directory, or the checkout it was installed from). If
`workspace.yaml` exists but `atc` is not on PATH, ask the user to install it
from their Agent Traffic Control checkout with
`uv tool install -e <checkout>/adapters/python`. If no such
Agent Traffic Control checkout is available, report that this environment
lacks the `atc-worktree` capability and stop. If no `workspace.yaml`
exists in the current directory or any parent and `atc` is not on PATH, this
is an independent clone: the managed-worktree lifecycle does not apply. Use
the `GIT_WORKFLOW.md` topic-branch procedure in this clone, and report the
branch, tip SHA and base SHA instead of a worktree path. Never substitute raw
`git worktree` commands.

Every call passes `--project <name> --contract 1 --json`. With `--project` absent,
the CLI infers the project whose repository or worktrees root holds the
working directory; an explicit `--project` always wins. The workspace root
repo takes `--repo <workspace root>` in place of `--project`; its worktrees
live under `worktrees/root/`, a working directory there infers it, and the
rest of its canonical checkout infers nothing. Read the result from
`--json`: `contract`, `command`, and per target `verdict`
(`OK`, `STOP` for a refusal or finding, `ERROR` for an environment problem),
`reason` (the code below), `target`, `detail`, `next` and `evidence`.

## Commands

Pick the command from the task: a new task is `create`, continuing one is
`resume`. There is deliberately no "create or resume".

A topic already published on `origin` with no managed worktree here is
continued with `create --base-sha`, given the exact 40-character tip of the
topic's own branch on `origin` (`worktree-<owner>+<topic>` for this owner and
topic). The CLI asks `origin` for that branch, refuses any other SHA with
`BASE_MISMATCH` and creates nothing, fetches the tip when it is not present
locally, and starts the new branch at that SHA. Report the published tip as the
worktree's base.

| Command | Use | Changes |
| --- | --- | --- |
| `create --owner O --topic T [--session S]` | start a new task | adds the worktree on a new branch from local `main`, locked with the reason above |
| `create ... --base-sha FULL_SHA` | continue a topic published on `origin` that has no worktree here | verifies the SHA is the exact tip of the topic's branch on `origin`, fetches it if missing, adds the worktree on a new branch at that SHA, locked as above |
| `resume --owner O --topic T [--session S]` | continue a task in a new session | nothing; reports how many commits `main` has moved since the worktree's tip |
| `resume ... --rebind-session` | the task authority explicitly asked to resume this topic from this session | rewrites the lock's session field (unlock then lock; not atomic) |
| `audit [--owner O] [--topic T]` | list a project's worktrees with lock, clean and merged state | nothing; read-only |
| `cleanup --owner O --topic T` | the task is integrated and the worktree is no longer needed | re-checks identity, merged and clean, then removes the worktree and deletes its branch; finishes a removal Git left half-done (below) |
| `cleanup --owner O --topic T --abandon --archive-tag archive/NAME --expected-head FULL_SHA` | task authority explicitly abandons this exact topic tip | requires a clean tree and a local archive tag at the exact tip; removes the worktree and compare-and-deletes its branch while retaining the tag |
| `cleanup --owner O --topic T --squash-integrated --expected-head FULL_SHA --reviewed FULL_SHA --expected-main FULL_SHA --integrated-as FULL_SHA --retained-ref refs/tags/NAME` | the topic was integrated by an authorized same-tree squash and cleanup is authorized | requires a clean tree, a single-parent squash commit on the recorded main tip with the topic tip's tree, reachable from current `main`, and an existing retained ref that reaches the tip; removes the worktree and compare-and-deletes its branch while retaining the ref |

## Results

Human output is one line per target for a person to read:
`<CODE> <project>[/<worktree>] [(<detail>)] - <next step>`, with 12-character
SHAs; paths and full SHAs are in `--json`.
Exit status is `0` for OK, `1` for a refusal or an audit finding, `2` for a
usage, environment, permission or contract problem.

| Code | Next step |
| --- | --- |
| `OK` | continue |
| `RECOVERED` | `cleanup` finished a removal Git left half-done; continue |
| `ALREADY_EXISTS` | use `resume` |
| `NOT_FOUND` | `create` it if this is a new task; otherwise confirm the topic |
| `IDENTITY_MISMATCH` | stop and hand to the user — the directory, branch, lock or caller disagree about who owns it |
| `IDENTITY_AMBIGUOUS` | the lock is missing or belongs to another session; use `--rebind-session` only if the task authority asked to resume this topic, otherwise ask the user |
| `LEGACY_TARGET` | a worktree outside the map's worktrees root; leave it to its original owner and the project's current procedure |
| `PATH_UNSAFE` | stop and hand to the user; `detail` is one of `outside-root`, `protected`, `reparse`, `unregistered`, `resolved-mismatch`, `external-root-overlap` |
| `DIRTY` | commit or clean the worktree, then retry |
| `UNMERGED` | integrate it per the lifecycle first, otherwise keep the worktree; explicitly authorized abandonment uses the archive-bound cleanup option; under `--squash-integrated`, `detail` names the recorded integration fact that does not hold |
| `ALREADY_INTEGRATED` | the topic tip is already reachable from `main`; run `cleanup` without `--squash-integrated` |
| `BASE_MISMATCH` | `--base-sha` is not the exact tip of the topic's own branch on `origin`, or that branch does not exist there; `evidence` holds `base_sha` and, when the branch exists, `remote_tip`. Confirm the published tip with the task authority, or `create` from `main` without the option |
| `ARCHIVE_MISMATCH` | keep the worktree; verify the local archive tag or retained ref preserves the expected HEAD |
| `HEAD_MISMATCH` | keep the worktree; confirm the exact HEAD authorized for abandonment or squash cleanup |
| `MAP_INVALID` | fix `workspace.yaml` at the root `atc` resolved (or the `ATC_ROOT` override, if set) |
| `UNKNOWN_PROJECT` | fix `--project` |
| `CONTRACT_INCOMPATIBLE` | stop; report the contract your adapter expects and the one the workspace provides |
| `PERMISSION_DENIED` | the sandbox, an ACL or a busy directory blocked Git metadata or the worktrees root; leave the directory or get approval, then retry once. A retried `cleanup` finishes a removal Git left half-done and reports `RECOVERED` |
| `UNSUPPORTED_ENVIRONMENT` | a local capability is missing or unverifiable (Git, `main`, lock-reason support, another tool's worktree root that cannot be read); stop retrying |

In `audit`, `LEGACY_TARGET` is informational; only identity conflicts, a
missing lock and `PATH_UNSAFE` are findings.

## Working in the worktree

- Use the absolute path in the `--json` result's `target`, and run Git as `git -C <path> ...`.
- Integrate the reviewed topic with `atc integrate` against the main
  checkout; it takes the branch and reviewed SHA, not the worktree. Run
  `cleanup` after it.
- Run `cleanup` from a working directory outside the worktree, as its own
  call after the last command that ran inside it (a merge step included). A
  shell still sitting in the tree keeps the directory busy: Git unregisters the
  worktree and deletes its files, the empty directory and the branch remain,
  and `cleanup` returns `PERMISSION_DENIED`. Leave the directory and run the
  same `cleanup` again. It finishes the removal and reports `RECOVERED` only
  when the path is not registered as a worktree, the directory is empty (no
  files, no reparse points) and the topic branch is merged into `main` or
  already gone; it removes the empty directory and compare-and-deletes a
  branch still there. Otherwise it refuses with `PATH_UNSAFE` `unregistered`,
  `evidence` `recovery_refused` names the failing condition, and nothing is
  deleted.
  Abandoned and squash cleanup never recover this way.
- The lock stays from `create` until `cleanup`. It guards against removal and
  records identity; it is not an edit lock. Never lock, unlock, move, prune or
  remove a worktree by hand.
- Revision lifecycle/integration follow the bound governance revision's `GIT_WORKFLOW.md`; verification follows `VERIFICATION.md`; publication/delivery follows `DELIVERY.md`. Default `cleanup` requires
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
  `--reviewed`, `--expected-main`, `--integrated-as` and `--retained-ref`
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
- The workspace root repo's canonical checkout stays on `main`, since the
  installed `atc` runs its code; each root topic gets a worktree from
  `atc worktree create --repo <workspace root>`.

## Never

- Retry a refused command unchanged, or work around a refusal with raw Git.
- Use `--rebind-session` without an explicit request from the task authority.
- Touch a legacy worktree, another owner's worktree, or a worktree another
  tool manages under its own root.
- Remove with `--force`.

## Claude Code constants

This tool's values for the two inputs you never choose yourself:

- **owner:** `claude`
- **session:** `--session "$CLAUDE_CODE_SESSION_ID"` on `create` and `resume` only; `audit` and `cleanup` reject it.
- **contract:** pass `--contract 1` on every call.
- Reach the worktree through the absolute path the CLI prints: `git -C <path>`,
  absolute file paths, or a subshell `( cd <path> && ... )`. A top-level `cd`
  into the worktree becomes the session's working directory for every later
  call, which keeps the directory busy and makes `cleanup` stop halfway.
- Create a topic's worktree with the CLI only; the native worktree tool's root
  inside the repo is a legacy location for this contract. To work inside a
  worktree the CLI created, enter it with the native tool by its `path`, and
  leave it with the keep action before `cleanup`; the native remove action is
  not the teardown.

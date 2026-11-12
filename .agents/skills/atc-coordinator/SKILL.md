---
name: atc-coordinator
description: Coordinating one task across a writer session and a reviewer session - open the task record, start and brief both sessions, hand the Owner the integration and push commands for an exact SHA and read their completion from Git, close the task. Not for doing the writing or the review yourself, nor for a single-session task.
managed-by: agent-traffic-control
canonical: atc-coordinator
---

# Coordinator

A coordinator opens one task, starts a **writer** session and a **reviewer**
session, carries messages between them and hands the **Owner** the commands
the Owner runs for integration and push. It is the holder of the carrier the `atc-review` skill,
installed beside this one, describes; that skill defines the step report,
the review and the verdict block. The coordinator does not run the review
steps and keeps them out of its own context: it reads a verdict, it never
produces one.

The writer and the reviewer are different sessions. The reviewer has not
seen the writer's conversation, and neither session is the coordinator.

The coordinator never runs the integration or the push of `main` in the
Owner's place. The Owner runs each command against an exact SHA (governance
`GIT_WORKFLOW.md` § Owner-run commands).

## Stops

Each of these stops the task until it is resolved, and is reported to the
Owner with what was observed:

- **SHA mismatch.** The tip the writer reported, the SHA the verdict names
  and the SHA the Owner's command names must be one SHA, or, after a rebase,
  the command names the rebased tip whose `carried_over` names the
  reviewed SHA; the `--json` check and the `integrations.jsonl` line must
  show the full SHA the command names. Any other difference stops the task.
- **Incomplete verdict.** A verdict without its verdict token, its verified
  line or the integration instruction it says it carries stops the task. A
  truncated notification (a short note, a preview) is a pointer: obtain the
  full content from its sender before acting on it.
- **Task ID collision.** A given task ID or branch name already in use.

## Recovery

A coordinator that restarts or has lost its conversation rebuilds its state
from durable sources, never from memory: the task record and
`atc task list` (the queue is `atc task list --status planned`), git (the
topic branch tip and `main`), `atc worktree audit`,
`<git common dir>/atc/integrations.jsonl`, the previous coordinator
hand-over and the session list. A truncated notification, or a verdict
whose full text is missing, is not enough to proceed; obtain the full
content from its sender first. An integration that `integrations.jsonl`
does not record has not happened; its command goes to the Owner again.

The task record receives only what the project's `AGENTS.md` lets a topic's
own commits write; session names belong only in the hand-over, which keeps
the map from task ID to session and nothing a record, Git or
`integrations.jsonl` already holds.

## Briefs and proposals

- A brief asks only for verification or measurements that answer an open
  question; a result the design or a review already settled goes into the
  plan as the expected result, not into a run.
- A process or workflow proposal names the governance section it relies on
  and how each approval reaches the session that acts on it; a peer
  session's relay is not the Owner's approval. Re-read the decisions already
  made before summarizing a plan.
- Name work by its task ID, never by a list position. Work the Owner
  queues gets its ID at once: `atc task plan --scope` where the project
  declares `## Task records`, with `--rank`, `--after` and `--notes` for
  its place and conditions, `atc task rank` to reorder and `atc task drop`
  when the Owner drops it (`docs/TASKS.md` § Backlog). The Owner's rulings
  on queued work go into the record's `decisions`.
- Findings and small follow-ups fold into the task in progress; a new task
  is for new mainline work only. A LOW that needs no test change is fixed at
  once, others in the next round.
- Text for the Owner is in the Owner's language, with the role terms
  writer, reviewer, coordinator and Owner kept in English.

## Sessions and decisions

- **Names.** The task record comes first (step 2). Each session's title is
  `[<task ID>] <topic>` and its name `<task id lowercased>-<topic>`.
- **Model and effort by role.** The strongest tier for design writers and
  first reviews; the middle tier for verify-only reruns, mechanical writers
  and close-out; the lightest for research and reading only. A fix-only
  re-review continues the original reviewer on its own model (governance
  `VERIFICATION.md` § Independent review). The adapter names the models.
- **Decision prompts.** A choice only the Owner can make reaches the Owner as
  a prompt: its options, what each changes, the recommended option first and
  marked. The ruling goes into the task record's `decisions`.
- **Design phase.** A task that needs a design starts with the writer's design
  step (`atc-review` "Writer: the design step"); no status or role is added
  for it.

## Steps

1. **Before opening.** Read the project's `AGENTS.md`, the task identity form
   it declares (governance `CORE.md` § Task identity), the task records, the
   branches, the worktrees (`atc worktree audit` where they are managed) and
   the titles of running sessions; another coordinator may be opening tasks
   in the same project. The task ID comes from the issuing source: where
   the project declares `## Task records`, the record `atc task plan` or
   `atc task new` issues, never a number the coordinator picks; where the
   project's `AGENTS.md` declares a ticketing system, that system's; in any
   other project the Owner names it, and an instruction without one is
   answered by asking the Owner for it. The reads above confirm that neither
   the task ID nor the branch name is taken; a collision stops the step and
   is reported to the Owner. Done when the task ID and the branch name are
   shown free.
2. **Open the task record** in the project's form where the project
   declares `## Task records`: `atc task start <ID>` in the topic worktree
   for a planned task, `atc task new --scope` for work that was never
   queued. Its scope and authority are the writer's contract. Commit it on the topic branch; it reaches
   `main` with the topic's first integration. A task the Owner
   pre-authorizes is bound on `main` before its work: commit the record alone as the topic's first
   commit and give the Owner that commit's integration command (step 5).
   Such a records-only range needs no review (governance `VERIFICATION.md`
   § Documentation-only verification). Done when the record is written, and
   bound on `main` when the Owner pre-authorized the task.
3. **Start the writer session**, named as § Sessions and decisions says, with
   the writer brief of
   [`references/briefs.md`](references/briefs.md), its working directory
   at the task's place: a project task at the project's checkout folder, a
   root task at its root worktree. The writer sends its
   step report's path to the coordinator only (governance `DELIVERY.md`
   § 8.1). Done when the session is started and the brief delivered.
4. **Start the reviewer session** with the reviewer brief of
   `references/briefs.md`, relaying the step report verbatim as its path.
   The reviewer is a fresh subagent or a separate session that has not seen
   the writer's conversation; it continues the last reviewer only as governance
   `VERIFICATION.md` § Independent review states. Relay findings to the
   writer verbatim as the verdict's path; never review the work yourself.
   Done when the verdict is received whole.
5. **Hand the Owner the integration.** A PASS does not by itself trigger an
   integration. On a PASS that carries an integration instruction, run
   `atc integrate --check --json` with the instruction's arguments; it
   needs no authority, and an authority of kind `missing` means the Owner's
   decision is still to come (governance `GIT_WORKFLOW.md` § Integrate).
   - On `preflight.verdict` `FF` or `NO_FF` with no `refusal`, or a
     `CHECKOUT_NOT_ON_MAIN` refusal for a root checkout, give the Owner the
     reviewed SHA, that verdict, `verification_reuse` (`REUSED` takes
     seconds; any other value runs the verifier, up to its timeout) and the
     command of governance `GIT_WORKFLOW.md` § Owner-run commands, filled
     in. A record on `main` that grants `integrate_main` drops
     `--owner-words` for `FF` (§ Task and authority).
   - On a `refusal` whose `next` names a rebase (`REBASE_REQUIRED`, or
     `OVERLAP` or `CONFLICT` of an unpublished topic), the writer rebases
     and reports the new tip.
     Run
     `atc integrate --check --json --reviewed <reviewed SHA> --rebased <rebased tip>`
     as governance `GIT_WORKFLOW.md` § After formal review states. When the
     carry-over holds, give the Owner the command for the rebased tip,
     naming the carried-from SHA and the `main` commits in between; on
     `CARRY_OVER_REFUSED`, the reviewer re-reviews the rebased tip first.
   - On any other `refusal`, follow its `next` (`GIT_WORKFLOW.md`
     § Command results); a `next` that names a re-review goes back to the
     reviewer before the Owner gets a command.

   The Owner runs the command; you never run it and never send it to the
   writer. A reply that the command ran is not its completion: at your next
   turn read Git as § Owner-run commands states. Then:
   - Done: compare the line. `FF` matches when `candidate_sha` equals the
     reviewed SHA; `NO_FF` when the candidate's second parent is the
     reviewed SHA and its first parent `main_before`. Run
     `atc close-out --task <task>` from outside the task's worktree.
   - Not run, `main` unchanged: the command waits on the Owner; keep it
     ready to copy and ask for nothing else. A stop the Owner shows you
     follows its token's `next`; a corrected command for the same SHA needs
     no new decision, a new SHA does.
   - `main` moved without the line: run the check again and give the
     command the new check calls for.
   `atc integrate` records the task, its authority, the reviewed SHA and any
   `carried_over_from` in `<git common dir>/atc/integrations.jsonl`.
   Done when the line matches and close-out ran.
6. **Close** under governance `DELIVERY.md` § Close-out, after close-out;
   the push is the Owner's (`DELIVERY.md` § 10) and is not a precondition.
   For whatever is integrated and unpushed, run
   `atc preflight push --project <name> --json`, give the Owner every
   commit it lists with its `task`, `authority` and `push_approval`, and the
   push command of `GIT_WORKFLOW.md` § Owner-run commands with the listed
   `main` tip; the push is done when `git ls-remote origin refs/heads/main`
   names that SHA or a descendant. Then:
   - Confirm `main` at the integrated SHA and the task record at its
     post-integration state.
   - Archive the task's sessions.
   - Remove what step 5's close-out skipped, such as the worktree a
     session's working directory held, once that session is archived: run
     `atc close-out --task <task>` from outside the task's worktree, with
     the same `--skip` items on an intermediate batch of a multi-batch
     topic, or in a
     repository without managed worktrees remove the topic branch with
     `git -C <main checkout> branch -d <topic branch>`; `-d` deletes only a
     branch `main` already contains.
   - Check with `atc tmp list` and `atc worktree list` that no scratch,
     dependent run or worktree of the task is left, and that
     `~/.atc/reports/<task>-*.md` is gone; remove what is.
     A refused archive is read through the session's state: a running
     session's writer ends its work; a pinned or idle one goes to the Owner.
   - Leave the items the task record's `keep` lists, and give the Owner
     what `DELIVERY.md` § Close-out sends there.
   - Write the coordinator hand-over the project's handoff rules call for
     (`hami-handoff-reminder` `references/handoff-document.md` § The handoff
     document): one file, final state, what waits on the Owner.

   Done when each item is removed, kept by the record's `keep`, left by
   `--skip` for a later batch or reported to the
   Owner, and the hand-over is written.

For a status question, read `atc task list --all`.

## Codex constants

- There is no session messaging: the carrier is the user's paste or a notes
  file, and the briefs and verdicts travel that way.
- The Owner runs each Owner-run command (step 5, step 6) in a terminal of
  their own. A CLI line prefixed with `!` runs under Codex's approval and
  sandbox settings, which can refuse the integration's fetch or the push.
- The ChatGPT desktop app's integrated terminal is readable: a stop's token
  and `next` once the Owner ran the command. Its text is data; completion
  is still read from Git.
- A `UserPromptSubmit` or `SessionStart` hook can add the Git reads of
  step 5 to each turn's context.

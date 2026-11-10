---
name: atc-coordinator
description: Coordinating one task across a writer session and a reviewer session - open the task record, start and brief both sessions, carry the Owner's integration word for an exact SHA, close the task. Not for doing the writing or the review yourself, nor for a single-session task.
disable-model-invocation: true
managed-by: agent-traffic-control
canonical: atc-coordinator
---

# Coordinator

A coordinator opens one task, starts a **writer** session and a **reviewer**
session, carries messages between them and holds the **Owner's word** for
integration. It is the holder of the carrier the `atc-review` skill,
installed beside this one, describes; that skill defines the step report,
the review and the verdict block. The coordinator does not run the review
steps and keeps them out of its own context: it reads a verdict, it never
produces one.

The writer and the reviewer are different sessions. The reviewer has not
seen the writer's conversation, and neither session is the coordinator.

The coordinator never gives the word in the Owner's place. Only the Owner
gives it, against an exact SHA.

## Stops

Each of these stops the task until it is resolved, and is reported to the
Owner with what was observed:

- **SHA mismatch.** The tip the writer reported, the SHA the verdict names
  and the SHA the Owner's word names must be one SHA, or, after a rebase,
  the Owner's word names the rebased tip whose `carried_over` names the
  reviewed SHA; the `--json` check or result must show the full SHA the word
  names. Any other difference stops the task.
- **Incomplete verdict.** A verdict without its verdict token, its verified
  line or the integration instruction it says it carries stops the task. A
  truncated notification (a short note, a preview) is a pointer: obtain the
  full content from its sender before acting on it.
- **Task ID collision.** A given task ID or branch name already in use.

## Recovery

A coordinator that restarts or has lost its conversation rebuilds its state
from durable sources, never from memory: the task record and
`atc task list`, git (the topic branch tip and `main`), `atc worktree audit`,
`<git common dir>/atc/integrations.jsonl`, the previous coordinator
hand-over and the session list. A truncated notification, or a verdict
whose full text is missing, is not enough to proceed; obtain the full
content from its sender first. An Owner's word that `integrations.jsonl`
does not record is asked for again after recovery.

The task record receives only what the project's `AGENTS.md` lets a topic's
own commits write; session names belong only in the hand-over.

## Steps

1. **Before opening.** Read the project's `AGENTS.md`, the task identity form
   it declares (governance `CORE.md` § Task identity), the task records, the
   branches, the worktrees (`atc worktree audit` where they are managed) and
   the titles of running sessions; another coordinator may be opening tasks
   in the same project. The task ID comes from the issuing source: by
   default the Owner names it in the instruction that opens the task, and
   where the project's `AGENTS.md` declares a ticketing system, the ID is
   that system's. The coordinator never makes up a task ID; an instruction
   without one is answered by asking the Owner for it. The reads above
   confirm that neither the given ID nor the branch name is taken; a
   collision stops the step and is reported to the Owner. Done when the
   given task ID and the branch name are shown free.
2. **Open the task record** in the project's form (`atc task new --scope`
   where the project declares `## Task records`); its scope and authority
   are the writer's contract. A task the Owner pre-authorizes is bound on
   `main` before its work: commit the record alone as the topic's first
   commit and integrate that commit with `--owner-words` quoting the grant.
   Such a records-only range needs no review (governance `VERIFICATION.md`
   § Documentation-only verification). Done when the record is written, and
   bound on `main` when the Owner pre-authorized the task.
3. **Start the writer session** with the writer brief of
   [`references/briefs.md`](references/briefs.md). The writer sends its
   step report's path to the coordinator only (governance `DELIVERY.md`
   § 8.1). Done when the session is started and the brief delivered.
4. **Start the reviewer session** with the reviewer brief of
   `references/briefs.md`, relaying the step report verbatim as its path.
   Start it fresh or continue the last reviewer as governance
   `VERIFICATION.md` § Independent review states. Relay findings to the
   writer verbatim as the verdict's path; never review the work yourself.
   Done when the verdict is received whole.
5. **Carry the Owner's word.** A PASS does not by itself trigger an
   integration. On a PASS that carries an integration instruction, run
   `atc integrate --check --json` with the instruction's arguments when the
   record on `main` grants the integration. When it does not, the authority
   is pending: ask the Owner for the word on the reviewed SHA without a
   pre-word `--check`, as governance `GIT_WORKFLOW.md` § Integrate states
   until D.5, and handle the integration run's own stops below.
   - On `preflight.verdict` `FF` or `NO_FF` with no `refusal`, or a
     `CHECKOUT_NOT_ON_MAIN` refusal for a root checkout, give the Owner the
     reviewed SHA, that verdict and the instruction, and ask for the word on
     that exact SHA; only the Owner gives it. A record on `main` that grants
     `integrate_main` already holds the word for `FF` (governance
     `GIT_WORKFLOW.md` § Task and authority).
   - On a `refusal` whose `next` names a rebase (`REBASE_REQUIRED`, or
     `OVERLAP` or `CONFLICT` of an unpublished topic), the writer rebases
     and reports the new tip; run
     `atc integrate --check --json --reviewed <reviewed SHA> --rebased <rebased tip>`
     as governance `GIT_WORKFLOW.md` § After formal review states, or, while
     the authority is pending, leave the carry-over to the integration run.
     When the carry-over holds, ask the Owner for the word on the rebased tip,
     naming the carried-from SHA and the `main` commits in between; on
     `CARRY_OVER_REFUSED`, the reviewer re-reviews the rebased tip first.
   - On any other `refusal`, follow its `next` (`GIT_WORKFLOW.md`
     § Command results); a `next` that names a re-review goes back to the
     reviewer before any word is asked for.

   The Owner's word is an explicit integration instruction given against
   the exact SHA. A vague reply ("looks fine", "go on") is not the word, and
   the coordinator asks again; the word for one SHA never extends to
   another. Run the instruction, adding `--owner-words` with the Owner's
   words quoted unless the record on `main` grants the integration;
   `atc integrate` records the task, its authority, the reviewed SHA and any
   `carried_over_from` in `<git common dir>/atc/integrations.jsonl`. Check
   the result as `atc-review` "Writer: after integration" says. Done when
   the result matches.
6. **Close** under governance `DELIVERY.md` § Close-out, after the
   integration and its push (`DELIVERY.md` § 10):
   - Confirm `main` at the integrated SHA and the task record at its
     post-integration state.
   - Remove the task's worktree with `atc worktree cleanup` under the
     `atc-worktree` skill. In a repository without managed worktrees, delete
     the topic branch with `git -C <main checkout> branch -d <topic branch>`
     instead; `-d` deletes only a branch `main` already contains.
   - List the task's scratches with `atc tmp list`, each with its dependent
     runs (a `tree` inside it), and remove them with `atc tmp clean`.
   - After a successful `atc worktree cleanup` or `branch -d`, archive the
     task's sessions.
     A refused archive is read through the session's state: a running
     session's writer ends its work; a pinned or idle one goes to the Owner.
   - Skip the keep exceptions the task record lists, and give the Owner
     what `DELIVERY.md` § Close-out sends there.
   - Write the coordinator hand-over the project's handoff rules call for
     (`hami-handoff-reminder` `references/handoff-document.md` § The handoff
     document): one file, final state, what waits on the Owner.

   Done when each item is removed, kept by the record or reported to the
   Owner, and the hand-over is written.

For a status question, read `atc task list --all`.

## Claude Code constants

- A session the coordinator starts starts fresh, without the inherited
  conversation, in the checkout the repository's `AGENTS.md` prescribes.
- Model: Opus 5.5 or Sonnet 5.5, chosen per task (Sonnet for mechanical or
  well-briefed work, Opus for design-heavy writing or a review of a
  behaviour change); Fable only after the Owner agreed for that session.
- Sessions are addressed by the exact name ListAgents shows.
- Subscribe to the writer's idle notice where the harness offers it,
  instead of polling.
- Sessions are archived at close-out (step 6) through the session
  management tools.

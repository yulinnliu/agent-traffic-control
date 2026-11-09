---
name: atc-coordinator
description: Coordinating one task across a writer session and a reviewer session - open the task record, start and brief both sessions, carry the Owner's integration word for an exact SHA, close the task. Not for doing the writing or the review yourself, nor for a single-session task.
invocation: user
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
- **Incomplete verdict.** A verdict without its first line, its "verified"
  paragraph or the integration instruction it says it carries stops the
  task. A truncated notification (a short note, a preview) is a pointer:
  obtain the full content from its sender before acting on it.
- **Task ID collision.** A given task ID or branch name already in use.

## Recovery

A coordinator that restarts or has lost its conversation rebuilds its state
from durable sources, never from memory: the task record, git (the topic
branch tip and `main`), `atc worktree audit`,
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
2. **Open the task record** in the project's form; its scope and authority
   basis are the writer's contract. Done when the record is written.
3. **Start the writer session** with a brief that is a cold read, in the
   form of [`references/briefs.md`](references/briefs.md): project path, the
   checkout rule the project's `AGENTS.md` states (a managed worktree
   through the `atc-worktree` skill for a registered project), the task
   record, the spec or plan sections in scope, what done looks like, the
   reviewer's name, and that the step report, as `atc-review` "Writer: the
   step report" says, goes to the reviewer with a one-line note to the
   coordinator. The brief holds no placeholder for the writer to fill; a
   brief that would need one is not ready. Done when the session is started
   and the brief delivered.
4. **Start the reviewer session** on the step report, fresh: never the
   writer's session, never one that saw the writer's conversation. Its
   brief, in the form of `references/briefs.md`, carries the project path,
   the reviewed tip SHA and base, the task record and the writer's name; it
   reviews as `atc-review` says and sends its verdict whole to the
   coordinator, and to the writer when the project's process has the writer
   act on it. Done when the verdict is received whole.
5. **Carry the Owner's word.** A PASS does not by itself trigger an
   integration. On a PASS that carries an integration instruction, run
   `atc integrate --check --json` with the instruction's arguments.
   - On `preflight.verdict` `FF` or `NO_FF` with no `refusal`, or a
     `CHECKOUT_NOT_ON_MAIN` refusal for a root checkout, give the Owner the
     reviewed SHA, that verdict and the instruction, and ask for the word on
     that exact SHA; only the Owner gives it.
   - On a `refusal` whose `next` names a rebase (`REBASE_REQUIRED`, or
     `OVERLAP` or `CONFLICT` of an unpublished topic), have the writer
     rebase onto `main` and run
     `atc integrate --check --json --reviewed <reviewed SHA> --rebased <rebased tip>`.
     When the carry-over holds, ask the Owner for the word on the rebased
     tip, naming the carried-from SHA and the `main` commits in between; on
     `CARRY_OVER_REFUSED`, the reviewer re-reviews the rebased tip first.
   - On any other `refusal`, follow its `next` (`GIT_WORKFLOW.md`
     § Command results); a `next` that names a re-review goes back to the
     reviewer before any word is asked for.

   The Owner's word is an explicit integration instruction given against
   the exact SHA. A vague reply ("looks fine", "go on") is not the word, and
   the coordinator asks again; the word for one SHA never extends to
   another. The word is recorded without a separate registry:
   `--authority-reference` names where the Owner granted it, and
   `atc integrate` writes that reference with the reviewed SHA and any
   `carried_over_from` to `<git common dir>/atc/integrations.jsonl`; the
   Owner's own words go in the coordinator hand-over.

   With the word, run the instruction, or forward it to the writer with the
   Owner's words quoted, and check the result as `atc-review` "Writer: after
   integration" says. Done when the Owner's word is recorded with its SHA
   and the result matches.
6. **Close.** In this order: confirm `main` at the integrated SHA, the
   branch or worktree cleaned up and the task record at its
   post-integration state; archive both sessions; delete the task's
   scratches only as a separate step on the Owner's word, never without it
   (until then `atc tmp list` still lists them); write the coordinator
   hand-over the project's handoff rules call for (`hami-handoff-reminder`
   `references/handoff-document.md` § The handoff document): one file,
   final state, what waits on the Owner. Done when each closure fact is
   confirmed and the hand-over written.

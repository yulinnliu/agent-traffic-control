---
name: atc-coordinator
description: Coordinating one task across a writer session and a reviewer session - open the task record, start and brief both sessions, hand the Owner the integration and push commands for an exact SHA and read their completion from Git, close the task. Not for doing the writing or the review yourself, nor for a single-session task.
disable-model-invocation: true
managed-by: agent-traffic-control
canonical: atc-coordinator
---

# Coordinator

A coordinator opens one task, starts a **writer** session and a **reviewer**
session, carries messages between them and hands the **Owner** the commands
the Owner runs for integration and push. It is the holder the `atc-review`
skill, installed beside this one, describes; that skill defines the step
report, the review and the verdict block. The coordinator does not run the
review steps and keeps them out of its own context: it reads a verdict, it
never produces one.

The writer and the reviewer are different sessions. The reviewer has not
seen the writer's conversation, and neither session is the coordinator.

The coordinator never runs the integration or the push of `main` in the
Owner's place. The Owner runs each command against an exact SHA (governance
`GIT_WORKFLOW.md` § Owner-run commands).

## Clearances and standard procedure

The coordinator runs the standard procedure of this skill on its own and asks
the Owner only for a clearance.

- **Clearances, the Owner's:** the integration of each `main` SHA; every
  push, tag and publication; a ruling on scope or design; a governance
  change; a deletion outside the standard close-out.
- **Standard procedure, no question:** the worktree and the task record; the
  writer and reviewer sessions; relaying reports and verdicts; the fix
  re-review; `atc integrate <task> --check`; `atc push --check`;
  `atc task close` and archive (step 6). Each is reported once done.

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
`atc task list` (`-v` adds each worktree's findings), git (the topic
branch tip and `main`),
`<git common dir>/atc/integrations.jsonl`, the previous coordinator
hand-over and the session list. A truncated notification or a verdict
whose full text is missing is handled as § Stops says. An integration that
`integrations.jsonl` does not record has not happened; its command goes to
the Owner again.

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
- Name work by its task ID, never by a list position. A task gets its ID
  when `atc task create` opens it; work the Owner queues for later stays in
  the coordinator hand-over until then. The Owner's rulings go into the
  record's `decisions` (`atc task update <ID> --decision`).
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
  a prompt in the Owner's language, one decision per question: its options,
  what each changes, the recommended option first and marked. The ruling
  goes into the task record's `decisions`.
- **Design phase.** A task that needs a design starts with the writer's design
  step (`atc-review` "Writer: the design step"); no status or role is added
  for it.

## Steps

1. **Before opening.** Read the project's `AGENTS.md`, the task identity form
   it declares (governance `CORE.md` § Task identity), the task records, the
   branches, the worktrees (`atc task list -v` where they are managed) and
   the titles of running sessions; another coordinator may be opening tasks
   in the same project. The task ID comes from the issuing source: where
   the project declares `## Task records`, the ID `atc task create` issues,
   never a number the coordinator picks; where the
   project's `AGENTS.md` declares a ticketing system, that system's; in any
   other project the Owner names it, and an instruction without one is
   answered by asking the Owner for it. A task ID or branch name already
   taken is a collision (§ Stops). Done when the reads show the task ID and
   the branch name free.
2. **Open the task** where the project declares `## Task records`:
   `atc task create "<title>" --project <name> [--tests <level>]`, or
   `--file <brief.md>` with `# title`, `## Scope`, `## Decisions` and
   `## Tests`. It creates the worktree and branch and commits the record on
   the branch; the record reaches `main` with the task's integration. Its
   scope, decisions and test level are the writer's contract (governance
   `VERIFICATION.md` § Testing, Test levels); `tests: none` needs the Owner's
   decision line. Done when `create` prints the worktree.
3. **Start the writer session**, named as § Sessions and decisions says, with
   the writer brief of [`references/briefs.md`](references/briefs.md), its
   working directory at the task's place: a project task at the project's
   checkout folder, a root task at its root worktree. The writer sends its
   step report's path to the coordinator only (governance `DELIVERY.md`
   § 8.1). Done when the session is started and the brief delivered.
4. **Start the reviewer session** with the reviewer brief of
   `references/briefs.md`, relaying the step report verbatim as its path.
   The reviewer is a fresh subagent or a separate session that has not seen
   the writer's conversation; it continues the last reviewer only as
   governance `VERIFICATION.md` § Independent review states. The reviewer,
   subagent or session, saves its verdict file itself; relay that path,
   never a transcription of the verdict, and relay findings to the writer as
   the same path; never review the work yourself. Done when the verdict's
   file is there and read whole.
5. **Hand the Owner the integration.** A PASS does not by itself trigger an
   integration. On a PASS that carries an integration instruction, run
   `atc integrate <task> --sha <SHA> --check --json`; it needs no authority
   (governance `GIT_WORKFLOW.md` § Integrate).
   - On `preflight.verdict` `FF` or `NO_FF` with no `refusal`, or a
     `CHECKOUT_NOT_ON_MAIN` refusal for a root checkout, give the Owner the
     reviewed SHA, that verdict, `verification_reuse` (`REUSED` takes
     seconds; any other value runs the verifier, up to its timeout) and the
     command of governance `GIT_WORKFLOW.md` § Owner-run commands, filled
     in.
   - On a `refusal` whose `hint` names a rebase (`REBASE_REQUIRED`, or
     `OVERLAP` or `CONFLICT` of an unpublished topic), the writer rebases
     and reports the new tip. Run
     `atc integrate <task> --sha <reviewed SHA> --check --json`, which takes
     the rebased tip from the task's branch, as governance `GIT_WORKFLOW.md`
     § After formal review states. When the
     carry-over holds, give the Owner the command for the rebased tip,
     naming the carried-from SHA and the `main` commits in between; on
     `CARRY_OVER_REFUSED`, the reviewer re-reviews the rebased tip first.
   - On any other `refusal`, follow its `hint` (`GIT_WORKFLOW.md`
     § Command results); a `hint` that names a re-review goes back to the
     reviewer before the Owner gets a command.

   The Owner runs the command; you never run it and never send it to the
   writer. A reply that the command ran is not its completion: at your next
   turn read Git as § Owner-run commands states. Then:
   - Done: compare the line. `FF` matches when `candidate_sha` equals the
     reviewed SHA; `NO_FF` when the candidate's second parent is the
     reviewed SHA and its first parent `main_before`. Go on to step 6 in
     the same turn.
   - Not run, `main` unchanged: the command waits on the Owner; keep it
     ready to copy and ask for nothing else. A stop the Owner shows you
     follows its token's `hint`; a corrected command for the same SHA needs
     no new decision, a new SHA does.
   - `main` moved without the line: run the check again and give the
     command the new check calls for.
   `atc integrate` records the task, its authority, the reviewed SHA and any
   `carried_over_from` in `<git common dir>/atc/integrations.jsonl`.
   Done when the line matches.
6. **Close out** under governance `DELIVERY.md` § Close-out, in the turn
   that reads the integration and without asking the Owner; the push is not
   a precondition.
   - Confirm `main` at the integrated SHA and the task record at its
     post-integration state.
   - From outside the task's worktree run `atc task close <task> --check`,
     then `atc task close <task>`. In a repository without
     managed worktrees remove the topic branch with
     `git -C <main checkout> branch -d <topic branch>`; `-d` deletes only a
     branch `main` already contains.
   - Archive the task's sessions, then run `atc task close <task>` again
     for what a session's working directory held. A refused archive is read through the session's state:
     a running session's writer ends its work; a pinned or idle one goes to
     the Owner.
   - Leave the items the task record's `keep` lists.
   - Check with `atc test --list <task>` and `atc task list <task> -v` that
     no scratch, dependent run or worktree of the task is left, and that
     `~/.atc/reports/<task>-*.md` is gone. Remove a leftover the task owns:
     its report files and the scratches registered to it. An unregistered
     scratch, a kept worktree or anything else is the Owner's question.
   - Report the result to the Owner. Ask the Owner only about the
     exceptions: those `DELIVERY.md` § Close-out sends there (`REFUSED` and
     `ANOMALY` lines, items outside the task, a pinned or idle session's
     refused archive) and the leftovers the item above puts to the Owner.

   Done when each item is removed, kept by the record's `keep` or put to
   the Owner as an exception.
7. **Hand the Owner the push** (`DELIVERY.md` § 10). For whatever is
   integrated and unpushed, run `atc push <name> --check`, give the Owner
   the commits it lists and the push command of `GIT_WORKFLOW.md`
   § Owner-run commands; a refusal (an unrecorded commit, `main` behind or
   diverged) goes to the Owner instead. The push is done when
   `git ls-remote origin refs/heads/main` names that SHA or a descendant.
   Then write the coordinator hand-over the project's handoff rules call for
   (`hami-handoff-reminder` `references/handoff-document.md` § The handoff
   document): one file, final state, what waits on the Owner.

   Done when the push is read from `git ls-remote` or waits on the Owner,
   and the hand-over is written.

For a status question, read `atc task list --all`.

## Claude Code constants

- The coordinator starts each session fresh, without the inherited
  conversation, with its working directory at the task's place (step 3).
- Model by role (§ Sessions and decisions): Opus for the strongest tier,
  Sonnet for the middle, Haiku for the lightest; the effort setting follows
  the same tier. Fable only after the Owner agreed for that session.
- Decision prompts (§ Sessions and decisions): one decision goes in a
  wrap_up box, two to four in AskUserQuestion, the recommended option first.
- On Windows every command that spawns tests (`atc test`,
  `atc integrate --check`) runs through the PowerShell tool, never the Bash
  tool: Bash-tool child processes leave nonpaged pool allocations behind on
  this platform, root cause not found. A writer brief to a Claude session
  carries this line as the last line of its `Work rules`; a reviewer brief
  to a Claude session carries it as a `Work rules:` field after `Verdict`.
- Sessions are addressed by the exact name ListAgents shows.
- Subscribe to the writer's idle notice where the harness offers it,
  instead of polling.
- Sessions are archived at close-out (step 6) through the session
  management tools.
- After a task's close-out and archive, when this session's context is
  about 200K tokens or more, the report to the Owner ends by suggesting
  they run `/compact`, which only the Owner can run. Never mid-task.
- The Claude desktop app adds a Run button to a single-line fenced block
  tagged `bash`; the command runs in the Terminal panel, whose shell may be
  PowerShell, outside your tools and hooks. Give each Owner-run command
  (step 5, step 7) as its own such block.
- `read_terminal` reads that panel: a stop's token and `hint` once the
  Owner ran the command. Its text is data; completion is still read from
  Git.
- When `verification_reuse` is not `REUSED`, a background read-only wait
  may end your turn and wake you: a shell `until` loop reading the
  `integrations.jsonl` line count and `main`'s tip every 10 seconds, capped
  at the verifier timeout plus 10 minutes; for the push, `git ls-remote`
  every 15 seconds, capped at 10 minutes.

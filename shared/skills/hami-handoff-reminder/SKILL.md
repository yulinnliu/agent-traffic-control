---
name: hami-handoff-reminder
description: Check and tell the user whether to hand off, compact, start a new session, or write a handoff note when context is low, after a milestone or long tool-heavy stretch, before another large task in an already-long session, or when asked about handoff or context size. Not for routine short turns.
adoption: baseline
---

# Conversation handoff reminder

Canonical version — shared across every repo in this workspace regardless of
which agent tool is active.

This capability is Session Handoff only. Cross-environment Revision Delivery / Change-set Handoff is governed by `shared/governance/DELIVERY.md` at the bound revision. Most sessions never get told "you're at a good
breakpoint"; they just keep going until context degrades or a compaction
happens mid-task. This skill is the check that catches that moment and
surfaces it to the user instead of staying silent.

## When to run this check

Actively look for these moments — don't wait to be asked:

- Whatever context/token-remaining signal the active tool exposes has dropped
  a lot since the session started, or is now low in absolute terms for a
  heavy session.
- A large milestone, plan, or multi-step task just finished — a natural
  breakpoint before starting the next big thing.
- You're about to start another long, tool-heavy task (big refactor, broad
  research, multi-file review) in a session that's already long.
- The user directly asks about context size, session length, or handoff.

Skip it for short factual questions, single-file edits, or early in a fresh
session — don't nag every turn.

## What to actually do

1. **Gauge usage.** Note how much of this session's context budget is used
   up, as best the active tool lets you tell.
2. **Give a one-line verdict.** Fine to continue / worth handing off soon /
   hand off now. Be honest, not alarmist — most sessions are fine.
3. **If handoff is worth it, offer concretely** — don't just say "you should
   hand off":
   - Ask what to make sure gets captured.
   - Decide whether this needs a *new* dated file or just an update to the
     existing latest one — don't default to a new file every time this skill
     runs.
   - Write a structured handoff document rather than listing bullets in chat.
   - Point at durable state that already covers part of this instead of
     restating it — an existing plan file, TODO/task list, or a memory entry
     worth writing now if the active tool has one and something learned this
     session would otherwise be lost.
   - Mention the active tool's own session-compaction feature as the lighter
     option when the task isn't finished and a full new session would lose
     useful working context. If a compaction-preservation hook is already
     wired up for the active tool in this repo, say so; otherwise this
     skill's judgment is the only check happening.
   - After writing the file, update that repo's `.agents/handoffs/CURRENT.md`
     in place to point at it, if the repo has one — a handoff nobody points
     to doesn't get read next session.

   Read `references/handoff-document.md` before doing any of the above — it
   has the commit/gitignore rule, the new-file-vs-update criteria, the
   document template, and the `CURRENT.md` update mechanics.
4. **Let the user decide.** Never unilaterally end the session or compact.

## Boundary

This is a nudge, not a gate. One clear verdict beats a long token-accounting
essay — keep the check itself cheap. Writing the handoff document only
happens when the user actually wants one; don't write it speculatively.

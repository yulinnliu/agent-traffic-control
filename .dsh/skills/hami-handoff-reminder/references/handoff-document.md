# Writing the handoff document

Read this only once the handoff-reminder skill has decided a handoff is
actually worth writing (skill step 3) — not needed for a quick verdict.

## `.agents/handoffs/` is local-only, not committed

Check whether the repo's `.gitignore` already covers `.agents/handoffs/`. If
so, these files are deliberately kept
out of git — write and update them freely, but never `git add` them or fold
them into a commit; they exist purely so tools sharing this same checkout
(Claude, Codex, whichever picks up the session next) can read them off disk.
This means no cross-machine sync — a fresh clone elsewhere won't have them —
which is an accepted tradeoff, not a bug to fix. `AGENTS.md`/`CLAUDE.md`
should say so in its memory-routing section if it doesn't already.

If a repo's `.gitignore` does *not* yet cover `.agents/handoffs/`, don't
silently add the entry as a side effect of an unrelated handoff — that's a
deliberate repo-level decision the user should confirm, same as it was here.
Until they do, treat commits touching only handoff files the same as before:
minimize how often a new dated file (and thus a new commit) gets created.

## When a new dated file is warranted

Even where handoffs are committed, a new dated file has its own history-noise
cost (a `docs(handoff)` commit and a file that lingers forever, per
[[feedback-docs-describe-final-state-not-process]] — process narrative
belongs in git log, not stacked on top of it in docs too). Don't create one
by default just because this skill fired. Create a **new** dated file only
when at least one holds:

- The prior latest handoff already says "no open work" (a closed session)
  **and** this session's task is a genuinely different topic/task line, not a
  continuation of it.
- This session is handing off mid-task to a different tool/session that will
  need full context to resume (an open task, unresolved question, or
  in-progress code change).
- The prior latest handoff has grown unwieldy or covers a topic that's fully
  wrapped up and archived (see the existing "archive to `docs/history/`"
  pattern) — i.e. you're deliberately starting a fresh page.

Otherwise — same-day continuation of the same topic, or a small closeout with
no open work (a quick cleanup, a doc fix, a session that confirmed something
and stopped) — update the **existing** latest dated file in place (append a
"## Follow-up [date]" section, or fold it into "What We Did") rather than
creating a new one. If there's truly nothing worth a future session
re-reading, skip writing anything and say so.

## The handoff document

Write it to `.agents/handoffs/[YYYY-MM-DD]-[brief-description].md` in the
current project (confirm the location if it's ambiguous) — `.agents/`
because either tool may resume from it.

```markdown
# Session Handoff: [Brief Description]

**Date:** [YYYY-MM-DD]
**Project:** [project name/path]

## Current State
**Task:** ... **Phase:** [exploration/planning/implementation/debugging/review] **Progress:** ...

## Git Anchors
**Repo / branch:** ... **Base SHA:** ... **HEAD SHA:** ...
**Published:** [fetchable ref, or "local only"] **Working tree:** [clean / dirty]
**Verification:** [checks passed at which SHA; what is unverified]

## What We Did
[2-3 sentence summary]

## Decisions Made
- **[Decision]** — [Rationale]

## Code Changes
- `path/to/file.ts` — [what and why]

## Open Questions
- [ ] [Question needing resolution]

## Context to Remember
[Constraints, preferences, domain knowledge that took time to establish]

## Next Steps
**Authority:** [who may write; what is authorized, e.g. commit / push / integrate]
1. [ ] [First thing to do next session]

## Files to Review on Resume
- `path/to/key/file.ts` — [why it matters]
```

Keep it scannable: bullet points, file paths with line numbers where useful,
checkboxes for open questions and next steps. Skip verbose tool output,
dead-end exploration, and anything obvious from the code — capture the
reasoning and state that would otherwise take time to re-establish.

### Updating `CURRENT.md`

If the repo has a `.agents/handoffs/CURRENT.md` fixed entry point, update it
in place as the last step of writing a handoff — never rename it or replace
it with a symlink:

- Point its "Latest" line at the new dated file.
- `CURRENT.md` tracks the *most recently written* handoff, which is not
  necessarily the one covering the topic someone needs next. If an older
  dated handoff still holds the live status for a different, still-open
  topic (e.g. this session's handoff is about tooling, but another handoff
  covers an in-progress feature slice), say so explicitly in `CURRENT.md`
  and link that file directly — don't let it get silently shadowed.
- If the repo has no `CURRENT.md` yet, don't invent the convention as part
  of an unrelated handoff — writing the dated file is enough.

On resume, treat the handoff as narrative and Git as the source of truth:
recheck HEAD, the branch's ref tip and the working tree against the Git Anchors
before acting, and stop if they differ.

To resume, the user shares or references `CURRENT.md` (or the dated file
directly) at the start of the next session, regardless of which tool picks
it up — on the same machine/checkout when `.agents/handoffs/` is gitignored,
or from any clone when it's committed.

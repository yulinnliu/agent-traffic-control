---
name: hami-review-relay
description: Writer/reviewer work relayed by the user between two sessions that cannot see each other - writing a step report for review, reviewing a relayed commit, writing the verdict block, checking an integration. Not for reviewing your own uncommitted work.
---

# Review relay

Two sessions share one task and never see each other: the **writer** commits
on its topic branch, the **reviewer** reviews and never edits that branch,
and the user carries every message between them by pasting. "Writer" is a
permission boundary, not a component name. Everything you send must work as a
**cold read**: the receiving session has only what the pasted block says.

Project specifics (integration command, required gates, frozen snapshot
files, build steps) live in that project's `AGENTS.md`, its runbooks and its
current session handoff; read them before the first report or review.

## Every block you write

- Self-contained: restate every item in full, never "as discussed" or "see above".
  This covers context the receiver lacks, not what it can read: a reviewer reads
  the code at the named SHA, so a report gives results, choices to rule on and
  deviations, and does not narrate the implementation.
- Inline in the reply as one code block the user pastes whole. Use a file only
  for a prompt that is long and revised repeatedly, or a lasting template.
- Commands the user runs are pasted as-is, so they carry zero placeholders:
  capture a value in a variable on an earlier line, show the fields the user
  must check, then reference the variable.
- Cite a checklist or spec row by its section and the start of its text; line
  numbers are hints only, because earlier batches shift them.

## Writer: the step report

End every completed step with a review request carrying:

- worktree path, branch, tip SHA and its base SHA;
- spec/plan sections in scope and the contract sections the step rewrote;
- changed-file map;
- targeted test commands with their exact summary lines;
- mutants run, one per guard, launchers and scripts included, and every survivor;
- deviations, open points, known failures you do not own, deferred items;
- the specific questions for the reviewer, including the real path to walk.

After a PASS the reviewed SHA is frozen; fixes go in new commits.

## Reviewer: the review

Work through every step; each ends on the stated criterion.

1. **Pin the revision.** Review exactly the tip SHA the request names, against
   its stated base. Done when the branch tip equals that SHA.
2. **Build a scratch copy.** Export that exact tree to a scratch directory
   (`git archive <sha>`), point imports at the copy, isolate data roots and
   strip ambient credentials; tests and mutants never run in a live worktree.
   An exported tree can differ only in line endings, so compare files with
   carriage returns ignored. Done when an import-location guard proves the
   copy, not an installed checkout, is what runs.
3. **Walk the real path.** Trace from the user-facing entry to the external
   effect (the order request, the published event, the written record) and
   list every gate and required input on the way. Done when each one is shown
   delivered by the real product assembly, not only by test fakes. Wording
   such as "pending wiring" or "awaiting acceptance" in a delivery is a
   blocker to tell the user now.
4. **Run tests and mutants.** Scale effort with risk: docs only, no tests;
   tests only, that file plus mutants; behaviour, the targeted set plus every
   project gate the change touches. One single-string mutant per guard, one
   at a time, restored after each run. Done when every file in the copy is
   proven equal to its Git object again and every surviving mutant is listed
   as a test gap or argued equivalent. `shared/skills/hami-review-relay/references/mutants.py`
   and `run_tests.sh` at the workspace root run steps 2 and 4 from a mutants list.
5. **Search by behaviour.** Look for a guard by its refusal token or the
   behaviour it produces; a single keyword search misses guards. Also search
   the repo for the same mechanism already solved elsewhere (a sibling script,
   an existing helper, a recent entry under `.agents/handoffs`), and treat a
   second mechanism for one problem as a finding.
6. **Check real state read-only.** Host evidence (scheduled tasks, stores,
   logs) is read without writing; credentials are never read.
7. **Verify delegated verdicts.** A fresh-context reviewer's verdict is
   evidence to check: spot-check its citations before relaying any of it.

## Reviewer: the verdict block

- First line: `<step> review: PASS.`, conditional PASS or FAIL.
- A "verified" paragraph: test counts, mutant results, read-only host evidence.
- A PASS includes the integration instruction naming the exact reviewed SHA
  and what not to do (build, install, push) when the project's adopted
  process lets a relayed PASS authorize integration; the user pasting it is
  that approval. Worktree audit/cleanup commands in a block follow the
  hami-worktree skill's command form; load it before writing them. A
  conditional PASS or FAIL carries no integration
  instruction. Anything needing the user's decision (version numbers, spec
  semantics, risk limits, remote deletes) stops for the user.
- Then the next step's scope and the conditions this step carries into it,
  ending with "report in the same format".

## Reviewer: after integration

The writer reports the new `main` SHA and its parents. Done when the first
parent is the previous `main`, the second parent is the reviewed SHA, and the
merge tree equals the reviewed tree; on any mismatch, hand it to the user and
leave `main` as it is.

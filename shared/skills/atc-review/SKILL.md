---
name: atc-review
description: Writer/reviewer protocol for work split across sessions that cannot see each other, whatever carries the messages (the user's paste, a session message, a notes file) - the step report, the review on a scratch copy, the verdict block, the integration check. Not for reviewing your own uncommitted work, nor a diff inside the session that wrote it.
adoption: baseline
---

# Review

Two sessions share one task and never see each other: the **writer** commits
on its topic branch, the **reviewer** reviews and never edits that branch,
and the **holder** carries every message between them. The holder is the
user, or a coordinator session following the `atc-coordinator` skill
installed beside this one. A message travels by whatever carrier the setting
provides: the user's paste, a message from one session to another, or a file
in a notes folder. "Writer" is a permission boundary, not a component name.
Everything you send is a **cold read**: the receiving session has this skill
and the message, nothing else.

The **Owner** is the person who holds the task's authority. Only the Owner
approves an integration, by running its command.

Integration runs through `atc integrate` in every project. Project specifics
(the checkout a session works in, stricter integration gates, required
gates, frozen snapshot files, build steps) live in that project's
`AGENTS.md`, its runbooks and its current session handoff; read them before
the first report or review. The coordinator's briefs to both sessions follow
the `atc-coordinator` skill's `references/briefs.md`.

## Every block you write

- Carry what varies, in full: results, SHAs, filled commands, choices to rule
  on, deviations. Never "as discussed" or "see above". Rules this skill states
  are already known to both sessions and stay out of the block, and so does
  what the receiver can read: a reviewer reads the code at the named SHA, so a
  report gives results rather than narrating the implementation.
- On session messaging, and for a reviewer running as a subagent, a report
  or verdict is saved as a file under `~/.atc/reports/`, and the message's
  or returned verdict's first line is its conclusion plus that path. The
  session that integrates the task deletes its report files at close-out
  (governance `DELIVERY.md` § Close-out). A notification that truncates (a
  short note, a preview) is a pointer, never the report.
- On the paste carrier, the report or verdict goes inline, in full, in the
  one code block the user pastes whole; the file and path are for session
  messaging and subagent reviewers only. A prompt that is long and revised repeatedly, or a
  lasting template, goes in a file.
- A block the user pastes must not contain nested code fences: internal
  triple backticks prematurely close the outer block. Put commands, scripts
  and quoted output on plain text lines or use indentation instead.
- Commands the user runs are pasted as-is, so they carry zero placeholders:
  capture a value in a variable on an earlier line, show the fields the user
  must check, then reference the variable.
- Cite a checklist or spec row by its section and the start of its text; line
  numbers are hints only, because earlier batches shift them.
- Name the task in the form the project's `AGENTS.md` declares (governance
  `CORE.md` § Task identity); with no declaration, repository, branch and SHA
  identify it. A task ID from another project's numbering is dropped and
  pointed out.

## Writer: the design step

A task that needs a design first starts with it: the writer's first step
produces the design, and the implementation follows as later steps of the
same task and record.

- The design document and the release notes are committed to the repository,
  at the place the project's `AGENTS.md` or docs layout gives; a report alone
  does not hold them, since close-out deletes `~/.atc/reports`.
- The Owner's decisions on the design go into the task record's `decisions`.
- The design step adds no status and no role: the same writer and reviewer
  carry it, with the step report and the review below.

## Writer: the step report

End every completed step with a step report in the shape governance
`DELIVERY.md` § 8.1 gives, sent to the coordinator only:

1. Run the tests the task's test level names (governance `VERIFICATION.md`
   § Testing, Test levels): `atc test <task>` for the related tests and,
   under `strict`, `atc test <task> --mutants <file>`, one mutant per guard,
   launchers and scripts included; the report gives each command with its
   summary line and every surviving mutant. Under `none` no test runs.
2. Run `atc integrate <task> --check` on the tip (governance
   `GIT_WORKFLOW.md` § Mainline check before final self-verification) and
   report the verdict it gives. On `REBASE_REQUIRED` or
   `OVERLAP`, rebase onto `main`, rerun the selection and report only the
   new tip.
3. Stop every background job (governance `DELIVERY.md` § Background work).
4. Deliver the report to the coordinator as "Every block you write" says;
   on session messaging its file is `~/.atc/reports/<task>-<sha>.md`.
5. Your session's final message to the Owner is that one line and nothing
   else: no summary before or after it. Write it plainly in the Owner's
   language with no tokens, the role terms writer, reviewer, coordinator and
   Owner kept in English, for example
   「報告已送 coordinator（tip <sha>），等審查與你核准；尚未整合、未 push。」.
   When there is a problem the line names it plainly instead (for example,
   a rebase is needed). Every detail and token stays in the step report and
   the coordinator message.

When taking over a topic from another session, fetch first and commit
only on the fetched tip; never continue from a stale base. For a published
topic, confirm that the remote branch tip equals the tip you continue from
before the first commit.

After a PASS the reviewed SHA is frozen; fixes go in new commits. After a
FAIL verdict the writer may amend or squash the fixes into the reviewed
commits instead (governance `GIT_WORKFLOW.md` § After formal review) and
reports the new tip.

Where the project runs its tests through `atc-testkit`, run targeted tests
against the worktree with `atc test <task> [-- <pytest args...>]`; elsewhere
the targeted test commands are the ones the project's `AGENTS.md` names.
Every run registers its scratch to the task (`<tmp>/<task>`, or
`<tmp>/<task>-<sha7>` under `--sha`), so `atc task close` removes it; a
directory under `<tmp>` named for the task but not registered to it is
listed for the Owner's consent and never deleted. `<tmp>` is `~/.atc/tmp`,
or `ATC_TMP` when that is set. The run removes every
`HAMI_*` variable so the host's project settings stay out of it, points each
data-root variable the tree's `[tool.atc-testkit]` names, or
`QUANT_DATA_ROOT` when it names none, at `<scratch>/qdr`, makes the scratch
the temporary directory, and proves with its import-location guard that the
imports come from the tree (`COPY_IMPORT_MISMATCH`, exit 8).

A tree that runs source child processes needs a `.venv`:

- Prepare it as the project's `AGENTS.md` says; never with Git Bash `ln -s`,
  which copies the whole venv here.
- `atc test <task> --clean` takes every `.venv` beside a `pyproject.toml`
  in the task's scratches out as a link.
- A worktree's `.venv` junction comes out as the link alone, with
  `cmd //c rmdir` on its literal path, before the worktree's own cleanup.

## Reviewer: the review

Classify the diff first; the class decides the steps, and each step ends on
its stated criterion:

- documentation only: steps 1 and 5, read on the diff. No scratch copy, no
  tests.

The task record's `tests` level bounds the class: a diff that needs more
than its level allows is a FAIL finding (governance `VERIFICATION.md`
§ Testing, Test levels).
- tests only: steps 1, 2 and 4.
- behaviour: every step; step 3 when the change lies on a path to an
  external effect, step 6 when it reads or writes host state.

Read the diff at the reviewed SHA (`git diff <base>..<tip>`), and a whole
file only for what the diff cannot settle. Send test and mutant output to a
file in the scratch directory and read its summary lines; run all mutants in
one `atc test <task> --mutants` call. Tests and mutants run in the foreground
(governance `DELIVERY.md` § Background work).

Whether you review fresh or as a continued re-review is governance
`VERIFICATION.md` § Independent review; a continued re-review reads the fix
diff against your last round's findings, after a FAIL amend the
`git range-diff` of the old and the new tip, and runs
`atc test <task> --candidate` on the new tip.

1. **Pin the revision.** First confirm the previous topic's closure read-only:
   the `main` position and `atc task list -v`. Then review exactly the tip
   SHA the request names, against its stated base. If a check shows its
   premise is stale (for example, `main` moved, a referenced file or section
   was renamed, or a count changed), re-check against current state and
   continue; do not stop to ask the holder to verify a premise you can check
   yourself. Done when the branch tip equals that SHA.
2. **Build a scratch copy.** `atc test <task> --sha <sha>` exports that
   exact tree to `<tmp>/<task>-<sha7>/tree`, builds its environment and runs
   the related tests there, which points imports at the copy and isolates
   data roots; strip ambient credentials from the shell that runs it. Tests and
   mutants never run in a live worktree. Prepare and delete a `.venv` in the
   copy as "Writer: the step report" says. An exported tree can differ only
   in line endings, so compare files with carriage returns ignored. Done when
   an import-location guard proves the copy, not an installed checkout, is
   what runs.
3. **Walk the real path.** Trace from the user-facing entry to the external
   effect (the order request, the published event, the written record) and
   list every gate and required input on the way. Done when each one is shown
   delivered by the real product assembly, not only by test fakes. Wording
   such as "pending wiring" or "awaiting acceptance" in a delivery is a
   blocker to tell the holder now.
4. **Run tests and mutants.** Run the tests governance `VERIFICATION.md`
   § Independent review assigns a reviewer, and name them in the verified
   line. Run a project gate in the command form the project's `AGENTS.md`
   gives, from the scratch copy's root; never with `python -I`, which
   drops the script's directory from the import path. Before a PASS on a
   `standard` or `strict` task, run `atc test <task> --candidate --sha <sha>`:
   it runs the integration verifier on the candidate `atc integrate` would
   build, without a slot, a ref update or the Owner's run, and records a PASS
   that `atc integrate` reuses in place of rerunning the verifier. Run it while no integration runs. Quote its last line
   in the verified line; a refusal is a blocker finding, except
   `WORKER_WAIT_TIMEOUT`. A `WAIT host workers` line means the run is queued
   for the host's test workers, not stuck; the queue is outside the timeout,
   a wait past its limit stops with `WORKER_WAIT_TIMEOUT` naming the runs
   ahead, and `atc doctor -v` shows the pool. Rerun it once they finish.
   One single-string mutant per guard, one at a time, restored after
   each run.
   Done when every file in the copy is proven equal to its Git object again
   and every surviving mutant is listed as a test gap or argued equivalent.
   Under `strict`, where the project runs its tests through `atc-testkit`,
   `atc test <task> --mutants <mutants.json> --sha <sha> [-- <tests>...]`
   performs this step on the export of `<sha>` with its own environment;
   elsewhere the
   project's `AGENTS.md` names how a mutant run is done. Its input file and
   result lines are in [`references/mutants.md`](references/mutants.md).
5. **Search by behaviour.** Look for a guard by its refusal token or the
   behaviour it produces; a single keyword search misses guards. Also search
   the repo for the same mechanism already solved elsewhere (a sibling script,
   an existing helper, a recent entry under `.agents/handoffs`), and treat a
   second mechanism for one problem as a finding.
6. **Check real state read-only.** Host evidence (scheduled tasks, stores,
   logs) is read without writing; credentials are never read.
7. **Review inline.** Delegate to a subagent only for a search the diff does
   not bound, one at most; its verdict is evidence to check, so spot-check
   its citations before relaying any of it.

## Reviewer: the verdict block

The verdict takes the shape governance `VERIFICATION.md` § Independent review
gives. Deliver it as "Every block you write" says; on session messaging or
as a subagent, you write its file
`~/.atc/reports/<task>-<sha>-review.md` yourself, with the full
40-character SHA. The file's first line is the verdict token, with no title
above it: `atc task close` reads the ledger's verdict from that line alone,
and `atc integrate <task>` takes the reviewed SHA from the newest PASS file.

- The verified line: test counts, mutant results, the last line of
  `atc test <task> --candidate`,
  read-only host evidence.
- On a PASS, when the project's adopted process lets a PASS carry an
  integration instruction, the integration instruction. The Owner runs it
  on the exact reviewed SHA, which approves the integration; the holder, a
  coordinator or, in a paste-only setup with no coordinator session, the user
  who counts as one, gives it to the Owner and never to the writer
  (governance `GIT_WORKFLOW.md` § Owner-run commands).
  Before writing it, run `atc integrate <task> --sha <SHA> --check --json`
  (governance `GIT_WORKFLOW.md` § Integrate). The PASS carries the instruction only when the result has no
  `refusal` and its `preflight.verdict` is `FF` or `NO_FF`; quote that verdict
  with `preflight.reason` and every entry of `resolved.warnings` in the
  verified line. A `refusal`, such as a missing `## Integration verification`
  section, leaves the PASS without an instruction: give the holder its
  `reason`, `detail` and `hint`. A `refusal` whose `hint` names a rebase (`REBASE_REQUIRED`, or `OVERLAP` or
  `CONFLICT` of an unpublished topic) still carries the instruction, with that
  refusal quoted. In a repository without managed worktrees, a
  `CHECKOUT_NOT_ON_MAIN` refusal naming the topic branch is the expected check
  result: the PASS still carries the instruction, the verified line quotes
  that refusal and every entry of `resolved.warnings`, and the writer's run
  after `switch main` assesses a dirty checkout and `FF` or `NO_FF`.

  When `main` has moved, the topic follows governance `GIT_WORKFLOW.md`
  § Integrate and § After formal review, and the Owner gets the command again
  for the SHA being integrated: the rebased tip, or for `NO_FF` the reviewed
  SHA.

  The instruction is the verdict's last line, every value filled in:
  `atc integrate <task> --sha <SHA>`, with the task record ID, which names the
  repository, and the full 40-character reviewed SHA (governance
  `GIT_WORKFLOW.md` § Integrate, § Task and authority). The command names no
  branch, verifier or timeout: the tool takes the branch from the record's
  `branch` and reads the verifier from the `## Integration verification`
  section of `AGENTS.md` at `main`. In a repository without managed worktrees, where
  the topic branch lives in the main checkout,
  `git -C <main checkout> switch main` comes before it.

  Cleanup never rides on a PASS: it follows governance `DELIVERY.md`
  § Close-out. A conditional PASS or FAIL carries no integration
  instruction.
- Anything needing the Owner's decision (version numbers, spec semantics,
  risk limits, remote deletes) stops for the Owner.

## Writer: after a PASS

A PASS ends the writer's work on the reviewed SHA. The Owner runs the
integration and the push, and the holder closes the task out (governance
`GIT_WORKFLOW.md` § Owner-run commands, `DELIVERY.md` § Close-out); the
writer runs neither and is sent neither.

- When the holder reports that `main` moved and the check's `hint` names a
  rebase, rebase the topic branch onto `main` and give the holder the
  rebased tip; `CARRY_OVER_REFUSED` sends that tip to the reviewer for a
  fresh review.
- When the verdict or the holder sends work back, the fix goes in new
  commits, or after a FAIL verdict as "Writer: the step report" allows, and
  in a new step report.

Done when the holder has the rebased tip or the new step report, or nothing
is sent back.

## Reviewer: after integration

The coordinator removes the task's scratches and report files at close-out (governance `DELIVERY.md` § Close-out).

A failing verifier or gate run leaves its working directory under
`<tmp>/systmp` for inspection; it stays until a later `atc test` prunes it,
seven days after its last change.

---
name: atc-review
description: Writer/reviewer protocol for work split across sessions that cannot see each other, whatever carries the messages (the user's paste, a session message, a notes file) - the step report, the review on a scratch copy, the verdict block, the integration check. Not for reviewing your own uncommitted work, nor a diff inside the session that wrote it.
managed-by: agent-traffic-control
canonical: atc-review
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
gives the word that approves an integration.

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
- On session messaging, a report or verdict is saved as a file under
  `~/.atc/reports/`, and the message's first line is its conclusion plus
  that path. The session that integrates the task deletes its report files
  at close-out (governance `DELIVERY.md` § Close-out). A notification that truncates (a
  short note, a preview) is a pointer, never the report.
- On the paste carrier, the report or verdict goes inline, in full, in the
  one code block the user pastes whole; the file and path are for session
  messaging only. A prompt that is long and revised repeatedly, or a
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

## Writer: the step report

End every completed step with a step report in the shape governance
`DELIVERY.md` § 8.1 gives, sent to the coordinator only:

1. Run the selection (governance `VERIFICATION.md` § Testing) and the
   mutants, one per guard, launchers and scripts included; the report gives
   each command with its summary line and every surviving mutant.
2. Run `atc integrate --check` on the tip (governance `GIT_WORKFLOW.md`
   § Mainline check before final self-verification) and report the verdict
   it gives; `authority=missing` or `authority=not_checked` on its resolved
   line is pending authority (§ Integrate). On `REBASE_REQUIRED` or
   `OVERLAP`, rebase onto `main`, rerun the selection and report only the
   new tip.
3. Stop every background job (governance `DELIVERY.md` § Background work).
4. Deliver the report to the coordinator as "Every block you write" says;
   on session messaging its file is `~/.atc/reports/<task>-<short sha>.md`.
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

After a PASS the reviewed SHA is frozen; fixes go in new commits.

Where the project runs its tests through `atc-testkit`, run targeted tests
against the worktree with
`atc test run <worktree> <scratch> <python> <label> <pytest args...>`;
elsewhere the targeted test commands are the ones the project's `AGENTS.md`
names. `<scratch>` is a name; the run creates it as `<tmp>/<scratch>`, where
`<tmp>` is `~/.atc/tmp`, or `ATC_TMP` when that is set. The run removes every
`HAMI_*` variable so the host's project settings stay out of it, points each
data-root variable the tree's `[tool.atc-testkit]` names, or
`QUANT_DATA_ROOT` when it names none, at `<scratch>/qdr`, makes the scratch
the temporary directory, and proves with its import-location guard that the
imports come from the tree (`COPY_IMPORT_MISMATCH`, exit 8).

A tree that runs source child processes needs a `.venv`:

- Prepare it as the project's `AGENTS.md` says; never with Git Bash `ln -s`,
  which copies the whole venv here.
- `atc tmp clean` takes every `.venv` beside a `pyproject.toml` in the
  scratch out as a link.
- A worktree's `.venv` junction comes out as the link alone, with
  `cmd //c rmdir` on its literal path, before the worktree's own cleanup.

## Reviewer: the review

Classify the diff first; the class decides the steps, and each step ends on
its stated criterion:

- documentation only: steps 1 and 5, read on the diff. No scratch copy, no
  tests.
- tests only: steps 1, 2 and 4.
- behaviour: every step; step 3 when the change lies on a path to an
  external effect, step 6 when it reads or writes host state.

Read the diff at the reviewed SHA (`git diff <base>..<tip>`), and a whole
file only for what the diff cannot settle. Send test and mutant output to a
file in the scratch directory and read its summary lines; run all mutants in
one `atc test mutants` call. Tests and mutants run in the foreground
(governance `DELIVERY.md` § Background work).

Whether you review fresh or as a continued re-review is governance
`VERIFICATION.md` § Independent review; a continued re-review reads the fix
diff against your last round's findings.

1. **Pin the revision.** First confirm the previous topic's closure read-only:
   the `main` position and `atc worktree audit`. Then review exactly the tip
   SHA the request names, against its stated base. If a check shows its
   premise is stale (for example, `main` moved, a referenced file or section
   was renamed, or a count changed), re-check against current state and
   continue; do not stop to ask the holder to verify a premise you can check
   yourself. Done when the branch tip equals that SHA.
2. **Build a scratch copy.** Export that exact tree to `<tmp>/<scratch>/tree`
   (`git archive <sha>`) and run it through `atc test run` as "Writer: the
   step report" says, which points imports at the copy and isolates data
   roots; strip ambient credentials from the shell that runs it. Tests and
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
   line. One single-string mutant per guard, one at a time, restored after
   each run.
   Done when every file in the copy is proven equal to its Git object again
   and every surviving mutant is listed as a test gap or argued equivalent.
   Where the project runs its tests through `atc-testkit`,
   `atc test mutants <scratch> <sha> <worktree> <mutants.json> --python <py> --tests <path>...`
   performs this step on the copy at `<tmp>/<scratch>/tree`; elsewhere the
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
gives. Deliver it as "Every block you write" says; on session messaging its
file is `~/.atc/reports/<task>-<short sha>-review.md`.

- The verified line: test counts, mutant results, read-only host evidence.
- On a PASS, when the project's adopted process lets a PASS carry an
  integration instruction, the integration instruction. The Owner's word on
  the exact reviewed SHA approves integration. The holder of that word, a
  coordinator or, in a paste-only setup with no coordinator session, the user
  who counts as one, runs the instruction or forwards it to the writer as
  governance `GIT_WORKFLOW.md` § Task and authority splits the forms by flag.
  Before writing it, run `atc integrate --check --json` (governance
  `GIT_WORKFLOW.md` § Integrate) with the same arguments as the integrate
  command below. The PASS carries the instruction only when the result has no
  `refusal` and its `preflight.verdict` is `FF` or `NO_FF`; quote that verdict
  with `preflight.reason` and every entry of `resolved.warnings` in the
  verified line. A `refusal`, such as a missing `## Integration verification`
  section, leaves the PASS without an instruction: give the holder its
  `reason`, `detail` and `next`. An authority of kind `missing` or
  `not_checked` is pending authority (§ Integrate): the PASS still carries
  the instruction. A
  `refusal` whose `next` names a rebase (`REBASE_REQUIRED`, or `OVERLAP` or
  `CONFLICT` of an unpublished topic) still carries the instruction, with that
  refusal quoted. In a repository without managed worktrees, a
  `CHECKOUT_NOT_ON_MAIN` refusal naming the topic branch is the expected check
  result: the PASS still carries the instruction, the verified line quotes
  that refusal and every entry of `resolved.warnings`, and the writer's run
  after `switch main` assesses a dirty checkout and `FF` or `NO_FF`.

  When `main` has moved, the topic follows governance `GIT_WORKFLOW.md`
  § Integrate and § After formal review, and the word is asked for again on
  the SHA being integrated: the rebased tip, or for `NO_FF` the reviewed
  SHA.

  The instruction is the verdict's last line, every value filled in:
  `atc integrate --task <task> --reviewed <SHA>`, with the task record ID,
  which names the repository, and the full 40-character reviewed SHA
  (governance `GIT_WORKFLOW.md` § Integrate). A task identity string in place
  of a record ID adds `--project <name>`, or `--repo <root checkout>` for the
  workspace root. When the record on `main` does not grant the integration,
  the holder adds `--owner-words` with the Owner's words quoted exactly and
  relays the filled instruction to the writer with the reviewed full SHA
  (governance `GIT_WORKFLOW.md` § Task and authority). The command names no branch, verifier or timeout: the tool
  finds the branch from the reviewed SHA and reads the verifier from the
  `## Integration verification` section of `AGENTS.md` at `main`. In a
  repository without managed worktrees, where the topic branch lives in the
  main checkout, `git -C <main checkout> switch main` comes before it.

  Cleanup never rides on a PASS: it follows governance `DELIVERY.md`
  § Close-out. A conditional PASS or FAIL carries no integration
  instruction.
- Anything needing the Owner's decision (version numbers, spec semantics,
  risk limits, remote deletes) stops for the Owner.

## Writer: after integration

A PASS authorizes only the commands it carries; build, install, push and any
other action need their own instruction. Who may run which form of
`atc integrate` is governance `GIT_WORKFLOW.md` § Task and authority.

1. Run the `atc integrate` command the holder relayed, with `--json` added
   (after `switch main`, when the block carries it), against the
   integration checkout by absolute path. Run `--owner-words` only with the
   Owner's exact words and the reviewed full SHA the holder relayed, for
   that SHA. When your session refuses to run it on relayed words, stop and
   report `BLOCKED` to the holder; the Owner then approves in your session.
   Check the result. `FF`
   matches when `main_after` equals the reviewed SHA; `NO_FF` matches when
   `second_parent` equals the reviewed SHA and `first_parent` equals
   `main_before`.
   - When it stops with a `next` that names a rebase, rebase the topic
     branch onto `main` and give the holder the rebased tip, so the word is
     asked for on it; the same command with `--rebased <rebased tip>` added
     is then run as step 1 says. `FF` then matches when `main_after`
     equals the rebased tip and `carried_over.carried_over_from` is the
     reviewed SHA. `CARRY_OVER_REFUSED` sends the rebased tip to the
     reviewer for a fresh review.
   - `push.commits` lists every commit a push of `main` would carry; give
     the holder each entry whose `push_approval` is `false`, with its `sha`,
     `task` and `authority`, with any push request.
     `atc preflight push --repo <main checkout> --json` gives the same list
     before a later push.
2. Close out (governance `DELIVERY.md` § Close-out): remove the task's
   scratches with `atc tmp clean`, its report files
   `~/.atc/reports/<task>-*.md` (the writer's and the reviewer's), and its
   worktree with `atc worktree cleanup` under the `atc-worktree` skill,
   unless your session's working directory is inside it; report that
   worktree as skipped.
3. Run the hami-handoff-reminder check and report the integration result,
   every close-out line and the check's result lines to the holder only; the
   reviewer gets no relay.

Stop on a `switch` failure or a result mismatch: leave `main` and the branch
as they are and hand the result lines to the holder for the reviewer.

Done when the result matches, close-out ran and the handoff check ran, or
the stop is with the holder.

## Reviewer: after integration

The session that integrates the task removes its scratches and report files
at close-out (governance `DELIVERY.md` § Close-out).

A failing verifier or gate run leaves its working directory under
`<tmp>/systmp` for inspection; it stays until `atc tmp prune` deletes it,
seven days after its last change.

## Claude Code constants

- A session that starts another session for a role starts it fresh, without
  the inherited conversation, so the cold read holds, in the checkout the
  repository's `AGENTS.md` prescribes: its own worktree for a registered
  project, and for the workspace root whatever the root's `AGENTS.md`
  § Changing this repo states.
- Model: Opus 5.5 or Sonnet 5.5, chosen per task (Sonnet for mechanical or
  well-briefed work, Opus for design-heavy writing or a review of a
  behaviour change); Fable only after the Owner agreed for that session.
- The step report or verdict goes by SendMessage as "Every block you write"
  says, to the receiving session addressed by the exact name ListAgents
  shows. The harness's idle note carries only the first few hundred
  characters; it is a pointer, never the report.
- A large hand-over (a brief revised repeatedly, a template) may go as a file
  in the side-session notes folder, the message naming its absolute path.
- Sessions are archived and scratches removed at close-out (governance
  `DELIVERY.md` § Close-out).

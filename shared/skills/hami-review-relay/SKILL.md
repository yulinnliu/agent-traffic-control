---
name: hami-review-relay
description: Writer/reviewer work relayed by the user between two sessions that cannot see each other - writing a step report for review, reviewing a relayed commit, writing the verdict block, checking an integration. Not for reviewing your own uncommitted work.
---

# Review relay

Two sessions share one task and never see each other: the **writer** commits
on its topic branch, the **reviewer** reviews and never edits that branch, and
the user carries every message between them by pasting. "Writer" is a
permission boundary, not a component name. Everything you send is a **cold
read**: the receiving session has this skill and the pasted block, nothing
else.

Integration runs through `atc integrate` in every project. Project specifics
(stricter integration gates, required gates, frozen snapshot files, build
steps) live in that project's `AGENTS.md`, its runbooks and its current
session handoff; read them before the first report or review.

## Every block you write

- Carry what varies, in full: results, SHAs, filled commands, choices to rule
  on, deviations. Never "as discussed" or "see above". Rules this skill states
  are already known to both sessions and stay out of the block, and so does
  what the receiver can read: a reviewer reads the code at the named SHA, so a
  report gives results rather than narrating the implementation.
- Inline in the reply as one code block the user pastes whole. Use a file only
  for a prompt that is long and revised repeatedly, or a lasting template.
- Relay blocks must not contain nested code fences: internal triple backticks
  prematurely close the outer block. Put commands, scripts and quoted output
  on plain text lines or use indentation instead.
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

End every completed step with a review request carrying:

- the checkout (a managed worktree path, or `independent clone` plus
  its host kind, such as `cloud VM`), branch, tip SHA and its base SHA;
- spec/plan sections in scope and the contract sections the step rewrote;
- changed-file map;
- targeted test commands with their exact summary lines;
- mutants run, one per guard, launchers and scripts included, and every
  survivor;
- deviations, open points, known failures you do not own, deferred items;
- the specific questions for the reviewer, including the real path to walk.

When taking over a topic from another session, fetch first and commit
only on the fetched tip; never continue from a stale base.

After a PASS the reviewed SHA is frozen; fixes go in new commits.

Run targeted tests against the worktree with
`atc test run <worktree> <scratch> <python> <label> <pytest args...>`.
`<scratch>` is a name; the run creates it as `<tmp>/<scratch>`, where `<tmp>`
is `~/.atc/tmp`, or `ATC_TMP` when that is set. The run removes every `HAMI_*`
variable so the host's project settings stay out of it, points each data-root
variable the tree's `[tool.atc-testkit]` names, or `QUANT_DATA_ROOT` when it
names none, at `<scratch>/qdr`, makes the scratch the temporary directory,
and proves with its import-location guard that the imports come from the
tree (`COPY_IMPORT_MISMATCH`, exit 8).

A tree that runs source child processes needs a `.venv`:

- Prepare it as the project's `AGENTS.md` says; never with Git Bash `ln -s`,
  which copies the whole venv here.
- `atc tmp clean` takes a scratch's `.venv` and `tree\.venv` out as links
  ("Reviewer: after integration").
- `atc tmp clean` refuses a scratch under `%TEMP%\q`; delete those with
  `powershell -NoProfile -ExecutionPolicy Bypass -File shared/skills/hami-review-relay/references/clean_scratch.ps1 <dir>...`
  from the root of the ATC checkout. It takes links out as links and stops on
  any link it does not expect.
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
one `atc test mutants` call.

1. **Pin the revision.** First confirm the previous topic's closure read-only:
   the `main` position and `atc worktree audit`. Then review exactly the tip
   SHA the request names, against its stated base. If a check shows its
   premise is stale (for example, `main` moved, a referenced file or section
   was renamed, or a count changed), re-check against current state and
   continue; do not stop to ask the user to verify a premise you can check
   yourself. Done when the branch tip equals that SHA.
2. **Build a scratch copy.** Export that exact tree to `<tmp>/<scratch>/tree`
   (`git archive <sha>`) and run it through `atc test run` as "Writer: the
   step report" says, which points imports at the copy and isolates data
   roots; strip ambient credentials from the shell that runs it. Tests and
   mutants never run in a live worktree. Prepare and delete a `.venv` in the
   copy as "Writer: the step report" says. An exported tree can differ only
   in line endings, so compare files with carriage returns ignored. Done when an import-location guard proves
   the copy, not an installed checkout, is what runs.
3. **Walk the real path.** Trace from the user-facing entry to the external
   effect (the order request, the published event, the written record) and
   list every gate and required input on the way. Done when each one is shown
   delivered by the real product assembly, not only by test fakes. Wording
   such as "pending wiring" or "awaiting acceptance" in a delivery is a
   blocker to tell the user now.
4. **Run tests and mutants.** Tests only: that file plus mutants. Behaviour:
   the targeted set plus every project gate the change touches. One
   single-string mutant per guard, one at a time, restored after each run. Done when every file in the copy is
   proven equal to its Git object again and every surviving mutant is listed
   as a test gap or argued equivalent.
   `atc test mutants <scratch> <sha> <worktree> <mutants.json> --python <py> --tests <path>...`
   performs this step on the copy at `<tmp>/<scratch>/tree`. `<mutants.json>`
   is a JSON array of `{"label": ..., "edits": [{"path", "old", "new"}]}`,
   every `old` occurring exactly once in its file. A red unmutated run prints
   `BASELINE-RED` and applies no mutant. Each mutant prints `KILLED` or
   `SURVIVED`; an `INCONCLUSIVE` mutant stops the run and leaves the rest
   unrun. Each touched file then prints `restored-ok` or `RESTORE-MISMATCH`,
   and the last line reads `done: M mutants, S survived <labels>`, or
   `done: N of M mutants, S survived <labels>` after a stop.
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

- First line: `<step> review: PASS.`, conditional PASS or FAIL.
- A "verified" paragraph: test counts, mutant results, read-only host
  evidence.
- On a PASS, when the project's adopted process lets a relayed PASS authorize
  integration, the integration instruction. The user pasting it approves
  integration and then default cleanup of the topic. Before writing it, run
  `atc integrate --check` with the same arguments as the integrate command
  below; it is read-only. The PASS carries the instruction only when the
  check prints `CHECK FF` or `CHECK NO_FF`; quote that line and every
  `WARNING` line in the "verified" paragraph. A `STOP` line, such as a
  missing `## Integration verification` section, leaves the PASS without an
  instruction: give the user the stop and its `next:` line. In a repository
  without managed worktrees, `STOP CHECKOUT_NOT_ON_MAIN` naming the topic
  branch is the expected check result: the PASS still carries the
  instruction, the "verified" paragraph quotes that `STOP` line and every
  `WARNING` line, and the writer's run after `switch main` assesses a dirty
  checkout and `FF` or `NO_FF`. The instruction carries, with every value
  filled in:
  - `atc integrate --project <name> --reviewed <full SHA> --authority-reference "<record>"`,
    with the project's `workspace.yaml` name and the exact 40-character
    reviewed SHA. The workspace root takes `--repo <root checkout>` in place
    of `--project`. The command names
    no branch, verifier or timeout: the tool finds the branch from the
    reviewed SHA and reads the verifier argv file and timeout from the
    `## Integration verification` section of `AGENTS.md` at `main`.
    `<record>` takes a form of `GIT_WORKFLOW.md` § Authority reference: the
    plan path, section and step (`<plan path> §<section> step <n>`), the
    project's task record, or
    `<owner>/<repo> <branch> per <file> § <section>`; the tool appends
    ` @ <reviewed SHA>`;
  - for a topic with a managed worktree, the default cleanup command in the
    hami-worktree skill's command form; load that skill before writing any
    worktree command;
  - for a repository without managed worktrees, where the topic branch lives
    in the main checkout, `git -C <main checkout> switch main` before the
    integrate command and `git -C <main checkout> branch -d <topic branch>`
    after it. `-d` deletes only a branch `main` already contains.

  Squash-integrated and abandon cleanups never ride on a PASS; hami-worktree
  states their authority. A conditional PASS or FAIL carries no integration
  instruction.
- Anything needing the user's decision (version numbers, spec semantics, risk
  limits, remote deletes) stops for the user.
- Then the next step's scope and the conditions this step carries into it.

## Writer: after integration

A PASS authorizes only the commands it carries; build, install, push and any
other action need their own instruction.

1. Run the `atc integrate` command (after `switch main`, when the block
   carries it) and check its result line. `FF` matches when the new `main`
   equals the reviewed SHA; `NO_FF` matches when the second parent equals the
   reviewed SHA and the first parent equals the `main` the line reports as
   before.
2. On a match, run the cleanup command: the worktree cleanup under
   hami-worktree's run rules, or the `branch -d`.
3. Run the hami-handoff-reminder check and report the result lines to the
   user only; the reviewer gets no relay.

Stop on a `switch` failure, a result mismatch, a cleanup verdict other than
`OK`, `cleanup_complete` other than `true`, or a `branch -d` refusal: leave
`main`, the worktree and the branch as they are, take only the read-only next
step hami-worktree names for a cleanup refusal (such as `audit` after
`PERMISSION_DENIED`), and hand the result lines to the user for the reviewer.

Done when the cleanup reported `cleanup_complete: true` or deleted the
branch, and the handoff check ran, or the stop is with the user.

## Reviewer: after integration

Once you confirm a topic integrated (the `main` position in step 1 of the next
review), delete that topic's scratches right away with
`atc tmp clean <dir>...`, or with `atc tmp clean --project <name>` when every
scratch of that project is done. `atc tmp list` lists the candidates with
their project, tree and size. Each `<dir>` ends on `DELETED`, `SKIPPED` or
`REFUSED`; a `REFUSED` leaves that scratch as it is for the user.

A failing verifier or gate run leaves its working directory under
`<tmp>/systmp` for inspection; it stays until `atc tmp prune` deletes it,
seven days after its last change.

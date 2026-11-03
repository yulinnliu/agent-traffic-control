---
name: hami-review-relay
description: Writer/reviewer work relayed by the user between two sessions that cannot see each other - writing a step report for review, reviewing a relayed commit, writing the verdict block, checking an integration. Not for reviewing your own uncommitted work.
managed-by: agent-traffic-control
canonical: hami-review-relay
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

- the worktree path (or the checkout, for a repository without managed
  worktrees), branch, tip SHA and its base SHA;
- spec/plan sections in scope and the contract sections the step rewrote;
- changed-file map;
- targeted test commands with their exact summary lines;
- mutants run, one per guard, launchers and scripts included, and every
  survivor;
- deviations, open points, known failures you do not own, deferred items;
- the specific questions for the reviewer, including the real path to walk.

After a PASS the reviewed SHA is frozen; fixes go in new commits.

Run targeted tests against the worktree with
`shared/skills/hami-review-relay/references/run_tests.sh <worktree> <scratch> <python> <label> <tests...>`
from the root of the ATC checkout. It points `QUANT_DATA_ROOT` at the scratch
directory, unsets every `HAMI_*` variable so the host's project settings stay
out of the run, and proves the imports come from the worktree.

A tree that runs source child processes needs a `.venv`:

- Prepare it as the project's `AGENTS.md` says; never with Git Bash `ln -s`,
  which copies the whole venv here.
- Delete scratch directories under `%TEMP%\q` with
  `powershell -NoProfile -ExecutionPolicy Bypass -File shared/skills/hami-review-relay/references/clean_scratch.ps1 <dir>...`
  from the root of the ATC checkout; it takes links out as links and stops on
  any link it does not expect. A worktree's `.venv` junction comes out the same
  way, as the link alone with `cmd //c rmdir` on its literal path, before the
  worktree's own cleanup.

## Reviewer: the review

Work through every step; each ends on the stated criterion.

1. **Pin the revision.** First confirm the previous topic's closure read-only:
   the `main` position and `atc worktree audit`. Then review exactly the tip
   SHA the request names, against its stated base. Done when the branch tip
   equals that SHA.
2. **Build a scratch copy.** Export that exact tree to a scratch directory
   `%TEMP%\q\<name>` (`git archive <sha>`), point imports at the copy, isolate
   data roots and strip ambient credentials; tests and mutants never run in a
   live worktree. Prepare and delete a `.venv` in the copy as "Writer: the
   step report" says. An exported tree can differ only in line endings, so compare files
   with carriage returns ignored. Done when an import-location guard proves
   the copy, not an installed checkout, is what runs.
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
   as a test gap or argued equivalent.
   `shared/skills/hami-review-relay/references/mutants.py` and `run_tests.sh`,
   run from the root of the ATC checkout, perform steps 2 and 4 from a mutants
   list.
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
- A "verified" paragraph: test counts, mutant results, read-only host
  evidence.
- On a PASS, when the project's adopted process lets a relayed PASS authorize
  integration, the integration instruction. The user pasting it approves
  integration and then default cleanup of the topic. It carries, with every
  value filled in:
  - `atc integrate --repo <main checkout> --branch <topic branch> --reviewed <full SHA> --authority-reference <authorization record> --verify-command-file <JSON argv file> [--verify-timeout <seconds>]`,
    with the exact 40-character reviewed SHA. `--authority-reference` takes
    the task-identity form `GIT_WORKFLOW.md` § Integration verifier source
    gives: the plan path, section and step, or the project's task record,
    then ` @ ` and the reviewed SHA. `--verify-command-file` is the path the
    project's `AGENTS.md` `## Integration verification` section names,
    resolved under the main checkout, and `--verify-timeout` appears only
    when that section lists one. When the section is missing, the PASS
    carries no integration instruction: stop and ask the owner. Pass the
    argv inline with `--verify-command` only from a shell that preserves
    embedded double quotes; PowerShell 5.1 strips them, so a pasted inline
    JSON argv fails there;
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
review), delete that topic's scratch directories with `clean_scratch.ps1`
right away; `%TEMP%\q\LEDGER.txt` lists the candidates, one scratch per line.

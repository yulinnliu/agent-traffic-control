# GIT_WORKFLOW.md

Canonical revision lifecycle. Publication is separate; load `DELIVERY.md` before publishing.

## Exact base SHA

Do not define:

```text
current local main
```

as the universal task base.

Instead:

> Every implementation task has one authoritative exact base SHA.

Resolve it from the strongest available authoritative source, independent of
execution location:

```text
exact base_sha supplied
        |
verify the object is available to the authoring context
        |
freeze base_sha
```

Otherwise:

```text
authorized base_ref supplied
        |
resolve it exactly once at task start
        |
record and freeze base_sha
```

Otherwise, when the execution context has an authoritative integration
checkout/ref:

```text
authoritative integration branch HEAD
        |
resolve exact SHA once
        |
record and freeze base_sha
```

If none of those can resolve the base authoritatively, stop rather than guess.

For example:

```text
base_ref: origin/main
base_sha: abc123...
```

The SHA, not a moving symbolic ref or machine location, is authoritative after
resolution.

An unpublished commit may be a valid base only when it is available to the
authoring context and every later step that must retrieve, verify, review, or
integrate that ancestry. If a downstream executor cannot retrieve it,
publication or another explicitly supported handoff is required before that
step can proceed.

`base_sha` means the starting integration baseline for the **current delivered
topic history**. Before formal review, history may still be rewritten under the
existing lifecycle. If a rebase or other rewrite changes the topic's ancestry
onto a newer integration baseline, update `base_sha` to that new baseline and
report the change. A rebase whose review carries over (`After formal review`)
does the same: `base_sha` becomes the `main` it was rebased onto, which the
integration record holds as `main_before`. Adding a later merge from `main`
without rewriting the existing topic history does not retroactively redefine
the original `base_sha`; the merge commit itself records that additional
ancestry and must be included in the delivered/reviewed history.

For **Hami Managed Worktree**, the current adapter creates a new worktree from
the authoritative integration checkout's current `main`. Therefore it is
compatible with a new task only when that creation base equals the task's bound
`base_sha`. If they differ, do not silently start from a different commit and
do not rewrite the task's base merely to fit the adapter; select another
compatible execution context. No managed-worktree runtime change is required
for this rule.

To continue a topic already published on `origin`, the adapter's
`create --base-sha` starts the worktree at the exact tip of that topic's own
branch on `origin` and refuses any other commit. That tip is the worktree's
creation base; the task's bound `base_sha` remains the topic's original
integration baseline.

## Topic branch

A topic branch remains the unit of implementation ownership.

Canonical governance does not require a branch to share the worktree path's
identity and does not require this naming form:

```text
worktree-<owner>+<topic>
```

Existing Hami Managed Worktree branches remain valid. The current machine
contract version remains `1`; the version number is an implementation
compatibility detail, not the human-facing name.

Future tasks may gradually adopt task-oriented names such as:

```text
task/<task-id>
topic/<task>
```

Executor/model identity should not be required in the canonical branch name;
writer ownership is governance metadata, not branch identity. Existing
agent/tool-derived branch names remain valid and are not migrated in bulk.

Branch naming migration is not required for governance adoption.

Existing branches must not be mass-renamed.

## Branch writer ownership

The branch bound before first mutation is authoritative. One active writer; transfer follows `DELIVERY.md`.

## Before formal review: history is draft

Until formal review, the owner may amend, squash, regroup
(`git reset --soft <base>` + one commit per group), or rebase onto a newer
mainline within the task's authority. Amend related corrections to the same
unpublished draft change; use separate commits for distinct reviewable changes.
Consolidate the final commit structure before final self-verification and
formal review, so evidence and review identify the commits that will land.

### Mainline check before final self-verification

After implementation is complete and before final self-verification for review,
inspect the task's authoritative integration ref and record its exact current
SHA. Compare that revision with the topic's bound base and assess whether new
mainline changes affect the implementation, dependencies, contracts or expected
integration. Mainline advancement alone does not require a rebase. Earlier
iteration checks remain available throughout implementation.

For an owned, unpublished, unreviewed draft, rebase before final
self-verification when needed to resolve integration conflicts or incorporate
relevant mainline changes, within the task's authority to update its base.
Record the updated `base_sha`, finalize the draft commits, then run the affected
checks on the resulting tip. Reuse only evidence that still applies to that
revision and environment under `VERIFICATION.md`.

Before its step report the writer runs `atc integrate --check` on its tip and
puts the verdict token in the report. On `REBASE_REQUIRED` or `OVERLAP` it
rebases onto `main` itself and reports the new tip. A writer whose task has
no grant on `main` gets `AUTHORITY_MISSING` from that check (§ Integrate) and
reports that token as pending authority.

Published history follows the publication restrictions below. After formal
review, use the frozen-history and integration rules below; a task-authorized
rewrite invalidates the previous review. Check the mainline again immediately
before integration to account for changes since self-verification.

### Published pre-review history

The pre-review amend/squash/rebase permission above applies to unpublished
draft commits. If a topic ref is already published and other executors may
have fetched it, do not rewrite its published history merely to tidy commits.
Prefer a separately authorized consolidated review-candidate ref. Record the
old published WIP SHA, new candidate SHA, original ref, and candidate ref in
the existing Task Delivery Packet so receiving executors can retrieve and
reconcile both histories.

Pre-review status does not authorize force push. Any rewrite of already
published history requires separately applicable force-push authority and a
safe reconciliation of dependent consumers. A formally delivered/reviewed
ref remains governed by the exact-SHA freeze and re-review rules below and
by `DELIVERY.md`. Do not rename existing Managed Worktree branches solely
for alignment with task-oriented branch naming.

## After formal review: reviewed commits are frozen

- Never amend, squash, rebase, or force-push a commit at or below the
  reviewed SHA.
- A further change is a new commit on top. Only that delta needs review, and
  its verdict names the new tip.
- If the reviewed SHA is no longer an ancestor of the branch tip, the review
  is void: the branch needs a full re-review.
- Rewriting anyway (for example, task authority explicitly chooses a rebase) is allowed only as an
  explicit decision, and voids the review the same way.
- The one exception is the rebase `Integrate` requires of an unpublished topic
  whose base `main` has advanced. The review carries over to the rebased tip
  only when that tip is based on the current `main`, neither range holds a
  merge commit, the commit count and order are unchanged, every
  `git patch-id --stable` matches, and replaying the reviewed commits onto
  `main` is conflict-free and yields the rebased tree. `atc integrate
  --rebased` checks this and still runs the full verification on the rebased
  tip. Otherwise the rebased tip needs a fresh review.
- After a rebase the writer reports only the new tip. The coordinator runs
  `atc integrate --check`, with `--rebased` when a review exists, for the
  carry-over and `OVERLAP`, and runs no range-diff or file diff by hand.

## Integrate

Integration needs current explicit `integrate_main` authority plus any stricter
repository/project approval. Review approval and integration authority are
separate prerequisites that may be granted in one owner statement. Once both
are present, enter the repository's integration queue below, re-check `main`,
and select the first matching row to identify the integration revision.
Complete required verification under `VERIFICATION.md`, including its
documentation-only exception, before advancing `main`; then recheck the source
and target refs immediately before the update.

`atc integrate` executes this table: each row maps to one verdict, `STOP`
with its reason code for a stop row, `FF` for the fast-forward row and `NO_FF`
for the compatible-merge row. The unpublished-topic row stops as
`REBASE_REQUIRED`, or as `CONFLICT` or `OVERLAP` when the trial merge finds
one, each with a `next:` naming the rebase.

Before evaluating the table, `atc integrate` (`--check` included) runs
`git fetch origin`. A local `main` behind or diverged from `origin/main` stops
before verification; integrate onto a `main` that contains `origin/main`. A
local `main` ahead of `origin/main` proceeds. A topic is published when an
`origin` branch tip equals the reviewed SHA as fetched; in a repository
without an `origin` remote, which fetches nothing, every topic is unpublished.

`atc integrate --check` checks review binding and integration topology,
takes no slot, creates no candidate and prints one `CHECK` line: the
verdict, a reason code, the project, branch, tip and task binding; `--json`
carries the full evidence. Its verdict is a topology decision; integration still
requires the authority and verification steps of this section. The check
resolves the task's authority as the integration does, but needs none: it
always names the task, and without `--owner-words` a task whose record grants
no `integrate_main` reports `authority=missing` where the integration stops
with `AUTHORITY_MISSING`. After the reviewer's PASS the check runs before the
Owner is asked:

```text
atc integrate --check --project <project> --reviewed <sha> --task <task> [--owner-words <quote>]
```

Its `verification_reuse` says whether the integration will reuse the
reviewer's `atc verify` record of the same candidate or run the verifier
(`shared/conformance/verification-evidence/README.md` § Integration reuse).

The Python integration adapter acquires the repository Integration Slot before
preflight, prepares the integration revision in an isolated candidate worktree,
and requires a passing verification command before updating `main`. Name the
reviewed commit by its full 40-character SHA or a unique prefix of at least 12
characters, which the run resolves once to the full SHA the integration record
holds; an ambiguous prefix stops. Name the task and its authority as
`Task and authority` below states; `Integration verifier source` names where
the verification argv comes from. The command
runs from the candidate checkout with these environment variables:

- `ATC_INTEGRATION_SHA`: immutable candidate commit SHA;
- `ATC_CANDIDATE_DIR`: isolated candidate checkout path;
- `ATC_EXPECTED_MAIN_SHA`: main SHA the candidate was based on;
- `ATC_REVIEWED_SOURCE_SHA`: reviewed source SHA;
- `ATC_VERIFICATION_REPORT`: path outside the candidate where the verifier
  may write its verification report.

When verification passes with a valid report, the adapter writes an evidence
record for the candidate under the repository's Git common directory after
`main` is updated and read back. A missing or invalid report leaves
integration unaffected and writes no record. The report and record formats
are defined in `shared/conformance/verification-evidence/README.md`. `atc verify`
writes a record of its own candidate, passed or failed, apart from the
records the pre-push gate reads; within the active slot `atc integrate`
reuses its latest passing one in place of rerunning the verifier when the
candidate tree, expected main, reviewed SHA, verifier argv and timeout,
platform, dependency inputs, selector and time slack all match, and names
the reused record in its evidence and integration record.

Apart from that evidence, the adapter records every integration that updates
`main`, with or without a verification report, as one JSON line in
`<git common dir>/atc/integrations.jsonl`: the candidate SHA, the reviewed
SHA, the `main` it was integrated onto (`main_before`), the task, its
authority, whether the range is records-only, and the time (the keys are in
`Task and authority`). For a carried-over review, `reviewed` holds the
rebased tip and `carried_over_from` the SHA the review named; the evidence
record carries the same pair. The pre-push gate reads only the verification
evidence of `Test evidence` below, never this record; `atc preflight push`
reads it to group the commits of a push (`Publication boundary`).

The verifier must test the candidate identified by `ATC_INTEGRATION_SHA`, not
resolve a moving branch name. A nonzero result, changed candidate HEAD, dirty
candidate checkout, or source/main drift blocks the update. The adapter records
the task and authority it ran under; what grants `integrate_main` is
`Task and authority` below. A recorded quote is audit evidence, not an
authentication mechanism.

The OS lock coordinates cooperative ATC integration writers only. Every ATC
writer to the target mainline must honor the slot; unrelated Git processes can
bypass it. The final checkout-aware fast-forward is an additional guard against
incompatible mainline drift, not a claim that arbitrary external writers are
serialized by the lock.

| State | Action |
| --- | --- |
| Tip ≠ reviewed SHA, or reviewed SHA not an ancestor of the tip | Stop: review the delta, or re-review |
| `main` is the merge-base (fast-forward possible) | Verify the reviewed tip, then `git merge --ff-only <verified-full-sha>` |
| `main` advanced; topic unpublished | Stop: rebase onto `main`, then integrate the rebased tip with `--rebased`, which carries the review over under `After formal review`; a rebase that resolves conflicts, or any refused carry-over, needs a fresh review |
| `main` advanced; topic published; the merge would conflict | Stop: reconcile on the topic branch with a new commit (merge `main` in, or fix forward), never by rewriting; re-review that commit |
| `main` advanced; topic published; some file changed on both sides | Stop: a reviewer judges semantic overlap, then prepare a merge candidate or reconcile as above |
| `main` advanced; topic published; no conflict, no shared file | Prepare and verify a merge candidate under the procedure below, then advance `main` to it |

With `--reviewed <reviewed SHA> --rebased <rebased tip>`, the rebased tip
takes the place of the reviewed SHA throughout the table: the first row
compares the branch tip with the rebased tip, and the fast-forward row
verifies and integrates it. The carry-over check runs after the table and
before any candidate exists.

### Repository integration queue

Use one integration queue per repository, coordinated in the existing task or
delivery record. One integrator owns the active slot from freezing the source
and target revisions through verification and the final update. Only the
active slot starts the required integration checks; waiting topics can
continue development and impact-selected checks on their own branches.

All writers to the target mainline respect the active slot. The integrator
serializes the final expected-main check and update under that exclusive
ownership. A worktree's structural lock is not this coordination mechanism.
Record the integrator, reviewed source SHA, expected main SHA, integration SHA
and evidence location. Release the slot on completion, failure or deliberate
pause; any writer handoff follows `DELIVERY.md`. Coordination uses existing
integration authority rather than adding another owner-approval round.

### Verify the integration revision before updating main

For a fast-forward, the integration revision is the final reviewed topic tip.
For a merge allowed by the table, prepare the merge commit on an authorized
integration-candidate branch or equivalent isolated execution context. Its first
parent is the recorded current `main` tip and its second parent is the reviewed
topic tip; use `merge(<topic>): integrate reviewed changes` as its subject.

`atc integrate` is an authorized integration context for this merge: the table
admits it only when the preflight finds no conflict and no file changed on both
sides, and the relayed PASS on the reviewed tip is the verification record; the
merge commit it produces is the integration revision.

The reviewed topic stays unchanged. Conflict resolutions or source changes
follow the reconciliation and re-review rules in the table.

Resolve the integration revision to its full immutable object ID before
running the required checks under `VERIFICATION.md`. Keep that ID bound to the
evidence and use it for the update, rather than resolving a mutable topic or
candidate branch again after verification.

Within the active integration slot, recheck that the source tip and expected
mainline SHA still match the recorded values. Serialize that check and the
update so another mainline writer cannot intervene. Use a checkout-aware
fast-forward to the verified full SHA, then read back `main` and verify its
checkout is clean and at that exact revision. A ref-only update of a checked-out
branch is insufficient because its index and working tree must stay consistent.
An executor with atomic expected-old-SHA update support must also preserve
those checkout invariants.

A failed check leaves `main` unchanged. Ref drift stops the update and requires
reassessment, any necessary review, and evidence for the final integration
revision. Reuse valid evidence under `VERIFICATION.md` when resuming a queued
integration.

A merge candidate preserves the reviewed commits and records them as one
reviewed unit through its second parent. Integration-candidate preparation
requires an authorized context under the applicable repository rules.

### Explicitly authorized squash

Squash (`git merge --squash`) is not a default integration method. The task
authority may select it for a specific reviewed tip and target branch. The
decision records the tip SHA, the reviewed SHA, the target branch, and the
task with the Owner's words that select the squash; the squash commit
message carries the source tip SHA. The first row of the table above still
applies: the tip must be the reviewed SHA before squashing.

Squashing leaves the reviewed commits untouched on the topic branch, so the
freeze rule above holds, but they are no longer ancestors of `main`. The
squash commit therefore needs its own verification before updating `main`:
prepare it in an authorized candidate context, confirm that its first parent is
the recorded target tip and that its tree equals the recorded source tip's tree,
and advance `main` to its verified full SHA under the same integration-slot,
expected-main and checkout-consistency safeguards above.
For this explicitly authorized, same-tree squash, successful source-tip test
evidence may carry over when the verification environment and configuration
also match. Record both SHAs and tree equivalence with that evidence. Any other
shape (`main` advanced before the squash, or overlapping files) is reviewed as
a new integration.

The source branch stays as review evidence; deleting it is a separate
decision. Matching trees or patches never establish authorization or
completeness by themselves. Plain `atc-worktree` cleanup checks ancestry only
and reports a squashed topic as `UNMERGED`. With valid cleanup authority, a
squashed Hami Managed Worktree is cleaned up with `cleanup --squash-integrated`
(see its skill), which verifies the recorded SHAs, tree and retained ref; the
caller checks the squash commit message and the integration record. The worktree
is never removed by hand.

## Git environment in hooks and tests

Hooks export repository-selecting variables such as `GIT_DIR`. A test or
script run from a hook that spawns git inherits them, so a fixture's
`git init <tmp>` or config write can act on the real repository, for example
flipping `core.bare` in a shared config and breaking every linked worktree.

- Git processes spawned by tests run with every `GIT_*` variable removed from
  their environment.
- Reproduce an inherited-`GIT_DIR` bug in a throwaway repository (a main
  checkout plus a linked worktree under a temp directory), never by running
  the unfixed code against a real repository's git directory.
- Record `git config --get core.bare` and a hash of the shared config before
  and after any experiment that sets `GIT_DIR`; after an aborted push with
  test errors, check `core.bare` first.

## Test evidence

Test evidence identifies the checked commit SHA and whether the tree was
clean. Verification scope, full regression timing and evidence reuse follow
`VERIFICATION.md`. Complete required integration checks on the integration
revision before advancing `main`. A fast-forward to that exact verified
revision preserves its evidence and requires no duplicate run solely because
it is now on `main`. Failed verification blocks the update; correct the
candidate under the applicable review rules before verifying again.

## Verification boundary

Formal evidence follows `VERIFICATION.md`; changing the delivered revision invalidates evidence that no longer applies.

## Task and authority

`atc integrate` names the task with `--task` and takes its authority from
exactly one source.

- `--task` is required. `<prefix>-<four digits>`, with a prefix declared
  under `## Task records` in `AGENTS.md`, names a task record and derives the
  repository from the prefix. The record must exist in the reviewed commit's
  tree (`TASK_NOT_FOUND`), and its `branch` is the integrated branch, which
  must hold the reviewed SHA as its tip; for a records-only range that branch
  is already at the reviewed SHA (`NO_BRANCH_AT_REVIEWED`). Any other value is
  a task identity string (`CORE.md` § Task identity) and needs `--project`
  (`root` for the workspace root); it integrates the one local branch at the
  reviewed SHA.
- The authority is either the record on `main` with `integrate_main: true`,
  or `--owner-words <quote>`: the Owner's words quoted, or the decision
  label of a command the Owner runs.
- A range whose changed paths all lie under `.atc/tasks/` is records-only: the
  record check runs in place of the verifier.
- Recovering an interrupted integration takes `--task`.

A record grants nothing by itself; the Owner's words do. A record on `main`
with `integrate_main: true` is the Owner's integration authority for `FF`
after an unconditional PASS on the exact SHA, never for `NO_FF`
(`AUTHORITY_NOT_FF`), a push or a deploy.

Who runs which form is split by flag:

- whoever the project's rules let integrate runs `atc integrate --task <ID>`
  on the record's authority;
- only the coordinator, who hears the Owner, writes a record's `authority`;
- `--owner-words` carries the Owner's decision: the Owner runs the
  integration command the coordinator gives (§ Owner-run commands), with
  the decision label `Owner run: approve integration` as the quote. The
  coordinator never runs it and never relays it to a writer. The Owner
  running the command is the authority; no local artifact attests it.

In a paste-only setup with no coordinator session, the user writes the
instruction and runs it, with their own words as the quote.

The writer keeps the record's `delivery.status`. A grant ends the moment the
Owner narrows or revokes it, whatever the record on `main` still reads. A
grant counts only when it entered `main` through this machine's records-only
`--owner-words` integration. `docs/TASKS.md` § Authority on `main` states how
`atc integrate` finds the grant's commit and what it refuses.

Each `integrations.jsonl` line carries `task`, `records_only` and
`authority`: `{"kind": "record", "task_id": ...}`,
`{"kind": "owner_words", "quote": ...}` or `{"kind": "sync"}` for a
governance sync commit; `{"kind": "unclassified"}` remains a value readers
accept in existing logs. A records-only integration that binds several tasks
writes one line per task with the same candidate, and every records-only line
carries `main_before`.

### Owner-run commands

After the reviewer's PASS and the check above, the coordinator gives the
Owner each command to run, one at a time: the integration, and after
`atc preflight push` the push of `main` (§ Publication boundary). Each is one
line that runs unchanged in PowerShell and in a POSIX shell:

- one command, with no `cd`, `&&`, `;`, pipe, redirection or variable;
- a checkout named by `git -C "<drive>:/<path>"`, the Windows drive path
  with forward slashes in double quotes;
- quoted text of letters, digits, spaces and `._:/-` only;
- every SHA in full, 40 characters.

```text
atc integrate --task <task> --reviewed <40-character SHA> --owner-words "Owner run: approve integration"
git -C "<main checkout>" push origin <40-character SHA>:refs/heads/main
```

A task identity string adds `--project <name>`; a record on `main` that
grants `integrate_main` for `FF` drops `--owner-words`. A records-only range
takes the same form.

The coordinator reads completion from Git, never from a report that the
command ran: the integration is done when `<git common dir>/atc/integrations.jsonl`
holds a line with the task and the reviewed SHA whose `candidate_sha` is
`main` or its ancestor; the push is done when `git ls-remote origin
refs/heads/main` names the pushed SHA or a descendant of it. Until then the
command waits on the Owner; a stop's token and `next` (§ Command results)
decide what follows, and the Owner's decision binds the SHA it named.

## Integration verifier source

The verifier argv file and timeout come only from the
`## Integration verification` section of `AGENTS.md` at the `main` revision
being integrated into (`Project verification section` below). Only
`--verify-timeout` may override the declared timeout; a value that differs
from the declaration is reported as a warning. A repository whose `main`
revision has no such section stops the integration; the caller obtains one
from the owner.

### Project verification section

Every registered project's `AGENTS.md` carries a section headed exactly
`## Integration verification`. It lists:

- `Verifier argv file: <repo-relative path>` — the file holding the JSON
  string array `atc integrate` runs.
- `Verifier timeout: <seconds>` — only when the verifier needs a bound other
  than the default 300 seconds; it is the test budget of `VERIFICATION.md`
  § Verifier test budget.
- `Verifier budget approval: ...` — only beside a `Verifier timeout` above
  1800 seconds, in the form that section gives.
- What the verification covers, by change class, including the project's
  required gates.
- How the verifier itself applies `VERIFICATION.md` § Documentation-only
  verification: it classifies the `ATC_EXPECTED_MAIN_SHA..ATC_INTEGRATION_SHA`
  diff and runs the content, reference and static checks instead of
  impact-selected tests when the diff qualifies. The verifier is the sole
  judge of that exception.

`atc validate --projects` reports a registered project whose `AGENTS.md`
lacks the section or names a missing or malformed argv file.

### Candidate checkout

The verifier runs with the candidate checkout as its working directory. That
checkout is created by `git worktree add --detach` at the candidate commit: a
fresh working tree with no virtual environment, no installed packages and
nothing carried over from the main checkout. The adapter sets
`ATC_INTEGRATION_SHA`, `ATC_CANDIDATE_DIR`, `ATC_EXPECTED_MAIN_SHA`,
`ATC_REVIEWED_SOURCE_SHA` and `ATC_VERIFICATION_REPORT`. When the verifier returns, the candidate `HEAD`
must still equal `ATC_INTEGRATION_SHA` and `git status --porcelain` must be
empty, untracked files included; ignored paths may hold what the verifier
built. The verifier runs inside its own process tree (a Job Object on Windows,
a session on POSIX) that the adapter ends when the verifier returns or the
timeout expires, so no process the verifier started outlives the
verification.

The verifier therefore:

- builds its environment under an ignored path inside the candidate, or
  obtains one outside it, without dirtying the tree;
- proves the code under test is imported from the candidate: an editable
  install of the main checkout resolves imports back to that checkout's
  source and fails this proof;
- selects its checks from the `ATC_EXPECTED_MAIN_SHA..ATC_INTEGRATION_SHA`
  range plus the project's required gates, and finishes within the timeout;
- may write the verification report: the selection it executed, its test
  stages and the digests of its dependency inputs;
- exits nonzero on any failure; its stderr becomes the refusal detail.

### Lock advancement

A project's `.atc/atc.lock` advances to a revision carrying this section only
when the project's `AGENTS.md` already has its
`## Integration verification` section and the named argv file exists.
Otherwise sync stops the project with `VERIFICATION_SECTION_MISSING` and
writes nothing, so the synced files never come from a newer revision than
the lock. Add the section and the file in the project first, then sync.

## Governance sync commits

`atc onboard --sync --project <project>` and `atc onboard --sync --all`
commit what they render on each project's `main` and append the commit to
`<git common dir>/atc/integrations.jsonl` with `kind: sync`. Such a commit
bypasses `## Integrate`: it needs no review and no `integrate_main` authority
of its own, because the ATC revision it names determines its content and
that revision already carries the Owner's integration approval in the ATC
repository. Pushing it still needs `push_main` authority under
`## Publication boundary`.

Sync renders from the ATC checkout on `main` with no tracked change, and
renders nothing until that checkout's own adapters are current. Per project
it takes the Integration Slot and writes nothing when the checkout is not on
`main`, has uncommitted or untracked changes, or does not contain
`origin/main`. A changed path outside the files sync owns stops the project
with the change left uncommitted; a failed `git commit`, such as a hook
refusal, stops it with the rendered files written and staged. Either way the
next sync stops as dirty until the Owner resolves the checkout. Every file
sync writes is committed: a project that ignores or excludes one stops before
anything is written, and `atc validate --projects` reports it.
`atc preflight push` shows a sync commit as `sync from <ATC revision>`.

A change sync does not render, such as a project file that cites a renamed
skill, goes through a topic and `## Integrate`. `atc onboard --sync
--root-only` renders the adapters of the ATC checkout it runs in, linked
worktree included, and commits nothing; run it in the topic that changes a
canonical skill.

## Cleanup trigger

Cleanup requires its own applicable authority (which may be granted by the
current task contract) and checks even after integration or authorized task
closure. Topology-specific cleanup applies only to the selected execution
context. Before removing a local worktree or deleting a
remote branch/ref, verify all of the following for the specific target:

- the topic's integration or closure outcome is authorized and recorded;
- no active writer still owns that mutable topic;
- unported source work has been incorporated, explicitly declined, or
  preserved for the responsible next work unit;
- every source/evidence commit still needed for review, audit or future
  implementation remains fetchable through a retained reachable Git ref or
  another approved durable artifact (a raw commit SHA alone does not guarantee
  future availability after the last ref is deleted).

Local worktree cleanup, remote ref deletion, and evidence retention are
separate operations with separate capability and authority boundaries.
Integrating a different topic never by itself makes an older WIP branch
cleanup-ready. For Hami Managed Worktree contract 1, use its existing
`atc-worktree` capability for local cleanup; do not bypass its identity,
lock, clean-tree or merged-state checks. Nothing here automatically
authorizes force push, remote deletion, or cleanup of another writer's work.

## Command results

Every `atc` command's default output is its verdict and the next action.
Detail sits behind a flag or in a log file whose path the command prints;
`--json` carries the full result for machines. An error is one line naming
the cause and the next step, and a usage error prints the usage line, not the
whole help. A run that did not complete reports `TIMEOUT` or `INTERRUPTED`
with the last lines of its log, never a pass.

`atc integrate` and `atc preflight` end with one of these exit codes; every
other command's codes are in `shared/conformance/CLI.md` § Exit codes:

| Exit | Meaning |
| --- | --- |
| 0 | Integrated, a `CHECK FF` or `CHECK NO_FF` line, or a preflight `PUSH` verdict |
| 1 | Stopped before a candidate exists: a preflight `STOP` verdict, a missing `## Integration verification` section, ref drift found before the candidate, a failed fetch, a local `main` behind or diverged from `origin/main`, a required rebase, or a refused carry-over |
| 2 | `ERROR`: an invalid invocation or a Git or runtime fault |
| 3 | Stopped after the candidate exists: the verifier failed or timed out, the candidate changed, or the refs drifted before the update, each with `main` unchanged; or a required recovery, where `main` or its checkout no longer matches the candidate after the update, found by that update or at the start of a later run |

The first stderr line of a refusal takes one of these forms:

- `STOP <TOKEN>: <detail>` for exits 1 and 3, followed by a `next:` line
  naming the step that resolves the stop: the `Integrate` table's step for a
  stop that is a row of it, otherwise the step below;
- `ERROR <TOKEN> <detail>` for exit 2;
- `<TOKEN> <detail>` for a refusal from `atc-testkit`, with the exit code
  `docs/TESTKIT.md` assigns that token.

| Stop | Next step |
| --- | --- |
| No reviewed SHA, or one that is invalid, ambiguous or names no commit | Run again with the full SHA the formal review named |
| Topic already merged into `main` | Nothing to integrate; continue with `Cleanup trigger` |
| Several local branches at the reviewed SHA | Delete or move the other local branches at the reviewed SHA, or integrate with the task's record ID |
| No local branch at the reviewed SHA, and none containing it | Restore the reviewed topic branch at the reviewed SHA, or run again with the full SHA the formal review named |
| Integration checkout not on `main` | Check out `main` in the integration checkout, then run again |
| Integration checkout has uncommitted changes | Have the owner of the listed paths commit or remove them, then run again |
| Missing `## Integration verification` section | Obtain the section from the owner, add it under `Project verification section`, then run again |
| `git fetch origin` failed | Restore access to `origin`, then run again |
| Local `main` behind `origin/main` | Fast-forward local `main` to `origin/main`, then run again; the topic is reassessed against the new `main` |
| Local `main` diverged from `origin/main` | The owner decides how the local-only commits reach `origin`; run again once `main` contains `origin/main` |
| Carry-over refused | The rebased tip needs a fresh review; integrate the SHA that review names |
| Ref drift, before the candidate or before the update | Run again; the preflight reassesses the table against the new refs |
| Verifier failed or timed out, or the candidate changed | Correct the cause on the topic branch with a new commit and re-review it, or correct the project's verifier, then run again |
| Required recovery | Reconcile `main` and its checkout with the retained candidate ref the detail names before any other integration of this repository |

## Publication boundary

Publishing a ref/revision is separately governed by `DELIVERY.md`.

`push_main` authority for a push of `main` must cover every commit in
`origin/main..main`, including commits other topics integrated. List them
before the push with `atc preflight push`, which fetches `origin` first; the
`push:` lines of `atc integrate` give the same list and flag each commit
outside that integration as lacking push approval. The Owner runs the push
with the SHA `atc preflight push` listed as `main`'s tip
(§ Owner-run commands), so the push carries exactly the commits the
preflight listed.

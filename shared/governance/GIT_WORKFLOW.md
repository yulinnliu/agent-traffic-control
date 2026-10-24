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
report the change. Adding a later merge from `main` without rewriting the
existing topic history does not retroactively redefine the original
`base_sha`; the merge commit itself records that additional ancestry and must
be included in the delivered/reviewed history.

For **Hami Managed Worktree**, the current adapter creates a new worktree from
the authoritative integration checkout's current `main`. Therefore it is
compatible with a new task only when that creation base equals the task's bound
`base_sha`. If they differ, do not silently start from a different commit and
do not rewrite the task's base merely to fit the adapter; select another
compatible execution context. No managed-worktree runtime change is required
for this rule.

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

## Integrate

Integration needs current explicit `integrate_main` authority plus any stricter
repository/project approval. Review approval and integration authority are
separate prerequisites that may be granted in one owner statement. Once both
are present, enter the repository's integration queue below, re-check `main`,
and select the first matching row to identify the integration revision.
Complete required verification under `VERIFICATION.md`, including its
documentation-only exception, before advancing `main`; then recheck the source
and target refs immediately before the update.

| State | Action |
| --- | --- |
| Tip ≠ reviewed SHA, or reviewed SHA not an ancestor of the tip | Stop: review the delta, or re-review |
| `main` is the merge-base (fast-forward possible) | Verify the reviewed tip, then `git merge --ff-only <verified-full-sha>` |
| `main` advanced; the merge would conflict | Stop: reconcile on the topic branch with a new commit (merge `main` in, or fix forward), never by rewriting; re-review that commit |
| `main` advanced; some file changed on both sides | Stop: a reviewer judges semantic overlap, then prepare a merge candidate or reconcile as above |
| `main` advanced; no conflict, no shared file | Prepare and verify a merge candidate under the procedure below, then advance `main` to it |

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
authorization reference; the squash commit message carries the source tip SHA.
The first row of the table above still applies: the tip must be the reviewed
SHA before squashing.

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
completeness by themselves. Plain `hami-worktree` cleanup checks ancestry only
and reports a squashed topic as `UNMERGED`. With valid cleanup authority, a
squashed Hami Managed Worktree is cleaned up with `cleanup --squash-integrated`
(see its skill), which verifies the recorded SHAs, tree and retained ref; the
caller checks the squash commit message and the integration record. The worktree
is never removed by hand.

`atc preflight merge` checks review binding and integration topology
read-only and prints the verdict, a reason code, and the evidence. Its verdict
is a topology decision; integration still requires the authority and
verification steps above:

```text
atc preflight merge --repo <integration-checkout> --branch <topic-branch> --reviewed <sha>
```

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
`hami-worktree` capability for local cleanup; do not bypass its identity,
lock, clean-tree or merged-state checks. Nothing here automatically
authorizes force push, remote deletion, or cleanup of another writer's work.

## Publication boundary

Publishing a ref/revision is separately governed by `DELIVERY.md`.

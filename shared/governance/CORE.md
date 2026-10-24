# CORE.md

Universal Hami governance invariants. Project/repository policy may be stricter; generic governance never silently loosens it.

```text
capability != authority
```
Technical ability never grants permission; current task/delegated authority and applicable governance/project policy must all allow the action.

## Canonical task contract

Any implementation task should be describable with at least:

```text
task_id
repository
base_sha
scope
governance_source
governance_revision

authority:
  edit
  commit
  push_topic
  integrate_main
  push_main
  deploy

verification_requirements:
  project_defined

delivery:
  branch
  target_sha
  validation_evidence
  unresolved_risks
  status
  next_action
```

`push_topic` is retained as the machine-facing field name for compatibility; in
human-facing prose it means **permission to publish the owned topic branch**.
Similarly, `FF`, `NO_FF`, and `STOP` remain machine decision codes while prose
should prefer their descriptive names.

### Effective authority and revocation

An action is permitted only when all applicable layers agree:

```text
required capability exists
AND
current task/delegated authority grants the action
AND
applicable governance and project/repository policy allow it
```

Generic governance defines what can be expressed; it does not silently loosen
a stricter project or repository policy. For example, support for bounded
topic-publication authority does not override a repository rule that still
requires fresh approval for every push.

Authority is evaluated at the time of action. A later authoritative
instruction may narrow or revoke an earlier grant immediately. A stale task
packet or earlier `push_topic: true` value must not be used to continue an
action after the task authority has said to stop, not publish, or otherwise
reduced scope. Delegated authority can never exceed the issuer's current
authority.

`repository` is a stable cross-environment repository identity, for example
`<owner>/<repo>`. A machine-local checkout path is not the
repository identity and belongs only to execution metadata.

`governance_source` identifies the authoritative governance repository and
`governance_revision` identifies the exact revision within that source. For
Hami governance the expected source is the `governance_source` recorded in the
workspace map (`workspace.yaml`) unless an authoritative task or project policy
explicitly names a different approved source. Source and revision are bound
together before the first mutation.

The revision is resolved by authority, not by whichever revision is most
convenient to the working agent.

Resolution precedence is:

1. an exact governance revision explicitly supplied by the task authority;
2. an approved project-pinned governance revision, when the project defines
   one;
3. otherwise, the authoritative governance repository's canonical branch tip
   resolved once at task start and frozen as an exact SHA.

The working agent must not select an older governance revision merely to obtain
more permissive rules.

How the content is read is separate from how the revision is chosen. A
repository that carries a vendored copy under `.atc/governance/` reads that
copy, whose source and exact revision are pinned beside it in
`.atc/governance.yaml`; a session inside the governance checkout reads
`shared/governance/`; an executor with neither reads the same files from the
exact Git revision of the governance source. In every case, the policy content
used as evidence must correspond to the bound exact revision. A dirty or
advanced working tree must not be read while claiming that an older bound SHA
governs the task; either verify the relevant policy files are clean at that
revision or read the files directly from the exact Git object.

Local path discovery and canonical governance identity are separate concerns.

A topic branch may be supplied by the task authority at dispatch time or bound
by the selected execution adapter. It must be bound to one exact branch
**before the first task mutation**, and the bound branch is then authoritative
for the task and must be reported in delivery. If a pre-specified branch is
incompatible with the selected adapter, stop or select a different execution
environment; the adapter must not silently substitute a different branch.

### Reading the scope of a request

A request's primary framing sets its scope. When it opens with an evaluative
verb (review, assess, evaluate whether) and later adds a conditional
go-ahead ("if there is no conflict, just implement, no need to ask again"),
the evaluation is the task. The conditional clause covers small follow-on
fixes found during that evaluation, not a build that is large relative to the
ask, such as new files, commits and merges across several repositories. When
the action the clause would trigger is large relative to the apparent size of
the ask, confirm before crossing from assessment into implementation; asking
costs little, and the maximal reading can cost an unwanted commit.

When a change stores, hashes, or gates access with a credential or other
secret, obtain explicit sign-off on the concrete design (storage location,
algorithm and parameters, exact user flow, flags) before writing code or
opening a topic. A general direction chosen earlier does not cover those
specifics, and they are hard to unship once built.

### Binding moments

Canonical governance avoids a heavyweight task state machine, but the
following bindings must occur at clear moments:

```text
Before first mutation:
  repository
  governance_source + governance_revision
  base_sha
  scope
  effective authority
  bound branch
  writer ownership

After an authoritative commit exists:
  target_sha

After required delivery verification:
  evidence @ target_sha

After formal review:
  reviewed SHA is frozen

Immediately before integration:
  re-evaluate current main
  choose Fast-forward / Merge-commit / Stop
```

A later authoritative instruction may narrow scope or authority without
rewriting historical evidence; changes that alter the delivered revision
produce a new target SHA and invalidate evidence that no longer applies.

`execution_context` records the facts that materially affect execution. It is
descriptive, not a closed universal schema. Relevant dimensions include:

```text
git_topology
source_materialization
governance_resolver
available_capabilities
verification_capabilities
host_affinity
```

Examples of values include `independent`, `shared_common_dir`, or
`not_materialized` Git topology; `materialized_checkout`,
`repository_api_only`, or `change_set` source materialization; and
workspace vs. exact-revision policy resolution.

`git_topology = not_materialized` is valid when the executor can operate
through a repository API or change-set transport without possessing local Git
metadata. Such an executor does not participate in linked-worktree or local
Git-metadata lifecycle mechanics; it still follows branch binding,
publication-authority, delivery, and verification rules that apply to the task.

Machine-specific details such as:

```text
checkout_path
cwd
worktree_path
session_id
lock_reason
```

are execution metadata only and must not be required for a cross-environment
handoff unless the receiving executor actually needs them.

## Risk and reversibility govern autonomy

Use the minimum ceremony that produces evidence proportional to the risk of
being wrong. Low-risk, easily-reversible work should proceed autonomously
with lightweight checks. As risk rises, so should verification strength,
context isolation, review independence, rollback preparation, and how
explicit the required task authority's go-ahead needs to be.

**Risk is driven by impact severity, blast radius, and reversibility — not
file count or lines changed.** A one-line change to a schema migration is
higher risk than a 50-file mechanical rename; a leaked production credential
is high-impact even when the blast radius is small and the key can be
rotated. High-risk examples: data deletion or migration, actions that can
directly cause, authorize, or materially alter a real financial effect,
production mutation, credential or auth changes, a public API/schema
compatibility change, a cross-repo contract, or any action that can't be
cheaply rolled back. A read-only analysis that happens to be *about* money
(a backtest report, a balance query) is not itself high-risk merely because
the subject matter is financial.

**Prefer reversibility over confidence.** For a destructive or stateful
operation, don't compensate for risk by using a smarter model or a more
careful prompt — use an engineering control instead: dry-run, snapshot/
backup, an isolated execution environment or checkout with separately
bounded mutable state (a sandbox, an isolated worktree with no shared-store
link back into it — an isolated checkout is not the same as isolated
mutable state if something still links back to the shared store), a
bounded blast radius, then verify before finalizing the change. If rollback
can't be demonstrated, treat the action as high-risk regardless of how
confident the plan sounds.

## One-active-writer principle

A mutable topic branch has one active writer at a time. Writer transfer is explicit via `DELIVERY.md` and grants no extra publication/integration/deployment authority.

### Subagents are not writers

A subagent spawned by a session inherits that session's bound branch and
effective authority. It works in the parent's checkout or in an isolated
worktree the parent provisions, returns its result to the parent, and never
publishes, integrates or deploys regardless of the tools its harness exposes.
Its output is the parent's intermediate result, not a delivery; the parent
remains the only writer and the only party that delivers.

### Truth order

When sources disagree about the state of work: runtime evidence, then Git and
files, then frozen design or governance records, then handoff notes, then a
tool's private memory. A later source never overrides an earlier one.

## What "actually done" requires

Before reporting a task complete:

1. Every part of the **current authoritative task** has a corresponding,
   checkable output. The authoritative task is the latest explicit
   instruction from the task authority, plus still-valid earlier
   instructions, plus any approved referenced spec, plus applicable
   workspace/repo constraints — **a later explicit clarification supersedes
   a conflicting earlier instruction.** Re-derive this from the actual
   conversation/dispatch, not a paraphrase held in working memory — a long
   session often has "wait, don't do B" or "change A to A2" partway
   through.
2. The output has been verified under `VERIFICATION.md`, not asserted.
3. Any status/progress file this workflow uses has been updated.
4. Nothing was silently dropped — a part that couldn't be done gets stated
   explicitly, with why.

A completion report should distinguish verified from unverified claims when
that distinction actually matters — a normal small change just needs "done,
tests and typecheck pass." Reserve explicit VERIFIED / PARTIALLY_VERIFIED /
UNVERIFIED / BLOCKED labeling for reporting a change that has a real
unverified piece (e.g. "production OAuth callback: UNVERIFIED, credentials
unavailable in this environment") — don't turn it into a ritual every
response has to produce. Words like "should work," "probably," "in theory"
in a completion claim mean it isn't actually verified yet.

## Failure routing invariant

On failed/refused operations, unexpected state, missing capability, non-converging retries, or incomplete required verification, load `FAILURE_ESCALATION.md` before continuing.

# DELIVERY.md

Single primary owner of publication, cross-environment task delivery, and writer-transfer semantics. General authority remains in `CORE.md`.

## Delivery forms

Cross-environment handoff is not defined by `.agents/handoffs/`.

There are two intentionally different delivery modes.

### WIP Git-ref checkpoint (not a third delivery form)

An authorized non-final Git-ref handoff for unfinished work must be labeled
`WIP / not a Revision Delivery`, naming task, repository, fetchable bound
topic ref, exact checkpoint SHA, base SHA, verification state (possibly
UNVERIFIED), known issues and next action. At handoff, that ref's tip must
equal the reported SHA. Applicable project/repository pre-publication gates
remain mandatory, and publication requires effective `push_topic` authority.

WIP is not formal Revision Delivery, PASS-at-SHA, or integration approval.
An authorized non-force commit may later advance the mutable WIP ref;
the immutable old checkpoint SHA need not remain the ref's tip. The recipient
rechecks the remote ref on handoff and immediately before first mutation and
stops on drift or uncertain writer ownership. Old verification evidence does
not automatically validate a later revision. This is a checkpoint distinction
within existing transport, not a third delivery protocol or new authority.

### 8.1 Revision Delivery

Use this when an authoritative Git revision exists and the work is eligible for
revision-bound verification/review. A formal delivery must be explicitly
labeled `Revision Delivery`: from declaration the published-ref stability
rule below applies. A labeled `WIP / not a Revision Delivery` checkpoint is
not a Revision Delivery and is exempt from that freeze until declaration.

Required verification may be pending at delivery only where the authoritative
verification contract and `VERIFICATION.md` permit subsequent checking (e.g.,
an authoring executor without the required runtime using authorized CI/another
capable executor). The delivery record names each pending required check,
its intended authorized executor/context and current status. Applicable
project/repository pre-publication gates remain mandatory. Pending is not
PASS. After explicit integration authorization, required integration checks
must pass on the final integration revision before the mainline advances, under
`GIT_WORKFLOW.md` and `VERIFICATION.md`. Review approval alone does not authorize
integration or start a full regression.
Failed pending verification leaves that delivery unverified; a corrective
commit produces a new target SHA and requires a new Revision Delivery with
applicable verification/review.

```text
Task ID:
Repository:
Base SHA:
Branch:
Target SHA:

Scope completed:
Validation:
Known risks / unresolved items:
Authority used:
Next action:
```

For cross-environment Git review, identity is not enough: the recipient must
also be able to retrieve the target Git object. A Git-ref delivery therefore
must either name an authorized fetchable ref whose tip is exactly the target
SHA, or use another explicitly approved transport.

The initial migration path is a fetchable Git ref:

```text
Published ref: refs/heads/agent/codex/example
Target SHA:    abc123...
```

At delivery time, the published ref **must resolve exactly to the reported
target SHA**. A ref that merely contains the target SHA is not sufficient for
the initial Git-ref transport, because formal review and integration require
the reviewed SHA to equal the topic branch tip.

The published ref must remain at that delivered/reviewed tip until the
applicable review/integration decision completes. If the producer adds another
commit, that creates a new delivery with a new target SHA and requires review
of the new delta under the existing reviewed-SHA lifecycle.

Canonical governance does not design bundle/patch transport in Phase A; such
transports can be added later if a real workflow requires them.

### 8.2 Change-set Handoff

Use this when the authoring executor cannot or is not authorized to create an
authoritative repository revision.

```text
Task ID:
Repository:
Base/context:
Proposed changes:
Scope:
Known risks / unresolved items:
Authority limitation:
Next action:
```

If publication is not authorized, an authoring executor must not use a
different API or transport mechanism to advance a remote ref. It may return a
patch or precise proposed edits to an authorized executor instead.

A Change-set Handoff has **no authoritative `target_sha`** and cannot receive
formal `PASS @ <sha>`. A later authorized executor must materialize the
change, verify it as required, commit it, and then issue a Revision Delivery
with the exact target revision.

Optional:

```text
Target tree:
Execution environment:
```

`target_tree` is useful where tree equivalence or history cleanup needs
independent verification but is not universally mandatory.

The transport is intentionally unspecified.

The same packet may be carried through:

- task dispatch/result
- ChatGPT conversation
- remote-agent result
- pull request description
- issue or comment
- committed project artifact where the project explicitly chooses one

Governance defines the content, not the transport.

## 9. Session handoff remains separate

Existing:

```text
.agents/handoffs/
```

remains a session/context continuity mechanism.

It may remain local-only.

Its purpose is:

```text
session A -> session B
```

not:

```text
remote implementation -> Git integration
```

The handoff-reminder documentation should explicitly distinguish:

```text
Session Handoff
vs
Task Delivery Packet
```

This avoids forcing machine-local state into the remote collaboration
protocol.

The three related artifacts have distinct roles:

```text
Revision Delivery / Change-set Handoff
    = cross-environment task delivery forms

Evidence Packet
    = failure or escalation evidence carried with that task context

Session Handoff
    = conversation / context continuity
```

The Evidence Packet remains the richer failure/escalation payload defined by
`FAILURE_ESCALATION.md`; it is not a second competing delivery protocol.
When escalation crosses environments, the selected delivery form identifies
the task/Git work and the Evidence Packet adds the observed failure evidence.

## 10. Push authority

Publication requires current effective authority. That authority may be granted per action or as bounded prior `push_topic` authority in the task contract, unless stricter project/repository policy requires fresh approval.

Example:

```text
push_topic: true
integrate_main: false
push_main: false
deploy: false
```

This counts as prior explicit task authority only when the authorization is
bound to the exact repository, exact owned topic branch/ref, and authorized
publication destination. `push_topic: true` means non-force publication of that branch only.

Publication authority is semantic, not command-specific. Advancing a remote
ref through `git push`, GitHub file/content APIs, commit/ref APIs, or another
mechanism is still publication. `push_topic: false` forbids those equivalent
remote mutations; an agent must not route around a no-publish boundary by
choosing a different transport.

Delegated authority is bounded by the issuer's own authority. A supervising or
orchestrating agent cannot grant a subagent push, integration, main, or
deployment authority that the supervisor itself did not receive.

The agent does not repeatedly request approval for each permitted topic push
within those bounds.

This bounded grant applies only where no stricter repository/project policy
requires a fresh approval. A later authoritative restriction or revocation
takes precedence immediately.

This does not authorize:

- force-pushing any topic branch unless separately and explicitly authorized
- merging `main`
- pushing `main`
- production deployment

Those remain independently controlled.

This distinction is necessary for unattended or remote execution to function
without weakening integration authority.

### Unpublished commits are status, not an open item

Report ahead/behind counts as plain status ("ahead of origin by N"). Do not
present unpublished commits as an open item, a recommendation, or a prompt to
publish. Publish only when the task authority asks or the task contract
grants publication authority; local commits are not publication.

## Writer transfer

One mutable topic has one active writer. Reuse the existing Task Delivery
Packet on a recipient-readable, checkable surface (PR, issue/comment, or an
explicitly shared handoff conversation); do not require a PR or new registry.
A valid record names task, repository, branch/ref, base SHA, **exact observed
remote HEAD**, original `initial_writer` when known, previous writer,
**intended receiving writer**, verification/WIP status, outstanding issues
and next action. Historical `initial_writer` persists across transfers.
Chat-only evidence counts only if the receiver can actually retrieve and
check the same record and its source.

The previous writer explicitly releases ownership and stops mutating or
publishing on that branch. The receiving writer explicitly accepts, verifies
remote HEAD against the recorded SHA **immediately before its first
mutation**, and independently confirms its own effective authority. Transfer
grants no `push_topic`, force-push, `integrate_main`, `push_main` or
deployment permission. Hami Managed Worktree contract 1 keeps its local
tool-owner/session/lock identity; a local worktree lock is not a shared
cross-environment branch lock.

If no checkable record exists, the recipient is unnamed, previous writer
release is unconfirmed, ownership conflicts, HEAD has drifted, or recipient
authority is unclear: **STOP and leave the branch unchanged**. Only the
task authority may explicitly release/take over a stale, unreachable or
dead previous writer, naming repository, branch, new writer, expected remote
SHA and scope. Do not infer release from inactivity, matching commit author,
existing branch, absent local lock or ignored local `.agents/handoffs/`.
A different new branch requires separate authority and must not usurp the
blocked branch. A non-force Git update alone cannot prove writer ownership.

# VERIFICATION.md

## Verification semantics

Canonical governance is language-, framework-, and CI-provider-neutral.

It defines these invariants:

1. required verification is resolved from the current authoritative task,
   approved project specifications, repository policy, and applicable required
   CI/gate contracts using the same authority precedence as
   `CORE.md`;
2. the producing agent may execute and report required checks but must not
   silently weaken, omit, or reclassify them; changing a required verification
   gate is a policy/requirement change and needs the corresponding authority;
3. every **formal delivery-verification result** names the exact revision it
   checked;
4. the executor must have the capability required by that verification;
5. evidence is checkable and reproducible enough for the risk involved;
6. changing the delivered revision invalidates evidence that no longer applies;
7. required verification may be performed by a different executor from the one
   that authored the change;
8. a verification requirement does not grant authority for destructive,
   stateful, host, credential, production, or other separately governed side
   effects needed to perform that verification.

Development feedback and formal verification are deliberately distinct:

```text
Development feedback
    -> may run on a dirty or pre-commit tree
    -> shortens the edit/fix loop
    -> is useful evidence of progress
    -> cannot by itself support PASS @ <target_sha>

Delivery verification
    -> runs against the exact delivered revision
    -> is recorded as evidence @ target_sha
    -> may run in any capable authorized executor
    -> supports formal review
```

Therefore authoring and verification may be split:

```text
Authoring executor
        |
 development feedback
        |
    target SHA
        |
  +-----+----------------------+
  |                            |
project-local gate        CI / host verification
  |                            |
  +-------------+--------------+
                |
        evidence @ target SHA
                |
          formal review
```

A materialized development checkout should use project-defined development
checks when available because they provide fast feedback. A project may also
define a pre-publication gate. An authoring executor without an executable
runtime may rely on another executor, including CI, for formal executable
verification after an authorized publication makes the exact target revision
available.

The universal governance does not prescribe commands such as `pytest`,
`gradle test`, `npm test`, `xcodebuild`, or any specific hook. Those are
project- or ecosystem-level choices.

A project's authoritative verification contract defines what "verified" means
for that repository or change class. "Project-defined" means defined by
authoritative project constraints, not chosen ad hoc by the current producing
agent. For example:

```text
repository_tests
repository_pre_publication_gate
target_platform_checks
host_bound_acceptance
```

The project then maps those requirements to its actual commands, frameworks,
platforms, and runners.

Formal delivery evidence should use a minimal, checkable envelope:

```text
check:
revision:
executor/context:
result:
evidence_ref:
```

The envelope does not require a central evidence service or database.
`evidence_ref` may be a CI run, command output, artifact, review record, or
other project-appropriate checkable source.

Language/ecosystem profiles may provide reusable recommendations but are not
canonical requirements unless a project explicitly adopts them through its
authoritative project policy/specification. Profile adoption or removal is a
project-policy decision, not a per-task convenience choice by the producing
agent. A Python verification profile may be added as follow-up guidance for
topics such as locked environments, targeted tests, full suites, and
Python-specific tooling; it must not make Python assumptions part of the canonical governance core.

### Preparing final self-verification

Before final self-verification for review, complete the mainline check and any
necessary draft-history updates under `GIT_WORKFLOW.md`. Run impact-selected
checks on the finalized tip and record formal evidence against that revision.
The mainline check does not itself trigger full regression. Full regression
remains subject to the integration timing below.

### Documentation-only verification

Documentation-only changes use content review, reference checks, diff checks
and applicable static-contract checks instead of full regression, including
when integrating into the mainline. Task authority may explicitly require
additional verification for a particular change.

Classify the complete diff by behavior and consumers, rather than file suffix.
Prose and comments/docstrings qualify when they leave application/tool
execution, test/build/runtime inputs and configuration, schemas, fixtures,
dependencies and generated artifacts unchanged. Check any automation that
consumes the changed text; a change to a consumed test or runtime contract/input
follows impact-selected verification rather than this exception. Governance and
workflow prose still requires its applicable policy review, static validation
and activation authority. Record the classification and supporting checks in
the delivery evidence. Required CI and publication gates remain applicable.

### Full regression timing and evidence reuse

This section applies to full regression checks already required by a task or
project, subject to the documentation-only exception above. The project defines
the checks and the change classes they cover.
The default venue is the project's remote CI, started by manual dispatch of its
workflow. Local mainline integration and
publication run checks selected by change impact, including required domain and
mutation checks, unless the project requires a local full regression before
advancing the mainline. A local full regression is otherwise for diagnosis.
During development, debugging, commits, review, review fixes and handoff, run
checks selected by change impact, including required domain and mutation checks.
For documentation-only changes, check content, references and diffs. Reporting a
final tip or receiving review approval does not itself trigger full regression.
Record the CI run as the evidence reference; a project whose mainline can be
updated without a pull request or dispatch defines how its full regression is
still obtained.

When a project requires a local full regression before advancing the mainline:

- After task authority explicitly authorizes mainline integration and the final
  reviewed source tip is confirmed, identify the exact integration revision
  under `GIT_WORKFLOW.md`. The active repository integration slot runs the
  required full regression against its bound immutable integration SHA before
  advancing the mainline; failed checks block integration.
- For a fast-forward, verify the reviewed tip. For a merge commit, prepare and
  verify the integration candidate while leaving the mainline unchanged, then
  advance it to that exact verified commit. Candidate preparation follows the
  review, authority and history constraints in `GIT_WORKFLOW.md`.
- Recheck the expected mainline SHA and source tip within the active integration
  slot, and update to the verified full SHA under `GIT_WORKFLOW.md`. If either
  recorded ref changed, reassess integration, complete any necessary reconciliation
  and review, and verify the final integration revision. Apply the evidence
  reuse conditions below rather than repeating an unchanged check.
- Before starting, check existing results and active runs. Reuse successful
  evidence for the same clean revision, checks, dependency environment and
  relevant configuration; continue waiting for an equivalent active run.
  Evidence records the revision, clean-tree state, command or checks,
  environment and result location. An explicitly authorized squash follows
  the recorded same-tree evidence exception in `GIT_WORKFLOW.md`.
- Fixes or test-relevant environment/configuration changes invalidate affected
  evidence. Run impact-selected checks first, then complete full regression on
  the final revision at actual integration. Each new commit is not a reason to
  immediately repeat full regression. Task instructions such as "full regression
  on the final tip/before reporting" should distinguish integration delivery
  from an earlier verification point specified by task authority.
Follow explicit task-authority requests for earlier full regression,
project-defined additional gates, and required CI/publication gates.
Changing an existing project rule that requires earlier or broader checks
needs explicit approval from the responsible authority; apply this default
under the authority precedence in `CORE.md`.

### Testing

Tests are one means of verification. Full-suite venue and timing follow the
section above; this section covers the rest of the test workflow.

- While iterating, run the touched test files.
- Before a commit or push, run the project's impact-selected `not slow` tests
  (its pre-push gate, where it has one).

A project's local pre-push gate, where it has one, runs the impact-selected
`not slow` tests for the pushed paths. Unknown paths select the project's
default suite. The checkout must be clean and match the pushed commit, and a
selection or test failure blocks the push. The gate is local feedback: it does
not replace the project's remote CI, and passing it does not authorize
publication. Each project owns its path-to-test mapping in code, and clone setup
installs the hook once per clone.

Every new or substantively changed test has exactly one semantic category and,
optionally, the orthogonal `slow` attribute. A project records its legacy
unmarked tests in a baseline that only shrinks; a new unmarked definition fails
collection. Shrinking the baseline does not by itself widen the impact-selected gate.

Flaky or timing-sensitive tests:

- Before measuring or stress-testing one, state what the test is meant to prove.
  When the failing part is a wall-clock number unrelated to that (a retry
  timeout, a fixed sleep, a default I/O budget), widen it as a ceiling and keep
  every assertion unchanged.
- Fixed sleeps and timeouts in integration tests are ceilings that return as soon
  as the awaited state holds, not delays. Deadline arithmetic belongs in
  fake-clock contract tests; integration tests assert state and behaviour.
- When the test makes no claim about speed, do not change product code or chase
  helper latency.
- Time-box stress experiments: one targeted run with temporary instrumentation
  that is reverted and never committed, then decide.

Projects that adopt `hami-testkit` follow its contract in
`shared/python/hami-testkit/README.md`, read from the same governance revision.
The category meanings, marker rules and selection behavior are defined there, for
the testkit version the README names. A project's `AGENTS.md` and `README.md` do
not restate this section; they hold the project's own commands, path rules and
tool bindings.

### Windows verification

Windows is one executor capability, not a separate governance model.

Use a reproducible Windows executor for portable checks. A hosted CI runner is
one example. Use a controlled host-bound Windows executor when verification
depends on machine-specific state such as installed runtime state, Credential
Manager, Task Scheduler, tray/native process inventory, or other host-specific
integration.

A self-hosted runner's technical ability to mutate that host does not grant
production authority:

```text
capability != authority
```

Production cutover, credential mutation, process replacement, task mutation,
or other production effects remain separately authorized actions.

Host-bound verification also has a trust boundary: permission to run a required
check does not automatically authorize arbitrary unreviewed topic code to
execute with privileged host credentials, secrets, scheduler access, or
production connectivity. Runner privilege, secret exposure, and allowed
commands must be constrained by the project/host policy appropriate to that
environment.


## Verification commands are data

A verification step is described as an executable plus a literal argument
list, run without a shell. Shell operators, substitutions, redirections and
environment assignments are not part of an execution contract; a descriptor
that needs them is rewritten until it does not. Evidence names the command
that ran and the revision it ran against.

## Independent review

## Verification requires evidence, not confidence

**Correctness claims require evidence proportional to risk.** The producing
agent may verify its own work with checkable evidence — running tests, a
build/typecheck, a diff/patch review, a read-back of a generated artifact,
behavioral checks. What it may not do is treat its own confidence or
reasoning as that evidence. "I believe this is correct" is not verification
at any risk level — and neither is "I looked at it," on its own, without
something checkable behind it (a test result, a concrete diff, a
reproducible command's output).

Independent review (a fresh context that didn't produce the change,
evaluating without seeing the producing agent's reasoning trail) is one
verification *mechanism*, not verification itself — required when
checkable/behavioral checks can't settle an important question, or when
residual risk after those checks is still high. Add review ceremony only
when it materially increases evidence; a fresh-context review of a rename
that's already confirmed by a clean typecheck, passing tests, and a
zero-hit grep for the old symbol adds close to nothing. Universal
"every change gets a second agent" ceremony is itself a cost: delegation
carries fixed context, latency, and compute overhead regardless of task
size, so blanket review can cost more than the evidence it adds on small
changes. (A specific measured cost belongs in that harness's own benchmark
notes, not in this policy — this file keeps the conclusion, not the number.)

Independent review requires independent *context*, not necessarily a
different model or provider — a same-model fresh call that never saw the
implementer's framing can be genuinely independent; a cross-vendor call fed
the implementer's conclusion to "please confirm" is not.

A fresh-context reviewer's verdict is itself evidence to be checked, not a
conclusion to relay. Brief it with an explicit evidence-per-item format
(recomputed digest, exact test summary line, file and line for each claim),
reject a hand-back that lacks concrete values, spot-check a few of its
citations against the source before relaying, and report any citation that
turned out wrong. For source-fidelity checks (a moved or rewritten section
against its original) the author performs its own sentence-level diff and
discloses every addition; a reviewer's "substance identical" is a
cross-check only, because reviewers have missed added sentences. Whether a
reviewer's verdict counts as the final independent review is for the task
authority to decide.

## Completion-status reporting

Use VERIFIED / PARTIALLY_VERIFIED / UNVERIFIED / BLOCKED only when a meaningful verification gap exists.

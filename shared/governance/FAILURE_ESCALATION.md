# FAILURE_ESCALATION.md

Load on failure/refusal, unexpected state, unavailable required capability, incomplete required verification, non-converging retries, or PARTIALLY_VERIFIED / UNVERIFIED / BLOCKED status.

## Classify failure before retrying or escalating

A failure is: a check didn't pass, a build/test failed, output doesn't
match the report contract, or the result doesn't match the goal. Before
deciding what to do next, classify *why*:

- **Implementation feedback** — a test/check surfaced a normal bug in the
  just-written code. This is the ordinary agent loop, not a failure signal:
  the same agent fixes it and moves on. Don't count this toward any retry
  budget — **but only while it's actually converging.** Repeated
  implementation feedback that isn't shrinking the failure surface or
  materially improving the result (red → different red → different red,
  edit after edit, with no narrowing) is no longer ordinary implementation
  feedback; reclassify it as an assumption or strategy failure below rather
  than continuing to call each new red an isolated "normal" bug.
- **Execution environment** — wrong cwd/branch/worktree, stale build, wrong
  config/profile, missing dependency, a cached/stale artifact, editing a
  mirrored copy instead of the source, a workspace/branch-creation tool
  silently starting from a stale base instead of true current HEAD. Split by
  whether the cause is deterministic (repair it, then retry) or transient
  (rate limit, timeout, a flaky external call — bounded retry is fine even
  with nothing changed).
- **Assumption failure** — the mental model of how the pieces fit together
  is wrong (fixing A breaks B, fixing B breaks A; an error that doesn't
  change across several edits despite real changes). **Stop editing.**
  Produce a dependency explanation, a call-path trace, or a minimal
  reproduction showing the actual conflict before making another code
  change — don't keep patching until a test happens to go green.
- **Strategy failure** — the same approach, correctly executed, isn't
  converging. Needs a fresh context or a genuinely different approach, not
  a bigger model on the same plan.
- **Capability failure** — the task is understood correctly but is beyond
  what this tier can execute. Escalate to a stronger model/tier.
- **Authority or oracle failure** — the blocker is a missing decision, a
  destructive action needing permission, or the spec/tests contradicting
  each other. Never guess: first try to resolve it using the
  authoritative-task precedence defined below (an oracle conflict where one
  side is superseded by a later explicit instruction doesn't need
  escalation). Only escalate to the task authority when what's already
  available genuinely can't determine which oracle governs.

**Retry budget is bounded per hypothesis/strategy, not reset by switching
models.** Default: at most one retry of the same hypothesis, at most two
attempts on the same strategy, three total unresolved attempts before
stopping to reassess. A workflow may define a different budget where
justified, but **switching agents or models does not reset the count when
the underlying hypothesis or strategy hasn't changed** — four models each
getting "one free try" at the same wrong assumption is four failures, not
zero. Still failing after budget exhausted → stop and surface to the task
authority, don't keep iterating.

**Escalation carries an Evidence Packet, not a story.** Required when
handing off to a fresh context/strategy/tier/task authority — not for an
ordinary first failure that gets fixed locally. The packet is evidence, not
the previous agent's narrative: goal, constraints, changed files, commands run,
observed failures/test output, the relevant diff/patch (or a precise
changed-files summary when the full diff is unnecessarily large),
hypotheses tested and ruled out, unresolved questions, and execution
context (repo, cwd, branch, HEAD, worktree). A fresh recipient should be
able to work from what was observed, not from how the previous attempt
framed it.

**The strongest signal of all: the urge to relax a check to make it pass**
(skip a test, disable an assertion, loosen a lint/gate threshold). Never
weaken, skip, delete, or bypass a check merely because the implementation
fails it — that's a signal something upstream is broken, not a reason to
adjust the check. A check may be changed only with evidence independent of
the failing implementation itself that the check is incorrect, stale, or no
longer reflects the approved requirement (an authoritative spec, an API
contract, a documented prior decision, upstream documentation — not another
agent's opinion of the same code) — and that's a requirement change to
report explicitly, not a bug fix to make quietly.

One more wrong-direction signal on its own: exception-case handling that
keeps growing is a warning sign only when the added cases don't correspond
to real domain distinctions — a status/state machine legitimately having
many branches (e.g. pending/active/suspended/expired/under-review) isn't
the same failure as bolting on a third unrelated special case to force a
test green. Count of cases is a prompt to check, not a threshold to enforce.

Distinguish capability limitation from authority denial.

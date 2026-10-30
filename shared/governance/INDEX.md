# Hami Agent Git Governance — Index

Canonical router for Hami governance. It does not duplicate module policy.

Always read `CORE.md` first from this same exact governance revision.

`INDEX.md` is intentionally small. It is the canonical router, not another
policy monolith.

Routing is:

```text
Always:
  CORE.md

If the task authors, modifies, reviews or integrates Git revisions/history:
  GIT_WORKFLOW.md

If the task authors or reviews a deliverable change,
determines whether work is verified/complete,
produces/consumes formal verification,
adds or changes tests, runs a project's test gate,
or changes test classification or shared test tooling:
  VERIFICATION.md

If the task publishes a revision/ref,
hands work across execution contexts,
or transfers active writer ownership:
  DELIVERY.md

If an operation/check fails or is refused,
an unexpected state is encountered,
a required capability is unavailable,
required verification cannot be completed,
retries are not converging,
or status may be PARTIALLY_VERIFIED / UNVERIFIED / BLOCKED:
  FAILURE_ESCALATION.md

If the task changes canonical governance:
  GOVERNANCE_CHANGES.md

If the task defines or changes how a project's product locates its files,
settings or other projects at runtime, or makes product code read an
ATC artifact:
  PROJECT_RUNTIME.md
```

A task may load more than one module when multiple conditions apply.

**Same-revision rule:** every module used by one task comes from the same bound `governance_source + governance_revision`. Never mix modules from different governance revisions.

Read the minimum authoritative governance set required by the task. Do not preload unrelated modules defensively.

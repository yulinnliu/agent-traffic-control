# Observability console

**Decision.** No dashboard, event projection, viewer or status API here.

**Why.** A viewer is a projection of execution events and must never become
execution infrastructure. Once it exists it attracts mutation routes and a
second source of truth. The rules this layer owns are readable from git and
files without a renderer.

**Evidence.** `hami-opc` delivered a console in four audited phases,
including a public observer view, then froze it as observation-only and
later proposed retiring the surface. Documents:
`docs/history/OBSERVABILITY_PHASE_1_AUDIT.md` through `PHASE_4`,
`docs/specs/OPC_DSH_SURFACE_REDUCTION_2026-09-16.md`,
`docs/specs/DEVELOPMENT_CONSTITUTION.md` sections 13 and 14.

**Instead.** `git worktree list --porcelain`, the lock reasons, branch tips
and delivery records are the status view.

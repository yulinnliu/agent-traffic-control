# Distributed locks, owner registry, approval service

**Decision.** Writer ownership is recorded in git metadata and delivery
records. There is no lock service, no registry of owners, no approval service
and no shared memory framework.

**Why.** The model serves one owner delegating to several agents on machines
that owner controls. Git worktree locks with a structured reason are readable
by every tool without a daemon, and a delivery record names its authority.
A service would add a trust root and an availability dependency to solve a
coordination problem that explicit transfer already solves at this scale.

**Evidence.** `hami-workspace/docs/CROSS_SESSION_GOVERNANCE_CLOSURE.md`
recorded the decision while amending `DELIVERY.md` and `GIT_WORKFLOW.md`;
`docs/CROSS_AGENT_WORK_CONTINUITY_CLOSURE.md` kept only the handoff template.

**Instead.** `<owner>:<topic>:<session>` in the lock reason, one active
writer, explicit transfer under `DELIVERY.md`. Multi-human teams needing more
should layer it on top in their own tooling.

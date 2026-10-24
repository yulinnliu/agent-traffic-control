# Orchestrator and dispatch board

**Decision.** No task board, dispatcher, scheduler or agent-spawning logic
lives in this layer.

**Why.** The orchestrator is a replaceable component: a script, a harness
loop, a cron job or a human all satisfy the same contract. Everything it needs
from this layer is data it reads: governance, policies, the workspace map.
Putting dispatch state here would couple the rules to one execution topology,
and the predecessor's own constitution required that single-agent, multi-agent
and human execution remain interchangeable backends.

**Evidence.** `hami-opc` built a plan-only dispatcher, a board schema, a
lifecycle ledger with admission, claim, attempt and reconciliation events, and
crash recovery. The 2026-09-10 direction correction froze all new contract
surface above the execution-governance layer, and no project friction since
has called for reopening it. Documents: `docs/specs/ARCHITECTURE.md`,
`docs/specs/OPC_DIRECTION_CORRECTION_2026-09-11.md`,
`docs/specs/DEVELOPMENT_CONSTITUTION.md` sections 1, 2, 5 and 18.

**Instead.** Whatever dispatches work reads `shared/governance/` at a pinned
revision, binds branch and writer before the first mutation, and delivers
under `DELIVERY.md`. Its own state is its own concern.

# Subagents as independent writers

**Decision.** A subagent spawned by a session is not a writer. It inherits
the parent's bound branch and authority, works in the parent's checkout or in
an isolated worktree the parent provisions, hands its result back, and never
publishes, integrates or deploys.

**Why.** Subagents live inside one harness and one session: no durable
identity, no life after the parent ends, permissions inherited wholesale.
Granting them their own writer ownership would create concurrent writers that
no tool outside that harness can see. Delegated authority never exceeds the
issuer's, which already follows from `CORE.md`.

**Evidence.** Harnesses now offer per-subagent worktree isolation in their
own private locations; those worktrees carry no cross-tool identity and are
listed as legacy targets by the worktree tooling.

**Instead.** The rule lives in `CORE.md` under the one-active-writer
principle. A parent that fans out work gives each subagent a disjoint scope
and remains the only party that delivers.

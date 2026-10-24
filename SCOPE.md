# Scope

This repository holds the rules and contracts one owner uses to run several
coding agents across several repositories. It is maintained for that use; it
is published so others can take what fits.

## In scope

- **Authority.** Who may edit, commit, publish a topic, integrate, push the
  mainline, deploy. Hard deny, approval required and permission presets as
  data, generated into each harness's own configuration.
- **Writer ownership.** One active writer per topic branch, explicit transfer,
  and the worktree identity that makes ownership readable from git.
- **Handoff.** Revision delivery, change-set handoff, WIP checkpoints and
  session handoff notes, with what the receiver must recheck.
- **Verification semantics.** What counts as verified, how evidence binds to
  a SHA, and how a project classifies and selects its tests.
- **Failure routing.** What an agent does when an operation fails, state is
  unexpected or verification cannot complete.
- **Shared skills and their adapters**, with the validator that keeps them
  identical across tools and repositories.
- **Conformance fixtures** so the tools can be reimplemented in any language.

## Out of scope

- **Orchestration.** Dispatching tasks, scheduling, a task board, spawning
  agents, choosing models. The orchestrator is a replaceable component that
  consumes these rules; it does not live here.
- **Observability and consoles.** Dashboards, event streams, viewers.
- **Model and provider economics.** Routing by tier, quotas, spend caps,
  retries across providers.
- **A project's own engineering.** Build, CI, dependencies, test commands,
  code style. Each repository owns these; this layer never rewrites them.
- **Multi-human teams.** The authority model assumes one owner delegating to
  agents. Approval services, owner registries and distributed locks are
  recorded rejections, see `.out-of-scope/`.

## The bar for an issue

An issue stays open when it describes an observed failure from a real session
and fits the principles in `shared/governance/CORE.md`. A hypothetical
improvement, a configuration option for one person's workflow, or a change
that belongs in a single harness goes to a fork or that harness.

Complexity must earn its place. A proposal that adds an abstraction, a stage
or a state answers first: what real problem exists today, whether the harness
or deterministic tooling already solves it, whether it would still matter if
models became ten times better, and whether it can be removed later without
migration. Prefer deterministic verification over another agent in the loop,
and prefer deleting a mechanism that model progress has made unnecessary over
finding it a new reason to exist.

Issues that cannot be clarified in one round are labelled `needs-info` and
closed after fourteen days without a reply.

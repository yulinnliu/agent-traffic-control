---
name: hami-retro
description: Conduct a retrospective on an agent session - read the session record and suggest improvements to the agent's environment such as navigation pointers, missing automated checks, guidance rules, and information access. Use only when the user asks for a retro or retrospective.
invocation: user
---

# Session retrospective

Adapted from [mattpocock/skills](https://github.com/mattpocock/skills)
(MIT License, Copyright (c) 2026 Matt Pocock).

The user has asked for a **retrospective**. You are suggesting improvements to the coding agent's **environment** to improve future runs.

## Steps

1. Read the `hami-writing-for-agents` skill first (installed beside this one in the same skills directory): any doc, skill or guidance change you propose follows its rules.

2. Read the primary sources for the session the user specifies. This may mean searching through session logs on this machine. If the user doesn't specify a session, default to the current one.

3. Look for candidates for improvement in these categories.

- **Navigation**: how easy was it for the agent to find the right files? Are there hidden dependencies between files? Would a **navigation pointer** make it easier? _Use when_ the session took a long time to find a piece of information.
- **Automated checks**: are there automated checks that could catch errors the agent made? Read the repo's own check commands first (its lint/typecheck/test commands, its CI workflows, the workspace's `scripts/validate_workspace.py`, a project's testkit gate), so a check that already exists but sits unwired or silently broken is the finding, not a reinvention. A repo with no **guardrail** (no pre-commit hook and no CI job running its checks) is itself a finding: an un-linted repo is a standing missed opportunity, not a neutral default. _Use when_ the agent made a mistake an automated check could have caught, or the repo has no guardrail at all.
- **Guidance rules**: should a rule be added, removed or clarified in the repo's `AGENTS.md`, or — for workspace-wide rules — proposed against `shared/governance/`? Classify the miss first: a **mechanical** one (a fixed syntactic pattern, a banned API, a file-location rule) gets a deterministic check (a linter rule, a hook, a CI job), whichever the repo's existing guardrail makes cheapest; default to building the check over writing the rule. Reserve prose rules for genuine **judgement calls** no check could substitute for. A governance change follows its own approval flow: propose it, don't edit it here. _Use when_ a mistake slipped through that a rule or check should have caught.
- **Tool economy**: did the agent make expensive tool calls that could be streamlined? Is there any custom tooling that is particularly token-inefficient? _Use when_ the agent made an expensive tool call.
- **Information access**: look for opportunities to increase the agent's access to information. Teeing dev server logs, readonly access to third-party services. _Use when_ a crucial piece of information was not available to the agent.
- **Static file bloat** (always-loaded guidance size, no-op instructions, stale or oversized docs): hand these to the `hami-token-audit` skill rather than re-auditing here. This retro keeps only findings the session's own behaviour exposed.

4. Present these candidates to the user, in order of severity. Propose; apply nothing without confirmation.

---
name: hami-token-hygiene
description: Keep agent sessions cheap when the user asks about token use or when delegating noisy work such as pytest runs - cut round trips, cap command output, require terse subagent summaries, and explain cost honestly. Not a repo audit; file/guidance bloat checks belong to token-audit.
managed-by: agent-traffic-control
canonical: hami-token-hygiene
---

# Token hygiene

Canonical version — shared across every repo in this workspace regardless of
which agent tool is active. Applied when the user mentions token cost, "save
tokens", session length, asks whether something is expensive, or when noisy
work such as a pytest run is being delegated. Answer honestly and act cheaply.

## What actually costs tokens

- Fixed cost dominates: every turn re-reads the accumulated conversation; longer sessions cost more regardless of output size.
- Variable cost: tool output is read back in full; command results and long replies add proportional cost.
- Round trips multiply the fixed cost.

## Behaviour

- Fewer, denser turns: batch independent tool calls; do not re-run checks whose result was already reported and has not changed.
- Cap output: prefer `--oneline`, `--stat`, explicit `-n`/`head` limits, and path-scoped queries over full logs, whole-file dumps, or unbounded listings.
- Self-service when better: if a step is trivial and safe for the user to run themselves (for example one `git branch -D`), offer the exact command and let them decide instead of spending a turn on it.
- Starting a genuinely unrelated topic (different bug, different feature) is worth a fresh session rather than carrying old context forward — it costs tokens on every future turn without helping the new task. A long task with natural phase breaks is worth compacting/summarizing at the breakpoint instead, using whatever the active tool offers for that.
- Delegate noisy operations (test runs, log reads, doc fetches, broad greps) to a subagent so the raw output stays in its own isolated context and only a short summary returns — but the isolation only helps if the summary actually is short. A subagent/sub-session spawn also carries its own fixed bootstrap cost regardless of task size, so don't delegate a task that's cheaper as a single direct command anyway. When delegating, say explicitly that you want a terse summary back, not the full findings.
- When delegating a pytest run to a subagent, include this exact output contract in its prompt:
  - Run `uv run pytest -q 2>&1 | tail -n 20` and determine success from pytest's summary, not merely the pipeline exit code.
  - If green, reply only `passed=N failed=0`.
  - If failed, start with `passed=N failed=M`, then include only failed test node IDs, pytest short-summary lines, and the final relevant assertion/error lines. Keep the entire reply within 20 lines.
  - Never include dots/progress output, passed-test listings, collection chatter, warnings, or full tracebacks.
- Prefer referencing reusable blocks (specs, schemas, long instructions) by file path over pasting them inline — load on demand instead of sitting in every turn.
- Keep a skill scoped to the projects that actually need it rather than defaulting everything to global — a global skill's description line is re-sent in every session regardless of relevance.
- Keep replies short for cost questions; restate only what changes the user's next decision.
- If asked mid-session, name the real lever (start a fresh session or batch requests) rather than polishing output size alone.

## Boundary

Repo hygiene - oversized always-loaded files, stale markdown, logs/build artifacts - is token-audit's job; do not duplicate its checklist here.

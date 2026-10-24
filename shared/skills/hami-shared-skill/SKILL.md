---
name: hami-shared-skill
description: Create a new skill shared across workspace projects or agent tools from one canonical source that sync renders into each tool's skills directory. Use for "make a shared skill" or a skill drafted for one project that would help another. Exclude skills scoped to one repo and one tool; place those in that repo's .claude/skills/ or .agents/skills/.
adoption: baseline
---

# Creating a shared skill

Agent Traffic Control keeps one **canonical** copy of every shared skill and
renders a **managed adapter** from it into each tool directory
(`.claude/skills/`, `.agents/skills/`, `.dsh/skills/`) of each repository
that uses it. The adapter is a complete copy, not a pointer: a repository
that carries it needs nothing else, no environment variable and no other
checkout. Drift is impossible to hide because sync re-renders and the
validator compares every copy with its canonical.

## Step 0 — does this actually need to be shared?

Skip everything below if the skill is scoped to one repo and one tool.
Write it directly in that repo's own `.claude/skills/<name>/SKILL.md`.

If the skill is useful in any project rather than tied to this workspace's
projects, it may belong in the user's global `~/.claude/skills/` (and
`~/.agents/skills/` for Codex). Ask if unclear. Never install the same
skill both globally and in a repository: Codex does not merge same-name
user and repository skills, so both appear in discovery.

## Step 1 — write the canonical SKILL.md

Location: `shared/skills/<name>/SKILL.md` in the Agent Traffic Control
checkout. Ask the user where that checkout is; never guess a path.

Write it **tool-agnostic** and **location-independent**:

- No Claude-Code-only or Codex-only features, slash commands or exact tool
  names. Phrase tool-specific behaviour as "whatever the active tool offers
  for X", or move it to an overlay (Step 2).
- Never reference the checkout by path or environment variable. The copy
  runs inside repositories that have neither. Refer to a sibling skill by
  name ("the `hami-writing-for-agents` skill, installed beside this one")
  and to a tool by its command on PATH (`atc ...`). The validator rejects a
  canonical skill that expands the `ATC_ROOT` variable.

For how to write the content and the description, read the
`hami-writing-for-agents` skill (and its `SKILL-MECHANICS.md` for the
invocation choice).

Frontmatter:

| Key | Meaning |
| --- | --- |
| `name` | kebab-case, equal to the directory name |
| `description` | the auto-invocation contract, one plain line; copied verbatim into every adapter |
| `adoption: baseline` | optional; new-project onboarding installs the skill by default |
| `invocation: user` | optional; the Claude Code adapter gets `disable-model-invocation: true` |
| `contract: N` | optional; the skill binds the calling tool's identity, so a DSH adapter is rendered too and every adapter carries `requires-contract: N` |

## Step 2 — per-tool overlays, only when behaviour really differs

When one tool needs different constants (an owner token, a session
variable, a shell dialect), put them in
`shared/skills/<name>/adapters/<tool>.md` with `<tool>` one of `claude`,
`codex`, `dsh`. Sync appends that file after the canonical body in the
matching adapter. Keep an overlay to constants and mechanics; shared rules
stay in the canonical body so they exist once.

## Step 3 — render, never hand-write adapters

Adapters are generated. From the checkout:

```
atc onboard --sync --root-only          # this checkout's own .claude/ .agents/ .dsh/
atc onboard --sync <project>            # one registered project
atc bootstrap sync --prune              # every registered project, then validate
scripts/link-skills.sh <repo> <name>    # a repository outside the workspace map, no Python
```

Each rendered adapter carries `managed-by: agent-traffic-control` and
`canonical: <name>`. Those fields are the mutation boundary: sync rewrites a
file that has them and never touches a same-named skill that does not. There
is no per-file format version; a managed file is current when it equals the
render, otherwise sync replaces it.

## Step 4 — guard against drift

`atc validate` (CI runs it on every push) compares each adapter with the
render of its canonical, rejects orphaned adapters and canonical skills
that point back at the checkout. `atc validate --projects` does the same
for every registered project.

## Step 5 — git

Canonical skills and overlays live in the Agent Traffic Control repository.
Rendered adapters live inside each repository that carries them and are
ordinary tracked files; commit them there with the user's confirmation on
scope.

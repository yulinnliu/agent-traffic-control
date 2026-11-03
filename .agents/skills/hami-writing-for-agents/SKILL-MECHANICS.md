# Skill mechanics

The skill-specific branch of [`hami-writing-for-agents`](SKILL.md): what changes when the document is a skill (frontmatter, the invocation choice, router skills, and this workspace's canonical/adapter mechanics). Everything else about writing it is the universal reference in `SKILL.md`.

## Invocation

Two choices, trading the two loads:

- A **model-invoked** skill keeps a `description`, so the agent can fire it autonomously, and other skills can reach it. You can still type its name: model-invocation always _includes_ user reach; a description only ever adds agent discovery, never removes the human's. The description is the skill's top-level context pointer, forced to stay loaded at all times: permanent context load in exchange for discoverability. A model-invoked skill whose content is all reference is also one home for shared reference: another skill can invoke it, so reference needed by several skills lives in one place. Mechanics: write a model-facing description carrying the trigger branches (the pointer-writing rules in `SKILL.md` apply in full), and leave model invocation enabled.
- A **user-invoked** skill strips the description from the agent's reach: only the human typing its name can invoke it, and no other skill can. Zero context load, but it spends cognitive load: you are the index that must remember it exists. Mechanics: set whatever flag the active tool offers to disable model invocation (Claude Code: `disable-model-invocation: true`; Codex: `policy.allow_implicit_invocation: false` in its agent config); the `description` becomes human-facing: a one-line summary, trigger lists stripped. A skill that must stay user-invoked in every tool only works where each tool the workspace uses has such a flag; where one doesn't, the skill is effectively model-invoked there, so write the description to model-facing standard anyway.

Pick model-invocation only when the agent must reach the skill on its own, or another skill must. If it only ever fires by hand, make it user-invoked and pay no context load.

Shared reference that two user-invoked skills both need can live in neither: with no descriptions, neither can fire the other. Push it to a plain file outside the skill system: external reference any skill can point at.

## Splitting by invocation

The invocation cut of splitting (the sequence cut lives in `SKILL.md`): split off a model-invoked skill when you have a distinct leading word that should trigger it on its own (a trigger word you actually use in your prompts), or another skill must reach it. You pay context load for the new always-loaded description, so that independent reach has to be worth it.

## Router skills

When user-invoked skills multiply past what you can remember, that piled-up cognitive load is cured by a **router skill**: one user-invoked skill that names the others and when to reach for each, so the human has one skill to remember instead of many. It can only hint, never fire them: user-invoked skills have no description, so nothing but the human can reach them.

## This workspace's mechanics

A skill shared across workspace projects or agent tools follows the canonical-source + rendered-copy pattern: placement, frontmatter fields, per-tool overlays, drift validation and git tracking are defined in the `hami-shared-skill` skill, and this file does not restate them. Two consequences for the writing:

- The description is duplicated verbatim into every adapter, so a description edit is a multi-file sync (`scripts/validate_workspace.py` catches drift). Budget pointer rewording accordingly: get the branches right before stamping adapters.
- The canonical body is read by every tool, so keep it tool-agnostic per `hami-shared-skill` Step 1; tool-specific mechanics belong in conditional phrasing or in the adapter when behavior genuinely differs.

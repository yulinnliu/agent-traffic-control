# Agent Traffic Control

Who is cleared to write, one writer per branch, and clean handoffs between
sessions. For one owner running several coding agents across several
repositories. Not a gateway, not an orchestrator.

[繁體中文](README.zh-TW.md)

## Why

Running Claude Code, Codex and your own tooling against the same repositories
fails in ways no single harness fixes:

- **Two sessions write the same branch.** Each one is right about its own
  diff and wrong about the branch.
- **A session resumes from a stale SHA.** The handoff note said where things
  were; the branch has moved since.
- **"Done" in the summary, unpushed in git.** The next session trusts the
  prose instead of the repository.
- **A worktree one tool created, another tool cleans up.** Nothing recorded
  whose it was.
- **An agent pushes because it can.** Having the credential was mistaken for
  having the permission.

Each failure has one mechanism here, and every mechanism is anchored in git,
the one thing every agent and every language already has.

| Failure | Mechanism |
| --- | --- |
| Concurrent writers | One active writer per topic branch; writer transfer is explicit and grants no publication authority |
| Stale resumption | Every handoff names base SHA, target SHA and verification state; the receiver rechecks the ref before its first mutation |
| Prose outranks git | Truth order: runtime evidence, then git and files, then design records, then handoff notes, then tool memory |
| Orphan worktrees | Owner, topic and session live in the worktree's git lock reason; any tool can read who holds it |
| Capability mistaken for authority | Effective authority is the intersection of task grant, governance and project policy; hard deny, approval required and permission presets are data, generated into each harness's own config |

## What is here

| Path | Holds |
| --- | --- |
| `shared/governance/` | The rules every agent reads at a pinned revision: `CORE.md` plus modules routed by `INDEX.md` |
| `shared/policies/` | JSON Schemas and examples for hard deny, approval required, permission presets and the escalation gate |
| `shared/skills/` | Canonical skills shared across tools and repositories; `.claude/`, `.agents/` and `.dsh/` hold the managed copies rendered from them |
| `shared/templates/` | The policy block onboarding inserts into a project's `AGENTS.md` |
| `shared/conformance/` | Language-neutral fixtures every tool implementation must pass |
| `scripts/` | POSIX sh entry points that need no language runtime: install and check skill adapters |
| `adapters/python/` | The Python reference implementation of the tools |
| `workspace.yaml.example` | The machine-local map from project name to repository, worktree and state roots |

Contracts are files: Markdown rules, YAML policies, JSON Schemas, git
metadata. Tools are replaceable implementations of those contracts. Python is
the reference implementation, not a requirement.

## Installation

Prerequisites: git. The full workspace path also needs [uv](https://docs.astral.sh/uv/).

### Skills only, no Python

```sh
git clone https://github.com/yulinnliu/agent-traffic-control.git atc
atc/scripts/link-skills.sh path/to/your-repo     # every baseline skill
atc/scripts/link-skills.sh path/to/your-repo hami-worktree hami-writing-for-agents
```

`link-skills.sh` copies each skill into `.claude/skills/`, `.agents/skills/`
and `.dsh/skills/` as a complete managed copy, vendors the governance rules
into `.atc/governance/` with their source and revision pinned in
`.atc/governance.yaml`, adds the `CLAUDE.md` pointer when the repository has
none, and inserts the governance block at the top of `AGENTS.md`. The
repository then depends on nothing outside itself: no environment variable,
no path back to this checkout. Files the repository owns are reported and
left alone. `--link` symlinks instead of copying; `--prune` removes adapters
whose canonical skill no longer exists. On Windows run it from Git Bash.

Skills that run a tool (`hami-worktree`, the merge preflight) call the `atc`
command, so put it on PATH once per machine:

```sh
uv tool install -e atc/adapters/python
```

### Full workspace

```sh
git clone https://github.com/yulinnliu/agent-traffic-control.git atc
cd atc/adapters/python
uv run --locked python scripts/bootstrap.py init                        # workspace.yaml, projects/ worktrees/ state/
uv run --locked python scripts/bootstrap.py add my-project --repo <url> # clone, register, onboard, validate
uv run --locked python scripts/bootstrap.py status                      # doctor plus a dry onboarding check
```

`add` also takes `--skills hami-worktree,hami-writing-for-agents` to install
opt-in adapters beside the baseline ones, and accepts a checkout you already
placed at `projects/<name>` when `--repo` is omitted. This path adds the
worktree, preflight, policy and friction tools behind one command, `atc`
(`uv run atc --help`, or `uv tool install -e .` to have it on PATH); the
layout it expects is in `docs/LAYOUT.md`. `atc` finds the workspace from the
nearest `workspace.yaml` above the working directory or from the checkout it
was installed from; `ATC_ROOT` is an optional override, never a requirement.

### Keeping installed files current

Project skill files and `.atc/governance/` are rendered copies, so after
this repository changes they are re-rendered, never edited:

```sh
atc/scripts/check-skills.sh path/to/your-repo                           # current / drifted / orphaned / stale
atc/scripts/link-skills.sh --prune path/to/your-repo                    # repair
uv run atc bootstrap sync --prune                                       # every registered project, then validate
```

Every rendered file carries `managed-by: agent-traffic-control`; a file
without it is project-owned and never touched.

## Skills

Model-invoked unless marked user-invoked.

- **hami-worktree** creates, resumes, audits and cleans up per-topic worktrees whose identity lives in git.
- **hami-handoff-reminder** decides whether to hand off, compact or write a handoff note.
- **hami-retro** reviews a session record and proposes improvements.
- **hami-writing-for-agents** is the reference for writing skills, `AGENTS.md` and pointers.
- **hami-shared-skill** creates a shared skill and its rendered per-tool copies.
- **hami-token-audit** audits a repository for recurring context cost.
- **hami-token-hygiene** keeps a session cheap when delegating noisy work.
- **hami-offload-to-chatgpt** (user-invoked) prepares a self-contained research prompt for a web model.

## Scope

`SCOPE.md` says what belongs here and which issues are accepted.
`.out-of-scope/` records concepts that were tried or considered and rejected,
with the evidence. `GLOSSARY.md` defines the terms the rules use.

## Status

Release 0.1.0 (git tag `v0.1.0`; the Python adapter carries the same
version). Governance, policies, schemas, skills, conformance fixtures and
the Python reference implementation are in place. CI runs the policy
examples against their schemas, the shell installer, and the Python suite on
Linux and Windows. Files installed into a repository carry no format
version of their own: they are current when they equal what this release
renders, and sync replaces them otherwise. `atc enforce` renders hard denies
per access mode, refuses unresolvable tokens and, with `--check`, compares
installed copies too; from `workspace.yaml` it also renders a machine-local
deny on each project's state root, kept out of `generated/enforcement/`.
`atc validate --projects` checks each project's
generated enforcement and, on `main`, that its governance pin is in
`main`'s history.

## License

MIT.

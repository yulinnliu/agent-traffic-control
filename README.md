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
| Unreviewed code reaching `main` | `atc integrate` fast-forwards `main` only to the exact SHA a reviewer passed, on the Owner's word for that SHA, bound by the task record in `.atc/tasks/`, after the integration verifier passes on the candidate |

## What is here

| Path | Holds |
| --- | --- |
| `shared/governance/` | The rules every agent reads at a pinned revision: `CORE.md` plus modules routed by `INDEX.md` |
| `shared/policies/` | JSON Schemas and examples for hard deny, approval required, permission presets and the escalation gate |
| `shared/skills/` | Canonical skills shared across tools and repositories; `.claude/`, `.agents/` and `.dsh/` hold the managed copies rendered from them |
| `shared/templates/` | The pointer line onboarding inserts into a project's `AGENTS.md` and the bootloader it writes to `.atc/README.md` |
| `shared/conformance/` | Language-neutral fixtures every tool implementation must pass |
| `scripts/` | POSIX sh entry points that need no language runtime: install and check skill adapters |
| `adapters/python/` | The Python reference implementation of the tools |
| `adapters/python/atc-testkit/` | The test kit behind `atc test` and `atc tmp`, which projects vendor as a wheel |
| `.atc/tasks/` | This repository's task records |
| `docs/` | How the tools work: `WORKFLOW.md` walks one task; `LAYOUT.md`, `TASKS.md`, `TESTKIT.md`, `ENFORCEMENT.md` and `ATC_HOME.md` are the references |
| `workspace.yaml.example` | The machine-local map from project name to repository, worktree and state roots |

Contracts are files: Markdown rules, YAML policies, JSON Schemas, git
metadata. Tools are replaceable implementations of those contracts. Python is
the reference implementation, not a requirement.

atc-testkit is the pytest side of the verification contract: test categories
and stages, an isolated environment per run, scratch copies of an exact
commit, and the skeletons of a project's pre-push gate and integration
verifier. `atc test` runs tests on a scratch copy and keeps the output in a
log; `atc tmp` makes, lists and deletes those scratches. A project vendors
the wheel this repository builds, so its CI needs nothing from this
checkout. `docs/TESTKIT.md` is the reference.

## Installation

Prerequisites: git. The full workspace path also needs [uv](https://docs.astral.sh/uv/).

### Skills only, no Python

```sh
git clone https://github.com/yulinnliu/agent-traffic-control.git atc
atc/scripts/link-skills.sh path/to/your-repo     # every baseline skill
atc/scripts/link-skills.sh path/to/your-repo atc-worktree hami-writing-for-agents
```

`link-skills.sh` copies each skill into `.claude/skills/`, `.agents/skills/`
and `.dsh/skills/` as a complete managed copy, vendors the governance rules
into `.atc/governance/` with the bootloader in `.atc/README.md` and their
source and revision in `.atc/atc.lock`, adds `CLAUDE.md` when the repository
has none, and inserts the pointer line before the first section of
`AGENTS.md`. It stops before writing anything when `AGENTS.md` has no
`## Integration verification` section or Git ignores a file it would write,
because every file it writes is committed. The repository then depends on
nothing outside itself: no environment variable,
no path back to this checkout. Files the repository owns are reported and
left alone. `--link` symlinks instead of copying; `--prune` removes adapters
whose canonical skill no longer exists. On Windows run it from Git Bash.

Skills that run a tool (`atc-worktree`, `atc-review`, `atc-coordinator`,
`atc-shared-skill`) call the `atc` command, so put it on PATH once per machine:

```sh
uv tool install -e atc/adapters/python
```

### Full workspace

```sh
git clone https://github.com/yulinnliu/agent-traffic-control.git atc
uv tool install -e atc/adapters/python
cd atc
atc bootstrap init                          # workspace.yaml, projects/ worktrees/ state/
atc bootstrap add my-project --url <url>    # clone, register, onboard, validate
atc bootstrap status                        # doctor plus a dry onboarding check
```

`add` also takes `--skills atc-worktree,hami-writing-for-agents` to install
opt-in adapters beside the baseline ones, and accepts a checkout you already
placed at `projects/<name>` when `--url` is omitted. The layout it expects is
in `docs/LAYOUT.md`, and `docs/WORKFLOW.md` walks one task from worktree to
push with the command of each step. `atc` finds the workspace from the
nearest `workspace.yaml` above the working directory or from the checkout it
was installed from; `ATC_ROOT` is an optional override, never a requirement.

### Keeping installed files current

Project skill files and `.atc/governance/` are rendered copies, so after
this repository changes they are re-rendered, never edited:

```sh
atc/scripts/check-skills.sh path/to/your-repo                           # current / drifted / orphaned / stale
atc/scripts/link-skills.sh --prune path/to/your-repo                    # repair
atc bootstrap sync --prune                                              # every registered project, committed on its main, then validate
```

Every rendered file carries `managed-by: agent-traffic-control`; a file
without it is project-owned and never touched.

## Commands

`atc <command> --help` gives the flags; `adapters/python/README.md` lists
them in one table.

| Command | Does | Reference |
| --- | --- | --- |
| `atc worktree` | Creates, resumes, audits and cleans up per-topic worktrees; `--project root` for this repository's own | skill `atc-worktree` |
| `atc task` | `new` writes a task record, `list` lists tasks, `check` checks records | `docs/TASKS.md` |
| `atc integrate` | Fast-forwards `main` to a reviewed SHA once the verifier passes; `--check` only resolves and preflights | `docs/WORKFLOW.md` § 6–7, governance `GIT_WORKFLOW.md` |
| `atc verify` | Runs the integration verifier on the candidate `atc integrate` would build, moving no ref, and records a PASS that `atc integrate` reuses | `docs/WORKFLOW.md` § 5 |
| `atc close-out` | Removes a task's worktree, scratches and reports | `docs/TASKS.md` § Close-out |
| `atc preflight` | `push` lists what `origin/main..main` would publish, by integration; reads only | `docs/WORKFLOW.md` § 8 |
| `atc test` | Runs tests on a scratch copy into a log; `mutants` checks guards; `private` runs a command on a private desktop | `docs/TESTKIT.md` § Commands |
| `atc tmp` | Exports a commit into a scratch with its environment; lists, cleans and prunes scratches | `docs/TESTKIT.md` § Commands, `docs/ATC_HOME.md` |
| `atc bootstrap` | `init`, `add`, `sync`, `status`: sets up a workspace and keeps registered projects current | § Installation |
| `atc onboard` | Checks or syncs one project's managed files; `--root-only` re-renders this repository's skill copies | `docs/LAYOUT.md` |
| `atc validate` | Checks skill copies, task records and this repository's contract; `--projects` checks registered projects | `docs/LAYOUT.md`, `docs/ENFORCEMENT.md` § Drift |
| `atc doctor` | Reports whether this machine is ready to run the tools (`READY`, `NOT_INITIALIZED`, `ERROR`), changing nothing | `atc doctor --help` |
| `atc policies` | `check` validates a policy directory against its schemas and refuses contradictions | `shared/policies/schemas/` |
| `atc enforce` | Generates each harness's deny configuration from `hard-deny.yaml`; `--check` reports drift | `docs/ENFORCEMENT.md` |
| `atc classify` | Rates a list of changed paths `LOW` or `HIGH` under an escalation policy; `HIGH` means an independent review first | `atc classify --help` |
| `atc friction` | Appends incidents and their resolutions to an append-only log under `state/workspace-friction/`; lists them and summarises their recurrence | `atc friction --help` |

## Skills

A skill that teaches an `atc` command is named `atc-<name>`; a skill about
agent working practice is named `hami-<name>`. Model-invoked unless marked
user-invoked.

- **atc-worktree** creates, resumes, audits and cleans up per-topic worktrees whose identity lives in git.
- **hami-handoff-reminder** decides whether to hand off, compact or write a handoff note.
- **atc-coordinator** (user-invoked) coordinates one task across a writer session and a reviewer session: opens the task, briefs both sessions, carries the Owner's integration word for an exact SHA, closes the task.
- **atc-review** runs writer/reviewer work across two sessions that cannot see each other, whatever carries the messages (a paste, a session message, a notes file): step reports, reviews, verdict blocks, the integration check.
- **hami-retro** reviews a session record and proposes improvements.
- **hami-writing-for-agents** is the reference for writing skills, `AGENTS.md` and pointers.
- **atc-shared-skill** creates a shared skill and its rendered per-tool copies.
- **hami-token-audit** audits a repository for recurring context cost.
- **hami-token-hygiene** keeps a session cheap when delegating noisy work.
- **hami-offload-to-chatgpt** (user-invoked) prepares a self-contained research prompt for a web model.

## Scope

`SCOPE.md` says what belongs here and which issues are accepted.
`.out-of-scope/` records concepts that were tried or considered and rejected,
with the evidence. `GLOSSARY.md` defines the terms the rules use.

## Status

Version 0.2.0, the version the Python adapter carries. Governance, policies, schemas, skills, conformance fixtures and
the Python reference implementation are in place. The `validate.yml` GitHub
Actions workflow runs only when dispatched by hand: it checks the policy
examples against their schemas and runs the shell installer and the full
Python suite on Linux and Windows.

## License

MIT.

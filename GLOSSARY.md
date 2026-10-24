# Glossary

Terms as the rules in `shared/governance/` use them. The air-traffic words
are a reading aid for humans; the rules themselves use the literal terms.

**Task authority.** The person or delegating process whose latest explicit
instruction defines the task. Authority is evaluated at the time of action; a
later instruction narrows or revokes an earlier grant immediately.

**Capability versus authority.** Having a credential or a tool is capability.
Being allowed to use it for this action is authority. An action needs both,
plus governance and project policy that permit it. In tower terms: a clearance.

**Effective authority.** The intersection of what the task grants, what
governance allows and what the project's own policy allows. The strictest
layer wins.

**Governance source and revision.** The repository holding the rules, and the
exact commit of it bound to a task before its first mutation. Resolved by
authority, never chosen for convenience, and never mixed across modules.

**Workspace map.** `workspace.yaml`: the machine-local file resolving a
logical project name to its repository, worktree and state directories.
The `atc` command finds it from the nearest parent directory or from the
checkout it was installed from; `ATC_ROOT` is an optional override. Project
repositories never need to know where it is.

**Persistent state.** A project's accumulated data under `state/`, produced
by running the project and often impossible to regenerate. Valuable data,
never a cache.

**Bound branch.** The one topic branch a task writes to, fixed before the
first mutation and reported in delivery.

**Writer ownership.** The right to mutate a bound branch. One active writer at
a time; transfer is explicit and carries no publication, integration or
deployment authority. One aircraft per runway.

**Worktree identity.** `<owner>+<topic>` as directory and branch name, plus
`<owner>:<topic>:<session>` as the git worktree lock reason. Any tool reads
it from git; a mismatch stops the resume.

**Owner.** The tool identity holding a worktree, for example `claude` or
`codex`. **Topic.** The short slug naming the work. **Session.** The
harness session that currently holds the worktree.

**Subagent.** An agent spawned by a session inside the same harness. Not a
writer: it inherits the parent's bound branch and authority, never publishes,
and its output is the parent's intermediate result, not a handoff.

**Handoff.** Work changing hands. *Session handoff* is a note for whichever
session resumes on the same machine; it is local and never committed.
*Delivery* is the governed form, see below. Git outranks any handoff
narrative.

**Revision Delivery.** A formal handoff of an authoritative git revision:
task, repository, base SHA, branch, target SHA, scope, validation, risks and
authority used. Once declared, the published ref is frozen.

**WIP checkpoint.** A labelled non-final git-ref handoff of unfinished work.
Not a Revision Delivery; the receiver rechecks the ref before touching it.

**Change-set handoff.** Delivery for an executor without a materialised
checkout, carrying the change as a set rather than a ref.

**FF, NO_FF, STOP.** The three integration verdicts: fast-forward, merge
commit, or stop because the mainline moved or conflicts appeared.

**Hard deny.** A capability, path or command an agent must be technically
unable to use. A preset can never grant it back; a conforming loader rejects
the attempt.

**Approval required.** An action that is allowed, but only after a human
says yes for this instance.

**Permission preset.** A named additive grant set over an implicit deny-all,
mapped by each harness adapter to its own mechanism.

**Escalation gate.** The rule classifying a delegated change LOW (ready for a
human fast-forward) or HIGH (independent review first) from the changed
paths and the first review's severity.

**Canonical skill.** The single source of a skill under `shared/skills/`,
with optional per-tool overlays in `adapters/<tool>.md` beside it.
**Adapter** (skill). The managed copy under `.claude/`, `.agents/` or `.dsh/`
rendered from the canonical skill: its name, description, body and this
tool's overlay, plus the ownership fields. It is complete on its own and
never points back at the checkout. **Adapter** (language). A tool
implementation such as `adapters/python/`.

**Vendored governance.** A repository's own copy of `shared/governance/`
under `.atc/governance/`, byte-equal to the pinned revision recorded in
`.atc/governance.yaml`. The copy is the project's governance pin.

**managed-by.** The frontmatter marker proving a file was installed by this
layer and may be re-synchronised. A file without it is project-owned and
never rewritten. The value is `agent-traffic-control`. There is no per-file
format version: a managed file is either byte-equal to the current render or
stale, and sync re-renders it.

**Baseline adoption.** A canonical skill every registered project must carry
adapters for.

**Conformance fixture.** A language-neutral test case under
`shared/conformance/` with inputs and expected results that any tool
implementation must satisfy.

**Friction.** A recurring workspace or governance failure that could affect
an unrelated future task, logged as evidence. Ordinary bugs are not friction.

**Truth order.** When sources disagree: runtime evidence, then git and files,
then frozen design records, then handoff notes, then a tool's private memory.

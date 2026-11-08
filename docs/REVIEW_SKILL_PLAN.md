# Review skill and coordinator skill plan

How and in what order the shared skill `hami-review-relay` becomes
`hami-review`, and a new shared skill `hami-coordinator` joins it. The
mechanics this plan builds on are those of `hami-shared-skill` (canonical
`shared/skills/<name>/SKILL.md`, `adapters/<tool>.md` overlays appended after
the body in the matching adapter, every other file of the canonical
directory copied verbatim beside the adapter, `adoption: baseline`) and of
`hami-writing-for-agents` with its `SKILL-MECHANICS.md`.

Steps 1 to 3 are one topic branch in the workspace root, reviewed by the
writer/reviewer method under the class behaviour (the change renders into
managed copies and edits a test), and integrated with `atc integrate`. The
rollout of § 4 follows that integration, one topic per registered project.

## Goal and scope

Two sessions share one task and never see each other. The protocol between
them stays as it is, rule for rule: what a step report carries, how a
reviewer reviews on a scratch copy, what a verdict block carries, and when a
PASS may carry an integration instruction. Two things change:

- The carrier of a message is neutral. A message travels by the user's
  paste, by a message from one session to another, or by a file in a notes
  folder; every message is still a cold read.
- A coordinator, a session that opens a task, starts a writer session and a
  reviewer session and holds the Owner's integration word, follows its own
  skill.

Out of scope: governance files under `shared/governance/`; the project
repositories beyond the rendered adapters of § 4, each synced in its own
round; the Agensi listing of the skills, a later follow-up.

## Final shape

Canonical sources in the workspace root:

```text
shared/skills/
├── hami-review/
│   ├── SKILL.md
│   ├── references/
│   │   └── mutants.md
│   └── adapters/
│       ├── claude.md
│       └── codex.md
└── hami-coordinator/
    ├── SKILL.md
    ├── references/
    │   └── briefs.md
    └── adapters/
        └── claude.md
```

Rendered adapters in each repository that carries the skills (the root, and
a registered project after its round of § 4):

```text
.claude/skills/hami-review/SKILL.md          body + adapters/claude.md
.claude/skills/hami-review/references/mutants.md
.agents/skills/hami-review/SKILL.md          body + adapters/codex.md
.agents/skills/hami-review/references/mutants.md
.claude/skills/hami-coordinator/SKILL.md     body + adapters/claude.md
.claude/skills/hami-coordinator/references/briefs.md
.agents/skills/hami-coordinator/SKILL.md     body
.agents/skills/hami-coordinator/references/briefs.md
```

The Claude Code adapter of `hami-coordinator` carries
`disable-model-invocation: true`, rendered from its `invocation: user`.
No `.claude/skills/hami-review-relay/` or `.agents/skills/hami-review-relay/`
remains anywhere.

## Decisions

- The skill is named `hami-review`.
- `hami-coordinator` is a separate skill, opt-in per project: it carries no
  `adoption` key.
- `hami-review` carries `adoption: baseline`.
- `hami-coordinator` carries `invocation: user` (`hami-shared-skill`
  Step 1, the frontmatter table): a coordinator is an orchestrator the Owner
  starts on purpose, never one the model triggers. `hami-review` stays
  model-invoked.
- A session a coordinator starts runs on Opus 5.5 or Sonnet 5.5, chosen per
  task; Fable only after the Owner agreed for that session.

## 1. `shared/skills/hami-review/` replaces `shared/skills/hami-review-relay/`

1. `git mv shared/skills/hami-review-relay shared/skills/hami-review`, then
   `SKILL.md` is edited as below.
2. Frontmatter:
   - `name: hami-review`.
   - `description:` one plain line: writer/reviewer protocol for work split
     across sessions that cannot see each other, whatever carries the
     messages (the user's paste, a session message, a notes file): the step
     report, the review on a scratch copy, the verdict block, the
     integration check. Excluded: reviewing your own uncommitted work, and
     reviewing a diff inside the same session that wrote it.
   - `adoption: baseline`. `atc onboard` renders a skill without it only
     where that skill's adapter file already exists
     (`adapters/python/scripts/onboard_project.py`, the `adoption` check in
     `build_plan`). After the rename no project holds a `hami-review`
     directory, so without `baseline` a project's next sync with `--prune`
     would remove the old adapter and create no new one.
3. Body edits, each a rephrase or a move; no rule is dropped:
   - Opening paragraph: a message travels by whatever carrier the setting
     provides, the user's paste, a message between sessions, or a file in a
     notes folder, and every message is a cold read. Three roles: the
     writer, the reviewer, and the carrier's holder, who is the user or a
     coordinator session following the `hami-coordinator` skill installed
     beside this one. "Writer" stays a permission boundary, not a component
     name.
   - "Every block you write": every bullet stays. The nested-code-fence
     rule is scoped to the paste carrier, where a pasted block closes on an
     inner fence. The zero-placeholder rule stays universal, because it
     concerns what the user runs. One bullet is added: a report or verdict
     travels whole in one message to its receiver; a notification that
     truncates (a short note or a preview) is a pointer, never the report.
   - "Reviewer: the verdict block": the sentence on the user pasting the
     instruction becomes an authority rule independent of who runs the
     command. The Owner's word on the exact reviewed SHA approves
     integration and default cleanup. The holder of that word (the user
     pasting the instruction into the writer, or a coordinator that has the
     Owner's word for that SHA) runs or forwards the instruction. When
     `main` moves after the word was given, the word is asked for again on
     the new SHA. The rest of the section stays: the `--check` rule, the
     `STOP CHECKOUT_NOT_ON_MAIN` case, the `--repo` form for the root and
     the cleanup commands.
   - "Writer: after integration" and "Reviewer: after integration" stay in
     substance.
   - "Reviewer: the review", step 4: the `atc test mutants` reference
     material (the `mutants.json` shape, `BASELINE-RED`, `KILLED`,
     `SURVIVED`, `INCONCLUSIVE`, `restored-ok`, `RESTORE-MISMATCH`, the
     `done:` lines) moves to `references/mutants.md`. Step 4 keeps the
     command line and one sentence pointing to `references/mutants.md`.
     `docs/TESTKIT.md` § Commands is the source of the mutant output
     contract and `references/mutants.md` restates it, so a change to that
     contract edits both in one topic; the implementation review compares
     the two.
   - The `atc test run` paragraph under "Writer: the step report" stays in
     the body; both roles use it.
   - The headings other documents cite by text stay: "Writer: the step
     report", "Reviewer: the review", "Reviewer: the verdict block",
     "Writer: after integration", "Reviewer: after integration".
4. `adapters/claude.md`, in the form of
   `shared/skills/hami-worktree/adapters/claude.md`, under the heading
   `## Claude Code constants`:
   - A session that starts another session for a role starts it fresh,
     without the inherited conversation, so the cold read holds: in its own
     worktree for a registered project, on a topic branch in the canonical
     checkout for the workspace root.
   - Model: Opus 5.5 or Sonnet 5.5, chosen per task (Sonnet for mechanical
     or well-briefed work, Opus for design-heavy writing or a review of a
     behaviour change); Fable only after the Owner agreed for that session.
   - The full step report or verdict goes by SendMessage to the receiving
     session, addressed by the exact name ListAgents shows. The harness's
     idle note carries only the first few hundred characters; it is a
     pointer, never the report.
   - A large hand-over (a brief revised repeatedly, a template) may go as a
     file in the side-session notes folder, the message naming its absolute
     path.
   - A session is archived once its topic is integrated and its scratches
     deleted; scratch deletion (`atc tmp clean`) runs only on the Owner's
     word.
5. `adapters/codex.md`, under the heading `## Codex constants`: no session
   messaging; the carrier is the user's paste or a notes file, so the
   nested-fence rule applies to every block, and the full report is written
   inline in the reply.

Verification: a rule-preservation check of the old `SKILL.md` at `main`
against the new body and `references/mutants.md`: every sentence has a
counterpart, the only exceptions being the paste-only wording the carrier
wording replaces, each listed in the step report.

## 2. New `shared/skills/hami-coordinator/`

A separate skill because its trigger is distinct (opening a task, assigning
a writer and a reviewer, "coordinator") and because a coordinator never runs
the review steps and keeps them out of its context
(`hami-writing-for-agents` `SKILL-MECHANICS.md` § Splitting by invocation;
`hami-writing-for-agents` `SKILL.md` § When to split, the cut by sequence).

Frontmatter: `name: hami-coordinator`; `description:` one plain line:
coordinating one task across a writer session and a reviewer session: open
the task record, start and brief both sessions, carry the Owner's
integration word for an exact SHA, close the task; not for doing the writing
or the review yourself, not for a single-session task. `invocation: user`,
so the skill starts only when the Owner names it. No `adoption` key.

`SKILL.md` body, tool-agnostic: it says "start a session" and "send a
message" and names no tool; Claude Code specifics live in the overlay. Its
steps, each ending on its done condition:

1. **Before opening.** Read the project's `AGENTS.md`, the task identity
   form it declares (governance `CORE.md` § Task identity), the task
   records, the branches, the worktrees (`atc worktree audit` where they are
   managed) and the titles of running sessions, so the new task's number
   and branch collide with nothing; another coordinator may be opening tasks
   in the same project. Done when the task's identity and branch name are
   chosen and shown free.
2. **Open the task record** in the project's form; its scope and authority
   basis are the writer's contract. Done when the record is written.
3. **Start the writer session** with a brief that is a cold read: project
   path, checkout rule (a managed worktree through the `hami-worktree`
   skill, or a topic branch in the canonical checkout), the task record, the
   spec or plan sections in scope, what done looks like, the reviewer's
   name, and that the step report, as `hami-review` "Writer: the step
   report" says, goes to the reviewer with a one-line note to the
   coordinator. The brief holds no placeholder for the writer to fill; a
   brief that would need one is not ready. Done when the session is started
   and the brief delivered.
4. **Start the reviewer session** on the step report, fresh: never the
   writer's session, never one that saw the writer's conversation. Its
   brief carries the project path, the reviewed tip SHA and base, the task
   record and the writer's name; it reviews as `hami-review` says and sends
   its verdict whole to the coordinator, and to the writer when the
   project's process has the writer act on it. Done when the verdict is
   received whole.
5. **Carry the Owner's word.** On a PASS that carries an integration
   instruction, run `atc integrate --check` with the instruction's
   arguments. On `CHECK FF` or `CHECK NO_FF`, or `STOP CHECKOUT_NOT_ON_MAIN`
   for a root checkout, give the Owner the reviewed SHA, the check line and
   the instruction, and ask for the word on that exact SHA; only the Owner
   gives it. When `main` has moved since, the writer rebases, the reviewer
   re-reviews the rebased tip, and the Owner is asked again on the new SHA.
   With the word, run the instruction, or forward it to the writer with the
   Owner's words quoted, and check the result as `hami-review` "Writer:
   after integration" says. Done when the Owner's word is recorded with its
   SHA and the result line matches.
6. **Close.** Confirm `main` at the integrated SHA, the branch or worktree
   cleaned up and the task record at its post-integration state; archive
   both sessions; delete the task's scratches only on the Owner's word;
   write the coordinator hand-over the project's handoff rules call for
   (`hami-handoff-reminder` `references/handoff-document.md` § The handoff
   document): one file, final state, what waits on the Owner. Done when
   each closure fact is confirmed and the hand-over written.

`references/briefs.md`: two templates, the writer brief and the reviewer
brief, each a complete skeleton with the fields of steps 3 and 4 in order
and a one-line note on what each field holds; no project-specific values.

`adapters/claude.md`, under the heading `## Claude Code constants`: sessions
start fresh, in their own worktree for a registered project; the model rule
of step 1's overlay; sessions are addressed by the name ListAgents shows;
the coordinator subscribes to the writer's idle notice where the harness
offers it instead of polling; sessions are archived through the session
management tools once the task is closed.

Verification: the review reads `SKILL.md` against this section step by step
and confirms no tool name in the body and no project-specific value in
`references/briefs.md`.

## 3. Render, citations, tests

1. The root's old adapter directories, `.claude/skills/hami-review-relay/`
   and `.agents/skills/hami-review-relay/`, are removed with `git rm -r`.
   `atc onboard --sync --root-only` renders the root's adapters from the
   canonical skills and carries no orphan pruning (`root_adapter_plan` in
   `adapters/python/scripts/onboard_project.py`); `--prune` acts on
   registered project targets only.
2. From `adapters/python`, `uv run atc onboard --sync --root-only`, then
   `uv run atc validate`. Expected: the `=== checkout-root adapters` block
   lists `create` entries for `hami-review` and `hami-coordinator` in
   `.claude/skills/` and `.agents/skills/` with their `references/` files,
   and no `collision` group, exit 0; validate prints
   `validate_workspace: all checks passed.`
3. `README.md` (the line that starts `- **hami-review-relay**`) and
   `README.zh-TW.md` (its counterpart): the line names `hami-review` and
   carries the carrier-neutral wording; one line each is added for
   `hami-coordinator`.
4. `adapters/python/tests/test_shell_install.py` names `hami-review-relay`
   as an existing canonical skill in several assertions; each names
   `hami-review` with its meaning kept. A search of the root tree, outside
   `projects/`, `worktrees/` and `state/`, finds every other live citation;
   `.agents/handoffs/` is local-only and outside the topic.
5. Verification of the topic: `git diff --check`; from `adapters/python`,
   `uv run --locked pytest tests/test_shell_install.py
   tests/test_onboard_prune.py tests/test_tooling.py` (the suite's
   `addopts` carries `-q`); the search of item 4 returns no live citation of
   `hami-review-relay`; the rule-preservation check of § 1.

## 4. Rollout

After the topic of §§ 1 to 3 integrates:

1. Per registered project, one sync topic through that project's own review
   and `atc integrate`: `atc onboard --sync <project> --prune` creates the
   `hami-review` adapters and removes the `hami-review-relay` ones.
   ria-project runs two coordinators; its round is scheduled with them.
2. `hami-coordinator` is seeded only in the projects the Owner names, in
   that project's sync topic: the root checkout's rendered adapter
   directories `.claude/skills/hami-coordinator/` and
   `.agents/skills/hami-coordinator/` (`SKILL.md` and
   `references/briefs.md`) are copied into the project at the same paths,
   then `atc onboard --sync <project>` rewrites the managed files to the
   current render (`build_plan` renders a skill without
   `adoption: baseline` wherever its adapter file exists). Verification:
   `atc onboard --check <project>` lists nothing pending, and
   `atc validate --projects` passes.
3. The reviewer session's memory and each repository's local
   `.agents/handoffs/CURRENT.md` name the new skills.

## Open decisions for the Owner

- Which projects opt in to `hami-coordinator` first; ria-project runs
  coordinator sessions.
- Whether the skill lists in `README.md` and `README.zh-TW.md` keep the
  `hami-` prefix wording.

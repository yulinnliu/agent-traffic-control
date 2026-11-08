# Review skill and coordinator skill plan

How and in what order the ATC tool skills take the `atc-` prefix: the shared
skill `hami-review-relay` becomes `atc-review`, a new shared skill
`atc-coordinator` joins it, and `hami-worktree` and `hami-shared-skill`
become `atc-worktree` and `atc-shared-skill`. The mechanics this plan builds
on are those of `hami-shared-skill` (canonical
`shared/skills/<name>/SKILL.md`, `adapters/<tool>.md` overlays appended after
the body in the matching adapter, every other file of the canonical
directory copied verbatim beside the adapter, `adoption: baseline`) and of
`hami-writing-for-agents` with its `SKILL-MECHANICS.md`.

Steps 1 to 4 are one topic branch in the workspace root, reviewed by the
writer/reviewer method under the class behaviour (the change renders into
managed copies and edits tests and a validation workflow), and integrated
with `atc integrate`. The rollout of § 5 follows that integration, one topic
per registered project.

## Goal and scope

Two sessions share one task and never see each other. The protocol between
them stays as it is, rule for rule: what a step report carries, how a
reviewer reviews on a scratch copy, what a verdict block carries, and when a
PASS may carry an integration instruction. Three things change:

- The carrier of a message is neutral. A message travels by the user's
  paste, by a message from one session to another, or by a file in a notes
  folder; every message is still a cold read.
- A coordinator, a session that opens a task, starts a writer session and a
  reviewer session and holds the Owner's integration word, follows its own
  skill.
- A skill that teaches an `atc` command carries the `atc-` prefix; a skill
  about agent working practice keeps `hami-`.

Out of scope: governance files under `shared/governance/` beyond the two
`hami-worktree` citations of § 3; the project repositories beyond the
rendered adapters and citations of § 5, each synced in its own round;
module, file and probe-file names inside `adapters/python/`
(`hami_worktree.py`, its tests, and the `hami-worktree-probe-` prefix of its
probe file); the Agensi listing of the skills, a later follow-up.

## Final shape

Canonical sources in the workspace root:

```text
shared/skills/
├── atc-review/
│   ├── SKILL.md
│   ├── references/
│   │   └── mutants.md
│   └── adapters/
│       ├── claude.md
│       └── codex.md
├── atc-coordinator/
│   ├── SKILL.md
│   ├── references/
│   │   └── briefs.md
│   └── adapters/
│       └── claude.md
├── atc-worktree/            (renamed from hami-worktree, content unchanged
│                             apart from citations)
└── atc-shared-skill/        (renamed from hami-shared-skill, content
                              unchanged apart from citations)
```

The skills `hami-handoff-reminder`, `hami-offload-to-chatgpt`, `hami-retro`,
`hami-token-audit`, `hami-token-hygiene` and `hami-writing-for-agents` keep
their names.

Rendered adapters in each repository that carries the skills (the root, and
a registered project after its round of § 5):

```text
.claude/skills/atc-review/SKILL.md          body + adapters/claude.md
.claude/skills/atc-review/references/mutants.md
.agents/skills/atc-review/SKILL.md          body + adapters/codex.md
.agents/skills/atc-review/references/mutants.md
.claude/skills/atc-coordinator/SKILL.md     body + adapters/claude.md
.claude/skills/atc-coordinator/references/briefs.md
.agents/skills/atc-coordinator/SKILL.md     body
.agents/skills/atc-coordinator/references/briefs.md
.claude/skills/atc-worktree/, .agents/skills/atc-worktree/, .dsh/skills/atc-worktree/
.claude/skills/atc-shared-skill/, .agents/skills/atc-shared-skill/
```

A `.dsh/skills/` copy exists only for a skill that declares `contract`
(`adapter_dirs` in `adapters/python/scripts/validate_workspace.py`), so
`atc-worktree` has one and `atc-shared-skill` does not.

The Claude Code adapter of `atc-coordinator` carries
`disable-model-invocation: true`, rendered from its `invocation: user`.
No adapter directory named `hami-review-relay`, `hami-worktree` or
`hami-shared-skill` remains anywhere.

## Decisions

- Prefix: a skill that teaches an `atc` command is named `atc-<name>`; a
  skill about agent working practice keeps `hami-<name>`.
- The review skill is named `atc-review`.
- `atc-coordinator` is a separate skill, opt-in per project: it carries no
  `adoption` key.
- `atc-review` carries `adoption: baseline`.
- `atc-coordinator` carries `invocation: user` (`atc-shared-skill`
  Step 1, the frontmatter table): a coordinator is an orchestrator the Owner
  starts on purpose, never one the model triggers. `atc-review` stays
  model-invoked.
- `atc-worktree` keeps the frontmatter of `hami-worktree` apart from `name`:
  no `adoption` key, `contract: 1`. `atc-shared-skill` keeps
  `adoption: baseline`.
- A session a coordinator starts runs on Opus 5.5 or Sonnet 5.5, chosen per
  task; Fable only after the Owner agreed for that session.

## 1. `shared/skills/atc-review/` replaces `shared/skills/hami-review-relay/`

1. `git mv shared/skills/hami-review-relay shared/skills/atc-review`, then
   `SKILL.md` is edited as below.
2. Frontmatter:
   - `name: atc-review`.
   - `description:` one plain line: writer/reviewer protocol for work split
     across sessions that cannot see each other, whatever carries the
     messages (the user's paste, a session message, a notes file): the step
     report, the review on a scratch copy, the verdict block, the
     integration check. Excluded: reviewing your own uncommitted work, and
     reviewing a diff inside the same session that wrote it.
   - `adoption: baseline`. `atc onboard` renders a skill without it only
     where that skill's adapter file already exists
     (`adapters/python/scripts/onboard_project.py`, the `adoption` check in
     `build_plan`). After the rename no project holds an `atc-review`
     directory, so without `baseline` a project's next sync with `--prune`
     would remove the old adapter and create no new one.
3. Body edits, each a rephrase or a move; no rule is dropped:
   - Opening paragraph: a message travels by whatever carrier the setting
     provides, the user's paste, a message between sessions, or a file in a
     notes folder, and every message is a cold read. Three roles: the
     writer, the reviewer, and the carrier's holder, who is the user or a
     coordinator session following the `atc-coordinator` skill installed
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
     `main` has moved after the word was given, the topic follows
     `GIT_WORKFLOW.md` § Integrate. An unpublished topic stops on a stop
     whose `next:` names a rebase (`REBASE_REQUIRED`, or `OVERLAP` or
     `CONFLICT`), is rebased onto `main` and integrates with
     `--reviewed <reviewed SHA> --rebased <rebased tip>`, which carries the
     review over when its check passes and otherwise stops as
     `CARRY_OVER_REFUSED`, needing a fresh review. A published topic
     integrates as `NO_FF` when it is compatible; on `CONFLICT` or
     `OVERLAP` it stops for a reconcile commit on the topic and a review of
     that commit. Either way the word is asked for again on the SHA being
     integrated: the rebased tip, or for `NO_FF` the reviewed SHA. The rest
     of the section stays: the
     `--check` rule, the `STOP CHECKOUT_NOT_ON_MAIN` case, the `--repo` form
     for the root and the cleanup commands.
   - "Writer: after integration" and "Reviewer: after integration" stay in
     substance.
   - "Reviewer: the review", step 4: the `atc test mutants` reference
     material (the `mutants.json` shape, `BASELINE-RED`, `KILLED`,
     `SURVIVED`, `INCONCLUSIVE`, `restored-ok`, `RESTORE-MISMATCH`, the
     `done:` lines) moves to `references/mutants.md`. Step 4 keeps the
     command line and one sentence pointing to `references/mutants.md`, and
     says that `atc test mutants` applies where the project runs its tests
     through `atc-testkit`; elsewhere the project's `AGENTS.md` names how a
     mutant run is done. `docs/TESTKIT.md` § Commands is the source of the
     mutant output contract and `references/mutants.md` restates it, so a
     change to that contract edits both in one topic; the implementation
     review compares the two.
   - The `atc test run` paragraph under "Writer: the step report" stays in
     the body; both roles use it. It opens by saying that it applies where
     the project runs its tests through `atc-testkit`, and that elsewhere
     the targeted test commands are the ones the project's `AGENTS.md`
     names.
   - The checkout rule of a session, wherever the body states one, cites
     the repository's `AGENTS.md` instead of restating it.
   - The headings other documents cite by text stay: "Writer: the step
     report", "Reviewer: the review", "Reviewer: the verdict block",
     "Writer: after integration", "Reviewer: after integration".
4. `adapters/claude.md`, in the form of
   `shared/skills/atc-worktree/adapters/claude.md`, under the heading
   `## Claude Code constants`:
   - A session that starts another session for a role starts it fresh,
     without the inherited conversation, so the cold read holds, in the
     checkout the repository's `AGENTS.md` prescribes: its own worktree for
     a registered project, and for the workspace root whatever the root's
     `AGENTS.md` § Changing this repo states.
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
wording replaces and the moved-`main` wording `GIT_WORKFLOW.md` § Integrate
now governs, each listed in the step report.

## 2. New `shared/skills/atc-coordinator/`

A separate skill because its trigger is distinct (opening a task, assigning
a writer and a reviewer, "coordinator") and because a coordinator never runs
the review steps and keeps them out of its context
(`hami-writing-for-agents` `SKILL-MECHANICS.md` § Splitting by invocation;
`hami-writing-for-agents` `SKILL.md` § When to split, the cut by sequence).

Frontmatter: `name: atc-coordinator`; `description:` one plain line:
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
   path, the checkout rule the project's `AGENTS.md` states (a managed
   worktree through the `atc-worktree` skill for a registered project), the
   task record, the spec or plan sections in scope, what done looks like,
   the reviewer's name, and that the step report, as `atc-review` "Writer:
   the step report" says, goes to the reviewer with a one-line note to the
   coordinator. The brief holds no placeholder for the writer to fill; a
   brief that would need one is not ready. Done when the session is started
   and the brief delivered.
4. **Start the reviewer session** on the step report, fresh: never the
   writer's session, never one that saw the writer's conversation. Its
   brief carries the project path, the reviewed tip SHA and base, the task
   record and the writer's name; it reviews as `atc-review` says and sends
   its verdict whole to the coordinator, and to the writer when the
   project's process has the writer act on it. Done when the verdict is
   received whole.
5. **Carry the Owner's word.** On a PASS that carries an integration
   instruction, run `atc integrate --check` with the instruction's
   arguments.
   - On `CHECK FF` or `CHECK NO_FF`, or `STOP CHECKOUT_NOT_ON_MAIN` for a
     root checkout, give the Owner the reviewed SHA, the check line and the
     instruction, and ask for the word on that exact SHA; only the Owner
     gives it.
   - On a `STOP` whose `next:` names a rebase (`REBASE_REQUIRED`, or
     `OVERLAP` or `CONFLICT` of an unpublished topic), have the writer
     rebase onto `main` and run `atc integrate --check --reviewed <reviewed
     SHA> --rebased <rebased tip>`. When the carry-over holds, ask the Owner
     for the word on the rebased tip, naming the carried-from SHA and the
     `main` commits in between; on `CARRY_OVER_REFUSED`, the reviewer
     re-reviews the rebased tip first.
   - On any other `STOP`, follow its `next:` line (`GIT_WORKFLOW.md`
     § Command results); a `next:` that names a re-review goes back to the
     reviewer before any word is asked for.
   With the word, run the instruction, or forward it to the writer with the
   Owner's words quoted, and check the result as `atc-review` "Writer:
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
start fresh, in the checkout the repository's `AGENTS.md` prescribes; the
model rule of § 1 item 4's overlay; sessions are addressed by the name
ListAgents shows; the coordinator subscribes to the writer's idle notice
where the harness offers it instead of polling; sessions are archived
through the session management tools once the task is closed.

Verification: the review reads `SKILL.md` against this section step by step
and confirms no tool name in the body and no project-specific value in
`references/briefs.md`.

## 3. `atc-worktree` and `atc-shared-skill`

1. `git mv shared/skills/hami-worktree shared/skills/atc-worktree` and
   `git mv shared/skills/hami-shared-skill shared/skills/atc-shared-skill`;
   in each `SKILL.md` only `name:` and the skill citations change. In
   `atc-worktree` those are the description's "through the shared
   hami-worktree CLI" and "lacks the `hami-worktree` capability"; its
   heading "Hami Managed Worktree" stays as the concept name.
2. Citations inside the workspace root, each renamed with its meaning kept:
   - the canonical skills that cite them (`atc-review` cites
     `atc-worktree`; `hami-writing-for-agents` `SKILL-MECHANICS.md` cites
     `atc-shared-skill`);
   - `adapters/python/scripts/hami_worktree.py`, the docstring line naming
     the canonical skill path;
   - `adapters/python/scripts/doctor.py`, the check label `hami-worktree`,
     which becomes `atc-worktree`;
   - `.github/workflows/validate.yml`, the shell-install job's
     `link-skills.sh` arguments and the `.claude/skills/hami-worktree/`
     paths;
   - `shared/governance/GIT_WORKFLOW.md`, the two citations of the
     `hami-worktree` capability (§ Integrate and § Cleanup trigger), a
     semantics-preserving correction for a renamed tool that
     `GOVERNANCE_CHANGES.md` § Editing canonical rules and policy lets
     proceed without waiting for activation authority; the concept name
     "Hami Managed Worktree" in `GIT_WORKFLOW.md` and `DELIVERY.md` stays.

Verification: a search of the root tree, outside `projects/`, `worktrees/`
and `state/`, finds no live citation of `hami-worktree` or
`hami-shared-skill` apart from the module, file and probe-file names of the
out of scope list and the concept name.

## 4. Render, citations, tests

1. The root's old adapter directories are removed with `git rm -r`:
   `hami-review-relay`, `hami-worktree` and `hami-shared-skill` under
   `.claude/skills/` and `.agents/skills/`, and `hami-worktree` under
   `.dsh/skills/`.
   `atc onboard --sync --root-only` renders the root's adapters from the
   canonical skills and carries no orphan pruning (`root_adapter_plan` in
   `adapters/python/scripts/onboard_project.py`); `--prune` acts on
   registered project targets only.
2. From `adapters/python`, `uv run atc onboard --sync --root-only`, then
   `uv run atc validate`. Expected: the `=== checkout-root adapters` block
   lists `create` entries for `atc-review`, `atc-coordinator`,
   `atc-worktree` and `atc-shared-skill` with their `references/` files,
   and no `collision` group, exit 0; validate prints
   `validate_workspace: all checks passed.`
3. `README.md` and `README.zh-TW.md`: the skill list names `atc-review`,
   `atc-worktree` and `atc-shared-skill` with the carrier-neutral wording
   for `atc-review`; one line each is added for `atc-coordinator`; a
   sentence states the prefix rule of Decisions. Outside the skill list,
   `hami-worktree` also appears in the `link-skills.sh` example, the
   "Skills that run a tool" sentence and the `--skills` example of each
   README; each names `atc-worktree`.
4. `adapters/python/tests/test_shell_install.py` and `test_tooling.py` name
   the old skills as existing canonical skills or as the doctor check label
   in several assertions; each names the new skill with its meaning kept.
   A search of the root tree, outside `projects/`, `worktrees/` and
   `state/`, finds every other live citation; `.agents/handoffs/` is
   local-only and outside the topic.
5. Verification of the topic: `git diff --check`; from `adapters/python`,
   `uv run --locked pytest tests/test_shell_install.py
   tests/test_onboard_prune.py tests/test_tooling.py
   tests/test_hami_worktree.py` (the last because the module's docstring
   changes; the suite's `addopts` carries `-q`); the
   searches of item 4 and § 3 return no live citation of the former names
   beyond the exceptions § 3 lists; the rule-preservation check of § 1.

## 5. Rollout

After the topic of §§ 1 to 4 integrates:

1. Per registered project, one sync topic through that project's own review
   and `atc integrate`: `atc onboard --sync <project> --prune` creates the
   `atc-review` and `atc-shared-skill` adapters and removes the
   `hami-review-relay`, `hami-worktree` and `hami-shared-skill` ones.
   ria-project runs two coordinators; its round is scheduled with them.
2. `atc-worktree` carries no `adoption` key, so a project that carried
   `hami-worktree` (hami-quant, hami-quant-trading, ria-project) is seeded
   in the same sync topic, before the sync: the root checkout's rendered
   `atc-worktree` adapter directories are copied into the project at the
   same paths, then the sync rewrites the managed files to the current
   render (`build_plan` renders a skill without `adoption: baseline`
   wherever its adapter file exists). The same topic renames the project's
   live citations of the former names in its documentation; task records
   and other history keep the name they were written with, and the
   vendored `.atc/governance/` changes only through its pin advancing.
   Until a project's round, a sync of that project runs without `--prune`,
   so `hami-worktree` is never pruned before `atc-worktree` is seeded.
3. `atc-coordinator` is seeded only in the projects the Owner names, in
   that project's sync topic, the same way as `atc-worktree`.
4. Verification per project: `atc onboard --check <project>` lists nothing
   pending, and `atc validate --projects` passes.
5. The reviewer session's memory and each repository's local
   `.agents/handoffs/CURRENT.md` name the new skills.

## Open decisions for the Owner

- Which projects opt in to `atc-coordinator` first; ria-project runs
  coordinator sessions.

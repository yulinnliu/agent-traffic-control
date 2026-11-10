# ATC layout plan

How and in what order a consuming repository's ATC files reach the layout
`docs/LAYOUT.md` § Inside a consuming repository describes: sync writes
`.atc/README.md`, `.atc/atc.lock` and `.atc/governance/` and owns them by
their path, `.atc/tasks/` stays the project's, `AGENTS.md` carries one pointer
line, `managed-by` stays only on rendered skill copies, every file sync writes
is committed, and `CLAUDE.md` imports `AGENTS.md`. The terms are those of
`GLOSSARY.md` (ATC lock, Bootloader, managed-by). The governance text of this
change (`CORE.md` § Task identity and the vendored copy, `GIT_WORKFLOW.md`
§ Lock advancement and § Governance sync commits, `PROJECT_RUNTIME.md`
§ Runtime never reads ATC) lands in the same topic, under the Owner's
authority for this topic.

Sections 1 to 7 are one topic branch in the workspace root, reviewed under
the writer/reviewer method (`atc-review`) as a behaviour change of onboard,
validate, `atc task new` and the shell installers, and integrated with
`atc integrate` once § Prerequisites hold. § 8 follows that integration.
Each section ends with its side effects and their mitigations; nothing is
implemented before the Owner accepts them.

## Prerequisites

1. **hami-quant.** Met. hami-quant `main` fc5a3a0 drops the t13 v3
   governance binding: nothing in hami-quant reads `.atc/governance.yaml` or
   its `managed-by` any more. The commit is integrated and not yet pushed.
   Its sync-managed `AGENTS.md` block still names `.atc/governance.yaml`
   until the migration sync of § 8 replaces that block with the pointer line.
2. **ria-project.** None. Its task records stay in `.atc/tasks/`, which
   remains the project's; `AGENTS.md` (the task binding rows) and
   `core/tests/unit/integration-verify.test.ts` keep their paths.

## 1. Templates

1. `shared/templates/ATC_README.md`, the bootloader sync writes to
   `.atc/README.md`. It states, in this order: sync writes `README.md`,
   `atc.lock` and `governance/` and none is edited by hand, `tasks/` holds
   the project's task records; the resolution order (an exact revision
   supplied by task authority; otherwise the `source` and `revision` of
   `.atc/atc.lock`, whose copy is `.atc/governance/`; otherwise stop); read
   `.atc/governance/INDEX.md` and `CORE.md` first, then only the modules
   `INDEX.md` routes to; the project's `AGENTS.md` may be stricter and stays
   authoritative for the project. The wording carries over from the current
   `PROJECT_AGENTS.md` block, each rule kept.
2. `shared/templates/PROJECT_AGENTS.md` becomes the single pointer line:

   ```markdown
   <!-- atc:governance -->This repository follows Agent Traffic Control governance: read `.atc/README.md` first.
   ```

3. `check_policy_template` in `validate_workspace.py` moves its content checks
   to `ATC_README.md` (the vendored index reference, the lock path, no
   hard-coded source, no machine-local path or environment variable) and
   checks `PROJECT_AGENTS.md` for the marker and the `.atc/README.md`
   reference.

Side effects:

- A session reads the resolution rules one file later, through the pointer.
  A session that skips the pointer misses them. Mitigation: § 6 makes
  Claude Code load `AGENTS.md` by import, and the pointer is the first line
  before any section, so it is read with the file's opening.
- Project `AGENTS.md` text after the pointer still governs the project, so
  a project rule that restates the old block's wording goes stale silently.
  Mitigation: the § 7 search runs over each project's `AGENTS.md` in § 8
  before its sync commit is pushed.

## 2. The lock

1. Format, written by onboard and `scripts/link-skills.sh`:

   ```toml
   version = 1
   source = "yulinnliu/agent-traffic-control"
   revision = "<40-hex ATC SHA>"
   ```

   Python reads it with `tomllib`; the shell scripts read `revision` with
   `sed -n 's/^revision = "\(.*\)"$/\1/p'`. A `version` other than `1` is a
   collision for onboard and a failure for validate.
2. Advancement: sync writes the ATC `HEAD` into `revision` whenever it
   changes any file it writes into the repository (the bootloader, the
   vendored governance, a rendered skill copy, the pointer line, `CLAUDE.md`)
   or when the recorded revision is not in the history of ATC `main`.
   § Lock advancement still holds back a revision that needs
   `## Integration verification`.
3. `GOVERNANCE_MANIFEST` and `pin_state` in `validate_workspace.py`, and every
   `pin` name in onboard, the shell scripts and their output lines, become
   `ATC_LOCK`, `lock_state` and `lock`.
4. `bound_governance` in `task_records.py`, which gives `atc task new` a
   project's `governance_source` and `governance_revision`, reads
   `.atc/atc.lock`. Until a project's migration sync, it reads
   `.atc/governance.yaml` when that file carries
   `managed-by: agent-traffic-control` and no lock exists; the fallback and
   its test leave in the topic that follows § 8 item 3.

Side effects:

- Advancing on a skill-only change moves `revision` in every sync that
  touches a project, so a project's recorded revision follows ATC more
  closely than the old pin did, which advanced on governance changes only.
  Records already written cite the revision they bound and do not change.
  Mitigation: none needed; the lock names what the synced files come from.
- A project's `governance_revision` in a new task record changes source file
  without changing meaning. Without item 4's fallback, `atc task new` refuses
  with `NO_GOVERNANCE_BINDING` in every project whose sync has not run yet,
  and § 8 runs per project on the Owner's word, possibly days apart.
  Mitigation: the fallback of item 4, removed once `atc validate --projects`
  passes everywhere.
- The output lines of onboard and the shell scripts rename `pin` to `lock`;
  a script or note matching the old words stops matching. Mitigation: the
  § 7 search covers the root tree, and no project script parses those lines
  (search of the four project repositories for the output words).

## 3. Ownership by path

1. Sync owns `.atc/README.md`, `.atc/atc.lock` and `.atc/governance/`.
   `.atc/tasks/` is the project's: sync never reads, writes or prunes it, and
   validate checks it only through the task-record check `docs/TASKS.md`
   specifies.
2. Validate reports any other file under `.atc/`.
3. Onboard treats such a file as a collision and writes nothing into `.atc/`,
   except the migration source of § 4.
4. `managed-by` is no longer read or written for anything under `.atc/`; it
   stays on rendered skill copies.

Side effects:

- A project tool that keeps its own file under `.atc/` (outside `tasks/`)
  stops every later sync of that project. A search of the four project
  repositories finds none besides `governance.yaml`. Mitigation: the
  collision names the path; the project moves it.
- A stray file in a vendored-governance directory is no longer protected
  by a missing marker: sync rewrites `.atc/governance/` as its own. A hand
  edit there is lost at the next sync, as the bootloader states.
- The verifiers of hami-quant and hami-quant-trading list `.atc/` among
  their automation Markdown prefixes, and ria-project's treats every path
  under `.atc/` as documentation-only. `atc.lock` takes the place of
  `governance.yaml`, also a non-Markdown file, so each project classifies a
  sync commit as it does today.

## 4. Migration

Sync, in onboard and in `scripts/link-skills.sh`, converts an existing layout
in one pass:

1. A `.atc/governance.yaml` carrying `managed-by: agent-traffic-control` is
   removed and `.atc/atc.lock` written; one without the marker stays a
   collision.
2. The block between `<!-- atc:project-policy-reference begin -->` and
   `<!-- atc:project-policy-reference end -->` in `AGENTS.md` is replaced by
   the pointer line at the same position. Text outside the markers is not
   touched. Without either marker, the pointer line goes before the first
   `## ` heading, as the block does today, with the same collision cases.
3. `.atc/README.md` is created.
4. `.atc/tasks/` is left as it is.
5. The sync commit carries the removal; `commit_sync` already owns paths of
   `update` items whose content is `None`.

Side effects:

- Project prose that cites `.atc/governance.yaml` goes stale: ria-project's
  `docs/DEVELOPMENT.md` and `docs/STATUS.md`. Sync does not render those
  files (`GIT_WORKFLOW.md` § Governance sync commits: a change sync does not
  render goes through a topic). Mitigation: each project's own session
  updates them in a topic after its migration sync; task records that cite
  the file keep the path they cite, which was true when written.
- The migration commit removes a file and rewrites `AGENTS.md` in one sync
  commit, so a project's reviewers see a larger sync diff once. Mitigation:
  the commit subject names the ATC revision and the migration; `atc preflight
  push` lists it before the push.
- An unmigrated project and a migrated one coexist through § 8, and
  `atc validate --projects` reports the unmigrated ones until then.
  Mitigation: § 8 item 3 is the finish line; § 2 item 4 keeps
  `atc task new` working meanwhile.

## 5. Every file sync writes committed

1. Before writing, sync checks each path it would write with
   `git check-ignore`; an ignored or excluded path stops the project with
   `IGNORED_OUTPUT` and the paths, nothing written. `.atc/tasks/` is not a
   path sync writes and is not checked.
2. Validate reports a file sync writes that exists but is not tracked.
3. `scripts/check-skills.sh` reports the same as `ignored`.

Side effects:

- my-heartopia-tools excludes `AGENTS.md`, `CLAUDE.md`, `.atc/`,
  `.agents/` and `.claude/` today, so every sync of it stops with
  `IGNORED_OUTPUT` from integration until § 8 item 2 completes, and
  `atc onboard --sync --all` reports it each time. Mitigation: the stop is
  per project, so `--all` continues with the others; § 8 item 2 ends it.
- The machine-local mode for a public repository ends. A repository that
  must not publish its agent files cannot adopt ATC. Mitigation: § 8 item
  2.1 is the Owner's check for heartopia, the only such repository.

## 6. `CLAUDE.md` imports `AGENTS.md`

1. The root `CLAUDE.md` becomes the import `@AGENTS.md` with one sentence
   saying the canonical guidance is that file. Claude Code expands the import
   at session start, so the guidance loads without depending on the model
   following prose.
2. Onboard replaces a project `CLAUDE.md` byte-equal to the previous root
   `CLAUDE.md`; any other existing `CLAUDE.md` stays the project's and is not
   touched, as today.
3. Verification: a fresh Claude Code session started in a synced project
   lists `AGENTS.md` among its loaded memory files (`/memory`).

Side effects:

- Every Claude Code session in the root and in each synced project loads
  `AGENTS.md` whole at start, and the bootloader only when the pointer is
  followed. The resident context grows by the size of `AGENTS.md` where a
  session used to read it anyway; a session that never opened it now pays
  for it. Mitigation: `AGENTS.md` files stay short by their own guidance;
  the cost is measured once in § 8 with the context hook's numbers.
- Codex and dsh read `AGENTS.md` natively; nothing changes for them.

## 7. Citations, tests

1. Citations: `README.md`, `README.zh-TW.md`, `adapters/python/README.md`,
   the `bootstrap.py` and `onboard_project.py` docstrings, the header
   comments of `link-skills.sh` and `check-skills.sh`, and the shell-install
   job of `.github/workflows/validate.yml` (its policy-block, `managed-by`
   and stale pin assertions become pointer-line, lock and stale lock
   assertions). A search of the root tree outside `projects/`, `worktrees/`
   and `state/` finds no live `governance.yaml`, `project-policy-reference`
   or `pin` naming the file, apart from the migration code of § 4, the
   fallback of § 2 item 4 and their tests.
2. Tests: `test_tooling.py`, `test_onboard_sync_commit.py`,
   `test_testkit_check.py`, `test_integration_verify.py` and
   `test_task_records.py` follow the new names; new tests cover the lock
   format and version check, lock advancement on a skill-only change, a
   stray file under `.atc/`, `.atc/tasks/` left untouched by sync and
   accepted by validate, the migration of § 4 from a project in today's
   layout with task records, `bound_governance` reading the lock and the
   legacy file, `IGNORED_OUTPUT`, and the `CLAUDE.md` replacement rule.
3. Verification of the topic: `git diff --check`; from `adapters/python`,
   `uv run --locked atc onboard --sync --root-only`,
   `uv run --locked atc validate`,
   `uv run --locked atc policies check ../../shared/policies/examples` and
   `uv run --locked pytest`; the CI shell-install job run locally with `sh`;
   the search of item 1.

Side effects:

- `docs/TASK_CONTRACT_PLAN.md` describes `bound_governance` reading
  `.atc/governance.yaml`. That file belongs to another open topic.
  Mitigation: the topic that integrates second updates the citation.

## 8. Rollout

After the topic integrates:

1. hami-quant, hami-quant-trading, ria-project: `atc onboard --sync
   <project>` makes each project's migration sync commit; each push needs the
   Owner's word. `atc onboard --check <project>` then lists nothing pending.
   hami-quant's sync commit lands on top of the unpushed fc5a3a0, so its push
   carries both; `atc preflight push` lists them before the Owner's word.
2. my-heartopia-tools, in order:
   1. The Owner reads its `AGENTS.md` for content unfit for a public
      repository.
   2. One bootstrap commit, made on a topic branch in the heartopia main
      checkout where the files already are, adds: `.gitignore` entries for
      `.agents/handoffs/` and `.claude/settings.local.json`; `AGENTS.md` with
      its `## Integration verification` section, the verifier argv file and
      the verifier; `CLAUDE.md`; and the ATC files as they are on disk. The
      same commit's tree is the one whose entries for `AGENTS.md`,
      `CLAUDE.md`, `.atc/`, `.agents/` and `.claude/` then leave
      `.git/info/exclude`. `atc integrate` cannot carry it, because the
      `main` it integrates into has no `## Integration verification` yet
      (`GIT_WORKFLOW.md` § Integration verifier source); its review is the
      open decision below.
   3. `atc onboard --sync my-heartopia-tools` makes the migration sync
      commit.
3. `atc validate --projects` passes with no problem.
4. The workspace handoff and agent memory describe the new layout.

Side effects:

- Each push of a migration commit publishes the pointer line and the
  bootloader; ria-project's and heartopia's remotes see them. Mitigation:
  each push takes the Owner's word on the listed commits.
- In heartopia, removing the exclude entries before the commit leaves the
  existing files untracked, and sync refuses an untracked tree; committing
  them on a branch first and fast-forwarding `main` keeps the checkout clean
  throughout. A managed worktree would need copies of the files, and moving
  `main` over paths left untracked in the main checkout is refused by Git.
- Heartopia's `.claude/settings.local.json` holds machine permissions; the
  `.gitignore` entry keeps it out of the public history. Mitigation: the
  Owner checks `git status --ignored` on the branch before the word.

## Open decisions for the Owner

- How the heartopia bootstrap commit of § 8 item 2.2 is reviewed and
  approved. Proposal: a fresh reviewer session reviews the branch tip under
  `atc-review` as a behaviour change, since it adds the verifier: it reads
  the diff, runs the verifier argv in a scratch copy of the tip
  (`git archive`) and quotes its result and the published-content check of
  item 2.1. On a PASS the Owner gives the word on that SHA, and the
  coordinator moves heartopia `main` with `git merge --ff-only <SHA>`.
  - Side effects: the fast-forward runs outside `atc integrate`, so no Slot,
    no candidate verifier run and no `integrations.jsonl` line record it;
    heartopia declares no task records, so no later authority check reads
    that line. Mitigation: the review quotes the verifier's result, and the
    first ordinary topic after it goes through `atc integrate`, which runs
    the verifier the commit added.
  - The alternative, the Owner's own reading, costs no session but runs the
    verifier for the first time at the first `atc integrate`, so a broken
    verifier is found on an unrelated topic.

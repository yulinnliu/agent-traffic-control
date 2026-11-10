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

Sections 1 to 8 are one topic branch in the workspace root (task ATC-0007),
reviewed under the writer/reviewer method (`atc-review`) as a behaviour
change of onboard, validate, `atc task new` and the shell installers, and
integrated with `atc integrate` once § Prerequisites hold. § 9 follows that
integration. Each section ends with its side effects and their mitigations;
nothing is implemented before the Owner accepts them.

my-heartopia-tools-deploy is not registered in `workspace.yaml` and is out of
scope.

## Prerequisites

1. **hami-quant.** Met. hami-quant `main` fc5a3a0 drops the t13 v3
   governance binding: nothing in hami-quant reads `.atc/governance.yaml` or
   its `managed-by` any more. The commit is integrated and not yet pushed.
   Its sync-managed `AGENTS.md` block still names `.atc/governance.yaml`
   until the migration sync of § 9 replaces that block with the pointer line.
2. **ria-project.** None. Its task records stay in `.atc/tasks/`, which
   remains the project's; `AGENTS.md` (the task binding rows) and
   `core/tests/unit/integration-verify.test.ts` keep their paths.
3. **ATC-0005.** ATC-0005 integrates into the root `main` first. This topic
   then rebases onto it and resolves the three files both touch:
   `adapters/python/README.md`, `docs/TASK_CONTRACT_PLAN.md` and `AGENTS.md`.
   `docs/TASK_CONTRACT_PLAN.md` § 2 describes `bound_governance` reading
   `.atc/governance.yaml`; the rebase makes it read `.atc/atc.lock` with the
   fallback of § 2 item 4.

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
  Mitigation: the § 8 search runs over each project's `AGENTS.md` in § 9
  before its sync commit is pushed.

## 2. The lock

1. Format, written by onboard and `scripts/link-skills.sh`. The file is
   exactly these three lines in this key order, UTF-8, each ending in LF
   (no CR, no BOM, a trailing newline after the last line, nothing else):

   ```toml
   version = 1
   source = "yulinnliu/agent-traffic-control"
   revision = "<40-hex ATC SHA>"
   ```

   Python reads it with `tomllib`. The shell scripts strip carriage returns
   before matching, since a checkout with `core.autocrlf` may give the file
   CRLF on disk:
   `tr -d '\r' < .atc/atc.lock | sed -n 's/^revision = "\(.*\)"$/\1/p'`.
   A `version` other than `1` is a collision for onboard and a failure for
   validate. Validate compares the lock with the render after the same CR
   normalisation it applies to every synced file.
2. Advancement: sync writes the ATC `HEAD` into `revision` whenever it
   changes any file it writes into the repository (the bootloader, the
   vendored governance, a rendered skill copy, the pointer line, `CLAUDE.md`)
   or when the recorded revision is not in the history of ATC `main`.
3. The verification gate (`GIT_WORKFLOW.md` § Lock advancement): when
   `shared/governance/GIT_WORKFLOW.md` at the ATC `HEAD` carries a heading
   `Integration verifier source` (one or more `#`) and the project's
   `AGENTS.md` has no `## Integration verification` section, or the argv file
   it names does not exist, onboard in every mode (check, apply, sync) and
   `link-skills.sh` stop the project with `VERIFICATION_SECTION_MISSING`
   before writing anything. Onboard parses the section with
   `verification_section.declaration`, the parser `atc integrate` uses: the
   section runs from the first `## Integration verification` heading line to
   the next `## ` heading or the end of the file, and the argv path is the
   first line in it that is exactly `Verifier argv file: <path>` (an optional
   `- ` or `* ` bullet, the path one word with optional backticks), resolved
   against the repository root. `link-skills.sh` runs the same check with
   `awk` on the same lines, CRs stripped, and stops with the same code. Files
   and lock therefore always come from one revision (`CORE.md`: the copy is
   byte-equal to the bound revision). Tests run onboard and `link-skills.sh`
   against a project without the section and assert the code, an unchanged
   tree and an unchanged lock.
4. `bound_governance` in `task_records.py`, which gives `atc task new` a
   project's `governance_source` and `governance_revision`, reads
   `.atc/atc.lock`. When no lock exists it reads `.atc/governance.yaml` that
   carries `managed-by: agent-traffic-control`, the Owner's ruling for the
   period before every project is migrated. The fallback and its test are
   removed by the final-sync task, the ATC root task opened once § 9 item 4
   holds (`atc validate --projects` passes and every registered project has
   `.atc/atc.lock`); it keeps that name until it has a task ID.
5. `GOVERNANCE_MANIFEST` and `pin_state` in `validate_workspace.py`, and every
   `pin` name in onboard, the shell scripts and their output lines, become
   `ATC_LOCK`, `lock_state` and `lock`.
6. `scripts/check-skills.sh` (its `.atc/governance.yaml` block) reads
   `.atc/atc.lock` with the CR-stripping reader of item 1, reports `current`,
   `stale` (revision not in ATC `HEAD`'s history) or `unlocked` (lock
   missing or `revision` empty) for `.atc/atc.lock`, and keeps its per-module
   comparison of `.atc/governance/`. `scripts/link-skills.sh` writes the lock
   with `printf` and LF line ends.

Side effects:

- Advancing on a skill-only change moves `revision` in every sync that
  touches a project, so a project's recorded revision follows ATC more
  closely than the old pin did, which advanced on governance changes only.
  Records already written cite the revision they bound and do not change.
  Mitigation: none needed; the lock names what the synced files come from.
- The verification gate stops a project that lacks the section instead of
  syncing it partly. Today hami-quant, hami-quant-trading and ria-project
  have the section and argv file; my-heartopia-tools has neither, so its
  sync stops until § 9 item 2.3. Mitigation: the code and its detail name
  the missing section or file; `--all` continues with the other projects.
- Without item 4's fallback, `atc task new` refuses with
  `NO_GOVERNANCE_BINDING` in every project whose sync has not run yet, and
  § 9 runs per project on the Owner's word, possibly days apart. Mitigation:
  the fallback, with its named removal task and trigger. It is a second read
  path while it lives; the test of item 4 pins that the lock wins when both
  files exist.
- The output lines of onboard and the shell scripts rename `pin` to `lock`
  and `unpinned` to `unlocked`; a script or note matching the old words
  stops matching. Mitigation: the § 8 search covers the root tree, and no
  project script calls `check-skills.sh`, `link-skills.sh` or
  `atc onboard` (search of the three synced project repositories).

## 3. Ownership by path

1. Sync owns `.atc/README.md`, `.atc/atc.lock` and `.atc/governance/`.
   `.atc/tasks/` is the project's: sync never reads, writes or prunes it, and
   validate checks it only through the task-record check `docs/TASKS.md`
   specifies.
2. Validate reports any other file under `.atc/`.
3. Onboard and `link-skills.sh` treat such a file as a collision and write
   nothing into the project, except that the migration source of § 4 is not
   such a file.
4. `managed-by` is no longer read or written for anything under `.atc/`; it
   stays on rendered skill copies.

Side effects:

- A project tool that keeps its own file under `.atc/` (outside `tasks/`)
  stops every later sync of that project. The four registered projects hold
  only `governance/` and `governance.yaml` there, and ria-project also
  `tasks/`. Mitigation: the collision names the path; the project moves it.
- A hand edit under `.atc/governance/` is no longer protected by a missing
  marker: sync rewrites the directory as its own, as the bootloader states.
- The verifiers of hami-quant and hami-quant-trading list `.atc/` among
  their automation Markdown prefixes, and ria-project's treats every path
  under `.atc/` as documentation-only. `atc.lock` takes the place of
  `governance.yaml`, also a non-Markdown file, so each project classifies a
  sync commit as it does today.

## 4. Migration

Sync, in onboard and in `scripts/link-skills.sh`, converts an existing layout
in one pass. Before writing, it plans every step and stops on any collision
(a `.atc/governance.yaml` without the marker, a stray file under `.atc/`, a
pointer position it cannot decide), writing nothing. It then writes in this
order:

1. `.atc/README.md` and `.atc/governance/` at the ATC `HEAD`.
2. The pointer line in `AGENTS.md`: the block between
   `<!-- atc:project-policy-reference begin -->` and
   `<!-- atc:project-policy-reference end -->` is replaced by the pointer
   line at the same position. Text outside the markers is not touched.
   Without either marker, the pointer line goes before the first `## `
   heading, as the block does today, with the same collision cases.
3. `.atc/atc.lock`.
4. `.atc/governance.yaml` is deleted last.

`.atc/tasks/` is left as it is. The sync commit carries the deletion;
`commit_sync` already owns paths of `update` items whose content is `None`.

A stop between the steps (a write error, an interrupted process) leaves the
checkout dirty with the steps done so far, and every later sync stops with
`CHECKOUT_DIRTY` (`GIT_WORKFLOW.md` § Governance sync commits). Each prefix
is readable: after step 1 or 2 the old `governance.yaml` still names a
revision and `bound_governance` uses it; after step 3 the lock wins and
`governance.yaml` is a leftover validate reports. Recovery is
`git restore --staged --worktree .` plus deletion of the new files the stop
created (`.atc/README.md`, `.atc/atc.lock`, never anything under
`.atc/tasks/`), each on the Owner's word, then the same sync again.

Rollback after the sync commit: before a push, `git reset --hard <main
before the sync>` in the project restores the previous layout; after a
push, `git revert <sync commit>` does, and the previous ATC tool (an ATC
`main` before this topic) is needed for any further sync of that project,
because the new one migrates it again.

Side effects:

- Project prose that cites `.atc/governance.yaml` goes stale: ria-project's
  `docs/DEVELOPMENT.md` and `docs/STATUS.md`. Sync does not render those
  files (`GIT_WORKFLOW.md` § Governance sync commits: a change sync does not
  render goes through a topic). Mitigation: each project's own session
  updates them in a topic after its migration sync; task records that cite
  the file keep the path they cite, which was true when written.
- The migration commit deletes a file and rewrites `AGENTS.md` in one sync
  commit, so a project's reviewers see a larger sync diff once. Mitigation:
  the commit subject names the ATC revision and the migration; `atc preflight
  push` lists it before the push.
- An unmigrated project and a migrated one coexist through § 9, and
  `atc validate --projects` reports the unmigrated ones until then.
  Mitigation: § 9 item 4 is the finish line; § 2 item 4 keeps
  `atc task new` working meanwhile.

## 5. Every file sync writes committed

1. Before writing, sync checks each path it would write with
   `git check-ignore`; an ignored or excluded path stops the project with
   `IGNORED_OUTPUT` and the paths, nothing written. `.atc/tasks/` is not a
   path sync writes and is not checked.
2. Validate, in a project that is the top of its own Git checkout, fails on
   a file sync writes that Git ignores or excludes, and lists one that is
   not committed yet as a note: `atc bootstrap add` onboards with the apply step,
   which writes without committing, and validates right after.
3. `scripts/check-skills.sh` reports an ignored one as `ignored`.

Side effects:

- my-heartopia-tools excludes `AGENTS.md`, `CLAUDE.md`, `.atc/`,
  `.agents/` and `.claude/` today, so every sync of it stops with
  `IGNORED_OUTPUT` (or `VERIFICATION_SECTION_MISSING` first) from
  integration until § 9 item 2 completes. Mitigation: the stop is per
  project, so `--all` continues with the others; § 9 item 2 ends it.
- The machine-local mode for a public repository ends. A repository that
  must not publish its agent files cannot adopt ATC. Mitigation: § 9 item
  2.1 is the Owner's publication check for heartopia, the only such
  repository.

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
  the cost is measured once in § 9 with the context hook's numbers.
- Codex and dsh read `AGENTS.md` natively; nothing changes for them.

## 7. The workspace root

The root is the governance source: it reads `shared/governance/` and binds
its own `main`, so it has no `.atc/README.md`, `.atc/atc.lock` or
`.atc/governance/`; its `.atc/` holds `tasks/` alone. Validate checks the
root's `.atc/` under the same rule of § 3, with no sync-owned file expected.

1. The topic edits the root `CLAUDE.md` of § 6 item 1 by hand; it is a
   committed root file, not sync output.
2. `atc onboard --sync --root-only`, run from the topic worktree's
   `adapters/python/`, renders only the root's skill copies under
   `.claude/skills/`, `.agents/skills/` and `.dsh/skills/`
   (`root_adapter_plan`) and commits nothing; it writes no `.atc/` file and
   no `CLAUDE.md`. The topic runs it once after the template change and
   commits any copy it changed.

Side effects:

- The root `CLAUDE.md` is also the file onboarding copies into a project
  without one; after the topic it is the import form, which § 6 item 2
  replaces in projects holding the previous form.

## 8. Citations, tests

1. Citations: `README.md`, `README.zh-TW.md`, `adapters/python/README.md`,
   `docs/TASK_CONTRACT_PLAN.md` (after the ATC-0005 rebase), the
   `bootstrap.py`, `onboard_project.py` and `task_records.py` docstrings, the
   header comments of `link-skills.sh` and `check-skills.sh`, and the
   shell-install job of `.github/workflows/validate.yml` (its policy-block,
   `managed-by` and stale pin assertions become pointer-line, lock and stale
   lock assertions). A search of the root tree outside `projects/`,
   `worktrees/` and `state/` finds no live `governance.yaml`,
   `project-policy-reference` or `pin` naming the file, apart from the
   migration code of § 4, the fallback of § 2 item 4 and their tests.
2. Tests: `test_tooling.py`, `test_onboard_sync_commit.py`,
   `test_testkit_check.py`, `test_integration_verify.py` and
   `test_task_records.py` follow the new names; new tests cover the lock
   bytes and version check, CRLF on disk read by both readers, lock
   advancement on a skill-only change, `VERIFICATION_SECTION_MISSING`
   writing nothing, a stray file under `.atc/`, the same stops and the lock
   and pointer writes through `link-skills.sh` (`test_shell_install.py`), `.atc/tasks/` left untouched
   by sync and accepted by validate, the migration of § 4 from a project in
   today's layout with task records and its write order, `bound_governance`
   reading the lock, the legacy file and the lock winning over both,
   `IGNORED_OUTPUT`, and the `CLAUDE.md` replacement rule.
3. Verification of the topic: `git diff --check`; from `adapters/python`,
   `uv run --locked atc onboard --sync --root-only` (§ 7 item 2),
   `uv run --locked atc validate`,
   `uv run --locked atc policies check ../../shared/policies/examples` and
   `uv run --locked pytest`; the CI shell-install job run locally with `sh`;
   the search of item 1.

## 9. Rollout

After the topic integrates:

1. hami-quant, hami-quant-trading, ria-project: `atc onboard --sync
   <project>` makes each project's migration sync commit; each push needs the
   Owner's word. `atc onboard --check <project>` then lists nothing pending.
   hami-quant's sync commit lands on top of the unpushed fc5a3a0, so its push
   carries both; `atc preflight push` lists them before the Owner's word.
   Rollback: § 4.
2. my-heartopia-tools, in order. Each push of its `main` needs the Owner's
   explicit word, every time.
   1. **Owner gate: publication check.** The Owner reads every file the
      bootstrap commit will publish (`AGENTS.md`, `CLAUDE.md`, the verifier
      files, `.atc/`, `.agents/skills/`, `.claude/skills/`) for content unfit
      for a public repository. This check is the Owner's alone; the reviewer
      of item 2.3 does not make it.
   2. **Bootstrap commit**, in the heartopia main checkout, where the files
      already are. The `.gitignore` gains exactly two patterns:

      ```text
      /.agents/handoffs/
      /.claude/settings.local.json
      ```

      Then the five lines `/.agents/`, `/.claude/`, `/AGENTS.md`,
      `/CLAUDE.md` and `/.atc/` leave `.git/info/exclude`.
      `git status --short --ignored` is saved before and after both edits
      and diffed: the diff must show only the five paths moving from
      ignored (`!!`) to untracked (`??`), `.gitignore` modified, the
      verifier files untracked, and `.agents/handoffs/` and
      `.claude/settings.local.json` newly ignored; any other ignored path
      that becomes untracked stops the bootstrap and goes to the Owner.
      Then `git status --short` is read before anything is staged: every
      untracked path under `.claude/` and `.agents/` must lie in
      `.claude/skills/` or `.agents/skills/`, and every other untracked path
      must be `AGENTS.md`, `CLAUDE.md`, a file under `.atc/` or one of the
      verifier files; any other path stops the bootstrap and goes to the
      Owner, because `git add .claude .agents` would publish it. Then:

      ```sh
      git switch -c atc-bootstrap
      git add .gitignore AGENTS.md CLAUDE.md .atc .claude .agents <verifier argv file> <verifier>
      git commit -m "chore: adopt Agent Traffic Control in the repository"
      ```

      `AGENTS.md` carries its `## Integration verification` section naming
      the argv file and the verifier.
   3. **Review.** A fresh reviewer session reviews the branch tip under
      `atc-review` as a behaviour change, since it adds the verifier: it
      reads the diff, exports the tip with `git archive` to a scratch and
      runs the verifier argv there, and quotes the result. `atc integrate`
      cannot carry this commit, because the `main` it integrates into has no
      `## Integration verification` yet (`GIT_WORKFLOW.md` § Integration
      verifier source).
   4. **Owner's word on the reviewed SHA**, then the coordinator moves
      `main` without touching the working tree:

      ```sh
      git fetch . <reviewed SHA>:refs/heads/main
      git switch main
      git branch -d atc-bootstrap
      ```

      `git fetch .` refuses a non-fast-forward update of `main`, and with
      `main` at the branch's commit the switch changes no file.
   5. `atc onboard --sync my-heartopia-tools` makes the migration sync
      commit on top.
   6. Push on the Owner's word, as above.

   Rollback before the push: `git reset --mixed <main before the bootstrap>`
   keeps every file on disk; the five exclude lines go back, `.gitignore` is
   restored with `git checkout -- .gitignore`, and the new verifier files are
   kept or deleted on the Owner's word. `git status --short --ignored` then
   equals the saved "before". After a push: `git revert` of the sync commit
   and the bootstrap commit, then the five exclude lines go back.
3. The root's own `atc onboard --sync --root-only` (§ 7) runs in its
   checkout's topic only; the root has no migration step.
4. `atc validate --projects` passes with no problem, and every registered
   project has `.atc/atc.lock`. This opens the final-sync task, which
   removes the fallback of § 2 item 4.
5. The workspace handoff and agent memory describe the new layout.

Side effects:

- Each push of a migration commit publishes the pointer line and the
  bootloader; ria-project's and heartopia's remotes see them. Mitigation:
  each push takes the Owner's word on the listed commits.
- While the heartopia checkout sits on `atc-bootstrap` awaiting review,
  sync refuses it (`NOT_MAIN`) and its other work waits. Mitigation: the
  branch lives only for the review.
- The tempting sequence, a commit on a branch followed by `git switch main`
  and `git merge --ff-only`, fails: the switch deletes the newly committed
  files from the working tree, and a fast-forward over the same paths left
  untracked is refused. The sequence of items 2.2 and 2.4 avoids both.

The sequence of item 2 and its rollback were proven on two scratch clones
of my-heartopia-tools carrying copies of its local files (the script and
log are in the side-session notes folder as `ATC-0007-heartopia-proof.*`):
the naive switch left 2 of 27 local ATC files; the proposed sequence kept
all 27, `main` equalled the reviewed tip with a clean tree,
`settings.local.json` and `.agents/handoffs/` stayed untracked, a
non-fast-forward `fetch .` was rejected with `main` unchanged, and the
rollback's `git status --short --ignored` equalled the saved "before".

## Owner rulings

- Heartopia bootstrap review (§ 9 item 2): a fresh reviewer reviews the
  content and runs the verifier on a scratch; the Owner separately checks
  the content that will be published; every heartopia push takes the
  Owner's explicit word each time.
- Legacy fallback (§ 2 item 4): kept until every project has `atc.lock`,
  then removed by its own task.

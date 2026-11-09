# Task contract plan

How and in what order every repository that adopts Agent Traffic Control
comes to share one task record and one `atc task` tool, `atc integrate`
comes to read a task's authority from `main` and to record task and
authority apart, and governance comes to state the coordinator flow. The
record, its authority on `main`, its derived status and the tool are
specified in `docs/TASKS.md`. Three topics in this repository, in order, each
reviewed under `atc-review` and integrated with `atc integrate`; topic C
carries the Owner's authorization for `shared/governance/` edits.

No project repository changes in these topics. Projects receive the result in
the final sync after the `.atc/` layout work.

## Prerequisite and follow-on

- The layout plan on branch `docs/atc-layout-lock` (`docs/ATC_LAYOUT_PLAN.md`)
  says `.atc/` holds only sync output and moves ria's records out. Its
  agreed revision (`.atc/tasks/` stays a project-owned path, two ownership
  zones by path) lands before that plan is implemented. Until then no check
  reads `.atc/` ownership, so topic A's `.atc/tasks/` in this repository and
  ria's records raise nothing; topic A does not depend on the revision.
- ria-project: 9 of 51 records are not valid YAML (an unquoted `: ` in a
  long string). `atc validate --projects` reports them from topic A on. The
  ria session fixes them as a ria task in the final sync; its prose
  `delivery` fields stay valid, since `delivery` is free in shape. Whether ria
  keeps its rule of bringing `docs/STATUS.md` and the record to their
  post-integration state is ria's decision; status no longer needs it.

## A. Task contract (task ATC-0001)

1. `shared/policies/schemas/task.schema.json`, Draft 2020-12, as
   `docs/TASKS.md` § Record states; the record check validates the schema
   against the Draft 2020-12 meta-schema before each use, so a broken schema
   fails `atc task check` and `validate`.
2. `adapters/python/scripts/task_records.py`: `new`, `list`, `check`;
   `atc_cli` maps `task` to it, behind the canonical checkout guard.
   - The declaration parser sits beside `verification_section.py`.
   - `list` reads refs with `git for-each-ref` and blobs with one
     `git cat-file --batch`; status follows `docs/TASKS.md` § Status, reading
     `integrations.jsonl` through `git_preflight.read_integrations`.
   - `new` reads the governance binding through one function,
     `bound_governance(repo)`: the root's `main`, or the revision in a
     project's `.atc/governance.yaml` (the layout work changes only this
     function, to read `.atc/atc.lock`). It writes with `yaml.safe_dump`.
3. `validate_workspace.py`: the record check for this repository and for each
   registered project, and the duplicate-prefix check across them.
4. Conformance: `shared/conformance/task-records/` with cases `ok`,
   `invalid-yaml`, `schema-violation`, `id-mismatch`, `no-declaration`,
   `authority-basis-missing`, run through `task_records.py check {input}`;
   `tests/test_conformance.py` maps the directory and
   `shared/conformance/README.md` gains its row. `new` and `list` need live
   Git and are covered in `tests/test_task_records.py`.
5. This repository: `## Task records` with `Task prefix: ATC` in `AGENTS.md`;
   `.atc/tasks/ATC-0001.yaml` written by hand, since the tool is this topic;
   `docs/LAYOUT.md` gains `.atc/tasks/`; `adapters/python/README.md` gains the
   `task` row.
6. Verification from the topic worktree: `uv run --locked pytest`,
   `validate_workspace.py`, `policies.py check`.

## B. Integration task and authority

1. `atc integrate`:
   - `--task <value>`: a value of the form `<prefix>-<four digits>` with a
     declared prefix names a record and derives the repository from the
     prefix; the record must exist in the reviewed commit's tree
     (`TASK_NOT_FOUND`) with the integrated branch as its `branch`
     (`TASK_BRANCH_MISMATCH`). Any other value is a task identity string
     (`CORE.md` § Task identity) and needs `--project` or `--repo`.
   - Authority is exactly one of: the record on `main` with
     `integrate_main: true`, which covers decision `FF` only
     (`AUTHORITY_NOT_FF`); or `--owner-words <quote>`.
   - A range that changes `authority` or `authority_basis` of a record on
     `main` stops with `TASK_AUTHORITY_CHANGED` unless every changed path is
     under `.atc/tasks/` and the authority is `--owner-words`.
   - A record's grant counts only as `docs/TASKS.md` § Authority on `main`
     states: walk `git log --first-parent --format=%H main -- <record>`,
     parse `authority` and `authority_basis` in each version and its first
     parent's, and take the newest commit where they differ; it must satisfy
     `git merge-base --is-ancestor <commit> <candidate>` and not
     `<commit> <main_before>` for some records-only `--owner-words` line
     naming the task; otherwise the run stops with
     `AUTHORITY_NOT_INTEGRATED` and needs `--owner-words`. A records-only
     line always carries `main_before`.
   - Removing or renaming a record that exists on `main` counts as changing
     its `authority`.
   - A range that adds a record granting more than `edit` and `commit` stops
     with `TASK_AUTHORITY_CHANGED` unless it is records-only with
     `--owner-words`; a range that adds a record whose `task_id` `main`
     already binds to another branch stops with `TASK_ID_TAKEN` (two clones
     that picked one number meet here).
   - A range whose changed paths are all under `.atc/tasks/` runs the record
     check in place of the project's verifier, and its integration record
     carries `"records_only": true`, which `atc task list` reads.
   - `--authority-reference` stays as a deprecated alias for the final-sync
     period: its value becomes the task identity string, its authority is
     recorded as `{"kind": "unclassified"}` (the run proceeds as today, on
     the per-SHA word the caller holds), it never satisfies a record's
     `integrate_main`, it is refused for any range that touches
     `.atc/tasks/`, and stderr names the replacement. Its removal after the
     final sync is a gate of this plan, not an option.
   - `--project root` names the workspace root in `atc integrate`,
     `atc worktree` and `atc task`.
2. Records: `integrations.jsonl` lines gain `task`, `records_only` and `authority`
   (`{"kind": "record", "task_id": ...}` or `{"kind": "owner_words",
   "quote": ...}`); a records-only integration that binds several tasks
   writes one line per task, all with the same candidate, and every
   records-only line carries `main_before` unconditionally; governance sync records from `onboard_project.py` carry
   `task` as the sync's source revision and authority `{"kind": "sync"}`.
   Verification evidence moves to `atc-verification-evidence/2` with the same
   keys; the adapter and `atc_testkit.evidence` read `/1` and `/2`, and
   `atc preflight push` reads old and new lines.
3. Human output of `atc integrate`, `atc preflight push` and `atc worktree`:
   project names instead of paths, 12-character SHAs, authority by kind, one
   push line per commit with the task and authority once above them. Full
   values stay in `--json`, `integrations.jsonl` and evidence. Every reader of
   these lines in `shared/skills/` and the tests moves to `--json` first.
4. Code, tests, `shared/conformance/` and `adapters/python/README.md` only;
   the governance text follows in C.

## C. Governance and skills

1. `GIT_WORKFLOW.md`: § Authority reference becomes § Task and authority (B's
   flags and record keys); § Integrate states `docs/TASKS.md` § Authority on
   `main` as rule: a record on `main` with `integrate_main: true` is the
   Owner's integration authority for `FF` after an unconditional PASS on the
   exact SHA, never for `NO_FF`, a push or a deploy; only the coordinator
   writes `authority`, from the Owner's quoted words; a grant ends the moment
   the Owner narrows or revokes it.
2. `VERIFICATION.md`: a range that changes only `.atc/tasks/` is verified by
   the record check and needs no formal review.
3. `DELIVERY.md`: writers report to the coordinator only; the coordinator
   relays step reports and findings verbatim and never reviews what it
   integrates; the reviewer is a fresh subagent or a separate session that
   has not seen the writer's conversation; close-out (archiving the task's
   sessions, deleting the scratches sessions report by exact name) is part
   of the task authority, replacing the rule that only the Owner's word
   deletes scratches; every session stops its background work before its
   final report, and every background wait has a ceiling.
4. `CORE.md` § Task identity: a repository with a `## Task records`
   declaration identifies tasks by record.
5. `shared/skills/atc-review`: the step report's fields (task, branch,
   reviewed SHA, range, verification as command and result, rulings,
   deviations, open items), at most 40 lines or 3,000 characters, no diff,
   log, file content or narrative, large material as `<sha>:<path>` or path
   plus SHA-256; the coordinator returns an oversized report unchanged; a
   PASS verdict ends with the runnable `atc integrate --task ... --reviewed
   ...` line.
6. `shared/skills/atc-coordinator`: the flow of § 3, binding a pre-authorized
   task on `main`, `atc task list --all` for status questions.
7. `shared/conformance/verification-evidence/README.md` and `docs/GLOSSARY.md`
   follow B's record keys; `atc onboard --sync --root-only` re-renders the
   root's managed skill copies in the topic worktree.

## After C

The `.atc/` layout work, then the final sync to every project (skills,
governance, `## Task records` declarations with prefixes `HQ`, `HQT`, `RIA`,
atc-coordinator seeded in hami-quant and hami-quant-trading), then a topic
removing `--authority-reference`.

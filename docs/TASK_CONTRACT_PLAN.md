# Task contract plan

How and in what order every repository that adopts Agent Traffic Control
comes to share one task record and one `atc task` tool, `atc integrate`
comes to read a task's authority from `main` and to record task and
authority apart, and governance comes to state the coordinator flow. The
record, its authority on `main`, its derived status and the tool are
specified in `docs/TASKS.md`. Four topics in this repository, in order, each
reviewed under `atc-review` and integrated with `atc integrate`; topic C
carries the Owner's authorization for `shared/governance/` edits. Every CLI
change in topic D and in the CLI trim after the final sync passes a
side-effect review of its entry below before it is built, then the usual
implementation review.

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
     project's `.atc/atc.lock` (`docs/ATC_LAYOUT_PLAN.md` § 2). It writes
     with `yaml.safe_dump`.
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

Each rule lands in the one home the placement table names; C.1 to C.7 write
the rules and steps, C.8 is the editorial pass over every file they touched.

1. `GIT_WORKFLOW.md`:
   - § Authority reference becomes § Task and authority (B's flags and record
     keys) and is the one home of the grant rule: a record grants nothing by
     itself, the Owner's words do; a record on `main` with
     `integrate_main: true` is the Owner's integration authority for `FF`
     after an unconditional PASS on the exact SHA, never for `NO_FF`, a push
     or a deploy; only the coordinator writes `authority` and passes
     `--owner-words`, from the Owner's quoted words; the writer keeps the
     record's `delivery.status`; a grant ends the moment the Owner narrows or
     revokes it; a grant counts only when it entered `main` through this
     machine's records-only `--owner-words` integration. Recovering an
     interrupted integration takes `--task`. `docs/TASKS.md` § Authority on
     `main` keeps the mechanism the tool implements (the binding commit, how
     the grant's commit is found, the refusals) and replaces its rule
     sentences with a one-line pointer to this section.
   - § Integration verifier source: the verifier argv comes only from the
     `AGENTS.md` declaration; only `--verify-timeout` may override it. The
     paragraph on `--verify-command-file` and `--verify-command` goes.
   - § Explicitly authorized squash and every other mention of
     `atc preflight merge` name `atc integrate --check`.
   - The reviewed commit is named by its full SHA or a unique prefix of at
     least 12 characters; the integration record holds the full SHA.
   - § Mainline check before final self-verification: before its step report
     the writer runs `atc integrate --check` and puts the verdict token in
     the report; on `REBASE_REQUIRED` or `OVERLAP` it rebases and reports the
     new tip.
   - § After formal review: after a rebase the writer reports only the new
     tip; the coordinator runs `atc integrate --check`, with `--rebased` when
     a review exists, for carry-over and `OVERLAP`, and runs no range-diff or
     file diff by hand.
   - § Command results becomes the output rule for every `atc` command (see
     Tool output below); its exit-code and refusal-line tables stay. Its
     rows for the `REMOVABLE` verdict and the cleanup `KEEP` verdict change
     with D.7, and its `--branch` stop row with item 3 of the CLI trim after
     the final sync; each is a governance follow-up under
     `GOVERNANCE_CHANGES.md` in the topic that makes the code change.
2. `VERIFICATION.md`:
   - § Documentation-only verification gains its sibling: a range that
     changes only `.atc/tasks/` is verified by the record check and needs no
     formal review.
   - § Testing: writers and reviewers run the selection (`select_tests.py`,
     or the project's selection); only the integration verifier runs the
     full suite.
   - § Independent review: a SHA's first review is a fresh reviewer, a
     subagent or a separate session that has not seen the writer's
     conversation; a re-review that only fixes the last round's findings
     continues the same reviewer with the findings and the fix diff; new
     design or new files get a fresh reviewer. No rule requires the final
     round to be fresh. The verdict's shape is a rule here: first line the
     verdict token, one line per finding (severity, `file:line`, required
     fix), verified items as one line, no narrative; a PASS ends with the
     runnable `atc integrate --task ... --reviewed ...` line.
3. `DELIVERY.md`:
   - § 8.1 Revision Delivery: writers report to the coordinator only; the
     coordinator relays step reports and findings verbatim, as file paths,
     and never reviews what it integrates. The step report's shape is a rule
     here: task, branch, tip, range, verification as command and result,
     check verdict token, rulings, deviations, open items; at most 40 lines
     or 3,000 characters; no diff, log, file content or narrative of what
     changed; large material as `<sha>:<path>` or path plus SHA-256; the
     coordinator returns an oversized report unchanged.
   - New § Close-out, after § 10: close-out is a standing rule of every
     task: after a task is integrated into `main`, the coordinator removes
     that task's worktree and scratches and archives its sessions, then
     reports. Keep exceptions are written in the task record.
     Items outside the task, `REFUSED` and `ANOMALY` lines, refused archives
     and failed integrations go to the Owner. This replaces the rule that
     only the Owner's word deletes scratches, which C.5 and C.6 remove from
     the skills.
   - New § Background work, after § Close-out: every session stops its
     background work before its final report; every background wait has a
     ceiling; no background work remains after the final report, and a
     later notification gets one delta line or nothing. Reviewers run
     their tests in the foreground.
4. `CORE.md` § Task identity: a repository with a `## Task records`
   declaration identifies tasks by record.
5. `shared/skills/atc-review`, steps citing C.1 to C.3:
   - The step report (shape in `DELIVERY.md` § 8.1): the writer saves it to
     `~/.atc/reports/<task>-<short sha>.md` and sends that path. Its closing
     message in its own session is one line for the Owner, in plain Chinese
     with no tokens, for example
     「報告已送 coordinator（tip <sha>），等審查與你核准；尚未整合、未 push。」;
     when there is a problem the line names it plainly instead (for example,
     a rebase is needed). Tokens stay in the step report.
   - The verdict (shape in `VERIFICATION.md` § Independent review): saved to
     `~/.atc/reports/<task>-<short sha>-review.md`; the message carries the
     token and the path.
   - "Writer: after integration" and `adapters/claude.md:19` drop the rule
     that scratch deletion runs only on the Owner's word and point to
     `DELIVERY.md` § Close-out.
   - The writer's `--check` before the report and its own rebase, the
     reviewer reuse rule, the selection, the foreground and background
     rules.
   - The `.venv` bullet under "Prepare it": `atc tmp clean` takes every
     `.venv` beside a `pyproject.toml` in the scratch out as a link.
6. `shared/skills/atc-coordinator`:
   - The flow of C.3, binding a pre-authorized task on `main`,
     `atc task list --all` for status questions.
   - `references/briefs.md` holds both brief templates, where the briefs
     already live; `atc-review` points to them. Writer brief: task ID,
     worktree path, plan section, and the Owner's special rulings only when
     there are any. Reviewer brief: task, SHA and report path. Scope and
     authority come from the task record, rules from `AGENTS.md` and the
     skills; a brief restates neither.
   - Step 6 "Close" (`SKILL.md:117-122`) drops "delete the task's scratches
     only as a separate step on the Owner's word" and follows
     `DELIVERY.md` § Close-out.
   - Close-out steps: the list comes from `atc tmp list`, with each
     scratch's dependent runs (a `tree` inside it); sessions are archived
     after a successful `atc worktree cleanup`; a refused archive is read
     through the session's state: a running session's writer ends its work,
     a pinned or idle one goes to the Owner.
7. `shared/conformance/verification-evidence/README.md` and `docs/GLOSSARY.md`
   follow B's record keys (`/2`); `atc onboard --sync --root-only` re-renders
   the root's managed skill copies in the topic worktree.
8. Editorial pass, the last step of C: over every file C.1 to C.7 touched,
   reorder, merge duplicates, drop stale wording, and check against the
   `hami-writing-for-agents` skill. No rule changes. Its reviewer checks
   that the meaning is unchanged, that no rule sits in two places, and the
   line count of each file before and after.

### Placement

Governance holds the rule, a skill holds the steps and cites the rule,
`AGENTS.md` holds an entry pointer. Any other file that needs the rule gets
a one-line pointer to its home.

| Rule | Home | Steps | Pointers |
| --- | --- | --- | --- |
| Task and authority, record grant, recovery needs `--task`, `delivery.status` | `GIT_WORKFLOW.md` § Task and authority | `atc-coordinator`, `atc-review` | `docs/TASKS.md` § Authority on `main` (mechanism only, one-line pointer for the rule) |
| Verifier argv from the declaration only | `GIT_WORKFLOW.md` § Integration verifier source | none | `AGENTS.md` `## Integration verification` |
| Writer's `--check` before the report, own rebase | `GIT_WORKFLOW.md` § Mainline check before final self-verification | `atc-review` writer | none |
| After a rebase: new tip only, coordinator's `--check --rebased` | `GIT_WORKFLOW.md` § After formal review | `atc-coordinator` | `atc-review` |
| Tool output principle | `GIT_WORKFLOW.md` § Command results | none | `docs/TESTKIT.md` § Running and logging |
| Records-only range needs no review | `VERIFICATION.md` § Documentation-only verification | `atc-coordinator` | none |
| Selection for writers and reviewers, full suite for the verifier | `VERIFICATION.md` § Testing | `atc-review` | none |
| Reviewer freshness and reuse, verdict shape | `VERIFICATION.md` § Independent review | `atc-review`, `atc-coordinator` | none |
| Report routing, verbatim relay, coordinator never reviews, step report shape | `DELIVERY.md` § 8.1 Revision Delivery | `atc-coordinator`, `atc-review` | none |
| Close-out standing rule (variant A) | `DELIVERY.md` § Close-out (new) | `atc-coordinator` close-out | `atc-review` after integration, `atc-review/adapters/claude.md` |
| Push needs the Owner's word | `DELIVERY.md` § 10 Push authority | none | § Close-out |
| Background work and ceilings | `DELIVERY.md` § Background work (new) | `atc-review` | none |
| Task identity by record | `CORE.md` § Task identity | none | `AGENTS.md` `## Task records` |
| Report files, closing line, brief and verdict steps | steps only | `atc-review` | `atc-coordinator` |
| Brief templates | steps only | `atc-coordinator` `references/briefs.md` | `atc-review` |

## Tool output

Measured from Bash and PowerShell tool results in the 419 session
transcripts of the 21 days before this plan, in the root's transcript folder
and the worktree folders beside it. Size is characters of the tool result. A
compound command counts its whole result, so the median is the robust
figure; many calls already cut their output with `tail` or `head`, so these
sizes are a floor of what the commands print.

| Rank | Command | Calls | Total (k chars) | Median | p90 |
| --- | --- | --- | --- | --- | --- |
| 1 | `pytest` run directly | 1101 | 974 | 486 | 1903 |
| 2 | `atc integrate` | 375 | 321 | 561 | 1753 |
| 3 | `atc onboard`, all modes | 176 | 316 | 945-2041 | 1551-4481 |
| 4 | `atc worktree audit` | 252 | 252 | 374 | 2248 |
| 5 | `atc worktree create` | 332 | 191 | 251 | 987 |
| 6 | `atc worktree cleanup` | 363 | 157 | 473 | 691 |
| 7 | `atc integrate --check` | 194 | 135 | 416 | 1332 |
| 8 | `atc validate` | 126 | 127 | 523 | 2065 |
| 9 | `atc test run` | 133 | 111 | 464 | 1688 |
| 10 | `atc tmp list` | 42 | 58 | 740 | 2243 |
| 11 | `select_tests.py` | 25 | 48 | 1486 | 3705 |
| 12 | `atc tmp clean` | 79 | 42 | 312 | 1721 |
| 13 | `atc test mutants` | 41 | 37 | 685 | 1968 |
| 14 | `atc worktree --help` | 18 | 17 | 602 | 2050 |

Command examples in skills: `shared/skills/` names `atc worktree audit` 5
times, `atc integrate` 5, `atc tmp clean` 4, `atc test run` 4,
`atc test mutants` 4, `atc tmp list` 3, `atc integrate --check` 3,
`atc validate` 2, `atc onboard` 2, `atc worktree create`, `atc tmp prune` and
`atc preflight push` once each; the project-only skills name
`atc integrate` twice, and the rendered project copies repeat the canonical
ones. Every command they name is in the table, so the skills add no command
to the ranking.

### Principle

A command's default output is its verdict and the next action. Detail sits
behind a flag or in a log file whose path the command prints; `--json`
carries the full result for machines; an error is one line naming the cause
and the next step, and a usage error prints the usage line, not the whole
help. A run that did not complete reports `TIMEOUT` or `INTERRUPTED` with
the last lines of its log, never a pass.

### Proposals

1. Direct `pytest`: writers and reviewers move to `atc test run --select`
   (C.2, D.12), which prints one summary line and keeps the progress dots,
   `SKIPPED` lines and tracebacks in the log.
2. `atc integrate`: success prints one line (decision, project, `main`
   old -> new as 12-character SHAs, task); the verifier's output stays in
   its log in the run's working directory and the line names it; a failure
   repeats the log's last twenty `FAILED` or `ERROR` lines as `TESTKIT.md`
   § Running and logging states.
3. `atc onboard`: `--check --all` prints 18,402 characters today, 135 of its
   172 lines being `is current` skips. Default output prints only items that
   change or stop, and one count line per checkout (`skip 29, update 0`);
   `--verbose` lists the skips.
4. `atc worktree audit`: its own output is one line per worktree (40 to 46
   characters measured); the transcript sizes come from commands chained to
   it. No change.
5. `atc worktree create` and `atc worktree cleanup`: one verdict line
   (`CREATED` or `REMOVED`, project/topic, base or main as a 12-character
   SHA); evidence under `--json`.
6. `atc integrate --check`: the short form of D.10 becomes the default
   human output, keeping the `CHECK <verdict>` line and the `WARNING`
   lines; `--json` keeps the full report.
7. `atc validate`: 38 characters on success; no change.
8. `atc test run`: D.12.
9. `atc tmp list`: one line per scratch with its name, its tree relative to
   the project and its age; full paths under `--json`. Registry entries
   whose scratch is gone show as `missing` today; the list ends with one
   line counting them and naming `atc tmp prune`.
10. `select_tests.py`: D.15.
11. `atc tmp clean` and `atc test mutants`: already one line per item; no
    change.
12. `--help`: covered by the principle's usage-error line; no command change
    beyond argparse's usage line plus one `next:` line on a usage error.
13. `atc task list --all`: 6,969 characters today; 9 of its 53 lines are
    `INVALID_RECORD` warnings, one per bad ria record. The warnings fold
    into one line per repository with the count and
    `atc task check <repo>`.

Proposals 2, 3, 5, 6, 9, 12 and 13 are built in D with the side effects
below; they share one risk, a reader that parses the human output, mitigated
by B.3's move of every reader in `shared/skills/` and the tests to `--json`
and by a test per command that pins its default output.

| Proposal | Side effect | Mitigation |
| --- | --- | --- |
| 2 | A failed verifier's detail is one step away | The failure line names the log and repeats its last twenty failure lines |
| 3 | A drift buried in skips was visible in the full list | Counts per checkout; every non-skip item is printed; `--verbose` restores the list |
| 5, 6 | Evidence a reader used from the human block is gone | `--json` unchanged; skills read `--json` (B.3) |
| 9 | Full scratch paths needed for `tmp clean <dir>` | `tmp clean` takes the scratch name as `tmp list` prints it |
| 12 | A caller that relied on the help on error | `--help` still prints it |
| 13 | A bad record no longer listed by name | `atc task check` names each one |

## Verified CLI facts

1. `atc preflight cleanup` duplicates `atc worktree cleanup`: the check is
   the same. `worktree cleanup` runs `git_preflight.cleanup_preflight`
   (`hami_worktree.py:824`) before it removes anything; `preflight cleanup`
   runs that check alone, read-only. No root doc, skill, project skill or
   project doc names it, and the transcripts hold no call; its one test is
   the routing row in `tests/test_atc_cli.py:19`.
2. `onboard --apply` serves heartopia only: false. The mode is generic
   (create missing managed files, update nothing, commit nothing) and names
   no project. Its one caller is
   `atc bootstrap add` (`bootstrap.py:167-168`); `adapters/python/README.md:19`
   documents it; no doc or skill gives it as a command, and the transcripts
   hold no direct call.
3. `--contract` accepts only 1: confirmed. It is a required integer and any
   value other than `CONTRACT = 1` stops with `CONTRACT_INCOMPATIBLE`
   (`hami_worktree.py:47`, `:1071`); all 17 call sites pass `--contract 1`.
4. Reference counts, files that name the flag:

   | Flag | Root docs, skills, governance | Root tests | Project skills | Project docs and vendored governance |
   | --- | --- | --- | --- | --- |
   | `--main` | 0 | 0 | 0 | 0 |
   | `preflight cleanup` | 0 | 1 (routing) | 0 | 0 |
   | `preflight merge` | 1 (`GIT_WORKFLOW.md`) | 2 | 0 | 4 |
   | `onboard --apply` | 1 (adapter README) | 0 | 0 | 0 |
   | `--verify-command`, `--verify-command-file` | 1 (`GIT_WORKFLOW.md`) | 5 | 0 | 4 |
   | `--contract` | 4 | 3 | 9 | 1 |
   | `--tree-name` | 2 | 1 | 8 | 0 |
   | `--authority-reference` | 4 | 5 | 10 | 4 |
   | `--expected-head` | 1 | 1 | 9 | 1 |

   The project-doc hits for `preflight merge` and the verifier flags are the
   vendored `GIT_WORKFLOW.md` copies.
5. `atc test run` writes no log. It collects pytest's output in memory
   (`testrun.py:105-119`), prints the last fifteen lines after pytest ends,
   has no timeout, and writes nothing to disk; `TESTKIT.md` § Running and
   logging, which states live output and a log, does not hold for it today.
6. The selection for a change to `git_preflight.py` or `git_integrate.py`
   (23 and 22 of 37 test files): one file comes from the "declared reader of
   its whole directory" rule (`test_select_tests.py`); the rest reach the
   module through imports. Narrowing that rule saves one test file, so it
   stays as it is.
7. Callers of `friction`, `classify`, `policies`, `enforce` and `bootstrap`,
   as files that name `atc <command>` and as transcript calls: `friction`
   0 files, 2 calls; `classify` 0, 0; `policies` 0, 7; `enforce` 2 root
   and 4 project, 18; `bootstrap` 4 root and 4 project, 8.

## D. CLI changes

Code, tests, `shared/conformance/`, `docs/TESTKIT.md`, `docs/ATC_HOME.md` and
`adapters/python/README.md`. Done: D.5, D.9, D.18, D.19, D.20, D.24 and
D.25. Order within the rest: D.13 before D.12's scratch-name form, D.15
before D.12 (the `--json` form `--select` runs), D.17 last. Each entry: the
change, its side effects, their mitigations.

The rest of D runs in two batches (Owner's ruling), each with one design
review, one implementation review and one integration:

- Batch 1, no governance edit, in this order: D.21 and D.23; the direct
  fixes D.4, D.6 and D.11; D.15, D.13, D.12; D.2 with D.3; D.10 with
  D.16; D.8; D.26 and D.27 after their own design review; D.17; then the
  lifecycle guide. A direct fix is self-evident
  and gets no design beyond its entry; the implementation review checks
  its side effects. Direct fixes beside D.4, D.6 and D.11:
  - `atc task new` refused with `UNKNOWN_PROJECT` (given `--repo .`) or
    `CHECKOUT_CANONICAL` (run from the canonical checkout) says what to do
    instead: run it inside the new worktree with an absolute `--repo`. A
    test asserts the hint.
- The lifecycle guide, last, once the CLI is settled: `docs/WORKFLOW.md`,
  one page ordered by a task's life: `atc worktree create`; `atc task new`
  inside the worktree with an absolute `--repo`; the writer's work and
  self-checks (`atc test run`, `ci --stages`); the report under
  `~/.atc/reports/`; the reviewer; `atc integrate --check`; the Owner's
  word relayed and the writer's `atc integrate --owner-words`; close-out;
  the coordinator's `atc preflight push` and push, asked each time. Each
  step names its command, who runs it, and its common refusals with their
  fix. It says how, never the rules: it links governance (`DELIVERY.md`,
  `GIT_WORKFLOW.md`) and the skills (`atc-coordinator`, `atc-review`,
  `atc-worktree`) instead of restating them. `README.md` points to it
  beside `docs/LAYOUT.md`; `AGENTS.md` does not, since every session loads
  it and the guide is read on demand; the skills do not, since a canonical
  skill names no path of this checkout and a project's vendored copy could
  not open it.
  - Side effects: a second place a command form is written, which drifts
    when the CLI changes.
  - Mitigations: it is written after D.17, against the settled CLI, and
    names only commands and refusal tokens that tests pin.
- Not taken: D.1 (Owner's ruling). `atc task new` in the new worktree is
  one command, and a second entry point that writes records adds the side
  effects D.1 lists for no saving.
- Batch 2, on the Owner's authorization of the governance edits: D.22;
  D.14, whose prefix form `GIT_WORKFLOW.md` § Integrate names as arriving
  with D.14; D.7, whose retired verdicts `REMOVABLE` and cleanup `KEEP`
  are rows of `GIT_WORKFLOW.md` § Command results.

1. `atc worktree create --scope <text>` writes the task record in the new
   worktree through `task_records.new`.
   - Side effects: a second entry point that writes records; a record for
     the branch may already exist; a record written with authority the
     coordinator did not give; a repository with no `## Task records`
     declaration (heartopia) has no prefix to number from; two concurrent
     creates pick the same number.
   - Mitigations: one writer function; refuse when a record names the
     branch; the record grants `edit` and `commit` only and is left
     uncommitted with its path printed; `--scope` in an undeclared
     repository stops with `TASK_PREFIX_MISSING` and creates nothing; a
     duplicate number meets `TASK_ID_TAKEN` at `atc integrate`, as two
     `atc task new` runs do today.
2. `atc tmp clean` lists a scratch's dependent runs (a `tree` inside it) and
   takes them with `--with-runs`.
   - Side effects: deleting a run another session still reads.
   - Mitigations: listing is the default; a run with an open file or a live
     process ends `REFUSED` as scratches do today.
3. The scratch registry records the task ID; `atc close-out --task <ID>`
   removes the task's worktree and scratches in one call, and its report
   files `~/.atc/reports/<task>-*.md` (`DELIVERY.md` § Close-out).
   - Side effects: a registry format change; one call deleting many items;
     an item of another task taken by mistake.
   - Mitigations: entries without a task never match one; it runs only
     when `integrations.jsonl` has the task's line and the local `main`
     contains it (no push needed, `DELIVERY.md` § Close-out), and skips the keep exceptions in the record; every item
     ends `DELETED`, `SKIPPED` or `REFUSED`; it touches nothing outside the
     task and runs `worktree cleanup`'s own checks. A worktree that holds
     the caller's working directory ends `SKIPPED` with that reason, so a
     writer running it from its worktree (D.5) leaves that item to the
     coordinator; `atc-review` "Writer: after integration" and
     `atc-coordinator` step 6 name the command once it lands.
4. Drop `--main` from `atc integrate` and `atc preflight push`.
   `git_preflight.py` adds `--main` to `merge`, `cleanup` and `push` in one
   loop; `merge` and `cleanup` keep it until D.7 retires them (batch 2).
   - Side effects: none found (0 references, 0 calls).
   - Mitigations: argparse's unknown-flag error.
5. Relay-first integration. The writer integrates its own reviewed
   tip and cleans its own items; the coordinator keeps the Owner's
   words, the push and the session archive.
   - `atc integrate --check` needs no authority: without `--task` and
     `--owner-words` the resolved line says `authority=not_checked`;
     with `--task` it evaluates the grant and says `authority=missing`
     where `integrate_main` is absent, in place of `AUTHORITY_MISSING`.
     Every other refusal of the grant still stops the check.
     - Side effects: a check that passes says nothing about authority.
     - Mitigations: the resolved line names the authority kind, and the
       integration run without `--check` still stops with
       `AUTHORITY_MISSING`.
   - Relayed words, not record authority: granting `integrate_main` at
     the Owner's word is a records-only commit on `main`, which puts
     `main` ahead of the topic and forces a rebase that voids the
     reviewed SHA.
   - The flow, per task:
     1. The Owner says start. The coordinator creates the worktree, runs
        `atc task new` there (a record granting `edit` and `commit`
        only) and commits it on the topic branch; the record reaches
        `main` with the topic's first integration (Owner's ruling: the
        record travels with the topic).
     2. The coordinator starts the writer with its working directory at
        the task's place: a project task at the project's checkout
        folder, a root task at its root worktree (the canonical root
        checkout stays on a clean `main`). The brief names the ID, the
        record path and the integration step: "after the reviewer's PASS
        and the Owner's word, run `atc integrate` and close out", in
        place of "do not integrate".
     3. Design, review, implementation and review run as today.
     4. After an unconditional PASS the coordinator runs
        `atc integrate --check` (no authority needed), asks the Owner
        for the integration word, and relays the Owner's exact words
        and the reviewed full SHA to the writer.
     5. The writer runs `atc integrate --task <ID> --reviewed <sha>
        --owner-words "<the relayed words>"` against the integration
        checkout by absolute path, then closes out: its scratches
        (`atc tmp clean`), `~/.atc/reports/<ID>-*.md`, and its worktree
        unless the session's working directory is inside it; D.3's
        `atc close-out --task <ID>` replaces these steps once it lands.
        It reports the integrated SHA and every close-out line.
     6. The coordinator asks the Owner for the push word once for
        whatever is integrated, pushes, archives the task's sessions,
        removes a worktree the writer's close-out skipped, and checks
        `atc tmp list` and `atc worktree list`.
   - Governance and skills, on the Owner's authorization for these
     files:
     - `GIT_WORKFLOW.md` § Task and authority: `--owner-words` is passed
       by the coordinator or by the writer it relays the Owner's exact
       words to, for the reviewed SHA the relay names; the paste-only
       paragraph keeps the user who pastes as the coordinator. This
       revises the ATC-0004 ruling "`--owner-words`: coordinator only".
       Record authority (`integrate_main`) stays as it is. § Integrate's
       note that a check without authority arrives with D.5 becomes the
       present rule.
     - `DELIVERY.md` § Close-out: the integrating session removes the
       task's items; the coordinator archives the sessions and removes
       what the integrating session could not.
     - `FAILURE_ESCALATION.md`: a port or service the Owner's steps
       expect is taken: stop and ask the Owner, never switch ports or end
       the process holding it.
     - `atc-review` skill and its Codex overlay (`adapters/codex.md`,
       whose "never pasted to a writer" line goes): the writer's
       integration and close-out step;
       on a takeover of a published topic, fetch and confirm the remote
       tip before committing; Owner-facing text in the Owner's language
       with the fixed English role terms (writer, reviewer, coordinator,
       Owner).
     - `atc-coordinator` skill: the flow above as the ticket flow;
       delegated sessions run Opus or Sonnet by task, another model only
       after asking the Owner; the Owner's language and role terms as in
       `atc-review`.
     - After they land, the memory notes they replace are listed per
       file for the Owner, then trimmed.
   - Side effects:
     - A writer session in auto mode may be refused `atc integrate`: the
       relayed words arrive as a peer message, which the session does not
       treat as the user's approval (seen once in four Hami relays).
     - Several writers integrating at once into one `main`.
     - A root writer cannot remove the worktree its session runs in.
     - Projects carry vendored governance and skills that say the
       coordinator integrates and never forwards `--owner-words`.
     - A relayed quote is the coordinator's copy of the Owner's words.
   - Mitigations:
     - A refused writer stops and reports `BLOCKED`; the Owner then
       approves in the writer's session. No permission rule changes.
     - The Integration Slot serialises runs; a moved `main` stops with
       `REBASE_REQUIRED` or `OVERLAP` and takes the existing route.
     - Close-out reports that worktree `SKIPPED` and the coordinator
       removes it after archiving the session.
     - Relay-first applies to root tasks once D.5 lands, and to a project
       once the final sync carries the new governance and skills; until
       then project tasks keep coordinator integration.
     - The coordinator relays the words verbatim with the SHA, and
       `integrations.jsonl` records the quote and the reviewed SHA as
       for any `--owner-words` run.
6. `--contract` becomes optional with default 1.
   - Side effects: none for callers; 17 call sites keep passing 1.
   - Mitigations: a value other than 1 still stops with
     `CONTRACT_INCOMPATIBLE`; removal waits for the CLI trim after the
     final sync.
7. Retire `atc preflight merge` and `atc preflight cleanup`.
   `preflight merge` stays, until the CLI trim after the final sync, as an
   alias that runs `atc integrate --check` and prints one stderr line
   naming it; its
   differences from `integrate --check`, each mapped or refused:
   - `--main`: accepted only as `main`; any other value stops with `USAGE`
     naming `atc integrate --check` (D.4 drops `--main` from integrate).
   - `--reviewed` is optional and takes any SHA in `preflight merge`; the
     alias requires it in `integrate --check`'s form (full SHA, or the
     prefix of D.14) and stops with `USAGE` without it.
   - `preflight merge` compares with `origin` as last fetched; the alias
     runs `git fetch origin` and so stops with `FETCH_FAILED` when `origin`
     is unreachable.
   - `preflight merge` accepts any checkout state; the alias inherits
     `integrate --check`'s stops for a checkout not on `main` or with
     uncommitted changes.
   - The verdict line `FF FAST_FORWARD` becomes `CHECK FF ...`, and
     `--json` prints `integrate --check`'s report.
   - Side effects: vendored `GIT_WORKFLOW.md` in four projects names
     `preflight merge` until the final sync; the differences above reach a
     caller of the old form; the read-only removability check of
     `preflight cleanup` is gone; § Command results rows for `REMOVABLE`
     and cleanup `KEEP` (C.1 follow-up).
   - Mitigations: no skill or transcript call uses the old form; the stderr
     line names the new command; `atc worktree audit` gains each worktree's
     removability verdict; `preflight cleanup` goes outright.
   - Design (batch 2):
     - `git_preflight.py merge` keeps its parser (`--repo`, `--branch`,
       `--reviewed`, `--main`, `--json`) and runs
       `git_integrate.main(["--check", "--repo", <repo>, "--branch",
       <branch>, "--reviewed", <sha>, *(["--json"])])`, imported inside the
       function (`git_integrate` imports `git_preflight`). Before it, one
       stderr line: `NOTE atc preflight merge is retired; this ran atc
       integrate --check --repo <repo> --reviewed <sha>`. Its exit code is
       integrate's: 0 for `CHECK FF`/`CHECK NO_FF`, 1 for a stop, 2 for an
       error, 3 for a required recovery found by the check. Its `--json`
       is integrate's payload (`command`, `check`, `preflight`), not the
       old top-level `verdict`/`reason`.
     - Refused before integrate runs, `ERROR USAGE <detail>` and exit 2, each
       naming `atc integrate --check`: `--main` other than `main`; no
       `--reviewed`.
     - `cleanup` leaves the parser, so `atc preflight cleanup` is argparse's
       invalid-choice usage error with atc's `next: atc preflight --help`.
       `cleanup_preflight()` stays: `atc worktree cleanup` runs it.
       `merge_preflight()` stays: `atc integrate` runs it.
     - `atc worktree audit`: `state_evidence()` adds `removable`, true when
       the worktree is present, `main` resolves, and it is clean and merged
       into `main`. That repeats the `REMOVABLE` condition of
       `cleanup_preflight()` over the evidence audit already gathers rather
       than calling it. `cmd_audit` sets the `Result.detail` of an `OK` or
       `LEGACY_TARGET` result with no detail of its own to `removable` or
       `not removable: <unmerged, uncommitted changes or missing>`, which
       `render` prints. Exit codes do not change: removability is
       evidence, not a finding.
     - `GIT_WORKFLOW.md` § Command results: exit 0 reads "Integrated, a
       `CHECK FF` or `CHECK NO_FF` line, or a preflight `PUSH` verdict";
       exit 1 reads "a preflight `STOP` verdict" in place of "a preflight
       `STOP` or cleanup `KEEP` verdict" (`--check`'s stop is the
       preflight's `STOP <TOKEN>:` line).
     - Docs: `git_preflight.py` docstring and usage, `atc_cli` summary line,
       `adapters/python/README.md` command table. The conformance README
       keeps `REMOVABLE`: `cleanup_preflight()` still returns it.
     - Tests: the alias's `--check` line and exit code for an `FF` topic,
       its `--json`, the NOTE line, each `USAGE` refusal, `preflight
       cleanup` refused as a usage error, and `audit`'s `removable` for a
       merged clean, an unmerged and a dirty worktree. Existing tests that
       change: `test_git_preflight.py`'s two `merge` CLI tests (the old
       JSON keys, `NO_REVIEWED_SHA` at exit 1, the stop on stdout) move to
       the alias's forms; `test_atc_cli.py`'s subcommand list drops
       `preflight cleanup`; `test_hami_worktree.py`'s exact audit evidence
       gains `removable`.
     - Side effects (beyond the entry's): a script that parsed `FF
       FAST_FORWARD` from `preflight merge` now reads a `CHECK` line; none
       in this repository or the skills does. `--check` takes no slot and
       creates no candidate, so the alias stays read-only apart from the
       fetch.
8. Drop `--verify-command` and `--verify-command-file`; the five test files
   that pass them (`test_git_integrate.py`, `test_integrate_task.py`,
   `test_integrate_moving_main.py`, `test_testkit_evidence.py`,
   `test_testkit_verifier.py`) declare a verifier in their fixture
   repository; `integrations.jsonl` records the verifier's source (argv
   file, timeout, whether `--verify-timeout` overrode it).
   `test_inline_verifier_other_than_the_argv_file_does_not_reuse` keeps its
   case by rewriting the record's argv instead of passing an inline one.
   D.8 lands before D.21's evidence template, which then integrates
   through the argv file.
   - Side effects: the vendored `GIT_WORKFLOW.md` names the flags until the
     final sync; slower fixtures; a new key in the record.
   - Mitigations: C.1 already forbids the flags; the function-scoped
     repository builder commits the `## Integration verification` section,
     so no module-scoped fixture regroups a module under D.23 (re-measured
     after); readers of `integrations.jsonl` ignore unknown keys.
9. `atc` sets UTF-8 for every child it starts (Owner's ruling A). Its
   console entry (`atc_cli.main` called with no `argv`, the `atc` script
   and `python -m atc_cli`) sets `PYTHONIOENCODING=utf-8` in its own
   environment before dispatch, so every child inherits it: the
   verifier, `git`, `uv` and the scripts' Python children. The testkit's
   isolated environment sets the same value for each stage and static
   check, so the pre-push gate's stages get it too; `ci --stages`, which
   builds no isolated environment, keeps the caller's (CI sets none). The
   display-only decoders `testrun.py:118` and `git_integrate.py:312-313`
   stay `utf-8` with `errors="replace"`. `PYTHONUTF8` stays unset, so a
   text-mode `input=` to a Python child is still encoded in the locale
   while its output is decoded as UTF-8: a caller that sends text passes
   `encoding="utf-8"` explicitly.
   - Side effects:
     - Non-Python children (`git`, `uv`, `sh`, `pwsh`) ignore the
       variable.
     - A caller's own value is replaced; the PowerShell tool sets
       `utf-8:surrogateescape`, under which the testkit's child tests
       fail.
     - A project verifier that relied on the inherited value.
     - Tests that call `atc_cli.main` in-process.
     - A Python child writing to an inherited console.
   - Mitigations:
     - Their bytes reach the replacing decoders, so a non-UTF-8 byte
       shows as U+FFFD in display text and changes no verdict.
     - Replacing that value is the point; the `env -u PYTHONIOENCODING`
       guidance in the adapter README and memory goes once D is
       integrated.
     - `TESTKIT.md` § Isolated environment states the value.
     - Tests pass `argv`, so the test process's environment stays as it
       is.
     - Python writes a console through the console API whatever the
       variable says; only pipes and files change.
10. A short `--check` output: the `CHECK <verdict> <reason>` line (now
    also naming project, branch and the 12-character tip), every `WARNING`
    line, and the `next:` line on a stop; the evidence block moves to
    `--json`.
    - Governance, on the Owner's authorization (「授權，併進第一批」, for
      that sentence only): `GIT_WORKFLOW.md` § Integrate's "prints the
      verdict, a reason code and the evidence" becomes the `CHECK` line with
      the full evidence behind `--json`.
    - Side effects: the vendored `atc-review` skill in every project reads
      `CHECK FF` and `CHECK NO_FF` and quotes every `WARNING` line
      (`projects/hami-quant/.claude/skills/atc-review/SKILL.md:166-175` and
      the same lines in each project); readers of the evidence block.
    - Mitigations: the `CHECK ` prefix, the verdict word right after it and
      the `WARNING` lines keep their form until the CLI trim after the
      final sync; readers of the evidence use `--json` (B.3).
11. `atc worktree cleanup` given `--expected-head` outside its modes names
    the modes that take it (`--abandon`, `--squash-integrated`).
    - Side effects: error text changes.
    - Mitigations: tests assert the token, not the prose.
12. `atc test run`:
    - The new behaviour belongs to the flag form
      (`atc test run --tree <tree> --scratch <name> [--python <py>]
      [--select <base>] [--label <label>] [-- <pytest args>]`). The
      positional form keeps its `<python>` argument and its fifteen-line
      tail, and `atc test mutants` keeps its quiet runs and its verdict
      lines; both stay byte-identical, pinned by their tests, while
      `execute` and `_capture` gain the log and summary path.
    - Python defaults to the tree's `.venv`; a missing one stops with
      `VENV_MISSING`, never system Python.
    - Ruling on the selector: `--select <base>` lives in atc-testkit and
      runs the argv of a new optional `[tool.atc-testkit]` key `selector`,
      a command that takes the changed paths as arguments and prints the
      selected test paths as JSON. The testkit runs that argv with the
      tree's `.venv` Python as its interpreter and the tree as its working
      directory, so the argv starts with the script path relative to the
      tree. The root adapter declares
      `selector = ["scripts/select_tests.py", "--json", "--paths"]` (D.15).
      A tree without the key stops with `SELECTION_UNDECLARED` and runs
      nothing; it is one of the pre-run refusals, exit 9, listed in
      `TESTKIT.md` § Commands `atc test run` step 1. The gate stays
      untouched. The other design, a default to the gate's `--paths` mode,
      adds a `--json` form to the gate, a re-vendored release whose first
      push re-runs each project's stages, and detection of an old gate that
      rejects `--json`; the declared key needs no gate change, and nothing
      in the testkit rejects an unknown `[tool.atc-testkit]` key.
    - The changed paths are `git diff --name-only <base>` plus
      `git ls-files --others --exclude-standard --full-name`, both relative
      to the repository's top level as `select_tests.py` expects, so new
      untracked files count. They run in the tree, or, for a scratch export,
      in the source worktree and commit its registry entry records (D.13).
      It prints the base and the count.
    - The console prints the log path at start, then one summary line
      (counts, failed test names, log path); the full log, pytest's dots
      and the summary line `-q` hides stay in the log in the run directory,
      written live
      with `PYTHONUNBUFFERED=1`. `--progress` streams for humans. An
      incomplete run reports `TIMEOUT` or `INTERRUPTED` with the last lines
      and never a pass. A log held open blocks cleanup with a one-line
      report, without retry or hang. The child encoding follows D.9. The
      positional form stays until the CLI trim after the final sync.
    - Side effects: runs that used system Python stop; disk use for logs;
      a selection that misses a test; output an agent read in the console
      is now in a file; `atc test mutants`, which shares `execute` and
      `_capture` (`testrun.py:284`) and which the vendored `atc-review`
      skill uses (`references/mutants.md` in four projects); a project
      gets `--select` only once it declares `selector`, which is its own
      change after the final sync.
    - Mitigations: `VENV_MISSING` names `atc tmp checkout` and the project's
      `AGENTS.md` preparation; logs live in the scratch and go with it; the
      verifier still runs the full suite; the summary names each failed
      test and the log; mutants and the positional form keep their output,
      pinned by tests; an undeclared tree stops with `SELECTION_UNDECLARED`
      instead of a guessed selection, and its positional runs are
      unchanged.
13. `atc tmp checkout <sha> --task <ID>` exports the commit, runs
    `uv sync --locked` and registers the scratch with its task, source
    worktree and commit, so `atc test run` takes the scratch name.
    - Side effects: network and cache use; a tree without `uv.lock`.
    - Mitigations: the scratch is registered before the sync, so a failed
      sync is still cleanable; a tree without `uv.lock` stops naming the
      project's `AGENTS.md` preparation.
14. `--reviewed` and `--rebased` take a unique prefix of 12 or more
    characters, record the full SHA and refuse an ambiguous one.
    - Side effects: a prefix that becomes ambiguous later.
    - Mitigations: resolved once with
      `git rev-parse --verify <prefix>^{commit}` in the integration
      checkout; `AMBIGUOUS_SHA` stops the run; every record and output line
      carries the full SHA.
    - `atc worktree cleanup --reviewed`, `--expected-head`,
      `--expected-main` and `--integrated-as` stay full-SHA only
      (`hami_worktree.py:1025-1031`): they bind a deletion, and their values
      come from `--json` or `integrations.jsonl`, which carry full SHAs.
    - Design (batch 2):
      - `git_integrate.resolve()` checks the form first: 12 to 40
        hexadecimal characters, case-insensitive, else
        `REVIEWED_SHA_INVALID` naming the form. After the fetch, a helper
        `resolve_commit(repo, value, flag)` runs `git rev-parse --verify
        --quiet <value>^{commit}`. A result that does not start with the
        value (a ref whose name is hex wins over an abbreviated ID, as in
        `merge_preflight()`; also an annotated tag's prefix, which peels to
        its commit: the flag names a commit) or no result is
        `REVIEWED_SHA_UNKNOWN`, unless `git rev-parse
        --disambiguate=<value>`, which lists objects of every type, holds
        two or more that `git cat-file -t` calls `commit` or `tag`: that is
        the new stop `AMBIGUOUS_SHA`. Its detail lists them at their
        shortest distinguishing length, longer than the value and below 40
        characters so the human output's 12-character rewrite leaves them
        whole; its `--json` evidence lists them in full. Exit 1, before any
        candidate; `gp.REASONS` and `gp.NEXT` gain it ("run again with a
        longer prefix or the full SHA the formal review named").
      - `gp.REVIEWED_SHA` (7-40) and its `REVIEWED_SHA_INVALID` reason text
        become 12-40, the one form both commands state; `merge_preflight()`
        receives integrate's resolved full SHA.
      - `--rebased` resolves through the same helper. From there on
        `reviewed` and `carried_from` hold the full SHA, so the record, the
        authority check, the candidate and `--json` carry it.
      - `atc verify` shares `resolve()` and so takes the prefix too.
      - Help text: "full SHA, or a unique prefix of 12 or more characters".
      - `GIT_WORKFLOW.md` § Integrate: the usage line's `--reviewed <full
        sha>` becomes `--reviewed <sha>`, and the sentence reads "Name the
        reviewed commit by its full 40-character SHA or a unique prefix of
        at least 12 characters, which the run resolves once to the full SHA
        the integration record holds; an ambiguous prefix stops."
        § Command results: the first stop row reads "No reviewed SHA, or one
        that is invalid, ambiguous or names no commit".
      - Tests: a 12-character prefix integrates and its record holds the
        full SHA; `--rebased` by prefix; 11 characters is
        `REVIEWED_SHA_INVALID`; an unknown prefix is `REVIEWED_SHA_UNKNOWN`;
        `AMBIGUOUS_SHA` against real Git with the minimum length patched to
        4 (Git's own floor for an abbreviation), over two commit objects
        whose IDs share four characters, found by hashing candidate commit
        bodies in Python and written with `git hash-object -t commit -w`;
        `atc verify` by prefix. `test_git_integrate.py`'s test that a
        12-character prefix is `REVIEWED_SHA_INVALID` ("must be the full
        40-character SHA") becomes the 11-character case.
      - Side effects: none beyond the entry's; a full SHA behaves as now.
15. `select_tests.py` prints a summary by default (selected n of m, one
    reason line per changed path); the per-test list only with `--explain`;
    a new `--json` flag prints the selected test paths as a JSON array, the
    form D.12's `selector` runs.
    - Side effects: callers that read the per-test list.
    - Mitigations: its only reader today is `tests/test_select_tests.py`,
      which moves to `--explain` and `--json`.
16. The output proposals 2, 3, 5, 9, 12 and 13 of Tool output, with the
    side effects and mitigations listed there.
17. D.9, D.12, D.20, D.21 (the `atc_testkit_reap_seconds` ini option) and
    D.23 (the stages' scheduler and the plugin's grouping) and D.26 (the
    private desktop by default, the plugin's desktop guard,
    `atc test private`) change atc-testkit source, so D bumps
    `atc_testkit.__version__`, updates `adapters/python/atc-testkit/README.md`
    to the new version and builds the release wheel, as that README
    requires for every source change.
    - Side effects: projects run two testkit versions for a while; a
      project's evidence records the testkit version, and a record from
      another version covers nothing (`TESTKIT.md` § Pre-push gate, the
      testkit README's reuse rules), so the upgrade re-runs each project's
      stages once.
    - Mitigations: projects stay on their vendored 0.8.1 until an explicit
      upgrade, which is their own task at the final sync; the root's `atc`
      runs `atc test run` from source, so projects get the flag form,
      `--select` and the log without re-vendoring; the one re-run of the
      stages is the upgrade's expected cost and is named in each project's
      upgrade task. `atc test run`'s private default and
      `atc test private` reach projects through the root's `atc` at once;
      the desktop guard is in the plugin, so it arrives with the upgrade,
      and hami-quant-trading's upgrade task routes its
      `verify_*_mutations.py` through `atc test private` in the same
      change.
18. Done ahead of D. `select_tests.py` selects, for the root's own guidance
    and skills and for every Markdown file outside `shared/`, only the test
    files declared to read them. The guarded paths are
    `shared/governance/`, `shared/skills/`, the rendered copies
    `.claude/skills/`, `.agents/skills/` and `.dsh/skills/`, and every
    Markdown file outside `shared/` and outside a `.venv` environment (the
    root's top-level Markdown, `docs/`, the adapter READMEs). Each reader
    is declared in `DIRECTORY_READERS` with the directories and files it
    reads in this checkout; an entry matches a path equal to it or below
    it, so a root file such as `AGENTS.md` selects its reader; entries and
    paths compare with any case, separator or trailing slash. A guarded
    Markdown file or directory selects its declared readers and nothing
    through the import reach: neither the area name (`shared/governance`,
    `shared/skills`) nor the bare name (`SKILL.md`, `AGENTS.md`,
    `CLAUDE.md`, `README.md`) matches through it. A guarded file of another
    kind adds the test files whose reach names its file name. A guarded
    Markdown path with no reader selects nothing; a guarded path of another
    kind with none selects the full suite. Markdown under another
    `shared/<area>/`, `shared/conformance/` among them, keeps the area
    rule; every other path keeps its rule.
    - Readers. A full run traced every file open, directory listing, copy
      and child command; the scripts that tests run as children were read
      for the paths they derive from their own location.
      - `test_tooling.py` reads `shared/skills/` and `.dsh/skills/` in
        process.
      - `test_integration_verify.py` reads `AGENTS.md` in process.
      - `test_shell_install.py` runs `scripts/link-skills.sh` and
        `scripts/check-skills.sh`. Both set `ROOT` from their own location
        and read this checkout's `shared/skills/`, the three rendered
        copies, `shared/governance/*.md` and `CLAUDE.md`; `link-skills.sh`
        also reads `shared/templates/PROJECT_AGENTS.md`. It is declared for
        `SCRIPT_READS`: the guidance, the skills and their rendered copies,
        the root's top-level Markdown and `shared/templates/`.
      - The other scripts that tests run as children read nothing guarded
        in this checkout: each child `validate_workspace.py` gets `--root`
        of a fixture, the conformance tools read `{input}` and
        `shared/policies/schemas/`, `worktree_cd_guard.py` reads
        `worktrees/`, and `check.ps1` runs a fake `uv`.
    - Selection, before → after, of 37 test files:
      `shared/governance/VERIFICATION.md` 21 → 1;
      `shared/skills/atc-review/SKILL.md` 21 → 2;
      `.claude/skills/atc-review/SKILL.md` 21 → 1; `AGENTS.md` 22 → 2;
      `CLAUDE.md` 21 → 1; `adapters/python/scripts/select_tests.py` 1 → 37.
    - Side effects:
      - A test that starts reading a guarded path without a declaration is
        missed by the selection.
      - The selection is what writers run (`VERIFICATION.md` § Testing,
        `adapters/python/README.md` § Selected tests) and what a local
        pre-push gate runs. This repository wires no pre-push gate to
        `select_tests.py`, and projects run their own selections, so the
        effect is the writer's and reviewer's run of a guarded path; the
        integration verifier still runs the full suite.
      - The root's own check of governance, rendered copies and `AGENTS.md`
        is `validate_workspace.py`, which the integration verifier and CI
        run and the selection does not.
      - The guard below makes `conftest.py` import `select_tests.py` when it
        loads, which loads `atc_testkit.selection` and `atc_testkit.git` in
        every session. Those three join the modules every session loads, so
        a change to any of them selects the full suite.
    - Mitigations:
      - Guard. `conftest.py` records, through an audit hook and by test
        file, every file open, directory listing and copy under a guarded
        path of this checkout. It counts any child command that runs this
        checkout's `scripts/*.sh` as a read of every guarded path, since
        the hook cannot see what a shell script opens. At the session's end
        a read missing from `DIRECTORY_READERS` fails the run and is named.
        It checks the test files of the session, so a selected run that
        includes a reader checks it and the integration verifier's full
        suite checks them all before `main`.
      - A guard self-test runs a test that starts an undeclared child of a
        `scripts/*.sh` stand-in and one that opens an undeclared guarded
        file in process, and proves each fails the run.
      - A selector test proves a root file entry in `DIRECTORY_READERS`
        (`AGENTS.md`) selects its reader.
      - Not traced: existence checks (`os.stat`, `Path.exists`,
        `Path.is_file`) raise no audit event, and a child Python process
        is not followed. An existence check on a guarded path is not
        traced; a change that only adds or removes such a file can break it
        unselected, and none exists in this checkout; the guard self-test
        does not cover it. Each child Python run measured passes a fixture
        root.
      - A selection reason for a guarded path names `validate_workspace.py`,
        which a writer runs as `uv run --locked atc validate` after touching
        it.
      - The selector, `atc_testkit.selection` and `atc_testkit.git` run the
        full suite on their own edits, which is the strongest check of a
        selection change.

19. Done ahead of D. The root's integration verifier follows
    `VERIFICATION.md` § Full regression timing and evidence reuse: local
    integration runs the impact selection, and the full suite runs in a
    dispatched CI run. `atc_testkit` stays as it is; the root's
    `integration_verify.py`, `select_tests.py`, `tests/root_reads.py` and
    their tests change, and since `conftest.py` loads the selector and the
    guard (D.18), that range itself verifies with the full suite. A later
    range that changes only `scripts/integration_verify.py` runs the three
    test files that reach it (`test_integration_verify.py`,
    `test_select_tests.py`, `test_testkit_gate.py`), since that script is
    outside the load set; `test_integration_verify.py`'s classification
    tables pin the predicate.
    - Mechanism:
      - The skeleton's root form (`docs/TESTKIT.md` § Integration verifier,
        a `Project` with no `select`) takes the test command and the
        documentation-only predicate as the root's data. It has no
        selection hook: `select` turns on the consumer form, with stages,
        lock, contract, lint and collection rows and a report the root does
        not carry. So the root keeps the root form and changes its data.
      - Test command: `uv run --locked python scripts/select_tests.py
        --base <ATC_EXPECTED_MAIN_SHA> --run --`. The skeleton appends
        `--basetemp <dir>`, which reaches pytest after `--`. In the clean
        candidate at `ATC_INTEGRATION_SHA` the selector's range, from the
        merge base of the base and `HEAD` to the working tree, is exactly
        `ATC_EXPECTED_MAIN_SHA..ATC_INTEGRATION_SHA`: the skeleton has
        checked the expected main is an ancestor, and its final-state check
        refuses a dirty tree. An empty selection exits 0 without pytest; a
        path the selector widens runs the full suite.
      - The base reaches the command through the environment the verifier
        already reads: `main(environ, root, run)` builds
        `project(root, environ)`, and `project()` puts
        `--base <ATC_EXPECTED_MAIN_SHA>` in the command when the variable is
        present. `iv.project(WORKSPACE)` without an environment, as the
        import test calls it, builds the command with the selector's
        default base `main`; the verifier never reaches that form, since
        `require_env` refuses a missing variable before any check runs.
      - Documentation-only, by consumers (`VERIFICATION.md`
        § Documentation-only verification): the predicate builds one
        selector graph and calls `select_tests.select(paths)` over the
        range's paths, not `changed_paths`. The range is documentation-only
        when every path is Markdown under `docs/`, a task record under
        `.atc/tasks/`, or Markdown the selection maps to no test file. Such
        a range skips the test command; the static checks
        (`validate_workspace.py`, `policies.py check`, `git diff --check`)
        run for every range.
      - The root's top-level Markdown joins D.18's guarded paths, so it
        selects its declared readers instead of every test that names a
        `README.md`. A full run traced with all of them guarded: in process
        only `test_integration_verify.py` reads `AGENTS.md`. D.18's rule
        stands that a child run of `scripts/*.sh` reads every guarded path
        (the scripts build paths as `"$ROOT"/…` and from a `$tool`
        variable, which no literal check follows), so `test_shell_install.py`
        is declared for the new ones too and each top-level Markdown file
        selects it: `README.md`, `README.zh-TW.md`, `GLOSSARY.md` and
        `SCOPE.md` are not documentation-only and run that one test file.
      - `.github/workflows/validate.yml` stays `workflow_dispatch` only.
        Root `AGENTS.md` § Integration verification states what the local
        verification covers: the static checks, the selection, the
        documentation-only classes above, and that the skeleton's line
        `classification: full` names every range that is not
        documentation-only, the selector's lines after it saying what ran,
        until D.17's version bump renames it. It states that the full suite,
        with D.18's `ROOT_READ_UNDECLARED` guard over every test file, runs
        only in a dispatched run of that workflow, which the Owner starts
        after topic D.
      - Edits: `integration_verify.py`; `select_tests.py` (`GUARDED` gains
        the top-level Markdown, the docstring's line 3-4 "the integration
        verifier runs the full suite" becomes the selection over the range);
        `adapters/python/README.md` § Selected tests (its "the integration
        verifier alone runs the full suite" becomes the dispatched CI run)
        and its timeout paragraph; root `AGENTS.md` § Integration
        verification and its `Verifier timeout`; `docs/TESTKIT.md`'s
        root-form paragraph (the root's predicate and test command are its
        data).
      - Tests that pin today's behaviour and move: in
        `test_integration_verify.py`, `name()` and the fake run (`"pytest"
        in argv`, lines 37 and 62), the documentation-only table (`README.md`
        and `AGENTS.md` not documentation-only), the move case expecting
        `classification: full` with `pytest` started (171), the
        failing-check and failing-suite cases keyed on `pytest` (194-196,
        212, 223), and the CI parity test (257-264), which becomes: CI's
        job runs the full `uv run --locked pytest` and the two static
        checks, and the local verifier runs the same static checks with
        the selection in place of pytest. In `test_select_tests.py`, the
        guarded-reader table (line 81) gains the top-level Markdown rows.
        New cases: a task record with a prose file, a mixed range, a
        selection that widens, an empty selection that runs no pytest.
    - Verify time, measured on this host:
      - `README.md` only: before, the full suite after about 1 s of static
        checks; after, the static checks, the selection and
        `test_shell_install.py`, 19 s for the verifier run.
      - `select_tests.py` only: before and after, the full suite, 822 s for
        the verifier run, since `conftest.py` loads the selector (D.18).
      - One guarded skill file: before, the full suite; after, the static
        checks and 2 of 38 test files.
      - The parts measured apart: `validate_workspace.py` 0.5 s,
        `policies.py check` 0.4 s, the selection 1 s, `git diff --check`
        0.1 s; the candidate's environment build is the same before and
        after.
    - Side effects and mitigations:
      - A selector blind spot (a test reading what the graph does not see,
        undeclared) reaches `main` and shows only in a dispatched CI run.
        Mitigations: every path no rule maps still runs the full suite;
        D.18's guard checks the selected tests in every integration run.
      - An undeclared reader of a guarded Markdown path (the guidance, the
        skills, or any Markdown file outside `shared/`) is never selected
        for that path, so the guard never runs it on such a range locally.
        This is an accepted residual, caught by the Owner's CI dispatch
        after topic D. No static scan is added: tests write fixture files
        under the same names (`AGENTS.md`, `SKILL.md`), so a scan of test
        source would flag fixtures as readers.
      - Guard timing: an undeclared reader among the tests a range does not
        select fails only in the dispatched CI run. The guard names the test
        and the paths, so the fix is one declaration.
      - `VERIFICATION.md` § Testing and § Independent review named the
        integration verifier's full suite; with the Owner's authorization
        both now point to § Full regression timing and evidence reuse:
        local integration runs impact-selected checks unless a project
        requires a local full regression, and the full suite, by default
        the project's manually dispatched remote CI, is the safety net.
        The root's verifier does not read the projects' vendored copies
        (`validate_workspace.py` runs without `--projects`), and those 29
        copies keep the old wording until the final sync.
      - `integrations.jsonl`: the root's line records task, authority,
        candidate, `main_before`, `records_only` and the reviewed SHA, with
        no verification field, and the root writes no verification report,
        so no recorded field changes meaning. The verifier's log carries
        the classification and the selection.
      - Timeout: `adapters/python/README.md` derives it as three times the
        measured full-run wall clock in the verifier, rounded up to a whole
        minute and never below 300 s; 3 × 822 s = 2466 s gives 2520 s,
        which replaces 1800 s in root `AGENTS.md`, since the verifier still
        widens to the full suite (`conftest.py`, the selector,
        `pyproject.toml`, an unmapped path).
      - The addition where the verifier subtracts what the writer
        already ran (and the coordinator's evidence run during review)
        is not taken into D, re-checked against D.19 and D.20:
        - The root: its verifier runs only the impact selection through
          the stages, and the root writes no evidence a writer's run could
          match; nothing is left to subtract.
        - Projects: reuse already runs from the verifier's evidence to the
          pre-push gate (`TESTKIT.md` § Pre-push gate). Subtracting the
          writer's run needs writer-side evidence `atc test run` does not
          write, at the exact candidate SHA (fast-forward only), and trusts
          the writer's environment, which the verifier is there to replace
          (`VERIFICATION.md` § Independent review).
        - The time it would save is bounded by D.22's budget; D.21 and
          D.23 cut the same time with no trust cost.
        - It returns only if a project's verifier stays over budget after
          D.21 and D.23, as part of D.22's exception path. The Owner ruled
          it dropped.
      - Markdown that the selection maps to tests by a bare name stays not
        documentation-only (`adapters/python/README.md` selects the five
        tests that name some `README.md`); this errs toward running tests.

20. The root runs its tests through the testkit's stages (`TESTKIT.md`
    § Markers, § Stages): `parallel` at `-n <workers> --dist loadscope`,
    then `serial` and `interactive` at `-n 0`, the `serial` and
    `interactive_desktop` markers placing a test. The parallel stage's
    worker count gains two overrides; its default stays
    `min(8, cpu count)` for every project and CI.
    - Measured on this host (16 physical cores, 24 threads), full suite:
      one serial invocation 791 s, one invocation at `-n 8`
      (`--dist load`) 184 s and at `-n 16` 136 s, each 1339 passed and 20
      skipped; the three stages through
      `python -m atc_testkit.ci --stages` at the default 8 workers 232 s,
      every stage exiting 0 (`serial` and `interactive` collect none;
      the doubled `-q` below hid the count). A second staged run split it:
      232 s again, the parallel stage 228 s, the empty serial stage 1 s,
      the empty interactive stage 2 s; the parallel stage's marker
      expression alone at `-n 8 --dist load` took 174 s (1339 passed, 20
      skipped). So the three runner starts cost about 3 s and `loadscope`
      about 54 s: it keeps a module on one worker, and the heavy modules
      (`test_integrate_task.py` holds a 10.6 s and two 6 s tests) set the
      critical path.
    - `loadscope` stays in this item. It is the testkit's stage contract
      for every project's gate, verifier and CI (`TESTKIT.md` § Stages),
      it keeps a module- or class-scoped fixture built once per module, and
      no project's suite has been measured under `load`. The lever is
      testkit-wide, never root-only: D.23. Small selections: `test_shell_install.py`
      (3 tests) 15.7 s serial, 14.5 s at `-n 16`; `test_task_records.py`
      (19 tests) 14.3 s serial, 6.1 s at `-n 16`: the workers' start-up,
      about 4 s, is repaid by a few tests, so every selection runs the
      stages.
    - Markers. No root test needs `serial` or `interactive_desktop`: no
      test opens a port or writes inside the checkout or the real home,
      every shared place has a per-test name (the `atc_tmp` and
      `fake_home` roots, `tmp_path`, the private desktop `atc_<uuid>`),
      the desktop tests choose their desktop through `desktop.start` and
      assert on their children, and the suite passed at `-n 8`, at `-n 16`
      and in the staged run, the parallel stage on a private desktop. A
      test that later fails under load or needs the interactive desktop
      takes the marker.
    - What is missing today, and where each piece lands:
      - Root config. The suite's command in `adapters/python/README.md`,
        `check.ps1 -Tests` and the CI job's `uv run --locked pytest` become
        `uv run --locked python -m atc_testkit.ci --stages`, and CI adds the
        `--slow` invocation (`TESTKIT.md` § Stages; it passes on no `slow`
        test). The runner writes its stage logs to `adapters/python/.tmp/`,
        which the root's `.gitignore` gains. A bare `uv run --locked pytest`
        stays one invocation for a targeted run, as `ci` without
        `--stages` is.
      - Root script. `select_tests.py --run` runs
        `python -m atc_testkit.ci --stages <paths> <arguments after -->`
        in place of one pytest; the full suite passes no path.
      - Testkit. The verifier's root form runs one test command "in place
        of the stages" (`TESTKIT.md` § Integration verifier). It gains the
        stages: the root's data becomes a selection function over the
        range (the selector's paths, `[]` for the full suite, `None` for
        none) and the directory the tests run in, and the verifier runs
        its stage method there, each stage with its own environment, base
        directory, data root and log in the working directory, the
        interactive stage off the private desktop. `Project.test_command`
        and `TestCommand` go; the root is their one user. The projects'
        vendored 0.8.1 wheels still carry both until the D.17 upgrade, and
        no project code names them (no match outside the wheels in the
        three checkouts), so the upgrade removes them with nothing to
        migrate.
      - The root's `serial` and `interactive` stages are kept although they
        collect nothing today: the stage list is the testkit's, so a test
        that takes either marker runs in its stage with no root change, and
        an empty stage passes (`TESTKIT.md` § Stages). Their cost is a
        runner start and a collection each, 1 s and 2 s here.
      - Testkit. The worker count (below).
    - Worker count:
      - Precedence: `--workers N` on the command, then `test_workers` in
        `~/.atc/config.toml`, then `min(8, cpu count)`. One function in
        `atc_testkit.stages` resolves it, and every stage start calls it.
      - The flag: `ci --stages --workers N` keeps its form; the gate's
        `--run` and D.12's flag form of `atc test run` gain it. The
        verifier takes none: its argv is the project's declared one and
        `atc integrate` adds nothing to it, so the host file is its
        override.
      - The host file: `~/.atc/config.toml` beside `tmp/` and `cache/`
        (`ATC_HOME.md` § Layout gains it), outside every repository,
        written by hand, read with `tomllib`. With `CI` set it is not read,
        as `ATC_HOME.md` § Location keeps CI on its own locations. It is
        read in the entry process before the stage environments point
        `HOME` into the run directory, so a test's child runner never sees
        it.
      - Validation: an integer from 1 to the logical CPU count, never
        clamped; the flag takes ASCII digits only, the key a TOML integer
        only (a TOML string is refused). A bad flag stops with `USAGE`; a
        file that cannot be read or decoded as UTF-8, or a bad key, stops
        with `ATC_CONFIG_INVALID` naming the file and key; both before any
        stage or static check runs. A missing file or key is the default.
      - Output: the parallel stage's first console line is
        `workers: <n> (<flag|~/.atc/config.toml|default>)`; each stage log's
        header already carries the command with `-n`. The evidence record
        holds a stage's name, marker expression and ignored paths, not the
        count, so a record from another count covers the push as before.
      - CI passes its own `--workers` (`TESTKIT.md` § Stages) or takes the
        default; the host file never reaches it. hami-quant's CI runs
        `ci --stages --workers 4` and hami-quant-trading's
        `scripts/ci_pytest.py --stages --workers 4`; both keep their count.
        The root's CI job passes none and takes `min(8, cpu count)` of its
        runner.
    - Side effects and mitigations:
      - The three projects run the vendored testkit 0.8.1 until their
        upgrade at the final sync (D.17), and 0.8.1 reads no host file and
        takes no `--workers` on the gate, so until then a host file reaches
        no project: it reaches the root's runs only, the root running the
        testkit from source. After the upgrade the default is unchanged and
        a host with no `~/.atc/config.toml` runs as today, while a host
        file applies to every project's gate, verifier and runs on that
        host at once, including hami-quant's load-sensitive tests; the flag lowers
        it for one run, and a test that fails under the higher count takes
        `serial`.
      - The stages' worker count reaches `atc test run`'s flag form only:
        D.12 changes from one `-n 0` invocation to the stages over its
        selection, logged per stage in the scratch; the positional form and
        `atc test mutants` keep `-n 0` and their output, pinned by
        `test_xdist_is_turned_off_when_installed_and_the_cache_provider_always`.
      - Temporary directories and children: each stage has its own base
        directory; each worker gets `<basetemp>/popen-gw<N>` below it
        (xdist `workermanage.py`); the plugin's `CHILD_PROCESSES_ALIVE`
        check runs in the controller over the whole base directory after
        the workers end. No test proves that under xdist, so
        `test_a_surviving_child_fails_the_run_with_its_token` gains an
        `-n 2` case.
      - Environment: workers carry `PYTEST_XDIST_*` into the children tests
        start; nothing in `atc_cli`, `atc_testkit` or `scripts/` reads
        them, and every child pytest runs in a fixture root with its own
        ini, passing `-n 0` or dropping `PYTEST_*`.
      - The root-read guard (D.18) hands each worker's failures to the
        controller, proven by
        `test_an_undeclared_open_fails_the_session[xdist]`; it now runs once
        per stage, and a stage that collects no test reads nothing.
      - The root verifier: the documentation-only predicate, the static
        checks and the classification line stay; its log gains one file per
        stage; `NO_TESTS_COLLECTED` refuses a selection whose stages all
        collect nothing, which a selected test file never does.
      - Output: the stage's `-q` on top of the root's `addopts` `-q` hides
        pytest's count line in the stage logs, so the root's `addopts`
        drops `-q`, and a bare `uv run --locked pytest` prints its file
        lines again; failure lines, `ROOT_READ_UNDECLARED` and
        `CHILD_PROCESSES_ALIVE` keep their form, and a failing stage names
        its log with its last twenty failure lines.
      - Load-sensitive deadlines run beside other workers:
        `test_git_integrate.py`'s verifier timeouts, `test_testkit_children.py`'s
        `timeout=3`, `test_integration_slot.py`'s `wait(10)`, the plugin's
        10 s survivor window. All passed in every run above; a failure
        under load widens the ceiling and keeps the assertion, or takes
        `serial`.
      - Verifier timeout (Owner ruling): a fixed test budget of 30 minutes.
        Root `AGENTS.md` `Verifier timeout` becomes 1800, and
        `adapters/python/README.md` replaces its derivation (three times a
        measured full run) with the budget rule: the budget is not derived
        from a run and not scaled by the worker count; a run that exceeds
        it is a signal to optimise the tests or for the Owner to decide,
        and nothing raises it automatically.
        - Headroom: the staged full run at 8 workers takes about 232 s;
          the stages at `--workers 1` took 825 s (1373 passed, 20
          skipped), measured beside a concurrent full-suite run, so an
          upper bound; D.19 measured the verifier's own steps at about
          31 s, so every worker count stays under 1800 s even under that
          load.
        - `atc integrate`'s timeout stop keeps its token
          `VERIFICATION_TIMEOUT` and its text "candidate verification timed
          out after <n>s for <sha>", which the tests assert, and adds
          "test budget exceeded: optimise the tests or ask the Owner; a
          retry does not help". The installed `atc` runs the root's code,
          so the line reaches every project's integration at once; their
          `VERIFICATION_TIMEOUT` handling reads the token, not the text.
        - Projects' current budgets, read from their `AGENTS.md`:
          hami-quant 3600 s, which exceeds 1800 s; hami-quant-trading
          1800 s; ria-project 600 s. This item changes none of them; D.22
          proposes the rule for every project.
    - Tests: the verifier's root form runs the stages (fake run: three
      stage commands, cwd, per-stage base directory, `None` skips them);
      `select_tests.py --run` starts the stage runner with the paths and
      the arguments after `--`; the worker precedence, `CI` ignoring the
      file, each validation refusal and the `workers:` line; the survivor
      check's `-n 2` case; the CI parity test in
      `test_integration_verify.py` compares the staged runner. Testkit and
      `conftest.py`-loaded changes select the full suite, so the range
      verifies itself through the stages.
    - Edits: `atc_testkit` `stages.py`, `verifier.py`, `project.py`,
      `gate.py`, `ci.py`, `testrun.py` (flag form, with D.12), its tests,
      `docs/TESTKIT.md` § Stages and § Integration verifier,
      `docs/ATC_HOME.md` § Layout; the root's `select_tests.py`,
      `integration_verify.py`, `check.ps1`, `.gitignore`,
      `.github/workflows/validate.yml`, `adapters/python/README.md`,
      root `AGENTS.md` `Verifier timeout`, and `git_integrate.py`'s
      timeout line with its test. D.17's version bump covers the testkit
      change.
21. The slowest tests. Measured on `main` 4338ad7 with
    `ci --stages --durations=0` at 8 workers (`workers: 8 (default)`, 24
    logical CPUs): 1446 passed, 20 skipped in 232 s; the per-test times
    sum to 1132 s (call 839 s, setup 293 s), more than the serial run's
    791 s because eight workers contend. By module: `test_git_integrate.py`
    229 s (98 tests), `test_testkit_evidence.py` 145 s (42),
    `test_hami_worktree.py` 130 s (159), `test_integrate_task.py` 129 s
    (37), `test_integrate_moving_main.py` 77 s (19), the rest under 60 s
    each. Under `loadscope` a module runs on one worker, so
    `test_git_integrate.py` alone is the parallel stage's 228 s (D.23).
    Each fix keeps every assertion:
    - `test_testkit_evidence.py`: its `verified` fixture runs one real
      `atc integrate` per test, 3.6-3.9 s of setup each, 131 s in all. A
      module-scoped fixture builds that repository once under
      `tmp_path_factory`, inside `pytest.MonkeyPatch.context()` with the
      `GIT_*` environment and `tempfile.tempdir` the function-scoped `repo`
      fixture sets, since a module fixture builds before the conftest's
      autouse fixtures and cannot take function-scoped ones. `verified`
      copies it with `shutil.copytree` to the exact path `repo` returns, so
      `record_path(repo, ...)` holds; the evidence record lives under the
      copy's `.git/atc/` and holds digests and SHAs, no path.
      - Side effects: tests share one integration's history; a test that
        changed the template would leak into the next; the module now has
        a module-scoped fixture, so D.23 keeps its tests on one worker.
      - Mitigations: each test reads and writes only its copy, and the
        template is never handed out; one worker running 42 tests of
        about 0.5 s each is well under the stage.
    - `test_testkit_plugin.py::test_a_surviving_child_fails_the_run_with_its_token`,
      two cases of 10.8-11.7 s: the plugin waits its whole 10 s window
      (`children.REAP_SECONDS`) for a child meant to outlive it. The
      plugin reads the window of `lasting_survivors` (not `_end`'s) from
      the ini option `atc_testkit_reap_seconds`, a number above 0 and up to
      the default, refusing any other value at startup; the test passes
      `-o atc_testkit_reap_seconds=1`.
      - Side effects: a project can set the option.
      - Mitigations: a shorter window only fails a lasting child sooner,
        so it never weakens the check; a larger value is refused.
    - `test_shell_install.py::test_managed_directory_is_repaired_whatever_its_file_names`,
      12-22 s: six `sh` runs, each starting many POSIX tools under Git
      Bash, every one asserting a different state. No change: after D.23
      it no longer sets the stage's length.
    - `test_git_integrate.py`, `test_integrate_task.py` and
      `test_integrate_moving_main.py`: 2-10 s each, nearly all in the
      `atc integrate` runs themselves (setup is 0.45 s and 0.69 s a test).
      No change here; cutting the `git` processes one integration starts
      is a product change for its own entry.
    - Expected: the parallel stage at the `load` figure of D.20 (174 s)
      less about 16 s for the evidence fixture; the implementation
      measures it.
22. Batch 2. Proposed, waiting for the Owner's authorization of a
    `shared/governance/` edit: `VERIFICATION.md` makes 30 minutes (1800 s)
    the verifier test budget of every project, with D.20's rule that a run
    over it is a signal to optimise the tests or for the Owner to decide.
    - Side effects: hami-quant declares 3600 s; hami-quant-trading
      (1800 s) and ria-project (600 s) already fit; my-heartopia-tools
      declares no verifier. The four vendored `VERIFICATION.md` copies
      (hami-quant, hami-quant-trading, my-heartopia-tools, ria-project)
      change at the final sync.
    - Exception path for hami-quant, for the Owner to decide with the
      authorization: either its verifier fits 1800 s before the rule
      reaches it (its stages under the testkit's workers, then D.21-style
      work on its slowest tests), or `VERIFICATION.md` lets a project's
      `AGENTS.md` declare a larger budget next to `Verifier timeout` with
      the Owner's recorded approval and the reason, the rule's breach
      signal then counting against that declared budget.
    - Owner's authorization (2026-11-10): 「三項都授權」 for the governance
      edits of D.22, D.14 and D.7; the exception path 「AGENTS.md
      宣告較大上限」.
    - Design (batch 2):
      - `VERIFICATION.md` gains `### Verifier test budget` after § Full
        regression timing and evidence reuse: a project's `Verifier
        timeout` (`GIT_WORKFLOW.md` § Project verification section) is its
        verifier's test budget, at most 1800 s; the budget is fixed, not
        derived from a run nor scaled by the worker count; a run over it is
        a signal to optimise the tests or for the Owner to decide, never a
        retry, and nothing raises it automatically. A larger budget is
        declared in the project's `AGENTS.md` beside `Verifier timeout`
        with a line `Verifier budget approval: <the Owner's recorded
        words and date>; reason: <reason>`; the breach signal then counts
        against that budget. The 300-second default for a project that
        declares no timeout stays (`GIT_WORKFLOW.md`).
      - No code: `atc integrate`'s timeout stop already says "test budget
        exceeded" against the timeout in force. A check that refuses a
        declaration over 1800 s without the approval line would reach
        hami-quant through the installed `atc` before its `AGENTS.md`
        carries the line, so it is not built; the 1800 s cap is a
        governance rule the reviewer checks, not a refusal.
      - `GIT_WORKFLOW.md` § Project verification section, the list of what
        a project's `AGENTS.md` carries, gains the approval line as an
        optional item pointing to the new section (design review finding;
        the `GIT_WORKFLOW.md` edit outside D.14 and D.7's passages is
        reported to the coordinator for confirmation).
      - Root `AGENTS.md`'s `Verifier timeout` note points to the new
        section. `VERIFICATION.md` is canonical for the rule;
        `adapters/python/README.md` drops its restatement and keeps the
        root's facts (1800 s, the 232 s staged run) with a pointer.
      - After D: hami-quant's `AGENTS.md` gains the approval line for its
        3600 s with the final sync, on the Owner's recorded word.
      - Tests: none new; the root's guidance change selects its readers.
23. The parallel stage's scheduler, testkit-wide. `loadscope` keeps a
    module on one worker; the root's 228 s parallel stage is exactly its
    longest module (D.21), against 174 s under `load` (D.20). What
    `loadscope` protects is a module-, class- or package-scoped fixture,
    built once per module (per class for a class's tests) instead of once
    per worker that runs a test of it: the root has one (`test_select_tests.py`'s `graph`),
    hami-quant 40 in 24 files, hami-quant-trading 10 in 9, ria-project and
    my-heartopia-tools none.
    - Change: the parallel stage runs `--dist loadgroup`, and the testkit
      plugin, at collection, marks every test that uses a fixture of
      module, class or package scope with `xdist_group` named after its
      module, its `module::Class` for a class-scoped fixture, or its
      package for a package-scoped one. Its `pytest_collection_modifyitems`
      runs with `tryfirst=True`, before xdist's worker hook
      (`xdist/remote.py`), which reads `xdist_group` and adds the
      `@<group>` suffix the scheduler groups by. Those tests stay on one
      worker as under `loadscope`; every other test spreads as under
      `load`. A test that already carries `xdist_group` keeps its own
      group.
    - Side effects:
      - The plugin reads each item's fixture definitions through pytest's
        `_fixtureinfo`, a private attribute.
      - A fixture of function scope that still shares a resource across a
        module (a fixed path, a port) runs on several workers at once.
      - Projects get the change with their testkit upgrade;
        hami-quant-trading's `docs/plans` name `-n 8 --dist loadscope`.
      - `--dist loadgroup` names grouped tests with an `@<group>` suffix in
        xdist's output.
    - Mitigations:
      - An end-to-end test in `test_testkit_plugin.py` runs a child
        project at `-n 2 --dist loadgroup` and asserts that a
        module-scoped fixture is built once and that the node IDs carry
        `@<group>`; a missing attribute stops collection with the
        testkit's token rather than ungrouping silently.
      - The marker contract already places shared-resource tests in
        `serial`, which runs `-n 0`; the root's run under `load` (D.20)
        passed; each project's upgrade task (D.17) runs its suite under
        the new scheduler before it lands.
      - An explicit mark wins, and the plugin adds none beside it; D.17
        updates hami-quant-trading's plan text with its upgrade.
      - The evidence record names no scheduler, so reuse is unchanged;
        the suffix is display only.
24. `atc integrate --repo .` prints every `.` in its human output as the
    project name (`integration_verifyrootpy`, `AGENTSrootmd`): the
    human stream replaces `str(target.repo)`, which is `.`. It replaces
    only the resolved absolute path, and the given path only when it is
    absolute.
    - Side effects: a relative `--repo` other than `.` shows as given in
      place of the project name.
    - Mitigations: the `CHECK` and result lines name the project from
      the target, not from the path; a test runs `--repo .` and asserts
      that `integration_verify.py` and `AGENTS.md` print unchanged.
25. Folded LOWs:
    - `onboard_project.check_project_checkout` compares
      `git rev-parse --show-toplevel` with the project path through
      `os.path.abspath`, which keeps a Windows 8.3 short name
      (`ANDYLI~1`) while Git prints the long one, so a short-named path
      stops with `NOT_A_REPOSITORY`. Both sides go through
      `os.path.realpath`.
      - Side effects: a project path that is a link or junction now
        compares by its target.
      - Mitigations: both sides resolve alike, so a link to the checkout
        top still matches and a link into another repository still
        stops; the test takes the short name from `GetShortPathNameW` and
        skips on a volume without 8.3 names.
    - `stages.flag_count` passes a digit string of more than 4300 digits
      to `int`, which raises `ValueError` (Python's integer-string limit)
      as a traceback. Leading zeros are stripped, and digits longer
      than `str(most_workers())` are refused before `int`, so `08` and
      `0016` parse on any host. A bad flag stays a usage error of its
      caller: `ci --stages` prints `USAGE` and exits 2, the gate exits 2
      through argparse; `ATC_CONFIG_INVALID` (exit 9) is the host file's.
      - Side effects: none.
      - Mitigations: tests pass 5000 digits and leading zeros to
        `flag_count`, and a 5000-digit flag to `ci --stages` and the gate.
26. A private desktop by default (Owner's ruling: batch 1). A
    hami-quant-trading mutation script started pytest directly and Tk test
    windows showed on the Owner's desktop; `atc test run` and
    `atc test mutants` use a private desktop only with
    `ATC_PRIVATE_DESKTOP=1`.
    - `atc test run` and `atc test mutants` run on a private desktop on
      Windows unless `ATC_PRIVATE_DESKTOP=0`, and on a private one exclude
      `interactive_desktop` tests, printing one line that says so and names
      the opt-out. `0` runs on the interactive desktop with those tests
      included. Unset and `1` are private; any other value is refused with
      `PRIVATE_DESKTOP_INVALID` (exit 9, the code every refusal before the
      run shares; the token tells it apart). The variable stays the one
      switch: the positional form passes every argument after `<label>` to
      pytest, so it has no room for a flag, and D.12's flag form reads the
      same variable.
    - A guard in `atc_testkit.plugin`: on Windows, a session whose own
      desktop is the interactive one (`WinSta0\Default`, read through
      `GetThreadDesktop` and `GetUserObjectInformationW`) refuses a Tk root
      created for an item not marked `interactive_desktop`. A fixture's
      setup and teardown count as part of the item being set up or torn
      down, so a module- or session-scoped Tk fixture set up for a marked
      test passes; a root created at collection, or at session start before
      any item, is refused. `tkinter.Tk.__init__` with `useTk` true, and
      `Tk.loadtk`, raise `INTERACTIVE_DESKTOP_WINDOW` naming the item and
      the two ways out (`atc test run`, or the marker) before Tcl creates
      the window, and the item's report is failed at teardown as well, so
      product code that swallows the exception does not hide it.
      `tkinter.Tcl()` (`useTk=False`) opens no window and passes;
      `Toplevel` needs a root. The plugin imports `tkinter` only when the
      guard is on, and a Python without it has nothing to guard.
      `ATC_DESKTOP_GUARD` overrides the detection: `0` off, `1` on, unset
      automatic; any other value is a usage error (exit 4). A failure, not
      a warning: only a refusal before the window exists keeps the desktop
      clear, which is the incident; a warning reports a window that has
      already shown, and in a passing `-q` run nobody reads it.
    - `atc test private <command> [args...]` runs one command on a private
      desktop, its output streamed and its exit code returned, through
      `atc_testkit.desktop`; `atc_testkit.desktop.run(command, cwd, env)`
      is the same for a Python caller. A project script that starts pytest
      goes through either; routing hami-quant-trading's
      `verify_*_mutations.py` through it is that project's change.
    - Side effects:
      - CI: the `parallel` and `serial` stages already run private; the
        `interactive` stage holds only marked tests, which the guard
        allows. The `slow` invocation runs on the job's desktop, where an
        unmarked slow Tk test would now be refused if that desktop is
        `WinSta0\Default`.
      - The verifier: its stages are the same as CI's; nothing changes.
      - `atc test run` without the variable no longer runs
        `interactive_desktop` tests: a reviewer targeting one sets
        `ATC_PRIVATE_DESKTOP=0`. A run whose selection is only such tests
        exits 5 (none collected).
      - hami-quant-trading: 26 test files and the product's Tk modules
        open Tk roots, and 58 `verify_*_mutations.py` scripts start pytest
        directly. Once it moves its testkit pin, those scripts' runs on the
        Owner's desktop fail with `INTERACTIVE_DESKTOP_WINDOW` (a red
        baseline) until they go through `atc test private`, and a plain
        `pytest` of a Tk test there fails the same way. hami-quant has one
        Tk test file. Their documented `ATC_PRIVATE_DESKTOP=1` still means
        private.
      - A Tk window opened by a child process a test starts is outside
        the guard (it runs no plugin); the private desktop, which the
        child inherits, is what covers it.
    - Mitigations: `ci.py` sets `ATC_DESKTOP_GUARD=0` for the `slow`
      invocation, the one run documented on the job's desktop; the
      excluded-tests line names the opt-out; the guard reaches a consumer
      only with its testkit pin, so hami-quant-trading routes its scripts
      in the same change, and D.17's plan note for it says so. Tests: the
      variable's three forms and the refusal, through `atc test run` and
      `atc test mutants`; the guard in a child pytest forced on with
      `ATC_DESKTOP_GUARD=1` (an unmarked test, a marked one, a swallowed
      exception, a root at collection), skipped without `tkinter`; `0`
      forces it off; `atc test private` returns the child's exit code and
      reports `desktop: private`. `docs/TESTKIT.md` § Private desktop and
      § Commands, and `adapters/python/atc-testkit/README.md`.
27. The reviewer runs the integration verifier before the Owner's word
    (Owner's ruling: batch 1). A hami-quant-trading step passed review and
    got the Owner's word, then integration stopped with
    `VERIFICATION_FAILED` on a test neither targeted set had selected,
    which cost a second word.
    - `atc verify --repo <checkout> --reviewed <full sha>
      [--rebased <full sha>] [--branch <name>] [--json]`: the target
      options of `atc integrate`, without authority. It resolves and
      preflights as `atc integrate --check` does (its fetch updates
      `origin/*`), and takes the verifier's argv file and timeout from
      `AGENTS.md` at the `main` tip it integrates into, as `atc integrate`
      does (`GIT_WORKFLOW.md` § Integration verifier source,
      `declared_verifier`). The candidate build and verification move out
      of `integrate()`'s Integration Slot into one slot-free function,
      `build_and_verify(repo, main_before, reviewed, verdict, argv,
      timeout)`, which builds the candidate (the reviewed commit on a
      fast-forward, the merge on `NO_FF`) in a detached worktree under
      `<tmp>/candidates`, runs the verifier, or the record check for a
      records-only range as `atc integrate` does, and removes the
      candidate. `atc integrate` calls it inside its slot as before;
      `atc verify` calls it with no slot. `atc verify` creates no local
      branch or candidate ref, writes no evidence or integration record,
      and needs no Owner's word.
      Its last line is `VERIFY PASS <candidate sha>`, or the refusal
      `atc integrate` would give after the candidate
      (`VERIFICATION_FAILED`, `VERIFICATION_TIMEOUT`, `CANDIDATE_CHANGED`)
      with the verifier's tails and the kept working directory. It shares
      `git_integrate`'s candidate and verifier code, so the selection,
      static checks and stages (`parallel` and `serial` on a private
      desktop) are the integration's own; a separate selection command
      would need each project's selector and its static checks restated.
    - A separate command, not `atc integrate --check --verify`:
      `GIT_WORKFLOW.md` § Integrate says the check creates no candidate.
    - Governance, on the Owner's authorization (「現在授權，留第一批」,
      for `VERIFICATION.md` § Independent review only): that section says a
      reviewer does not rerun the writer's selection. It gains the rule
      that before a PASS on a tests or behaviour diff the reviewer runs
      `atc verify`, quotes its last line in the verified line, and treats a
      refusal as a blocker; the "does not rerun" paragraph is reworded so
      the targeted tests stay the reviewer's own choice and `atc verify` is
      the one run of the writer's selection it makes.
    - `atc-review` (a skill edit): step 4 and the verified line name
      `atc verify` with the integrate command's target arguments. Synced
      with `atc onboard --sync --root-only`.
    - Side effects:
      - About four minutes per review plus the candidate's environment
        (the root and hami-quant-trading build `.venv` from the lock), and
        integration runs the verifier again: no evidence carries over.
      - Its fetch can meet a ref lock held by a concurrent
        `atc integrate` fetch, and a verifier running beside an
        integration's verifier shares the CPU, which slows both against the
        1800-second budget; a reviewer runs it when no integration is
        running, and a lock failure is retried by rerunning it.
      - A candidate worktree is registered in the checkout's `.git` while
        it runs; one left by a crash is pruned by `atc tmp prune` after
        seven days, and an `atc worktree audit` run meanwhile lists it as
        it lists `atc integrate`'s candidates.
      - `main` may move between `atc verify` and the integration; the
        integration verifies its own candidate, so a PASS from `atc verify`
        is evidence for the review, not for the integration.
      - CI: none. hami-quant and hami-quant-trading: their reviewers gain
        the step once the skills are synced to them (After D).
    - Mitigations: tests reuse `test_git_integrate`'s verifier fixtures: a
      passing and a failing verifier, the candidate removed both ways, no
      ref, record or evidence written, `main` unchanged, a `NO_FF` candidate
      is the merge, and no slot taken (a concurrent `atc integrate` proceeds).
      Reusing `atc verify`'s result as integration evidence is a
      `VERIFICATION.md` § Full regression timing and evidence reuse change,
      so it is not part of this entry.

## After D

The `.atc/` layout work, then the final sync to every project (skills,
governance, `## Task records` declarations with prefixes `HQ`, `HQT`, `RIA`,
atc-coordinator seeded in hami-quant and hami-quant-trading), then the CLI
trim below.

## CLI trim after the final sync

Project skills use these until the final sync replaces them.

1. `--authority-reference` goes; `--task` becomes required.
   - Side effects: a repository without `## Task records` (heartopia) has
     no record ID.
   - Mitigations: `--task` keeps its task-identity-string form with
     `--owner-words` for such a repository.
2. `--project` replaces `--repo`, the root included.
   - Side effects: tests drive unregistered fixture repositories through
     `--repo`.
   - Mitigations: the tests register their fixtures in a temporary
     `workspace.yaml`.
3. Drop `atc integrate --branch`; the record names the branch.
   - Side effects: a task-identity-string run has no record.
   - Mitigations: such a run takes the one local branch at `--reviewed`, as
     the default does today, and stops when there are several.
4. Drop `--contract`.
   - Side effects: 9 project skill files pass it until the final sync.
   - Mitigations: the final sync drops it first; `atc doctor` keeps
     printing the contract number.
5. Drop `onboard --apply`.
   - Side effects: `atc bootstrap add` calls it.
   - Mitigations: `bootstrap` calls the apply mode as a function; the
     adapter README row changes with it.
6. Drop `tmp clean --tree-name`; `close-out --task` replaces it.
   - Side effects: `atc-review` and 8 project skill files use it.
   - Mitigations: the final sync moves them to `close-out --task` first.
7. Drop `atc preflight merge`'s alias (D.7) and `atc test run`'s positional
   form (D.12).
   - Side effects: vendored governance and project skills that still name
     them.
   - Mitigations: the final sync carries C's governance and skills first.
8. `friction`, `classify`, `policies`, `enforce` and `bootstrap` stay:
   `enforce` and `bootstrap` have root and project references,
   `scripts/policies.py` runs in CI, and `friction` and `classify` carry no cost
   while unused.

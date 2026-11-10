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
     task: after a task is integrated and pushed (the push under § 10), the
     coordinator removes that task's worktree and scratches and archives its
     sessions, then reports. Keep exceptions are written in the task record.
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
`adapters/python/README.md`. Order: D.5 before D.7 (the alias needs a
`--check` without authority), D.9 before D.12 (the child encoding), D.13
before D.12's scratch-name form, D.15 before D.12 (the `--json` form
`--select` runs), D.17 last; D.18 and D.19 are done ahead of the rest. Each entry: the
change, its side effects, their mitigations.

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
     when `integrations.jsonl` has the task's line and `origin/main`
     contains it, and skips the keep exceptions in the record; every item
     ends `DELETED`, `SKIPPED` or `REFUSED`; it touches nothing outside the
     task and runs `worktree cleanup`'s own checks.
4. Drop `--main` from `atc integrate` and `atc preflight push`.
   - Side effects: none found (0 references, 0 calls).
   - Mitigations: argparse's unknown-flag error.
5. `atc integrate --check` needs no authority.
   - Side effects: a check that passes says nothing about authority.
   - Mitigations: with `--task` it still evaluates the grant; without one
     its line says `authority: not checked`.
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
8. Drop `--verify-command` and `--verify-command-file`; the four test files
   that pass them declare a verifier in their fixture repository;
   `integrations.jsonl` records the verifier's source (argv file, timeout,
   whether `--verify-timeout` overrode it).
   - Side effects: the vendored `GIT_WORKFLOW.md` names the flags until the
     final sync; slower fixtures; a new key in the record.
   - Mitigations: C.1 already forbids the flags; fixtures commit the
     `## Integration verification` section once per module; readers of
     `integrations.jsonl` ignore unknown keys.
9. `atc` sets `PYTHONIOENCODING=utf-8` in the verifier child's environment
   and decodes the child's output as UTF-8 with replacement.
   - Side effects: a project verifier that relied on the inherited value;
     the display-only decoders `testrun.py:118` and `git_integrate.py:288`.
   - Mitigations: `TESTKIT.md` § Isolated environment states the value; the
     two decoders follow the Owner's decision on the `TESTKIT.md`
     environment contract; the `env -u PYTHONIOENCODING` guidance is removed
     after D is integrated.
10. A short `--check` output: the `CHECK <verdict> <reason>` line (now
    also naming project, branch and the 12-character tip), every `WARNING`
    line, and the `next:` line on a stop; the evidence block moves to
    `--json`.
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
15. `select_tests.py` prints a summary by default (selected n of m, one
    reason line per changed path); the per-test list only with `--explain`;
    a new `--json` flag prints the selected test paths as a JSON array, the
    form D.12's `selector` runs.
    - Side effects: callers that read the per-test list.
    - Mitigations: its only reader today is `tests/test_select_tests.py`,
      which moves to `--explain` and `--json`.
16. The output proposals 2, 3, 5, 9, 12 and 13 of Tool output, with the
    side effects and mitigations listed there.
17. D.9 and D.12 change atc-testkit source, so D bumps
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
      upgrade task.
18. Done ahead of D. `select_tests.py` selects, for the root's own guidance
    and skills, only the test files declared to read them. The guarded
    paths are `shared/governance/`, `shared/skills/`, the rendered copies
    `.claude/skills/`, `.agents/skills/` and `.dsh/skills/`, and the root
    `AGENTS.md` and `CLAUDE.md`. Each reader is declared in
    `DIRECTORY_READERS` with the directories and files it reads in this
    checkout; an entry matches a path equal to it or below it, so a root
    file such as `AGENTS.md` selects its reader; entries and paths compare
    with any case, separator or trailing slash. A guarded Markdown file or
    directory selects its declared readers and nothing through the import
    reach: neither the area name (`shared/governance`, `shared/skills`)
    nor the bare name (`SKILL.md`, `AGENTS.md`, `CLAUDE.md`) matches
    through it. A guarded file of another kind adds the test files whose
    reach names its file name. A guarded Markdown path with no reader
    selects nothing; a guarded path of another kind with none selects the
    full suite. Every
    other copy of those bare names lies under `shared/conformance/` and
    keeps the area rule; every other path keeps its rule.
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
        every guarded path and for that template.
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
    guard (D.18), that range itself verifies with the full suite.
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
      - `README.md` only: before, the full suite (664 s for a full run on
        this host) after about 1 s of static checks; after, about 1 s of
        static checks, 1 s of selection and `test_shell_install.py`
        (15 s), about 17 s.
      - `select_tests.py` only: before and after, the full suite (664 s),
        since `conftest.py` loads the selector (D.18).
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
      - An undeclared reader of a guarded Markdown path is never selected
        for that path, so the guard never runs it on such a range locally.
        This is an accepted residual, caught by the Owner's CI dispatch
        after topic D. No static scan is added: tests write fixture files
        under the same names (`AGENTS.md`, `SKILL.md`), so a scan of test
        source would flag fixtures as readers.
      - Guard timing: an undeclared reader among the tests a range does not
        select fails only in the dispatched CI run. The guard names the test
        and the paths, so the fix is one declaration.
      - `VERIFICATION.md` § Testing ("only the integration verifier runs
        the full suite") and § Independent review ("the integration
        verifier's full suite is the safety net") stop holding for the
        root; their rewording waits on the Owner's authorization and is
        not part of this entry until given.
      - `integrations.jsonl`: the root's line records task, authority,
        candidate, `main_before`, `records_only` and the reviewed SHA, with
        no verification field, and the root writes no verification report,
        so no recorded field changes meaning. The verifier's log carries
        the classification and the selection.
      - Timeout: `adapters/python/README.md` derives it as three times the
        measured full-run wall clock, rounded up to a whole minute and
        never below 300 s; 3 × 664 s = 1992 s gives 2040 s, which replaces
        1800 s in root `AGENTS.md`, since the verifier still widens to the
        full suite (`conftest.py`, the selector, `pyproject.toml`, an
        unmapped path).
      - The addition to D where the verifier subtracts what the writer
        already ran is not in this plan yet; for the root it would find
        nothing to subtract, since the root writes no evidence the writer's
        run could match, so it concerns the consumer form only.
      - Markdown that the selection maps to tests by a bare name stays not
        documentation-only (`adapters/python/README.md` selects the five
        tests that name some `README.md`); this errs toward running tests.

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

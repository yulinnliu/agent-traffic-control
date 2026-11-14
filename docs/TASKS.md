# Task records

A task record binds one task of one repository: the machine form of the
canonical task contract's binding fields (`shared/governance/CORE.md`
§ Canonical task contract). Every repository that adopts Agent Traffic
Control, this one included, keeps its records in
`.atc/tasks/<task_id>.yaml`, a project-owned path that sync never writes. A
record holds what changes only when the task is bound, its authority changes
or it ends; a task's progress is read from Git, never written to the record.

## Declaration

A repository's `AGENTS.md` declares its records in a section of its own:

```markdown
## Task records

- Task prefix: HQ
```

The prefix is 2 to 8 characters, an uppercase letter followed by uppercase
letters or digits, unique across the workspace map and the workspace root.
The section may carry prose after the line; tools read only the line.

## Record

`shared/policies/schemas/task.schema.json` defines the shape.

| Field | Required | Value |
| --- | --- | --- |
| `task_id` | yes | `<prefix>-<four digits>`, equal to the file name without `.yaml` |
| `repository` | yes | stable identity `<owner>/<repo>`; a repository without a remote gives its local name and a `repository_identity_status` string |
| `base_sha` | once bound | full SHA of the merge base of the topic and `main` when the record was written |
| `scope` | yes | what the task changes; a plan task cites its plan section |
| `governance_source` | yes | the governance repository, `<owner>/<repo>` |
| `governance_revision` | once bound | full SHA of the governance revision bound to the task |
| `branch` | once bound | the bound topic branch; `base_sha`, `governance_revision` and `branch` are present together or, in a planned record, all absent (§ Backlog) |
| `authority` | yes | the six booleans `edit`, `commit`, `push_topic`, `integrate_main`, `push_main`, `deploy`, and no other key |
| `authority_basis` | when any of `push_topic`, `integrate_main`, `push_main`, `deploy` is `true` | the Owner's words granting it, quoted |
| `closure` | no | `{state: closed \| abandoned, note: text}`, written in the commit that ends a task without integrating it |
| `keep` | no | what close-out keeps permanently, a list of `worktree`, `scratches` and `reports` (§ Close-out) |
| `rank` | no | a planned task's place in the queue, an integer from 1; lower comes first |
| `after` | no | task IDs that must read `integrated` or `closed` before this one starts; one that reads `abandoned` no longer blocks it |
| `notes` | no | background and conditions for whoever starts the task |
| `version` | no | the release the task is planned for, such as `0.8.0`; `atc task list` shows it and nothing checks or enforces it |

Other keys (`repository_identity_status`, `writer`,
`verification_requirements`, `execution_context`, `delivery`, `decisions`)
are optional and free in shape; a project may add its own. Tools write
records with a YAML serializer, so every string is quoted where YAML needs it.

## Authority on `main`

The rule of what a record grants and who writes it is governance
`GIT_WORKFLOW.md` § Task and authority; this section is the mechanism
`atc integrate` implements. `--owner-run` is `--owner-words` with the quote
`Owner run: approve integration`, for a command the Owner runs; the two are
mutually exclusive, and `--owner-words` below covers both.

- A task the Owner pre-authorizes is bound on `main` before its work: the
  coordinator commits the record alone as the topic's first commit and
  integrates it with `--owner-words` quoting the grant. One such commit may
  bind several tasks the Owner authorizes together.
- `atc integrate --task` reads `authority` and `authority_basis` from the
  record on `main`, never from the reviewed commit, and refuses a range that
  changes either field of a record that exists on `main`. Only a range that
  changes nothing but files under `.atc/tasks/`, integrated with
  `--owner-words`, changes them.
- A task not bound on `main` integrates with `--owner-words` each time; its
  record lands on `main` with its work. A range that mixes other changes with
  a new record refuses that record unless its `authority` grants `edit` and
  `commit` at most, so granted authority reaches `main` only through a
  records-only range.
- A narrowed or revoked grant reaches the record as a records-only range
  integrated with `--owner-words`.
- A records-only range integrates under one of the records it changes:
  `--task` names one of them, or the run stops `USAGE`. The `branch` of that
  record is the branch integrated and must point at the reviewed SHA, or the
  run stops `NO_BRANCH_AT_REVIEWED`; a record committed on a topic whose
  branch has another name, or whose branch moved past the reviewed SHA,
  trips it.
- `atc integrate --check` of a records-only range runs the record check
  `atc integrate` runs in place of the verifier on the tree the candidate
  will hold, without building it: a missing `## Task records` section or a
  record the schema refuses stops `VERIFICATION_FAILED` at the check.
- The grant's commit is the most recent commit on `main`'s first-parent
  history of the record whose parsed `authority` or `authority_basis`
  differs from its first parent's copy, the commit that creates the record
  counting as a change; a later edit that changes neither, such as
  `closure`, leaves it in place. That commit must be
  one of the new commits of a records-only integration with `--owner-words`
  in this clone's `integrations.jsonl`: reachable from the line's candidate
  and not from its `main_before`, so a binding of several commits and a
  `NO_FF` merge both qualify. A record changed on `main` any other way, such
  as a push by someone who does not use Agent Traffic Control or a binding
  made on another machine, grants nothing here: every integration of that
  task on this machine takes `--owner-words`, and the refusal says why. A
  repository shared with other developers protects `.atc/tasks/` on its host
  (code owners, branch protection) when it needs a real boundary.
- The tool cannot tell whose words a quote holds; it records
  the quote, and the integration record is the evidence the Owner audits.
  This is the limit of a governance layer that is not a security boundary
  (`CORE.md`: capability is not authority).
- `push_topic`, `push_main` and `deploy` are governance fields: no tool reads
  them yet, and each push and deploy still takes the Owner's word under
  `GIT_WORKFLOW.md`.

This is not the rejected approval ledger (`.out-of-scope/approval-ledger.md`):
no store or writer of its own exists; authority enters `main` only through
`atc integrate`, which takes the Owner's words, and its history is Git's.

## Status

`atc task list` derives a task's status at each run:

| Status | Derived from |
| --- | --- |
| `closed`, `abandoned` | the record's `closure.state` |
| `planned` | the record has no `branch` (§ Backlog) |
| `open` | its branch exists with commits not on `main` |
| `integrated` | `integrations.jsonl` names the task in a record that is not records-only, whose candidate SHA is reachable from `main`, and its branch is gone or has no commit beyond `main`: everything on the branch so far is on `main` |
| `bound` | the record is on `main`, it has at least one integration and every one is records-only, and its branch is gone or has no commit beyond `main` |
| `unknown` | none of the above, such as a task integrated on another machine |

Rows are tried in order; a multi-step task reads `open` while a later step
has commits off `main` and `integrated` between steps. A task stopped midway
reads `integrated` for its integrated part until its `closure` says
`abandoned`; a task whose branch is deleted with nothing integrated reads
`unknown` until its `closure` is written.

## Backlog

A planned task has its record before it has a branch. The coordinator plans
it when the Owner queues the work, so the queue lives in Git and
`atc task list --status planned` shows it in order.

- **Where.** A planned record lives on the repository's local `backlog`
  branch, which holds only `.atc/tasks/` and is never checked out, pushed or
  integrated. The record reaches `main` only after `atc task start`, with its
  topic, under § Authority on `main`.
- **Shape.** A planned record has no `branch`, `base_sha` or
  `governance_revision`. Its `authority` is all false: queuing a task
  authorizes no work. `atc task start` binds it and grants `edit` and
  `commit`. The Owner's rulings on a queued task go into its `decisions`.
- **Writes.** `plan`, `rank`, `drop`, `start` and `new` write the `backlog`
  branch with plumbing and a compare-and-swap `git update-ref`. A writer that
  finds the branch moved reads the new tip and tries again, so two
  coordinators on one machine never issue one ID twice. Writers on other
  machines are not coordinated, and nothing detects two planned records that
  share an ID: `atc task list` reads this clone's local branches only, and
  its `CONFLICT` mark covers only copies bound to different branches.
- **Identity.** An ID is issued at `plan`, or at `new` for unplanned work,
  and stays taken: the branch's history keeps every record it ever held, a
  started record moves to its topic, and a dropped one stays on the branch
  as `abandoned`. A deleted `backlog` branch loses the queue and frees the
  IDs only it held; recover it from the reflog.

## Commands

`atc task` reads and writes records. It keeps the queue the Owner sets and
never assigns work itself.

| Command | Does |
| --- | --- |
| `atc task plan --scope TEXT [--rank N] [--after ID...] [--notes TEXT] [--version V] [--project P]` | commits a planned record to the `backlog` branch (§ Backlog): `task_id` issued as `new` issues it, `repository`, `scope`, `governance_source`, `authority` all false, and the options given. Touches no working tree, so it runs from any checkout. Refuses an `--after` ID that no record or integration holds (`ERROR UNKNOWN_TASK`) |
| `atc task start ID [--scope TEXT] [--version V] [--project P]` | moves the planned record `ID` from the `backlog` branch into the topic worktree holding the working directory, as `new` binds one: `base_sha`, `governance_revision`, `branch`, `authority` `edit` and `commit` true; it drops `rank`, keeps the other fields and replaces `scope` when `--scope` is given. The record is written first and left uncommitted; the `backlog` copy is removed after it, and when that removal fails the written record is deleted again and the removal's refusal stands, or, when it cannot be deleted, the command stops with `STOP START_INCOMPLETE` (exit 3) naming both copies. Refuses as `new` refuses, with `STOP RECORD_EXISTS` when the worktree already holds the record, and with `STOP NOT_PLANNED` for an ID the `backlog` branch holds no planned record of |
| `atc task rank ID N [--project P]` | sets `rank` of a planned record on the `backlog` branch. A `rank` below 1 or an empty `--notes`, here or at `plan`, is refused with `ERROR RECORD_INVALID` |
| `atc task drop ID --note TEXT [--project P]` | writes `closure: {state: abandoned, note}` to a planned record on the `backlog` branch; the record stays there, so its ID is never issued again |
| `atc task new --scope TEXT [--version V] [--project P]` | writes `.atc/tasks/<id>.yaml` in the topic worktree holding the working directory, planning and starting the task in one step: `task_id` one past the highest on `main`, every local and remote-tracking branch, the `backlog` branch's history and `integrations.jsonl`, so a number is never reused on this machine; `repository` from the `origin` remote; `base_sha` the merge base of `HEAD` and `main`; `governance_source` and `governance_revision` from the repository's bound governance (the workspace root, being the source, binds its own `main`); `branch` the current branch; `authority` `edit` and `commit` true, the rest false. Refuses with `STOP` (exit 1) in a checkout on `main` and in the workspace root's canonical checkout |
| `atc task list [--project P \| --all] [--status S] [--json]` | reads the records on `main` and every local branch without a checkout, one line per task: `task_id`, status, `branch`, the first 60 characters of `scope`; a planned task adds its `rank`, `BLOCKED by` the `after` IDs not yet `integrated`, `closed` or `abandoned` (`blocked_by` in `--json`), and `after <ID> (dropped)` for each `after` ID that reads `abandoned`, which no longer blocks it (`after_dropped` in `--json`). `--status planned` lists the queue in order: by `rank`, unranked last, then by ID. A record on several refs shows the copy on `main` for authority and the copy on its own `branch` for the rest. An open or planned task with a `version` adds `version <V>`. After the rows, one `OVERLAP <ID> <ID>: <n> files: <first five>` line names each pair of open tasks of one repository whose branches both change a file since their merge base with `main` (task records left out), and one `VERSIONS <project>: <version> <IDs>; ...` line per repository groups its open and planned tasks by `version`; `--json` adds `overlaps`. Without a target it infers the project as `atc worktree` does, and a working directory inside the workspace root that is in no registered project names the root; `--all` covers every registered project and the root; `--json` puts the rows under `tasks` |
| `atc task check [DIR]` | checks the records in the working tree of `DIR` (default: the working directory's repository) |

## Close-out

`atc close-out --task <ID> [--project P] [--skip ITEM]... [--check]` carries out governance
`DELIVERY.md` § Close-out for one task, the project named as `atc task` names
it. Without `--project` the project is the one repository, the
workspace root or a registered project, whose `AGENTS.md` on `main` declares
the task's prefix, as `atc integrate --task` resolves it; with none or
several, the working directory's.

1. It runs only when `integrations.jsonl` names an integration of the task
   that is not records-only and whose candidate local `main` contains, and
   the record on `main` is readable with a valid `keep`; otherwise it stops
   with `STOP NOT_INTEGRATED` or `STOP RECORD_MISSING` (exit 1), or
   `ERROR RECORD_INVALID` (exit 2), before anything is removed.
2. The worktree whose branch is the record's `branch` goes through
   `atc worktree clean` and its checks, and so does an empty directory
   left at that branch's managed path after Git unregistered the worktree.
   A worktree that holds the working directory ends
   `SKIPPED (working directory)`, for the coordinator to remove.
3. The scratches whose registry line names the task, or whose `tree` lies
   inside the task's worktree (paths compared as real paths without case),
   with their dependent runs (`ATC_HOME.md` § Deleting), go through
   `atc tmp clean`. Every command that creates a scratch takes a required
   `--task` and writes it into the scratch's line (`ATC_HOME.md` § Scratch
   registry). A directory under `<tmp>` whose name starts with the task ID,
   any case, followed by the end of the name or a character other than a
   digit, and that is not among those scratches, prints
   `CONSENT <path> (...)`: close-out never deletes it, the Owner decides,
   and `atc tmp clean <name>` deletes it on the Owner's consent. Such lines
   change no exit code, and the summary line adds
   `, <n> need the Owner's consent` when there are any; under `--json` each
   is an item with outcome `CONSENT` and `counts` has `consent`.
4. Each report file `~/.atc/reports/<ID>-*.md` is deleted. A writer report
   `<ID>-<sha>.md` is deleted with no ledger line (`--check`:
   `writer report, no ledger line`); any other first appends one line to the
   repository's `integrations.jsonl`:
   `{"kind": "report", "task", "role", "sha", "verdict", "file", "closed_at"}`.
   `role` is `reviewer` for `<ID>-<sha>-review.md`, else `unknown` with `sha`
   `null`; a reviewer's `verdict` is the first whole word `CONDITIONAL PASS`
   (also written `CONDITIONAL-PASS` or `CONDITIONAL_PASS`), `PASS` or `FAIL`
   of the file's first non-empty line in any case, a leading `#` ignored and
   a word after `NO` or `NOT` skipped, also across `A`, `AN` or `YET`, else
   `null`. A `PASS` followed by an unnegated `CONDITIONAL` before any `FAIL`,
   as in `PASS (conditional)`, is `CONDITIONAL PASS`. A line that cannot be
   written ends the file `REFUSED` with the error and keeps it. Readers of
   integrations take only lines with a `candidate_sha`, so these lines change
   no status.

An item the record's `keep` names ends `SKIPPED (keep)` on every run; `keep`
is permanent. An item a `--skip` names (`worktree`, `scratches` or
`reports`, repeatable) ends `SKIPPED (--skip)` on this run only, and nothing
is written; a `--skip` of an absent item does nothing. Every item ends on one line,
`DELETED`, `SKIPPED` or `REFUSED` with the reason, and a last line counts
them; the exit code is 0 when none is `REFUSED`, 3 when one is `REFUSED`
and another `DELETED`, else 1. A refusal also prints
`STOP ITEM_REFUSED: <count> refused, <count> deleted; ...` on stderr.
`--json` prints the items and counts.

`--check` rehearses the run: it deletes nothing, writes no ledger line,
unregisters no scratch and takes no lock. Every item ends on the line the
run would give it, `DELETED`, `SKIPPED` or `REFUSED`, each with its reason: a
worktree's comes from the read-only `atc worktree audit` (`removable`, or
what keeps it), an empty leftover directory's from the conditions
`atc worktree clean` checks, a scratch's names it a registered scratch or a
dependent run, and a report's previews its ledger line's `role` and
`verdict`. The last line counts them and says nothing was deleted. The exit
code is 0 when no item would be `REFUSED`, else 1 with
`STOP ITEM_REFUSED: <count> would be refused; ...` on stderr; `--skip` and
`--json` apply, and `--json` gives `verdict` `READY` or `REFUSED` and
`check: true`. A step the check cannot see, such as a file locked at
deletion time, still ends `REFUSED` on the run.

`atc validate` runs the check on this repository and `atc validate
--projects` on every registered project. A check reports: a file that is not
valid YAML; a record the schema rejects; a `task_id` unlike its file name or
prefix; records without a declaration; a declaration with no prefix line;
and one prefix declared by two repositories. `atc task list` marks a
`task_id` that two refs bind to different branches as `CONFLICT`.

The project is named as `atc worktree` names it: `--project <name>`, `root`
for the workspace root, or inferred from the working directory.

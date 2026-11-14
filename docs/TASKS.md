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
| `title` | no | one line naming the task; `atc task create` writes it |
| `repository` | yes | stable identity `<owner>/<repo>`; a repository without a remote gives its local name and a `repository_identity_status` string |
| `base_sha` | yes | full SHA of the merge base of the topic and `main` when the record was written |
| `scope` | yes | what the task changes; it defaults to the title |
| `tests` | no | the test level, `none`, `standard` (the default) or `strict` (§ Test levels) |
| `governance_source` | yes | the governance repository, `<owner>/<repo>` |
| `governance_revision` | yes | full SHA of the governance revision bound to the task |
| `branch` | yes | the bound topic branch, `<harness>+<ID>` |
| `authority` | yes | the six booleans `edit`, `commit`, `push_topic`, `integrate_main`, `push_main`, `deploy`, and no other key |
| `authority_basis` | when any of `push_topic`, `integrate_main`, `push_main`, `deploy` is `true` | the Owner's words granting it, quoted |
| `keep` | no | what `atc task close` keeps permanently, a list of `worktree`, `scratches` and `reports` (§ Close-out) |
| `decisions` | no | the Owner's rulings on the task, a list of text lines (an object is also accepted); `atc task update --decision` appends one |

Other keys (`repository_identity_status`, `writer`,
`verification_requirements`, `execution_context`, `delivery`) are optional
and free in shape; a project may add its own. Tools write records with a YAML
serializer, so every string is quoted where YAML needs it.

## Test levels

The record's `tests` field sets what verification the task needs; a reviewer
may fail a task whose diff exceeds its level. CI after the push still runs.

| Level | Means |
| --- | --- |
| `none` | text-only changes; the reviewer reads the diff, and the record carries an Owner decision line saying so |
| `standard` | the writer runs `atc test ID`; the reviewer runs `atc test ID --sha SHA` and `atc test ID --candidate` |
| `strict` | changes to guards (refusal, deletion, authority, safety boundary, data integrity): `standard` plus `atc test ID --mutants FILE` by the writer and by the reviewer |

A project with `atc-testkit` defaults to `standard`; a project without it
takes only `none`.

## Authority on `main`

The rule of what a record grants and who writes it is governance
`GIT_WORKFLOW.md` § Task and authority; this section is the mechanism
`atc integrate` implements. `atc integrate ID` is the Owner's own run, and
that run is the authority: the integration records the quote
`Owner run: approve integration`.

- A task the Owner pre-authorizes is bound on `main` before its work: the
  coordinator commits the record alone as the topic's first commit and the
  Owner integrates it. One such commit may bind several tasks the Owner
  authorizes together.
- `atc integrate` reads `authority` and `authority_basis` from the record on
  `main`, never from the reviewed commit, and refuses a range that changes
  either field of a record that exists on `main`. Only a range that changes
  nothing but files under `.atc/tasks/`, integrated by the Owner, changes
  them.
- A task not bound on `main` is integrated by the Owner's run each time; its
  record lands on `main` with its work. A range that mixes other changes with
  a new record refuses that record unless its `authority` grants `edit` and
  `commit` at most, so granted authority reaches `main` only through a
  records-only range.
- A narrowed or revoked grant reaches the record as a records-only range
  integrated by the Owner.
- A records-only range integrates under one of the records it changes: `ID`
  names one of them. The `branch` of that record is the branch integrated and
  must point at the reviewed SHA, or the run stops `NO_BRANCH_AT_REVIEWED`; a
  record committed on a topic whose branch has another name, or whose branch
  moved past the reviewed SHA, trips it.
- `atc integrate ID --check` of a records-only range runs the record check
  `atc integrate` runs in place of the verifier on the tree the candidate
  will hold, without building it: a missing `## Task records` section or a
  record the schema refuses stops `VERIFICATION_FAILED` at the check.
- The grant's commit is the most recent commit on `main`'s first-parent
  history of the record whose parsed `authority` or `authority_basis`
  differs from its first parent's copy, the commit that creates the record
  counting as a change; a later edit that changes neither leaves it in place.
  That commit must be one of the new commits of a records-only integration
  in this clone's `integrations.jsonl`: reachable from the line's candidate
  and not from its `main_before`, so a binding of several commits and a
  `NO_FF` merge both qualify. A record changed on `main` any other way, such
  as a push by someone who does not use Agent Traffic Control or a binding
  made on another machine, grants nothing here, and the refusal says why. A
  repository shared with other developers protects `.atc/tasks/` on its host
  (code owners, branch protection) when it needs a real boundary.
- The tool cannot tell who ran the command; it records the Owner's-run
  quote, and the integration record is the evidence the Owner audits. This is
  the limit of a governance layer that is not a security boundary (`CORE.md`:
  capability is not authority).
- `push_topic`, `push_main` and `deploy` are governance fields: no tool reads
  them yet, and each push and deploy still takes the Owner's word under
  `GIT_WORKFLOW.md`.

This is not the rejected approval ledger (`.out-of-scope/approval-ledger.md`):
no store or writer of its own exists; authority enters `main` only through
`atc integrate`, which the Owner runs, and its history is Git's.

## Status

`atc task list` derives a task's status at each run:

| Status | Derived from |
| --- | --- |
| `open` | its branch exists with commits not on `main` |
| `integrated` | `integrations.jsonl` names the task in a record that is not records-only, whose candidate SHA is reachable from `main`, and its branch still exists with nothing beyond `main`: everything on the branch so far is on `main` |
| `closed` | the same integration, and its branch is gone |
| `abandoned` | the tag `archive/<ID>` exists and the branch is gone (`atc task close --abandon`) |
| `bound` | the record is on `main`, it has at least one integration and every one is records-only |
| `unknown` | none of the above, such as a task integrated on another machine |

Rows are tried in order; a multi-step task reads `open` while a later step
has commits off `main` and `integrated` between steps.

## Commands

`atc task` reads and writes records and the worktrees they bind. It never
assigns work itself.

| Command | Does |
| --- | --- |
| `atc task create [TITLE] [--scope TEXT] [--tests none\|standard\|strict] [--file FILE] [--project P] [--json]` | opens a task in one step: the worktree `<project worktrees>/<harness>+<ID>`, the branch `<harness>+<ID>`, the worktree's lock reason `<harness>:<ID>` and the record `.atc/tasks/<ID>.yaml` (`task_id`, `title`, `repository`, `base_sha`, `scope`, `tests`, `governance_source`, `governance_revision`, `branch`, `authority`, `decisions`), in one commit `chore(tasks): open <ID>, <title>`. The harness comes from the environment (`CLAUDECODE` is `claude`, `CODEX_*` is `codex`, `DSH_*` is `dsh`), else `STOP HARNESS_UNKNOWN`. `--file` is Markdown: `# title`, `## Scope`, `## Decisions` as a list, `## Tests` as the level. The ID is one past the highest on `main`, every local and remote-tracking branch, the tags and `integrations.jsonl`, so a number is never reused on this machine. `authority` has `edit` and `commit` true, the rest false. Refuses with `STOP` (exit 1) in a checkout on `main` and in the workspace root's canonical checkout |
| `atc task update ID [--title T] [--decision TEXT]... [--scope S] [--tests L] [--file F] [--json]` | changes the record in one commit; `--decision` appends a line to `decisions` |
| `atc task list [ID] [--project P \| --all] [--status S] [-v] [--json]` | reads the records on `main` and every local branch without a checkout, one line per task: `task_id`, status, `branch`, the first 60 characters of the title or `scope`. A record on several refs shows the copy on `main` for authority and the copy on its own `branch` for the rest. After the rows, one `OVERLAP <ID> <ID>: <n> files: <first five>` line names each pair of open tasks of one repository whose branches both change a file since their merge base with `main` (task records left out); `--json` adds `overlaps`. `-v` adds the worktree audit: whether each worktree is removable (merged into `main` and clean), and the candidate leftovers (`ATC_HOME.md` § Candidates). Without a target it infers the project from the working directory, and a working directory inside the workspace root that is in no registered project names the root; `--all` covers every registered project and the root; `--json` puts the rows under `tasks` |
| `atc task resume ID [--json]` | prints the path of the task's worktree and how far behind `main` its branch is; it refuses a worktree another session holds |
| `atc task close ID [--check] [--abandon] [-v] [--json]` | § Close-out |
| `atc-maintain task check [DIR]` | checks the records in the working tree of `DIR` (default: the working directory's repository) |

## Close-out

`atc task close ID [--check] [--abandon] [-v] [--json]` carries out governance
`DELIVERY.md` § Close-out for one task. The project is the repository, the
workspace root or a registered project, whose `AGENTS.md` on `main` declares
the task's prefix, as `atc integrate` resolves it; with none or several, the
working directory's.

1. It runs only when `integrations.jsonl` names an integration of the task
   that is not records-only and whose candidate local `main` contains, and
   the record on `main` is readable with a valid `keep`; otherwise it stops
   with `STOP NOT_INTEGRATED` or `STOP RECORD_MISSING` (exit 1), or
   `ERROR RECORD_INVALID` (exit 2), before anything is removed. `--abandon`
   instead closes a task that was not integrated and keeps its tip as the
   tag `archive/<ID>`.
2. The worktree whose branch is the record's `branch`, and the branch, are
   removed after the checks that the worktree is clean and merged into
   `main`. When the task's latest integration on `main` is a `kind: squash`
   line that passes governance `GIT_WORKFLOW.md` § Explicitly authorized
   squash (Owner's words, the squash commit's only parent `main_before`, its
   tree that of `reviewed`, the branch still at `reviewed`), the branch counts
   as merged and its tip is kept as the tag `archive/<ID>`; any other squashed
   branch is refused `UNMERGED`; an empty directory left at that branch's managed path after Git
   unregistered the worktree goes too. A worktree that holds the working
   directory ends `SKIPPED (working directory)`, for the coordinator to
   remove.
3. The scratches whose registry line names the task, or whose `tree` lies
   inside the task's worktree (paths compared as real paths without case),
   with their dependent runs (`ATC_HOME.md` § Deleting), are deleted as
   `atc test ID --clean` deletes them. Every command that creates a scratch
   writes the task into the scratch's line (`ATC_HOME.md` § Scratch
   registry). A directory under `<tmp>` whose name starts with the task ID,
   any case, followed by the end of the name or a character other than a
   digit, and that is not among those scratches, prints
   `CONSENT <path> (...)`: the close never deletes it, and the Owner decides.
   Such lines change no exit code, and the summary line adds
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

An item the record's `keep` names (`worktree`, `scratches` or `reports`) ends
`SKIPPED (keep)` on every run; `keep` is permanent. Every item ends on one
line, `DELETED`, `SKIPPED` or `REFUSED` with the reason, and the verdict line
`CLOSED|CHECK|REFUSED ID: N deleted ...` counts them; the exit code is 0 when
none is `REFUSED`, 3 when one is `REFUSED` and another `DELETED`, else 1. A
refusal also prints `STOP ITEM_REFUSED: <count> refused, <count> deleted; ...`
on stderr, then `hint:`. `-v` lists every item and `--json` prints the items
and counts.

`--check` rehearses the run: it deletes nothing, writes no ledger line,
unregisters no scratch and takes no lock. Every item ends on the line the
run would give it, `DELETED`, `SKIPPED` or `REFUSED`, each with its reason: a
worktree's comes from the read-only audit of `atc task list -v` (`removable`,
or what keeps it), a scratch's names it a registered scratch or a dependent
run, and a report's previews its ledger line's `role` and `verdict`. The last
line counts them and says nothing was deleted. The exit code is 0 when no
item would be `REFUSED`, else 1 with
`STOP ITEM_REFUSED: <count> would be refused; ...` on stderr; `--json` gives
`verdict` `READY` or `REFUSED` and `check: true`. A step the check cannot see,
such as a file locked at deletion time, still ends `REFUSED` on the run.

`atc-maintain validate` runs the record check on this repository and
`atc sync --check` on every registered project. A check reports: a file that
is not valid YAML; a record the schema rejects; a `task_id` unlike its file
name or prefix; records without a declaration; a declaration with no prefix
line; and one prefix declared by two repositories. `atc task list` marks a
`task_id` that two refs bind to different branches as `CONFLICT`.

The project is named as `--project <name>` names it, `root` for the workspace
root, or inferred from the working directory.

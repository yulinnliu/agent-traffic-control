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
| `base_sha` | yes | full SHA of the merge base of the topic and `main` when the record was written |
| `scope` | yes | what the task changes; a plan task cites its plan section |
| `governance_source` | yes | the governance repository, `<owner>/<repo>` |
| `governance_revision` | yes | full SHA of the governance revision bound to the task |
| `branch` | yes | the bound topic branch |
| `authority` | yes | the six booleans `edit`, `commit`, `push_topic`, `integrate_main`, `push_main`, `deploy`, and no other key |
| `authority_basis` | when any of `push_topic`, `integrate_main`, `push_main`, `deploy` is `true` | the Owner's words granting it, quoted |
| `closure` | no | `{state: closed \| abandoned, note: text}`, written in the commit that ends a task without integrating it |
| `keep` | no | what close-out keeps permanently, a list of `worktree`, `scratches` and `reports` (§ Close-out) |

Other keys (`repository_identity_status`, `writer`,
`verification_requirements`, `execution_context`, `delivery`, `decisions`)
are optional and free in shape; a project may add its own. Tools write
records with a YAML serializer, so every string is quoted where YAML needs it.

## Authority on `main`

The rule of what a record grants and who writes it is governance
`GIT_WORKFLOW.md` § Task and authority; this section is the mechanism
`atc integrate` implements.

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
| `open` | its branch exists with commits not on `main` |
| `integrated` | `integrations.jsonl` names the task in a record that is not records-only, whose candidate SHA is reachable from `main`, and its branch is gone or has no commit beyond `main`: everything on the branch so far is on `main` |
| `bound` | the record is on `main`, it has at least one integration and every one is records-only, and its branch is gone or has no commit beyond `main` |
| `unknown` | none of the above, such as a task integrated on another machine |

Rows are tried in order; a multi-step task reads `open` while a later step
has commits off `main` and `integrated` between steps. A task stopped midway
reads `integrated` for its integrated part until its `closure` says
`abandoned`; a task whose branch is deleted with nothing integrated reads
`unknown` until its `closure` is written.

## Commands

`atc task` reads and writes records; it never assigns, queues or ranks work.

| Command | Does |
| --- | --- |
| `atc task new --scope TEXT [--project P \| --repo ROOT]` | writes `.atc/tasks/<id>.yaml` in the topic worktree holding the working directory: `task_id` one past the highest on `main` and every local and remote-tracking branch and in `integrations.jsonl`, so a number is never reused on this machine; `repository` from the `origin` remote; `base_sha` the merge base of `HEAD` and `main`; `governance_source` and `governance_revision` from the repository's bound governance (the workspace root, being the source, binds its own `main`); `branch` the current branch; `authority` `edit` and `commit` true, the rest false. Refuses in a checkout on `main` and in the workspace root's canonical checkout |
| `atc task list [--project P \| --repo ROOT \| --all] [--status S] [--json]` | reads the records on `main` and every local branch without a checkout, one line per task: `task_id`, status, `branch`, the first 60 characters of `scope`. A record on several refs shows the copy on `main` for authority and the copy on its own `branch` for the rest. Without a target it infers the project as `atc worktree` does; `--all` covers every registered project and the root |
| `atc task check [DIR]` | checks the records in the working tree of `DIR` (default: the working directory's repository) |

## Close-out

`atc close-out --task <ID> [--project P | --repo ROOT] [--skip ITEM]...` carries out governance
`DELIVERY.md` § Close-out for one task, the project named as `atc task` names
it.

1. It runs only when `integrations.jsonl` names an integration of the task
   that is not records-only and whose candidate local `main` contains, and
   the record on `main` is readable with a valid `keep`; otherwise it stops
   with `NOT_INTEGRATED`, `RECORD_MISSING` or `RECORD_INVALID` (exit 2)
   before anything is removed.
2. The worktree whose branch is the record's `branch` goes through
   `atc worktree cleanup` and its checks, and so does an empty directory
   left at that branch's managed path after Git unregistered the worktree.
   A worktree that holds the working directory ends
   `SKIPPED (working directory)`, for the coordinator to remove.
3. The scratches whose registry line names the task, with their dependent
   runs (`ATC_HOME.md` § Deleting), go through `atc tmp clean`.
4. The report files `~/.atc/reports/<ID>-*.md` are deleted.

An item the record's `keep` names ends `SKIPPED (keep)` on every run; `keep`
is permanent. An item a `--skip` names (`worktree`, `scratches` or
`reports`, repeatable) ends `SKIPPED (--skip)` on this run only, and nothing
is written; a `--skip` of an absent item does nothing. Every item ends on one line,
`DELETED`, `SKIPPED` or `REFUSED` with the reason, and a last line counts
them; the exit code is 1 when one is `REFUSED`, else 0.

`atc validate` runs the check on this repository and `atc validate
--projects` on every registered project. A check reports: a file that is not
valid YAML; a record the schema rejects; a `task_id` unlike its file name or
prefix; records without a declaration; a declaration with no prefix line;
and one prefix declared by two repositories. `atc task list` marks a
`task_id` that two refs bind to different branches as `CONFLICT`.

The project is named as `atc worktree` names it: `--project <name>`, `--repo
<workspace root>`, or inferred from the working directory.

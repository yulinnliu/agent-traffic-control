# Task lifecycle

How one task moves from start to push, step by step, with the command each
step runs, who runs it and the refusals met most often. This page says how;
the rules are governance `DELIVERY.md` and `GIT_WORKFLOW.md`, and the
protocols are the skills `atc-coordinator`, `atc-review` and `atc-worktree`.
Where this page and those differ, they win.

Commands below take `<root>` for the workspace root's canonical checkout,
`<project>` for a registered project, `<ID>` for the task ID and `<sha>` for
a full commit SHA.

## 1. Worktree

The coordinator, on the Owner's word to start.

```text
atc worktree create --project <project> --owner claude --topic <topic>
atc worktree create --repo <root> --owner claude --topic <topic>
```

The second form is for a task of the workspace root itself, whose
canonical checkout stays on a clean `main`. The worktree lands under
`worktrees/<project>/` or `worktrees/root/` on the branch
`worktree-<owner>+<topic>`. The `atc-worktree` skill covers resume, audit
and cleanup.

- `CHECKOUT_NOT_MAIN`: the canonical checkout is off `main`; switch it back
  before any other `atc` command.

## 2. Task record

The coordinator, inside the new worktree.

```text
atc task new --repo "<root>" --scope "<plan section>"
atc task new --project <project> --scope "<plan section>"
```

It writes `.atc/tasks/<ID>.yaml` granting `edit` and `commit`; commit it on
the topic branch. A record's `keep` list names what close-out keeps
permanently (`docs/TASKS.md` § Record).

- `CHECKOUT_CANONICAL` or `UNKNOWN_PROJECT` with a relative `--repo`: run it
  inside the new worktree with the absolute root path, as the refusal says.
- `ON_MAIN`: the working directory is a checkout on `main`, not the
  worktree.

## 3. Writer: work and self-checks

The writer, in the worktree. Tests run on an exported copy, never in the
live worktree:

```text
atc tmp checkout <sha> --task <ID>
atc test run --scratch <ID>-<first 7 of sha> --select main
```

`atc tmp checkout` exports the commit and builds its `.venv`; for the
workspace root, whose tests run in `adapters/python`, add
`--lock-dir adapters/python`. `atc test run`
prints `LOG <path>` and one summary line; the log holds the rest
(`docs/TESTKIT.md` § Commands). The root's own suite also runs through
`python -m atc_testkit.ci --stages` from `adapters/python`.

- `VENV_MISSING`: the tree has no `.venv`; make the scratch with
  `atc tmp checkout`.
- `SELECTION_UNDECLARED`: the project declares no `selector`; run without
  `--select`, naming the tests.

## 4. Writer: report

The writer runs the check on its tip, then writes the step report to
`~/.atc/reports/<ID>-<short sha>.md` as `atc-review` "Writer: the step
report" says.

```text
atc integrate --check --repo "<root>" --branch <branch> --task <ID> --reviewed <sha>
```

- `REBASE_REQUIRED` or `OVERLAP`: rebase onto `main` and report the new tip.
- `authority=missing`: pending authority; the report says so.

## 5. Reviewer

The reviewer, from its own scratch, as `atc-review` "Reviewer: the review"
says: `atc tmp checkout` of the reviewed SHA, `atc test run` on it, and
`atc test mutants` for the guards. Before a PASS on a tests or behaviour
diff it runs the integration verifier on the candidate `atc integrate` would
build, and quotes its last line in the verified line
(`VERIFICATION.md` § Independent review):

```text
atc verify --repo "<root>" --branch <branch> --reviewed <sha>
```

`atc verify` takes no slot, moves no ref and needs no Owner's word. Run it
while no integration runs: the two share a fetch and the CPU. A refusal is a
blocker finding. The verdict goes to
`~/.atc/reports/<ID>-<short sha>-review.md`.

## 6. The Owner's word

The coordinator runs `atc integrate --check` on the reviewed SHA, asks the
Owner for the integration word, and relays the Owner's exact words with the
full SHA to the writer (`GIT_WORKFLOW.md` § Task and authority).

## 7. Writer: integrate and close out

The writer, after the relay:

```text
atc integrate --repo "<root>" --branch <branch> --task <ID> --reviewed <sha> --owner-words "<the relayed words>"
atc close-out --task <ID> --repo "<root>"
```

Use `--project <project>` in place of `--repo "<root>"` for a project task.
`atc close-out` removes the worktree, the task's scratches and its reports
(`docs/TASKS.md` § Close-out):

- An intermediate batch of a multi-batch topic runs it with `--skip` for
  each item the next batch needs, for example `--skip worktree`; it writes
  nothing. The final batch runs it without `--skip`. The record's `keep` is
  for items kept permanently.
- The worktree that holds the session's working directory ends
  `SKIPPED (working directory)`. The writer leaves its working directory as
  it is; the coordinator removes the worktree after that session ends.

- `VERIFICATION_FAILED`: the verifier's log names the failing tests; fix,
  and the change goes back to review.
- A session that refuses to run on relayed words reports `BLOCKED`; the
  Owner approves in that session.

## 8. Coordinator: push and archive

The coordinator asks the Owner's push word once for whatever is integrated,
checks with `atc preflight push --repo "<root>"`, pushes, archives the task's
sessions and runs `atc close-out` for what the writer skipped
(`DELIVERY.md` § Close-out and § 10).

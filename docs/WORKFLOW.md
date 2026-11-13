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
atc worktree create --project root --owner claude --topic <topic>
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
atc task new --project root --scope "<plan section>"
atc task new --project <project> --scope "<plan section>"
```

It writes `.atc/tasks/<ID>.yaml` granting `edit` and `commit`; commit it on
the topic branch. A record's `keep` list names what close-out keeps
permanently (`docs/TASKS.md` § Record). `atc task check` checks the records
in a working tree and `atc task list` lists the tasks (`docs/TASKS.md`
§ Commands).

- `CHECKOUT_CANONICAL`: the working directory is the canonical checkout; run
  it inside the new worktree, as the refusal says.
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
`python -m atc_testkit.ci --stages` from `adapters/python`. A command that
opens windows runs under `atc test private` (`docs/TESTKIT.md` § Private
desktop). A root writer also runs the checks its change calls for, such as
`atc sync --root` then `atc validate` after a skill change and
`atc policies check` after a schema or example change; root `AGENTS.md`
§ Changing this repo says which apply.

- `VENV_MISSING`: the tree has no `.venv`; make the scratch with
  `atc tmp checkout`.
- `SELECTION_UNDECLARED`: the project declares no `selector`; run without
  `--select`, naming the tests.

## 4. Writer: report

The writer runs the check on its tip, then writes the step report to
`~/.atc/reports/<ID>-<short sha>.md` as `atc-review` "Writer: the step
report" says.

```text
atc integrate --check --task <ID> --reviewed <sha>
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
atc verify --project <project> --reviewed <sha> --task <ID>
```

`atc verify` takes no slot, moves no ref and needs no Owner's word; `--task`
only resolves the target. Its PASS is recorded, and `atc integrate` reuses it
in place of rerunning the verifier while the candidate and its inputs are
unchanged (`shared/conformance/verification-evidence/README.md`
§ Integration reuse). Run it
while no integration runs: the two share a fetch and the CPU. A refusal is a
blocker finding. The verdict goes to
`~/.atc/reports/<ID>-<short sha>-review.md`. `atc tmp list` shows the
scratches, and `atc tmp clean` and `atc tmp prune` delete them (`atc-review`
"Reviewer: after integration", `docs/TESTKIT.md` § Commands).

## 6. The Owner runs the integration

The coordinator runs `atc integrate --check --json` on the reviewed SHA and
gives the Owner one command to run (`GIT_WORKFLOW.md` § Owner-run
commands):

```text
atc integrate --task <ID> --reviewed <40-character sha> --owner-run
```

The record ID names the repository by its prefix and the branch by the
record's `branch`; `--project <project>` (`root` for the workspace root)
names it where the prefix is not declared on `main`. The check's
`verification_reuse` tells the Owner how long it takes: `REUSED` reuses the
reviewer's `atc verify` record and takes seconds, any other value runs the
verifier. Neither the coordinator nor the writer runs it.

The coordinator reads completion from Git at its next turn: the task's line
in `<git common dir>/atc/integrations.jsonl` with the reviewed SHA, and
`main` at or past its `candidate_sha`. A stop the Owner shows follows its
`next`; `VERIFICATION_FAILED` names the failing tests in the verifier's log,
and the fix goes back to review.

## 7. Coordinator: close out

```text
atc close-out --task <ID> --check
atc close-out --task <ID>
```

`--check` lists each item with what the run would do and why, and deletes
nothing; the coordinator shows the Owner those items, then runs it without
`--check`. `atc close-out` removes the worktree, the task's scratches and its reports
(`docs/TASKS.md` § Close-out):

- An intermediate batch of a multi-batch topic runs it with `--skip` for
  each item the next batch needs, for example `--skip worktree`; it writes
  nothing. The final batch runs it without `--skip`. The record's `keep` is
  for items kept permanently.
- The worktree that holds a session's working directory ends
  `SKIPPED (working directory)`; the coordinator removes it after that
  session ends.

## 8. Coordinator: push and archive

For whatever is integrated and unpushed, the coordinator runs
`atc preflight push --project <project> --json`, gives the Owner every
commit it lists and the push command with the listed `main` tip:

```text
git -C "<main checkout>" push origin <40-character sha>:refs/heads/main
```

The push is done when `git ls-remote origin refs/heads/main` names that SHA
or a descendant. The coordinator archives the task's sessions
(`DELIVERY.md` § Close-out and § 10).

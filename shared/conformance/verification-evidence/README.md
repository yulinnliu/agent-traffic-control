# Verification evidence

The contract between a verifier, `atc integrate` and a project's pre-push
gate for reusing a passed candidate verification (`VERIFICATION.md` § Testing,
`GIT_WORKFLOW.md` § Integrate). All three files are UTF-8 JSON objects.

## Report

The verifier may write a report to the path in `ATC_VERIFICATION_REPORT`
before it exits zero. The path lies outside the candidate checkout.

```text
schema              "atc-verification-report/2"
selector            {"name": str, "version": str}
selection           {"full": bool, "paths": [str, ...]}
stages              [{"name": str, "marker": str, "ignore": [str, ...]}, ...]
dependency_inputs   {<repo-relative path>: <sha256 hex, or "absent">, ...}
time_slack          number: the seconds of ATC_TEST_TIME_SLACK the stages ran with
```

- `selection` is what the verifier executed: `full` for the project's default
  suite, otherwise the test paths in the project's own order, with `/`
  separators. No tests is `{"full": false, "paths": []}`.
- `stages` lists what decides which tests a stage runs: its category
  expression (category names joined by `and`, `or` and `not`) and ignored
  paths. Worker counts, temporary directories and verbosity are
  not part of a stage.
- `dependency_inputs` holds the digest of each file that fixes the dependency
  environment, such as the lock file and the interpreter version file.
- `time_slack` is the slack the stages ran with, the default when the
  variable was unset (`docs/TESTKIT.md` § Time slack).
- Further keys (`python`, `classification`, `checks`, `durations`) are
  recorded and never compared.

## Record

After `verification=PASS`, with `main` updated and read back, the adapter
writes `<git-common-dir>/atc/verification-evidence/<candidate_sha>.json`:

```text
schema               "atc-verification-evidence/2"
candidate_sha        full commit ID the verifier ran against
tree                 tree ID of that commit
clean                true: candidate HEAD unchanged and status empty after the verifier
expected_main        main before the integration
reviewed             reviewed source SHA; the rebased tip of a carried-over approval
carried_over_from    present only for a carried-over approval: the reviewed SHA it came from
verdict              "FF" | "NO_FF"
tasks                every task the integration names, as in its integrations.jsonl lines
task                 the one entry of tasks, or null when tasks holds several
authority            {"kind": "record", "task_id": str} | {"kind": "owner_words", "quote": str}
                     | {"kind": "unclassified"}, which readers accept and no adapter writes
verify_argv          the verifier argv that ran
platform             {"system": str, "machine": str}
verified_at          UTC timestamp, ISO 8601
duration_seconds     verifier wall clock
report               the report above, verbatim
```

The adapter also wraps an `atc-verification-report/1` report, which has no
`time_slack`: the report of a project on an earlier testkit. The adapter
writes no record when the report is missing (`NO_REPORT`), is not a valid
report (`INVALID_REPORT`), or the file cannot be written
(`WRITE_FAILED`); integration succeeds regardless. A recovered integration
writes none (`RECOVERED`). The result names the record path, or `NONE` with
one of these reasons. After each write the adapter deletes records older than
14 days. Readers never write or delete.

A reader accepts `atc-verification-evidence/1` as well: its records carry
`authority_reference`, the string supplied to the adapter, in place of `tasks`,
`task` and `authority`. No reuse check reads these keys.

## Reuse

A gate about to run tests for a pushed commit reuses the record in place of
its test stage only when every row holds, checked in this order. The first
row that fails gives the verdict `RUN: <reason>`; all rows holding gives
`REUSE`.

| Check | Reason |
| --- | --- |
| The record for the pushed commit exists | `NO_RECORD` |
| It is readable JSON holding an object | `UNREADABLE` |
| `schema` is `atc-verification-evidence/2` or `/1`, `report.schema` is `atc-verification-report/2`, and every field the reuse checks read has its type | `SCHEMA_MISMATCH` |
| `candidate_sha` equals the pushed commit | `COMMIT_MISMATCH` |
| `tree` equals the pushed commit's tree | `TREE_MISMATCH` |
| `clean` is `true` | `NOT_CLEAN` |
| `platform` equals the gate's platform | `PLATFORM_MISMATCH` |
| `report.dependency_inputs` equals the digests of the gate's dependency files in its checkout | `DEPENDENCY_MISMATCH` |
| `verify_argv` equals the project's verifier argv file | `ARGV_MISMATCH` |
| `report.selector` equals the gate's selector name and version | `SELECTOR_MISMATCH` |
| `report.stages` equals the gate's stages | `STAGES_MISMATCH` |
| `report.time_slack` equals the gate's time slack | `TIME_SLACK_MISMATCH` |
| `report.selection` covers the gate's own selection | `SELECTION_NOT_COVERED` |

A recorded selection covers the gate's selection when it is `full`, or when
the gate's selection is not `full` and each of its paths equals a recorded
path or lies below a recorded directory. Any failure while checking gives
`RUN: CHECK_FAILED`. Static checks run under either verdict, and the gate
prints the verdict.

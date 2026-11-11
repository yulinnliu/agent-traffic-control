# Conformance fixtures

Language-neutral test cases for every tool in the contract. The conventions
every `atc` command follows (help, exit codes, `--json`, naming, versions and
deprecations) are in `CLI.md`. A conforming
implementation, in any language, passes all of them; the Python reference
implementation under `adapters/python/` runs them as its own test suite
(`tests/test_conformance.py`).

Each case is a directory:

```text
<tool>/<case-name>/
  input/        files the tool reads: a repo skeleton, a policy file, an adapter
  args.json     command-line arguments as a JSON array
  expected/
    result.json {"exit_code": int, "output_contains": [str, ...]}
    ...         any files the tool must produce, compared after line-ending normalisation
```

The runner copies `input/` to a fresh directory, runs the tool from inside
that copy with `ATC_ROOT` unset, and
substitutes three placeholders in `args.json`: `{input}` (the copy),
`{out}` (an empty directory for produced files) and `{root}` (the checkout
holding `shared/`). Every string in `output_contains` must occur in the
combined stdout and stderr. Every file under `expected/` other than
`result.json` must be produced at the same relative path under `{out}`.
Cases never depend on absolute paths, the host operating system or network
access.

| Tool | Directory | Verdict vocabulary |
| --- | --- | --- |
| adapter validation | `validate-adapters/` | `all checks passed`, or one line per failure |
| policy loading | `load-policies/` | `ok: <kinds>`, `schema-violation`, `preset-regrants-hard-deny`, `approval-overlaps-hard-deny`, `duplicate-glob` |
| enforcement generation | `generate-enforcement/` | `generated:`, `ok: enforcement configs are current`, `drift:` (files and `live-claude`/`live-codex`/`live-hook`), `unresolved-token`, plus golden files per harness |
| escalation gate | `classify-change/` | `LOW: <reason>`, `HIGH: <reason>` |
| task record check | `task-records/` | `ok: <dir>`, `invalid-yaml`, `schema-violation`, `id-mismatch`, `no-declaration`, `no-prefix-line`, `invalid-prefix` |

Merge preflight (`FF`, `NO_FF`, `STOP`), push preflight (`PUSH`, `STOP`) and
worktree identity (`CREATED`, `RESUMED`, `IDENTITY_MISMATCH`,
`IDENTITY_AMBIGUOUS`, `ALREADY_EXISTS`, `REMOVABLE`) and task records
(`atc task new` and `list`) need live git
repositories. Their cases are the Python suites `tests/test_git_preflight.py`,
`tests/test_integrate_moving_main.py`, `tests/test_managed_worktree.py` and
`tests/test_task_records.py`, which build
each repository in code; a second implementation ports those suites until
fixtures with an `init.json` repository description exist here.

Verification evidence (the report a verifier hands to `atc integrate`, the
record the adapter writes, and the reuse verdicts `REUSE` and `RUN: <reason>`)
is specified in `verification-evidence/README.md`. Writing needs a live
integration and reading needs a live repository, so its cases are the Python
suites `tests/test_git_integrate.py` and `tests/test_testkit_evidence.py`.

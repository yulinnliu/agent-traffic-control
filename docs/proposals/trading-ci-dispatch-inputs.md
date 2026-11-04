# Trading CI: workflow dispatch inputs

## Context

The current trading CI contract is a manually dispatched workflow on
`windows-latest`. It runs the pytest marker matrix through
`scripts/ci_pytest.py`. This note proposes a small dispatch interface; it
does not change the workflow or test runner.

## Proposed inputs

- `ref`: optional branch name or commit SHA to test. Empty means the
  workflow's default checked-out revision.
- `tests`: optional space-separated pytest paths or node IDs. Empty means
  retain the existing default marker matrix.
- `verify_script`: optional single filename matching
  `scripts/verify_*_mutations.py`. Empty means no mutation-verification
  script is run.

Illustrative workflow input shape:

```yaml
on:
  workflow_dispatch:
    inputs:
      ref:
        description: Branch or commit SHA
        required: false
        type: string
      tests:
        description: Pytest paths or node IDs
        required: false
        type: string
      verify_script:
        description: Optional scripts/verify_*_mutations.py filename
        required: false
        type: string
```

## Job behavior

1. Check out `ref` when supplied; otherwise preserve the workflow's current
   default-ref behavior. Record the resolved commit SHA in the job summary.
2. If `tests` is empty, invoke the existing `scripts/ci_pytest.py` default
   marker matrix unchanged. Otherwise, pass the supplied paths/node IDs to
   pytest as the selected test scope; do not silently combine a targeted
   selection with the full default matrix.
3. If `verify_script` is non-empty, validate that it is exactly one filename
   under `scripts/` matching `verify_*_mutations.py`, then run that script.
   Reject path separators, traversal components, and arbitrary commands.
4. Keep the existing Windows runner and marker semantics. The private runner
   has 2 vCPU: do not increase parallelism beyond its capacity. Tk tests that
   require serialization remain in a serial job. The slow marker remains
   CI-only.

## Reporting

The job summary should include the resolved ref/SHA, selected test scope,
pytest's final summary line, and (when run) the verification script name and
its killed-mutant count. A failed test or verification script must fail the
workflow; summary generation must not mask the original exit code.

## Boundaries

This is a dispatch-input design only. It does not authorize workflow changes,
runner provisioning, changes to marker classification, or changes to test
semantics. Before implementation, confirm the actual workflow and
`scripts/ci_pytest.py` invocation contract in the trading repository.

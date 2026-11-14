# `atc test mutants`

```
atc test mutants <scratch> <sha> <worktree> <mutants.json> --task <ID> [--python <py>] [--kill-rule failure|assertion] --tests <path>...
```

Applies each mutant alone to the copy at `<tmp>/<scratch>/tree`, the export
of `<sha>`, through `atc test run`, and restores its files after each run.
Each run is pytest with `-x` and a 900-second timeout.

## Input

`<mutants.json>` is a JSON array of mutants:

```json
[{"label": "...", "edits": [{"path": "...", "old": "...", "new": "..."}], "tests": ["..."]}]
```

Every `old` occurs exactly once in its file; one that does not aborts the
run. Edits follow the file's line endings. `tests` is optional: the pytest
arguments that judge that mutant in place of `--tests`.

## Kill rule

- `failure` (default): a mutated run that exits 1 with a `FAILED` line kills
  the mutant.
- `assertion`: pytest also writes a junit report to
  `<scratch>/junit/<NNN>-<label>.xml`, and the run kills only when every
  failure's message starts with `AssertionError`, `assert ` or
  `Failed: DID NOT RAISE` and no test errored. A failure by any other
  exception, `pytest.fail` among them, is `INCONCLUSIVE`.

## Result lines

- `--task` registers the scratch under the task; a scratch registered to
  another task stops with `SCRATCH_TASK_MISMATCH` (exit 9).
- `--python` defaults to the copy's own `.venv`: in the directory the
  scratch's registry line names, as `atc tmp checkout --lock-dir` records
  it, else in `<scratch>/tree`; without one, `VENV_MISSING` (exit 9). An
  interpreter whose environment installs the tree's packages from another
  checkout stops with `COPY_IMPORT_MISMATCH` (exit 8), naming them.
- `<sha>` must name a commit in `<worktree>`: otherwise `SHA_NOT_FOUND`
  (exit 9) before the scratch is touched, then `PYTHON_NOT_FOUND`, the
  scratch refusals of `atc test run`, `TIME_SLACK_INVALID` and
  `PRIVATE_DESKTOP_INVALID` (exit 9).
- The tests run once unmutated first, `--tests` together with every
  mutant's own `tests`. A red unmutated run prints `BASELINE-RED`, applies no
  mutant and exits 1.
- Each mutant prints one line: `KILLED` with the `FAILED` line of a run the
  kill rule counts, or `SURVIVED` with the summary of a run that exits 0.
- Any other ending of a mutated run (an `ERROR` line alone, exits 2 to 5, a
  timeout, a failure the assertion rule does not count) prints
  `INCONCLUSIVE <label> -> <reason>`, stops the run with exit 1 and leaves
  the rest unrun. An import-guard refusal stops the run with its own exit
  code, and an exception while applying or running a mutant with exit 1,
  each with its token or name on stderr.
- Each touched file is then compared with its Git object at `<sha>`,
  carriage returns ignored, and prints `restored-ok` or `RESTORE-MISMATCH`;
  any mismatch exits 1.
- The last line reads `done: M mutants, S survived <labels>`, or
  `done: N of M mutants, S survived <labels>` after a stop, also one on the
  last mutant, where `N` counts the mutants that printed `KILLED`,
  `SURVIVED` or `INCONCLUSIVE`. Survivors alone leave the exit code 0; the
  reviewer reads them from this line.

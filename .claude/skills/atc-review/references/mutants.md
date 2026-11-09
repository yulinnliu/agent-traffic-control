# `atc test mutants`

```
atc test mutants <scratch> <sha> <worktree> <mutants.json> --python <py> --tests <path>...
```

Applies each mutant alone to the copy at `<tmp>/<scratch>/tree`, the export
of `<sha>`, through `atc test run`, and restores its files after each run.

## Input

`<mutants.json>` is a JSON array of mutants:

```json
[{"label": "...", "edits": [{"path": "...", "old": "...", "new": "..."}]}]
```

Every `old` occurs exactly once in its file; one that does not aborts the
run. Edits follow the file's line endings.

## Result lines

- `<sha>` must name a commit in `<worktree>`: otherwise `SHA_NOT_FOUND`
  (exit 9) before the scratch is touched, then `PYTHON_NOT_FOUND` and the
  scratch refusals of `atc test run` (exit 9).
- The tests run once unmutated first. A red unmutated run prints
  `BASELINE-RED`, applies no mutant and exits 1.
- Each mutant prints one line: `KILLED` with the `FAILED` line of a run that
  exits 1, or `SURVIVED` with the summary of a run that exits 0.
- Any other ending of a mutated run (an `ERROR` line alone, exits 2 to 5, a
  timeout) prints `INCONCLUSIVE <label> -> <reason>`, stops the run with
  exit 1 and leaves the rest unrun. An import-guard refusal stops the run
  with its own exit code, and an exception while applying or running a
  mutant with exit 1, each with its token or name on stderr.
- Each touched file is then compared with its Git object at `<sha>`,
  carriage returns ignored, and prints `restored-ok` or `RESTORE-MISMATCH`;
  any mismatch exits 1.
- The last line reads `done: M mutants, S survived <labels>`, or
  `done: N of M mutants, S survived <labels>` after a stop, where `N` counts
  the mutants that printed `KILLED`, `SURVIVED` or `INCONCLUSIVE`.

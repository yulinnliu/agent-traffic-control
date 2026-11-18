# `atc test --mutants`

`atc test -h` gives the command's form, which tests judge a mutant and the
shape of the mutants file. A mutant's tests are narrowed to the tests the
unmutated run saw execute the lines it edits, child processes included;
`<scratch>/mutants-covered.json` lists each mutant's lines and tests. A
mutant whose edited lines no test executed is reported `NOT_COVERED`
without a run. Each run is pytest with `-x` and a 900-second timeout, and
the mutant's files are restored after it.

## Input

Every `old` occurs exactly once in its file; one that does not aborts the
run. Edits follow the file's line endings. A mutant's own `tests` are the
pytest arguments that judge it in place of the default tests.

## Kill rule

- `failure` (default): a mutated run that exits 1 with a `FAILED` line kills
  the mutant.
- `assertion`: pytest also writes a junit report to
  `<scratch>/junit/<NNN>-<label>.xml`, and the run kills only when every
  failure's message starts with `AssertionError`, `assert ` or
  `Failed: DID NOT RAISE` and no test errored. A failure by any other
  exception, `pytest.fail` among them, is `INCONCLUSIVE`.

## Result lines

- The scratch is registered to the task; a scratch registered to another
  task stops with `SCRATCH_TASK_MISMATCH` (exit 9).
- The tests run with the export's own `.venv`, in the directory under the
  tree whose `pyproject.toml` has `[tool.atc-testkit]`; without one,
  `VENV_MISSING` (exit 9). An interpreter whose environment installs the
  tree's packages from another checkout stops with `COPY_IMPORT_MISMATCH`
  (exit 8), naming them.
- `<sha>` must name a commit of the task's repository: otherwise
  `SHA_NOT_FOUND` (exit 9) before the scratch is touched, then the scratch
  refusals, `TIME_SLACK_INVALID` and `PRIVATE_DESKTOP_INVALID` (exit 9).
- The tests run once unmutated first, the default tests together with every
  mutant's own `tests`, in parallel on the host's workers. A red unmutated
  run prints `BASELINE-RED` naming every failed test, applies no mutant and
  exits 1. A rerun on the same SHA, tests, recorder and testkit (a fix-only
  re-review, an added mutant in a file already recorded) prints
  `BASELINE-OK reused ...` and runs no baseline; `NOTE baseline: not
  reused: <field>` says why one did not.
- One run uses a scratch at a time: a second stops with `MUTANTS_RUNNING`
  (exit 9). A file a killed run left mutated stops the command with
  `TREE_NOT_PRISTINE` (exit 9); `atc test <task> --clean` then exports anew.
- Each mutant prints one line: `KILLED` with the `FAILED` line of a run the
  kill rule counts, `SURVIVED` with the summary of a run that exits 0, or
  `NOT_COVERED` with the edited lines no test executed. A `NOTE <label>:`
  line before it names why a mutant runs its unnarrowed tests instead (a
  file that is not Python, a file no recorded process loaded, lines run
  outside a test).
- `NOT_COVERED` is a test gap like a survivor; it also says nothing about
  tests that read the source instead of running it, which judge a mutant
  only when its `tests` name them by node ID or follow `--`.
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
  `SURVIVED`, `NOT_COVERED` or `INCONCLUSIVE`, and `S` and the labels
  include the `NOT_COVERED` ones. A survivor exits 1; the
  reviewer reads them from this line.

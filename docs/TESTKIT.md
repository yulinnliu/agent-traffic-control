# atc-testkit

What `atc-testkit` gives every project that carries it, so that the projects
classify, select, isolate, run and verify tests the same way. A project
supplies data (§ What a project supplies); the behaviour below is the
testkit's. Temporary and cache locations are in `ATC_HOME.md`; evidence
formats are in `shared/conformance/verification-evidence/README.md`.

The package's own `README.md` is the contract `VERIFICATION.md` § Testing
names: it defines the category meanings, the marker rules and the selection
behaviour for the version it describes, and this document repeats none of
them. This document states the capabilities that README describes from the
release that carries them, in which the package, not each repository, owns
execution commands, parallelism, platform initialization and diagnostics.
`VERIFICATION.md` names the package and the README by path, so the release
lands together with a governance change to that sentence.

## Adoption

- A project vendors one released `atc-testkit` wheel under `vendor/`, with
  its release record `vendor/atc-testkit.json` beside it; all projects of a
  workspace carry the same version.
- The project's root `conftest.py` registers the testkit's pytest plugin and
  nothing else of its own for the concerns below. The plugin registers the
  markers, enforces the marker contract, moves the temporary directory
  (`ATC_HOME.md` § Resolving the locations) and ends child processes
  (§ Child processes).
- `[tool.atc-testkit]` in `pyproject.toml` holds the project's data under the
  keys listed in § What a project supplies. The selection policy and the
  static checks stay in the project's Python file.
- A project keeps no script of its own for a targeted isolated run;
  `atc test run` against the checkout is that run.

## Markers

The plugin registers every marker with one description, so a project's
`pyproject.toml` lists none. Beside the semantic categories and `slow`, which
the README defines, the contract has two markers that place a test in a
stage:

| Marker | Meaning |
| --- | --- |
| `serial` | sensitive to parallel load; runs without xdist workers |
| `interactive_desktop` | needs the interactive Windows desktop (§ Private desktop) |

## Stages

A test run of the gate or the verifier is the same three stages in this
order. Each is one pytest invocation over the selected paths.

| Stage | Marker expression | Workers | Desktop |
| --- | --- | --- | --- |
| `parallel` | `not slow and not serial and not interactive_desktop` | `-n <workers> --dist loadgroup` | private |
| `serial` | `serial and not slow and not interactive_desktop` | `-n 0` | private |
| `interactive` | `interactive_desktop and not slow` | `-n 0` | interactive |

- Every stage passes its worker option explicitly, so a project's `addopts`
  cannot change it.
- Under `loadgroup` a test spreads to any worker unless it carries
  `xdist_group`. The plugin marks, at collection, every test that uses a
  fixture of wider scope with a group: its module for a module-scoped
  fixture, its `module::Class` for a class-scoped one, its package for a
  package-scoped one, so such a fixture is built once as under
  `loadscope`. A test that already carries `xdist_group` keeps its own.
- `<workers>` is, in order: the `--workers N` of the command that starts the
  stages (the CI runner's `--stages`, the gate's `--run`); `test_workers` in
  the host file `~/.atc/config.toml` (`ATC_HOME.md` § Host settings), read
  only when `CI` is not set; `min(8, cpu count)`. The verifier takes no flag,
  so the host file is its override. A count is an integer: ASCII digits on
  the flag, from 1 to the larger of 8 and the logical CPU count (a runner of
  fewer CPUs may oversubscribe up to 8); a TOML integer in the key, from 1 to
  the logical CPU count. A
  bad flag stops with `USAGE`, a host file that cannot be read or decoded
  as UTF-8 or a bad key with `ATC_CONFIG_INVALID` (exit 9), before any check
  or stage runs; a count is never clamped. The
  parallel stage prints `workers: <n> (<flag|~/.atc/config.toml|default>)`
  before it starts; the evidence record holds no count, so a record covers
  a push whatever count ran it. A project that carries the testkit has `pytest-xdist`
  installed.
- A stage that collects no test passes. A run in which every stage collects
  no test is refused with `NO_TESTS_COLLECTED`.
- A project may append `--ignore` arguments to a stage, for a platform for
  example, and nothing else. A stage's evidence record is its name, marker
  expression and ignored paths, as the evidence format defines.
- Each stage has its own data root (§ Isolated environment).
- CI runs the same stages through the same runner and passes its own worker
  count.
- `slow` tests run in CI only, as one further invocation through the same
  runner with the marker expression `slow` and `-n 0`, whatever other
  markers a test carries. The `interactive` stage excludes `slow`, so this
  invocation is the one run of a test that is both `slow` and
  `interactive_desktop`; it therefore runs on the desktop the CI job starts
  it from, with no private desktop, and the runner sets
  `ATC_DESKTOP_GUARD=0` for it unless the caller set the variable (§ Private
  desktop). It passes when it collects no test. The project decides which CI
  job holds it.
- No project has a test that is both `slow` and `interactive_desktop`, so no
  run has shown that a hosted Windows CI runner's job desktop serves one. The
  first such test's first CI run of this invocation shows it.

## Isolated environment

The gate and the verifier first remove every `HAMI_*` variable from their own
process. Every process they start for a static check or a stage then gets an
environment built from the caller's by these rules:

- Removed: every `GIT_*` and `HAMI_*` variable, and every credential variable:
  one whose name ends in `_API_KEY`, `_API_SECRET`, `_SECRET`, `_TOKEN` or
  `_PASSWORD`, or that the project names. Names compare case-insensitively.
- Pointed into the run's working directory: `HOME`, `USERPROFILE`, `APPDATA`,
  `LOCALAPPDATA`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`,
  `PYTHONPYCACHEPREFIX`, pytest's `cache_dir`, and each data-root variable the
  project names, one directory per stage.
- Shared across runs: `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR` and
  `RUFF_CACHE_DIR`, under `~/.atc/cache`. These stores are content-addressed
  and safe to share.
- `TEMP`, `TMP`, `TMPDIR` and `ATC_TMP` carry the values the entry point
  resolved before it redirected the profile.
- Set: `PYTHONIOENCODING=utf-8`, whatever the caller's value, so a Python
  child writes UTF-8 to its pipes and files; output read back is decoded
  as UTF-8 with replacement. `atc` sets the same value for every child it
  starts.
- Written into the directory `HOME` and `USERPROFILE` name: a `.gitconfig`
  with a test identity (`user.name`, `user.email`) and
  `core.longpaths=true`. Git finds it through the profile, so it also holds
  for a Git process a test starts with every `GIT_*` variable removed
  (`GIT_WORKFLOW.md` § Git environment in hooks and tests), and it is the
  only global configuration a stage reads.

The run's working directory is created under `<tmp>/systmp`, removed after a
passing run, and kept with its path printed after a failing one. Every
stage's pytest base directory lies in it (`ATC_HOME.md` § Integration and
the pre-push gate).

## Running and logging

The output rule every `atc` command follows is governance `GIT_WORKFLOW.md`
§ Command results.

- A command's output is shown live and written to a log. The log starts with
  a header: time, platform, Python, working directory, desktop and command.
- The gate keeps the latest run's logs in the checkout's ignored `.tmp/`
  directory, one file per static check and per stage, and the CI runner's
  `--stages` writes its stage logs there. A `.tmp` that is a link or anything
  else that is not a directory stops the gate's `--run` and the runner's
  `--stages` with `LOG_DIRECTORY_UNSUITABLE` (exit 9) before either reads,
  writes or deletes anything in it. The verifier keeps its logs in the run's
  working directory.
- A failure names the log and repeats its last twenty `FAILED` or `ERROR`
  lines.

## Pre-push gate

`<gate> --run` reads the pushed refs from stdin; `<gate> --paths <path>...`
prints the selection for those paths and runs nothing.

1. Check that the pushed commits are what the checkout holds.
2. Select tests from the changed paths with the project's selection policy
   and print the selection. A selection failure aborts the push (exit 2).
3. Run the project's static checks that the changed paths select.
4. For each pushed commit, decide whether an `atc integrate` evidence record
   covers this push's selection and stages. When every pushed commit is
   covered, print `tests REUSED` for each and finish.
5. Otherwise print `tests RUN` with the reason and run the stages.

A record covers a push only when its selector name and stage list are the
gate's own. A record written by a testkit version that differs in either
covers nothing, and a push with no other record runs the stages.

The hook is one line: `exec uv run --frozen <project's uv options> python
<gate> --run`.

## Integration verifier

The verifier runs with the candidate tree as its working directory. It
refuses by printing a token as the first stderr line, a summary after it, and
exiting 1, or exiting 9 for `ATC_TMP_UNSUITABLE`; any unexpected fault refuses
with `VERIFIER_ERROR`. Checks run in
this order:

| Check | Token |
| --- | --- |
| The four `ATC_*` inputs are present | `VERIFY_ENV_MISSING` |
| Each `*_SHA` input is a full object ID | `VERIFY_ENV_INVALID` |
| The verifier's tree, its working directory and `ATC_CANDIDATE_DIR` are one directory | `CANDIDATE_DIR_MISMATCH` |
| `HEAD` is `ATC_INTEGRATION_SHA` | `CANDIDATE_HEAD_MISMATCH` |
| `ATC_EXPECTED_MAIN_SHA` is an ancestor of the candidate | `EXPECTED_MAIN_NOT_IN_CANDIDATE` |
| `ATC_REVIEWED_SOURCE_SHA` is an ancestor of the candidate | `REVIEWED_SOURCE_NOT_IN_CANDIDATE` |
| Every product module the tree defines, and every one the search path offers under the project's prefixes, imports from inside the candidate, on pytest's search path | `CANDIDATE_IMPORT_MISMATCH` |
| Every source root of a product namespace package that the tree holds is among the locations that namespace imports from | `CANDIDATE_IMPORT_MISMATCH` |
| The expected-main to candidate range changes at least one path | `EMPTY_INTEGRATION_DIFF` |
| The selection for that range can be computed | `SELECTION_FAILED` |
| The project's static checks that run first | the project's tokens |
| `uv lock --check` | `UV_LOCK_CHECK_FAILED` |
| `git diff --check` over the range | `DIFF_CHECK_FAILED` |
| `python -m atc_testkit.check --installed` | `TESTKIT_CONTRACT_FAILED` |
| `ruff check` over the project's lint scope | `RUFF_CHECK_FAILED` |
| Repository-wide collection, when the range changes a test, test helper, fixture or `conftest.py` | `COLLECTION_FAILED` |
| The project's other static checks | the project's tokens |
| The stages | `TESTS_FAILED`, `NO_TESTS_COLLECTED` |
| `HEAD` is still `ATC_INTEGRATION_SHA` | `CANDIDATE_HEAD_MOVED` |
| The tree is clean, untracked files included | `CANDIDATE_DIRTY` |

- A project static check keeps its own condition on the changed paths and
  says whether it runs before the shared checks.
- A range is documentation-only when every changed path is Markdown, the
  selection names no test, and no path is automation Markdown (`AGENTS.md`,
  `CLAUDE.md`, or a file under a prefix the project names). Such a range
  skips the stages and runs the project's documentation check.
- After a pass the verifier writes the verification report to the path in
  `ATC_VERIFICATION_REPORT`. A report that cannot be written leaves the
  verdict a pass.
- The gate and the verifier of a project share one selection policy and one
  stage definition, which is what lets a record of one be reused by the other.

### The workspace root's verifier

The workspace root builds the testkit and vendors no wheel. Root `AGENTS.md`
§ Integration verification names its argv file, which runs
`adapters/python/scripts/integration_verify.py` in the candidate's own
`adapters/python/.venv/` built from `uv.lock`. That script supplies the
root's data to the same skeleton, imported from the tree, with the rows a
consumer's data feeds absent: no selection hook, lock, contract, lint or
collection row. § Isolated environment, § Running and logging and the
working-directory rules bind it as they bind a project's verifier.

- Its own rows carry its own tokens: after the ancestry rows it reads the
  range, and a range Git cannot read refuses with `RANGE_UNREADABLE`; its
  static checks follow, then the stages.
- Static checks: `git diff --check` of the range and the static checks of
  the `python-adapter` job in `.github/workflows/validate.yml`, run from
  `adapters/python`: `uv run --locked python scripts/validate_workspace.py`
  and `uv run --locked python scripts/policies.py check
  ../../shared/policies/examples`.
- Selection: in place of the job's full run of the stages, `test_paths`
  maps the range through `select_tests.py` (`adapters/python/README.md`
  § Selected tests) to the pytest paths the stages run in `tests_dir`: `[]`
  for the full suite, where the selector's rules widen to it, and `None` for
  none, which skips the stages. A failure of it refuses with
  `SELECTION_FAILED`.
- Documentation-only (`VERIFICATION.md` § Documentation-only verification),
  decided by consumers: a range is documentation-only, and skips the
  selection and the stages, when every path is Markdown under `docs/`, a
  task record under `.atc/tasks/`, or Markdown the selection maps to no test
  file. Markdown a test reads or a tool renders (the guidance, skills and
  their managed copies, the root's top-level Markdown) selects its readers
  and is not documentation-only. The caller never chooses the exception.
  The log line `classification: full` names every range that is not
  documentation-only; the selector's lines after it say which tests ran.
- The candidate has no `workspace.yaml`, `projects/`, `worktrees/` or
  `state/`, so the checks read the committed tree only, as CI does.
- It writes no verification report, so `atc integrate` records no evidence
  for the root.
- The full suite, and with it the check that fails a session whose tests
  read the root's guidance undeclared (`ROOT_READ_UNDECLARED`), runs only in
  a manually dispatched run of `.github/workflows/validate.yml`
  (`VERIFICATION.md` § Full regression timing and evidence reuse); the
  Owner starts it.

## Child processes

A test starts a child process through the testkit's `start` or `run`.

- The child and everything it starts belong to one Windows Job (a process
  group on other platforms). The teardown of the test that started it ends
  the whole tree and waits for it; `run` does so when it returns, also on a
  timeout.
- A child opens no console window. On Windows the testkit starts it on a
  windowless console (`CREATE_NO_WINDOW`), which the processes it starts
  inherit. A venv `python.exe` is a redirector that passes its console to
  the interpreter it starts; started without a console (`DETACHED_PROCESS`)
  it has none to pass, and the interpreter allocates a new visible one.
- A test whose subject is a detached or new-console launch passes its own
  creation flags through `start`. A new console starts hidden
  (`CREATE_NEW_CONSOLE` with `STARTF_USESHOWWINDOW` and `SW_HIDE`). A launch
  that still shows a window carries `interactive_desktop`.
- At the end of a session the controlling process lists every live process
  whose command line names that session's pytest base directory, excluding
  itself and its ancestors, and fails the run with `CHILD_PROCESSES_ALIVE`
  when one is still listed after up to 10 seconds, or the shorter window the
  ini option `atc_testkit_reap_seconds` gives (above 0 and up to 10; any other
  value stops the run as a usage error, `REAP_SECONDS_INVALID`). A process that a test ended
  itself, for example through a kill-on-close Job of its own, leaves the
  testkit's Job before its teardown finishes, and that wait covers the rest of
  it. Sessions with different base directories never fail one
  another; two runs given the same scratch at the same time share one and are
  not supported.
- The testkit reads the process list and command lines itself, through the
  standard library and the tools the operating system ships (`ps` outside
  Windows). It imports no product code and no third-party package for it.

## Private desktop

How a run keeps the windows it opens off the desktop the user is working on.
Windows only; on other platforms a stage starts normally.

- The testkit starts a command on a new Windows desktop created for it. The
  command and every process it starts open their windows there. Its stdout
  and stderr arrive merged, stdin is empty, and waiting for it returns the
  exit code and releases the desktop.
- The desktop holds the windows a process creates itself, Tk windows among
  them, and xdist workers run on it. A console window is created by the
  console host, and a visible one appears on the interactive desktop
  whatever desktop the process that allocated it runs on.
  Consoles stay hidden through the launch flags: § Child processes for the
  children a test starts, and the product's own flags for the processes
  product code starts.
- When the desktop or the process cannot be created, the stage runs on the
  interactive desktop and one line says so. The log header records `private`
  or `interactive`.
- A test that needs the interactive desktop (native tray icons are the
  known case) carries `interactive_desktop` and runs in the
  `interactive` stage, which still shows windows.
- `atc test run` and `atc test mutants` run on a private desktop and
  exclude `interactive_desktop` tests, saying so in one line:
  `NOTE private desktop: interactive_desktop tests are excluded; ATC_PRIVATE_DESKTOP=0 runs on the interactive desktop with them`.
  `ATC_PRIVATE_DESKTOP=0` runs on the interactive desktop with those tests
  included. Unset and `1` are private; any other value is refused with
  `PRIVATE_DESKTOP_INVALID` (exit 9) before any test runs.
- `atc test private <command> [args...]` runs one command on a private
  desktop (§ Commands); `atc_testkit.desktop.run(command, cwd=, env=)` does
  the same for a Python caller. A project script that starts pytest itself
  goes through one of them.
- A pytest run started directly, outside these, runs on the desktop it was
  started from. On that desktop the plugin's guard refuses a Tk window:
  - The guard is on when the session's own desktop is `WinSta0\Default`.
    `ATC_DESKTOP_GUARD` overrides the detection: `0` off, `1` on, unset
    automatic; any other value stops the run as a usage error
    (`DESKTOP_GUARD_INVALID`, exit 4).
  - A Tk root created for a test not marked `interactive_desktop` is refused
    before Tcl creates the window: `tkinter.Tk()` with `useTk` true and
    `Tk.loadtk` raise `INTERACTIVE_DESKTOP_WINDOW` naming the test and the two
    ways out, `atc test run` or the marker. The test's report is failed at
    teardown as well, so code that catches the exception does not hide it.
  - A fixture's setup and teardown count as part of the test being set up or
    torn down, so a module- or session-scoped Tk fixture set up for a marked
    test passes. A root created at collection, or at session start before
    any test, is refused and stops the run.
  - `tkinter.Tcl()` opens no window and passes. The plugin imports `tkinter`
    only when the guard is on; a Python without it has nothing to guard.
  - A window opened by a child process a test starts is outside the guard;
    the private desktop, which the child inherits, is what covers it.

## CI runner

CI starts pytest through the testkit's runner, which adds to pytest's own
output, for each failing test: one line of context as JSON, one line per
exception of the cause chain with the root cause first, and the last 4000
characters of the report. At interpreter shutdown it lists the non-daemon
threads still alive. A project may add fields to the context line and hints
to the summary, and may name one initialization function, which the runner
calls before pytest starts, for preparation only that project needs.

## Commands

The test tools a review or a developer runs are subcommands of `atc`, one
implementation on every platform. A machine that runs them has `atc`
installed (`uv tool install -e adapters/python`), as integration already
requires. Every variable ATC itself defines starts with `ATC_`.

No run has confirmed this on a cloud writer host, a clone with no
`workspace.yaml`: installing `atc` from a clone there and running
`atc test run` against a project clone is untried, and what such a host runs
when it has no `atc` is not decided.

`atc test` and `atc tmp` are implemented in `atc-testkit`. The adapter
depends on the package by its path in the tree, and `atc` dispatches to it;
`atc integrate` takes the location rule from the same package.

`atc <command> [<subcommand>] --help` prints a usage line that starts with
`usage: atc <command> [<subcommand>]` and exits 0; output the console cannot
encode is written as backslash escapes. A wrong form prints the usage on
stderr and exits 2, and no option is matched by an abbreviation; an argument
error that argparse finds adds its error line and
`next: atc <command> [<subcommand>] --help`. A command
that imports a module the installed `atc` lacks stops with
`ERROR MISSING_DEPENDENCY` (exit 2) naming the module and the reinstall
command of the root's `AGENTS.md`.

### `atc test run --scratch <name> [--tree <tree>] [--python <py>] [--select <base>] [--label <label>] [--timeout <s>] [--progress] [-- <pytest args>]`

One isolated pytest run inside a tree, a worktree or an exported copy, in
the scratch `<name>` (`ATC_HOME.md` § Test scratch), its output kept in a
log. In order:

1. Before the run, each refusal exits 9 and runs no test. Check and create
   the scratch: `SCRATCH_OUTSIDE_TMP`, `SCRATCH_PATH_TOO_LONG`,
   `SCRATCH_NOT_RESOLVED`, `SCRATCH_NOT_CREATED`. Resolve `--tree` and a
   path-form `--python` against the caller's directory:
   `TREE_NOT_FOUND`, `PYTHON_NOT_FOUND`. `--tree` defaults to the tree the
   scratch's registry line names, as `atc tmp checkout` records it; without
   either, `TREE_NOT_FOUND`. `--python` defaults to the interpreter of the
   tree's environment (§ Toolchain); without one, `VENV_MISSING`, which
   names `atc tmp checkout` and the project's `AGENTS.md` preparation, and
   never another Python.
2. `--select <base>` runs the tree's `[tool.atc-testkit]` `selector`, a
   list of strings: the argv of a command that takes changed paths as
   arguments and prints the test paths to run as a JSON array on its last
   line. It runs with the run's Python and the tree as its working
   directory. The changed paths are `git diff --name-only <base>` and the
   untracked files of the tree's working tree, relative to its top; for a
   tree outside a working tree, the diff from `<base>` to the commit of
   the scratch's registry line in its source worktree. A tree without the
   key stops with `SELECTION_UNDECLARED`, a base Git cannot resolve with
   `SELECTION_BASE_UNKNOWN`, an export whose scratch names no source with
   `SELECTION_SOURCE_UNKNOWN`, a selector that fails or prints no JSON
   array of strings with `SELECTOR_FAILED`. The line
   `SELECT <base>: <n> changed paths, <k> test paths` follows; with none
   selected, `NO_TESTS no test selected` ends the command with exit 0. The
   selected paths come before the pytest arguments.
3. Remove every `HAMI_*` variable. Point each data-root variable the tree's
   `[tool.atc-testkit]` names, and `QUANT_DATA_ROOT` when it names none, at
   `<scratch>/qdr`. Point the toolchain's bytecode variable,
   `PYTHONPYCACHEPREFIX`, at `<scratch>/pyc`, so no child of the run (the
   guard, the selector, pytest) writes bytecode into the tree. Make the
   scratch the temporary directory. `ATC_TEST_TIME_SLACK` passes through
   and is checked first: `TIME_SLACK_INVALID` (§ Time slack).
4. Set `PYTHONPATH` to the tree's `packages/*/src` and `src` directories. One
   that is absent after entering the tree is refused with
   `PYTHONPATH_MISSING` (exit 8).
5. Import every regular package under those directories, descending through
   namespace directories, and require each to load from inside the tree:
   `COPY_IMPORT_MISMATCH` (exit 8). This guard is what proves the copy, not
   an installed checkout, is what runs.
6. Run pytest with `PYTHONUNBUFFERED=1`, `-q`, `-n 0` when xdist is
   installed, `-p no:cacheprovider` and `--basetemp <scratch>/bt`, its output
   written as it arrives to `<scratch>/runs/<UTC time>-<label>.log` after
   the § Running and logging header. The console shows `LOG <path>` first,
   then the private desktop's exclusion line (§ Private desktop), and, with
   `--progress`, the output too.
7. One summary line ends the command: `PASS`, `FAIL`, `NO_TESTS`,
   `INTERRUPTED` or `ERROR exit <code>`, pytest's counts, the failed and
   errored tests by node ID, and the log path. A run that `--timeout` ends
   prints the log's last fifteen lines and `TIMEOUT` (exit 124); one the
   caller interrupts prints them and `INTERRUPTED` (exit 130). Neither ever
   reports a pass. Otherwise the exit code is pytest's.

### `atc test mutants <scratch> <sha> <worktree> <mutants.json> --python <py> --tests <path>...`

Applies each mutant alone to `<scratch>/tree`, the export of `<sha>`, through
`atc test run`.

- `<sha>` must name a commit in `<worktree>` (`git rev-parse --verify`):
  `SHA_NOT_FOUND` (exit 9) before the scratch is touched, then
  `PYTHON_NOT_FOUND`, the scratch refusals of `atc test run` and
  `PRIVATE_DESKTOP_INVALID` (exit 9). The private desktop's exclusion line
  comes first when the runs are private.
- The tests run once unmutated first; a failing baseline prints
  `BASELINE-RED` and exits 1 before any mutant is applied.
- A mutant is a label and a list of edits `{path, old, new}`. An `old` that
  does not occur exactly once in its file aborts the run. Edits follow the
  file's line endings.
- Each mutant prints one line, and its files are restored whether or not the
  run completes. Every edit and restore moves the file's modification time
  to a later whole second than its last, so a bytecode cache, which records
  that second and the size, never stands in for the source the run is meant
  to see. A run that exits 1 with a `FAILED` line prints `KILLED` with
  that line; a run that exits 0 prints `SURVIVED` with the summary.
- Any other ending of a mutated run, an `ERROR` line alone, exits 2 to 5 or a
  timeout, prints `INCONCLUSIVE <label> -> <reason>` and aborts the run with
  exit 1. An import-guard refusal aborts the run with its exit code, and an
  exception while applying or running a mutant with exit 1, each with its
  token or name on stderr. The remaining mutants of an aborted run stay unrun.
- At the end every touched file is compared with its Git object at `<sha>`,
  carriage returns ignored: `restored-ok` or `RESTORE-MISMATCH` per file,
  exit 1 on any mismatch. The last line counts the mutants and names the
  survivors: `done: M mutants, S survived <labels>`, or
  `done: N of M mutants, S survived <labels>` after any abort, also one on the
  last mutant, where `N` counts the mutants that printed a `KILLED`,
  `SURVIVED` or `INCONCLUSIVE` line.

### `atc test private [--] <command> [args...]`

Runs `<command>` on a private desktop from the current directory with the
current environment. It prints `desktop: private`, or `desktop: interactive`
with the line § Private desktop names when no desktop can be created, then
streams the command's merged output, and exits with its code. Without a
command it prints its usage (exit 2).

### `atc tmp checkout`, `atc tmp list`, `atc tmp clean`, `atc tmp prune`

A commit exported into a scratch with its environment built, the scratch
registry and deletion (`ATC_HOME.md` § Checkout, § Scratch registry,
§ Deleting).

## What a project supplies

In the project's Python file:

- The selection policy: package dependencies, path prefixes, full-suite
  files, and how a missing remote baseline is read.
- Its static checks with their tokens, path conditions and position, and the
  documentation check.

In `[tool.atc-testkit]`; the first seven keys are required:

| Key | Holds |
| --- | --- |
| `gate` | the gate script's path |
| `hook` | the pre-push hook's path |
| `workflow` | the CI workflow's path |
| `ci_job` | the CI job that runs `atc_testkit.check` |
| `unmarked_baseline` | the unmarked-baseline file's path |
| `slow_ci_job` | the CI job that runs `slow` tests |
| `product_module_prefixes` | product module prefixes for the import check |
| `credential_variables` | extra credential variable names |
| `data_root_variables` | data-root variable names |
| `automation_markdown_prefixes` | automation-Markdown path prefixes |
| `lint_scope` | the paths the lint check reads |
| `hook_uv_options` | `uv` options for the hook |
| `ci_platforms` | the CI platform matrix |
| `ci_init` | the CI runner's initialization function, as `module:function` |
| `ignore` | `--ignore` paths appended to a stage, under `nt` and `posix` |

Elsewhere in the project:

- Test paths and pytest's search path, as `testpaths` and `pythonpath` in
  `[tool.pytest.ini_options]`.
- The verifier argv file, which is a project file and carries the `uv`
  options the project's verifier needs, an interpreter version for example.

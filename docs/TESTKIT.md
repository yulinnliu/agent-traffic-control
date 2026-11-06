# atc-testkit

What `atc-testkit` gives every project that carries it, so that the projects
classify, select, isolate, run and verify tests the same way. A project
supplies data (§ What a project supplies); the behaviour below is the
testkit's. Temporary and cache locations are in `ATC_HOME.md`; evidence
formats are in `shared/conformance/verification-evidence/README.md`.

## Adoption

- A project vendors one released `atc-testkit` wheel; all projects of a
  workspace carry the same version.
- The project's root `conftest.py` registers the testkit's pytest plugin and
  nothing else of its own for the concerns below. The plugin registers the
  markers, enforces the marker contract, moves the temporary directory
  (`ATC_HOME.md` § Resolving the locations) and ends child processes
  (§ Child processes).
- `[tool.atc-testkit]` in `pyproject.toml` names the project's gate, hook,
  workflow, CI job, unmarked-baseline file and the data listed in
  § What a project supplies.

## Markers

The plugin registers these markers with one description each, so a project's
`pyproject.toml` lists none of them.

| Marker | Meaning |
| --- | --- |
| `unit`, `integration`, `render`, `statistical` | semantic category; a test carries exactly one |
| `slow` | unsuitable for the fast loop; combined with one semantic category |
| `serial` | sensitive to parallel load; runs without xdist workers |
| `interactive_desktop` | needs the interactive Windows desktop (§ Private desktop) |

A test definition with no semantic category fails collection unless the
project's unmarked-baseline file lists it.

## Stages

A test run of the gate or the verifier is the same three stages in this
order. Each is one pytest invocation over the selected paths.

| Stage | Marker expression | Workers | Desktop |
| --- | --- | --- | --- |
| `parallel` | `not slow and not serial and not interactive_desktop` | `min(8, cpu count)`, `--dist loadscope` | private |
| `serial` | `serial and not slow and not interactive_desktop` | none | private |
| `interactive` | `interactive_desktop and not slow` | none | interactive |

- A stage that collects no test passes. A run in which every stage collects
  no test is refused with `NO_TESTS_COLLECTED`.
- A project may append pytest arguments to a stage (an `--ignore` for a
  platform, for example); the appended arguments are part of the stage's
  evidence record.
- Each stage has its own data root (§ Isolated environment).
- CI runs the same stages through the same runner and passes its own worker
  count.

## Isolated environment

Every process the gate or the verifier starts for a static check or a stage
gets an environment built from the caller's by these rules:

- Removed: every `GIT_*` and `HAMI_*` variable, and every credential variable:
  one whose name ends in `_API_KEY`, `_API_SECRET`, `_SECRET`, `_TOKEN` or
  `_PASSWORD`, or that the project names. Names compare case-insensitively.
- Pointed into the run's working directory: `HOME`, `USERPROFILE`, `APPDATA`,
  `LOCALAPPDATA`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`,
  `PYTHONPYCACHEPREFIX`, pytest's `cache_dir`, and each data-root variable the
  project names, one directory per stage.
- Shared across runs: `UV_CACHE_DIR` and `RUFF_CACHE_DIR`, under
  `~/.atc/cache`. These caches are content-addressed and safe to share.
- `TEMP`, `TMP`, `TMPDIR` and `ATC_TMP` carry the values the entry point
  resolved before it redirected the profile.

The run's working directory is created under `<tmp>/systmp`, removed after a
passing run, and kept with its path printed after a failing one.

## Running and logging

- A command's output is shown live and written to a log. The log starts with
  a header: time, platform, Python, working directory, desktop and command.
- The gate keeps the latest run's logs in the checkout's ignored `.tmp/`
  directory, one file per static check and per stage. The verifier keeps its
  logs in the run's working directory.
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

The hook is one line: `exec uv run --frozen <project's uv options> python
<gate> --run`.

## Integration verifier

The verifier runs with the candidate tree as its working directory. It
refuses by printing a token as the first stderr line, a summary after it, and
exiting 1; any unexpected fault refuses with `VERIFIER_ERROR`. Checks run in
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
| The expected-main to candidate range changes at least one path | `EMPTY_INTEGRATION_DIFF` |
| The selection for that range can be computed | `SELECTION_FAILED` |
| `uv lock --check` | `UV_LOCK_CHECK_FAILED` |
| `git diff --check` over the range | `DIFF_CHECK_FAILED` |
| `python -m atc_testkit.check --installed` | `TESTKIT_CONTRACT_FAILED` |
| `ruff check` over the project's lint scope | `RUFF_CHECK_FAILED` |
| Repository-wide collection, when the range changes a test, test helper, fixture or `conftest.py` | `COLLECTION_FAILED` |
| The project's own static checks | the project's tokens |
| The stages | `TESTS_FAILED`, `NO_TESTS_COLLECTED` |
| `HEAD` is still `ATC_INTEGRATION_SHA` | `CANDIDATE_HEAD_MOVED` |
| The tree is clean, untracked files included | `CANDIDATE_DIRTY` |

- A range is documentation-only when every changed path is Markdown, the
  selection names no test, and no path is automation Markdown (`AGENTS.md`,
  `CLAUDE.md`, or a file under a prefix the project names). Such a range
  skips the stages and runs the project's documentation check.
- After a pass the verifier writes the verification report to the path in
  `ATC_VERIFICATION_REPORT`. A report that cannot be written leaves the
  verdict a pass.
- The gate and the verifier of a project share one selection policy and one
  stage definition, which is what lets a record of one be reused by the other.

## Child processes

A test starts a child process through the testkit's `start` or `run`.

- The child and everything it starts belong to one Windows Job (a process
  group on other platforms). The teardown of the test that started it ends
  the whole tree and waits for it; `run` does so when it returns, also on a
  timeout.
- A child opens no console window unless the test passes its own creation
  flags.
- At the end of a session the controlling process lists every live process
  whose command line names the session's temporary root, excluding itself and
  its ancestors, and fails the run with `CHILD_PROCESSES_ALIVE` when one
  exists.

## Private desktop

How a run keeps the windows it opens off the desktop the user is working on.
Windows only; on other platforms a stage starts normally.

- The testkit starts a command on a new Windows desktop created for it. The
  command and every process it starts open their windows there. Its stdout
  and stderr arrive merged, stdin is empty, and waiting for it returns the
  exit code and releases the desktop.
- When the desktop or the process cannot be created, the stage runs on the
  interactive desktop and one line says so. The log header records `private`
  or `interactive`.
- A test that needs the interactive desktop (native tray icons and some Tk
  windows are the known cases) carries `interactive_desktop` and runs in the
  `interactive` stage, which still shows windows.
- `atc test run` runs on a private desktop when called with
  `ATC_PRIVATE_DESKTOP=1`, and then excludes `interactive_desktop` tests.
- A pytest run started directly, outside the gate, the verifier and
  `atc test run`, runs on the desktop it was started from.

## CI runner

CI starts pytest through the testkit's runner, which adds to pytest's own
output, for each failing test: one line of context as JSON, one line per
exception of the cause chain with the root cause first, and the last 4000
characters of the report. At interpreter shutdown it lists the non-daemon
threads still alive. A project may add fields to the context line and hints
to the summary.

## Commands

The test tools a review or a developer runs are subcommands of `atc`, one
implementation on every platform. A machine that runs them has `atc`
installed (`uv tool install -e adapters/python`), as integration already
requires.

| Command | What it does |
| --- | --- |
| `atc test run <tree> <scratch> <python> <label> <pytest args...>` | one isolated pytest run in a scratch (`ATC_HOME.md` § Test scratch) |
| `atc test mutants <scratch> <sha> <worktree> <mutants.json> --python <py> --tests <path>...` | applies each mutant alone to the scratch copy, runs the tests, restores the file and proves its bytes equal Git |
| `atc tmp list`, `atc tmp clean`, `atc tmp prune` | the scratch registry and cleanup (`ATC_HOME.md`) |

Every environment variable an ATC tool reads or sets starts with `ATC_`.

## What a project supplies

- The selection policy: package dependencies, path prefixes, full-suite
  files, and how a missing remote baseline is read.
- Its static checks and their tokens, the lint scope, and the documentation
  check.
- Product module prefixes for the import check, extra credential variable
  names, data-root variable names, and automation-Markdown prefixes.
- Arguments appended to a stage, `uv` options for the hook, test paths, and
  the CI platform matrix.

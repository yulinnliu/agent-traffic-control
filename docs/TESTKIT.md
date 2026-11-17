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
  `atc test ID` against the task's worktree is that run.

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

| Stage | Category expression | Workers | Desktop |
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
  stages (the CI runner's `--stages`, the gate's `--run`); 1 in a run nested
  in one that holds host workers (§ Host worker budget); `test_workers` in
  the host file `~/.atc/config.toml` (`ATC_HOME.md` § Host settings), read
  only when `CI` is not set; the default. In the host pool the default asks
  for the pool size, and memory cuts it only when the stage takes its tokens
  (§ Host worker budget); outside it (CI, `test_worker_pool = 0`) the default
  is `min(logical CPU count, memory ceiling)`. Before the parallel stage of a
  pooled Python run takes its tokens it collects its tests with
  `--collect-only -q`, and the default asks for at most one worker per 8
  collected tests, at least 2; a flag and the host file's count are not cut
  by it. The verifier takes no flag, so the host file is its override. A
  count is an integer: ASCII digits on the flag, from 1 to the larger of 8
  and the logical CPU count (a runner of fewer CPUs may oversubscribe up to
  8, since the tests are largely subprocess- and timer-bound); a TOML integer
  in the key, from 1 to the logical CPU count. A bad flag stops with `USAGE`,
  a host file that cannot be read or decoded as UTF-8 or a bad key with
  `ATC_CONFIG_INVALID` (exit 9), before any check or stage runs.
- The memory ceiling is the workers available physical memory holds after a
  1 GiB reserve, at least 1, each worker counted at, in order: the project's
  `worker_memory_mib` (§ What a project supplies); the project's recorded
  cost, the largest of its last 10 recorded runs times 1.25 and at least
  128 MiB (§ Host worker budget), read only when `CI` is not set; 256 MiB.
  The available memory is read on Windows and on Linux, where a cgroup v2
  limit lowers it; elsewhere it is unknown and nothing is cut. The ceiling
  cuts the flag and the default, on CI as elsewhere; the CPU count never cuts
  a flag, and the host file's count is not cut.
- The parallel stage prints its count once it holds its tokens, and writes
  the same line into its log's header:
  `workers: <n>[ of <pool>] (<source>[ <asked>][, <cut>][; <limit>])`, the
  source being `flag`, `~/.atc/config.toml`, `default` or `nested in a
  budgeted run`, `of <pool>` naming the host pool's size when the run is in
  it. A count that was cut names the count asked for and what cut it:
  `workers: 6 (flag 8, memory ceiling: 7.0 GiB available, 256 MiB/worker
  default)`, the per-worker cost's source being `pyproject`, `recorded` or
  `default`; `workers: 4 of 18 (default 18, 31 tests collected, 8 per
  worker)`. One that memory or the host pool cut, delayed or shares when it
  took its tokens adds that part, `workers: 6 of 18 (default 18; host
  budget: 6 of 18 free, 2 runs active)`. With `--jobserver-auth` in
  `MAKEFLAGS` the line adds `make jobserver present, not joined`: the
  testkit is no jobserver client, and make's tokens and the host pool's are
  counted apart. The evidence record holds no count, so a record covers a
  push whatever count ran it. A project that carries the testkit has
  `pytest-xdist` installed.
- A stage that collects no test passes. A run in which every stage collects
  no test is refused with `NO_TESTS_COLLECTED`.
- A project may append `--ignore` arguments to a stage, for a platform for
  example, and nothing else. A stage's evidence record is its name, category
  expression and ignored paths, as the evidence format defines.
- Each stage has its own data root (§ Isolated environment).
- CI runs the same stages through the same runner and passes its own worker
  count or takes the default; it uses no host pool.
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

## Host worker budget

The runs on one host share a pool of worker tokens, so concurrent full
suites do not add up to more workers than the host holds. CI runs (`CI`
set) use no pool.

- The pool is `~/.atc/run/workers.lock` of the user the entry point runs
  as, `ATC_WORKER_POOL_DIR` when that names an absolute directory. It holds
  `test_worker_pool` tokens (`ATC_HOME.md` § Host settings), three quarters
  of the logical CPUs rounded up by default; `0` turns the pool off. A token
  is a lock on one byte of the file, so the operating system frees it when
  the process holding it ends, however it ends. A pool directory that is a
  link or not a directory stops the run with `WORKER_POOL_UNSUITABLE`, one
  the run cannot open or lock with `WORKER_POOL_UNAVAILABLE` (exit 9, before
  any stage).
- Each stage takes its tokens before it starts and gives them all back when
  it ends: the parallel stage its count, down to a floor of 2, or the whole
  count of a flag; the serial and interactive stages one each. `atc test
  ID` takes its pytest `-n K` (cut by the memory ceiling and the pool size
  and rewritten when cut), one token for `-n 0`, and otherwise the default
  count down to 2 (§ Commands), its `-n` becoming the granted count;
  `atc test ID --mutants FILE` takes what `atc test ID` takes for its
  baseline's tests, whether it runs or reuses that baseline, and holds it
  to its end for the mutants. Static checks
  take none. A flag
  above the pool size runs the pool size, and its line names the pool,
  `workers: 2 (flag 8, host pool: 2)`; on CI, outside the pool, only the
  memory ceiling cuts a flag.
- A run takes the tokens free when it asks, never more than memory holds at
  that moment, counting the runs granted in the last 60 seconds at their
  full size. Fewer free than its floor, it waits: it holds the pool's
  turnstile and no token until its floor is free, and no later run takes a
  token meanwhile. It prints
  `WAIT host workers: <stage> needs <floor> (wants <n>), <free> of <size> free; <k> runs active, oldest <label> pid <pid> for <time>`
  when it starts waiting and every 30 seconds after.
- A run that holds tokens sets `ATC_TEST_WORKERS_HELD` for its stages. A run
  started with it set, a test's gate or verifier for example, takes no token
  and never waits, and its parallel stage takes one worker unless a flag
  names a count.
- The wait is not part of any timeout: the test timeout and the
  mutant timeout start once the tokens are held, and the verifier writes its
  total wait to `ATC_WORKER_WAIT_FILE`, which `atc integrate` and `atc test
  ID --candidate` add to its clock, by at most the verifier timeout. The total wait
  of a run has its own limit: `ATC_WORKER_MAX_WAIT` (which `atc integrate`
  sets to the verifier timeout), else the run's own timeout (the test dir's `timeout`, 900 seconds for `atc test
  ID --mutants`), else
  `test_worker_max_wait`, else 1800 seconds. A run past it stops with
  `WORKER_WAIT_TIMEOUT`, the time it waited, the runs it waited behind and
  `hint: atc doctor -v`: exit 124 from the gate, the CI runner, `atc test ID`
  and `atc test ID --mutants`; the verifier refuses with it (§ Integration
  verifier), and `atc integrate` stops with it without judging the
  candidate.
- Each process keeps one row in `~/.atc/run/workers/`, locked for its life:
  its label, stage, tokens and wait, and while a stage or run holds tokens
  its log and its `workers:` line. Only the locks decide who holds a
  token; a row nobody holds is stale, whatever process now has its PID, and
  the next run that reads it removes it. `atc doctor -v` lists the pool's free
  tokens, the runs holding tokens and the runs waiting (`--json`: the
  `worker_pool` field); it removes nothing.
- On Windows a stage's process tree is in a kill-on-close Job of the process
  holding its tokens, so the tree ends with that process, and what is left of
  it when the stage ends ends with it. Elsewhere a stage's process group
  outlives a killed holder until it exits.
- A pooled stage of 4 workers or more records its memory cost per worker in
  `~/.atc/run/memory/<project>.json`, `<project>` being the `[project]`
  name of the tested tree's `pyproject.toml`, else its directory name: on
  Windows the Job's committed peak (`PeakJobMemoryUsed`) divided by its
  workers, elsewhere the largest child's peak resident size. The file keeps
  the last 10 values; the memory ceiling counts a worker at their largest
  times 1.25, at least 128 MiB, unless the project declares
  `worker_memory_mib` (§ Stages).
- On Windows `atc doctor -v` reports the physical memory available and the
  kernel nonpaged pool: `READY memory: 17.4 of 31.8 GiB available; kernel
  nonpaged pool 1.0 GiB`. A nonpaged pool above `max(4 GiB, physical
  memory / 8)`, or `nonpaged_pool_warn_gib` of the host file (`ATC_HOME.md`
  § Host settings), is a driver leaking kernel memory and gives a `WARN`
  line, as does less memory available than the reserve and 2 workers at
  256 MiB. `WARN` leaves the exit code alone; `--json` carries the figures
  in its `memory` field.

## Toolchain

A toolchain is how a tree's tests are prepared, selected and run. The stages,
the isolation, the gate, the verifier, the CI runner and the commands go
through it; `[tool.atc-testkit]` `toolchain` names it, and `python`, the one
toolchain, is the default. Any other name stops the gate, the verifier and
`atc test ID --sha SHA` with `TOOLCHAIN_UNKNOWN` (exit 9).

| The toolchain supplies | `python` |
| --- | --- |
| The runner of a stage, given the category expression, the workers and the ignored paths | pytest through `python -m atc_testkit.ci`: `-q -m <expression>`, `-n <workers> --dist loadgroup` or `-n 0`, `--ignore=<path>` |
| The exit code of a run that collects no test | 5 |
| The dependency files whose digests evidence compares | `uv.lock`, `.python-version` |
| The lock check of the verifier | `uv lock --check` |
| The repository-wide collection of the verifier | `python -m pytest -q -n 0 --collect-only` |
| The variable pointed at the run's bytecode directory | `PYTHONPYCACHEPREFIX` |
| The environment `atc test ID --sha SHA` prepares in an export, and its interpreter | `uv sync --locked` into `.venv` |
| The search path the verifier's import check uses, and the test paths that make a range a collection change | `pythonpath` and `testpaths` of `[tool.pytest.ini_options]` |
| A stage's log name in the verifier's working directory | `pytest-<stage>.log` |
| The `<tmp>/systmp` entries its runner expires itself, which the scratch pruning leaves | `pytest-of-*` |

A stage's marker is a category expression over the § Markers names with
`and`, `or`, `not` and parentheses; the toolchain turns it into its runner's
selection, and every toolchain selects the same tests for it. One function of
the `python` toolchain builds every pytest command line, always in the order
above.

## Isolated environment

The gate and the verifier first remove every `HAMI_*` variable from their own
process. Every process they start for a static check or a stage then gets an
environment built from the caller's by these rules:

- Removed: every `GIT_*` and `HAMI_*` variable, and every credential variable:
  one whose name ends in `_API_KEY`, `_API_SECRET`, `_SECRET`, `_TOKEN` or
  `_PASSWORD`, or that the project names. Names compare case-insensitively.
- Pointed into the run's working directory: `HOME`, `USERPROFILE`, `APPDATA`,
  `LOCALAPPDATA`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`,
  the toolchain's bytecode variable, pytest's `cache_dir`, and each
  data-root variable the project names, one directory per stage.
- Shared across runs: `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`,
  `RUFF_CACHE_DIR` and `PRE_COMMIT_HOME`, under `~/.atc/cache`. These stores are content-addressed
  and safe to share.
- `TEMP`, `TMP`, `TMPDIR` and `ATC_TMP` carry the values the entry point
  resolved before it redirected the profile, and `ATC_TEST_TIME_SLACK`
  passes through (§ Time slack).
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

## Time slack

`ATC_TEST_TIME_SLACK` is the seconds a project's timing tests may add to
their limits on a slow host: a number from 0 to 30, 2 when unset or empty.
Any other value stops the gate, the verifier, the CI runner and
`atc test ID` with `TIME_SLACK_INVALID` (exit 9) before any check or test.

- The isolation passes it to every stage; a project's own variable for the
  same purpose is a project setting the isolation removes with the rest.
- The log header shows `time slack: <seconds> (<ATC_TEST_TIME_SLACK|default>)`
  after the command line, and the parallel stage prints the same line after
  its workers line.
- The verification report records it, and evidence made with another slack
  covers nothing (`TIME_SLACK_MISMATCH`).

## Running and logging

The output rule every `atc` command follows is governance `GIT_WORKFLOW.md`
§ Command results.

- A command's output is shown live and written to a log. The log starts with
  a header: time, platform, Python, working directory, desktop, command and
  time slack.
- The gate keeps the latest run's logs in the checkout's ignored `.tmp/`
  directory, one file per static check and per stage, and the CI runner's
  `--stages` writes its stage logs there. A `.tmp` that is a link or anything
  else that is not a directory stops the gate's `--run` and the runner's
  `--stages` with `LOG_DIRECTORY_UNSUITABLE` (exit 9) before either reads,
  writes or deletes anything in it. The verifier keeps its logs in the run's
  working directory.
- A failure names the log and repeats its last twenty `FAILED` or `ERROR`
  lines.

## Stage times

`atc test ID`, each run of `atc test ID --mutants` and the integration
verifier time each stage they run and report it in one place.

- A stage is timed from its host worker tokens held to its end, so a wait
  for tokens is not part of it. Its line is
  `ELAPSED <stage> <seconds>s workers <n> <result>`, with one decimal.
- The stages:

  | Command | Stage | Workers | Result |
  | --- | --- | --- | --- |
  | `atc test ID` | its log label, `related` or `all` | the pytest `-n` it ran, 1 in process | the summary line's first word |
  | `atc test ID --mutants` | `baseline` (none when reused), then `mutant:<label>` per mutant | the `-n` the run ran, 1 in process | `PASS` or `FAIL` for the baseline, the mutant's verdict |
  | the verifier, through `atc test ID --candidate` and `atc integrate` | `checks` (every static check before the stages, or up to the first that fails), then each stage of § Stages | 1 for `checks`, the granted count for `parallel`, 1 for the others | `PASS`, `FAIL`, `NO_TESTS` or `TIMEOUT` |

- `atc test ID` prints its line just before the summary line. A mutant run
  prints its line just before the verdict line. The verifier prints its
  lines into its log, and `atc test ID --candidate` repeats them before
  its `PASS candidate` line.
- Each line is also one JSON record appended to `timings.jsonl` in the host
  pool directory (`ATC_HOME.md` § Layout). A record has the fields `time` (UTC),
  `task`, `sha`, `command` (`test`, `mutants` or `verify`), `stage`,
  `seconds`, `workers` and `result`.
  - `sha` is the commit tested: for `atc test ID`, the export's commit, else
    the tree's `HEAD`.
  - The verifier takes `task` from the optional input `ATC_TASK`, which
    `atc integrate` and `atc test ID --candidate` set for a candidate of one
    task.
  - An empty string is a value no input gave.
  - A record that cannot be written is skipped. A reader skips a line that is
    not a JSON object. Nothing deletes the file or reads it to decide
    anything.
- No evidence record or report carries a time, so evidence written before
  and after this section compares the same.

## Pre-push gate

`<gate> --run` reads the pushed refs from stdin; `<gate> --paths <path>...`
prints the selection for those paths and runs nothing.

1. Set aside each `refs/tags/*` line whose commit a remote-tracking ref
   already contains, printing `pre-push: <ref> skipped`: it brings no new
   commit. A push of such lines only finishes here. Then check that the
   pushed commits of the other lines are what the checkout holds.
2. Select tests from the changed paths with the project's selection policy
   and print the selection. A selection failure aborts the push (exit 2).
3. Run the project's static checks that the changed paths select.
4. For each pushed commit, decide whether an `atc integrate` evidence record
   of a run on the same inputs covers this push's selection and stages
   (§ Input evidence). When every pushed commit is covered, print
   `tests REUSED` for each and finish.
5. Otherwise print `tests RUN` with the reason and run the stages.

A record covers a push only when its selector name and stage list are the
gate's own. A record written by a testkit version that differs in either
covers nothing, and a push with no other record runs the stages.

The hook is one line: `exec uv run --frozen <project's uv options> python
<gate> --run`.

## Lint stage

The lint stage runs the repository's `.pre-commit-config.yaml` through
pre-commit before any test: the static rules that read files and syntax
trees and run no product code. One list serves every entry:

- the commit hook pre-commit installs;
- `atc test ID`, over the files changed since the task's `base_sha` and the
  untracked ones, before its tests, and `atc test ID --lint` alone
  (§ Commands);
- the integration verifier, over the range
  (`pre-commit run --from-ref <ATC_EXPECTED_MAIN_SHA> --to-ref <ATC_INTEGRATION_SHA>`),
  with the token `LINT_FAILED` (§ Integration verifier);
- the CI job, over every file.

Its rules:

- A repository adopts the lint stage by holding `.pre-commit-config.yaml` at
  its root. Without it, `atc test ID`, `atc test ID --lint` and the
  verifier print `LINT_NOT_CONFIGURED: no .pre-commit-config.yaml at
  <root>; the lint stage is not adopted and does not run` and go on, with
  no lint row and no `LINT_FAILED`.
- pre-commit runs as `python -m pre_commit` with the interpreter the tests
  run with, so a repository with the config pins `pre-commit` in its
  development dependencies (`atc_testkit.check`). With the config, a stage
  that cannot run, pre-commit missing or a hook environment that fails to
  build, is `LINT_FAILED`.
  Hook environments live in `PRE_COMMIT_HOME`, `~/.atc/cache/pre-commit`
  (`ATC_HOME.md` § Cache).
- Vale runs as the `vale` hook of its own repository, pinned by `rev`, with
  `language: golang`: pre-commit downloads the Go its `language_version`
  pins and builds Vale from source, so a host needs no Go of its own. The
  first run on a host downloads that Go and Vale's modules and builds the
  binary, about 35 seconds and 300 MB in the hook cache on the workspace's
  Windows host; later runs reuse the environment. A new `rev` or
  `language_version` builds again. The build reports `vale version master`,
  since `go install` sets no version; the `rev` is the version.
- Vale reads `shared/lint/vale.ini` (in a project `.atc/lint/vale.ini`),
  whose `ATC` style holds the role-term rule (`error`: a role term written
  in another language) and two that only report (`warning`): `the owner`
  in lower case, and wording that may narrate a change (`previously`,
  `used to be`, `no longer`).
- A failure ends the run before pytest. `atc test` prints pre-commit's last
  lines, then `STOP LINT_FAILED: pre-commit exited <code> on <n> files; log
  <path>`, and exits 1; the log is `<scratch>/runs/<UTC time>-lint.log`. A
  run whose changed paths name no existing file prints `LINT no changed
  file` and goes on.
- An export (`--sha`) is no Git working tree, and pre-commit runs only in
  one: the run gives the export a Git directory `<scratch>/lint.git` holding
  an index of its files, and removes it with the export's `.git` pointer
  file when pre-commit ends, so the tree is again the export of its commit.
- `atc_testkit.lint` holds the commands: `range_argv` is the verifier's,
  `run_paths` the run over a set of paths.
- `atc sync` delivers the shared rules to a project's `.atc/lint/`
  (`ruff.toml`, `markdownlint.jsonc`, `vale.ini` with `vale/ATC/`) and
  keeps them current; the project's
  ruff settings extend them with `extend = ".atc/lint/ruff.toml"`, its
  `.markdownlint-cli2.jsonc` with
  `"config": {"extends": ".atc/lint/markdownlint.jsonc"}`. Files in
  `.atc/lint/` run nothing by themselves.
- A project adopts the lint stage in one commit of its own: it copies
  `shared/templates/pre-commit-config.yaml` to `.pre-commit-config.yaml`
  and adds `pre-commit` to its development dependencies. pre-commit has no
  include, so the project owns that copy from then on. Sync never creates
  it, so a re-sync adopts nothing (`docs/LAYOUT.md` § The manifest).

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
| The toolchain's lock check, `uv lock --check` | `UV_LOCK_CHECK_FAILED` |
| The lint stage over the range, when the candidate holds `.pre-commit-config.yaml` (§ Lint stage) | `LINT_FAILED` |
| `python -m atc_testkit.check --installed` | `TESTKIT_CONTRACT_FAILED` |
| Repository-wide collection, when the range changes a test, test helper, fixture or `conftest.py` | `COLLECTION_FAILED` |
| The project's other static checks | the project's tokens |
| The stages | `TESTS_FAILED`, `NO_TESTS_COLLECTED`, `WORKER_WAIT_TIMEOUT` (§ Host worker budget) |
| `HEAD` is still `ATC_INTEGRATION_SHA` | `CANDIDATE_HEAD_MOVED` |
| The tree is clean, untracked files included | `CANDIDATE_DIRTY` |

- A project static check keeps its own condition on the changed paths and
  says whether it runs before the shared checks.
- The optional input `ATC_TASK` names the task the stage-time records
  carry (§ Stage times). The verifier needs only the four other inputs.
- A range is documentation-only when the selection names no test and every
  changed path is either Markdown that is not automation Markdown
  (`AGENTS.md`, `CLAUDE.md`, or a file under a prefix the project names) or a
  path ATC manages: a task record `.atc/tasks/*.yaml`, `.atc/atc.lock`,
  `.atc/README.md`, a file under `.atc/governance/`, or a file of a skill
  copy under `.claude/skills/<name>/`, `.agents/skills/<name>/` or
  `.dsh/skills/<name>/` whose `SKILL.md` carries
  `managed-by: agent-traffic-control` (read at the range's base when the
  range deletes it). A skill copy without that line is the project's own
  and stays automation Markdown. Such a range ends after the lint stage,
  which holds the link check (§ Documentation check).
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
consumer's data feeds absent: no selection hook, lock, contract or
collection row. § Isolated environment, § Running and logging and the
working-directory rules bind it as they bind a project's verifier.

- Its own rows carry its own tokens: after the ancestry rows it reads the
  range, and a range Git cannot read refuses with `RANGE_UNREADABLE`; its
  static checks follow, then the stages.
- Static checks: the lint stage over the range. The root's
  `.pre-commit-config.yaml` holds its workspace checks as `local` hooks
  (`atc-maintain validate`, `atc-maintain policies check`), with the drift
  checks and the link check; the `python-adapter` job in
  `.github/workflows/validate.yml` runs the same list over every file.
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
- A pass writes the verification report, as a consumer's does
  (`shared/conformance/verification-evidence/README.md` § Report), so
  `atc integrate` reuses the reviewer's `atc test ID --candidate` record of a
  run on the same tree in place of running the verifier again
  (`shared/conformance/verification-evidence/README.md` § Integration reuse).
  Its `dependency_inputs` are the adapter toolchain's dependency files,
  named from the root (`adapters/python/uv.lock`,
  `adapters/python/.python-version`); its `classification` is
  `documentation-only` or `impact-selected`.
- The full suite, and with it the check that fails a session whose tests
  read the root's guidance undeclared (`ROOT_READ_UNDECLARED`), runs only in
  a manually dispatched run of `.github/workflows/validate.yml`
  (`VERIFICATION.md` § Full regression timing and evidence reuse); the
  Owner starts it.

## Input evidence

A passed test file is recorded against the inputs it ran on, so a later run
on the same inputs takes the record in place of running the file again.
A run's input set and input digest are
`shared/conformance/verification-evidence/README.md` § Input set: the
commit's files less the paths the project's verifier classifies as no
check's input (`atc_testkit.verifier.non_input`: for the workspace root its
documentation-only paths, for a consumer its managed files and the Markdown
the selection maps to no test). The candidate and gate records of that
README carry the same digest (`report.inputs`).

- **Record.** `<git-common-dir>/atc/verification-evidence/inputs/<name>-<key>.json`,
  schema `atc-input-evidence/1`, written once and never changed:
  `identity`, `inputs` (the digest), `excluded` (the SHA-256 of the run's
  excluded list, which is stored once as `inputs/excluded/<hash>.json`),
  `ran_on` (the commit), `result` `PASS` and `recorded_at`. `<name>` is a
  hash of the identity and `<key>` a hash of the identity and the digest.
- **Identity.** `{"kind": "test-file", "file", "marker", "selector",
  "platform", "time_slack", "dependency_inputs"}`: the test file by its path
  from the repository's top, the category expression it ran under, and what
  else decides its outcome. The verifier argv is not part of it, so a run of
  `atc test` and a verifier's stage share records.
- **Lookup.** A reader lists the records of the identity's name and takes
  the newest whose digest equals the digest of its own commit's tree with
  that record's excluded list left out (`atc_testkit.inputs.Finder`).
- **Coverage.** A record under the expression `R` serves a run under `W`
  when every test `W` selects, `R` selects too (`stages.covers`). The
  verifier looks for records under `""` (no expression), the private
  desktop's exclusion (§ Private desktop) and the stage's own expression.
- **Retention.** Every write deletes records and excluded lists older than
  14 days. A failure while reading or writing evidence never changes a
  verdict: the files run.

The integration verifier, before each pytest stage of a candidate, collects
the stage (`ATC_COLLECTED_FILES`) and runs only the files no record covers,
printing `<stage>: REUSED <n> files, RUN <m> files`; a stage every file of
which is covered does not run. After the final-state rows pass it records
each file the stages ran whose every test passed (`ATC_FILE_OUTCOMES`). A
non-Python toolchain runs its stages whole.

`atc test ID` records too, when it runs the related tests or `--all` with no
pytest arguments and the tree equals its commit before and after the run:
the worktree's `HEAD` with a clean status, or an export still equal to its
commit. It asks the project's verifier for the commit's input set by running
the tree's own verifier argv file with `ATC_INPUTS_OF=<commit>` in that
tree. An export is no Git checkout, so for the classification it gets a Git
directory beside it, `<scratch>/classify.git`, that reads the source
repository's objects through `objects/info/alternates`, with `HEAD` and the
index at the commit, removed with its `.git` pointer file afterwards; no
code runs in the source checkout, and an export that already holds a `.git`
records nothing. The verifier prints
`{"commit", "excluded", "dependency_files"}` and runs no check, and records
each file of the run whose every test passed under the expression the run
used. It prints `EVIDENCE <n> test files recorded for <sha12> (inputs
<digest12>)` or `NOTE input evidence: not recorded: <reason>`.

## Documentation check

`atc_testkit.docs` checks the relative links of Markdown, one rule set for
every project.

- A link target outside code (inline links, images and reference
  definitions; fenced blocks and inline code are skipped) without a URL
  scheme must name an existing path. A path starting with `/` is read from
  the repository root; a `?query` is ignored and `%XX` is decoded.
- A link that leaves the repository is a problem, `relative link escapes
  the tree`, unless the caller allows links out, to a sibling repository
  for example.
- A Markdown file that is missing, not UTF-8 or empty is a problem.
- With anchors, a `#fragment` must name a heading, as GitHub numbers a
  repeated one, or an HTML `id` or `name` of the Markdown file it points
  into.
- A project can exclude Markdown files it must never change, frozen
  records for example: an excluded file is never read as a link source, in
  any mode, while links from other files into it still resolve and still
  have their anchors checked. A glob matches the repository-relative POSIX
  path with `fnmatch` rules, case-sensitive, `*` also matching `/`, so a
  broad glob such as `*.md` excludes every file and turns the check off:
  name the exact files or the narrowest directory pattern. A glob that
  matches no file is not an error, so a stale one stays in the project's
  lint hook until a reader removes it.
- `python -m atc_testkit.docs --tracked` checks every Markdown file Git
  tracks, so a deleted or renamed target breaks its links in any file;
  paths in place of `--tracked` check those files. It prints one
  `path:line: message` per problem and exits 1 on any; `--anchors` and
  `--allow-outside` turn on the two options, and each repeatable
  `--exclude GLOB` excludes the files it matches.
- The lint stage runs it as a `local` hook of the repository's
  `.pre-commit-config.yaml` with `--tracked`, `pass_filenames: false` and
  `always_run: true`; the hook's `entry` carries the project's
  `--allow-outside`, `--anchors` and excludes, for example:

  ```yaml
  - id: markdown-links
    name: Markdown relative links and anchors
    entry: uv run --frozen python -m atc_testkit.docs --anchors --tracked --exclude "packages/hami-quant-research/hypotheses/H00[1-6]_*.md"
    language: system
    pass_filenames: false
    always_run: true
  ```

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
- `atc test ID` and `atc test ID --mutants` run on a private desktop and
  exclude `interactive_desktop` tests, saying so in one line:
  `NOTE private desktop: interactive_desktop tests are excluded; ATC_PRIVATE_DESKTOP=0 runs on the interactive desktop with them`.
  `ATC_PRIVATE_DESKTOP=0` runs on the interactive desktop with those tests
  included. Unset and `1` are private; any other value is refused with
  `PRIVATE_DESKTOP_INVALID` (exit 9) before any test runs.
- `atc test --private -- <command> [args...]` runs one command on a private
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
    ways out, `atc test ID` or the marker. The test's report is failed at
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

The test commands a review or a developer runs are forms of `atc test`, one
implementation on every platform. A machine that runs them has `atc`
installed (`uv tool install` of a tag, `WORKFLOW.md` § Installing a release), as
integration already requires. Every variable ATC itself defines starts with `ATC_`.

No run has confirmed this on a cloud writer host, a clone with no
`workspace.yaml`: installing `atc` from a clone there and running
`atc test ID` against a project clone is untried, and what such a host runs
when it has no `atc` is not decided.

`atc test` is implemented in `atc-testkit`. The adapter depends on the
package by its path in the tree, and `atc` dispatches to it; `atc integrate`
takes the location rule from the same package.

`atc <command> [<subcommand>] --help` prints a usage line that starts with
`usage: atc <command> [<subcommand>]` and exits 0; output the console cannot
encode is written as backslash escapes. A wrong form prints the usage on
stderr and exits 2, and no option is matched by an abbreviation. A command
that imports a module the installed `atc` lacks stops with
`ERROR MISSING_DEPENDENCY` (exit 2) naming the module and the reinstall
command of the root's `AGENTS.md`.

### `atc test ID [--sha SHA] [--all] [-v] [-- PYTEST_ARG...]`

One isolated pytest run for the task `ID` after the lint stage
(§ Lint stage), its output kept in a log. By default it runs the related tests, the test files changed since the record's
`base_sha` and the test files that import a changed module directly, one edge
and no closure, in the task's worktree, in the scratch `<ID>`
(`ATC_HOME.md` § Test scratch). `--sha SHA` runs them in an export of `SHA`,
in the scratch `<ID>-<sha7>`, whose environment is built once; `--all` runs
the full suite. The test directory is the shallowest directory, at most two
levels below the repository root, whose `pyproject.toml` has
`[tool.atc-testkit]`; the run's timeout is that table's `timeout`. A project
with no such directory refuses with `NO_TESTKIT`, and its tasks use
`tests: none`. `-v` streams pytest's output. Every run first prunes stale
scratch entries. In order:

1. Before the run, each refusal exits 9 and runs no test. `ID` has the task
   form `ATC-0008`; another form stops with `TASK_INVALID`. The scratch is
   checked without creating it: `SCRATCH_OUTSIDE_TMP`,
   `SCRATCH_PATH_TOO_LONG`, `SCRATCH_NOT_RESOLVED`, and
   `SCRATCH_NOT_CREATED` for a path that holds a file. The tree is the
   task's worktree or the export: `TREE_NOT_FOUND`. The interpreter is that
   of the tree's environment (§ Toolchain); without one, `VENV_MISSING`,
   which names the project's `AGENTS.md` preparation, and never another
   Python. Only then does the scratch get its registry line
   (`ATC_HOME.md` § Scratch registry) with the task, and after that its
   directory and `qdr`; a scratch whose line names another task stops with
   `SCRATCH_TASK_MISMATCH`, and one that cannot be made with
   `SCRATCH_NOT_CREATED`. So a refusal before the line leaves no directory,
   and a later one leaves the scratch registered.
2. Unless `--all` is given, the related tests come from the tree's
   `[tool.atc-testkit]` `selector`, a list of strings: the argv of a command
   that takes changed paths as arguments and, with `--direct` after them,
   prints the changed test files and the test files one import from a changed
   module as a JSON array on its last line. It runs with the run's Python and
   the tree as its working directory. The changed paths are
   `git diff --name-only <base_sha>` and the untracked files of the tree's
   working tree, relative to its top; for an export, the diff from
   `<base_sha>` to the exported commit. A selector that fails with `--direct`
   gives the changed test files of the tree. The line
   `RELATED <base>: <n> changed paths, <k> test paths` follows; with
   none selected, `NO_TESTS no test selected` ends the command with exit 0.
   The selected paths come before the pytest arguments.

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
   namespace directories, and require each to load from inside the tree.
   Then locate, without running it, every regular package of each other
   project in the tree, a `src` directory beside a `pyproject.toml` up to
   three directory levels below the tree (directories named `src`, `tests`,
   `test` or `node_modules`, or starting with `.` or `_`, are not entered),
   and require each the interpreter's environment resolves to lie inside the
   tree; one it does not resolve is not used and passes. Either failure is
   `COPY_IMPORT_MISMATCH` (exit 8), whose last line names the copy's own
   environment as the next step. This guard is what proves the copy, not an
   installed checkout, is what runs: an environment built from another
   checkout installs that checkout's packages, and the guard names them.
   Every process the run starts after the guard (the selector, pytest) gets
   the verifier's environment, § Isolated environment, built from the one
   steps 3 and 4 made, with the scratch as the run's working directory: so
   `HOME` and `USERPROFILE` name `<scratch>/home`, the uv, Python and ruff
   caches are those under `~/.atc/cache`, and the project's credential
   variables are removed. The data-root variables keep `<scratch>/qdr`.
   The lint stage then runs over the changed paths step 2 lists, when the
   record has a `base_sha`; a failure ends the command with exit 1 before
   any test.
6. Run pytest with `PYTHONUNBUFFERED=1`, `-q`, `-p no:cacheprovider`,
   `--basetemp <scratch>/bt` and, when xdist is installed, the run's worker
   option, its output written as it arrives to
   `<scratch>/runs/<UTC time>-<label>.log` after the § Running and logging
   header. With xdist installed, pytest arguments without `-n` run the
   default count of § Stages, as `-n auto` and `-n logical` do: in the host
   pool up to the pool size, at most one worker per 8 collected tests, down
   to 2 workers, and a grant of 1 runs in-process as `-n 0`. `-n 0` runs
   serially, and `-n K` runs K, cut by memory and the pool (§ Host worker
   budget). A project's own `-n` in `addopts` comes before the run's option,
   so the run's option wins. The console shows `LOG <path>` first, then the
   private desktop's exclusion line (§ Private desktop), the `workers:` line
   of a parallel run (§ Stages), and, with `-v`, the output too.
   A run without `-n` is parallel under this testkit; `-n 0` keeps it
   serial.
7. A run that may record input evidence prints its evidence line
   (§ Input evidence). The run's stage-time line (§ Stage times; none after an interrupt), then one summary line, ends the command: `PASS`, `FAIL`, `NO_TESTS`,
   `INTERRUPTED` or `ERROR exit <code>`, pytest's counts, the failed and
   errored tests by node ID, and the log path. A run that the timeout ends
   prints the log's last fifteen lines and `TIMEOUT` (exit 124); one the
   caller interrupts prints them and `INTERRUPTED` (exit 130). Neither ever
   reports a pass. Otherwise the exit code is pytest's.

Before step 6 the run takes its host worker tokens, and the timeout counts
from then; a wait past its limit stops it with `WORKER_WAIT_TIMEOUT` (exit
124) before pytest starts, and an interrupt during the wait stops it with
`INTERRUPTED` (exit 130) before pytest starts (§ Host worker budget).

### `atc test ID --lint [--sha SHA]`

The lint stage alone (§ Lint stage), in the task's worktree or with `--sha`
in the export of `SHA`, over the files changed since the record's
`base_sha`: steps 1 to 5 of the run above, then the lint stage, and no test.
It takes no tests and no `--all`. Exit 0 when it passes or prints `LINT_NOT_CONFIGURED`, 1 on
`LINT_FAILED`. A reviewer runs it before reading the diff (atc-review
skill).

### `atc test ID --mutants FILE [--sha SHA] [-- TEST...]`

Applies each mutant of `FILE` alone to the export of `SHA` (the branch tip
when `--sha` is absent) in the scratch `<ID>-<sha7>`, through the isolation of
`atc test ID`. The judging tests are the `TEST...` after `--`, which judge
every mutant. Without them, each mutant is judged by the tests the tree's
`selector` picks with `--direct` for the paths its edits name (§ Commands,
`atc test ID` step 2), followed by its own `tests`; a mutant that gets none
stops the command with `NO_TESTS` before the baseline. That list goes to
`<scratch>/mutants-selected.json` with each mutant's `tests` filled in, and
the baseline runs their union, never the task's related tests. Each run is pytest with `-x` and a
900-second timeout. Before the baseline the command takes the host worker
tokens `atc test ID` takes for those tests (§ Commands, `atc test ID` step
6: the default count, at most one worker per 8 collected tests and at
least 2, or an `-n K` among them as a flag), also when it reuses the
baseline, and holds them to its end. A
wait past 900 seconds stops the command with `WORKER_WAIT_TIMEOUT` (exit
124) before the baseline, and an interrupt during the wait stops it with
`INTERRUPTED` (exit 130) (§ Host worker budget). Each mutant runs on
those tokens with `-n W --dist loadgroup`, `W` being one worker per 8 node
IDs its line record names, up to the grant, or the whole grant for
file-level tests; a `W` of 1 runs in process. An `-n K` among a mutant's
own tests decides its workers instead, outside the pool and its memory
clamp.

- `ID` is checked as `atc test ID` checks it: `TASK_INVALID`. `SHA` must name
  a commit in the task's worktree (`git rev-parse --verify`): `SHA_NOT_FOUND`
  (exit 9) before the scratch is touched, then `PYTHON_NOT_FOUND`, the
  scratch refusals of `atc test ID`, `VENV_MISSING`, `TREE_NOT_FOUND`,
  `SCRATCH_TASK_MISMATCH`, `TIME_SLACK_INVALID` and `PRIVATE_DESKTOP_INVALID`
  (exit 9). The private desktop's exclusion line comes first when the runs
  are private.
- The runs use the copy's own environment, the `.venv` interpreter of the
  directory the scratch's registry line names inside the scratch, else of
  `<scratch>/tree`; without one, `VENV_MISSING`. After every refusal of
  this list but `SCRATCH_NOT_CREATED` has passed, the scratch gets its
  registry line with the task, before anything is created in it. The import
  guard of `atc test ID` runs before each run on `<scratch>/tree`, so an
  interpreter whose environment installs the tree's packages from another
  checkout stops the command with `COPY_IMPORT_MISMATCH` (exit 8).

- A mutant is a label, a list of edits `{path, old, new}` and optional
  `tests`, the pytest arguments that judge it in place of `--tests`, and
  `kill_rule`; edit paths and tests are relative to the repository root. A
  file that cannot be read, is not JSON or breaks that shape stops the
  command with `STOP MUTANTS_INVALID` and a `hint:` showing the shape, before
  the export or the scratch is touched. An `old` that does not occur exactly
  once in its file aborts the run. Edits follow the file's line endings.
- One `--mutants` run uses a scratch at a time. After the scratch's
  registry line, before the export's mutants list is written and before
  any other check, the command locks `<scratch>/mutants.lock` and holds it
  to its last line; a scratch another run holds stops the command at once
  with `MUTANTS_RUNNING` and that run's pid and start time (exit 9), and
  `atc test ID --clean` refuses such a scratch whole. Then every edit path
  of the mutants and every path an earlier run of the scratch mutated
  (`<scratch>/mutants-touched.txt`, appended before each mutant is
  applied) must equal its Git object at `SHA`, carriage returns ignored:
  a file that differs stops the command with
  `TREE_NOT_PRISTINE <path> differs from <sha7>` (exit 9) before any
  baseline; `atc test ID --clean` and a new run export the tree anew.
- The tests run once unmutated first, `--tests` together with every
  mutant's own `tests`, as one pytest run on the granted workers with
  `--dist loadgroup` (`-n 0` in process on a grant of 1). As in `atc test
  ID`, `serial` tests run in that one parallel run, unlike the stages'
  `serial` stage; one that fails under that load gives `BASELINE-RED`, and a
  rerun settles it, never a wrong verdict. A failing baseline prints
  `BASELINE-RED <first FAILED line>; failed: <node ID>, ...`, naming every
  failed and errored test, and exits 1 before any mutant is applied.
- A passed baseline writes `<scratch>/baseline.json`: the export's commit
  (`sha`), its inputs, the baseline's test arguments, whether it recorded
  lines and which files, the digest of the recorder's files, the digest of
  the running atc-testkit package's `.py` sources, the interpreter (by its
  path from the scratch when it lies inside it) and whether the runs are
  private, with its time, summary, seconds and workers. `inputs` is
  `{"digest", "excluded"}`, the commit's input digest and the paths outside
  its input set (§ Input evidence), as the export's own verifier argv file
  classifies them in the export, with the Git directory § Input evidence
  gives an export; without a classification it is `commit <sha>`. A later run
  reuses the record when its commit has those inputs
  (`shared/conformance/verification-evidence/README.md` § Input set; a
  `commit <sha>` record only its own commit), its test arguments (in order),
  recording, recorder, package, interpreter and privacy are equal, and the
  recorded files include those it would record. It runs no baseline, prints
  no stage-time line for it, prints
  `BASELINE-OK reused <time> (<seconds>s, workers <n>) <summary>` and judges
  the mutants by the lines the baseline recorded. A record that differs
  prints `NOTE baseline: not reused: <field>` first. A scratch with no
  matching record of its own takes the newest matching record of another
  scratch registered to the task, so an amend that changes only excluded
  paths, under a new `<ID>-<sha7>`, runs no baseline: it copies that
  scratch's line records with each recorded path moved to its own tree,
  keeps the record as its own with its own `sha` and
  `reused_from {scratch, sha}`, and prints
  `BASELINE-OK reused from <scratch> ...`. A record whose line records do
  not read back, or which changed during the copy, is not taken, and the
  baseline runs. Every baseline deletes the record before it starts and
  writes it only after it passed, so a failed, timed-out or interrupted
  baseline leaves none. A hand edit of the export outside the edit paths is
  not part of the key.
- Without `TEST...`, the baseline also records the lines each test executes
  in the `.py` files the mutants edit, in the pytest process and in every
  process it starts that keeps `PYTHONPATH` (a nested testkit run keeps the
  recorder). The recorder is a `sys.monitoring` tool the command writes to
  `<scratch>/lines/hook` (a `sitecustomize` that hands over to the one it
  shadows, and a pytest plugin that gives each test its own context, passed
  to its child processes in `ATC_LINES_CONTEXT`); a test's context and its
  collected name are its node ID as collected, without the `@<group>` that
  pytest-xdist's `loadgroup` appends in a worker; each process appends its
  rows to its own file in `<scratch>/lines/data`, which every baseline
  starts empty. Each pytest process that loads the plugin, the xdist
  controller and every worker, marks that it loaded it and, when its
  recorder started, that it started; each that collects writes the
  collected tests in order. The records of all processes are merged; the
  baseline counts as recorded only when every process that loaded the
  plugin started its recorder, and its order is the collection order, the
  order a single-process baseline runs. Only
  the baseline records, together with each test's seconds from its setup
  to its teardown (`durations-<pid>`; a name two processes recorded keeps
  the longer time). The tests of a mutant are then the tests that executed
  a line it changes, ordered to reach a kill soonest under `-x`: the node
  IDs named among its own tests first, then the others fastest first, in
  collection order among equal times and with a test without a time last,
  then its other items in place. The changed lines are the lines where `old` and `new`
  differ, each widened to its innermost statement, a compound statement by
  its header alone; a line of a compound statement in none of its children
  (an `else:` or `finally:`, which runs no line event) also stands for the
  first statement of its clause. A file or directory among its file-level tests narrows
  to those tests; a node ID, an option and a path right after an option stay
  among them, unchanged. `<scratch>/mutants-covered.json` lists each mutant's lines,
  tests and how it is judged.
- A mutant that no recorded test executes, with no node ID among its tests,
  prints `NOT_COVERED <label> -> no baseline test runs <path>:<lines>`
  without a run and counts as a survivor. A mutant keeps its file-level
  tests, after `NOTE <label>: <why>; judged by its file-level tests`, when
  an edit is in a file that is not Python or cannot be read, that no
  recorded process loaded (no row at all), whose `old` does not occur
  exactly once before the run, or whose changed lines also ran outside a
  test (at import). A recorder that did not start in the pytest process
  (no `sys.monitoring`, no free tool id) prints `NOTE lines: not recorded;
  every mutant is judged by its file-level tests`. Past 30 000 characters
  a mutant's tests go to pytest in an `@argfile`. A process started with
  `-I` or `-E`, or with an environment without `PYTHONPATH`, and a test that
  reads the source instead of executing it are not recorded, so a mutant
  only they would kill shows as `SURVIVED` or `NOT_COVERED`.
- Each mutant prints one verdict line, preceded by its stage-time line
  (§ Stage times; none for `NOT_COVERED`, which runs nothing). Its files are restored whether or not the run completes. Every edit and restore moves the file's modification time
  to a later whole second than its last, so a bytecode cache, which records
  that second and the size, never stands in for the source the run is meant
  to see. A run that exits 0 prints `SURVIVED` with the summary.
- The kill rule decides a failed run. `failure`, the default: a run that
  exits 1 with a `FAILED` line prints `KILLED` with that line; on workers,
  where pytest-xdist ends a run at `-x`'s first failure as an interrupt, an
  exit 2 with its `xdist.dsession.Interrupted: stopping after N failures`
  line counts as that exit 1.
  `assertion`: pytest also writes a junit report to
  `<scratch>/junit/<NNN>-<label>.xml`, and the run kills only when every
  failure's message starts with `AssertionError`, `assert ` or
  `Failed: DID NOT RAISE` and no test errored; a failure by any other
  exception, `pytest.fail` among them, is `INCONCLUSIVE`.
- Any other ending of a mutated run, an `ERROR` line alone, exits 2 to 5, a
  timeout or a failure the kill rule does not count, prints
  `INCONCLUSIVE <label> -> <reason>` and aborts the run with exit 1. An
  import-guard refusal aborts the run with its exit code, and an exception
  while applying or running a mutant with exit 1, each with its token or
  name on stderr. The remaining mutants of an aborted run stay unrun.
- At the end every touched file is compared with its Git object at `<sha>`,
  carriage returns ignored: `restored-ok` or `RESTORE-MISMATCH` per file,
  exit 1 on any mismatch. The last line counts the mutants and names the
  survivors: `done: M mutants, S survived <labels>`, or
  `done: N of M mutants, S survived <labels>` after any abort, also one on the
  last mutant, where `N` counts the mutants that printed a `KILLED`,
  `SURVIVED`, `NOT_COVERED` or `INCONCLUSIVE` line; `S` and the labels
  include the `NOT_COVERED` mutants. A survivor exits 1.

### `atc test ID --candidate [--sha SHA]`

Runs the integration verifier on the candidate `atc integrate ID` would
build, from the reviewed SHA (the newest PASS review report, or `--sha`),
moving no ref. It writes the candidate record that `atc integrate` reuses
(`shared/conformance/verification-evidence/README.md` § Candidate record). The
last line is `PASS candidate <sha> FF|NO_FF`, or the verifier's refusal token.

### `atc test ID --clean`, `atc test --list [ID]`

`--clean` deletes the task's scratches, after the registry's safety checks;
`--list` lists the registered scratches, those of `ID` when given
(`ATC_HOME.md` § Checkout, § Scratch registry, § Deleting).

### `atc test --private -- COMMAND [ARG...]`

Runs `COMMAND` on a private desktop from the current directory with the
current environment. It prints `desktop: private`, or `desktop: interactive`
with the line § Private desktop names when no desktop can be created, then
streams the command's merged output, and exits with its code. Without a
command it prints its usage (exit 2).

`atc doctor -v` lists the host's worker pool, as § Host worker budget
describes.

## What a project supplies

In the project's Python file:

- The selection policy: package dependencies, path prefixes, full-suite
  files, and how a missing remote baseline is read.
- Its static checks with their tokens, path conditions and position.

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
| `hook_uv_options` | `uv` options for the hook |
| `ci_platforms` | the CI platform matrix |
| `ci_init` | the CI runner's initialization function, as `module:function` |
| `ignore` | `--ignore` paths appended to a stage, under `nt` and `posix` |
| `selector` | the argv of the command the related-test selection of `atc test ID` runs; with `--direct` after the changed paths it prints only the changed test files and the test files one import from a changed module, which `atc test ID` runs (§ Commands) |
| `toolchain` | the toolchain's name, `python` when absent (§ Toolchain) |
| `worker_memory_mib` | the memory one test worker is counted to need, an integer from 1 to 65536; when absent, the project's recorded cost on a host, else 256 (§ Stages); any other value stops a run with `ATC_CONFIG_INVALID` (exit 9) |

Elsewhere in the project:

- To adopt the lint stage, `.pre-commit-config.yaml` at the repository
  root, the lint stage's list (§ Lint stage), and `pre-commit` pinned in
  the development dependencies; without the config the stage does not run.
- Test paths and pytest's search path, as `testpaths` and `pythonpath` in
  `[tool.pytest.ini_options]`.
- The verifier argv file, which is a project file and carries the `uv`
  options the project's verifier needs, an interpreter version for example.

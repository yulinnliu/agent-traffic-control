# ATC home and atc-testkit implementation plan

How and in what order `ATC_HOME.md` and `TESTKIT.md` become what the tools do.
The two specs hold every rule; this plan cites them by section and adds none.
A point the specs leave open is listed in § Points for the Owner with the
step that meets it, and is settled in the spec before that step continues.

Step 1 is a set of measurements and commits nothing. Steps 2 to 5 and the
reference clean-up of step 6 are each one topic branch in the repository
named, reviewed by the `hami-review-relay` method under the class given, and
integrated with `atc integrate` before the next step that depends on it. The
Owner's deletions of step 6 are no topic. A step in a project repository
follows that project's `AGENTS.md`; § Project plan entries says where each
project records it.

## Order

```text
1 verifications ──> 2 atc-testkit release ──> 3 root tools ──┬─> 4 hami-quant-trading ─┬─> 6 retire old locations
                                    │                        └─> 5 hami-quant ─────────┘
                                    └─ needs the governance change of § Separate authority
```

Steps 4 and 5 do not depend on each other. Step 6 needs both. Step 2 starts
on the Owner's word after the report of step 1.

## 1. Verifications that may change the spec

The four measurements below run before any code of step 2 is written. Each
is a measurement, not a change to a project: its script lives in a scratch
directory and nothing is committed in a project repository. Every result is
reported to the Owner with its numbers, whatever it shows, and step 2 starts
on the Owner's word after that report.

### 1.1 xdist workers on a private desktop

Question: does the `parallel` stage of `TESTKIT.md` § Stages work as
written, with its worker option, when started on a private Windows desktop
(`TESTKIT.md` § Private desktop)?

- Repository: `hami-quant-trading`, read only. The launcher is its
  `scripts/private_desktop.py`, which today starts only the serial stage
  (`scripts/prepush_select.py`, `run_stages()`).
- How: start the trading `parallel`-stage command through
  `private_desktop.popen` for a targeted set that includes process, Tk and
  subprocess tests (not the full suite, which is CI-only), once with workers
  and once with `-n 0`, and the same set on the interactive desktop as the
  control. Record: exit codes, the summary lines, whether execnet workers
  start, whether any window reaches the interactive desktop, wall clock of
  each run.
- The report names each of these when it occurs, because it changes the
  Desktop column of `TESTKIT.md` § Stages: workers that fail to start or
  hang on a private desktop; a test that passes on the interactive desktop
  and fails on the private one for a reason other than needing the
  interactive desktop (those are counted in 1.2). It gives the wall clock of
  the private and the interactive run side by side.

### 1.2 Count of `interactive_desktop` tests

Question: how many tests in each project need the interactive desktop, and is
the `interactive` stage small enough that showing its windows is acceptable?

- Repositories: `hami-quant-trading` and `hami-quant`, read only.
- How: list candidates by source (tray icon, Tk root creation, detached or
  new-console launches, `CREATE_NEW_CONSOLE`), then run each candidate file
  on a private desktop and on the interactive desktop. A test needs the
  marker when it fails only on the private desktop. Report per project: the
  count, the files, the wall clock of the would-be `interactive` stage, and
  the tests that open a window on the interactive desktop.
- The report names each of these when it occurs: a test that needs the
  interactive desktop and is also `serial`-sensitive in a way the stage
  table does not place; a whole category (every Tk test, for example) that
  needs the marker. It gives the share of a gate run's wall clock the
  would-be `interactive` stage takes.

### 1.3 Metadata cost under `AppData`

Question: does the cost `ATC_HOME.md` § Location states for `AppData` hold
on this host?

- How: time one targeted set of each project with its base directory under
  `%LOCALAPPDATA%\Temp` and under `%USERPROFILE%\.atc\tmp`, three runs
  each. Report the wall clocks and their ratio.
- Needs the write guard to allow `~/.atc` (§ Separate authority).

### 1.4 Path length below `<tmp>`

Question: does the bound `ATC_HOME.md` § Location puts on `<tmp>` leave the
paths the tests build room under the Windows path limit?

- How: for the targeted sets of 1.3, record the longest path created below
  the base directory, for a scratch run and for a run without a scratch
  (`ATC_HOME.md` § pytest without a scratch). Report the two lengths per
  project and the room each leaves. Step 3 repeats the measurement for the
  root's suite in a candidate.

## 2. The `atc-testkit` release

Repository: the workspace root. Class: behaviour. One release carries the
rename and every capability, because `TESTKIT.md` § Adoption has all projects
of a workspace on one version.

Files:

- `adapters/python/hami-testkit/` becomes `adapters/python/atc-testkit/`:
  distribution `atc-testkit`, import package `atc_testkit`, config table
  `[tool.atc-testkit]`, release record `vendor/atc-testkit.json`
  (`pyproject.toml`, `src/`, `README.md`). The record's name is a point for
  the Owner (§ Points for the Owner).
- Existing modules carried over: `markers`, `selection`, `git`, `evidence`,
  `check`.
- New modules, each written from the spec section and the code it replaces:
  - temporary locations: `ATC_HOME.md` § Location, § Resolving the locations;
    replaces trading's `scripts/tests/pytest_temp_root.py`.
  - pytest plugin: `TESTKIT.md` § Adoption, § Markers; replaces the two root
    `conftest.py` bodies and hami-quant's `_pytest_marker_contract.py`.
  - stages and the runner with logging: `TESTKIT.md` § Stages, § Running and
    logging; replaces `run_stages()`, `run_logged()` and `start()` in
    trading's `scripts/prepush_select.py` and `run_isolated_pytest()` in
    hami-quant's.
  - isolated environment: `TESTKIT.md` § Isolated environment; replaces
    `isolated_env()` in trading's `scripts/integration_verify.py` and
    `isolated_environment()` in hami-quant's. `HOME` and `USERPROFILE` are
    given one directory, which is what lets the section's `.gitconfig` hold
    for both; a test pins that the two are equal.
  - pre-push gate skeleton: `TESTKIT.md` § Pre-push gate; replaces `main()`
    of both `scripts/prepush_select.py`.
  - integration verifier skeleton: `TESTKIT.md` § Integration verifier;
    replaces both `scripts/integration_verify.py`, and carries the form of
    the section's workspace-root paragraph, in which the rows a consumer's
    data feeds are absent.
  - child processes: `TESTKIT.md` § Child processes; replaces trading's
    `scripts/tests/child_process_support.py`, without its imports of trading
    product code (`trading_app_job`, `hami_quant_trading_cli.host_stop`).
    The process list comes from the sources the section's last item names.
  - private desktop: `TESTKIT.md` § Private desktop; replaces trading's
    `scripts/private_desktop.py`.
  - CI runner: `TESTKIT.md` § CI runner, with the `slow` invocation of
    § Stages and the initialization function a project names; replaces the
    shared part of trading's `scripts/ci_pytest.py`. The desktop of the
    `slow` invocation is settled here (§ Points for the Owner).
  - commands: `atc test run`, `atc test mutants` and
    `atc tmp list|clean|prune` (`TESTKIT.md` § Commands,
    `ATC_HOME.md` § Test scratch, § Scratch registry, § Deleting), which
    that section places in the package. They are written from those
    sections and take the place of `run_tests.sh`, `mutants.py` and
    `clean_scratch.ps1` under
    `shared/skills/hami-review-relay/references/`.
- `check`: reads `[tool.atc-testkit]`, the new record name and the data of
  `TESTKIT.md` § What a project supplies; `python -m atc_testkit.check`.
- `adapters/python/scripts/build_testkit_release.py`: the new names.
- `adapters/python/scripts/testkit_contract.py`, `doctor.py`,
  `onboard_project.py`, and `validate_workspace.py` through
  `testkit_contract`: they read `vendor/atc-testkit.json` and
  `vendor/hami-testkit.json`, so a project that still carries the old record
  keeps its contract check until its step 4 or 5. Step 6 drops the old name.
- `adapters/python/scripts/integration_verify.py`: its import of the
  package's `git` module follows the rename; the script stays the root's own
  until step 3.
- `adapters/python/tests/test_testkit_*.py` renamed with the package, plus
  one test file per new module with a test per refusal token and exit code
  of the spec tables. The test of `atc tmp clean` is a new file that
  carries every case of `adapters/python/tests/test_clean_scratch.py` and
  adds the registry, the reserved names, `--project` and `prune`;
  `test_clean_scratch.py` stays the test of `clean_scratch.ps1` while that
  script exists (step 6).
- `adapters/python/README.md`: the package name and the new commands.

Verification: `uv run --locked pytest` from `adapters/python` in an isolated
copy; the topic's own `atc integrate`, a full run of the root verifier in a
candidate, up to `git worktree remove` of a candidate that holds a `.venv`
(§ Unverified items); one mutant per guard of the new modules; the wheel
built by `build_testkit_release.py` installs into an empty virtual
environment and
`python -m atc_testkit.check --installed` runs against a fixture project;
the Windows-only modules (private desktop, child processes) are exercised on
this host and their non-Windows paths in the `python-adapter` CI job on
`ubuntu-latest`.

Review: behaviour, with the real path walked for the verifier skeleton
(`atc integrate` to the exit code) and for the plugin (a direct `pytest` in a
fixture project to the temporary directory it ends up in).

Lands together with: the `VERIFICATION.md` § Testing sentence
(§ Separate authority). The release is not vendored into a project before
that change is in `main`.

Blocked until: the Owner's `write_scope_guard.py` allows `~/.atc`
(§ Separate authority). The location tests need a root outside `AppData`
and outside every repository, and the review walks the plugin to
`<tmp>/systmp`.

## 3. Root tools

Repository: the workspace root. Class: behaviour. Depends on step 2 for the
package.

1. The adapter's dependency on the package (`TESTKIT.md` § Commands):
   `adapters/python/pyproject.toml` names `atc-testkit` by its path in the
   tree, `uv.lock` follows, and the verifier and the scripts import the
   package without a search-path insert.
2. `atc test` and `atc tmp`: registered in
   `adapters/python/src/atc_cli/__init__.py`, dispatching to the package.
   Tests: each subcommand reaches the package's implementation with its
   arguments and returns its exit code, on Windows and POSIX.
3. `atc integrate` (`ATC_HOME.md` § Integration and the pre-push gate):
   `adapters/python/scripts/git_integrate.py` makes the location call before
   it creates a file, and places the candidate, its report and its output
   capture, names the candidate and removes it as that section says.
   `tests/test_git_integrate.py` follows, with a test of the section's bound
   on the candidate path.
4. `build_testkit_release.py`: the same location call.
5. `adapters/python/check.ps1`: the entry-point row of
   `ATC_HOME.md` § Resolving the locations, the cache values of § Cache and
   the base-directory rule of § pytest without a scratch.
6. `adapters/python/tests/conftest.py`: registers the plugin from the in-tree
   source and sets no base directory of its own.
7. The root's integration verifier
   (`TESTKIT.md` § Integration verifier, the workspace-root paragraph):
   `adapters/python/scripts/integration_verify.py` becomes a thin caller of
   the skeleton, imported from the tree, that supplies the root's static
   checks with their tokens and its test command. This closes the four
   differences the root `AGENTS.md` `## Integration verification` section
   lists: the skeleton in place of the root's own script; the environment
   of `TESTKIT.md` § Isolated environment; a working directory under
   `<tmp>/systmp` with the name bound of
   `ATC_HOME.md` § Integration and the pre-push gate; and the logs of
   `TESTKIT.md` § Running and logging. The token of a range Git cannot read
   is settled here (§ Points for the Owner).
   `adapters/python/tests/test_integration_verify.py` follows.
8. Root `AGENTS.md` `## Integration verification`: the paragraph of
   differences is removed and the timeout is re-measured. The section keeps
   what `GIT_WORKFLOW.md` § Project verification section requires of it:
   the argv file and timeout lines `atc integrate` reads, what the
   verification covers by change class with the root's two static checks
   named, and how the documentation-only exception is applied. The rule
   that derives the timeout moves to `adapters/python/README.md`, and the
   timeout line points there.
9. `shared/skills/hami-review-relay/references/`: `run_tests.sh`,
   `mutants.py` and `mutants.example.json` are removed in the same topic
   that changes the skill text (§ Separate authority); until then both the
   scripts and the commands exist. `clean_scratch.ps1` stays until step 6,
   because `atc tmp clean` refuses a directory outside `<tmp>`
   (`ATC_HOME.md` § Deleting) and `%TEMP%\q` is retired with the script.

Verification: the adapter suite; one mutant per guard; a real
`atc test run` and `atc test mutants` against an exported copy of each
project at its current `main`, with pytest's own output compared with that
of the scripts they replace and everything else read against the spec;
`atc tmp clean` on a scratch that holds a `.venv` junction; a real
`atc integrate` in the root of a documentation topic and of this step's own
topic, each with its candidate and its working directory where
`ATC_HOME.md` § Integration and the pre-push gate puts them, and both gone
after the pass; the longest path the root's suite creates in such a run
(§ Unverified items).

Review: behaviour; host state is read for the registry and the candidate
directory. The deletion path of `atc tmp clean` is walked against the six
steps of `ATC_HOME.md` § Deleting.

## 4. hami-quant-trading moves onto the testkit

Repository: `hami-quant-trading`. Class: behaviour. Depends on steps 2 and 3.
The App's product code is untouched; every file below is test tooling,
with one exception under `packages/`, a test inventory that names a tooling
script.

- `vendor/`: the `atc-testkit` wheel and `vendor/atc-testkit.json` replace
  `hami_testkit-0.5.0-py3-none-any.whl` and `vendor/hami-testkit.json`;
  `pyproject.toml` pin, `[tool.uv.sources]`, `[tool.atc-testkit]` with the
  data of `TESTKIT.md` § What a project supplies, `markers` list removed;
  `uv.lock`; `vendor/README.md`.
- `conftest.py`: registers the testkit plugin only.
- Removed, their callers moved to the testkit: `scripts/private_desktop.py`,
  `scripts/tests/pytest_temp_root.py`, `scripts/tests/pytest_temp_root_probe.py`,
  `scripts/tests/child_process_support.py`, `scripts/run_targeted_tests.ps1`,
  and the tests and mutation verifiers that test them
  (`scripts/tests/test_private_desktop.py`, `test_pytest_temp_root.py`,
  `test_child_process_support.py`, `test_pytest_marker_contract.py`,
  `scripts/verify_pytest_temp_root_mutations.py`).
- `scripts/ci_pytest.py`: reduced to the project's additions to the CI runner
  (`TESTKIT.md` § CI runner): context fields, hints, and the one
  initialization function, which holds the Tk library and token-owner
  preparation; `scripts/tests/test_ci_pytest.py` follows.
- `scripts/prepush_select.py`: keeps the selection policy and the project's
  static checks; stages, logging, desktop and evidence calls come from the
  testkit. `scripts/git-hooks/pre-push` keeps its one line.
- `scripts/integration_verify.py`, `scripts/integration-verify.json`: the
  project's data passed to the shared verifier.
- Every test that starts a child: imports `start` and `run` from the testkit.
- Tests found in step 1.2: the `interactive_desktop` marker.
- `.github/workflows/ci.yml`: the testkit's runner, the three stages, the
  `slow` invocation of `TESTKIT.md` § Stages in the job the project names,
  the targeted `workflow_dispatch` run through the same runner, and the
  `python -m atc_testkit.check --installed` step.
- `scripts/setup_clone.ps1`, `scripts/setup_clone.sh`: the new record name
  and `python -m atc_testkit.check --installed --hooks`.
- Files that pin what this step changes, updated in the same topic:
  - `scripts/test_app_boundary.json`: its digests of
    `scripts/tests/child_process_support.py`, `pytest_temp_root.py` and
    `pytest_temp_root_probe.py`.
  - `packages/trading-cli/tests/account_writer_inventory.json`: its entry
    for `scripts/ci_pytest.py`, symbol `controller_context`.
  - `scripts/pytest_unmarked_baseline.txt`: the entries of removed tests.
  - `scripts/tests/test_shared_testkit.py`, `test_prepush_select.py`,
    `test_trading_selection.py`: the package, record and module names.
  - `scripts/verify_account_flow_step5_mutations.py`, `step6` and `step8`:
    their use of `scripts/private_desktop.py`.
  - Any further file a `git grep` of the old names at the project's `main`
    returns when the step starts.
- Documents: `AGENTS.md` § Test layout and § Integration verification,
  `scripts/tests/README.md`, `README.md`, `docs/specs/error_inventory.json`
  where it lists verifier tokens, and `docs/specs/ACCOUNT_DRIVEN_FLOW.md`
  where it names `scripts/run_targeted_tests.ps1`.
- Token pins: `scripts/tests/test_integration_verify.py`,
  `scripts/tests/test_evidence_reuse.py`,
  `scripts/verify_integration_verify_mutations.py` and
  `scripts/verify_evidence_reuse_mutations.py` are updated to the token table
  of `TESTKIT.md` § Integration verifier in the same topic (§ Unverified
  items, the last entry).

Verification: the project's own gates as its `AGENTS.md` lists them; the
impact-selected stages through the new gate on this host, on a private
desktop; `atc test run` of the targeted set in an exported copy;
repository-wide `pytest --collect-only`; `atc integrate` of the topic itself
is the first run of the shared verifier on this project, so its evidence
record and the next push's `tests REUSED` line are checked; a push that has
only records of the earlier testkit version prints `tests RUN`
(`TESTKIT.md` § Pre-push gate). The full suite runs in CI only.

Review: behaviour, under the project's `## Review` section, including the App
boundary snapshot and the digest-pinned tests the touched modules belong to.
The real path is the push: hook, gate, selection, stages, evidence reuse.

## 5. hami-quant upgrades from 0.4.0 and adopts everything

Repository: `hami-quant`. Class: behaviour. Depends on steps 2 and 3.

- `vendor/`, `pyproject.toml`, `uv.lock`, `vendor/README.md`: as in step 4,
  from `hami_testkit-0.4.0`. `addopts = "-n auto --dist loadscope"` is
  removed from `[tool.pytest.ini_options]` together with the `markers` list;
  the stages pass their own worker option.
- `conftest.py` registers the plugin; `_pytest_marker_contract.py` and
  `tests/test_pytest_marker_contract.py` are removed.
- `scripts/prepush_select.py`: selection policy and static checks only; the
  gate gains the stages, the private desktop and evidence reuse, which it
  does not have at 0.4.0. `scripts/git-hooks/pre-push`: the one-line form.
- `scripts/integration_verify.py`, `scripts/integration-verify.json`: the
  project's data passed to the shared verifier; the argv file is the
  project's (`TESTKIT.md` § What a project supplies) and keeps
  `--managed-python --python 3.13.14` and `-B`, which the project's
  `AGENTS.md` requires. Its tokens change to the spec's table
  (`tests/test_integration_verify.py`).
- Tests that are sensitive to parallel load get `serial`; tests found in
  step 1.2 get `interactive_desktop`; tests that start a child use the
  testkit's `start` and `run`.
- `.github/workflows/ci.yml`: the testkit's runner and stages on
  `ubuntu-latest`, the `slow` invocation of `TESTKIT.md` § Stages in the job
  the project names, and the Windows supervision job through the same
  runner.
- `scripts/setup_clone.ps1`, `scripts/setup_clone.sh`: the new record name
  and check command.
- Files that pin what this step changes, updated in the same topic:
  `tests/test_shared_testkit.py`, `tests/test_prepush_select.py`,
  `tests/test_git_env_isolation.py`, and any further file a `git grep` of
  the old names at the project's `main` returns when the step starts.
- Documents: `AGENTS.md` § Commands and § Integration verification,
  `docs/agent-runbooks/quant-worktree-workflow.md` "Test-run isolation",
  `README.md`.

Verification: as step 4, plus `tests/test_collector_supervision.py` on this
host under the `3.13.14` candidate interpreter, because the verifier's
interpreter choice is what that test depends on.

Review: behaviour. The #007 v1 evidence-gate controlled paths are not
touched; the reviewer confirms it from the diff.

## 6. Retiring the old temporary locations

Runs only after steps 4 and 5 are integrated and one gate run and one
`atc integrate` in each project have passed on the new locations. Its topic
in the root removes `clean_scratch.ps1` and `test_clean_scratch.py` once
`%TEMP%\q` is empty, and drops `vendor/hami-testkit.json` from the root
tools of step 2.

| Location | Written by | Retired by |
| --- | --- | --- |
| `%TEMP%\q`, `%TEMP%\q\LEDGER.txt` | `run_tests.sh`, `clean_scratch.ps1` | `clean_scratch.ps1` on every listed scratch, then the Owner removes the empty directory and the script is removed |
| `%SystemDrive%\hami-pytest` | trading's `pytest_temp_root.py` | the Owner, step 4 having ended its use |
| `C:\hqt`, `C:\hqt070s` | no known source | the Owner; this plan only confirms no file in either repository or in `shared/` names them |
| `adapters/python/tests/.tmp-pytest` | the adapter's `conftest.py` | deleted here, step 3 having ended its use; the `**/tests/.tmp*/` ignore line stays until no checkout has one |
| `.tooling/uv-cache`, `.tooling/python`, `.tooling/pytest-tmp` | `check.ps1` | the Owner, step 3 having ended their use; `.tooling/testkit-dist/` stays (`ATC_HOME.md` § Outside the ATC home) |
| `%TEMP%\atc-integration-candidates`, `%TEMP%\hami-trading-verify-*`, `%TEMP%\hami-targeted-*`, `%TEMP%\atc-root-verify-*` | `atc integrate`, the verifiers, `run_targeted_tests.ps1` | candidates through `git worktree remove` in the repository each tree's `.git` file names, report files and trees Git no longer knows by the Owner; the rest by the Owner |

A session deletes nothing at a drive root and nothing outside the directories
its write guard allows; each row above that names the Owner is a list handed
to the Owner with sizes, not a command a session runs.

Verification: a search of the root and both projects for each old location's
name returns only history documents; `atc tmp list` shows the live scratches.

Review: behaviour for the root topic, which removes a script and a record
name the tools read; documentation only for the reference clean-up in each
project; no review for the Owner's deletions.

## Project plan entries

This plan schedules the root's own steps (2, 3, the root rows of 6). A
project's steps are recorded where that project's `AGENTS.md` says new work
goes, by a writer in that project, before the step starts:

- `hami-quant`: a step of `docs/plans/HAMI_QUANT_PLAN.md` that cites
  `TESTKIT.md` and `ATC_HOME.md` at the governance revision the project pins
  and states only its own order and verification.
- `hami-quant-trading`: its plans are per App version, and this work changes
  no App behaviour. Either a step of the open version plan under
  `docs/plans/`, or work the Owner orders directly, identified by repository,
  branch and SHA plus `TESTKIT.md` as the governing document. The Owner
  chooses.
- Other registered projects carry no testkit and get no entry; they receive
  the skill text through the usual sync.

## Separate authority

Changes this plan depends on and a writer of the steps above does not make:

| Change | Authority | Needed before |
| --- | --- | --- |
| `shared/governance/VERIFICATION.md` § Testing: the sentence naming `hami-testkit` and `adapters/python/hami-testkit/README.md` | a governance change under `GOVERNANCE_CHANGES.md` | step 2 is vendored anywhere; then each project's pin advances with its step 4 or 5 |
| `docs/LAYOUT.md`: `hami-testkit/` in § The checkout, and the place of `~/.atc` beside the checkout | the Owner's instruction for that document | step 2 lands |
| `shared/skills/hami-review-relay/SKILL.md` and its rendered copies: `run_tests.sh`, `clean_scratch.ps1`, `mutants.py`, `%TEMP%\q`, `LEDGER.txt` become the `atc` commands and `<tmp>`, with `clean_scratch.ps1` kept for `%TEMP%\q` until step 6 | a skill change with its own review (`hami-writing-for-agents`), then `atc onboard --sync` into every project | the end of step 3 |
| `~/.claude/hooks/write_scope_guard.py`: allow `~/.atc` | the Owner edits it | measurement 1.3 of step 1, and step 2 |
| The release version number of `atc-testkit` | the Owner | step 2 |

## Unverified items

Statements of the specs and of this plan that no run has confirmed. Each
has the step that settles it; a result that contradicts the spec goes to the
Owner before that step continues.

| Item | Settled in | How |
| --- | --- | --- |
| `atc` on cloud writer hosts: `TESTKIT.md` § Commands assumes `atc` is installed where the test tools run; a cloud VM clone has no `workspace.yaml` and may have no `atc` | step 3 | install from a clone on a cloud host and run `atc test run` against a project clone; decide what a host without `atc` runs |
| The ten-times `AppData` metadata cost in `ATC_HOME.md` § Location | step 1 | measurement 1.3 |
| PowerShell module-cache timing and trading's tests under a redirected profile (`TESTKIT.md` § Isolated environment redirects `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`) | step 4 | run trading's PowerShell-launching test files with the redirected variables before the gate is switched |
| The 40-character bound on `<tmp>` against `MAX_PATH` | steps 1 and 3 | measurement 1.4 for the projects; step 3's verification for the root's suite in a candidate |
| Tests and trading's mutation verifier that pin today's verifier tokens | steps 4 and 5 | listed in those steps; hami-quant's tokens differ from the spec's table today |
| `atc integrate` on the workspace root: a candidate of the root, the `.venv` the verifier argv builds in it, and `git worktree remove` of a candidate that holds that `.venv` | the first root topic integrated, then step 2 | a documentation-only range exercises all three without the suite; step 2's own topic is the first full range |
| The installed `atc` with a path dependency: `uv tool install -e adapters/python` and the source snapshot under `~/.local/atc-src` (`ATC_HOME.md` § Outside the ATC home) must carry `atc-testkit` beside the adapter | step 3 | install from the step's tree and run `atc tmp list` from a directory outside the checkout |

## Points for the Owner

What the two specs leave open. None is decided here; each is settled in the
spec when the step named meets it.

1. `TESTKIT.md` § Stages gives each stage a desktop and gives the `slow`
   invocation none. Step 2 builds the invocation with the CI runner.
2. `TESTKIT.md` § Integration verifier gives the workspace root no selection
   row, so no row carries a range Git cannot read. The root's verifier
   refuses it with `RANGE_UNREADABLE`, a token the table does not hold.
   Step 3 item 7 moves the verifier onto the skeleton.
3. No spec names the release record a project vendors beside the wheel.
   The code names it `vendor/hami-testkit.json`, and this plan writes
   `vendor/atc-testkit.json` after the package. Step 2 renames it.

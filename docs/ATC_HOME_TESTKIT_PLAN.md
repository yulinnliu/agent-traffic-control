# ATC home and atc-testkit implementation plan

How and in what order `ATC_HOME.md` and `TESTKIT.md` become what the tools do.
The two specs hold every rule; this plan cites them by section and adds none.
A point the specs leave open is listed in § Points for the Owner with the
step that meets it, and is settled in the spec before that step continues.

Every step is one topic branch in the repository it names, reviewed by the
`hami-review-relay` method under the class given, and integrated with
`atc integrate` before the next step that depends on it. A step in a project
repository follows that project's `AGENTS.md`; § Project plan entries says
where each project records it.

## Order

```text
1 verifications ──> 2 atc-testkit release ──> 3 root tools ──┬─> 4 hami-quant-trading ─┬─> 6 retire old locations
                                    │                        └─> 5 hami-quant ─────────┘
                                    └─ needs the governance change of § Separate authority
```

Steps 4 and 5 do not depend on each other. Step 6 needs both.

## 1. Verifications that may change the spec

Both run before any code of step 2 is written. Each is a measurement, not a
change to a project: its script lives in a scratch directory and nothing is
committed in a project repository. The result is reported to the Owner with
the numbers.

### 1.1 xdist workers on a private desktop

Question: does the `parallel` stage of `TESTKIT.md` § Stages work as written,
that is, a pytest run with `-n min(8, cpu count) --dist loadscope` started on
a private Windows desktop (`TESTKIT.md` § Private desktop)?

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
- Back to the Owner when: workers fail to start or hang on a private desktop;
  a test passes on the interactive desktop and fails on the private one for a
  reason other than needing the interactive desktop (those are counted in
  1.2); or the private run is slower than the interactive one by more than
  the gate can absorb. Any of these changes the Desktop column of
  `TESTKIT.md` § Stages.

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
- Back to the Owner when: the count is large enough that the `interactive`
  stage still flashes windows for a noticeable part of a gate run; a test
  needs the interactive desktop and is also `serial`-sensitive in a way the
  stage table does not place; or a whole category (every Tk test, for
  example) needs the marker, which would empty the private stages of the
  tests that motivated them.

## 2. The `atc-testkit` release

Repository: the workspace root. Class: behaviour. One release carries the
rename and every capability, because `TESTKIT.md` § Adoption has all projects
of a workspace on one version.

Files:

- `adapters/python/hami-testkit/` becomes `adapters/python/atc-testkit/`:
  distribution `atc-testkit`, import package `atc_testkit`, config table
  `[tool.atc-testkit]`, release record `vendor/atc-testkit.json`
  (`pyproject.toml`, `src/`, `README.md`).
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
    that section places in the package. Their behaviour is that of
    `run_tests.sh`, `mutants.py` and `clean_scratch.ps1` under
    `shared/skills/hami-review-relay/references/`, in Python on every
    platform.
- `check`: reads `[tool.atc-testkit]`, the new record name and the data of
  `TESTKIT.md` § What a project supplies; `python -m atc_testkit.check`.
- `adapters/python/scripts/build_testkit_release.py`,
  `testkit_contract.py`, `doctor.py`, `onboard_project.py`: the new names.
- `adapters/python/scripts/integration_verify.py`: its import of the
  package's `git` module follows the rename; the script stays the root's own
  until step 3.
- `adapters/python/tests/test_testkit_*.py` renamed with the package, plus
  one test file per new module with a test per refusal token and exit code
  of the spec tables. `adapters/python/tests/test_clean_scratch.py` becomes
  the test of `atc tmp clean`, keeping every case it has and adding the
  registry, the reserved names, `--project` and `prune`.
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
   it creates a file, places each candidate at `<tmp>/candidates/<id>` and
   its report at `<tmp>/candidates/<id>.report.json` with the `<id>` of that
   section, removes both when the run ends, and keeps its output capture
   under `<tmp>/systmp`. `tests/test_git_integrate.py` follows, with a test
   of the 64-character bound on the candidate path.
4. `build_testkit_release.py`: the same location call.
5. `adapters/python/check.ps1` (`ATC_HOME.md` § Resolving the locations,
   § Cache, § pytest without a scratch): caches under `~/.atc/cache` unless
   the caller set them, the location rule in shell, no `--basetemp`.
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
   differences is removed, the section keeps the argv file and timeout lines
   `atc integrate` reads plus a pointer to `TESTKIT.md` § Integration
   verifier, the rule that derives the timeout moves to
   `adapters/python/README.md`, and the timeout is re-measured by that rule.
9. `shared/skills/hami-review-relay/references/`: `run_tests.sh`,
   `clean_scratch.ps1`, `mutants.py` and `mutants.example.json` are removed
   in the same topic that changes the skill text (§ Separate authority); until
   then both the scripts and the commands exist.

Verification: the adapter suite; one mutant per guard; a real
`atc test run` and `atc test mutants` against an exported copy of each
project at its current `main`, compared line for line with the output of the
scripts they replace; `atc tmp clean` on a scratch that holds a `.venv`
junction; a real `atc integrate` in the root of a documentation topic and of
this step's own topic, each with its candidate at `<tmp>/candidates/<id>`,
its working directory under `<tmp>/systmp`, and both gone after the pass.

Review: behaviour; host state is read for the registry and the candidate
directory. The deletion path of `atc tmp clean` is walked against the six
steps of `ATC_HOME.md` § Deleting.

Blocked until: the Owner's `write_scope_guard.py` allows `~/.atc`
(§ Separate authority). A session cannot run items 2 to 7 for real before
that.

## 4. hami-quant-trading moves onto the testkit

Repository: `hami-quant-trading`. Class: behaviour. Depends on steps 2 and 3.
The App's product code is untouched; every file below is test tooling.

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
  and the `python -m atc_testkit.check --installed` step.
- Documents: `AGENTS.md` § Test layout and § Integration verification,
  `scripts/tests/README.md`, `README.md`, `docs/specs/error_inventory.json`
  where it lists verifier tokens.
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
`atc integrate` in each project have passed on the new locations.

| Location | Written by | Retired by |
| --- | --- | --- |
| `%TEMP%\q`, `%TEMP%\q\LEDGER.txt` | `run_tests.sh`, `clean_scratch.ps1` | `clean_scratch.ps1` on every listed scratch, then the Owner removes the empty directory |
| `%SystemDrive%\hami-pytest` | trading's `pytest_temp_root.py` | the Owner, after step 4 |
| `C:\hqt`, `C:\hqt070s` | no known source | the Owner; this plan only confirms no file in either repository or in `shared/` names them |
| `adapters/python/tests/.tmp-pytest` | the adapter's `conftest.py` | deleted with step 3; the `**/tests/.tmp*/` ignore line stays until no checkout has one |
| `.tooling/uv-cache`, `.tooling/python`, `.tooling/pytest-tmp` | `check.ps1` | the Owner, after step 3; `.tooling/testkit-dist/` stays (`ATC_HOME.md` § Outside the ATC home) |
| `%TEMP%\atc-integration-candidates`, `%TEMP%\hami-trading-verify-*`, `%TEMP%\hami-targeted-*`, `%TEMP%\atc-root-verify-*` | `atc integrate`, the verifiers, `run_targeted_tests.ps1` | candidates through `git worktree remove` in the repository each tree's `.git` file names, report files and trees Git no longer knows by the Owner; the rest by the Owner |

A session deletes nothing at a drive root and nothing outside the directories
its write guard allows; each row above that names the Owner is a list handed
to the Owner with sizes, not a command a session runs.

Verification: a search of the root and both projects for each old location's
name returns only history documents; `atc tmp list` shows the live scratches.

Review: documentation only for the reference clean-up; no review for the
Owner's deletions.

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
| `shared/skills/hami-review-relay/SKILL.md` and its rendered copies: `run_tests.sh`, `clean_scratch.ps1`, `mutants.py`, `%TEMP%\q`, `LEDGER.txt` become the `atc` commands and `<tmp>` | a skill change with its own review (`hami-writing-for-agents`), then `atc onboard --sync` into every project | the end of step 3 |
| `~/.claude/hooks/write_scope_guard.py`: allow `~/.atc` | the Owner edits it | the first real run in step 3 |
| The release version number of `atc-testkit` | the Owner | step 2 |

## Unverified items

Neither the spec writer nor the spec reviewer verified these. Each has the
step that settles it; a result that contradicts the spec goes to the Owner
before that step continues.

| Item | Settled in | How |
| --- | --- | --- |
| `atc` on cloud writer hosts: `TESTKIT.md` § Commands assumes `atc` is installed where the test tools run; a cloud VM clone has no `workspace.yaml` and may have no `atc` | step 3 | install from a clone on a cloud host and run `atc test run` against a project clone; decide what a host without `atc` runs |
| The ten-times `AppData` metadata cost in `ATC_HOME.md` § Location | step 1 | time one targeted set with its base under `%LOCALAPPDATA%\Temp` and under `%USERPROFILE%\.atc\tmp`. One data point exists: the root adapter suite took 276 s with its base in the checkout and 337 s with its base under `%TEMP%` |
| PowerShell module-cache timing and trading's tests under a redirected profile (`TESTKIT.md` § Isolated environment redirects `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`) | step 4 | run trading's PowerShell-launching test files with the redirected variables before the gate is switched |
| The 40-character bound on `<tmp>` against `MAX_PATH` | steps 1 and 3 | measure the longest path each project's tests create below the base directory, for a scratch run and for a run without a scratch |
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

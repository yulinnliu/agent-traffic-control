# atc-testkit

This document describes `atc-testkit` 0.8.0; update it with every version bump.

Development-only Python package shared by the projects of a workspace. It holds
the pytest plugin and marker contract, policy-driven test selection, Git gate
helpers, verification evidence reuse, temporary locations, stages and their
logging, the isolated environment, the pre-push gate and integration verifier
skeletons, child processes, the private desktop, the CI runner and the
`atc test` and `atc tmp` commands. Production packages have no dependency on it.

`docs/TESTKIT.md` and `docs/ATC_HOME.md` specify the execution behaviour and the
locations; this document defines the category meanings, the marker rules and the
selection behaviour.

`markers` validates one semantic category and an orthogonal `slow` attribute. The
plugin registers every marker and calls validation when a run collects its tests.

- `unit`: exercises one production unit with fake dependencies or temporary paths
  and crosses no CLI, persistent-store or subprocess boundary.
- `integration`: composes multiple production components or crosses a CLI,
  persistent-store or subprocess boundary.
- `render`: validates a page, report or visualization output contract.
- `statistical`: validates a statistical property with synthetic samples,
  permutation, bootstrap or resampling.
- `slow`: a runtime attribute for tests unsuitable for the fast development loop;
  it is not a category and needs exactly one semantic marker.

`serial` and `interactive_desktop` place a test in a stage (`docs/TESTKIT.md`
§ Stages); they are neither categories nor attributes of the runtime.

A test with more than one semantic marker, a `slow` test without one, and an
unmarked definition outside the baseline are violations. Each consumer owns its
legacy unmarked baseline (one nodeid per line, `#` comments allowed), named by
`unmarked_baseline`, which shrinks as tests acquire categories. All parameters of one test definition share
its definition nodeid, so the baseline lists the definition once. A consumer that
sets pytest `strict_markers` also fails collection on an unregistered marker.

`plugin` is what a project's root `conftest.py` registers with
`pytest_plugins = ("atc_testkit.plugin",)`. It moves a run's temporary directory
below the ATC home, enforces the marker contract, ends the child processes a test
started through `children`, and fails a run that leaves one alive
(`CHILD_PROCESSES_ALIVE`).

`selection` takes a repository-owned `SelectionPolicy`: dependencies, ordered test
paths, shared inputs, special package inputs and fixture consumers. Unknown paths
select the default suite. Unknown configured consumers or selected test groups
raise an error that blocks the repository gate. `Selection.pytest_paths()` returns `None` for no test
inputs, `[]` for the full default suite, and an ordered list for a partial run.

`SelectionPolicy.baseline_files` names the consumer's unmarked-test baselines. They
are not full-suite inputs: a push that only removes baseline entries selects the
test file of each removed entry, a push that adds an entry selects the full default
suite, and a baseline whose change cannot be read selects the full default suite.
`git.baseline_deltas` computes the per-file `BaselineDelta` from the pushed ranges
and is passed to `classify`; a consumer that omits it gets the full suite. Comment
and blank-line changes count as neither.

`git.changed_paths` accepts an explicit `missing_remote` policy: `full` returns
an unknown range for unfetched remote commits; `merge-base` compares them against
the common ancestor of `origin/main`. `verify_push_checkout` enforces clean-tree
and pushed-commit identity when a consumer's gate requires it.

`evidence` lets a pre-push gate reuse a passed `atc integrate` candidate
verification in place of its test stage; the formats and the reuse table are
`shared/conformance/verification-evidence/README.md` at the bound governance
revision.

- The project's integration verifier calls `write_report(os.environ, root,
  selection.pytest_paths(), stages)` once its checks pass. It writes the report
  to the path in `ATC_VERIFICATION_REPORT` and returns `None` when the variable
  is unset. A `Stage` holds what decides which tests run: the marker expression
  and ignored paths, without worker counts, temporary directories or verbosity.
- The gate calls `check(git, root, pushed_sha, selection.pytest_paths(), stages,
  argv_file)` after its static checks and before its test stage, with the same
  `Stage` values the verifier reports, and prints `describe(decision)`:
  `pre-push: tests REUSED (...)` or `pre-push: tests RUN (evidence: <reason>)`.
  It skips the test stage only when `decision.reuse` is true.
- Reuse needs the record of exactly the pushed commit and its tree, a clean
  candidate, the same platform, `uv.lock` and `.python-version` digests,
  verifier argv file, testkit version and stages, and a recorded selection
  that covers the gate's own: the full suite covers everything, a directory
  covers the files below it. The Python version is recorded and not compared.
- A missing, unreadable or mismatched record, and any error while checking,
  leaves `decision.reuse` false. `check` reads only; `atc integrate` writes and
  expires records.

`__version__` is the selector version a report carries and equals the package
version.

## Execution

The package owns execution commands, parallelism, platform initialization and
diagnostics. A project supplies data.

- `gate.main` and `verifier.main` take a `project.Project`: the repository root,
  the verifier argv file, the selection function, static checks as `StaticCheck`
  values, and the data below. A `Project` with no selection function is the form
  of the workspace root, in which the rows a consumer's data feeds are absent.
- `ci` is the runner a CI job calls: `python -m atc_testkit.ci --stages` runs the
  stages, `--slow` the slow tests, and any other arguments go to pytest. A project
  adds failure context, hints and initialization through `ci.main`.
- `atc test run`, `atc test mutants` and `atc tmp list|clean|prune` run a test
  tree under a scratch directory, kill mutants of a committed tree and manage the
  scratch registry; `python -m atc_testkit.commands` is the same entry point.
  `-h` after a subcommand prints its help and exits 0. `atc tmp clean
  --tree-name <name>` takes the registry rows of one tree. A refusal token goes to stderr; the per-directory lines `DELETED`, `SKIPPED`,
  `REFUSED`, `ANOMALY` and `LEFT` are the normal output of `atc tmp` on stdout.
- `atc test mutants` counts `KILLED` only for pytest exit 1 with a `FAILED` line.
  Every other ending of a mutated run (a setup `ERROR` alone, exits 2 to 5, a
  timeout) prints `INCONCLUSIVE` with the reason, ends the run with exit 1 after
  restoring the files, and leaves the remaining mutants unrun; the last line
  then reads `done: N of M mutants`, where `N` counts the mutants that printed a
  result line. An exception while applying or running a
  mutant ends the run the same way, with its name on stderr.

## Distribution

Build a universal wheel and release record with
`uv run --locked python scripts/build_testkit_release.py` from the workspace.
The builder copies canonical metadata and Python sources into a clean temporary
source tree, fixes archive timestamps, and checks the complete wheel payload
against that snapshot. It refuses to replace changed release content. Use a fresh
`--output-dir` for each subsequent version. `__version__` in
`src/atc_testkit/__init__.py` is the one place that sets the version.

Each consumer commits the exact wheel under `vendor/`, pins
`atc-testkit==0.8.0` in its development dependencies and records the local
artifact in `uv.lock`. Standalone clones and CI install their committed artifact
without a workspace checkout or credentials. Released versions are immutable;
source changes require a new version and explicit consumer upgrades.

The release record `vendor/atc-testkit.json` names the canonical source, source
digest, wheel digest and files. Consumer tests compare installed modules against
their committed wheel. Full-suite timing and evidence reuse follow the bound
governance and each project's stricter rules.

## Consumer checks

`python -m atc_testkit.check --installed` validates the committed release,
development pin, uv lock artifact, required test directories, supplied data, local
gate and an unconditional CI check after dependency sync. `--hooks` also checks the
effective installed pre-push hook; clone setup invokes it after dependencies are
installed. The CLI reads files and Git configuration without changing them.

Consumers opt in through `[tool.atc-testkit]` and a workspace-owned release record.
The rows down to `product_module_prefixes` are required; the others are optional.

| Key | Holds |
| --- | --- |
| `gate`, `hook`, `workflow`, `ci_job` | the pre-push gate script, the hook, the CI workflow and the job that runs the check |
| `unmarked_baseline` | the file of the legacy unmarked tests |
| `slow_ci_job` | the job that runs the slow tests |
| `product_module_prefixes` | the module prefixes the verifier checks are imported from the candidate |
| `credential_variables` | further variables the isolated environment removes |
| `data_root_variables` | the variables that get one data directory per stage |
| `automation_markdown_prefixes` | Markdown paths that are not documentation |
| `lint_scope` | the paths `ruff check` reads |
| `hook_uv_options` | the options the hook's `uv run --frozen` carries |
| `ci_platforms` | the platforms the CI job covers |
| `ci_init` | the `module:function` the CI runner calls first |
| `ignore` | the test paths each stage ignores, under `nt` and `posix` |

The selection policy and the static checks stay in the project's gate script.
Workspace validator, doctor and onboarding inspect opted-in projects. Onboarding
reports drift while preserving project-owned settings. CI executes the check
directly and requires no skill.

`SelectionPolicy.critical_prefixes` reserves important inputs for conservative
verification instead of relying on literal filename matching. Required test
directories must exist and contain test modules; missing or empty groups fail
the gate. Ordinary unreferenced prose can still have a recorded skip reason.

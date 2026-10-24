# Hami testkit

This document describes `hami-testkit` 0.4.0; update it with every version bump.

Development-only Python package shared by independent Hami projects. It contains
the pytest marker contract, policy-driven test selection and Git gate helpers.
Production packages have no dependency on it.

`markers` validates one semantic category and an orthogonal `slow` attribute. The
importing repository decides when to call validation.

- `unit`: exercises one production unit with fake dependencies or temporary paths
  and crosses no CLI, persistent-store or subprocess boundary.
- `integration`: composes multiple production components or crosses a CLI,
  persistent-store or subprocess boundary.
- `render`: validates a page, report or visualization output contract.
- `statistical`: validates a statistical property with synthetic samples,
  permutation, bootstrap or resampling.
- `slow`: a runtime attribute for tests unsuitable for the fast development loop;
  it is not a category and needs exactly one semantic marker.

A test with more than one semantic marker, a `slow` test without one, and an
unmarked definition outside the baseline are violations. Each consumer owns its
legacy unmarked baseline (one nodeid per line, `#` comments allowed), which
shrinks as tests acquire categories. All parameters of one test definition share
its definition nodeid, so the baseline lists the definition once. A consumer that
sets pytest `strict_markers` also fails collection on an unregistered marker.

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

## Distribution

Build a universal wheel and release record with
`uv run --locked python scripts/build_testkit_release.py` from the workspace.
The builder copies canonical metadata and Python sources into a clean temporary
source tree, fixes archive timestamps, and checks the complete wheel payload
against that snapshot. It refuses to replace changed release content. Use a fresh `--output-dir` for each subsequent version. Each consumer commits the exact wheel under
`vendor/`, pins `hami-testkit==0.4.0` in its development dependencies and records
the local artifact in `uv.lock`. Standalone clones and CI install their committed
artifact without a workspace checkout or credentials. Released versions are
immutable; source changes require a new version and explicit consumer upgrades.

The release record in each consumer names the canonical source, source digest,
wheel digest and files. Consumer tests compare installed modules against their committed wheel. Repository adapters retain execution commands, parallelism,
platform initialization and diagnostic hooks. Full-suite timing and evidence
reuse follow the bound governance and each project's stricter rules.

## Consumer checks

`python -m hami_testkit.check --installed` validates the committed release,
development pin, uv lock artifact, required test directories, local gate and an
unconditional CI check after dependency sync. `--hooks` also checks the effective
installed pre-push hook; clone setup invokes it after dependencies are installed.
The CLI reads files and Git configuration without changing them.

Consumers opt in through `[tool.hami-testkit]` (`gate`, `hook`, `workflow`,
`ci_job`) and a workspace-owned release record. Workspace validator, doctor and
onboarding inspect opted-in projects. Onboarding reports drift while preserving
project-owned settings. CI executes the check directly and requires no skill.

`SelectionPolicy.critical_prefixes` reserves important inputs for conservative
verification instead of relying on literal filename matching. Required test
directories must exist and contain test modules; missing or empty groups fail
the gate. Ordinary unreferenced prose can still have a recorded skip reason.

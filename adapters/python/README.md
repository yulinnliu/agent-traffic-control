# Python reference implementation

One adapter among several: the contracts these tools implement live under
`shared/` and are defined by their schemas and the conformance fixtures, not
by this code. Python 3.12 or newer, provisioned by `uv` from the
`pyproject.toml` and `uv.lock` here. Run everything from this directory.

One command fronts the scripts: `uv run atc <command>` here, or
`uv tool install -e .` once per machine to have `atc` on PATH, which is how
the skills call it; the root `AGENTS.md` states when it is installed again.
`atc` resolves the checkout from `--root`, `ATC_ROOT`,
the nearest `workspace.yaml` above the working directory, or its own install
location, in that order.

| `atc` command | Script | Does |
| --- | --- | --- |
| `bootstrap` | `bootstrap.py` | `init`, `add NAME [--repo URL] [--skills a,b]`, `sync [NAME] [--prune]`, `status`: the quickstart path over the tools below |
| `validate` | `validate_workspace.py` | canonical skills versus rendered adapters, root contract, task records and their declaration, `--projects` for registered checkouts (adapters, policy block, vendored governance, task records, one prefix per repository), `--root DIR --adapters-only` for a fixture |
| `onboard` | `onboard_project.py` | `--check`, `--apply`, `--sync [--prune]` a project's managed files; `--root-only` re-renders this checkout's adapters; never touches project-owned files |
| `doctor` | `doctor.py` | local readiness: `READY`, `NOT_INITIALIZED`, `ERROR` |
| `policies` | `policies.py` | `check DIR`: schema-validate a policy directory and refuse contradictions |
| `enforce` | `generate_enforcement.py` | `--policies DIR --out DIR [--check]`: harness deny configs from hard deny, see `docs/ENFORCEMENT.md` |
| `classify` | `classify_change.py` | `--policy escalation.yaml --changed FILE [--severity]`: `LOW` or `HIGH` |
| `preflight` | `git_preflight.py` | `merge` verdict `FF` / `NO_FF` / `STOP`; `cleanup` verdict `REMOVABLE`; `push` lists `origin/main..main` by integration with its task and authority, reading old and new `integrations.jsonl` lines; reads only |
| `integrate` | `git_integrate.py` | `--reviewed SHA --task TASK [--owner-words QUOTE] [--project NAME \| --repo DIR]`: a `--task` record ID with a declared prefix names the repository and takes its authority from the record on `main` (`FF` only) unless `--owner-words` quotes the Owner; a range that changes only `.atc/tasks/` runs the record check in place of the verifier and may omit `--task`; `docs/TASKS.md` § Authority on `main` states the checks. `--authority-reference REF` is the deprecated form, recorded with authority `unclassified` and refused for a range touching `.atc/tasks/`. The branch is found from the reviewed SHA, so `--branch` names it only when several local branches sit there; `--check` resolves, preflights and warns without a slot or candidate; human output prints project names and 12-character SHAs, `--json` the full values |
| `task` | `task_records.py` | `new --scope TEXT`, `list [--all] [--status S] [--json]`, `check [DIR]` task records under `.atc/tasks/` (`docs/TASKS.md`); `--project NAME \| --repo DIR` as `worktree` takes them |
| `worktree` | `hami_worktree.py` | `create`, `resume`, `audit`, `cleanup` per-topic worktrees with identity in git; `--repo` or `--project root` for this checkout's own topics under `worktrees/root/`; human output names `<project>/<worktree>`, `--json` the path |
| `friction` | `friction.py` | append incident and resolution events; summaries |
| `test` | `atc-testkit` | `run TREE SCRATCH PYTHON LABEL [pytest args]`: a test tree under a scratch of `~/.atc/tmp`; `mutants`: one guard mutant at a time on an exported commit |
| `tmp` | `atc-testkit` | `list`, `clean DIR... \| --tree-name NAME`, `prune`: the scratch registry and what is older than seven days |
| | `check_workspace.py` | runs the validator and records a failure as friction evidence |
| | `integration_verify.py` | the verifier `atc integrate` runs in a candidate of this checkout (root `AGENTS.md` § Integration verification) |
| | `build_testkit_release.py` | builds the `atc-testkit` wheel and digest record for consuming projects |
| | `select_tests.py` | the test files a change can affect, each with its cause (§ Selected tests) |

`src/atc_cli/` is the dispatcher; the scripts stay runnable directly with
`uv run python scripts/<name>.py`.

`atc-testkit/` is the pytest adapter of the verification contract: marker
classification, change-impact selection, temporary locations, the stages, the
gate and verifier skeletons, and the installed-artifact check a consuming project
runs in CI. This adapter depends on it by in-tree path.

```powershell
uv run --locked pytest                                  # suite, including shared/conformance
uv run --locked pytest tests/test_tooling.py -k name    # one test
uv run atc validate                                     # this checkout's contract
uv run atc onboard --sync --root-only                   # re-render .claude/ .agents/ .dsh/ after a canonical skill change (topic worktree only)
pwsh -File check.ps1 -Projects -Tests                   # one-shot with temporary files and caches under ~/.atc
```

Tests build throwaway repositories under pytest's base directory
(`docs/ATC_HOME.md` § pytest without a scratch) and never touch `projects/`,
`worktrees/` or `state/`. Registering the `atc-testkit` plugin puts the suite
under its marker contract: a new test carries a category, and
`tests/unmarked_baseline.txt` lists the tests that have none yet.

The `Verifier timeout` in the root `AGENTS.md` is three times the measured
wall clock of a full run, rounded up to a whole minute and never below 300
seconds; re-measure it when the suite's duration changes.

## Selected tests

Writers and reviewers run the tests their change can affect; the integration
verifier alone runs the full suite, because the installed `atc` runs the
canonical checkout's `main`.

```powershell
uv run --locked python scripts/select_tests.py --run               # select against main, then run pytest on it
uv run --locked python scripts/select_tests.py                     # print the selection only
uv run --locked python scripts/select_tests.py --paths A B         # explain workspace-relative paths
uv run --locked python scripts/select_tests.py --base REF --run -- -x   # another base; pytest arguments after --
```

The changed paths run from the merge base of `main` and `HEAD` to the working
tree, untracked files included. The output lists each selected test file with
the paths that select it, then every path with its rule; the rules are in the
script's docstring. `conftest.py`, the pytest plugin and what they load,
`pyproject.toml`, `uv.lock`, the unmarked baseline and every path no rule
maps select the full suite.

The selection sees imports and names written in strings, not globs: a test
that reads every file of a directory (a package-wide scan, a copy, a digest)
selects nothing through the graph, so it is declared in `DIRECTORY_READERS`
in the script, and a new such test is added there.

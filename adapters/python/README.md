# Python reference implementation

One adapter among several: the contracts these tools implement live under
`shared/` and are defined by their schemas and the conformance fixtures, not
by this code. Python 3.12 (`requires-python = ">=3.12,<3.13"`), provisioned
by `uv` from the `pyproject.toml` and `uv.lock` here. Run everything from this
directory.

One command fronts the scripts: `uv run atc <command>` here, or
`uv tool install -e .` once per machine to have `atc` on PATH, which is how
the skills call it. The distribution is `agent-traffic-control`; its console
script is `atc`.
`atc` resolves the checkout from `--root`, `ATC_ROOT`,
the nearest `workspace.yaml` above the working directory, or its own install
location, in that order.

The installed `atc` is an editable install: it reads the checkout's code and
holds the dependencies of the moment it was installed. An integrated range
that changes the dependencies in `pyproject.toml` or
`atc-testkit/pyproject.toml` is followed, on each host with an installed
`atc`, by `uv tool install -e "<checkout>/adapters/python" --force`. Until
then a command that imports a new dependency stops with
`ModuleNotFoundError`, and `uv run --locked atc` from here works.

A host whose `atc` is installed under the earlier distribution name
`atc-python-adapter` (`uv tool list` shows it) uninstalls that tool before
installing this one, since both provide the `atc` executable:

```sh
uv tool uninstall atc-python-adapter
uv tool install -e "<checkout>/adapters/python"
```

## Commands

`atc --help` lists the commands by group and `atc <command> --help` gives the
flags. Exit codes, `--json`, naming, versions and deprecated forms:
`shared/conformance/CLI.md`.

| `atc` command | Script | Does | Reference |
| --- | --- | --- | --- |
| `bootstrap` | `bootstrap.py` | `init`, `add --project NAME [--url URL] [--skills NAME,...]`, `status [--json]`: the quickstart path over the tools below | root `README.md` § Installation |
| `validate` | `validate_workspace.py` | canonical skills versus rendered adapters, root contract, task records and their declaration, `--projects` for registered checkouts (adapters, the `AGENTS.md` pointer line, `.atc/` with its lock, task records, one prefix per repository), `--root DIR --adapters-only` for a fixture; `--json` (§ validate) | `docs/LAYOUT.md`, `docs/ENFORCEMENT.md` § Drift |
| `onboard` | `onboard_project.py` | `--check`, `--sync [--prune]` the managed files of `--project NAME` or `--all`; `--root-only` re-renders this checkout's adapters; never touches project-owned files. The sync commit's subject names the ATC revision with its release tag `v*`: `sync from <source> v0.4.0 (c91d318)` at a tagged revision, `<sha> (after v0.4.0)` past one, the bare SHA before any; the ledger's sync line adds `source_tag` and `source_after`. The tags are the ATC checkout's local ones, so an unfetched newer tag shows as an older `after` | `docs/LAYOUT.md` |
| `doctor` | `doctor.py` | local readiness: `READY`, `NOT_INITIALIZED`, `ERROR`, with the host worker pool's free tokens, holders and waiters; `--json` | `atc doctor --help` |
| `policies` | `policies.py` | `check DIR`: schema-validate a policy directory and refuse contradictions | `shared/policies/schemas/` |
| `enforce` | `generate_enforcement.py` | `--policies DIR --out DIR [--check]`: harness deny configs from hard deny, see `docs/ENFORCEMENT.md` | `docs/ENFORCEMENT.md` |
| `classify` | `classify_change.py` | `--policy escalation.yaml --changed FILE [--severity]`: `LOW` or `HIGH` | `atc classify --help` |
| `preflight` | `git_preflight.py` | `push` lists `origin/main..main` by integration with its task and authority, reading old and new `integrations.jsonl` lines; reads only | `docs/WORKFLOW.md` § 8 |
| `integrate` | `git_integrate.py` | `--reviewed SHA --task TASK [--owner-words QUOTE] [--project NAME]`: `--task` is required; a record ID with a declared prefix names the repository and the branch (the record's `branch`) and takes its authority from the record on `main` (`FF` only) unless `--owner-words` quotes the Owner; a range that changes only `.atc/tasks/` runs the record check in place of the verifier, `--task` naming one of its records; `docs/TASKS.md` § Authority on `main` states the checks. A task identity string needs `--project`. `--reviewed` and `--rebased` take the full SHA or a unique prefix of 12 or more characters, resolved once to the full SHA the record holds; an ambiguous prefix stops with `AMBIGUOUS_SHA`. A task identity string and a records-only range integrate the one local branch at the reviewed SHA; `--check` resolves, preflights and warns without a slot or candidate, and warns with the first problems of `git diff --check main...<reviewed>` (whitespace errors, conflict markers) without refusing; within the slot the latest passing `atc verify` record of the same candidate replaces the verifier run when every check of `shared/conformance/verification-evidence/README.md` § Integration reuse holds; human output prints project names and 12-character SHAs, `--json` the full values | `docs/WORKFLOW.md` § 6–7, governance `GIT_WORKFLOW.md` |
| `verify` | `git_integrate.py` | runs the integration verifier on the candidate `atc integrate` would build, moving no ref, and records a PASS that `atc integrate` reuses | `docs/WORKFLOW.md` § 5 |
| `close-out` | `close_out.py` | removes a task's worktree, scratches and reports | `docs/TASKS.md` § Close-out |
| `task` | `task_records.py` | `new --scope TEXT`, `list [--all] [--status S] [--json]`, `check [DIR]` task records under `.atc/tasks/` (`docs/TASKS.md`); `--project NAME` as `worktree` takes it | `docs/TASKS.md` |
| `worktree` | `managed_worktree.py` | `create`, `resume`, `audit`, `clean` per-topic worktrees with identity in git; `audit` says whether each worktree is removable (merged into `main` and clean); `--project root` for this checkout's own topics under `worktrees/root/`; human output names `<project>/<worktree>`, `--json` the path | skill `atc-worktree` |
| `friction` | `friction.py` | append incident and resolution events; summaries | `atc friction --help` |
| `test` | `atc-testkit` | `run --scratch NAME [--tree DIR] [-- PYTEST_ARG...]`: a test tree under a scratch of `~/.atc/tmp`; `mutants`: one guard mutant at a time on an exported commit; `private`: a command on a private desktop | `docs/TESTKIT.md` § Commands |
| `tmp` | `atc-testkit` | `checkout`, `list`, `clean DIR...`, `prune`: the scratch registry and what is older than seven days | `docs/TESTKIT.md` § Commands, `docs/ATC_HOME.md` |
| | `check_workspace.py` | runs the validator and records a failure as friction evidence | |
| | `integration_verify.py` | the verifier `atc integrate` runs in a candidate of this checkout (root `AGENTS.md` § Integration verification) | |
| | `build_testkit_release.py` | builds the `atc-testkit` wheel and digest record for consuming projects | |
| | `select_tests.py` | the test files a change can affect: a count and one reason per path, each file's cause with `--explain`, a JSON array with `--json` (§ Selected tests) | |

`src/atc_cli/` is the dispatcher: it resolves `--root`, applies the
canonical checkout guard and wraps every `--json` result in the envelope of
`shared/conformance/CLI.md` § Output; `scripts/cli_conventions.py` holds the
exit codes, the shared options and the help form. The scripts stay runnable
directly with `uv run python scripts/<name>.py`.

### validate

`validate` checks what drifts silently: the shape of `workspace.yaml.example`
and canonical skills against their managed adapters. An adapter is a managed
copy rendered from its canonical skill (`render_adapter`): the same name and
description, the canonical body, the tool's overlay, and the ownership fields
`managed-by` and `canonical`. A canonical skill depends on no environment
variable naming this checkout, and no adapter is left behind when its
canonical skill goes. It also checks the task records under `.atc/tasks/` and
their `## Task records` declaration (`docs/TASKS.md`). By default it covers
this checkout's own `.claude/skills/`, `.agents/skills/` and `.dsh/skills/`,
the ones CI sees; `--projects` adds each project of the machine-local
`workspace.yaml` that exists on disk (its adapters for every
`adoption: baseline` skill, the `AGENTS.md` pointer line, a current `.atc/`,
all tracked) and rejects user-level copies of a canonical skill under
`~/.claude/skills`, `~/.agents/skills` or `~/.dsh/skills`, so it runs locally,
never in CI. It needs PyYAML, and jsonschema for the record check, and prints
every failure, not only the first.

`atc-testkit/` is the pytest adapter of the verification contract: marker
classification, change-impact selection, temporary locations, the stages, the
gate and verifier skeletons, and the installed-artifact check a consuming project
runs in CI. This adapter depends on it by in-tree path.

```powershell
uv run --locked python -m atc_testkit.ci --stages       # suite through the testkit's stages, including shared/conformance
uv run --locked python -m atc_testkit.ci --stages --workers 16   # the same with 16 workers in the parallel stage
uv run --locked pytest tests/test_tooling.py -k name    # one test, one invocation
uv run atc validate                                     # this checkout's contract
uv run atc onboard --sync --root-only                   # re-render .claude/ .agents/ .dsh/ after a canonical skill change (topic worktree only)
pwsh -File check.ps1 -Projects -Tests                   # one-shot with temporary files and caches under ~/.atc
```

Tests build throwaway repositories under pytest's base directory
(`docs/ATC_HOME.md` § pytest without a scratch) and never touch `projects/`,
`worktrees/` or `state/`. Registering the `atc-testkit` plugin puts the suite
under its marker contract: a new test carries a category, and
`tests/unmarked_baseline.txt` lists the tests that have none yet.

The suite runs through the testkit's stages (`docs/TESTKIT.md` § Stages): the
`parallel` stage on `min(8, cpu count)` workers, then `serial` and
`interactive`, which no test here enters yet. `--workers N` sets the parallel
stage's count for one run, and `test_workers = N` in `~/.atc/config.toml` for
every run on this host outside CI (`docs/ATC_HOME.md` § Host settings). Each
stage logs to `.tmp/ci-<stage>.log`.

The `Verifier timeout` in the root `AGENTS.md` is the test budget, 1800 s
(governance `VERIFICATION.md` § Verifier test budget). A verifier run that
exceeds it stops with `VERIFICATION_TIMEOUT` and "test budget exceeded". The
staged full suite takes about 232 s at 8 workers on this repository's
development host.

## Selected tests

Writers, reviewers and the integration verifier run the tests a change can
affect, the verifier over the range from the expected `main` to the
candidate. The full suite runs in a manually dispatched run of
`.github/workflows/validate.yml`, the safety net for what the selection
misses.

```powershell
uv run --locked python scripts/select_tests.py --run               # select against main, then run the stages on it
uv run --locked python scripts/select_tests.py                     # print the selection only: the count and one reason per path
uv run --locked python scripts/select_tests.py --explain           # also each selected test file with its cause
uv run --locked python scripts/select_tests.py --json --paths A B  # the test files to run as a JSON array, the whole suite spelled out
uv run --locked python scripts/select_tests.py --paths A B         # select for workspace-relative paths
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

The root's guidance and skills (`shared/governance/`, `shared/skills/`, the
rendered `.claude/skills/`, `.agents/skills/` and `.dsh/skills/`) and every
Markdown file outside `shared/` (the root's top-level Markdown, `docs/`, this
README) select only the tests declared in `DIRECTORY_READERS` as reading
them in this checkout; a file there other than Markdown also selects the
tests whose reach names it. Guarded Markdown no declared test reads selects
nothing; the guidance and skills have their own check,
`uv run --locked atc validate`, run after touching them. Markdown under
another `shared/<area>/` selects the tests that read that area.
`tests/root_reads.py` records each test file's reads of the guarded paths,
and counts a run of this checkout's `scripts/*.sh` as reading what
`SCRIPT_READS` names; a session in which a test
file reads one undeclared fails with `ROOT_READ_UNDECLARED`, naming the
test and the paths, and under `-n` each worker hands its failures to the
controller. Existence checks and what a child Python process opens are not
recorded.

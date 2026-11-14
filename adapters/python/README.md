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
`atc`, by `uv tool install -e "<checkout>/adapters/python" --force`, after
the uninstall below on a host whose `uv tool list` shows
`atc-python-adapter`. Until
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

`atc --help` lists the commands and `atc <command> --help` gives the flags.
Exit codes, `--json`, output and naming: `shared/conformance/CLI.md`.

| `atc` command | Script | Does | Reference |
| --- | --- | --- | --- |
| `task` | `task_records.py` | `create [TITLE] [--scope TEXT] [--tests none\|standard\|strict] [--file FILE]` opens a task: worktree, branch, lock reason and record in one commit; `update`, `list [-v]`, `resume`: task records under `.atc/tasks/` and their worktrees; `--project NAME` selects the repository, `root` this checkout's own topics under `worktrees/root/`; human output names `<project>/<worktree>`, `--json` the path | `docs/TASKS.md` |
| `task close` | `close_out.py` | `ID [--check] [--abandon] [-v]`: after integration removes the task's worktree, branch, registered scratches and reports; consent items are listed, never deleted; `--abandon` closes an unintegrated task and keeps its tip as tag `archive/<ID>` | `docs/TASKS.md` § Close-out |
| `test` | `task_tests.py` | `ID [--sha SHA] [--all] [-v] [-- PYTEST_ARG...]`: the related tests in the task's worktree, in an export of SHA, or the full suite; `ID --mutants FILE`: one guard mutant at a time; `ID --candidate`: the integration verifier on the candidate `atc integrate` would build, recorded for reuse; `ID --clean`, `--list [ID]`, `--private -- COMMAND` | `docs/TESTKIT.md` § Commands |
| `integrate` | `git_integrate.py` | `ID [--sha SHA] [--check]`: Owner-run; fast-forwards `main` to the reviewed SHA (the newest PASS review report, or `--sha`) after the verifier passes on the candidate; a range that changes only `.atc/tasks/` runs the record check in place of the verifier; a passing `atc test ID --candidate` record of the same candidate replaces the verifier run when every check of `shared/conformance/verification-evidence/README.md` § Integration reuse holds; does not close the task | `docs/WORKFLOW.md`, governance `GIT_WORKFLOW.md` |
| `push` | `push.py` | `NAME... \| --all [--check] [-v]`: Owner-run; pushes `main` when every commit in `origin/main..main` comes from a recorded integration or sync | `docs/WORKFLOW.md` |
| `init` | `bootstrap.py` | `[--governance-source OWNER/REPO] [--force]`: writes `workspace.yaml` and `projects/`, `worktrees/`, `state/` | root `README.md` § Installation |
| `add` | `bootstrap.py` | `NAME [--url URL] [--skills NAME,...] [-v]`: clone or adopt, register, create the project's ATC files, validate | root `README.md` § Installation |
| `remove` | `bootstrap.py` | `NAME [--check]`: Owner-run; drops the project's `workspace.yaml` entry and its `skill_harnesses` entry, never its checkout | `docs/LAYOUT.md` |
| `sync` | `sync_project.py` | `[NAME...] [--check] [--revision SHA] [-v]`: write and commit, or with `--check` only list and validate, the managed files of the named projects or of every registered project, rendered from the latest `vMAJOR.MINOR.PATCH` tag on `main` or the SHA the Owner names with `--revision`; a project locked ahead of that release is skipped; managed adapters with no canonical skill or in a harness directory the project's `skill_harnesses` does not select are deleted in the same commit; a project with no `AGENTS.md` gets a minimal one; never touches project-owned files. The sync commit's subject names the target with its release tag: `sync from <source> v0.4.0 (c91d318)` at a tagged revision, `<sha> (after v0.4.0)` past one, the bare SHA before any; the ledger's sync line adds `source_tag` and `source_after`, and `--json` a top-level `target`. The tags are the ATC checkout's local ones, so an unfetched newer tag is not seen | `docs/LAYOUT.md` |
| `doctor` | `doctor.py` | `[-v]`: local readiness verdict `READY`, `NOT_INITIALIZED` or `ERROR` plus the non-READY checks; `-v` lists every check and the host test worker pool's free tokens, holders and waiters; `--json` | `docs/ATC_HOME.md` |
| `guard` | `guard.py` | `[--install]`: checks, or for the Owner installs, the PreToolUse hook table `~/.atc/enforcement` and its entries in `~/.claude/settings.json` and `~/.codex/hooks.json` | `docs/ENFORCEMENT.md` |
| | `integration_verify.py` | the verifier `atc integrate` runs in a candidate of this checkout (root `AGENTS.md` § Integration verification) | |
| | `build_testkit_release.py` | builds the `atc-testkit` wheel and digest record for consuming projects | |
| | `select_tests.py` | the test files a change can affect: a count and one reason per path, each file's cause with `--explain`, a JSON array with `--json` (§ Selected tests) | |

### atc-maintain

`uv run --locked atc-maintain <command>` from an ATC worktree's
`adapters/python/` maintains this repository; it refuses in the canonical
checkout and is not part of the installed `atc`.

| `atc-maintain` command | Script | Does | Reference |
| --- | --- | --- | --- |
| `sync --root` | `sync_project.py` | `[--check] [-v] [--json]`: re-renders this checkout's adapters and commits nothing | `docs/LAYOUT.md` |
| `validate` | `validate_workspace.py` | canonical skills versus rendered adapters, root contract, task records and their declaration; `--json` (§ validate) | `docs/LAYOUT.md`, `docs/ENFORCEMENT.md` § Drift |
| `policies` | `policies.py` | `check DIR`: schema-validate a policy directory and refuse contradictions | `shared/policies/schemas/` |
| `task check` | `task_records.py` | `[DIR]`: task records and their `## Task records` declaration | `docs/TASKS.md` |
| `guard` | `generate_enforcement.py` | `--policies DIR --out DIR [--check]` and the `--live-*`, `--local-out`, `--hook-out` forms: harness deny configs from hard deny | `docs/ENFORCEMENT.md` |
| | `select_tests.py` | the test files a change can affect: a count and one reason per path, each file's cause with `--explain`, a JSON array with `--json` (§ Selected tests) | |

`src/atc_cli/` holds the dispatchers for `atc` and `atc-maintain`: they resolve `--root`, applies the
canonical checkout guard and wraps every `--json` result in the envelope of
`shared/conformance/CLI.md` § Output; `scripts/cli_conventions.py` holds the
exit codes, the shared options and the help form. The scripts stay runnable
directly with `uv run python scripts/<name>.py`.

### validate

`validate` checks what drifts silently: the shape of `workspace.yaml.example`
and canonical skills against their managed adapters. An adapter is a managed
copy rendered from its canonical skill (`render_adapter`): the same name and
description, the canonical body, the harness's overlay, and the ownership fields
`managed-by` and `canonical`. A canonical skill depends on no environment
variable naming this checkout, and no adapter is left behind when its
canonical skill goes. It also checks the task records under `.atc/tasks/` and
their `## Task records` declaration (`docs/TASKS.md`). By default it covers
this checkout's own `.claude/skills/`, `.agents/skills/` and `.dsh/skills/`,
the ones CI sees; `atc sync --check` validates each project of the machine-local
`workspace.yaml` that exists on disk (its adapters for every
`adoption: baseline` skill, the `AGENTS.md` pointer line, a current `.atc/`,
all tracked, task records, one prefix per repository) and rejects user-level
copies of a canonical skill under `~/.claude/skills`, `~/.agents/skills` or
`~/.dsh/skills`, so it runs locally, never in CI. It needs PyYAML, and jsonschema for the record check, and prints
every failure, not only the first.

`atc-testkit/` is the pytest adapter of the verification contract: marker
classification, change-impact selection, temporary locations, the stages, the
gate and verifier skeletons, and the installed-artifact check a consuming project
runs in CI. This adapter depends on it by in-tree path.

```powershell
uv run --locked python -m atc_testkit.ci --stages       # suite through the testkit's stages, including shared/conformance
uv run --locked python -m atc_testkit.ci --stages --workers 16   # the same with 16 workers in the parallel stage
uv run --locked pytest tests/test_tooling.py -k name    # one test, one invocation
uv run atc-maintain validate                            # this checkout's contract
uv run atc-maintain sync --root                                  # re-render .claude/ .agents/ .dsh/ after a canonical skill change (topic worktree only)
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
`uv run --locked atc-maintain validate`, run after touching them. Markdown under
another `shared/<area>/` selects the tests that read that area.
`tests/root_reads.py` records each test file's reads of the guarded paths,
and counts a run of this checkout's `scripts/*.sh` as reading what
`SCRIPT_READS` names; a session in which a test
file reads one undeclared fails with `ROOT_READ_UNDECLARED`, naming the
test and the paths, and under `-n` each worker hands its failures to the
controller. Existence checks and what a child Python process opens are not
recorded.

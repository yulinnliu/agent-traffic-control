# ATC home

The per-user directory where ATC's shared tools keep what they generate on a
machine. Everything in it is either disposable or regenerable: deleting the
whole directory loses nothing. Persistent state lives elsewhere
(§ Outside the ATC home).

## Location

- The ATC home is `.atc` in the user's home directory (`%USERPROFILE%\.atc` on
  Windows, `$HOME/.atc` elsewhere), written `~/.atc` below.
- The temporary root is `~/.atc/tmp`. `ATC_TMP`, when set to an absolute path,
  replaces it; this is how one machine places temporary files on a faster or
  shorter volume. The resolved root is written `<tmp>` below.
- No shared tool writes generated files to the system temporary directory, to
  a drive root, or into a repository's tracked tree.
- `<tmp>` lies outside `AppData`, where file metadata calls cost about ten
  times those elsewhere, and has no ancestor that holds `.git`, because
  product code under test runs Git when one does. An `ATC_TMP` value keeps
  both properties.
- CI (`CI` set) keeps the platform's own temporary locations.
- `~/.atc` is unrelated to a repository's tracked `.atc/` directory
  (`LAYOUT.md` § Inside a consuming repository) and is never a governance
  source.

## Layout

```text
~/.atc/
  tmp/                          disposable
    scratch-dirs.tsv            registry of scratch directories
    <scratch>/                  one per isolated test run
      bt/                       pytest basetemp
      qdr/                      QUANT_DATA_ROOT
    systmp/                     stand-in for the system temporary directory
      pytest-of-<user>/         pytest runs started without a scratch
    integration-candidates/     candidate trees and verifier reports of `atc integrate`
  cache/                        regenerable
    uv/                         UV_CACHE_DIR
    python/                     UV_PYTHON_INSTALL_DIR
    ruff/                       RUFF_CACHE_DIR
```

## Resolving the locations

One rule gives `<tmp>` and `<tmp>/systmp`, and every entry point applies it:

- `atc-testkit` publishes the Python implementation: it returns the two
  paths, creating them on first use, and offers one call that makes
  `<tmp>/systmp` the temporary directory of the current process and of every
  process it starts (`TEMP`, `TMP`, `TMPDIR`, and Python's `tempfile`). It
  also publishes a pytest plugin that makes the call at configure time, which
  every project that carries `atc-testkit` registers in its root
  `conftest.py`.
- An entry point makes that call before it creates a temporary file or starts
  a child. The entry points are:

  | Entry point | Owner |
  | --- | --- |
  | `atc test run`, `atc test mutants`, `atc integrate`, `build_testkit_release.py` | ATC |
  | `check.ps1` | ATC, applying the same rule in shell |
  | the integration verifier named by a project's verifier argv file | the project |
  | the pre-push gate | the project |

- A process started by an entry point inherits the setting, so a verifier or
  test that calls `tempfile` writes under `<tmp>/systmp` without naming it. A
  project script run on its own, outside every entry point, uses whatever
  temporary directory its caller has.
- An entry point may create its own working directory under `<tmp>/systmp`
  and point its children's temporary directory inside it.
- An entry point exports the resolved `<tmp>` as `ATC_TMP` to the processes
  it starts, so a child whose home directory is redirected
  (`TESTKIT.md` § Isolated environment) still resolves the same root.

## Test scratch

A scratch is the working directory of one isolated test run made through
`atc test run <tree> <scratch> <python> <label> <pytest args...>`.

- `<scratch>` is a name matching `[A-Za-z0-9._-]+`, resolved to
  `<tmp>/<scratch>`. A path is accepted only when it resolves to a direct
  child of `<tmp>`; anything else is refused with `SCRATCH_OUTSIDE_TMP`
  (exit 9) before anything is created. `systmp` and
  `integration-candidates` are refused as scratch names.
- The absolute long-name form of `<tmp>/<scratch>` is at most 64 characters;
  a longer one is refused with `SCRATCH_PATH_TOO_LONG` (exit 9).
- Inside the scratch, `bt` is pytest's basetemp and `qdr` is
  `QUANT_DATA_ROOT`. The run's temporary directory is `<tmp>/systmp`, shared
  with every other entry point, so the paths `tempfile` produces stay short.
- An exported copy of a tree (`git archive <sha>`) that a review runs against
  is itself placed at `<tmp>/<scratch>`.

## Scratch registry

`<tmp>/scratch-dirs.tsv` lists every scratch that exists, one per line.

- Encoding UTF-8 without BOM, LF line ends, tab-separated, first line the
  header `project	scratch	tree	created_utc`.
- `project`: `[project].name` from `<tree>/pyproject.toml`; the tree's
  directory name when that file or key is absent.
- `scratch`, `tree`: absolute native paths. `created_utc`: `YYYY-MM-DDTHH:MM:SSZ`.
- `atc test run` appends the line when it creates the scratch. A scratch that
  is reused keeps its existing line.
- `atc tmp clean <dir>...` deletes scratches and removes their lines. It
  refuses any `<dir>` that is not a direct child of `<tmp>` or that carries a
  reserved name, and treats links inside `<dir>\bt`, and `<dir>\.venv`, as
  expected links that are removed without being entered.
- `atc tmp clean --project <name>` selects every registered scratch of one
  project. `atc tmp list` prints the registry with each scratch's size and
  deletes nothing.

Scratches are classified by the `project` column, never by a directory level,
so classification costs no path length.

## Integration and the pre-push gate

- `atc integrate` creates each candidate tree at
  `<tmp>/integration-candidates/<id>` and names the verifier's report file
  `<tmp>/integration-candidates/<id>.report.json` through
  `ATC_VERIFICATION_REPORT`. It removes both when the run ends. The files that
  capture the verifier's output are under `<tmp>/systmp`.
- A project's integration verifier keeps its logs and per-stage data under
  `<tmp>/systmp`. It removes them after a passing run and leaves them, with
  the path printed, after a failing one.
- The pre-push gate, when it runs tests instead of reusing an evidence record,
  keeps its working files under `<tmp>/systmp` in the same way.
- The evidence record that lets the gate skip its tests is not temporary: it
  stays in the repository's git common directory (§ Outside the ATC home).
  Losing it makes the gate run the tests; it never makes the gate pass.

## Other temporary files

- **pytest without a scratch.** pytest's base follows the system temporary
  directory, so a run in a repository that registers the plugin gets
  `<tmp>/systmp/pytest-of-<user>/pytest-<N>/`. pytest removes runs older than
  the newest three at each run's start and exit and leaves a run with a live
  `.lock` alone. An explicit `--basetemp` or `PYTEST_DEBUG_TEMPROOT` wins.
- **`atc tmp prune`** deletes entries of `<tmp>/systmp`, other than
  `pytest-of-*`, and of `<tmp>/integration-candidates` whose last modification
  is older than seven days, and reports registered scratches older than seven
  days without deleting them.

## Cache

- `check.ps1` sets `UV_CACHE_DIR` to `~/.atc/cache/uv` and
  `UV_PYTHON_INSTALL_DIR` to `~/.atc/cache/python` when the caller has not set
  them.
- The gate and the verifier give the processes they start `UV_CACHE_DIR` as
  `~/.atc/cache/uv` and `RUFF_CACHE_DIR` as `~/.atc/cache/ruff`
  (`TESTKIT.md` § Isolated environment).
- A cache entry is safe to delete at any time; the next run downloads it again.

## Outside the ATC home

| What | Where | Why |
| --- | --- | --- |
| Workspace map, project checkouts, worktrees, project data | `workspace.yaml`, `projects/`, `worktrees/`, `state/<name>-data/` | bound to one workspace checkout (`LAYOUT.md`) |
| ATC friction records | `state/workspace-friction/` | persistent, bound to one workspace checkout |
| Verification evidence records, integration slot lock | the repository's git common directory | bound to one repository |
| Built testkit wheels | `.tooling/testkit-dist/` | release output of `build_testkit_release.py` |
| Installed `atc` command and its source snapshot | `~/.local/bin`, `~/.local/atc-src` | installer convention |
| A product's runtime files | the product's own locations (`PROJECT_RUNTIME.md`) | product state, not ATC's |

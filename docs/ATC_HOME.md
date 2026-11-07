# ATC home

The per-user directory where ATC's shared tools keep what they generate on a
machine. Everything in it is either disposable or regenerable: deleting the
whole directory loses nothing. Persistent state lives elsewhere
(§ Outside the ATC home).

## Location

- The ATC home is `.atc` in the user's home directory (`%USERPROFILE%\.atc` on
  Windows, `$HOME/.atc` elsewhere), written `~/.atc` below.
- The temporary root is `~/.atc/tmp`. `ATC_TMP`, when set to an absolute path,
  replaces it, and a value that is not absolute is refused with
  `ATC_TMP_UNSUITABLE`; this is how one machine places temporary files on a faster or
  shorter volume. The resolved root is written `<tmp>` below.
- No shared tool writes generated files to the system temporary directory, to
  a drive root, or into a repository's tracked tree.
- `<tmp>` lies outside `AppData`, where file metadata calls cost more than
  elsewhere (create 1.6x, stat 7.7x, delete 2.0x), and has no
  ancestor that holds `.git`, because
  product code under test runs Git when one does. Its absolute long-name form
  is at most 40 characters, which leaves a scratch name of at least 23. A
  root that breaks one of these is refused with `ATC_TMP_UNSUITABLE` and the
  property it breaks (exit 9), before anything is created.
- `<tmp>`, `<tmp>/systmp` and `<tmp>/candidates` are real directories. One
  that is a link or anything else that is not a directory is refused with
  `ATC_TMP_UNSUITABLE` and the property it breaks (exit 9), before anything
  is created. One that does not exist yet is created on first use.
- CI (`CI` set) keeps the platform's own temporary locations. `<tmp>` is the
  platform's temporary directory, or `ATC_TMP` when that is set and absolute,
  and the properties above are not checked on it. `<tmp>/systmp` and
  `<tmp>/candidates` are still real directories. An entry point that names no
  directory keeps the platform's temporary directory: its call to § Resolving
  the locations returns that directory, creates nothing, checks nothing and
  sets no variable, `ATC_TMP` included. One that names a directory gets the
  call as that section describes it.
- `~/.atc` is unrelated to a repository's tracked `.atc/` directory
  (`LAYOUT.md` § Inside a consuming repository) and is never a governance
  source.

## Layout

```text
~/.atc/
  tmp/                          disposable
    scratch-dirs.tsv            registry of scratch directories
    <scratch>/                  one per isolated test run; the run's temporary directory
      tree/                     exported copy of the tree under review
      bt/                       pytest basetemp
      qdr/                      data root
    systmp/                     stand-in for the system temporary directory
      pytest-of-<user>/         pytest runs started without a scratch
    candidates/                 candidate trees and verifier reports of `atc integrate`
  cache/                        regenerable
    uv/                         UV_CACHE_DIR
    python/                     UV_PYTHON_INSTALL_DIR
    ruff/                       RUFF_CACHE_DIR
```

## Resolving the locations

One rule gives `<tmp>` and `<tmp>/systmp`, and every entry point applies it:

- `atc-testkit` publishes the Python implementation, and `atc` uses it
  through the adapter's dependency on the package (`TESTKIT.md` § Commands).
  It returns the two
  paths, creating them on first use, and offers one call that makes a
  directory below `<tmp>` the temporary directory of the current process and
  of every process it starts (`TEMP`, `TMP`, `TMPDIR`, and Python's
  `tempfile`). The directory is `<tmp>/systmp` unless the entry point names
  another.
- An entry point makes that call before it creates a temporary file or starts
  a child. The entry points are:

  | Entry point | Temporary directory | Owner |
  | --- | --- | --- |
  | `atc test run`, `atc test mutants` | the scratch, `<tmp>/<scratch>` | ATC |
  | `atc integrate`, `build_testkit_release.py` | `<tmp>/systmp` | ATC |
  | `check.ps1` | `<tmp>/systmp`, applying the same rule in shell | ATC |
  | the integration verifier named by a project's verifier argv file | `<tmp>/systmp`, or a working directory it creates there | the project |
  | the pre-push gate | `<tmp>/systmp`, or a working directory it creates there | the project |

- A process started by an entry point inherits the setting, so a verifier or
  test that calls `tempfile` writes there without naming it. A project script
  run on its own, outside every entry point, uses whatever temporary
  directory its caller has.
- The pytest plugin every project registers (`TESTKIT.md` § Adoption) makes
  the call at configure time only when the process's temporary directory is
  not already below `<tmp>`. It changes nothing when `--basetemp` or
  `PYTEST_DEBUG_TEMPROOT` is given.
- An entry point exports the resolved `<tmp>` as `ATC_TMP` to the processes
  it starts, so a child whose home directory is redirected
  (`TESTKIT.md` § Isolated environment) still resolves the same root.

## Test scratch

A scratch is the working directory of one isolated test run made through
`atc test run` (`TESTKIT.md` § Commands).

- `<scratch>` is a name matching `[A-Za-z0-9._-]+`, resolved to
  `<tmp>/<scratch>`. A path is accepted only when it resolves to a direct
  child of `<tmp>`; anything else is refused with `SCRATCH_OUTSIDE_TMP`
  (exit 9) before anything is created. `systmp` and
  `candidates` are refused as scratch names.
- The absolute long-name form of `<tmp>/<scratch>` is at most 64 characters;
  a longer one is refused with `SCRATCH_PATH_TOO_LONG` (exit 9).
- The scratch is the run's temporary directory. `bt` inside it is pytest's
  basetemp, so state a test builds in its temporary path lies below the
  temporary directory, which product code that requires isolated state
  checks. `qdr` inside it is the data root.
- An exported copy of a tree (`git archive <sha>`) that a review runs against
  is placed at `<tmp>/<scratch>/tree`.

## Scratch registry

`<tmp>/scratch-dirs.tsv` lists every scratch that exists, one per line.

- Encoding UTF-8 without BOM, LF line ends, tab-separated, first line the
  header `project	scratch	tree	created_utc`.
- `project`: `[project].name` from `<tree>/pyproject.toml`; the tree's
  directory name when that file or key is absent.
- `scratch`, `tree`: absolute native paths. `created_utc`: `YYYY-MM-DDTHH:MM:SSZ`.
- `atc test run` adds the line at the start of a run whenever the scratch has
  none, whether or not the directory already exists.
- Every change to the file is made under `<tmp>/scratch-dirs.lock` and
  replaces the file in one step.
- `atc tmp list` prints the registry with each scratch's size and deletes
  nothing.

Scratches are classified by the `project` column, never by a directory level,
so classification costs no path length.

## Deleting

`atc tmp clean <dir>...` deletes scratches; `atc tmp clean --tree-name <name>`
takes its directories from the registry rows whose `project` column is
`<name>`. Both pass every directory through the same checks, and the single
recursive delete in ATC's temporary tooling runs only at the end of them.
`atc-testkit` 0.7.x also accepts `--project <name>` in place of
`--tree-name <name>`, with a `NOTE` on stderr naming the new option; 0.8.0
accepts only `--tree-name`.

1. Each `<dir>` is resolved first. One that is not a direct child of `<tmp>`
   through real directories, or that carries a reserved name, prints
   `REFUSED` with the reason, and the command exits 2 before anything is
   deleted.
2. A missing `<dir>` prints `SKIPPED` and loses its registry line.
3. A `<dir>` that is not a directory, or is itself a link, prints `REFUSED`.
4. Links found in the tree are sorted. Expected ones are `<dir>\.venv`,
   `<dir>\tree\.venv` and any link inside `<dir>\bt`; they are removed as
   links and never entered. Any other link prints `ANOMALY` with its path,
   the `<dir>` prints `REFUSED` and is left as it is.
5. The tree is scanned again and deleted recursively only when the scan finds
   no link. A link that remains prints `LEFT` and the `<dir>` prints
   `REFUSED`.
6. A deleted `<dir>` prints `DELETED` and loses its registry line.

Each `<dir>` ends on one line: `DELETED`, `SKIPPED` or `REFUSED` with the
reason; an error while handling a `<dir>` is a `REFUSED` with the error. The exit code is the highest across all of them: 0, 1 for a refusal
at steps 3 to 5, 2 for a refusal at step 1.

`atc tmp prune` deletes entries whose last modification is older than seven
days. An entry that is a link prints `REFUSED` and is left. A plain file is
deleted as a file. A directory goes through steps 4 and 5 with no link
expected, except a working directory of the verifier or of the gate.

- Under `<tmp>/systmp` it takes every entry other than `pytest-of-*`. This
  includes the working directory a failed verifier or gate run left for
  inspection, named `w` and six lowercase hexadecimal digits. Links inside a
  base directory, `<work>/<stage>/bt`, are the run's own: they are expected,
  removed as links and never entered. `<stage>` is a stage of `TESTKIT.md`
  § Stages or `tests`, the workspace root's test command.
- Under `<tmp>/candidates` it removes a candidate tree through
  `git worktree remove` in the repository that owns it, which the tree's own
  `.git` file names; a tree Git refuses to
  remove prints `REFUSED` with Git's message and is left. A report file, and
  a tree Git no longer knows, are handled as any other entry.

It reports registered scratches older than seven days and deletes none.

## Integration and the pre-push gate

- `atc integrate` creates each candidate tree at
  `<tmp>/candidates/<id>` and names the verifier's report file
  `<tmp>/candidates/<id>.report.json` through
  `ATC_VERIFICATION_REPORT`. `<id>` is twelve random lowercase hexadecimal
  digits, so the tree's absolute path is at most 64 characters, the bound of
  a scratch. It removes both when the run ends. The files that
  capture the verifier's output are under `<tmp>/systmp`.
- A project's integration verifier keeps its logs and per-stage data in a
  working directory under `<tmp>/systmp`. It removes the directory after a
  passing run and leaves it, with the path printed, after a failing one.
- A working directory of the verifier or of the gate has a name of at most
  eight characters, so its absolute
  path is at most 56. pytest's base directory of every stage lies in it and
  never in the candidate tree or the checkout, whose own path would leave
  the paths a test builds no room under the Windows path limit.
- The pre-push gate, when it runs tests instead of reusing an evidence record,
  keeps its working files under `<tmp>/systmp` in the same way.
- The evidence record that lets the gate skip its tests is not temporary: it
  stays in the repository's git common directory (§ Outside the ATC home).
  Losing it makes the gate run the tests; it never makes the gate pass.

## pytest without a scratch

pytest's base follows the temporary directory, so a run started directly in a
repository that registers the plugin gets
`<tmp>/systmp/pytest-of-<user>/pytest-<N>/`. pytest removes runs older than
the newest three at each run's start and exit and leaves a run with a live
`.lock` alone. The ATC adapter's own test suite and `check.ps1` follow the
same rule and pass no `--basetemp` of their own.

This base carries the user name and has no length bound of its own. A suite
whose paths need the shorter base runs through `atc test run`, the gate or
the verifier.

## Cache

- `check.ps1` sets `UV_CACHE_DIR` to `~/.atc/cache/uv` and
  `UV_PYTHON_INSTALL_DIR` to `~/.atc/cache/python` when the caller has not set
  them.
- The gate and the verifier give the processes they start the same two
  values and `RUFF_CACHE_DIR` as `~/.atc/cache/ruff`
  (`TESTKIT.md` § Isolated environment).
- A cache entry is safe to delete at any time; the next run downloads it again.

## Outside the ATC home

| What | Where | Why |
| --- | --- | --- |
| Workspace map, project checkouts, worktrees, project data | `workspace.yaml`, `projects/`, `worktrees/`, `state/<name>-data/` | bound to one workspace checkout (`LAYOUT.md`) |
| ATC friction records | `state/workspace-friction/` | persistent, bound to one workspace checkout |
| Verification evidence records, integration slot lock | the repository's git common directory | bound to one repository |
| The latest pre-push gate logs | the checkout's ignored `.tmp/` directory | read next to the checkout after a refused push |
| Built testkit wheels | `.tooling/testkit-dist/` | release output of `build_testkit_release.py` |
| Installed `atc` command and its source snapshot | `~/.local/bin`, `~/.local/atc-src` | installer convention |
| A product's runtime files | the product's own locations (`PROJECT_RUNTIME.md`) | product state, not ATC's |

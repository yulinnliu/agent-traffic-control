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
  is created. One that does not exist yet is created on first use. A `<tmp>`
  whose nearest existing ancestor is a link to a missing target is refused in
  the same way: creating it would recreate that target wherever the link
  points.
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
  config.toml                   host settings, written by hand (§ Host settings)
  tmp/                          disposable
    scratch-dirs.tsv            registry of scratch directories
    <scratch>/                  one per isolated test run; the run's temporary directory
      tree/                     exported copy of the tree under review
      bt/                       pytest basetemp
      qdr/                      data root
    systmp/                     stand-in for the system temporary directory
      pytest-of-<user>/         pytest runs started without a scratch
    candidates/                 candidate trees, verifier reports and logs of `atc integrate` and `atc test ID --candidate`
  run/                          the host worker pool (`TESTKIT.md` § Host worker budget); never deleted
    workers.lock                the pool's tokens, one locked byte each
    workers/                    one row per process using the pool, locked for its life
    memory/                     per-project worker memory of recent runs, `<project>.json`
  cache/                        regenerable
    uv/                         UV_CACHE_DIR
    python/                     UV_PYTHON_INSTALL_DIR
    ruff/                       RUFF_CACHE_DIR
```

## Host settings

`~/.atc/config.toml` holds settings of this host, outside every repository,
so none reaches CI or another host. It is a TOML table written by hand; a
missing file or key means the default.

| Key | Meaning |
| --- | --- |
| `test_workers` | the testkit's parallel-stage worker count (`TESTKIT.md` § Stages), a TOML integer from 1 to the logical CPU count (the `--workers` flag takes up to the larger of 8 and that count); the host pool may cut it |
| `test_worker_pool` | the tokens of the host worker pool (`TESTKIT.md` § Host worker budget), a TOML integer from 0 to the logical CPU count; three quarters of the logical CPUs rounded up when absent; `0` turns the pool off |
| `nonpaged_pool_warn_gib` | the kernel nonpaged pool, in GiB, above which `atc doctor` warns of a leaking driver (`TESTKIT.md` § Host worker budget), a TOML integer from 1 to 1024; `max(4, physical memory / 8)` when absent |
| `test_worker_max_wait` | the seconds a run without a timeout of its own may wait for host workers in total, a TOML integer from 1 to 86400; 1800 when absent |

With `CI` set the file is not read. An entry point reads it in its own
process, before a run's environment points `HOME` into the run's working
directory, so a child the run starts never reads it.

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
  | `atc test ID`, `atc test ID --mutants FILE` | the scratch, `<tmp>/<scratch>` | ATC |
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
`atc test ID` (`TESTKIT.md` § Commands).

- `<scratch>` is a name matching `[A-Za-z0-9._-]+`, the task ID or `<ID>-<sha7>`, resolved to
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
  is placed at `<tmp>/<scratch>/tree`; `atc test ID --sha SHA` makes one
  (§ Checkout).

## Checkout

`atc test ID --sha SHA` prepares a scratch for a review or a run of one commit
of the task `ID`.

1. `SHA`, a full SHA or a unique prefix, is resolved in the task's
   worktree; a commit it does not name stops with `SHA_NOT_FOUND`, a
   directory outside any working tree with `NOT_IN_REPOSITORY`. `ID` has the
   task form `ATC-0008`; another form stops with `TASK_INVALID`.
2. The scratch is `<ID>-<first seven characters of the commit>`, resolved as
   § Test scratch says. A `tree` already in it is reused, its environment
   built once.
3. The scratch gets its registry line with its task, the source worktree
   and the full commit, its `project` read from the commit's
   test directory's `pyproject.toml`, and as its `tree` that test directory
   (`TESTKIT.md` § Commands), where `atc test ID --sha SHA` runs. Then `git archive` of
   the commit is unpacked into `<tmp>/<scratch>/tree`; the line prints as
   `CHECKOUT <scratch> <tree> <commit>`.
4. `uv sync --locked` runs in the test directory without `VIRTUAL_ENV` and
   `UV_PROJECT_ENVIRONMENT`, and `SYNCED <environment>` names the `.venv`
   it built. A directory without `uv.lock` stops with `LOCK_MISSING`,
   which names the project's `AGENTS.md` preparation; a failed sync stops
   with `SYNC_FAILED` and the sync's last twenty lines (exit 1).

Refusals exit 9 unless stated. A refusal before step 3 creates nothing; one
from step 3 on, `EXPORT_FAILED` among them, leaves the scratch registered,
so `atc test ID --clean` and `atc task close` remove it.

## Scratch registry

`<tmp>/scratch-dirs.tsv` lists every scratch that exists, one per line.

- Encoding UTF-8 without BOM, LF line ends, tab-separated, first line the
  header `project	scratch	tree	created_utc	task	source	commit`.
- `project`: `[project].name` from `<tree>/pyproject.toml`, or from the
  test directory of an export; the directory's name when that file
  or key is absent.
- `scratch`, `tree`: absolute native paths. `created_utc`: `YYYY-MM-DDTHH:MM:SSZ`.
- `task`: the task the scratch belongs to, which `atc task close` reads
  (`TASKS.md` § Close-out). `atc test ID` writes its `ID`, as do its
  `--sha` and `--mutants` forms.
- `source`, `commit`: the source worktree and the full commit of a scratch
  `atc test ID --sha SHA` made; empty for other scratches.
  A line of the four-column format reads with the three empty, and a change
  to the file keeps it as it is.
- `atc test ID` and `atc test ID --mutants FILE` add the line at the start of a run
  whenever the scratch has none, whether or not the directory already
  exists. An empty task of an existing line is filled; a line that names
  another task refuses the run with `SCRATCH_TASK_MISMATCH` (exit 9), so no
  scratch changes task.
- Every change to the file is made under `<tmp>/scratch-dirs.lock` and
  replaces the file in one step.
- `atc test --list` prints one line per scratch and deletes nothing: its name,
  its tree relative to the scratch (the full path when the tree lies outside
  it), its age, `missing` when the directory is gone, and its task when the
  row has one; `atc test --list ID` prints the scratches of one task. A line
  after them counts the missing scratches. Each leftover candidate
  (§ Candidates) then prints `candidates/<id>`, its age, `leftover`, its
  owner's command, task and PID (or `no owner marker`) and its repository (or
  `repository gone`), and a last line counts them.
  `atc test --list --json` prints the envelope of `shared/conformance/CLI.md`
  § Output with every row under `scratches`, all its columns plus `size` in
  bytes (`null` when the directory is missing).

Scratches are classified by the `project` column, never by a directory level,
so classification costs no path length.

## Candidates

`atc integrate` and `atc test ID --candidate` write `<tmp>/candidates/<id>.owner.json`
before they add the candidate worktree `<tmp>/candidates/<id>`, and remove
both when they finish: `pid`, `created` (the process's creation time),
`command`, `repo`, `task` and `started_at`. The candidate is removed with
`git worktree remove -f -f`, so a dirty or locked candidate goes too; when
its directory is already gone, its record under the repository's
`worktrees/` whose `gitdir` names it is removed instead (paths compared
without case). A file another process holds open still makes the removal
fail, which prints the retained-candidate warning.

A candidate is in use while a process with the marker's `pid` and the same
creation time runs; a process whose creation time cannot be read counts as
running. It is left over when that process is gone, or, without a marker,
when the directory was last modified more than an hour ago; a younger
candidate without a marker is neither. An owner counted as running only
because its creation time cannot be read is not confirmed: the seven-day
rule of § Deleting still removes its candidate. `atc test --list` names the leftovers,
`atc task list -v` reports a candidate Git still lists as
`CANDIDATE_LEFTOVER` or `CANDIDATE_IN_USE`, and only the prune that starts
every `atc test` run removes them (§ Deleting).

## Deleting

`~/.atc/run` is outside `<tmp>`, and no ATC command deletes it: a new
`workers.lock` beside a process still holding the old one would split the
pool. A stale row in `run/workers/` is removed by the next run that reads it.
`run/memory/` is a measurement the next runs rebuild; deleting it by hand
only returns the projects to the default worker memory until they run.

`atc test ID --clean` deletes the scratches registered to the task `ID`,
together with their dependent runs. Every scratch directory passes through
the same checks, and the single recursive delete in ATC's temporary tooling runs only at the end of them.

1. Each `<dir>` is resolved first. One that is not a direct child of `<tmp>`
   through real directories, or that carries a reserved name, prints
   `REFUSED` with the reason, and the command exits 1 before anything is
   deleted.
2. A missing `<dir>` prints `SKIPPED` and loses its registry line.
3. A `<dir>` that is not a directory, or is itself a link, prints `REFUSED`.
4. Links found in the tree are sorted. Expected ones are `<dir>\.venv`,
   `<dir>\tree\.venv`, a `.venv` below `<dir>\tree` whose directory holds a
   `pyproject.toml` that is a regular file, such as a whole repository's
   `tree\adapters\python\.venv`, and any link inside `<dir>\bt`; they are
   removed as links and never entered, also when their target is gone. Any other link prints `ANOMALY` with its path,
   the `<dir>` prints `REFUSED` and is left as it is.
5. The tree is scanned again and deleted recursively only when the scan finds
   no link. A link that remains prints `LEFT` and the `<dir>` prints
   `REFUSED`.
6. A deleted `<dir>` prints `DELETED` and loses its registry line.

Each `<dir>` ends on one line on stdout: `DELETED`, `SKIPPED` or `REFUSED`
with the reason; an error while handling a `<dir>` is a `REFUSED` with the
error. The exit code is 0, or 1 when any `<dir>` is refused, and a refusal
also prints `STOP SCRATCH_REFUSED: <count> refused; ...` on stderr
(`shared/conformance/CLI.md` § Exit codes).

A scratch's dependent runs are the other registered scratches whose `tree`
lies inside it, such as the mutant and test runs made on its export. After
step 1 each one prints `DEPENDENT <run>` and is left. With `--with-runs`
(always in `atc test ID --clean`) each goes through steps 2 to 6
itself, before the scratch it depends on.

The prune that starts every `atc test` run deletes entries whose last modification is older than seven
days. An entry that is a link prints `REFUSED` and is left. A plain file is
deleted as a file. A directory goes through steps 4 and 5 with no link
expected, except a working directory of the verifier or of the gate.

- Under `<tmp>/systmp` it takes every entry other than the ones a toolchain's
  runner expires itself, `pytest-of-*` (`TESTKIT.md` § Toolchain). This
  includes the working directory a failed verifier or gate run left for
  inspection, named `w` and six lowercase hexadecimal digits. Links inside a
  base directory, `<work>/<stage>/bt`, are the run's own: they are expected,
  removed as links and never entered. `<stage>` is a stage of `TESTKIT.md`
  § Stages or `tests`, the workspace root's test command.
- Under `<tmp>/candidates` it removes a candidate tree through
  `git worktree remove -f -f` in the repository that owns it, which the
  tree's own `.git` file names; a tree Git refuses to remove prints
  `REFUSED` with Git's message and is left. A candidate whose owner is
  confirmed to run, its `pid` and creation time matching (§ Candidates), is
  left at any age; one whose owner's creation time cannot be read is left
  until it is older than seven days. A leftover candidate is removed at any
  age, with its marker; a marker whose candidate is gone and whose owner no
  longer runs is deleted after the repository it names forgets the
  candidate. A report file, and a tree Git no longer knows, are handled as
  any other entry.

A refused entry makes the exit code 1 and prints
`STOP PRUNE_REFUSED: <count> refused; ...` on stderr.

It reports registered scratches older than seven days and deletes none.

## Integration and the pre-push gate

- `atc integrate` and `atc test ID --candidate` create each candidate tree at
  `<tmp>/candidates/<id>` and name the verifier's report file
  `<tmp>/candidates/<id>.report.json` through
  `ATC_VERIFICATION_REPORT`. `<id>` is twelve random lowercase hexadecimal
  digits, so the tree's absolute path is at most 64 characters, the bound of
  a scratch. They remove both when the run ends. The files that
  capture the verifier's output are under `<tmp>/systmp`; its stdout and
  stderr are then written to `<tmp>/candidates/<id>.verify.log`, which the
  run names and the prune deletes after seven days.
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
whose paths need the shorter base runs through `atc test ID`, the gate or
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
| Verification evidence records, integration slot lock | the repository's git common directory | bound to one repository |
| The latest pre-push gate logs | the checkout's ignored `.tmp/` directory | read next to the checkout after a refused push |
| Built testkit wheels | `.tooling/testkit-dist/` | release output of `build_testkit_release.py` |
| Installed `atc` command | `~/.local/bin`, an editable install of the checkout's `adapters/python` | installer convention; it runs the checkout's code (root `AGENTS.md` `## Integration verification`) |
| A product's runtime files | the product's own locations (`PROJECT_RUNTIME.md`) | product state, not ATC's |

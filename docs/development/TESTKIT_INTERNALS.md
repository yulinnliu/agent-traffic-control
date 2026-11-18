# atc-testkit internals

How `atc-testkit` is built inside: what a maintainer changing the package
needs and a user never runs or sees. The behaviour users rely on is
`docs/TESTKIT.md`, the evidence formats
`shared/conformance/verification-evidence/README.md`, and the package's API
its own `adapters/python/atc-testkit/README.md`.

## Packaging

`atc test` is implemented in `atc-testkit`. The adapter depends on the
package by its path in the tree (`adapters/python/README.md`), and `atc`
dispatches to it; `atc integrate` takes the location rule
(`docs/ATC_HOME.md` § Resolving the locations) from the same package.

`adapters/python/scripts/build_testkit_release.py` builds the wheel and its
release record. Without `--output-dir` it writes them to
`.tooling/testkit-dist/` in the workspace, which Git ignores; a project's
upgrade passes a fresh directory instead (`docs/TESTKIT.md` § Upgrading a
project's testkit).

## Input evidence

The record format, its lookup and its coverage are
`shared/conformance/verification-evidence/README.md` § Input evidence; what
a run reuses and prints is `docs/TESTKIT.md` § Input evidence. Between the
processes of a run:

- The integration verifier, before each pytest stage of a candidate,
  collects the stage into the file `ATC_COLLECTED_FILES` names, where the
  plugin writes the selected tests' files, and runs only the files no record
  covers.
- It sets `ATC_FILE_OUTCOMES` only for the stages whose evidence it records,
  and the plugin writes each test file's outcome there. The isolated
  environment removes the caller's value (`docs/TESTKIT.md` § Isolated
  environment), so a run nested under one that records never writes into
  the outer run's outcomes file.
- `atc test ID` asks the project's verifier for the commit's input set by
  running the tree's own verifier argv file with `ATC_INPUTS_OF=<commit>` in
  that tree. The verifier prints `{"commit", "excluded", "dependency_files"}`
  and runs no check.
- An export is no Git checkout, so for the classification it gets a Git
  directory beside it, `<scratch>/classify.git`, that reads the source
  repository's objects through `objects/info/alternates`, with `HEAD` and the
  index at the commit, removed with its `.git` pointer file afterwards.

## Line recorder

The recorder behind the mutant baseline's line records
(`docs/TESTKIT.md` § `atc test ID --mutants FILE`):

- The recorder is a `sys.monitoring` tool the command writes to
  `<scratch>/lines/hook`: a `sitecustomize` that hands over to the one it
  shadows, and a pytest plugin that gives each test its own context, passed
  to its child processes in `ATC_LINES_CONTEXT`.
- A test's context and its collected name are its node ID as collected,
  without the `@<group>` that pytest-xdist's `loadgroup` appends in a
  worker.
- Each process appends its rows to its own file in `<scratch>/lines/data`,
  which every baseline starts empty.
- Each pytest process that loads the plugin, the xdist controller and every
  worker, marks that it loaded it and, when its recorder started, that it
  started; each that collects writes the collected tests in order.
- The records of all processes are merged; the baseline counts as recorded
  only when every process that loaded the plugin started its recorder, and
  its order is the collection order, the order a single-process baseline
  runs.
- Each process writes the seconds of its tests to `durations-<pid>`; a name
  two processes recorded keeps the longer time.

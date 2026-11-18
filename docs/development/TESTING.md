# Testing this repository

How a maintainer's change to this repository is verified: the integration
verifier of the workspace root, and the scripts that run the adapter's own
suite. The commands that select and run the suite, and the rules of the
selection, are `adapters/python/README.md` § Selected tests. A project's
verifier and the testkit's behaviour are the user-facing `docs/TESTKIT.md`.

## The workspace root's verifier

The workspace root builds the testkit and vendors no wheel. Root `AGENTS.md`
§ Integration verification names its argv file, which runs
`adapters/python/scripts/integration_verify.py` in the candidate's own
`adapters/python/.venv/` built from `uv.lock`. That script supplies the
root's data to the same skeleton (`docs/TESTKIT.md` § Integration verifier),
imported from the tree, with the rows a consumer's data feeds absent: no
selection hook, lock, contract or collection row. `docs/TESTKIT.md`
§ Isolated environment, § Running and logging and the working-directory
rules bind it as they bind a project's verifier.

- Its own rows carry its own tokens: after the ancestry rows it reads the
  range, and a range Git cannot read refuses with `RANGE_UNREADABLE`; its
  static checks follow, then the stages.
- Static checks: the lint stage over the range. The root's
  `.pre-commit-config.yaml` holds its workspace checks as `local` hooks
  (`atc-maintain validate`, `atc-maintain policies check`), with the drift
  checks and the link check; the `python-adapter` job in
  `.github/workflows/validate.yml` runs the same list over every file.
- Selection: in place of the job's full run of the stages, `test_paths`
  maps the range through `select_tests.py` (`adapters/python/README.md`
  § Selected tests) to the pytest paths the stages run in `tests_dir`: `[]`
  for the full suite, where the selector's rules widen to it, and `None` for
  none, which skips the stages. A failure of it refuses with
  `SELECTION_FAILED`.
- Documentation-only (governance `VERIFICATION.md` § Documentation-only
  verification), decided by consumers: a range is documentation-only, and
  skips the selection and the stages, when every path is Markdown under
  `docs/`, a task record under `.atc/tasks/`, or Markdown the selection maps
  to no test file. Markdown a test reads or a tool renders (the guidance,
  skills and their managed copies, the root's top-level Markdown) selects its
  readers and is not documentation-only. The caller never chooses the
  exception. The log line `classification: full` names every range that is
  not documentation-only; the selector's lines after it say which tests ran.
- The candidate has no `workspace.yaml`, `projects/`, `worktrees/` or
  `state/`, so the checks read the committed tree only, as CI does.
- A pass writes the verification report, as a consumer's does
  (`shared/conformance/verification-evidence/README.md` § Report), so
  `atc integrate` reuses the reviewer's `atc test ID --candidate` record of a
  run on the same tree in place of running the verifier again
  (`shared/conformance/verification-evidence/README.md` § Integration reuse).
  Its `dependency_inputs` are the adapter toolchain's dependency files,
  named from the root (`adapters/python/uv.lock`,
  `adapters/python/.python-version`); its `classification` is
  `documentation-only` or `impact-selected`.
- The full suite, and with it the check that fails a session whose tests
  read the root's guidance undeclared (`ROOT_READ_UNDECLARED`), runs only in
  a manually dispatched run of `.github/workflows/validate.yml`
  (governance `VERIFICATION.md` § Full regression timing and evidence
  reuse); the Owner starts it.

## check.ps1

`adapters/python/check.ps1` runs the adapter's checks and suite in one
command (`adapters/python/README.md`). It is an entry point of
`docs/ATC_HOME.md` § Resolving the locations and applies that rule in
shell, with `<tmp>/systmp` as its temporary directory, since uv needs the
values before any Python runs; `tests/test_check_ps1.py` compares it with
`atc_testkit/locations.py`.

- It sets `UV_CACHE_DIR` to `~/.atc/cache/uv` and `UV_PYTHON_INSTALL_DIR`
  to `~/.atc/cache/python` when the caller has not set them
  (`docs/ATC_HOME.md` § Cache).
- The adapter's own test suite and `check.ps1` follow the rule of
  `docs/ATC_HOME.md` § pytest without a scratch and pass no `--basetemp` of
  their own.

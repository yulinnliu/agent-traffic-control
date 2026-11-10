# AGENTS.md

Bootstrap and safety guidance for this repository: the governance and routing
layer above the independent project repositories under `projects/`. Directory
layout lives in `docs/LAYOUT.md`.

## Governance bootloader

Resolve governance in this order: exact source/revision from task authority; otherwise approved project/task pin; otherwise the `governance_source` recorded in `workspace.yaml`, at its canonical `main` tip resolved once and frozen; otherwise stop.

Then read `shared/governance/INDEX.md` and `CORE.md` from that exact same revision, followed only by routed modules. A project repository reads its own vendored copy under `.atc/governance/` instead; this checkout's location is never part of governance identity.

## Where things live

- Normative governance lives only under `shared/governance/`. Changing it routes through `GOVERNANCE_CHANGES.md`.
- Policy shapes live in `shared/policies/schemas/`; `shared/policies/examples/` are illustrations, never a project's real policy.
- A shared skill's single source is `shared/skills/<name>/SKILL.md` plus optional per-tool overlays in `adapters/<tool>.md`; `.claude/`, `.agents/` and `.dsh/` hold managed copies rendered from them by `atc onboard --sync --root-only`, never edited by hand. A canonical skill never names this checkout by path or environment variable.
- `CLAUDE.md` here is what onboarding copies verbatim into every registered project, so it stays a one-line pointer that reads correctly from any repo. Guidance belongs in this file or `docs/`.
- `scripts/` holds POSIX sh entry points that need no runtime (install and check adapters); tool implementations live under `adapters/<language>/`; the contract they implement is `shared/conformance/`, not their own tests.

## Changing this repo

- Read by default; editing this repo requires explicit task authority.
- The installed `atc` runs this canonical checkout's code, so the checkout stays on `main` with no tracked modification: off `main` every `atc` command but `doctor` refuses, and `onboard --root-only` refuses to render here.
- Authorized changes use a managed worktree under `worktrees/root/` from `atc worktree create --repo <this checkout>`; `atc integrate --repo <this checkout>` fast-forwards `main` here.
- Integration, publication and deployment are separately governed actions.
- Never aim destructive project/worktree tooling at this checkout itself. Do not run `git clean -fdx` here or recursively delete `projects/`, `worktrees/`, or `state/` from above.
- Read back changes, then validate from the topic worktree's `adapters/python/`, entered in a subshell `( cd <worktree>/adapters/python && ... )` and through `uv run --locked atc` (bare `atc` runs this checkout's code): `onboard --sync --root-only` followed by `validate` after touching a shared skill or overlay, `policies check ../../shared/policies/examples` after touching a schema or example, and `uv run --locked pytest` (reads `shared/conformance/`) after touching a tool or fixture.

## Task records

- Task prefix: ATC

Records live in `.atc/tasks/`; `docs/TASKS.md` specifies them and `atc task`.
Tasks here are identified by record (governance `CORE.md` § Task identity).

## Integration verification

`atc integrate` runs this verifier in its candidate checkout (governance
`GIT_WORKFLOW.md` § Integration verifier source).

- Verifier argv file: adapters/python/scripts/integration-verify.json
- Verifier timeout: 2040
  (`adapters/python/README.md` states how this value is derived)

The argv runs `adapters/python/scripts/integration_verify.py` in the
candidate's own `adapters/python/.venv/` built from `uv.lock`; that script
supplies the root's data to the verifier skeleton (`docs/TESTKIT.md`
§ Integration verifier), which defines its input, import-location and
clean-tree checks, its environment, working directory and logs. The verifier
writes no verification report, so `atc integrate` records no evidence for this
repository.

Every range passes `git diff --check` of the range and the static checks of
the `python-adapter` job in `.github/workflows/validate.yml`, run from
`adapters/python`: `uv run --locked python scripts/validate_workspace.py` and
`uv run --locked python scripts/policies.py check ../../shared/policies/examples`.
In place of the job's full `uv run --locked pytest`, it runs the impact
selection over the range, `uv run --locked python scripts/select_tests.py
--base <expected main> --run`, which runs the full suite only where the
selector's rules widen to it (`adapters/python/README.md` § Selected tests).
The candidate has no `workspace.yaml`, `projects/`, `worktrees/` or `state/`,
so the checks read the committed tree only, as CI does.

The verifier applies `VERIFICATION.md` § Documentation-only verification
itself, by consumers: a range is documentation-only, and skips the selection,
when every path is Markdown under `docs/`, a task record under `.atc/tasks/`,
or Markdown the selection maps to no test file. Markdown a test reads or a
tool renders (the guidance, skills and their managed copies, the root's
top-level Markdown) selects its readers and is not documentation-only. The
caller never chooses the exception. The verifier's log line
`classification: full` names every range that is not documentation-only; the
selector's lines after it say which tests ran.

The full suite, and with it the check that fails a session whose tests read
the root's guidance undeclared (`ROOT_READ_UNDECLARED`), runs only in a
manually dispatched run of `.github/workflows/validate.yml`
(`VERIFICATION.md` § Full regression timing and evidence reuse); the Owner
starts it.

The installed `atc` is an editable install of `adapters/python`: it reads the
checkout's code and holds the dependencies of the moment it was installed. An
integrated range that changes the dependencies in
`adapters/python/pyproject.toml` or `adapters/python/atc-testkit/pyproject.toml`
is followed, on each host with an installed `atc`, by
`uv tool install -e "<checkout>/adapters/python" --force`. Until
then a command that imports a new dependency stops with
`ModuleNotFoundError`, and `uv run --locked atc` from `adapters/python` works.

## This host

- Taipei time comes from PowerShell `[TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), 'Taipei Standard Time')`; Git Bash ignores `TZ=Asia/Taipei` and still prints GMT.

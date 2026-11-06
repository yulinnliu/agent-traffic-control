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
- Authorized changes use a topic branch in this canonical checkout; the managed worktree tooling is for registered project repos, not this root.
- Integration, publication and deployment are separately governed actions.
- Never aim destructive project/worktree tooling at this root. Do not run `git clean -fdx` here or recursively delete `projects/`, `worktrees/`, or `state/` from above.
- Read back changes, then validate from `adapters/python/` with `uv run atc onboard --sync --root-only` followed by `uv run atc validate` after touching a shared skill or overlay, `uv run atc policies check ../../shared/policies/examples` after touching a schema or example, and `uv run --locked pytest` (reads `shared/conformance/`) after touching a tool or fixture.

## Integration verification

`atc integrate` runs this verifier in its candidate checkout (governance
`GIT_WORKFLOW.md` § Integration verifier source).

- Verifier argv file: adapters/python/scripts/integration-verify.json
- Verifier timeout: 1080

The argv runs `adapters/python/scripts/integration_verify.py` in the
candidate's own `adapters/python/.venv/` built from `uv.lock`; that script
defines its input, import-location and clean-tree checks. A refusal exits
nonzero with the reason token on the first stderr line and keeps the failing
check's log in an `atc-root-verify-*` directory under the system temp
directory. Pytest's base directory lies there too, because the candidate's
path leaves the repositories the tests build no room under the Windows path
limit. The verifier writes no
verification report, so `atc integrate` records no evidence for this
repository.

Every range passes `git diff --check` of the range and the commands of the
`python-adapter` job in `.github/workflows/validate.yml`, run from
`adapters/python`: `uv run --locked python scripts/validate_workspace.py`,
`uv run --locked python scripts/policies.py check ../../shared/policies/examples`
and `uv run --locked pytest`. The candidate has no `workspace.yaml`,
`projects/`, `worktrees/` or `state/`, so the checks read the committed tree
only, as CI does.

The verifier applies `VERIFICATION.md` § Documentation-only verification
itself: when every path in the range is Markdown and none is `AGENTS.md`,
`CLAUDE.md` or a file under `shared/skills/`, `shared/governance/`,
`shared/conformance/` or `adapters/`, the range is documentation-only and
skips `pytest` alone. The caller never chooses the exception.

The timeout is three times the measured wall clock of a full run, rounded up
to a whole minute and never below 300 seconds; re-measure it when the suite's
duration changes.

## This host

- Taipei time comes from PowerShell `[TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), 'Taipei Standard Time')`; Git Bash ignores `TZ=Asia/Taipei` and still prints GMT.

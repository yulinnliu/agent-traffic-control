# AGENTS.md

Bootstrap and safety guidance for this repository: the governance and routing
layer above the independent project repositories under `projects/`. Directory
layout lives in `docs/LAYOUT.md`.

## Governance bootloader

Resolve governance in this order: exact source/revision from task authority (a task record here binds this repository's `main` at task start; a SHA the Owner names counts as one); otherwise approved project/task pin; otherwise the `governance_source` recorded in `workspace.yaml`, at its latest release tag on `main` resolved once and frozen as its SHA; otherwise stop.

Then read `shared/governance/INDEX.md` and `CORE.md` from that exact same revision, followed only by routed modules. A project repository reads its own vendored copy under `.atc/governance/` instead; this checkout's location is never part of governance identity.

## Where things live

- Normative governance lives only under `shared/governance/`. Changing it routes through `GOVERNANCE_CHANGES.md`.
- Policy shapes live in `shared/policies/schemas/`; `shared/policies/examples/` are illustrations, never a project's real policy.
- A shared skill's single source is `shared/skills/<name>/SKILL.md` plus optional per-tool overlays in `adapters/<tool>.md`; `.claude/`, `.agents/` and `.dsh/` hold managed copies rendered from them by `atc sync --root`, never edited by hand. A canonical skill never names this checkout by path or environment variable.
- `CLAUDE.md` here is what sync copies verbatim into every registered project, so it stays the `@AGENTS.md` import with one sentence, which reads correctly from any repo. Guidance belongs in this file or `docs/`.
- `scripts/` holds POSIX sh entry points that need no runtime (install and check adapters); tool implementations live under `adapters/<language>/`; the contract they implement is `shared/conformance/`, not their own tests.

## Changing this repo

- Read by default; editing this repo requires explicit task authority.
- The installed `atc` runs this canonical checkout's code, so the checkout stays on `main` with no tracked modification: off `main` every `atc` command but `doctor` refuses, and `sync --root` refuses to render here.
- Authorized changes use a managed worktree under `worktrees/root/` from `atc worktree create --project root`; `atc integrate --task <ID>` fast-forwards `main` here.
- Integration, publication and deployment are separately governed actions.
- Never aim destructive project/worktree tooling at this checkout itself. Do not run `git clean -fdx` here or recursively delete `projects/`, `worktrees/`, or `state/` from above.
- Read back changes, then validate from the topic worktree's `adapters/python/`, entered in a subshell `( cd <worktree>/adapters/python && ... )` and through `uv run --locked atc` (bare `atc` runs this checkout's code): `sync --root` followed by `validate` after touching a shared skill or overlay, `policies check ../../shared/policies/examples` after touching a schema or example, and `uv run --locked pytest` (reads `shared/conformance/`) after touching a tool or fixture.

## Task records

- Task prefix: ATC

Records live in `.atc/tasks/`; `docs/TASKS.md` specifies them and `atc task`.
Tasks here are identified by record (governance `CORE.md` § Task identity).

## Integration verification

`atc integrate` runs this verifier in its candidate checkout (governance
`GIT_WORKFLOW.md` § Integration verifier source).

- Verifier argv file: adapters/python/scripts/integration-verify.json
- Verifier timeout: 1800
  (the test budget, governance `VERIFICATION.md` § Verifier test budget)

What the verifier checks, which ranges it treats as documentation-only and
where the full suite runs: `docs/TESTKIT.md` § The workspace root's verifier.
After an integrated range changes the dependencies of `adapters/python` or of
its `atc-testkit`, reinstall `atc` on each host as `adapters/python/README.md`
says.

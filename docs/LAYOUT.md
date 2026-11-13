# Layout

Operational description of the tree; governance semantics live under
`shared/governance/`.

## Tracked

- `shared/governance/` — canonical rules. `INDEX.md` routes; `CORE.md` is
  always read; the other modules load only when their condition applies.
  Each rule has one home here: `AGENTS.md` holds an entry pointer to it, and
  any other file that needs the rule carries a one-line pointer to its home.
- `shared/policies/schemas/` — JSON Schema for each policy file kind.
  `shared/policies/examples/` — generic illustrations of each shape. A
  project's real policy lives in that project's repository.
- `shared/skills/<name>/SKILL.md` — canonical skills. Frontmatter
  `adoption: baseline` marks the ones every registered project must carry;
  `invocation: user` and `contract: N` shape the rendered copies. Optional
  `adapters/<tool>.md` beside it holds constants only one tool needs. A skill
  states the steps of a procedure and cites the governance section that
  holds its rule, never restating the rule.
- `.claude/skills/`, `.agents/skills/`, `.dsh/skills/` — managed copies
  rendered from the canonical skills, for this repository's own sessions and
  as the files the shell installer copies into other repositories. Each
  carries the canonical body, `managed-by` and `canonical`; nothing in them
  refers back to this checkout.
- `shared/templates/PROJECT_AGENTS.md` — the pointer line onboarding inserts
  into a project's `AGENTS.md`, sending every session to `.atc/README.md`.
  `shared/templates/ATC_README.md` — the content sync writes to a project's
  `.atc/README.md`: the governance bootloader.
- `shared/conformance/<tool>/<case>/` — language-neutral fixtures: `input/`,
  `args.json`, `expected/`.
- `scripts/` — POSIX sh entry points with no language runtime:
  `link-skills.sh` installs or repairs a repository's adapters, `check-skills.sh`
  reports drift. Anything heavier belongs in a language adapter.
- `adapters/python/` — the Python reference implementation of the tools behind
  the `atc` command, with its own `pyproject.toml` and lock file, plus
  `atc-testkit/`, the pytest adapter of the verification contract. Other
  languages add a sibling directory and run the same fixtures.
- `workspace.yaml.example` — the shape of the machine-local map.
- `policies/hard-deny.yaml` — the workspace policy: what no agent on the
  machine may do, composed first into the PreToolUse hook's table
  (`docs/ENFORCEMENT.md` § PreToolUse hook).
- `.atc/tasks/<task_id>.yaml` — this repository's task records, under the
  `ATC` prefix its `AGENTS.md` declares (`docs/TASKS.md`). This repository is
  the governance source: it reads `shared/governance/` and binds its own
  `main`, so its `.atc/` holds `tasks/` alone, with no bootloader, lock or
  vendored governance. Validation applies the path rule of a consuming
  repository's `.atc/` to it with no sync-owned file expected.
- `docs/` — operational documentation; `docs/design/<task_id>.md` — a
  task's committed design; `docs/releases/v<version>.md` — the notes of the
  `atc` release `v<version>`, committed in its release commit and used
  verbatim as its GitHub Release body. `.out-of-scope/` — recorded
  rejections.

## Inside a consuming repository

```text
.atc/
├── README.md          sync: the governance bootloader, the entry every session reads first
├── atc.lock           sync: TOML version, source, revision
├── governance/        sync: shared/governance/ byte-equal at the locked revision
└── tasks/             the project's: its task records (docs/TASKS.md)
```

- `.atc/` is split by path. Sync writes `README.md`, `atc.lock` and
  `governance/` and nothing else; `tasks/` belongs to the project, and sync
  never reads or writes it. Validation reports any other file under `.atc/`,
  and sync treats one as a collision and writes nothing into that repository.
  Ownership under `.atc/` goes by path alone; `managed-by` marks only the
  rendered skill copies.
- `.atc/atc.lock` — `version` (the lock format, `1`), `source` (the governance
  repository, `owner/name`) and `revision` (a full ATC SHA), as TOML strings
  except `version`. The file is exactly these three lines in this order,
  UTF-8 with LF line ends and a final newline:

  ```toml
  version = 1
  source = "<owner>/<name>"
  revision = "<40-hex ATC SHA>"
  ```

  Readers drop carriage returns first, since a `core.autocrlf` checkout may
  hold the file with CRLF. A lock that does not parse, has a `version` other
  than `1`, or lacks a non-empty `source` or `revision` is a collision for
  sync and a failure for validation. `atc onboard` renders and locks the
  sync target: the highest `vMAJOR.MINOR.PATCH` tag in the history of ATC
  `main`, or the SHA on `main` the Owner names with `--revision` (governance
  `CORE.md` § Effective authority and revocation). It reads the tags of the
  ATC checkout and never fetches, so that checkout holds the release tags:
  the Owner creates each one there, and another host fetches tags first. Sync
  writes the lock with the target as `revision` when the lock is missing,
  when its `source` differs from the governance repository, when sync changes
  any file it writes into the repository, or when the recorded revision is
  not in the history of ATC `main`. When the locked revision is ahead of the
  latest release, sync writes nothing into that repository until a later
  release or a named `--revision`. `scripts/link-skills.sh` installs the
  checkout it runs from, at its `HEAD`; it reads the lock without parsing
  it, so it also rewrites a lock with an empty `revision`, and checks the
  recorded revision against that `HEAD`. On `main`, `atc validate
  --projects` checks that this revision is in `main`'s history and compares
  the synced files with that revision's tree. The files sync writes carry no
  format version of their own: they are current when they equal what the
  locked revision renders, and sync replaces them otherwise.
- `.atc/tasks/<task_id>.yaml` — the repository's own task records, declared by
  `## Task records` in its `AGENTS.md` (`docs/TASKS.md`).
- `.claude/skills/<name>/`, `.agents/skills/<name>/`, `.dsh/skills/<name>/` —
  the rendered skill copies. These directories also hold the project's own
  skills, so each rendered copy carries `managed-by`.
- One marked pointer line in `AGENTS.md`, placed before its first section;
  the rest of `AGENTS.md` is the project's. `CLAUDE.md`, when the repository
  had none, imports `AGENTS.md`. Sync also replaces a `CLAUDE.md` byte-equal
  to the root `CLAUDE.md` of the form before it imported `AGENTS.md`; any
  other existing `CLAUDE.md` is the project's.

All of them are ordinary tracked files; sync commits its own on `main`
(`GIT_WORKFLOW.md` § Governance sync commits), and people review the diff. A
per-topic worktree holds only committed files, so a session in any worktree
reads the same governance and skills. The repository needs no environment
variable and no other checkout. The lock advances to a revision that requires
`## Integration verification` in `AGENTS.md` only after that section and its
argv file exist (`GIT_WORKFLOW.md` § Lock advancement).

## Machine-local, never committed

- `workspace.yaml` — absolute paths for this machine; one copy per clone.
- `projects/<name>/` — independent repositories brought in by the owner. This
  repository never tracks their content.
- `worktrees/<name>/<owner>+<topic>/` — disposable per-topic worktrees whose
  identity is in git metadata.
- `worktrees/root/<owner>+<topic>/` — the same for this repository's own
  topics; `root` is therefore reserved and names no registered project.
- `state/<name>-data/` — a project's persistent, hard-to-regenerate data.
  Protected by the mutation policy in `AGENTS.md`, not by `.gitignore`.
- `shared/memory/INDEX.md` — cross-repository memory router naming real
  local paths.

## Three lifecycles

Repository, disposable worktrees and persistent state have independent
lifecycles and are never nested in one another. Deleting a worktree loses
nothing; deleting state may be unrecoverable; the repository is recoverable
from its remote.

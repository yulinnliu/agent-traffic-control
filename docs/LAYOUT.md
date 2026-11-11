# Layout

Operational description of the tree; governance semantics live under
`shared/governance/`.

## Tracked

- `shared/governance/` — canonical rules. `INDEX.md` routes; `CORE.md` is
  always read; the other modules load only when their condition applies.
- `shared/policies/schemas/` — JSON Schema for each policy file kind.
  `shared/policies/examples/` — generic illustrations of each shape. A
  project's real policy lives in that project's repository.
- `shared/skills/<name>/SKILL.md` — canonical skills. Frontmatter
  `adoption: baseline` marks the ones every registered project must carry;
  `invocation: user` and `contract: N` shape the rendered copies. Optional
  `adapters/<tool>.md` beside it holds constants only one tool needs.
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
- `.atc/tasks/<task_id>.yaml` — this repository's task records, under the
  `ATC` prefix its `AGENTS.md` declares (`docs/TASKS.md`).
- `docs/` — operational documentation. `.out-of-scope/` — recorded rejections.

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
  never reads or writes it. Validation reports any other file under `.atc/`.
- `.atc/atc.lock` — `version` (the lock format, `1`), `source` (the governance
  repository, `owner/name`) and `revision` (the full ATC SHA of the last sync
  that changed a file sync writes into the repository), as TOML strings
  except `version`. On `main`, `atc validate --projects` checks that this
  revision is in `main`'s history. The files sync writes carry no format
  version of their own: they are current when they equal what the locked
  revision renders, and sync replaces them otherwise.
- `.atc/tasks/<task_id>.yaml` — the repository's own task records, declared by
  `## Task records` in its `AGENTS.md` (`docs/TASKS.md`).
- `.claude/skills/<name>/`, `.agents/skills/<name>/`, `.dsh/skills/<name>/` —
  the rendered skill copies. These directories also hold the project's own
  skills, so each rendered copy carries `managed-by`.
- One marked pointer line in `AGENTS.md`, placed before its first section;
  the rest of `AGENTS.md` is the project's. `CLAUDE.md`, when the repository
  had none, imports `AGENTS.md`.

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

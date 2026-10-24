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
- `shared/templates/PROJECT_AGENTS.md` — the policy block onboarding inserts
  at the top of a project's `AGENTS.md`, between ownership markers. It points
  at the project's own `.atc/governance/`.
- `shared/conformance/<tool>/<case>/` — language-neutral fixtures: `input/`,
  `args.json`, `expected/`.
- `scripts/` — POSIX sh entry points with no language runtime:
  `link-skills.sh` installs or repairs a repository's adapters, `check-skills.sh`
  reports drift. Anything heavier belongs in a language adapter.
- `adapters/python/` — the Python reference implementation of the tools behind
  the `atc` command, with its own `pyproject.toml` and lock file, plus
  `hami-testkit/`, the pytest adapter of the verification contract. Other
  languages add a sibling directory and run the same fixtures.

## Inside a consuming repository

- `.claude/skills/<name>/`, `.agents/skills/<name>/`, `.dsh/skills/<name>/` —
  the rendered skill copies.
- `.atc/governance/*.md` — the vendored governance modules, byte-equal to
  `shared/governance/` at the pinned revision.
- `.atc/governance.yaml` — `managed-by`, `source` and `revision` of that copy.
- The policy block in `AGENTS.md` and, when the repository had none, `CLAUDE.md`.

All of them are ordinary tracked files; sync rewrites them, people review the
diff. The repository needs no environment variable and no other checkout.
- `workspace.yaml.example` — the shape of the machine-local map.
- `docs/` — operational documentation. `.out-of-scope/` — recorded rejections.

## Machine-local, never committed

- `workspace.yaml` — absolute paths for this machine; one copy per clone.
- `projects/<name>/` — independent repositories brought in by the owner. This
  repository never tracks their content.
- `worktrees/<name>/<owner>+<topic>/` — disposable per-topic worktrees whose
  identity is in git metadata.
- `state/<name>-data/` — a project's persistent, hard-to-regenerate data.
  Protected by the mutation policy in `AGENTS.md`, not by `.gitignore`.
- `shared/memory/INDEX.md` — cross-repository memory router naming real
  local paths.

## Three lifecycles

Repository, disposable worktrees and persistent state have independent
lifecycles and are never nested in one another. Deleting a worktree loses
nothing; deleting state may be unrecoverable; the repository is recoverable
from its remote.

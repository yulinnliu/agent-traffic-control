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
- `shared/templates/PROJECT_AGENTS.md` — the pointer line sync places
  in a project's `AGENTS.md`, sending every session to `.atc/README.md`.
  `shared/templates/ATC_README.md` — the content sync writes to a project's
  `.atc/README.md`: the governance bootloader.
  `shared/templates/pre-commit-config.yaml` — the lint stage's list a
  project copies to its `.pre-commit-config.yaml` to adopt the lint stage,
  and owns from then on (`docs/TESTKIT.md` § Lint stage); sync does not
  deliver it.
- `shared/lint/` — the shared lint rules sync keeps current in a project's
  `.atc/lint/` (`docs/TESTKIT.md` § Lint stage). This repository's own ruff
  and markdownlint settings extend them in place.
- `shared/conformance/<tool>/<case>/` — language-neutral fixtures: `input/`,
  `args.json`, `expected/`.
- `scripts/` — POSIX sh entry points with no language runtime:
  `link-skills.sh` installs or repairs a repository's adapters, `check-skills.sh`
  reports drift. Anything heavier belongs in a language adapter.
- `adapters/python/` — the Python reference implementation of the tools behind
  the `atc` command, with its own `pyproject.toml` and lock file, plus
  `atc-testkit/`, the pytest adapter of the verification contract. Other
  languages add a sibling directory and run the same fixtures.
- `atc-manifest.yaml` — what `atc sync` delivers from this revision and where
  (§ The manifest).
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
  verbatim as its GitHub Release body. What it says to projects lists only
  that version's changes and points to `docs/TESTKIT.md` § Upgrading a
  project's testkit for the steps. Before the Owner tags that commit, a
  dispatch of `.github/workflows/validate.yml` on `main` runs at it, and the
  tag goes on only after that run passes on Linux and Windows.
  `docs/development/` — maintainer documentation, for whoever changes `atc`
  and `atc-testkit` or runs this repository's own tools, kept off the docs
  site: `RELEASING.md`, how a release is cut; `TESTING.md`, how this
  repository verifies itself; `TESTKIT_INTERNALS.md`, how `atc-testkit` is
  built inside; `DOCS_SITE.md`, how the docs site is built and published.
  `.out-of-scope/` — recorded rejections.

## The manifest

`atc-manifest.yaml` at the root of a revision lists, per scope, each item
`atc sync` delivers: an `id`, its `source` in that revision, its `target` and
its `mode`. The fields and the modes are the tool's format; the manifest names
only paths, all relative and without `..`.

| Mode | Delivery |
| --- | --- |
| `owned` | written, and rewritten on drift; a directory item lists its `files` |
| `pointer-line` | the one marked line in the target `AGENTS.md` |
| `create-only` | written when the target is absent, never modified after |
| `skill-render` | a rendered copy of each canonical skill under the source, in `{harness}/skills/` |

- `manifest` is the format (`1`); `min_atc` is the oldest `atc` that renders
  the revision, and an older one stops with `MANIFEST_TOO_NEW`.
- A `project` item of mode `owned` targets a path under `.atc/` outside
  `.atc/tasks/`, so the path alone tells a project's tools what sync owns
  there; any other target is `MANIFEST_INVALID`.
- `project` items go into every registered project. `workspace` items go into
  the workspace directory when it is not the governance checkout: the
  workspace policy (`create-only`) and the workspace's own skill copies.
- A revision without a manifest is not a sync target: sync stops with
  `NO_MANIFEST`, also for a SHA the Owner names. A manifest that breaks the
  format stops with `MANIFEST_INVALID`, one naming a source the revision lacks
  with `MANIFEST_SOURCE_MISSING`.

Sync reads the revision's files from the **content repository**, never from a
working tree: the Git objects of the checkout `workspace.yaml` names as
`governance_checkout`, or else the bare cache
`~/.atc/content/<owner>/<repo>.git` of `governance_source`, cloned on first
use and fetched (`main` and tags) once per run. A SHA names the same tree in
either, so the SHA is the integrity check. A host without `governance_checkout`
syncs only to commits and tags pushed to the governance repository. A
workspace directory that is itself a checkout of the governance repository
(a Git top level holding `atc-manifest.yaml`) but whose `workspace.yaml` has
no `governance_checkout` never falls back to the cache: sync and `atc add`
stop, and doctor reports an `ERROR`, with `GOVERNANCE_CHECKOUT_UNSET` and the
fix `governance_checkout: .`. Doctor reads the cache as it is and fetches
nothing; a cache not yet fetched is a `WARN` (`CONTENT_CACHE_MISSING`).

The workspace directory's own skill copies are checked by one check that
`atc doctor` and `atc sync` share: the copies on disk against copies rendered
from the Git objects of the tip of `main` when the workspace directory is the
governance checkout, else from the sync target. A drift is an `ERROR` in
doctor; in the governance checkout `atc sync` stops with `ROOT_ADAPTER_DRIFT`,
elsewhere it renders the `workspace` items.

## Inside a consuming repository

```text
.atc/
├── README.md          sync: the governance bootloader, the entry every session reads first
├── atc.lock           sync: TOML version, source, revision
├── governance/        sync: shared/governance/ byte-equal at the locked revision
├── lint/              sync: shared/lint/, the lint stage's shared rules (docs/TESTKIT.md § Lint stage)
└── tasks/             the project's: its task records (docs/TASKS.md)
```

- `.atc/` is split by path. Sync writes `atc.lock` and the manifest's
  `owned` targets, all of which lie under `.atc/` outside `tasks/`
  (§ The manifest); `tasks/` belongs to the project, and sync never reads or
  writes it. Validation reports any other file under `.atc/`, and sync
  treats one as a collision and writes nothing into that repository.
  Ownership under `.atc/` goes by path alone; `managed-by` marks only the
  rendered skill copies. These two records are what a project's testkit
  reads to tell the files ATC manages (`docs/TESTKIT.md` § ATC-managed
  paths).
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
  sync and a failure for validation. `atc sync` renders and locks the
  sync target: the SHA on `main` the Owner names with `--revision`
  (governance `CORE.md` § Effective authority and revocation); else, when
  `atc` records the commit it was installed from (`direct_url.json`), that
  installed release's commit, which
  must be in the history of ATC `main` (`STOP RELEASE_NOT_ON_MAIN`), so a
  project never moves ahead of the tool; else the highest
  `vMAJOR.MINOR.PATCH` tag in the history of ATC `main`. `main`, its history
  and the tags are those of the content repository (§ The manifest). Sync
  writes the lock with the target as `revision` whenever the recorded
  revision differs from the target, even when every file it writes is
  current, and when the lock is missing or its `source` differs from the
  governance repository; such a sync commit holds the lock alone. When the
  locked revision is ahead of the target release, sync writes nothing into
  that repository until a later release or a named `--revision`. `scripts/link-skills.sh` installs the
  checkout it runs from, at its `HEAD`; it reads the lock without parsing
  it, so it also rewrites a lock with an empty `revision`, and checks the
  recorded revision against that `HEAD`. `atc sync --check` checks that this
  revision is in the history of the content repository's `main` and compares
  the synced files with that revision's tree; a lock at a revision without a
  manifest is a problem until the next sync. The files sync writes carry no
  format version of their own: they are current when they equal what the
  locked revision renders, and sync replaces them otherwise.
- `.atc/tasks/<task_id>.yaml` — the repository's own task records, declared by
  `## Task records` in its `AGENTS.md` (`docs/TASKS.md`).
- `.claude/skills/<name>/`, `.agents/skills/<name>/`, `.dsh/skills/<name>/` —
  the rendered skill copies: every baseline skill, and every opt-in skill the
  project adopted, as a complete copy (the canonical body plus that tool's
  overlay) in each harness directory the project's `skill_harnesses` selects. DSH
  also reads `.agents/skills/` and prefers `.dsh/skills/`. Sync deletes a
  managed copy whose canonical skill is gone and every managed copy in a harness
  directory the project no longer selects. These directories also hold the
  project's own skills, so each rendered copy carries `managed-by`.
- One marked pointer line in `AGENTS.md`, placed before its first section, or
  at its end when it has none. A repository without `AGENTS.md` gets a minimal
  one (a title, the pointer line, one line saying the project's rules go
  there); after that the rest of `AGENTS.md` is the project's. `CLAUDE.md`, when the repository
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
  Its optional top-level `skill_harnesses` maps a project name to the harnesses whose
  skill directories it gets copies in: `claude` (`.claude/skills/`), `codex`
  (`.agents/skills/`), `dsh` (`.dsh/skills/`), or `all`. A project it does
  not name, and this checkout, get `all`: every harness the running `atc`
  supports, so a harness supported in a later release joins `all` there. The
  map stays top-level; a `projects.<name>` entry holds exactly `repo`,
  `worktrees` and `state`. Its optional `governance_checkout` is a path,
  relative to the workspace directory, of a checkout of `governance_source`:
  the content repository (§ The manifest) and the repository the reserved
  project `root` names. Only a host that develops ATC sets it; `.` makes the
  workspace directory that checkout. The canonical checkout guard applies to
  that checkout, and to a workspace directory that is a Git top level holding
  `atc-manifest.yaml` without the line (`shared/conformance/CLI.md`
  § Canonical checkout guard). A
  workspace directory is any directory holding `workspace.yaml`.
- `projects/<name>/` — independent repositories brought in by the owner. This
  repository never tracks their content.
- `worktrees/<name>/<harness>+<ID>/` — disposable per-topic worktrees whose
  identity is in git metadata.
- `worktrees/root/<harness>+<ID>/` — the same for this repository's own
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

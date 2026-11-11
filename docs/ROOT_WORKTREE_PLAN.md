# Root worktree plan

How and in what order topics of this repository move into linked worktrees,
so the canonical checkout always stays on `main`. The installed `atc` is an
editable install of `adapters/python`, so the canonical checkout's content is
the code every session runs.

## Target state

### 1. Where a root topic lives

- Directory `worktrees/root/<owner>+<topic>/`, branch `worktree-<owner>+<topic>`,
  lock reason `<owner>:<topic>:<session>`: managed worktree contract 1, the
  same as for a registered project.
- `root` is reserved: `validate_workspace.load_and_check_workspace_yaml`
  refuses a project named `root` or a project whose `worktrees` is
  `worktrees/root`, so `atc validate` and every command that loads the map
  report it; `atc bootstrap add --project root` refuses before writing.
- The canonical checkout stays on `main` with no tracked modification. It is
  fast-forwarded by `atc integrate` and serves the installed `atc`; it
  never checks out a topic branch and never renders managed copies.

### 2. `atc worktree` for the root

- `create`, `resume`, `audit` and `cleanup` take `--project root`, the name
  `root` being reserved for the workspace root.
- The root is a `Project` built in `run` from `--project root` or from inference, never
  an entry of `ws.projects`: repo `<root>`, worktrees `<root>/worktrees/root`,
  state `None`. `check_target_path` keeps iterating `ws.projects` for protected
  paths, so the root's missing state never enters it.
- Inference: a working directory under `<root>/worktrees/root/` infers the
  root, in `atc worktree` and in `atc integrate` (which then targets the
  canonical checkout as `--project root` does). Any other directory of the
  canonical checkout infers nothing and needs `--project root`.
- `external_roots` always adds `<root>/.claude/worktrees`, independent of
  `ws.projects`.
- A root worktree holds the committed tree only, as the integration verifier's
  candidate does: no `workspace.yaml`, `projects/` or `state/`. The
  `worktree_cd_guard` hook already denies a top-level `cd` into it, so commands
  run there through a subshell `( cd <worktree>/adapters/python && ... )` or
  the tool's own working-directory option.

### 3. Canonical checkout guard

- The canonical checkout is the checkout holding `atc_cli` when its
  `--git-dir` equals its `--git-common-dir` and it holds `workspace.yaml`.
  CI checkouts and integration candidates hold no `workspace.yaml`, and linked
  worktrees have a separate git dir, so neither is the canonical checkout.
- Off `main`: when the canonical checkout's HEAD is not `refs/heads/main`,
  every command, the testkit commands included, except `doctor` and `--help`,
  prints `ERROR CHECKOUT_NOT_MAIN <checkout> <branch>` and exits 2, because
  every command would run that branch's code.
- Unreadable: a checkout holding `workspace.yaml` and `.git` whose Git state
  cannot be read (for example a `safe.directory` refusal) refuses the same way
  with `ERROR CHECKOUT_UNREADABLE`; the guard's Git calls drop inherited
  repository variables such as `GIT_DIR`.
- Dirty: a tracked modification (`git --no-optional-locks status --porcelain
  --untracked-files=no`) prints one `WARNING CHECKOUT_DIRTY <checkout>` line on
  stderr and the command runs. `atc integrate`'s own `merge --ff-only` touches
  tracked files for a moment, so refusing on dirt would refuse concurrent
  commands transiently and block every project for a local slip.
- `atc onboard --sync --root-only` refuses with `ERROR CHECKOUT_CANONICAL`
  when the checkout it renders is the canonical checkout; managed copies are
  rendered and committed in the topic worktree.
- `doctor` reports both conditions as problems.
- `atc_cli.main` takes the checkout as an injectable parameter, so tests and
  fixture roots choose it explicitly.

### 4. Documents

- Root `AGENTS.md` § Changing this repo: an authorized change uses a worktree
  created by `atc worktree create --project root`; the canonical checkout always
  stays on `main`; rendering and validation run in the topic worktree's
  `adapters/python/` through `uv run --locked atc` (bare `atc` runs the
  canonical checkout's code), from a subshell.
- `docs/LAYOUT.md` § Machine-local: a `worktrees/root/<owner>+<topic>/` line and
  the reserved name `root`.
- `shared/skills/atc-worktree/SKILL.md`: the description (line 3), the
  inference rule (§ near line 59) and the closing note (line 183) describe
  `--project root` and the root's inference; overlays and the three managed copies
  follow through sync.
- Docstrings of `managed_worktree.py` and `atc_cli`, `adapters/python/README.md`.

`shared/governance/` already describes rendering in a linked worktree and
managed worktrees integrated from the integration checkout's `main`
(`GIT_WORKFLOW.md`); this topic leaves governance unchanged.

## Order

1. `validate_workspace.py` and `bootstrap.py`: the reserved name `root` and the
   `worktrees/root` collision; tests.
2. `managed_worktree.py`: `--repo`, the root `Project` built in `run`, the
   inference rule, the external root; `git_integrate.py`: root inference from
   `worktrees/root/`. Tests cover create, resume, audit and cleanup with
   `--repo`, the `--repo` refusals, inference from `worktrees/root/` for both
   commands, and no inference elsewhere in the canonical checkout.
3. `atc_cli`: the guard with the injectable checkout, the dirty warning, the
   `onboard --root-only` refusal, the `doctor` checks. Tests on fixture
   repositories cover off-main refusal, dirty warning, a linked worktree and a
   checkout without `workspace.yaml` exempt, and `doctor` running; the existing
   dispatcher tests (`test_atc_cli.py` `call` and the missing-dependency test,
   `test_git_integrate.py` `atc_cli.main` calls) pass a non-canonical checkout.
4. Documents (§ 4), then `uv run --locked atc onboard --sync --root-only` and
   `uv run --locked atc validate` in the topic worktree.
5. Verification in the topic worktree: `uv run --locked pytest`,
   `validate_workspace.py`, `policies.py check`.
6. Review by a fresh reviewer; integration with `atc integrate --repo` on the
   Owner's approval; then `atc worktree audit --repo <root>` once from the
   canonical checkout.

## This topic

The tool cannot yet create a root worktree, so this topic was opened by hand
with `git worktree add` at `worktrees/root/claude+root-worktree`. Before the
first commit its branch is renamed to `worktree-claude+root-worktree` and the
worktree locked with reason `claude:root-worktree:-`, so it already matches
contract 1 and `atc worktree clean --project root` removes it after integration, on
the Owner's word.

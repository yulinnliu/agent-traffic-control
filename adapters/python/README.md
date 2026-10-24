# Python reference implementation

One adapter among several: the contracts these tools implement live under
`shared/` and are defined by their schemas and the conformance fixtures, not
by this code. Python 3.12 or newer, provisioned by `uv` from the
`pyproject.toml` and `uv.lock` here. Run everything from this directory.

One command fronts the scripts: `uv run atc <command>` here, or
`uv tool install -e .` once per machine to have `atc` on PATH, which is how
the skills call it. `atc` resolves the checkout from `--root`, `ATC_ROOT`,
the nearest `workspace.yaml` above the working directory, or its own install
location, in that order.

| `atc` command | Script | Does |
| --- | --- | --- |
| `bootstrap` | `bootstrap.py` | `init`, `add NAME [--repo URL] [--skills a,b]`, `sync [NAME] [--prune]`, `status`: the quickstart path over the tools below |
| `validate` | `validate_workspace.py` | canonical skills versus rendered adapters, root contract, `--projects` for registered checkouts (adapters, policy block, vendored governance), `--root DIR --adapters-only` for a fixture |
| `onboard` | `onboard_project.py` | `--check`, `--apply`, `--sync [--prune]` a project's managed files; `--root-only` re-renders this checkout's adapters; never touches project-owned files |
| `doctor` | `doctor.py` | local readiness: `READY`, `NOT_INITIALIZED`, `ERROR` |
| `policies` | `policies.py` | `check DIR`: schema-validate a policy directory and refuse contradictions |
| `enforce` | `generate_enforcement.py` | `--policies DIR --out DIR [--check]`: harness deny configs from hard deny, see `docs/ENFORCEMENT.md` |
| `classify` | `classify_change.py` | `--policy escalation.yaml --changed FILE [--severity]`: `LOW` or `HIGH` |
| `preflight` | `git_preflight.py` | `merge` verdict `FF` / `NO_FF` / `STOP`; `cleanup` verdict `REMOVABLE`; reads only |
| `worktree` | `hami_worktree.py` | `create`, `resume`, `audit`, `cleanup` per-topic worktrees with identity in git |
| `friction` | `friction.py` | append incident and resolution events; summaries |
| | `check_workspace.py` | runs the validator and records a failure as friction evidence |
| | `build_testkit_release.py` | builds the `hami-testkit` wheel and digest record for consuming projects |

`src/atc_cli/` is the dispatcher; the scripts stay runnable directly with
`uv run python scripts/<name>.py`.

`hami-testkit/` is the pytest adapter of the verification contract: marker
classification, change-impact selection and the installed-artifact check a
consuming project runs in CI.

```powershell
uv run --locked pytest                                  # suite, including shared/conformance
uv run --locked pytest tests/test_tooling.py -k name    # one test
uv run atc validate                                     # this checkout's contract
uv run atc onboard --sync --root-only                   # re-render .claude/ .agents/ .dsh/ after a canonical skill change
pwsh -File check.ps1 -Projects -Tests                   # Windows one-shot with caches under ../../.tooling
```

Tests build throwaway repositories under `tests/.tmp-pytest/` and never
touch `projects/`, `worktrees/` or `state/`.

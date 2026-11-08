# atc CLI implementation plan

How and in what order the `atc` command line becomes what
`shared/governance/GIT_WORKFLOW.md`, `TESTKIT.md` and `ATC_HOME.md` say, with
fewer values typed by hand and a refusal that names its next step. The
governing documents hold every rule; this plan cites them by section and adds
none. Where a rule does not exist yet, step 1 writes it before any tool
changes.

Every step is one topic branch of this repository, reviewed by the
`hami-review-relay` method and integrated with `atc integrate` before the
next step that depends on it. The old command forms stay valid throughout: a
PASS block written before a step runs after it.

## Order

```text
1 governance change ──> 2 integrate reads its declarations ──> 3 integrate infers its target ──> 4 command surface ──> 5 skill text
```

Steps 2 and 3 touch `adapters/python/scripts/` and none of the testkit's
adoption. Step 4 changes `atc-testkit`. Step 5 waits for step 3 to be on
`main`, because a shorter PASS block runs only on a checkout that has it.

## Rulings

Decided by the Owner before this plan was written; the steps carry them out.

- `--reviewed` and the record part of `--authority-reference` are typed by the
  caller and never inferred: the first is the review's freeze point, the
  second is the caller's statement of authority.
- The authority reference is recorded whether or not the verifier writes a
  report, in a file of its own apart from verification evidence.
- A PASS block names no `--branch`; the tool finds the branch from the
  reviewed SHA.
- An explicit verifier argv file or timeout that differs from the repository's
  declaration is a warning, not a refusal.
- `atc tmp clean --project` is renamed, since its value is the tree's own
  name and not a `workspace.yaml` key.

## 1. Governance change

One change under `GOVERNANCE_CHANGES.md` to `shared/governance/GIT_WORKFLOW.md`,
synced into each project's `.atc/governance/` copy by the usual route.

1. § Integration verifier source: the verifier argv file and timeout come from
   the `main` revision's `AGENTS.md` verification section; an explicit value
   is allowed, and one that differs from the declaration is reported as a
   warning. A repository without the declaration stops the integration.
2. § Authority reference: the ` @ <sha>` part is the reviewed SHA, appended by
   the tool when absent and required to equal `--reviewed` when present; the
   form that names a plan section puts the SHA at the end so one rule covers
   every form.
3. § Integrate: the adapter records every integration (candidate SHA,
   reviewed SHA, authority reference, time) in `<git common dir>/atc/integrations.jsonl`,
   separate from the verification evidence of § Test evidence; the pre-push
   gate reads only the latter.
4. A new section naming the exit codes and the first-line form of every
   refusal: `STOP <TOKEN>: <detail>` with a `next:` line, `ERROR <TOKEN> <detail>`,
   and the testkit's `<TOKEN> <detail>`; verifier failure and preflight stops
   get distinct exit codes.

## 2. `atc integrate` reads its declarations

Topic in `adapters/python/scripts/` (`git_integrate.py`, `git_preflight.py`)
and the tests beside them.

1. The parsing of the verification section leaves `validate_workspace.py`
   for a module both it and `git_integrate.py` import.
2. `--verify-command-file` and `--verify-timeout` become optional; the
   defaults are the `main` revision's declaration read with `git show`, and
   an explicit value that differs prints a warning. A relative argv file is
   resolved under the repository, as the relay skill already states.
3. The authority SHA rule of step 1 item 2, with refusal
   `AUTHORITY_SHA_MISMATCH`; a record path that `--reviewed` does not contain
   prints a warning.
4. The integration record of step 1 item 3.
5. Every stop carries a `next:` line from the integration table of
   `GIT_WORKFLOW.md` § Integrate; `CHECKOUT_NOT_ON_MAIN` names the branch in
   its first line and `CHECKOUT_DIRTY` lists the paths.
6. Before the candidate exists: the verifier's `argv[0]` is looked up on the
   path, and a tool checkout (the editable install's source) that is not on
   `main` or holds uncommitted changes prints a warning naming its branch and
   SHA.
7. At the start of verification one line names the candidate SHA, the timeout
   and its source; a verifier failure keeps the tail of both streams.
8. The first-line form and exit codes of step 1 item 4; `--json` writes a
   parseable result for a refusal before the candidate as well.

## 3. `atc integrate` infers its target

Same files, plus `hami_worktree.py` for the shared project lookup.

1. `--project <name>` resolves through `workspace.yaml` and is exclusive
   with `--repo`; with neither, the project is the one whose repository or
   worktree holds the current directory, as `atc worktree` infers it. The
   workspace root stays `--repo`.
2. `--branch` becomes optional. From `--reviewed`, in this order: an ancestor
   of `main` is `ALREADY_MERGED`; exactly one local branch at the SHA is the
   branch; several stop with `BRANCH_AMBIGUOUS`; none, with a branch that
   contains the SHA, is `UNREVIEWED_COMMITS`; none at all stops with
   `NO_BRANCH_AT_REVIEWED`.
3. `--check`: every inference, the preflight and the warnings of step 2, with
   no slot taken, no candidate and no change to `main`.
4. The resolved values print as one line on stderr before anything runs.
5. `atc --root` is honoured or refused, never ignored.
6. An uppercase reviewed SHA is lowered; a short one stays refused.

## 4. Command surface

Topic in `adapters/python/src/atc_cli/` and `adapters/python/atc-testkit/`;
it changes `atc-testkit` and takes the version the Owner sets.

1. `--help` of every subcommand names the subcommand, exits 0, never shows
   `python -m`, and writes non-ASCII with `backslashreplace`; `atc test
   mutants` documents its positional parameters.
2. `atc test mutants` verifies `<sha>` with `rev-parse --verify` before the
   first mutant; a missing dependency of a subcommand prints the reinstall
   command of the root's `AGENTS.md` instead of a traceback.
3. `atc tmp clean --project` takes its new name; the old one is accepted for
   one version and documented as such in `ATC_HOME.md` § Deleting.
4. `--reviewed` is the one name for the reviewed SHA across `integrate`,
   `preflight` and `worktree cleanup`; option abbreviation is off in every
   parser.
5. An option renamed in this section changes, in the same topic, every skill
   and doc that teaches it.

## 5. Skill text

`shared/skills/hami-review-relay/SKILL.md` and its rendered copies, by a
skill change with its own review (`hami-writing-for-agents`), then
`atc onboard --sync` into every project: the PASS block becomes
`atc integrate --project <name> --reviewed <sha> --authority-reference <record>`,
the verdict step names `atc integrate --check`, and the reviewer no longer
copies the timeout from `AGENTS.md`.

## Unverified items

| Item | Settled in | How |
| --- | --- | --- |
| Scripts in the workspace that branch on `atc integrate`'s exit code 1 | step 2 | search every project's scripts and skills before the exit codes split |
| The `main` revision's `AGENTS.md` of every registered project parses with the shared module | step 2 | `atc validate --projects` after the module moves |
| Which `atc` a cloud writer host runs the new forms with | step 5 | the cloud writer host item of `TESTKIT.md` § Commands |

# Releasing atc

How a maintainer cuts a release of `atc` and `atc-testkit` from this
repository. A release is a task of this repository like any other (root
`AGENTS.md` § Changing this repo): it is opened, written, reviewed and
integrated, and the Owner tags and installs it afterwards. The user-facing
side, installing a release on a host, is `docs/WORKFLOW.md` § Installing a
release.

`atc-maintain release` holds the mechanical part: `prepare` writes the
version pins and the notes' scaffold, and `check` holds them consistent in the
lint stage. The judgment the notes need is in § Release notes.

## Prepare

The Owner names the versions when the release task opens. In the task's
worktree, from `adapters/python`:

```text
uv run --locked atc-maintain release prepare <atc version> [--testkit <version>] [--min-atc <version>]
```

It changes the working tree and commits nothing:

- **Version pins.** Every place a version is written takes the new one:
  `adapters/python/pyproject.toml` and the `agent-traffic-control` entry of
  `uv.lock`, the Status sections of `README.md` and `README.zh-TW.md`
  (`atc`), and `atc_testkit/__init__.py` and the two pins in the testkit's
  `README.md` (`atc-testkit`). Without `--testkit` the testkit keeps its
  version. `min_atc` in `atc-manifest.yaml` changes only with `--min-atc`.
- **Tasks.** It lists the tasks integrated since the last tag, the highest
  `vX.Y.Z` tag reachable from `HEAD`, from the integration ledger
  (`<git common dir>/atc/integrations.jsonl`), leaving out records-only lines.
  `check` takes the list from the tree, as § Check states, and a `NOTE` names
  each task only one of the two holds: the release's own task, not
  integrated yet, is one, and so is a task integrated on another host.
- **Notes.** It writes `docs/releases/v<atc version>.md` with every required
  section, the task list in place and `TODO(release)` where a sentence of
  judgment goes. It never overwrites notes that exist.

It refuses a version that is not above the last tag, a `min_atc` above the
new version and notes that exist, and then writes nothing.

## Check

```text
uv run --locked atc-maintain release check
```

The lint stage runs it on every commit (`.pre-commit-config.yaml`, hook
`release`), so the commit hook, `atc test ID`, the integration verifier and
CI all hold to it. It prints one line per problem, `RELEASE_<KIND> <detail>`,
and exits 1 on any.

Always:

- every pin of a package names one version (`RELEASE_VERSION_MISMATCH`), the
  testkit's `pyproject.toml` takes its version from `atc_testkit.__version__`,
  and the notes' first line is `atc X (atc-testkit Y)`;
- `min_atc` is not above the `atc` version (`RELEASE_MIN_ATC`);
- `docs/releases/v<atc version>.md` exists (`RELEASE_NOTES_MISSING`).

While the version has no tag, which is from `prepare` until the Owner tags:

- the version is above the last tag (`RELEASE_NOT_AHEAD`);
- the notes hold `## Breaking`, `## atc-testkit Y` when the testkit version
  differs from the last tag's, `## Tasks`, `## Upgrade` with `### Projects`
  linking to `docs/TESTKIT.md` § Upgrading a project's testkit, and
  `## Assets` (`RELEASE_SECTION_MISSING`);
- no `TODO(release)` is left (`RELEASE_PLACEHOLDER`), so the scaffold cannot
  be committed until it is written;
- `## Tasks` lists, as `- <ID>: …` lines, exactly the tasks whose record
  `.atc/tasks/<ID>.yaml` the index adds since the last tag
  (`RELEASE_TASK_MISSING`, `RELEASE_TASK_EXTRA`). A task's record reaches
  `main` with its integration, so these are the tasks integrated since the
  tag, the release task among them. The tree gives the same answer on every
  host and in CI, which have no ledger.

A task integrated after the release task and before the tag fails that check
until the notes list it: the release task updates its notes, or the task
waits for the next release.

A tree without a release tag in its history, such as the export of
`atc test ID --lint --sha`, checks what needs no tag and prints
`NOTE no release tag in this history`.

## Release notes

`docs/releases/v<version>.md` is the GitHub Release body, read by the Owner
installing it and by each project's maintainer. It says what changed for
them, not how it was built.

- **Summary.** One paragraph under the first line: what this release does,
  in the terms a user meets.
- **Breaking.** Complete: every removed or renamed command, flag, output
  token, exit code, record field, skill contract and governance rule, each
  with its replacement and its task. Removed names keep no alias, so a reader
  who matched an old spelling finds it here.
- **atc-testkit Y.** A project keeps the testkit wheel it vendors until it
  upgrades. One table row per testkit change: where it runs (the installed
  `atc` or the project's vendored testkit) and whether a project upgrade is
  required or skippable, with what a project that skips it loses.
- **`min_atc`.** The lowest `atc` that reads what this release writes, such
  as the report and record formats of the new testkit's verifier. The notes
  state why that version, in one sentence.
- **First-run cost.** A new tool or hook environment the first run on a host
  builds: what it downloads or builds, and the time and space recorded in
  `docs/TESTKIT.md`.
- **Tasks.** One line per task, what it changed for a user.
- **Upgrade.** The Owner's steps on each host, in order, one command per
  line. Step 1 is the install precondition: no `atc test`, verifier or other
  `atc` command runs, and `atc doctor` reports `atc environment` READY.
  While a process runs from the installed environment, `uv tool install
  --force` fails to replace it with os error 5 or `WinError 32` and can leave
  an `atc.exe` that cannot start, which fails every session's PreToolUse hook
  closed until a rerun succeeds.
- **Projects.** Only this version's delta for projects, and a link to
  `docs/TESTKIT.md` § Upgrading a project's testkit for the steps.

## After integration

The Owner's steps, from their own terminal, once the release task is
integrated:

1. Push `main`, then dispatch `.github/workflows/validate.yml` on it and
   wait for the run to pass on Linux and Windows (`docs/LAYOUT.md`):

   ```text
   atc push root
   ```

2. Tag the release commit and push the tag. The tag's run of `validate.yml`
   refuses a tag that is not on `origin/main`, builds the assets and drafts
   the GitHub Release with the notes as its body:

   ```text
   git tag -a v<version> -m "atc <version>"
   git push origin v<version>
   ```

3. Install on each host as the notes' § Upgrade states, after `atc doctor`
   reports `atc environment` READY (`docs/WORKFLOW.md` § Installing a
   release).
4. Re-sync and check:

   ```text
   atc sync --check
   atc sync
   atc doctor
   ```

5. Publish the drafted GitHub Release.

Each project follows the release through its own task: `atc sync <project>`
moves its pin, and upgrading its vendored testkit is
`docs/TESTKIT.md` § Upgrading a project's testkit.

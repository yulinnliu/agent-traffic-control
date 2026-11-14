# Briefs

Two skeletons, one per session the coordinator starts; `atc-review` points
here. Scope and authority come from the task record, rules from the
project's `AGENTS.md` and the skills, so a brief restates neither; the
fixed-text fields below, with the lines the coordinator's tool adapter adds
to them, are the only rules a brief carries. A brief is ready to send once
every field holds its real value. The line under each field says what it
holds.

## Writer brief

```text
Task: <task ID>
  The task record's ID; its scope and authority are the writer's contract.
Worktree: <worktree path>
  The absolute path of the managed worktree, or the checkout the project's
  AGENTS.md prescribes.
Plan: <plan section>
  The spec or plan section the task implements, by path and section.
Rulings: <the Owner's special rulings>
  Present only when the Owner gave any.
Integration: none. The Owner runs it after the reviewer's PASS; your work
  ends at the PASS unless the coordinator sends a rebase or the verdict
  sends work back (atc-review "Writer: after a PASS").
  Fixed text; it replaces any "do not integrate".
Read first: <the files to read before starting>
  The worktree's AGENTS.md, the atc-review skill, and any skill the task's
  writing needs.
Work rules:
  Fixed text, one line each, in this order:
  - Reply in the Owner's language, role terms in English.
  - Run the tests the task's level names: `atc test <ID>` for the related
    tests, `-- <files>` for an explicit list (atc-review "Writer: the step
    report"); never the full suite: the reviewer's `atc test <ID> --candidate`
    is the one full run.
  - Amend before review; after review, new commits, except after a FAIL
    verdict (governance GIT_WORKFLOW.md § After formal review).
  - No background shells, no polling (governance DELIVERY.md § Background
    work).
  - No version bump unless shared/conformance/CLI.md requires it.
  - Commands for the Owner are plain `atc ...` lines, one per command, with
    no environment prefix (governance GIT_WORKFLOW.md § Owner-run commands).
  - Finish as atc-review "Writer: the step report" step 5: one line, the
    report's path and the tip's full 40-character SHA.
Hard rules:
  Fixed text, the Owner's: no change to authorization files,
  system or security settings; no `atc integrate` except `--check`; no push;
  no deletion of a worktree, scratch or branch.
```

## Reviewer brief

```text
Task: <task ID>
  The task record's ID.
SHA: <full tip SHA>
  The exact tip SHA the step report names.
Report: <report path>
  The step report's path, relayed verbatim.
Verdict: ~/.atc/reports/<task ID>-<short SHA>-review.md
  The reviewer, subagent or session, writes this file itself, its first
  line the verdict token; its reply's first line is the conclusion plus
  this path.
```

# Briefs

Two skeletons, one per session the coordinator starts; `atc-review` points
here. Scope and authority come from the task record, rules from the
project's `AGENTS.md` and the skills, so a brief restates neither; the
fixed-text fields below are the only rules a brief carries. Each field is
filled with its real value before the brief is sent; a brief with a field
still open is not ready. The line under each field says what it holds.

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
  Only when the Owner gave any; otherwise the field is left out.
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
  - Run the targeted tests by an explicit file list, chosen with the
    project's selection tool; never the full suite: the reviewer's
    `atc verify` is the one full run.
  - Amend before review; after review, new commits, except after a FAIL
    verdict (governance GIT_WORKFLOW.md § After formal review).
  - No background shells, no polling (governance DELIVERY.md § Background
    work).
  - No version bump.
  - Finish as atc-review "Writer: the step report" step 5: one line, the
    report's path and the tip's full 40-character SHA.
Hard rules:
  Fixed text, the Owner's: no change to authorization files,
  system or security settings; no `atc integrate` except `--check`; no push;
  no deletion of a worktree, scratch or branch. Commands for the Owner
  follow governance GIT_WORKFLOW.md § Owner-run commands, with no
  environment prefix.
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
  The file the reviewer writes itself, subagent or session; its reply's
  first line is the conclusion plus this path.
```

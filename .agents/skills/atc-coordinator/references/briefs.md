# Briefs

Two skeletons, one per session the coordinator starts; `atc-review` points
here. Scope and authority come from the task record, rules from the
project's `AGENTS.md` and the skills, so a brief restates neither. Each field
is filled with its real value before the brief is sent; a brief with a field
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
```

## Reviewer brief

```text
Task: <task ID>
  The task record's ID.
SHA: <full tip SHA>
  The exact tip SHA the step report names.
Report: <report path>
  The step report's path, relayed verbatim.
```

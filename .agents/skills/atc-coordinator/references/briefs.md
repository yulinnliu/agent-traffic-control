# Briefs

Two skeletons, one per session the coordinator starts. Each field is
filled with its real value before the brief is sent; a brief with a field
still open is not ready. The line under each field says what it holds.

## Writer brief

```text
Project: <project path>
  The absolute path of the repository the task changes.
Checkout: <checkout rule>
  The checkout rule the project's AGENTS.md states; for a registered
  project, a managed worktree through the atc-worktree skill, with its
  owner and topic.
Task record: <task record>
  The path of the task record, whose scope and authority basis are the
  writer's contract.
In scope: <spec or plan sections>
  The spec or plan sections the task implements, each by path and section.
Done: <what done looks like>
  The checks that pass and the state the topic branch is left in.
Reviewer: <reviewer session name>
  The exact name of the reviewer session.
Report: send the step report, as atc-review "Writer: the step report" says,
  whole to the reviewer, and a one-line note to the coordinator.
  Fixed routing; no value to fill.
```

## Reviewer brief

```text
Project: <project path>
  The absolute path of the repository under review.
Reviewed tip: <full tip SHA>
  The exact 40-character tip SHA the step report names.
Base: <full base SHA>
  The exact 40-character base SHA the step report names.
Task record: <task record>
  The path of the task record.
Writer: <writer session name>
  The exact name of the writer session.
Review: review as atc-review says, and send the verdict whole to the
  coordinator, and to the writer when the project's process has the
  writer act on it.
  Fixed routing; name which of the two receives the verdict.
```

## Codex constants

- **harness:** `codex`, which the CLI reads from the `CODEX_*` variables.
- Set each command's `workdir` to the worktree path the CLI prints.
- Git metadata writes and ordinary file writes are separate sandbox
  permissions. On `PERMISSION_DENIED`, request escalation for that one
  command and retry it once.
- Do not use the app's own managed worktrees for a task; they follow a
  different lifecycle and are never touched by this CLI.

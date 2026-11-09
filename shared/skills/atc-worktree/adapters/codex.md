## Codex constants

This tool's values for the two inputs you never choose yourself:

- **owner:** `codex`
- **session:** `--session "$CODEX_THREAD_ID"`; if it is unset, `$CODEX_SESSION_ID`.
  If both are set and differ, stop and ask the user. Pass it to `create` and `resume` only; `audit` and `cleanup` reject it.
- **contract:** pass `--contract 1` on every call.
- Set each command's `workdir` to the worktree path the CLI prints.
- Git metadata writes and ordinary file writes are separate sandbox
  permissions. On `PERMISSION_DENIED`, request escalation for that one
  command and retry it once.
- Do not use the app's own managed worktrees for a topic; they follow a
  different lifecycle and are never touched by this CLI.

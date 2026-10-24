## Claude Code constants

This tool's values for the two inputs you never choose yourself:

- **owner:** `claude`
- **session:** `--session "$CLAUDE_CODE_SESSION_ID"` on `create` and `resume` only; `audit` and `cleanup` reject it.
- **contract:** pass `--contract 1` on every call.
- The shell returns to the session root after every call, so use the
  absolute worktree path the CLI prints and `git -C <path>`.
- Do not use the native worktree tool to create a topic's worktree; its root
  inside the repo is a legacy location for this contract.

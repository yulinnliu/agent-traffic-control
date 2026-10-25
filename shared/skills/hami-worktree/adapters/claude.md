## Claude Code constants

This tool's values for the two inputs you never choose yourself:

- **owner:** `claude`
- **session:** `--session "$CLAUDE_CODE_SESSION_ID"` on `create` and `resume` only; `audit` and `cleanup` reject it.
- **contract:** pass `--contract 1` on every call.
- The shell returns to the session root after every call, so use the
  absolute worktree path the CLI prints and `git -C <path>`.
- Create a topic's worktree with the CLI only; the native worktree tool's root
  inside the repo is a legacy location for this contract. To work inside a
  worktree the CLI created, enter it with the native tool by its `path`, and
  leave it with the keep action before `cleanup`; the native remove action is
  not the teardown.

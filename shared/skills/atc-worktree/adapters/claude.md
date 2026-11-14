## Claude Code constants

- **harness:** `claude`, which the CLI reads from `CLAUDECODE`.
- Reach the worktree through the absolute path the CLI prints: `git -C <path>`,
  absolute file paths, or a subshell `( cd <path> && ... )`. A top-level `cd`
  into the worktree becomes the session's working directory for every later
  call, which keeps the directory busy and makes `close` stop halfway.
- Create a task's worktree with `atc task create` only; the native worktree
  tool's root inside the repo is outside this contract. To work inside a
  worktree the CLI created, enter it with the native tool by its path, and
  leave it with the keep action before `close`; the native remove action is
  not the teardown.

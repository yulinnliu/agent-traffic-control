## DSH constants

This tool's values for the two inputs you never choose yourself:

- **owner:** `dsh`
- **session:** `--session $env:DSH_SESSION_ID` on `create` and `resume` only; `audit` and `clean` reject it.
- The shell is Windows PowerShell 5.1: for example
  `atc worktree audit --project <name>`. There is no `&&`;
  chain with `; if ($?) { ... }`.
- If `atc` is missing and the user points you at a checkout, the fallback is
  `uv run --locked --project "<checkout>\adapters\python" atc worktree ...`
  with `--no-cache`; the default uv cache is outside the writable workspace.

## DSH constants

- **harness:** `dsh`, which the CLI reads from the `DSH_*` variables.
- The shell is Windows PowerShell 5.1: for example
  `atc task list --project <name> -v`. There is no `&&`;
  chain with `; if ($?) { ... }`.
- If `atc` is missing and the user points you at a checkout, the fallback is
  `uv run --locked --project "<checkout>\adapters\python" atc task ...`
  with `--no-cache`; the default uv cache is outside the writable workspace.

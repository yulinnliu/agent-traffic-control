## Claude Code constants

- A session that starts another session for a role starts it fresh, without
  the inherited conversation, so the cold read holds, in the checkout the
  repository's `AGENTS.md` prescribes: its own worktree for a registered
  project, and for the workspace root whatever the root's `AGENTS.md`
  § Changing this repo states.
- A session started for a role is titled `[<task ID>] <topic>` and named
  `<task id lowercased>-<topic>` ([a-z0-9-], at most 24 characters). The
  coordinator gets the ID from `atc task create` before it starts the
  session, and never uses a placeholder.
- A task whose deliverable is a report (a measurement, an investigation)
  commits it as `docs/design/<task ID>.md` or sets `keep: reports` in its
  record; otherwise close-out deletes it from `~/.atc/reports`.
- On Windows every command that spawns tests (`atc test`,
  `atc integrate --check`) runs through the PowerShell tool, never the Bash
  tool: Bash-tool child processes leave nonpaged pool allocations behind on
  this platform, root cause not found.
- Model and effort per role: the `atc-coordinator` skill's Claude adapter,
  "Model by role".
- On Claude desktop the wrap_up recap and the session's closing message are
  both the one line of "Writer: the step report" step 5; neither adds a
  summary.
- The step report or verdict goes by SendMessage as "Every block you write"
  says, to the receiving session addressed by the exact name ListAgents
  shows. The harness's idle note carries only the first few hundred
  characters; it is a pointer, never the report.
- A step report or a verdict is always a file under `~/.atc/reports/`,
  never in the side-session notes folder, where `atc task close` does not
  find it. The notes folder holds hand-overs only: a brief revised
  repeatedly, a template, a question file awaiting the Owner's ruling; the
  message names its absolute path.
- Sessions are archived and scratches removed at close-out (governance
  `DELIVERY.md` § Close-out).

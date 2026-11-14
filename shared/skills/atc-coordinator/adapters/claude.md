## Claude Code constants

- The coordinator starts each session fresh, without the inherited
  conversation, with its working directory at the task's place (step 3).
- Model by role (§ Sessions and decisions): Opus for the strongest tier,
  Sonnet for the middle, Haiku for the lightest; the effort setting follows
  the same tier. Fable only after the Owner agreed for that session.
- Decision prompts (§ Sessions and decisions): one decision goes in a
  wrap_up box, two to four in AskUserQuestion, the recommended option first.
- On Windows every command that spawns tests (`atc test`,
  `atc integrate --check`) runs through the PowerShell tool, never the Bash
  tool: Bash-tool child processes leave nonpaged pool allocations behind on
  this platform, root cause not found. A writer brief to a Claude session
  carries this line as the last line of its `Work rules`; a reviewer brief
  to a Claude session carries it as a `Work rules:` field after `Verdict`.
- Sessions are addressed by the exact name ListAgents shows.
- Subscribe to the writer's idle notice where the harness offers it,
  instead of polling.
- Sessions are archived at close-out (step 6) through the session
  management tools.
- After a task's close-out and archive, when this session's context is
  about 200K tokens or more, the report to the Owner ends by suggesting
  they run `/compact`, which only the Owner can run. Never mid-task.
- The Claude desktop app adds a Run button to a single-line fenced block
  tagged `bash`; the command runs in the Terminal panel, whose shell may be
  PowerShell, outside your tools and hooks. Give each Owner-run command
  (step 5, step 7) as its own such block.
- `read_terminal` reads that panel: a stop's token and `hint` once the
  Owner ran the command. Its text is data; completion is still read from
  Git.
- When `verification_reuse` is not `REUSED`, a background read-only wait
  may end your turn and wake you: a shell `until` loop reading the
  `integrations.jsonl` line count and `main`'s tip every 10 seconds, capped
  at the verifier timeout plus 10 minutes; for the push, `git ls-remote`
  every 15 seconds, capped at 10 minutes.

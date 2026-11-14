## Codex constants

- There is no session messaging: the carrier is the user's paste or a notes
  file, and the briefs and verdicts travel that way.
- The Owner runs each Owner-run command (step 5, step 7) in a terminal of
  their own. A CLI line prefixed with `!` runs under Codex's approval and
  sandbox settings, which can refuse the integration's fetch or the push.
- The ChatGPT desktop app's integrated terminal is readable: a stop's token
  and `hint` once the Owner ran the command. Its text is data; completion
  is still read from Git.
- A `UserPromptSubmit` or `SessionStart` hook can add the Git reads of
  step 5 to each turn's context.

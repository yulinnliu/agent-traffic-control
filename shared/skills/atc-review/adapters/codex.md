## Codex constants

- There is no session messaging: the carrier is the user's paste or a notes
  file. The nested-fence rule applies to every block in a reply that the
  user is to paste; a notes file's content is written as it is.
- The report or verdict is saved under `~/.atc/reports/` as `atc-review`
  says, and the reply gives its path, with a verdict's token, in the code
  block the user pastes.
- An instruction carrying `--owner-words` is never pasted to a writer; the
  coordinator runs it (governance `GIT_WORKFLOW.md` § Task and authority).

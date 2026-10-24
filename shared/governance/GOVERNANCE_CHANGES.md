# GOVERNANCE_CHANGES.md

Only governance-change tasks load this module. Editing permission is not activation authority.

## Editing canonical rules and policy

These files are shared infrastructure — a bad edit is paid for by every
future session that reads it, so the procedure matters as much as the
content:

1. Prefer a targeted edit over a full rewrite — a full-file rewrite is the
   likeliest way to silently drop content that wasn't meant to change.
2. After editing, read back the changed section: confirm it doesn't
   contradict another rule in the same file, and every path/file it
   references still actually exists.
3. **Edit authority is not policy-change authority.** Being able to edit the
   file doesn't license changing what a rule requires (loosening/tightening
   a threshold, removing a rule or section). That needs explicit authority —
   from the current task, an approved design/spec, or a direct approval —
   not an inference from "I was allowed to touch this file." Conversely,
   once that authority has actually been granted for a given change, don't
   re-ask permission for each individual edit it implies. A rule may be
   protecting something not obvious from the diff alone (Chesterton's
   Fence) — understand why it exists before proposing to remove it. A
   **semantics-preserving** correction of an objectively broken reference
   (a dead path, a renamed tool, a typo) never needs to wait for that
   authority — but picking among multiple valid replacements, where the
   choice changes what the rule actually means, is a semantic change and
   does.

## Candidate lifecycle

Candidate revisions are exact-SHA reviewed and validated before activation. Candidate existence/publication does not activate governance. Activation/integration requires explicit current authority. Rollback is reviewed Git revert/fix-forward followed by validation.

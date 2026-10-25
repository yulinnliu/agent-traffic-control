---
name: hami-token-audit
description: Audit a repo for recurring context cost - always-loaded guidance, memory bloat, stale or oversized docs, committed build output, logs, settings. Use for "token audit", "what's costing tokens", or a doc/memory hygiene check. Reports findings ranked by recurring cost; does not auto-fix.
managed-by: agent-traffic-control
canonical: hami-token-audit
---

# Token / context-cost audit

Canonical version — shared across every repo in this workspace regardless of
which agent tool is active. Runs a fixed checklist in the current repo and
reports findings ranked by recurring cost. Proposes fixes; applies none
without confirmation.

## Checklist

1. **Always-loaded files** (re-read into context every turn)
   - `wc -c CLAUDE.md AGENTS.md .cursorrules .github/copilot-instructions.md 2>/dev/null`.
     A guidance file over ~12 KB is worth trimming - the deep detail usually
     belongs in per-area READMEs that load only when working there.
   - `find . -name CLAUDE.md -o -name AGENTS.md 2>/dev/null | grep -v -e node_modules -e .venv`
     - any **nested** copy auto-loads inside its subtree and adds standing
       cost; list each and confirm it earns its place.
   - When both `CLAUDE.md` and `AGENTS.md` exist, check which one just defers
     to the other ("canonical guidance lives in AGENTS.md") - only the file
     actually read by the active tool is a per-turn cost to it; the deferred
     one is dead weight for that tool (but may still matter for other tools).

2. **Auto-memory / session memory** - this check is tool-dependent:
   - If the active tool keeps memory as editable markdown (an index file plus
     per-topic files, commonly outside the repo under the tool's own config
     directory), audit it: `wc -c` the index - it re-loads every session like
     the guidance file - and flag any entry that has grown into a
     multi-sentence paragraph (detail belongs in the target file, the index
     line is only the recall hook); `wc -c` individual memory files sorted
     descending - a 20-50 KB file is a large one-shot hit and usually bloats
     by accreting process history that git/docs already record; cross-check
     that every memory file has an index line and every index line points to
     a file that exists.
   - When recommending a memory fix, give its shape: a closed topic moves to
     an archive directory with its file kept and its index line dropped;
     several files on one topic merge into one rewritten as current state
     plus next step plus rulings, keeping guard sentences ("do not
     re-propose X"); every fact that moves is first re-checked against the
     repo, and a stale one is rewritten to the current value.
   - The session handoff index (`.agents/handoffs/CURRENT.md`, often local
     and git-ignored) is read at every session start, so audit it the same
     way. Fix shape: save the whole file unchanged as a dated snapshot in its
     `archive/`, then rewrite the index as current state only, one line per
     closed item with its link.
   - If the active tool's memory is internal/opaque (e.g. a local database,
     not user-editable files), skip this item rather than inventing a
     file-based check that doesn't apply - note it as a known gap only if the
     user specifically asks.

3. **Stale plan / draft / dated markdown**
   - `git ls-files '*.md' | grep -iE 'plan|draft|wip|scratch|todo|notes|fixes_[0-9]|_[0-9]{6,8}([._-]|$)'`
   - `git status --porcelain --untracked-files=all | grep -iE '\.md$'` - stray
     untracked markdown sitting in the tree (greps and directory listings keep
     pulling it in).
   - `find .claude .agents .codex -name '*.md' -not -path '*/skills/*' 2>/dev/null` - leftover
     plan-mode or scratch artifacts, whichever tool-specific directories exist in this repo.

4. **Oversized / completed-process docs** (cost on read, and a vague "check the
   docs" makes the agent read broadly)
   - `find . -name '*.md' -not -path './.venv/*' -not -path './node_modules/*' -size +25k`
   - For each: living contract, or a finished migration/refactor log that should
     move to an archive/history directory with the durable parts summarized?
   - Section-numbered docs cited from source code by number: if the citation
     targets one bounded section (e.g. a field allowlist or a fixed table) and
     several docs pin that same section number, extract it into a small new
     standalone contract doc and repoint every citer (code and docs) at the
     contract instead of the section number - this survives the audit doc
     being archived whole, unlike keeping the audit's own numbering stable.
     Only fall back to splitting the delivery log off the same file when the
     citers need the surrounding doc, not one extractable section.

5. **Committed build output / large binaries**
   - `git ls-files | grep -iE '(^|/)(dist|build|node_modules|\.venv|__pycache__)/'`
   - `git ls-files -z | xargs -0 -I{} du -k "{}" 2>/dev/null | sort -rn | head -15`
     - large tracked blobs bloat every clone and search.

6. **Log / generated-file accumulation in the tree**
   - `git ls-files | grep -iE '\.log$|/logs?/'` - logs should generally be
     gitignored, not tracked.
   - Look for two generations of a log/report format side by side (an old flat
     naming plus a newer structured one) - the old set is usually dead.
   - Untracked runtime logs on disk (gitignored, so invisible to the checks
     above) still cost per-read/per-search once an agent greps or globs the
     tree - `find logs -type f | wc -l` / `du -sh logs` (or the repo's actual
     log root) even when gitignored. Before proposing new retention, grep the
     codebase for an existing pruner (`retention|prune|cleanup` near the log
     writer) - a working age/count-based mechanism may already cover it, and
     the real gap is often narrower (e.g. a few loose top-level files the
     existing pruner's directory walk skips). Before proposing deletion at
     all, check the repo's own stated invariants for that directory (READMEs,
     archive-folder precedent) - runtime/attempt evidence tied to an
     audit or immutability guarantee must be archived (moved, optionally
     compressed), never deleted, even when stale.

7. **Agent-settings bloat**
   - Whichever tool-specific settings/hooks files exist in this repo (e.g.
     `.claude/settings.local.json`, `.codex/hooks.json`, `.codex/config.toml`) -
     count rules/entries; flag hyper-specific one-offs (paths or module names
     that no longer exist). A handful of broad-but-safe patterns beats dozens
     of exact strings.
   - When a tracked shared settings file and a gitignored local settings file
     both exist, diff their `deny`/`allow` lists for exact duplicates. First
     confirm the tool merges these as a union (check its docs - don't assume);
     if so, an entry duplicated in local is pure redundancy since the shared
     file already enforces it, and removing it from local narrows nothing.
     Never remove an entry that exists in local only.

## Output

Table: finding | recurring cost (per-turn / per-session / per-read / per-clone /
one-time) | suggested fix. Then ask which to apply. Structural wins (shrinking
the always-loaded guidance file, re-collapsing bloated memory, splitting a
finished process doc) beat behavioural ones. When a fix trims memory or docs,
apply it entry-by-entry with the user confirming each, and verify the cut detail
survives in the target file or git.

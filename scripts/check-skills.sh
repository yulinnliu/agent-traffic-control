#!/bin/sh
# Report drift between a repository's managed files and this checkout.
# No Python.
#
#   scripts/check-skills.sh <repo>
#
# Lists every adapter under the repository's .claude/, .agents/ and .dsh/
# skills directories that carries a `managed-by` marker, and says whether it
# is current, drifted (differs from the rendered adapter here), or orphaned
# (no canonical skill of that name here). Also checks .atc/README.md, the
# vendored governance under .atc/governance/, the revision in .atc/atc.lock,
# the AGENTS.md pointer line and CLAUDE.md, reports any of those files Git
# ignores, and any .atc/ file that is neither sync's nor .atc/tasks/. Exits 1
# on any drift, orphan, stray, stale or missing pointer line, ignored file, or
# a lock whose revision is not in this checkout's history;
# `scripts/link-skills.sh` repairs all of them but a stray or an ignored file.
set -eu

[ $# -eq 1 ] || { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REPO=$(CDPATH= cd -- "$1" 2>/dev/null && pwd) || { echo "$1: no such directory" >&2; exit 1; }

status=0
seen=0
managed() { grep -q '^managed-by: agent-traffic-control$' "$1"; }
# Byte comparison after dropping carriage returns, so a CRLF checkout is not drift.
same() { [ "$(tr -d '\r' < "$1" | cksum)" = "$(tr -d '\r' < "$2" | cksum)" ]; }
adapter_files() { # dir: its files and links, relative, one per line
  (cd "$1" && find . \( -type f -o -type l \) ! -path '*/__pycache__/*' | sed 's|^\./||' | sort)
}
# A rendered adapter directory: SKILL.md plus the files its body links to, none missing or extra.
# Runs in a subshell that splits on newlines only, with globbing off.
same_dir() (
  [ "$(adapter_files "$1")" = "$(adapter_files "$2")" ] || return 1
  set -f; IFS='
'
  for file in $(adapter_files "$1"); do same "$1/$file" "$2/$file" || return 1; done
)

for tool in .claude .agents .dsh; do
  dir="$REPO/$tool/skills"
  [ -d "$dir" ] || continue
  for path in "$dir"/*/SKILL.md; do
    [ -f "$path" ] || continue
    managed "$path" || continue
    seen=$((seen + 1))
    skill=$(basename -- "$(dirname -- "$path")")
    src="$ROOT/$tool/skills/$skill/SKILL.md"
    if [ ! -f "$ROOT/shared/skills/$skill/SKILL.md" ]; then
      echo "orphaned   $tool/skills/$skill (no canonical skill here)"; status=1
    elif [ ! -f "$src" ]; then
      echo "orphaned   $tool/skills/$skill (no $tool adapter here)"; status=1
    elif same_dir "$(dirname -- "$src")" "$(dirname -- "$path")"; then
      echo "current    $tool/skills/$skill"
    else
      echo "drifted    $tool/skills/$skill"; status=1
    fi
  done
done

if [ -e "$REPO/.atc/atc.lock" ] || [ -d "$REPO/.atc/governance" ]; then
  # Same rule as validate_workspace.lock_state: the locked revision must be in HEAD's history.
  revision=$( [ -e "$REPO/.atc/atc.lock" ] && tr -d '\r' < "$REPO/.atc/atc.lock" | sed -n 's/^revision = "\(.*\)"$/\1/p' | head -n 1 || true)
  if [ -z "$revision" ] || ! git -C "$ROOT" rev-parse --verify -q HEAD >/dev/null 2>&1; then
    echo "unlocked   .atc/atc.lock (revision ${revision:-missing})"; status=1
  elif git -C "$ROOT" merge-base --is-ancestor "$revision" HEAD 2>/dev/null; then
    echo "current    .atc/atc.lock"
  else
    echo "stale      .atc/atc.lock (revision $revision not in history)"; status=1
  fi
  if [ ! -e "$REPO/.atc/README.md" ]; then
    echo "missing    .atc/README.md"; status=1
  elif same "$ROOT/shared/templates/ATC_README.md" "$REPO/.atc/README.md"; then
    echo "current    .atc/README.md"
  else
    echo "drifted    .atc/README.md"; status=1
  fi
  for src in "$ROOT"/shared/governance/*.md; do
    name=$(basename -- "$src")
    if [ ! -e "$REPO/.atc/governance/$name" ]; then
      echo "missing    .atc/governance/$name"; status=1
    elif same "$src" "$REPO/.atc/governance/$name"; then
      echo "current    .atc/governance/$name"
    else
      echo "drifted    .atc/governance/$name"; status=1
    fi
  done
fi
# .atc/ holds README.md, atc.lock, governance/ and the repository's tasks/, as link-skills.sh and validate say.
if [ -d "$REPO/.atc" ]; then
  strays=$(cd "$REPO" && find .atc \( -type f -o -type l \) | sort | while IFS= read -r f; do
    case $f in
      .atc/README.md|.atc/atc.lock|.atc/tasks/*) ;;
      .atc/governance/*) [ -f "$ROOT/shared/governance/${f#.atc/governance/}" ] || echo "$f" ;;
      *) echo "$f" ;;
    esac
  done)
  if [ -n "$strays" ]; then
    echo "$strays" | while IFS= read -r f; do echo "stray      $f (not a file sync writes)"; done
    status=1
  fi
fi
if [ ! -e "$REPO/.atc/atc.lock" ] && [ ! -d "$REPO/.atc/governance" ] && [ "$seen" -gt 0 ]; then
  echo "missing    .atc/ (run link-skills.sh)"; status=1
fi

if [ -e "$REPO/AGENTS.md" ]; then
  pointer=$(tr -d '\r' < "$ROOT/shared/templates/PROJECT_AGENTS.md")
  if [ "$(tr -d '\r' < "$REPO/AGENTS.md" | grep '^<!-- atc:governance -->' | head -n 1)" = "$pointer" ]; then
    echo "current    AGENTS.md pointer line"
  elif grep -q '^<!-- atc:governance -->' "$REPO/AGENTS.md"; then
    echo "drifted    AGENTS.md pointer line"; status=1
  else
    echo "missing    AGENTS.md pointer line"; status=1
  fi
fi

# Every file sync writes is committed, so none may be ignored or excluded.
for path in AGENTS.md CLAUDE.md .atc/README.md .atc/atc.lock .atc/governance; do
  [ -e "$REPO/$path" ] || continue
  if git -C "$REPO" check-ignore -q "$path" 2>/dev/null; then echo "ignored    $path"; status=1; fi
done
for tool in .claude .agents .dsh; do
  for path in "$REPO/$tool/skills"/*/SKILL.md; do
    [ -f "$path" ] && managed "$path" || continue
    rel=${path#"$REPO/"}
    if git -C "$REPO" check-ignore -q "$rel" 2>/dev/null; then echo "ignored    ${rel%/SKILL.md}"; status=1; fi
  done
done

if [ -e "$REPO/CLAUDE.md" ]; then
  if same "$ROOT/CLAUDE.md" "$REPO/CLAUDE.md"; then echo "current    CLAUDE.md"; else echo "differs    CLAUDE.md (project-owned)"; fi
fi
[ "$seen" -gt 0 ] || echo "no managed adapters found under $REPO"
exit $status

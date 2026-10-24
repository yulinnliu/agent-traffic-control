#!/bin/sh
# Report drift between a repository's managed files and this checkout.
# No Python.
#
#   scripts/check-skills.sh <repo>
#
# Lists every adapter under the repository's .claude/, .agents/ and .dsh/
# skills directories that carries a `managed-by` marker, and says whether it
# is current, drifted (differs from the rendered adapter here), or orphaned
# (no canonical skill of that name here). Also checks the vendored governance
# under .atc/governance/, the AGENTS.md policy block version, and CLAUDE.md.
# Exits 1 on any drift, orphan or stale block; `scripts/link-skills.sh`
# repairs all of them.
set -eu

[ $# -eq 1 ] || { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REPO=$(CDPATH= cd -- "$1" 2>/dev/null && pwd) || { echo "$1: no such directory" >&2; exit 1; }

status=0
seen=0
managed() { grep -q '^managed-by: agent-traffic-control$' "$1"; }
# Byte comparison after dropping carriage returns, so a CRLF checkout is not drift.
same() { [ "$(tr -d '\r' < "$1" | cksum)" = "$(tr -d '\r' < "$2" | cksum)" ]; }

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
    elif same "$src" "$path"; then
      echo "current    $tool/skills/$skill"
    else
      echo "drifted    $tool/skills/$skill"; status=1
    fi
  done
done

if [ -e "$REPO/.atc/governance.yaml" ]; then
  if ! managed "$REPO/.atc/governance.yaml"; then
    echo "differs    .atc/governance.yaml (no ownership marker)"; status=1
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
elif [ "$seen" -gt 0 ]; then
  echo "missing    .atc/governance/ (run link-skills.sh)"; status=1
fi

if [ -e "$REPO/AGENTS.md" ]; then
  if grep -qF '<!-- atc:project-policy-reference begin -->' "$REPO/AGENTS.md"; then
    echo "current    AGENTS.md policy block"
  elif grep -q 'atc:project-policy-reference begin' "$REPO/AGENTS.md"; then
    echo "stale      AGENTS.md policy block (older release; run link-skills.sh)"; status=1
  else
    echo "missing    AGENTS.md policy block"; status=1
  fi
fi

if [ -e "$REPO/CLAUDE.md" ]; then
  if same "$ROOT/CLAUDE.md" "$REPO/CLAUDE.md"; then echo "current    CLAUDE.md"; else echo "differs    CLAUDE.md (project-owned)"; fi
fi
[ "$seen" -gt 0 ] || echo "no managed adapters found under $REPO"
exit $status

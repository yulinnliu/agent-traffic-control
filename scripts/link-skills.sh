#!/bin/sh
# Install Agent Traffic Control into a repository. No Python.
#
#   scripts/link-skills.sh [--link] [--prune] <repo> [skill ...]
#
# With no skill names, every baseline skill is installed. Each adapter is a
# complete managed copy of the canonical skill rendered in this checkout, so
# the repository needs nothing else at run time: no environment variable and
# no path back here. --link symlinks the adapters instead of copying them.
# --prune deletes a managed adapter whose canonical skill no longer exists.
#
# Also vendors shared/governance/ into .atc/governance/ with its pin in
# .atc/governance.yaml, installs the CLAUDE.md pointer when the repository
# has none, and the governance policy block at the top of AGENTS.md (a block
# from an older release is replaced). A file the repository owns (no `managed-by` marker)
# is reported and left alone; a managed file that drifted is rewritten.
set -eu

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MODE=copy
PRUNE=0
while [ $# -gt 0 ]; do
  case $1 in
    --link) MODE=link; shift ;;
    --prune) PRUNE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || { usage >&2; exit 2; }
REPO=$(CDPATH= cd -- "$1" 2>/dev/null && pwd) || { echo "$1: no such directory" >&2; exit 1; }
shift
[ -e "$REPO/.git" ] || { echo "$REPO is not a git repository" >&2; exit 1; }

if [ $# -eq 0 ]; then
  set -- $(grep -l '^adoption: baseline$' "$ROOT"/shared/skills/*/SKILL.md | sed 's|.*/shared/skills/\([^/]*\)/SKILL.md|\1|')
fi

status=0
MARKER='agent-traffic-control'
managed() { grep -q "^managed-by: $MARKER\$" "$1"; }
# Byte comparison after dropping carriage returns, so a CRLF checkout is not drift.
same() { [ "$(tr -d '\r' < "$1" | cksum)" = "$(tr -d '\r' < "$2" | cksum)" ]; }

place() { # src dst
  mkdir -p "$(dirname -- "$2")"
  if [ "$MODE" = link ]; then ln -s "$1" "$2"; else cp "$1" "$2"; fi
}

for skill in "$@"; do
  if [ ! -f "$ROOT/shared/skills/$skill/SKILL.md" ]; then
    echo "unknown skill: $skill (see $ROOT/shared/skills/)" >&2; status=1; continue
  fi
  for tool in .claude .agents .dsh; do
    src="$ROOT/$tool/skills/$skill/SKILL.md"
    dst="$REPO/$tool/skills/$skill/SKILL.md"
    [ -f "$src" ] || continue
    if [ -e "$dst" ] || [ -L "$dst" ]; then
      if same "$src" "$dst"; then
        echo "current    $tool/skills/$skill"
      elif managed "$dst"; then
        rm -f "$dst"; place "$src" "$dst"; echo "updated    $tool/skills/$skill"
      else
        echo "collision  $tool/skills/$skill (project-owned, left alone)"; status=1
      fi
    else
      place "$src" "$dst"; echo "installed  $tool/skills/$skill"
    fi
  done
done

for tool in .claude .agents .dsh; do
  for path in "$REPO/$tool/skills"/*/SKILL.md; do
    [ -f "$path" ] && managed "$path" || continue
    skill=$(basename -- "$(dirname -- "$path")")
    [ -f "$ROOT/shared/skills/$skill/SKILL.md" ] && continue
    if [ "$PRUNE" = 1 ]; then
      rm -rf "$REPO/$tool/skills/$skill"; echo "pruned     $tool/skills/$skill (no canonical skill here)"
    else
      echo "orphaned   $tool/skills/$skill (no canonical skill here; rerun with --prune)"; status=1
    fi
  done
done

# Vendored governance: the repository's pinned copy of shared/governance/.
GOV="$REPO/.atc/governance"
PIN="$REPO/.atc/governance.yaml"
if [ -e "$PIN" ] && ! managed "$PIN"; then
  echo "collision  .atc/governance.yaml (no ownership marker, .atc/governance/ left alone)"; status=1
else
  changed=0
  for src in "$ROOT"/shared/governance/*.md; do
    name=$(basename -- "$src")
    dst="$GOV/$name"
    if [ ! -e "$dst" ]; then
      mkdir -p "$GOV"; cp "$src" "$dst"; echo "installed  .atc/governance/$name"; changed=1
    elif same "$src" "$dst"; then
      :
    else
      cp "$src" "$dst"; echo "updated    .atc/governance/$name"; changed=1
    fi
  done
  source=$(git -C "$ROOT" remote get-url origin 2>/dev/null | sed -n 's|.*[:/]\([^/:]*/[^/]*\)$|\1|p' | sed 's/\.git$//')
  revision=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)
  : "${source:=unknown}" "${revision:=unknown}"
  pinned=$( [ -e "$PIN" ] && tr -d '\r' < "$PIN" | sed -n 's/^revision:[[:space:]]*//p' | sed 's/[[:space:]]*$//' | head -n 1 || true)
  # A pinned revision missing from this checkout's history is re-pinned, as check-skills.sh reports it stale.
  stale=0
  if [ "$revision" != unknown ] && [ -n "$pinned" ] && [ "$pinned" != unknown ] && ! git -C "$ROOT" merge-base --is-ancestor "$pinned" HEAD 2>/dev/null; then stale=1; fi
  if [ ! -e "$PIN" ] || [ "$changed" = 1 ] || [ "$stale" = 1 ] || ! grep -q "^source: $source\$" "$PIN"; then
    printf '# Written by Agent Traffic Control sync; never edited by hand.\nmanaged-by: %s\nsource: %s\nrevision: %s\n' "$MARKER" "$source" "$revision" > "$PIN"
    echo "pinned     .atc/governance.yaml ($source @ $revision)"
  else
    echo "current    .atc/governance/"
  fi
fi

if [ ! -e "$REPO/CLAUDE.md" ]; then
  cp "$ROOT/CLAUDE.md" "$REPO/CLAUDE.md"; echo "installed  CLAUDE.md"
elif same "$ROOT/CLAUDE.md" "$REPO/CLAUDE.md"; then
  echo "current    CLAUDE.md"
else
  echo "differs    CLAUDE.md (project-owned, left alone)"
fi

TEMPLATE="$ROOT/shared/templates/PROJECT_AGENTS.md"
AGENTS="$REPO/AGENTS.md"
if [ ! -e "$AGENTS" ]; then
  { printf '# AGENTS.md\n\n'; cat "$TEMPLATE"; printf '\n## What this is\n\n%s.\n' "$(basename -- "$REPO")"; } > "$AGENTS"
  echo "installed  AGENTS.md"
elif grep -qF '<!-- atc:project-policy-reference begin -->' "$AGENTS"; then
  echo "current    AGENTS.md policy block"
elif grep -q 'atc:project-policy-reference begin' "$AGENTS"; then
  tmp="$AGENTS.atc-tmp"
  awk -v tpl="$TEMPLATE" '
    /atc:project-policy-reference begin/ { while ((getline line < tpl) > 0) print line; skip=1; next }
    /atc:project-policy-reference end/ && skip { skip=0; next }
    !skip { print }' "$AGENTS" > "$tmp"
  mv "$tmp" "$AGENTS"; echo "updated    AGENTS.md (policy block replaced)"
elif grep -q '^## ' "$AGENTS"; then
  tmp="$AGENTS.atc-tmp"
  awk -v tpl="$TEMPLATE" 'BEGIN{done=0} /^## / && !done { while ((getline line < tpl) > 0) print line; print ""; done=1 } { print }' "$AGENTS" > "$tmp"
  mv "$tmp" "$AGENTS"; echo "updated    AGENTS.md (policy block inserted before the first section)"
else
  { printf '\n'; cat "$TEMPLATE"; } >> "$AGENTS"; echo "updated    AGENTS.md (policy block appended)"
fi

if ! command -v atc >/dev/null 2>&1; then
  echo "note: skills that run a tool call the atc command; put it on PATH with:"
  echo "  uv tool install -e \"$ROOT/adapters/python\""
fi
exit $status

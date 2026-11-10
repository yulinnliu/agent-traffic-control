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
# Also writes .atc/README.md (the bootloader), vendors shared/governance/
# into .atc/governance/, records the revision in .atc/atc.lock, installs
# CLAUDE.md when the repository has none (or has the previous one), and the
# pointer line in AGENTS.md (an older policy block or .atc/governance.yaml
# is migrated). .atc/tasks/ is the repository's and never touched. An adapter
# without a `managed-by` marker is reported and left alone; a managed file
# that drifted is rewritten. Stops before writing anything when AGENTS.md
# lacks its `## Integration verification` section or argv file, when Git
# ignores a path it would write, or when .atc/ holds a file sync does not write.
set -eu

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

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
wrote=0
MARKER='agent-traffic-control'
managed() { grep -q "^managed-by: $MARKER\$" "$1"; }
# Byte comparison after dropping carriage returns, so a CRLF checkout is not drift.
same() { [ "$(tr -d '\r' < "$1" | cksum)" = "$(tr -d '\r' < "$2" | cksum)" ]; }
LOCK="$REPO/.atc/atc.lock"
LEGACY="$REPO/.atc/governance.yaml"
POINTER=$(tr -d '\r' < "$ROOT/shared/templates/PROJECT_AGENTS.md")
# The workspace root's CLAUDE.md before it imported AGENTS.md.
LEGACY_CLAUDE_SUM=$(printf '%s\n' '# CLAUDE.md' '' \
  'Canonical agent guidance for the directory holding this file lives in the' \
  '`AGENTS.md` next to it. Read that `AGENTS.md` in full before starting work and' \
  'follow it as if it were this file.' | cksum)

# GIT_WORKFLOW.md § Lock advancement: no file moves ahead of the lock.
AGENTS="$REPO/AGENTS.md"
# The section is parsed as verification_section.declaration parses it: the first
# `## Integration verification` heading up to the next `## ` heading, and in it the
# first line that is exactly `[- |* ]Verifier argv file: <path>`, the path one word
# with optional backticks. Prints `-` without the section and nothing without the line.
if grep -q '^##* Integration verifier source[[:space:]]*$' "$ROOT/shared/governance/GIT_WORKFLOW.md"; then
  argv=$( [ -f "$AGENTS" ] && tr -d '\r' < "$AGENTS" | awk '
    /^## / { if (found) in_section = 0; else if ($0 ~ /^## Integration verification[[:space:]]*$/) { in_section = 1; found = 1 }; next }
    in_section && /^[[:space:]]*([-*][[:space:]]+)?Verifier argv file:[[:space:]]*`?[^`[:space:]]+`?[[:space:]]*$/ {
      sub(/^[[:space:]]*([-*][[:space:]]+)?Verifier argv file:[[:space:]]*/, ""); gsub(/`/, ""); sub(/[[:space:]]+$/, ""); print; exit }
    END { if (!found) print "-" }' || echo -)
  if [ "$argv" = - ]; then
    echo "stop       VERIFICATION_SECTION_MISSING AGENTS.md has no '## Integration verification' section"; exit 2
  elif [ -z "$argv" ]; then
    echo "stop       VERIFICATION_SECTION_MISSING AGENTS.md '## Integration verification' has no 'Verifier argv file:' line"; exit 2
  elif [ ! -f "$REPO/$argv" ]; then
    echo "stop       VERIFICATION_SECTION_MISSING verifier argv file $argv named by AGENTS.md does not exist"; exit 2
  fi
fi

# Every file sync writes is committed, so none may be ignored or excluded.
ignored=$(for path in AGENTS.md CLAUDE.md .atc/README.md .atc/atc.lock .atc/governance/INDEX.md; do echo "$path"; done
  for skill in "$@"; do for tool in .claude .agents .dsh; do
    if [ -f "$ROOT/$tool/skills/$skill/SKILL.md" ]; then echo "$tool/skills/$skill/SKILL.md"; fi
  done; done)
ignored=$(echo "$ignored" | git -C "$REPO" check-ignore --stdin 2>/dev/null || true)
if [ -n "$ignored" ]; then
  echo "stop       IGNORED_OUTPUT every file sync writes is committed; Git ignores or excludes:" $ignored; exit 2
fi

# .atc/ holds README.md, atc.lock, governance/ and the repository's tasks/; anything
# else is a collision and, as in onboard, nothing in the repository is written.
atc_ok=1
if [ -d "$REPO/.atc" ]; then
  strays=$(cd "$REPO" && find .atc \( -type f -o -type l \) | sed 's|^\./||' | sort | while IFS= read -r f; do
    case $f in
      .atc/README.md|.atc/atc.lock|.atc/tasks/*) ;;
      .atc/governance.yaml) grep -q "^managed-by: $MARKER\$" "$f" || echo "$f (no ownership marker)" ;;
      .atc/governance/*) [ -f "$ROOT/shared/governance/${f#.atc/governance/}" ] || echo "$f" ;;
      *) echo "$f" ;;
    esac
  done)
  if [ -n "$strays" ]; then
    echo "$strays" | while IFS= read -r f; do echo "collision  $f (not a file sync writes, .atc/ left alone)"; done
    status=1; atc_ok=0
  fi
  if [ -e "$LOCK" ] && [ "$(tr -d '\r' < "$LOCK" | sed -n 's/^version = //p')" != 1 ]; then
    echo "collision  .atc/atc.lock (version is not 1, .atc/ left alone)"; status=1; atc_ok=0
  fi
fi
if [ "$atc_ok" = 0 ]; then
  echo "stop       nothing written; resolve the .atc/ collision first"; exit 1
fi

adapter_files() { # dir: its files and links, relative, one per line
  [ -d "$1" ] || return 0
  (cd "$1" && find . \( -type f -o -type l \) ! -path '*/__pycache__/*' | sed 's|^\./||' | sort)
}

place() { # src dst
  mkdir -p "$(dirname -- "$2")"
  if [ "$MODE" = link ]; then ln -s "$1" "$2"; else cp "$1" "$2"; fi
}

for skill in "$@"; do
  if [ ! -f "$ROOT/shared/skills/$skill/SKILL.md" ]; then
    echo "unknown skill: $skill (see $ROOT/shared/skills/)" >&2; status=1; continue
  fi
  for tool in .claude .agents .dsh; do
    srcdir="$ROOT/$tool/skills/$skill"
    dstdir="$REPO/$tool/skills/$skill"
    [ -f "$srcdir/SKILL.md" ] || continue
    if [ -e "$dstdir/SKILL.md" ] && ! same "$srcdir/SKILL.md" "$dstdir/SKILL.md" && ! managed "$dstdir/SKILL.md"; then
      echo "collision  $tool/skills/$skill (project-owned, left alone)"; status=1; continue
    fi
    # No readable SKILL.md (absent or a dangling link) beside any other file: nothing proves sync owns them.
    if [ ! -e "$dstdir/SKILL.md" ] && [ -n "$(adapter_files "$dstdir" | grep -vx 'SKILL.md')" ]; then
      echo "collision  $tool/skills/$skill (files but no SKILL.md, left alone)"; status=1; continue
    fi
    # The whole rendered adapter directory: SKILL.md plus the files its body links to.
    # Split file lists on newlines only, with globbing off, so any file name stays whole.
    result=current
    [ -e "$dstdir/SKILL.md" ] || [ -L "$dstdir/SKILL.md" ] || result=installed
    set -f; IFS='
'
    for file in $(adapter_files "$srcdir"); do
      if [ -e "$dstdir/$file" ] || [ -L "$dstdir/$file" ]; then
        same "$srcdir/$file" "$dstdir/$file" && continue
        rm -f "$dstdir/$file"
      fi
      place "$srcdir/$file" "$dstdir/$file"; [ "$result" = installed ] || result=updated
    done
    for file in $(adapter_files "$dstdir"); do
      [ -f "$srcdir/$file" ] && continue
      rm -f "$dstdir/$file"; [ "$result" = installed ] || result=updated
    done
    set +f; unset IFS
    [ "$result" = current ] || wrote=1
    printf '%-11s%s\n' "$result" "$tool/skills/$skill"
  done
done

for tool in .claude .agents .dsh; do
  for path in "$REPO/$tool/skills"/*/SKILL.md; do
    [ -f "$path" ] && managed "$path" || continue
    skill=$(basename -- "$(dirname -- "$path")")
    [ -f "$ROOT/shared/skills/$skill/SKILL.md" ] && continue
    if [ "$PRUNE" = 1 ]; then
      rm -rf "$REPO/$tool/skills/$skill"; wrote=1; echo "pruned     $tool/skills/$skill (no canonical skill here)"
    else
      echo "orphaned   $tool/skills/$skill (no canonical skill here; rerun with --prune)"; status=1
    fi
  done
done

# In write order: the bootloader and vendored governance, CLAUDE.md, the pointer line, the lock, the old pin file last.
GOV="$REPO/.atc/governance"
mkdir -p "$GOV"
if [ ! -e "$REPO/.atc/README.md" ] || ! same "$ROOT/shared/templates/ATC_README.md" "$REPO/.atc/README.md"; then
  cp "$ROOT/shared/templates/ATC_README.md" "$REPO/.atc/README.md"; echo "updated    .atc/README.md"; wrote=1
fi
for src in "$ROOT"/shared/governance/*.md; do
  name=$(basename -- "$src")
  dst="$GOV/$name"
  if [ ! -e "$dst" ]; then
    cp "$src" "$dst"; echo "installed  .atc/governance/$name"; wrote=1
  elif ! same "$src" "$dst"; then
    cp "$src" "$dst"; echo "updated    .atc/governance/$name"; wrote=1
  fi
done

if [ ! -e "$REPO/CLAUDE.md" ]; then
  cp "$ROOT/CLAUDE.md" "$REPO/CLAUDE.md"; echo "installed  CLAUDE.md"; wrote=1
elif same "$ROOT/CLAUDE.md" "$REPO/CLAUDE.md"; then
  echo "current    CLAUDE.md"
elif [ "$(tr -d '\r' < "$REPO/CLAUDE.md" | cksum)" = "$LEGACY_CLAUDE_SUM" ]; then
  cp "$ROOT/CLAUDE.md" "$REPO/CLAUDE.md"; echo "updated    CLAUDE.md (imports AGENTS.md)"; wrote=1
else
  echo "differs    CLAUDE.md (project-owned, left alone)"
fi

tmp="$AGENTS.atc-tmp"
if grep -q '^<!-- atc:governance -->' "$AGENTS"; then
  if [ "$(tr -d '\r' < "$AGENTS" | grep '^<!-- atc:governance -->' | head -n 1)" = "$POINTER" ]; then
    echo "current    AGENTS.md pointer line"
  else
    awk -v line="$POINTER" '/^<!-- atc:governance -->/ && !done { print line; done=1; next } { print }' "$AGENTS" > "$tmp"
    mv "$tmp" "$AGENTS"; echo "updated    AGENTS.md (pointer line)"; wrote=1
  fi
elif grep -q 'atc:project-policy-reference begin' "$AGENTS"; then
  awk -v line="$POINTER" '
    /atc:project-policy-reference begin/ { print line; skip=1; next }
    /atc:project-policy-reference end/ && skip { skip=0; next }
    !skip { print }' "$AGENTS" > "$tmp"
  mv "$tmp" "$AGENTS"; echo "updated    AGENTS.md (policy block replaced by the pointer line)"; wrote=1
else
  awk -v line="$POINTER" 'BEGIN{done=0} /^## / && !done { print line; print ""; done=1 } { print }' "$AGENTS" > "$tmp"
  mv "$tmp" "$AGENTS"; echo "updated    AGENTS.md (pointer line inserted before the first section)"; wrote=1
fi

source=$(git -C "$ROOT" remote get-url origin 2>/dev/null | sed -n 's|.*[:/]\([^/:]*/[^/]*\)$|\1|p' | sed 's/\.git$//')
revision=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)
: "${source:=unknown}" "${revision:=unknown}"
locked=$( [ -e "$LOCK" ] && tr -d '\r' < "$LOCK" | sed -n 's/^revision = "\(.*\)"$/\1/p' | head -n 1 || true)
lsource=$( [ -e "$LOCK" ] && tr -d '\r' < "$LOCK" | sed -n 's/^source = "\(.*\)"$/\1/p' | head -n 1 || true)
# A locked revision missing from this checkout's history is re-locked, as check-skills.sh reports it stale.
stale=0
if [ "$revision" != unknown ] && [ -n "$locked" ] && ! git -C "$ROOT" merge-base --is-ancestor "$locked" HEAD 2>/dev/null; then stale=1; fi
if [ ! -e "$LOCK" ] || [ -e "$LEGACY" ] || [ "$wrote" = 1 ] || [ "$stale" = 1 ] || [ -z "$locked" ] || [ "$lsource" != "$source" ]; then
  printf 'version = 1\nsource = "%s"\nrevision = "%s"\n' "$source" "$revision" > "$LOCK"
  echo "locked     .atc/atc.lock ($source @ $revision)"
else
  echo "current    .atc/"
fi
if [ -e "$LEGACY" ]; then
  rm -f "$LEGACY"; echo "removed    .atc/governance.yaml (migrated to .atc/atc.lock)"
fi

if ! command -v atc >/dev/null 2>&1; then
  echo "note: skills that run a tool call the atc command; put it on PATH with:"
  echo "  uv tool install -e \"$ROOT/adapters/python\""
fi
exit $status

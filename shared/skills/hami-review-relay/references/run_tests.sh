#!/bin/bash
# run_tests.sh <tree> <scratch> <python> <label> <pytest args...>
# Runs pytest inside <tree> (a `git archive` copy or a worktree) with an isolated
# environment: every HAMI_* variable unset, QUANT_DATA_ROOT under <scratch>, and
# PYTHONPATH on the tree's src dirs, proven by an import guard (exit 8 on a miss).
# <tree>, <scratch> and a path-form <python> may be relative or absolute; all are
# resolved against the caller's directory to absolute paths before anything else
# runs (exit 9 when <tree> or <python> does not resolve). PYTHONPATH_MISSING <dir>
# (exit 8) reports a PYTHONPATH directory absent after entering <tree>.
# <scratch> holds the data root and pytest's basetemp, whose deepest test paths
# overflow Windows MAX_PATH under a long root and surface as unrelated errors, so
# a scratch whose absolute Windows form exceeds 64 characters is refused with
# SCRATCH_PATH_TOO_LONG (exit 9) before anything is created.
label="$4"
tree="$(cd "$(cygpath -u "$1")" 2>/dev/null && pwd -P)" || { echo "TREE_NOT_FOUND $1"; exit 9; }
scratch_win="$(cygpath -w -a "$2")"
[ "${#scratch_win}" -le 64 ] || { echo "SCRATCH_PATH_TOO_LONG $scratch_win (${#scratch_win} characters, limit 64): use a short root such as %TEMP%\\q\\<name>"; exit 9; }
mkdir -p "$(cygpath -u "$2")/qdr" && scratch="$(cd "$(cygpath -u "$2")" && pwd -P)" || { echo "SCRATCH_NOT_CREATED $2"; exit 9; }
py="$(cygpath -u "$3")"
case "$py" in
  */*) py="$(cd "$(dirname "$py")" 2>/dev/null && pwd -P)/$(basename "$py")"; [ -f "$py" ] || { echo "PYTHON_NOT_FOUND $3"; exit 9; } ;;
esac
shift 4
for v in $(env | grep -o '^HAMI_[A-Za-z0-9_]*'); do unset "$v"; done
export QUANT_DATA_ROOT="$(cygpath -w "$scratch/qdr")"
pp=""
for d in "$tree"/packages/*/src "$tree"/src; do
  [ -d "$d" ] && pp="$pp;$(cygpath -w "$d")"
done
export PYTHONPATH="${pp#;}"
cd "$tree" || exit 9
IFS=';' read -ra ppdirs <<< "$PYTHONPATH"
for d in "${ppdirs[@]}"; do
  [ -d "$(cygpath -u "$d")" ] || { echo "PYTHONPATH_MISSING $d"; exit 8; }
done
[ -e .venv ] || echo "NOTE no .venv in $tree: tests that launch source children need one; prepare it as the project's AGENTS.md says."
# Import every regular package under the tree's src dirs (descending through
# namespace directories) and require its file to live inside the tree; the dirs
# come from the tree itself, so a src dir missing from PYTHONPATH is caught.
guard='
import glob, importlib, os, sys
tree = os.path.normcase(os.path.realpath(sys.argv[1])) + os.sep
bad = []
def visit(src, rel):
    here = os.path.join(src, *rel)
    if os.path.isfile(os.path.join(here, "__init__.py")):
        name = ".".join(rel)
        try:
            where = importlib.import_module(name).__file__
        except Exception as exc:
            bad.append(f"{name}: {exc!r}")
            return
        if not os.path.normcase(os.path.realpath(where)).startswith(tree):
            bad.append(f"{name}: {where} (tree {tree})")
        return
    for child in sorted(os.listdir(here)):
        if os.path.isdir(os.path.join(here, child)) and not child.startswith((".", "_")):
            visit(src, rel + [child])
for src in glob.glob(os.path.join(sys.argv[1], "packages", "*", "src")) + glob.glob(os.path.join(sys.argv[1], "src")):
    for child in sorted(os.listdir(src)):
        if os.path.isdir(os.path.join(src, child)) and not child.startswith((".", "_")):
            visit(src, [child])
print("\n".join(bad))
'
loc=$("$py" -c "$guard" "$(cygpath -w "$tree")" 2>&1)
if [ -n "$loc" ]; then echo "COPY_IMPORT_MISMATCH $loc"; exit 8; fi
# -n 0 keeps a project's xdist addopts from forking workers; pytest without xdist rejects it.
serial=()
"$py" -c "import xdist" 2>/dev/null && serial=(-n 0)
echo "== $label =="
"$py" -m pytest -p no:cacheprovider "${serial[@]}" -q --basetemp "$(cygpath -w "$scratch/bt")" "$@" 2>&1 | tail -n 15

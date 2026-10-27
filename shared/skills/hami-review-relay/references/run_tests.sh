#!/bin/bash
# run_tests.sh <tree> <scratch> <python> <label> <pytest args...>
# Runs pytest inside <tree> (a `git archive` copy or a worktree) with an isolated
# environment; <scratch> holds the data root and pytest's basetemp, so keep it short.
tree="$(cygpath -u "$1")"; scratch="$(cygpath -u "$2")"; py="$(cygpath -u "$3")"; label="$4"; shift 4
for v in $(env | grep -o '^HAMI_[A-Za-z0-9_]*'); do unset "$v"; done
mkdir -p "$scratch/qdr"
export QUANT_DATA_ROOT="$(cygpath -w "$scratch/qdr")"
pp=""
for d in "$tree"/packages/*/src "$tree"/src; do
  [ -d "$d" ] && pp="$pp;$(cygpath -w "$d")"
done
export PYTHONPATH="${pp#;}"
cd "$tree" || exit 9
[ -e .venv ] || echo "NOTE no .venv in $tree: hami-quant-trading process, notifications and observer tests need a .venv junction to the main checkout's .venv"
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
            bad.append(f"{name}: {where}")
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
echo "== $label =="
"$py" -m pytest -p no:cacheprovider -n 0 -q --basetemp "$(cygpath -w "$scratch/bt")" "$@" 2>&1 | tail -n 15

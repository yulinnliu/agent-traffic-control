#!/bin/bash
# run_tests.sh <scratch> <python> <label> <pytest args...>
# Runs pytest inside <scratch>/tree with an isolated environment.
scratch="$(cygpath -u "$1")"; py="$(cygpath -u "$2")"; label="$3"; shift 3
for v in $(env | grep -o '^HAMI_[A-Za-z0-9_]*'); do unset "$v"; done
mkdir -p "$scratch/qdr"
export QUANT_DATA_ROOT="$(cygpath -w "$scratch/qdr")"
pp=""
for d in "$scratch"/tree/packages/*/src "$scratch"/tree/src; do
  [ -d "$d" ] && pp="$pp;$(cygpath -w "$d")"
done
export PYTHONPATH="${pp#;}"
cd "$scratch/tree" || exit 9
# Import every regular package under the copy's src dirs (descending through
# namespace directories) and require its file to live inside the copy.
guard='
import importlib, os, sys
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
for src in os.environ["PYTHONPATH"].split(os.pathsep):
    for child in sorted(os.listdir(src)):
        if os.path.isdir(os.path.join(src, child)) and not child.startswith((".", "_")):
            visit(src, [child])
print("\n".join(bad))
'
loc=$("$py" -c "$guard" "$(cygpath -w "$scratch/tree")" 2>&1)
if [ -n "$loc" ]; then echo "COPY_IMPORT_MISMATCH $loc"; exit 8; fi
echo "== $label =="
"$py" -m pytest -p no:cacheprovider -n 0 -q "$@" 2>&1 | tail -n 15

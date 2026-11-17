"""Copies kept beside their single source, checked by the lint stage (`.pre-commit-config.yaml`).

usage: drift.py schemas [--write]

`schemas`: `src/atc_cli/schemas/*.json` equal `shared/policies/schemas/*.json` byte for byte, so an installed wheel
validates what the workspace specifies; `--write` copies the canonical files over and removes packaged extras.
Exit 0 current, 1 drift (one line per file), 2 usage error.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ADAPTER = Path(__file__).resolve().parents[1]
CANONICAL = ADAPTER.parents[1] / "shared" / "policies" / "schemas"
PACKAGED = ADAPTER / "src" / "atc_cli" / "schemas"


def schemas(write: bool) -> list[str]:
    canonical = {path.name: path.read_bytes() for path in CANONICAL.glob("*.json")}
    packaged = {path.name: path.read_bytes() for path in PACKAGED.glob("*.json")}
    if not canonical:
        return [f"{CANONICAL}: no schema"]
    names = sorted(set(canonical) | set(packaged))
    drifted = [name for name in names if canonical.get(name) != packaged.get(name)]
    if write:
        for name in drifted:
            if name in canonical:
                shutil.copyfile(CANONICAL / name, PACKAGED / name)
            else:
                (PACKAGED / name).unlink()
        return []
    return [f"{PACKAGED / name}: differs from {CANONICAL / name}; run scripts/drift.py schemas --write"
            for name in drifted]


def main(argv: list[str]) -> int:
    if argv in (["schemas"], ["schemas", "--write"]):
        problems = schemas(write=len(argv) == 2)
    else:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

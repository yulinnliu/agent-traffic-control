"""Copies and version strings kept beside their single source, checked by the lint stage (`.pre-commit-config.yaml`).

usage: drift.py schemas [--write] | drift.py testkit-version

`schemas`: `src/atc_cli/schemas/*.json` equal `shared/policies/schemas/*.json` byte for byte, so an installed wheel
validates what the workspace specifies; `--write` copies the canonical files over and removes packaged extras.
`testkit-version`: `atc_testkit.__version__` is the package's only version and its README names it.
Exit 0 current, 1 drift (one line per file), 2 usage error.
"""
from __future__ import annotations

import re
import shutil
import sys
import tomllib
from pathlib import Path

ADAPTER = Path(__file__).resolve().parents[1]
CANONICAL = ADAPTER.parents[1] / "shared" / "policies" / "schemas"
PACKAGED = ADAPTER / "src" / "atc_cli" / "schemas"
TESTKIT = ADAPTER / "atc-testkit"
VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)


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


def testkit_version() -> list[str]:
    found = VERSION.search((TESTKIT / "src" / "atc_testkit" / "__init__.py").read_text(encoding="utf-8"))
    if found is None:
        return [f"{TESTKIT / 'src/atc_testkit/__init__.py'}: no __version__ line"]
    problems = []
    project = tomllib.loads((TESTKIT / "pyproject.toml").read_text(encoding="utf-8"))
    if "version" in project["project"] or project["project"].get("dynamic") != ["version"] \
            or project["tool"]["setuptools"]["dynamic"].get("version") != {"attr": "atc_testkit.__version__"}:
        problems.append(f"{TESTKIT / 'pyproject.toml'}: the version must be dynamic from atc_testkit.__version__")
    if f"`atc-testkit` {found.group(1)}" not in (TESTKIT / "README.md").read_text(encoding="utf-8"):
        problems.append(f"{TESTKIT / 'README.md'}: does not name `atc-testkit` {found.group(1)}")
    return problems


def main(argv: list[str]) -> int:
    if argv in (["schemas"], ["schemas", "--write"]):
        problems = schemas(write=len(argv) == 2)
    elif argv == ["testkit-version"]:
        problems = testkit_version()
    else:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

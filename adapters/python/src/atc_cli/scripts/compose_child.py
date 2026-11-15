#!/usr/bin/env python3
"""The composing child of the PreToolUse hook (`hook_state.run_child`): read the request, compose the table from
the map and each repository's `main`, and write the result or the failure, keyed (docs/ENFORCEMENT.md § Table)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(argv: list[str]) -> int:
    request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    import generate_enforcement as ge
    import hook_state
    result = ge.compose_result(Path(request["workspace"]), Path(request["home"]), Path(request["atc_home"]),
                               key_out=Path(request["key_out"]))
    hook_state.write_json(Path(request["out"]), result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

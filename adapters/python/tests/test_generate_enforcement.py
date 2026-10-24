"""Generator behaviour outside the conformance cases: hook mode and option rules."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import generate_enforcement

CASE = Path(__file__).resolve().parents[3] / "shared/conformance/generate-enforcement/filesystem-modes/input"


@pytest.mark.skipif(os.name == "nt", reason="Windows has no executable bit")
def test_pre_commit_is_written_executable(tmp_path):
    assert generate_enforcement.main(["--policies", str(CASE), "--out", str(tmp_path)]) == 0
    assert (tmp_path / "git-hooks/pre-commit").stat().st_mode & stat.S_IXUSR


def test_live_options_require_check(tmp_path):
    with pytest.raises(SystemExit) as exc:
        generate_enforcement.main(["--policies", str(CASE), "--out", str(tmp_path), "--live-hook", str(tmp_path / "h")])
    assert exc.value.code == 2

"""Read-only workspace bridge to the canonical opted-in testkit checker."""
from pathlib import Path
import importlib.util

SOURCE = Path(__file__).resolve().parents[1] / 'atc-testkit/src/atc_testkit/check.py'
RECORD = 'vendor/atc-testkit.json'


def record(project: Path) -> Path | None:
    """The release record a project carries."""
    path = project / RECORD
    return path if path.is_file() else None


def problems(project: Path, *, hooks: bool = False) -> list[str]:
    if record(project) is None:
        return []
    spec = importlib.util.spec_from_file_location('_workspace_testkit_contract', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.repository_problems(project, hooks=hooks)

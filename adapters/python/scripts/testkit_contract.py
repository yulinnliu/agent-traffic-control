"""Read-only workspace bridge to the canonical opted-in testkit checker."""
from pathlib import Path
import importlib.util

SOURCE = Path(__file__).resolve().parents[1] / 'hami-testkit/src/hami_testkit/check.py'


def problems(project: Path, *, hooks: bool = False) -> list[str]:
    if not (project / 'vendor/hami-testkit.json').is_file():
        return []
    spec = importlib.util.spec_from_file_location('_workspace_testkit_contract', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.repository_problems(project, hooks=hooks)

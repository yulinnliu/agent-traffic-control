"""Read-only workspace bridge to the canonical opted-in testkit checker."""
from pathlib import Path
import importlib.util
import sys

SOURCE = Path(__file__).resolve().parents[1] / 'atc-testkit/src/atc_testkit/check.py'
RECORDS = ('vendor/atc-testkit.json', 'vendor/hami-testkit.json')


def record(project: Path) -> Path | None:
    """The release record a project carries, the current name first."""
    return next((project / name for name in RECORDS if (project / name).is_file()), None)


def problems(project: Path, *, hooks: bool = False) -> list[str]:
    if record(project) is None:
        return []
    spec = importlib.util.spec_from_file_location('_workspace_testkit_contract', SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass resolves its module while the class body runs
    spec.loader.exec_module(module)
    return module.repository_problems(project, hooks=hooks)

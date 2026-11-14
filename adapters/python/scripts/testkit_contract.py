"""Read-only workspace bridge to the canonical opted-in testkit checker, `atc_testkit.check` as installed with
`atc`, so an installed wheel needs no source file beside it."""
from pathlib import Path

RECORD = 'vendor/atc-testkit.json'


def record(project: Path) -> Path | None:
    """The release record a project carries."""
    path = project / RECORD
    return path if path.is_file() else None


def problems(project: Path, *, hooks: bool = False) -> list[str]:
    if record(project) is None:
        return []
    from atc_testkit import check
    return check.repository_problems(project, hooks=hooks)

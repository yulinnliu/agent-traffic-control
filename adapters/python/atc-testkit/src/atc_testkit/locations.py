"""The ATC home and the rule that places temporary files (docs/ATC_HOME.md § Location, § Resolving the locations)."""
from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile

TMP_VARIABLE = "ATC_TMP"
TEMP_VARIABLES = ("TEMP", "TMP", "TMPDIR")
TMP_NAME_BOUND = 40
UNSUITABLE = "ATC_TMP_UNSUITABLE"
SYSTMP = "systmp"
CANDIDATES = "candidates"


class Refusal(Exception):
    """A refused command: the token and detail it prints and the exit code it returns."""

    def __init__(self, token: str, detail: str, code: int = 9):
        super().__init__(f"{token} {detail}")
        self.token, self.detail, self.code = token, detail, code


@dataclass(frozen=True)
class Locations:
    tmp: Path
    systmp: Path


def _environ(environ: MutableMapping[str, str] | None) -> MutableMapping[str, str]:
    return os.environ if environ is None else environ


def long_name(path: str | os.PathLike[str]) -> str:
    """The absolute form with links resolved and 8.3 names expanded, as pytest sees it."""
    return os.path.realpath(path)


def same_path(left: str | os.PathLike[str], right: str | os.PathLike[str]) -> bool:
    return os.path.normcase(left) == os.path.normcase(right)


def atc_home(environ: MutableMapping[str, str] | None = None) -> Path:
    environ = _environ(environ)
    home = environ.get("USERPROFILE" if os.name == "nt" else "HOME")
    return (Path(home) if home else Path.home()) / ".atc"


def cache_directories(environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """The shared cache variables of the gate and the verifier (docs/ATC_HOME.md § Cache)."""
    cache = atc_home(environ) / "cache"
    return {"UV_CACHE_DIR": str(cache / "uv"), "UV_PYTHON_INSTALL_DIR": str(cache / "python"),
            "RUFF_CACHE_DIR": str(cache / "ruff")}


def _broken_property(root: str, environ: MutableMapping[str, str]) -> str | None:
    """The property of docs/ATC_HOME.md § Location that ``root`` breaks, or ``None``."""
    long = long_name(root)
    parts = [part.lower() for part in Path(long).parts]
    app_data = [environ.get(name) for name in ("APPDATA", "LOCALAPPDATA")]
    if "appdata" in parts or any(value and Path(os.path.normcase(long)).is_relative_to(os.path.normcase(value))
                                 for value in app_data):
        return f"lies inside AppData: {long}"
    for ancestor in (Path(long), *Path(long).parents):
        if (ancestor / ".git").exists():
            return f"has an ancestor that holds .git: {ancestor}"
    if len(long) > TMP_NAME_BOUND:
        return f"its long name has {len(long)} characters, limit {TMP_NAME_BOUND}: {long}"
    return None


def tmp_root(environ: MutableMapping[str, str] | None = None) -> Path:
    """The resolved ``<tmp>``, checked and not created; CI keeps the platform's own temporary location."""
    environ = _environ(environ)
    value = environ.get(TMP_VARIABLE)
    if value and not os.path.isabs(value):
        raise Refusal(UNSUITABLE, f"{TMP_VARIABLE} is not absolute: {value}")
    if not value and environ.get("CI"):
        return Path(long_name(tempfile.gettempdir()))
    root = value or str(atc_home(environ) / "tmp")
    if not environ.get("CI"):
        broken = _broken_property(root, environ)
        if broken:
            raise Refusal(UNSUITABLE, broken)
    return Path(long_name(root))


def locate(environ: MutableMapping[str, str] | None = None) -> Locations:
    """``<tmp>`` and ``<tmp>/systmp``, created on first use."""
    root = tmp_root(environ)
    systmp = root / SYSTMP
    try:
        systmp.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise Refusal(UNSUITABLE, f"cannot be created: {root}: {error}") from error
    return Locations(root, systmp)


def enter(directory: str | os.PathLike[str] | None = None,
          environ: MutableMapping[str, str] | None = None) -> Path:
    """Make a directory below ``<tmp>`` the temporary directory of this process and of every process it starts.

    The directory is ``<tmp>/systmp`` unless the entry point names another. In CI an unnamed directory leaves the
    platform's temporary directory in place.
    """
    environ = _environ(environ)
    if directory is None and environ.get("CI"):
        return Path(tempfile.gettempdir())
    places = locate(environ)
    target = places.systmp if directory is None else Path(directory)
    if not Path(os.path.normcase(long_name(target))).is_relative_to(os.path.normcase(places.tmp)):
        raise ValueError(f"temporary directory outside {places.tmp}: {target}")
    target.mkdir(parents=True, exist_ok=True)
    for name in TEMP_VARIABLES:
        environ[name] = str(target)
    environ[TMP_VARIABLE] = str(places.tmp)
    if environ is os.environ:
        tempfile.tempdir = None
    return target


def temporary_directory_below_tmp(environ: MutableMapping[str, str] | None = None) -> bool:
    """Whether this process's temporary directory already lies strictly below ``<tmp>``."""
    root = os.path.normcase(tmp_root(environ))
    current = os.path.normcase(long_name(tempfile.gettempdir()))
    return current != root and Path(current).is_relative_to(root)

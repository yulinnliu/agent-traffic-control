"""The environment of the processes the gate and the verifier start (docs/TESTKIT.md § Isolated environment)."""
from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
import os
from pathlib import Path
import secrets

from . import locations

CREDENTIAL_SUFFIXES = ("_API_KEY", "_API_SECRET", "_SECRET", "_TOKEN", "_PASSWORD")
WORK_NAME_BOUND = 8
GITCONFIG = "[user]\n\tname = ATC Test\n\temail = atc-test@example.invalid\n[core]\n\tlongpaths = true\n"
XDG_VARIABLES = ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME")
PROFILE_VARIABLES = frozenset({"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", *XDG_VARIABLES,
                               "PYTHONPYCACHEPREFIX"})


def drop_hami_variables(environ: MutableMapping[str, str]) -> None:
    for name in [name for name in environ if name.upper().startswith("HAMI_")]:
        del environ[name]


def create_working_directory(environ: MutableMapping[str, str] | None = None) -> Path:
    """A new directory of at most eight characters under ``<tmp>/systmp``."""
    systmp = locations.locate(environ).systmp
    while True:
        work = systmp / ("w" + secrets.token_hex((WORK_NAME_BOUND - 1) // 2))
        try:
            work.mkdir()
        except FileExistsError:
            continue
        return work


def _removed(name: str, credentials: frozenset[str]) -> bool:
    upper = name.upper()
    return upper.startswith(("GIT_", "HAMI_")) or upper in credentials or upper.endswith(CREDENTIAL_SUFFIXES)


class IsolatedRun:
    """One run's working directory and the environment each process in it gets."""

    def __init__(self, work: Path, *, environ: Mapping[str, str] | None = None, credentials: Iterable[str] = (),
                 data_roots: Iterable[str] = ()):
        self.work = Path(work)
        self.caller = dict(os.environ if environ is None else environ)
        self.credentials = frozenset(name.upper() for name in credentials)
        self.data_roots = tuple(data_roots)
        self.home = self.work / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / ".gitconfig").write_text(GITCONFIG, encoding="utf-8")

    def base_directory(self, stage: str) -> Path:
        return self.work / stage / "bt"

    def data_root(self, stage: str) -> Path:
        return self.work / stage / "data"

    def environment(self, stage: str) -> dict[str, str]:
        """The caller's environment by the rules of the section; ``stage`` names the stage or static check."""
        data = self.data_root(stage)
        pointed = {"HOME": self.home, "USERPROFILE": self.home,
                   "APPDATA": self.home / "AppData" / "Roaming", "LOCALAPPDATA": self.home / "AppData" / "Local",
                   "PYTHONPYCACHEPREFIX": self.work / "pyc",
                   **{name: self.work / name.lower() for name in XDG_VARIABLES},
                   **{name: data for name in self.data_roots}}
        for path in pointed.values():
            path.mkdir(parents=True, exist_ok=True)
        replaced = {name.upper() for name in pointed} if os.name == "nt" else set(pointed)
        env = {name: value for name, value in self.caller.items()
               if not _removed(name, self.credentials)
               and (name.upper() if os.name == "nt" else name) not in replaced}
        env.update({name: str(path) for name, path in pointed.items()})
        env.update(locations.cache_directories(self.caller))
        return env

    def pytest_options(self, stage: str) -> list[str]:
        """The base directory and ``cache_dir`` of one pytest invocation."""
        return ["--basetemp", str(self.base_directory(stage)), "-o", f"cache_dir={self.work / stage / 'cache'}"]

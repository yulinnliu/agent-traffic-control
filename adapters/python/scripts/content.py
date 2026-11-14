"""The content repository: the Git repository `atc` reads delivered content from (docs/LAYOUT.md § The manifest).

With `governance_checkout` in `workspace.yaml`, it is that checkout: its Git objects, never its working tree,
so the branch it is on and its uncommitted changes do not matter and nothing needs a network. Otherwise it is
the bare cache `<ATC home>/content/<owner>/<repo>.git` of `governance_source`, cloned once and fetched on use.
Either way a revision names the same tree, so the SHA is the integrity check.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

GOVERNANCE_CHECKOUT = "governance_checkout"
CONTENT = "content"
_FETCHED: set[str] = set()


class ContentError(ValueError):
    """No content repository can be used; ``token`` names why."""

    def __init__(self, token: str, detail: str):
        super().__init__(f"{token} {detail}")
        self.token = token
        self.detail = detail


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, check=False)


def workspace_doc(root: Path) -> dict:
    """``workspace.yaml`` of the workspace directory ``root`` as a mapping; empty when absent or unreadable."""
    try:
        doc = yaml.safe_load((root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return doc if isinstance(doc, dict) else {}


def governance_checkout(root: Path) -> Path | None:
    """The checkout `governance_checkout` names, resolved against the workspace directory; None when unset."""
    value = workspace_doc(root).get(GOVERNANCE_CHECKOUT)
    if not isinstance(value, str) or not value.strip():
        return None
    return (root / value).resolve()


def same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def is_workspace_checkout(root: Path) -> bool:
    """Whether the workspace directory is itself the governance checkout (`governance_checkout: .`)."""
    checkout = governance_checkout(root)
    return checkout is not None and same(checkout, root.resolve())


def source(root: Path) -> str | None:
    value = workspace_doc(root).get("governance_source")
    return value if isinstance(value, str) and value.count("/") == 1 and "<" not in value else None


def remote_url(name: str) -> str:
    return f"https://github.com/{name}.git"


def cache_dir(name: str, environ: dict[str, str] | None = None) -> Path:
    from atc_testkit.locations import atc_home
    owner, repo = name.split("/")
    return atc_home(environ) / CONTENT / owner / f"{repo}.git"


def repository(root: Path, environ: dict[str, str] | None = None) -> Path:
    """The content repository of the workspace at ``root``; the cache is fetched once per process."""
    checkout = governance_checkout(root)
    if checkout is not None:
        found = _git("-C", str(checkout), "rev-parse", "--git-dir")
        if found.returncode:
            raise ContentError("GOVERNANCE_CHECKOUT_INVALID",
                               f"governance_checkout {checkout} is not a Git checkout ({found.stderr.strip()})")
        return checkout
    name = source(root)
    if name is None:
        raise ContentError("CONTENT_SOURCE_UNKNOWN", "workspace.yaml names neither governance_checkout nor an "
                                                     "<owner>/<repo> governance_source")
    cache = cache_dir(name, environ)
    key = os.path.normcase(str(cache))
    if key in _FETCHED:
        return cache
    if not (cache / "HEAD").is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        done = _git("clone", "--bare", "--filter=blob:none", "--quiet", remote_url(name), str(cache))
    else:
        done = _git("-C", str(cache), "fetch", "--quiet", "--tags", "--force", "origin",
                    "+refs/heads/main:refs/heads/main")
    if done.returncode:
        raise ContentError("CONTENT_FETCH_FAILED", f"{remote_url(name)} into {cache}: "
                                                   f"{(done.stderr or done.stdout).strip()}")
    _FETCHED.add(key)
    return cache

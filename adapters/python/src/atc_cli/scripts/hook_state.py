#!/usr/bin/env python3
"""The PreToolUse hook's own table: its key, its baseline and cache, the ratchet, and the composing child.

Standard library only, since the hook imports it on every call (docs/ENFORCEMENT.md § PreToolUse hook, Table).
A cache hit reads one file and the key's inputs (the map's bytes, each repository's `refs/heads/main`, the
composer's file stats); a miss composes in a child process under a deadline, merges the result into the
baseline, which only `atc guard --install` and `--reset` create, and caches it under its key.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FORMAT = 2
# Below the harness entries' timeout (10 s): a hook still composing then would let a Codex call run unchecked.
DEADLINE = 6.0
KILL_MARGIN = 0.8
OWNER_TIMEOUT = 60.0
ENFORCEMENT = "enforcement"
LEGACY = "pretooluse.json"
MAX_POLICY_BYTES = 256 * 1024
SCRIPTS = Path(__file__).resolve().parent
SCHEMA = SCRIPTS.parent / "schemas" / "hard-deny.schema.json"
COMPOSER_FILES = ("generate_enforcement.py", "policies.py", "globs.py", "shell_words.py", "write_targets.py",
                  "pretooluse_hook.py", "hook_state.py", "compose_child.py")
CHILD = SCRIPTS / "compose_child.py"
HOOK_ONLY = ("with_args", "unless_args", "push_targets", "background")
SHA = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?")
# What a composing git sees of the caller's environment; every GIT_* variable stays out.
GIT_KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "LANG")
FIX = "the Owner runs atc doctor from their own terminal for the cause"


class Failure(Exception):
    """The table cannot be composed or read: a token, its detail, and whether the cache keeps it by key."""

    def __init__(self, token: str, detail: str, cached: bool = True):
        super().__init__(f"{token}: {detail}")
        self.token = token
        self.detail = detail
        self.cached = cached

    def as_dict(self) -> dict:
        return {"token": self.token, "detail": self.detail}


def deny_reason(failure: Failure) -> str:
    return (f"Refused: the PreToolUse hook cannot compose its table: {failure.token}: {failure.detail} "
            f"Every session is blocked until this is fixed; {FIX}.")


# Locations


def map_id(workspace: Path) -> str:
    """`<m>`: the state files of one map are named for its absolute path, case-folded."""
    return hashlib.sha256(os.path.normcase(os.path.abspath(str(workspace))).encode("utf-8")).hexdigest()[:16]


def state_paths(atc_home: Path, workspace: Path) -> dict[str, Path]:
    directory = Path(atc_home) / ENFORCEMENT
    m = map_id(workspace)
    return {"dir": directory, "baseline": directory / f"baseline-{m}.json", "cache": directory / f"table-{m}.json",
            "lock": directory / f"compose-{m}.lock", "legacy": directory / LEGACY}


def read_json(path: Path) -> object:
    """The parsed file; raises OSError or ValueError."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    """Replace ``path`` in one step; a reader holding it open on Windows makes the replace retry briefly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        for attempt in range(50):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.02)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def remove(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


# Git


def git_env(home: Path) -> dict[str, str]:
    """A git environment built from nothing: no GIT_* variable of the caller, no system config, no prompt and
    no lazy fetch of a partial clone."""
    env = {name: os.environ[name] for name in GIT_KEEP if name in os.environ}
    env.update({"HOME": str(home), "USERPROFILE": str(home), "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"})
    return env


def git(repo: Path, args: list[str], home: Path, timeout: float = 30, data: bytes | None = None
        ) -> subprocess.CompletedProcess:
    """`git --no-replace-objects -C <repo> ...`, its output captured here."""
    return subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *args], input=data, capture_output=True,
                          env=git_env(home), timeout=timeout, check=False)


def git_dir(repo: Path) -> Path | None:
    """The repository's own Git directory when it can be read as files: `.git` as a directory, or a bare
    repository; None when `.git` is a `gitdir:` file or absent."""
    dot = repo / ".git"
    if dot.is_dir():
        return dot
    if not dot.exists() and (repo / "HEAD").is_file() and (repo / "objects").is_dir():
        return repo
    return None


def _ref_storage(directory: Path) -> bool:
    try:
        text = (directory / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return re.search(r"^\s*refstorage\s*=", text, re.IGNORECASE | re.MULTILINE) is not None


def main_sha(repo: Path, home: Path) -> str | None:
    """The commit `refs/heads/main` names: the loose ref, else `packed-refs`; `git rev-parse` only for a `gitdir:`
    file, a declared `extensions.refStorage` or a symbolic ref. None when there is none."""
    directory = git_dir(repo)
    if directory is not None and not _ref_storage(directory):
        try:
            loose = (directory / "refs" / "heads" / "main").read_text(encoding="ascii", errors="replace").strip()
        except OSError:
            loose = None
        if loose is not None and SHA.fullmatch(loose):
            return loose.lower()
        if loose is None:
            try:
                packed = (directory / "packed-refs").read_text(encoding="ascii", errors="replace")
            except OSError:
                return None
            for line in packed.splitlines():
                sha, _, name = line.partition(" ")
                if name.strip() == "refs/heads/main" and SHA.fullmatch(sha):
                    return sha.lower()
            return None
    if not repo.is_dir():
        return None
    try:
        found = git(repo, ["rev-parse", "--verify", "-q", "refs/heads/main^{commit}"], home, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = found.stdout.decode("ascii", "replace").strip()
    return sha.lower() if found.returncode == 0 and SHA.fullmatch(sha) else None


# Key


def file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return "missing"


def composer() -> str:
    """The running `atc`'s composing files by name, size and modification time: a new install changes it."""
    digest = hashlib.sha256()
    for path in [SCRIPTS / name for name in COMPOSER_FILES] + [SCHEMA]:
        try:
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
        except OSError:
            digest.update(f"{path.name}:missing\n".encode())
    return digest.hexdigest()


def source_value(source: dict, home: Path) -> str | None:
    """A source's part of the key: the `main` commit of a Git source, the bytes' hash of each file of a file
    source."""
    path = Path(source["path"])
    if source["kind"] == "git":
        return main_sha(path, home)
    return ",".join(file_hash(path / name) for name in source.get("files", ()))


def make_key(workspace: Path, map_hash: str, sources: list[dict] | None, env_names: list[str] | None,
             home: Path) -> dict:
    """The cache key; ``sources`` None is the key of a composition that never read the map's sources."""
    key: dict = {"format": FORMAT, "map": map_hash, "composer": composer()}
    if sources is not None:
        key["sources"] = [[s["label"], s["kind"], str(s["path"]), source_value(s, home)] for s in sources]
        key["env"] = {name: os.environ.get(name) for name in env_names or []}
    return key


# Ratchet


def command_key(rule: dict) -> str:
    return json.dumps({k: rule[k] for k in ("argv_prefix", *HOOK_ONLY) if k in rule}, sort_keys=True)


def filesystem_key(rule: dict) -> str:
    return json.dumps([rule["glob"], rule["modes"], rule["scope"]])


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("\\") + "\\")


def ratchet(base: dict | None, fresh: dict) -> tuple[dict, dict]:
    """``base`` ⊕ ``fresh`` and what ``base`` keeps that ``fresh`` lacks. Rule lists and the cd and protected roots
    are unions (fresh first); the write roots, an allowlist, are an intersection. With no ``base``, ``fresh``."""
    if base is None:
        return fresh, {"rules": [], "write_roots": []}
    merged = dict(fresh)
    retained: list[str] = []
    for field, key in (("commands", command_key), ("filesystem", filesystem_key)):
        seen = {key(rule) for rule in fresh[field]}
        kept = [rule for rule in base[field] if key(rule) not in seen]
        merged[field] = [*fresh[field], *kept]
        retained += [f"{rule['id']} ({rule.get('source', 'policy')})" for rule in kept]
    for field in ("worktrees", "protected"):
        merged[field] = [*fresh[field], *(p for p in base[field] if p not in fresh[field])]
    roots = [r for r in fresh["write_roots"] if any(_within(r, b) for b in base["write_roots"])]
    roots += [b for b in base["write_roots"] if b not in roots and any(_within(b, r) for r in fresh["write_roots"])]
    merged["write_roots"] = roots
    waiting = [r for r in fresh["write_roots"] if r not in roots]
    return merged, {"rules": retained, "write_roots": waiting}


def valid_table(table: object) -> bool:
    return (isinstance(table, dict) and table.get("version") == 1
            and all(isinstance(table.get(k), list)
                    for k in ("commands", "filesystem", "worktrees", "write_roots", "protected")))


def read_baseline(path: Path) -> dict:
    """The baseline's table; Failure BASELINE_MISSING or BASELINE_INVALID, never cached."""
    try:
        record = read_json(path)
    except FileNotFoundError:
        raise Failure("BASELINE_MISSING", f"{path} is missing; the Owner runs atc guard --reset.",
                      cached=False) from None
    except (OSError, ValueError) as exc:
        raise Failure("BASELINE_INVALID", f"{path} cannot be read ({exc}); the Owner runs atc guard --reset.",
                      cached=False) from None
    if not isinstance(record, dict) or record.get("version") != FORMAT or not valid_table(record.get("table")):
        raise Failure("BASELINE_INVALID", f"{path} is not a version {FORMAT} baseline; the Owner runs "
                                          "atc guard --reset.", cached=False)
    return record


# Lock


class Lock:
    """One locked byte of the lock file, released by the OS when its holder ends."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = open(path, "a+b")  # noqa: SIM115 - held for the lock's life
        self.held = False

    def acquire(self, until: float) -> bool:
        while True:
            try:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.held = True
                return True
            except OSError:
                if time.monotonic() >= until:
                    return False
                time.sleep(0.05)

    def release(self) -> None:
        try:
            if self.held:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.held = False
            self.stream.close()


# The composing child


def _kill_tree(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def run_child(workspace: Path, home: Path, atc_home: Path, timeout: float, child: Path = CHILD) -> dict:
    """Compose in a child process within ``timeout`` seconds; its result, else Failure COMPOSE_TIMEOUT (keyed by
    what the child wrote of its key) or COMPOSE_FAILED. Output goes to files, never pipes, so a grandchild
    holding a handle cannot hold the wait past the timeout."""
    with tempfile.TemporaryDirectory(prefix="compose-", dir=_temp_parent(atc_home),
                                     ignore_cleanup_errors=True) as scratch:
        out, key_out, err = Path(scratch) / "out.json", Path(scratch) / "key.json", Path(scratch) / "err.txt"
        request = Path(scratch) / "request.json"
        request.write_text(json.dumps({"workspace": str(workspace), "home": str(home), "atc_home": str(atc_home),
                                       "out": str(out), "key_out": str(key_out)}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        options: dict = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        with open(err, "wb") as errors:
            process = subprocess.Popen([sys.executable, "-I", str(child), str(request)], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=errors, env=env, **options)
            try:
                code = process.wait(timeout=max(timeout, 0.1))
            except subprocess.TimeoutExpired:
                _kill_tree(process)
                key = _read_or_none(key_out)
                raise TimedOut(key, f"composing took longer than {timeout:.1f} s") from None
        result = _read_or_none(out)
        if not isinstance(result, dict):
            detail = err.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:] if err.exists() else []
            raise ComposeCrashed(_read_or_none(key_out),
                                 f"the composing process exited {code} without a result ({' '.join(detail)})")
        return result


class TimedOut(Failure):
    def __init__(self, key: object, detail: str):
        super().__init__("COMPOSE_TIMEOUT", detail)
        self.key = key


class ComposeCrashed(Failure):
    def __init__(self, key: object, detail: str):
        super().__init__("COMPOSE_FAILED", detail)
        self.key = key


def _temp_parent(atc_home: Path) -> Path:
    path = Path(atc_home) / ENFORCEMENT
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_or_none(path: Path) -> object:
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


# The hook's table


def _hit(record: object, workspace: Path, home: Path) -> dict | None:
    """The cached table when the record's key equals the current one; Failure for a cached failure."""
    if not isinstance(record, dict) or record.get("version") != FORMAT or not isinstance(record.get("key"), dict):
        return None
    sources = record.get("sources")
    if sources is not None and not isinstance(sources, list):
        return None
    current = make_key(workspace, file_hash(workspace), sources, record.get("env_names"), home)
    if current != record["key"]:
        return None
    failure = record.get("failure")
    if isinstance(failure, dict):
        raise Failure(str(failure.get("token")), str(failure.get("detail")))
    table = record.get("table")
    return table if valid_table(table) else None


def cache_record(result: dict, table: dict | None = None, retained: dict | None = None,
                 failure: Failure | None = None) -> dict:
    return {"version": FORMAT, "key": result.get("key"), "sources": result.get("sources"),
            "env_names": result.get("env_names"), "inputs": result.get("inputs"), "table": table,
            "retained": retained, "failure": failure.as_dict() if failure else None, "atc": result.get("atc")}


def _failure_key(partial: object) -> dict | None:
    """The key a timed-out or crashed composition is cached under: the one the child wrote, with its sources. None
    when it wrote none: a key of the map alone would hold the failure after a fix on a repository's `main`."""
    if isinstance(partial, dict) and isinstance(partial.get("key"), dict):
        return partial
    return None


def table_for(workspace: Path, home: Path, atc_home: Path, until: float, child: Path = CHILD) -> dict:
    """The hook's table for ``workspace``: the cached one under the current key, else composed now and merged
    into the baseline. Raises Failure (docs/ENFORCEMENT.md § PreToolUse hook, Failure)."""
    paths = state_paths(atc_home, workspace)
    hit = _hit(_read_or_none(paths["cache"]), workspace, home)
    if hit is not None:
        return hit
    lock = Lock(paths["lock"])
    try:
        if not lock.acquire(until):
            raise Failure("COMPOSE_BUSY", "another process is composing the table; retry the call.", cached=False)
        hit = _hit(_read_or_none(paths["cache"]), workspace, home)
        if hit is not None:
            return hit
        base = read_baseline(paths["baseline"])
        try:
            result = run_child(workspace, home, atc_home, until - time.monotonic() - KILL_MARGIN, child)
        except (TimedOut, ComposeCrashed) as failure:
            keyed = _failure_key(failure.key)
            if keyed is not None:
                write_json(paths["cache"], cache_record(keyed, failure=failure))
            raise
        if isinstance(result.get("failure"), dict):
            failure = Failure(str(result["failure"].get("token")), str(result["failure"].get("detail")))
            write_json(paths["cache"], cache_record(result, failure=failure))
            raise failure
        merged, retained = ratchet(base["table"], result["table"])
        if merged != base["table"]:
            write_json(paths["baseline"], {**base, "table": merged, "inputs": result.get("inputs"),
                                           "written_by": "hook", "at": _now()})
        write_json(paths["cache"], cache_record(result, merged, retained))
        return merged
    finally:
        lock.release()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

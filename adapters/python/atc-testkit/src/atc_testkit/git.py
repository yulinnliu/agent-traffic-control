"""Git input handling for repository-local test gates."""
from __future__ import annotations
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass


def git_env() -> dict[str, str]:
    """Environment for Git and test subprocesses without repository-selecting hook variables."""
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}


class SelectionError(RuntimeError):
    pass


def is_zero(oid: str) -> bool:
    return set(oid) == {"0"}


def has_commit(git: Callable[..., str], oid: str) -> bool:
    try:
        git("cat-file", "-e", f"{oid}^{{commit}}")
    except SelectionError:
        return False
    return True


def push_ranges(push_lines: Iterable[str], git: Callable[..., str], *,
                missing_remote: str = "full") -> list[tuple[str, str]] | None:
    """Revision ranges ``(base, local)`` covered by the pushed refs, or ``None`` when unknowable."""
    if missing_remote not in {"full", "merge-base"}:
        raise ValueError("unknown missing-remote policy")
    ranges: list[tuple[str, str]] = []
    for line in push_lines:
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 4:
            raise SelectionError(f"malformed pre-push input line: {line!r}")
        _local_ref, local_oid, _remote_ref, remote_oid = fields
        if is_zero(local_oid):
            continue  # branch deletion
        missing = not is_zero(remote_oid) and not has_commit(git, remote_oid)
        if missing and missing_remote == "full":
            return None
        if is_zero(remote_oid) or missing:
            # New branch, or a force-push over a commit this clone never fetched.
            try:
                base = git("merge-base", local_oid, "origin/main").strip()
            except SelectionError:
                return None
            if not base:
                return None
        else:
            base = remote_oid
        ranges.append((base, local_oid))
    return ranges


def changed_paths(push_lines: Iterable[str], git: Callable[..., str], *,
                  missing_remote: str = "full") -> list[str] | None:
    """Paths changed by the pushed refs, or ``None`` when no range is knowable."""
    ranges = push_ranges(push_lines, git, missing_remote=missing_remote)
    if ranges is None:
        return None
    paths: set[str] = set()
    for base, local_oid in ranges:
        out = git("diff", "--name-only", "--no-renames", f"{base}..{local_oid}")
        paths.update(p for p in out.splitlines() if p.strip())
    return sorted(paths)


@dataclass(frozen=True)
class BaselineDelta:
    """Net effect of a push on one unmarked-test baseline file."""
    added: bool = False
    removed_files: frozenset[str] = frozenset()


def baseline_deltas(push_lines: Iterable[str], git: Callable[..., str], baseline_files: Iterable[str], *,
                    missing_remote: str = "full") -> dict[str, BaselineDelta] | None:
    """Added/removed baseline entries per changed baseline file; ``None`` when no range is knowable.

    A baseline file that the push does not touch has no entry. Removed entries
    report the test file part of each nodeid.
    """
    names = sorted(set(baseline_files))
    ranges = push_ranges(push_lines, git, missing_remote=missing_remote)
    if ranges is None:
        return None
    added: dict[str, bool] = {}
    removed: dict[str, set[str]] = {}
    for base, local_oid in ranges:
        for name in names:
            out = git("diff", "--unified=0", "--no-renames", f"{base}..{local_oid}", "--", name)
            for line in out.splitlines():
                if line.startswith(("+++", "---")) or not line[:1] in {"+", "-"}:
                    continue
                entry = line[1:].strip()
                if not entry or entry.startswith("#"):
                    continue
                if line[0] == "+":
                    added[name] = True
                else:
                    removed.setdefault(name, set()).add(entry.split("::", 1)[0])
    touched = set(added) | set(removed)
    for base, local_oid in ranges:
        for name in names:
            if name not in touched and git("diff", "--name-only", "--no-renames", f"{base}..{local_oid}", "--", name).strip():
                touched.add(name)
    return {name: BaselineDelta(added.get(name, False), frozenset(removed.get(name, ()))) for name in touched}


def verify_push_checkout(push_lines: Iterable[str], git: Callable[..., str]) -> None:
    """Require the tested tree to be the commit being published."""
    commits=[]
    for line in push_lines:
        fields=line.split()
        if not fields:
            continue
        if len(fields)!=4:
            raise SelectionError(f"malformed pre-push input line: {line!r}")
        if not is_zero(fields[1]):
            commits.append(git("rev-parse", f"{fields[1]}^{{commit}}").strip())
    if commits:
        if any(commit!=git("rev-parse", "HEAD").strip() for commit in commits):
            raise SelectionError("check out the commit being pushed before running its tests")
        if git("status", "--porcelain", "--untracked-files=normal").strip():
            raise SelectionError("commit or set aside checkout changes before pushing")

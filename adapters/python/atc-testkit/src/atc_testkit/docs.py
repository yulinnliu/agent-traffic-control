"""Relative links of Markdown files, a hook of the lint stage (docs/TESTKIT.md § Documentation check).

``python -m atc_testkit.docs --tracked`` checks every Markdown file Git tracks, so a deleted or renamed target
breaks its links in any file; ``python -m atc_testkit.docs [--anchors] <path>...`` checks the files given.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from .git import git_env

FENCE = re.compile(r"(?ms)^ {0,3}(`{3,}|~{3,}).*?(?:^ {0,3}\1[^\n]*$|\Z)")
INLINE_CODE = re.compile(r"(`+)(?!`).+?(?<!`)\1(?!`)", re.S)
INLINE_LINK = re.compile(r"!?\[(?:[^\]\\]|\\.)*\]\(\s*(<[^>\n]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
REFERENCE = re.compile(r"(?m)^ {0,3}\[[^\]\n]+\]:[ \t]*(<[^>\n]*>|\S+)")
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
HEADING = re.compile(r"(?m)^ {0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$")
HTML_ANCHOR = re.compile(r"""<a\s[^>]*?\b(?:id|name)\s*=\s*["']([^"']+)["']""", re.I)
SLUG_DROP = re.compile(r"[^\w\- ]", re.U)
MARKDOWN_SUFFIX = ".md"


@dataclass(frozen=True)
class Target:
    line: int
    raw: str


def _blank(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def without_code(text: str) -> str:
    """``text`` with fenced blocks and inline code blanked, line breaks kept."""
    return INLINE_CODE.sub(_blank, FENCE.sub(_blank, text))


def link_targets(text: str) -> list[Target]:
    """Each link target outside code: inline links, images and reference definitions, with its line."""
    body = without_code(text.replace("\r\n", "\n"))
    found = [(match.start(1), match.group(1))
             for pattern in (INLINE_LINK, REFERENCE) for match in pattern.finditer(body)]
    return [Target(body.count("\n", 0, start) + 1, raw.removeprefix("<").removesuffix(">"))
            for start, raw in sorted(found)]


def slugs(text: str) -> set[str]:
    """GitHub's anchors of a Markdown text: one per heading, a repeated one numbered, and HTML ``id``/``name``.

    A heading keeps its inline code, whose text is part of the anchor; a fenced block holds no heading.
    """
    body = FENCE.sub(_blank, text.replace("\r\n", "\n"))
    seen: dict[str, int] = {}
    anchors = set(HTML_ANCHOR.findall(body))
    for title in HEADING.findall(body):
        slug = SLUG_DROP.sub("", title.strip().lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def excluded(name: str, exclude: Iterable[str]) -> bool:
    """Whether the repo-relative ``name`` matches a glob of ``exclude``; ``*`` also matches ``/``, case counts."""
    posix = PurePosixPath(name.replace("\\", "/")).as_posix()
    return any(fnmatchcase(posix, glob) for glob in exclude)


def relative_link_problems(root: Path, files: Iterable[str], *, anchors: bool = False,
                           allow_outside: bool = False, exclude: Sequence[str] = ()) -> list[str]:
    """``path:line: message`` for each relative link of ``files`` that leaves ``root`` or names nothing.

    ``files`` are relative to ``root``. A link starting with ``/`` is read from ``root``. A file that is missing,
    not UTF-8 or empty is a problem too. With ``anchors``, a ``#fragment`` must name a heading or HTML anchor of the
    Markdown file it points into, the same file for a bare ``#fragment``. With ``allow_outside``, a link that leaves
    ``root``, into a sibling repository for example, is not checked. A file matching a glob of ``exclude`` is not
    read; links into it are checked as any other.
    """
    root = Path(root).resolve()
    problems: list[str] = []
    cache: dict[Path, set[str]] = {}
    for name in files:
        if excluded(name, exclude):
            continue
        path = root / name
        if not path.is_file():
            problems.append(f"{name}: Markdown file is missing")
            continue
        try:
            text = _read(path)
        except (OSError, UnicodeError) as error:
            problems.append(f"{name}: cannot read UTF-8 Markdown: {error}")
            continue
        if not text.strip():
            problems.append(f"{name}: Markdown file is empty")
        for target in link_targets(text):
            if SCHEME.match(target.raw):
                continue
            parts = urlsplit(target.raw)
            local = unquote(parts.path)
            where = f"{name}:{target.line}"
            if not local:
                resolved = path
            else:
                resolved = (root / local.lstrip("/") if local.startswith("/") else path.parent / local).resolve()
                if not resolved.is_relative_to(root):
                    if not allow_outside:
                        problems.append(f"{where}: relative link escapes the tree: {target.raw}")
                    continue
                if not resolved.exists():
                    problems.append(f"{where}: broken relative link: {target.raw}")
                    continue
            if anchors and parts.fragment and resolved.is_file() and resolved.suffix.lower() == MARKDOWN_SUFFIX:
                if resolved not in cache:
                    try:
                        cache[resolved] = slugs(_read(resolved))
                    except (OSError, UnicodeError):
                        cache[resolved] = set()
                if unquote(parts.fragment) not in cache[resolved]:
                    problems.append(f"{where}: missing anchor: {target.raw}")
    return problems


def _git(root: Path, *args: str) -> bytes:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, env=git_env(), stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def _nul(raw: bytes) -> list[str]:
    return [item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item]


def tracked_markdown(root: Path) -> list[str]:
    """Every Markdown file Git tracks under ``root``, sorted."""
    return sorted(_nul(_git(root, "ls-files", "-z", "--", "*.md", "*.MD")))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m atc_testkit.docs", allow_abbrev=False,
                                     description="Check the relative links of Markdown files.",
                                     epilog="Exit: 0 when every link resolves, 1 for a problem, 2 for a usage error.")
    parser.add_argument("--tracked", action="store_true", help="every Markdown file Git tracks")
    parser.add_argument("--anchors", action="store_true", help="also check #fragments against headings")
    parser.add_argument("--allow-outside", action="store_true", help="leave links that leave the tree unchecked")
    parser.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                        help="never read a Markdown file whose repo-relative POSIX path matches GLOB as a link "
                             "source; links into it are still checked; repeatable; * also matches /, so a broad GLOB "
                             "such as *.md turns the check off: name exact files or the narrowest directory; a GLOB "
                             "that matches no file is not an error, so a stale one stays until a reader removes it")
    parser.add_argument("paths", nargs="*", help="Markdown files relative to the working directory")
    args = parser.parse_args(argv)
    root = Path.cwd()
    if args.tracked == bool(args.paths):
        parser.error("give --tracked or paths, not both")
    paths = tracked_markdown(root) if args.tracked else args.paths
    problems = relative_link_problems(root, paths, anchors=args.anchors, allow_outside=args.allow_outside,
                                      exclude=args.exclude)
    out = "".join(f"{problem}\n" for problem in problems)
    sys.stdout.buffer.write(out.encode("utf-8"))
    sys.stdout.flush()
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

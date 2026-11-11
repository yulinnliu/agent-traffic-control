"""Relative links of Markdown files: the documentation check a verifier runs (docs/TESTKIT.md § Documentation check).

``python -m atc_testkit.docs --range`` checks the range the verifier's ``ATC_*`` inputs name;
``python -m atc_testkit.docs [--anchors] <path>...`` checks the files given.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from .git import git_env
from .project import StaticCheck

FENCE = re.compile(r"(?ms)^ {0,3}(`{3,}|~{3,}).*?(?:^ {0,3}\1[^\n]*$|\Z)")
INLINE_CODE = re.compile(r"(`+)(?!`).+?(?<!`)\1(?!`)", re.S)
INLINE_LINK = re.compile(r"!?\[(?:[^\]\\]|\\.)*\]\(\s*(<[^>\n]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
REFERENCE = re.compile(r"(?m)^ {0,3}\[[^\]\n]+\]:[ \t]*(<[^>\n]*>|\S+)")
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
HEADING = re.compile(r"(?m)^ {0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$")
HTML_ANCHOR = re.compile(r"""<a\s[^>]*?\b(?:id|name)\s*=\s*["']([^"']+)["']""", re.I)
SLUG_DROP = re.compile(r"[^\w\- ]", re.U)
MARKDOWN_SUFFIX = ".md"
TOKEN = "DOCUMENTATION_REFERENCE_FAILED"
RANGE_VARIABLES = ("ATC_EXPECTED_MAIN_SHA", "ATC_INTEGRATION_SHA")

# The verifier's documentation check: the relative links of the range's Markdown.
DOCUMENTATION_CHECK = StaticCheck("documentation", TOKEN, (sys.executable, "-m", "atc_testkit.docs", "--range"))


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


def markdown_to_check(root: Path, base: str, head: str) -> list[str]:
    """The Markdown files whose links the range can break: the ones it adds or changes, or every tracked one when it
    deletes a path."""
    fields = _nul(_git(root, "diff", "--name-status", "-z", "--no-renames", f"{base}..{head}"))
    changes = list(zip(fields[::2], fields[1::2]))
    if any(status.startswith("D") for status, _path in changes):
        return sorted(_nul(_git(root, "ls-files", "-z", "--", "*.md", "*.MD")))
    return sorted(path for status, path in changes if path.lower().endswith(MARKDOWN_SUFFIX))


def documentation_problems(root: Path, base: str, head: str, *, anchors: bool = False,
                           allow_outside: bool = False, exclude: Sequence[str] = ()) -> list[str]:
    return relative_link_problems(root, markdown_to_check(root, base, head), anchors=anchors,
                                  allow_outside=allow_outside, exclude=exclude)


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m atc_testkit.docs", allow_abbrev=False,
                                     description="Check the relative links of Markdown files.",
                                     epilog="Exit: 0 when every link resolves, 1 for a problem, 2 for a usage error.")
    parser.add_argument("--range", action="store_true",
                        help="the Markdown the range ATC_EXPECTED_MAIN_SHA..ATC_INTEGRATION_SHA can break")
    parser.add_argument("--anchors", action="store_true", help="also check #fragments against headings")
    parser.add_argument("--allow-outside", action="store_true", help="leave links that leave the tree unchecked")
    parser.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                        help="never read a Markdown file whose repo-relative POSIX path matches GLOB as a link "
                             "source; links into it are still checked; repeatable; * also matches /, so a broad GLOB "
                             "such as *.md turns the check off: name exact files or the narrowest directory; a GLOB "
                             "that matches no file is not an error, so a stale one stays until a reader removes it; "
                             "a project's integration verifier appends it to docs.DOCUMENTATION_CHECK's argv")
    parser.add_argument("paths", nargs="*", help="Markdown files relative to the working directory")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    root = Path.cwd()
    if args.range == bool(args.paths):
        parser.error("give --range or paths, not both")
    rules = {"anchors": args.anchors, "allow_outside": args.allow_outside, "exclude": args.exclude}
    if args.range:
        missing = [name for name in RANGE_VARIABLES if not environ.get(name)]
        if missing:
            parser.error(f"--range needs {', '.join(missing)}")
        problems = documentation_problems(root, *(environ[name] for name in RANGE_VARIABLES), **rules)
    else:
        problems = relative_link_problems(root, args.paths, **rules)
    out = "".join(f"{problem}\n" for problem in problems)
    sys.stdout.buffer.write(out.encode("utf-8"))
    sys.stdout.flush()
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

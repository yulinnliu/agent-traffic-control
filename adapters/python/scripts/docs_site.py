"""The docs site: stage the repository's docs, build them with Zensical, check the built site.

docs/WORKFLOW.md § Docs site; design docs/design/ATC-0060.md. Usage, from the workspace root::

    python adapters/python/scripts/docs_site.py check            # stage, build and check in a temporary directory
    python adapters/python/scripts/docs_site.py build <output>   # the same, keeping <output>/site for publication

Each published document is read from its one place in the repository; staging rewrites its links for the site and
adds front matter, and nothing it writes is committed.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from atc_testkit.docs import SCHEME, heading_anchors, link_spans, without_code

ROOT = Path(__file__).resolve().parents[3]
CONFIG = "site/zensical.toml"
OVERRIDES = "site/overrides"
TOKEN_VARIABLE = "GOOGLE_SITE_VERIFICATION"
# What the site publishes: a tracked file whose directory is the pattern's and whose name matches it, and the
# root its site path is taken from. Everything else stays on GitHub, where links to it are sent.
PUBLISHED = (
    ("README.md", ""),
    ("README.*.md", ""),
    ("SCOPE.md", ""),
    ("GLOSSARY.md", ""),
    ("docs/*.md", "docs"),
    ("docs/releases/*.md", "docs"),
    ("docs/assets/*", "docs"),
)
README = re.compile(r"README(?:\.([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)?))?\.md")
OPEN_GRAPH = ("og:type", "og:site_name", "og:title", "og:description", "og:url", "og:image", "twitter:card")
DESCRIPTION_LENGTH = 160
TITLE_LENGTH = 70
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[((?:[^\]\\]|\\.)*)\]\([^)]*\)")
TAG = re.compile(r"<[^>]+>")
H1 = re.compile(r"(?m)^ {0,3}#[ \t]")
BLOCK = re.compile(r"(?s)\S.*?(?=\n[ \t]*\n|\Z)")


class SiteError(Exception):
    """A document or a built page breaks a rule of the site."""


def site_path(source: str, root: str) -> str:
    """The site path of ``source``: relative to its root, file name lower case with ``_`` as ``-``; READMEs are indexes."""
    relative = PurePosixPath(source).relative_to(root) if root else PurePosixPath(source)
    readme = README.fullmatch(relative.name)
    if readme:
        language = readme.group(1)
        return str(relative.parent / language / "index.md" if language else relative.parent / "index.md").lstrip("./")
    return str(relative.parent / relative.name.lower().replace("_", "-")).removeprefix("./")


def published(tracked: Iterable[str]) -> dict[str, str]:
    """Each published source with its site path; two sources on one site path stop."""
    paths: dict[str, str] = {}
    owners: dict[str, str] = {}
    for source in sorted(tracked):
        parent, _, name = source.rpartition("/")
        for pattern, root in PUBLISHED:
            pattern_parent, _, pattern_name = pattern.rpartition("/")
            if parent == pattern_parent and fnmatchcase(name, pattern_name):
                target = site_path(source, root)
                if target in owners:
                    raise SiteError(f"{source} and {owners[target]} both publish to {target}")
                owners[target] = source
                paths[source] = target
                break
    return paths


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True).stdout
    return [os.fsdecode(name) for name in out.split(b"\0") if name]


def page_url(site_url: str, path: str) -> str:
    """The canonical URL of a staged page: directory URLs, an index at its directory."""
    stem = path.removesuffix(".md")
    if stem == "index" or stem.endswith("/index"):
        stem = stem.removesuffix("index")
    else:
        stem += "/"
    return site_url + stem


@dataclass
class Repository:
    """What staging resolves links against: the tracked files, the published ones and the GitHub URL of the rest."""
    tracked: set[str]
    pages: dict[str, str]
    repo_url: str
    directories: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.directories = {str(parent) for name in self.tracked for parent in PurePosixPath(name).parents} - {"."}

    def rewrite(self, source: str, raw: str) -> str:
        """``raw``, a link of ``source``, as the site needs it; a link to no tracked path stops."""
        if SCHEME.match(raw) or raw.startswith(("#", "//")):
            return raw
        path, hash_, fragment = raw.partition("#")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(path)))
        if target.startswith("../") or target == "..":
            raise SiteError(f"{source}: {raw} leaves the repository")
        anchor = hash_ + fragment
        if target in self.pages:
            here = posixpath.dirname(self.pages[source]) or "."
            return posixpath.relpath(self.pages[target], here) + anchor
        if target in self.tracked:
            return f"{self.repo_url}/blob/main/{target}{anchor}"
        if target in self.directories:
            return f"{self.repo_url}/tree/main/{target}{anchor}"
        raise SiteError(f"{source}: {raw} names {target}, which Git does not track")


def rewrite_links(source: str, text: str, repository: Repository) -> str:
    """``text`` with every link outside code rewritten for the site; code keeps its links as written."""
    body = text.replace("\r\n", "\n")
    for span in reversed(link_spans(body)):
        new = repository.rewrite(source, span.raw)
        if new != span.raw:
            written = f"<{new}>" if body[span.start] == "<" else new
            body = body[:span.start] + written + body[span.end:]
    return body


def plain(block: str) -> str:
    """A Markdown block as plain text: images dropped, links as their text, emphasis, code marks and tags removed."""
    text = LINK.sub(r"\1", IMAGE.sub("", block))
    text = TAG.sub("", text).replace("**", "").replace("`", "")
    return " ".join(text.split())


def paragraphs(text: str) -> list[str]:
    """The prose blocks of ``text`` in order: no heading, table, code, or block of only images and links."""
    found = []
    masked = without_code(text)  # the same length as text, code blanked
    for block in BLOCK.finditer(masked):
        stripped = block.group(0).strip()
        if not stripped or stripped.startswith(("#", "|", "<", "---")):
            continue
        if not plain(LINK.sub("", IMAGE.sub("", stripped))).strip(" *_-"):
            continue
        found.append(plain(text[block.start():block.end()]))
    return [paragraph for paragraph in found if paragraph]


def shorten(text: str, limit: int) -> str:
    """``text`` cut at a word boundary to at most ``limit`` characters, an ellipsis marking the cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "…"


def duplicate_headings(source: str, text: str) -> None:
    """Stop when two headings of one document share GitHub's anchor (design § 5.2)."""
    first: dict[str, int] = {}
    for line, slug in heading_anchors(text):
        if slug in first:
            raise SiteError(f"{source}: headings at lines {first[slug]} and {line} share the anchor #{slug}")
        first[slug] = line


def front_matter(fields: Mapping[str, str]) -> str:
    """YAML front matter; each value a JSON string, which YAML reads as a double-quoted scalar."""
    lines = [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items() if value]
    return "---\n" + "\n".join(lines) + "\n---\n\n" if lines else ""


def stage(root: Path, docs: Path, *, home_title: str, token: str, repo_url: str) -> dict[str, str]:
    """Write every published document to ``docs`` at its site path; returns each Markdown page's staged text."""
    tracked = tracked_files(root)
    pages = published(tracked)
    repository = Repository(set(tracked), pages, repo_url)
    staged: dict[str, str] = {}
    for source, target in pages.items():
        destination = docs / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not source.endswith(".md"):
            shutil.copyfile(root / source, destination)
            continue
        text = (root / source).read_bytes().decode("utf-8").replace("\r\n", "\n")
        duplicate_headings(source, text)
        body = rewrite_links(source, text, repository)
        prose = paragraphs(body)
        fields = {}
        if target == "index.md":
            fields["title"] = home_title
        elif not H1.search(without_code(body)) and prose:
            # A page without a heading, such as release notes, is titled by its first paragraph.
            fields["title"] = shorten(prose.pop(0), TITLE_LENGTH)
        fields["description"] = shorten(prose[0], DESCRIPTION_LENGTH) if prose else ""
        if target == "index.md":
            fields["google_site_verification"] = token
        staged[target] = front_matter(fields) + body
        destination.write_text(staged[target], encoding="utf-8", newline="\n")
    return staged


def build(root: Path, output: Path, *, token: str = "") -> tuple[Path, dict[str, str], dict]:
    """Stage into ``output``, build ``output/site`` with Zensical in strict mode and write its robots.txt."""
    config_text = (root / CONFIG).read_text(encoding="utf-8")
    config = tomllib.loads(config_text)["project"]
    output.mkdir(parents=True, exist_ok=True)
    (output / "zensical.toml").write_text(config_text, encoding="utf-8", newline="\n")
    shutil.copytree(root / OVERRIDES, output / "overrides", dirs_exist_ok=True)
    staged = stage(root, output / "docs", home_title=config.get("extra", {}).get("home_title", ""), token=token,
                   repo_url=config["repo_url"].rstrip("/"))
    run = subprocess.run([sys.executable, "-m", "zensical", "build", "--strict", "--clean", "-f",
                          str(output / "zensical.toml")], cwd=output, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if run.returncode:
        sys.stderr.write(run.stdout + run.stderr)
        raise SiteError(f"zensical build --strict exited {run.returncode}")
    site = output / "site"
    (site / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {config['site_url']}sitemap.xml\n",
                                     encoding="utf-8", newline="\n")
    return site, staged, config


class Page(HTMLParser):
    """What the check reads of one built page: ids, title, meta tags, canonical and every href and src."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.headings: set[str] = set()
        self.links: list[str] = []
        self.meta: dict[str, list[str]] = {}
        self.canonical: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if "id" in values:
            self.ids.add(values["id"])
            if re.fullmatch(r"h[1-6]", tag):
                self.headings.add(values["id"])
        if "name" in values and tag == "a":
            self.ids.add(values["name"])
        if tag == "meta":
            key = values.get("property") or values.get("name")
            if key:
                self.meta.setdefault(key, []).append(values.get("content", ""))
        if tag == "link" and values.get("rel") == "canonical":
            self.canonical.append(values.get("href", ""))
        elif tag in ("a", "link") and values.get("href"):
            self.links.append(values["href"])
        if tag in ("img", "script") and values.get("src"):
            self.links.append(values["src"])
        self._in_title = self._in_title or tag == "title"

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def read_pages(site: Path) -> dict[str, Page]:
    pages = {}
    for path in sorted(site.rglob("*.html")):
        parser = Page()
        parser.feed(path.read_text(encoding="utf-8"))
        pages[path.relative_to(site).as_posix()] = parser
    return pages


def resolve(site: Path, page: str, link: str, site_url: str) -> tuple[str, str] | None:
    """The built file and fragment an internal link of ``page`` names; None for an external link."""
    prefix = urlsplit(site_url).path
    if link.startswith(site_url):
        link = link[len(site_url) - len(prefix):]
    parts = urlsplit(link)
    if parts.scheme or parts.netloc:
        return None
    path = unquote(parts.path)
    if not path:
        return page, parts.fragment
    if path.startswith("/"):
        if not path.startswith(prefix):
            return path, parts.fragment  # outside the site's path: names no built file
        target = posixpath.normpath(path[len(prefix):] or ".")
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(page), path))
    target = "" if target == "." else target
    if path.endswith("/") or (site / target).is_dir():
        target = posixpath.join(target, "index.html") if target else "index.html"
    return target, parts.fragment


def check(site: Path, staged: Mapping[str, str], site_url: str, *, token: str = "") -> list[str]:
    """Every problem of the built site (design § 5.2): links, search tags, heading ids, sitemap and robots.txt."""
    problems: list[str] = []
    pages = read_pages(site)
    for name, page in pages.items():
        for link in page.links:
            resolved = resolve(site, name, link, site_url)
            if resolved is None:
                continue
            target, fragment = resolved
            if not (site / target).is_file():
                problems.append(f"{name}: {link} names no built file")
            elif fragment and target.endswith(".html") and fragment not in pages[target].ids:
                problems.append(f"{name}: {link} names no id on {target}")
    sitemap = sitemap_urls(site / "sitemap.xml")
    for path, text in staged.items():
        url = page_url(site_url, path)
        built = url[len(site_url):] + "index.html"
        page = pages.get(built)
        if page is None:
            problems.append(f"{path}: no built page {built}")
            continue
        problems += [f"{built}: {problem}" for problem in page_problems(page, url)]
        missing = sorted({slug for _, slug in heading_anchors(text)} - page.headings)
        if missing:
            problems.append(f"{built}: no heading id for GitHub's anchors {', '.join(missing)}")
        if url not in sitemap:
            problems.append(f"sitemap.xml: {url} missing")
    for url in sorted(sitemap):
        built = url[len(site_url):] + "index.html" if url.startswith(site_url) else ""
        if not built or built not in pages:
            problems.append(f"sitemap.xml: {url} names no built page")
    robots = site / "robots.txt"
    if not robots.is_file() or f"Sitemap: {site_url}sitemap.xml" not in robots.read_text(encoding="utf-8"):
        problems.append(f"robots.txt: no line Sitemap: {site_url}sitemap.xml")
    home = pages.get("index.html")
    if token and (home is None or home.meta.get("google-site-verification") != [token]):
        problems.append("index.html: the google-site-verification meta is not the token, once")
    return problems


def page_problems(page: Page, url: str) -> list[str]:
    problems = []
    if not page.title.strip():
        problems.append("empty <title>")
    if not "".join(page.meta.get("description", [])).strip():
        problems.append("no description meta")
    if page.canonical != [url]:
        problems.append(f"canonical {page.canonical} is not [{url}]")
    for key in OPEN_GRAPH:
        values = page.meta.get(key, [])
        if len(values) != 1 or not values[0].strip():
            problems.append(f"{key} appears {len(values)} times, not once with content")
    return problems


def sitemap_urls(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    tree = ET.parse(path)
    return {element.text.strip() for element in tree.iter() if element.tag.endswith("loc") and element.text}


def run(root: Path, output: Path, token: str) -> int:
    try:
        site, staged, config = build(root, output, token=token)
    except SiteError as error:
        print(f"SITE_FAILED {error}", file=sys.stderr)
        return 1
    problems = check(site, staged, config["site_url"], token=token)
    for problem in problems:
        print(f"SITE_FAILED {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"SITE OK {len(staged)} pages, {sum(1 for _ in site.rglob('*.html'))} HTML files: {site}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="the repository (default: this checkout)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="stage, build and check in a temporary directory")
    build_parser = sub.add_parser("build", help="stage, build and check into OUTPUT; the site is OUTPUT/site")
    build_parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    if args.command == "build":
        return run(args.root, args.output, token)
    with tempfile.TemporaryDirectory(prefix="docs-site-") as directory:
        return run(args.root, Path(directory), token)


if __name__ == "__main__":
    sys.exit(main())

"""docs/WORKFLOW.md § Docs site: staging, the Zensical build and the built site's check (design docs/design/ATC-0060.md)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

import docs_site
from atc_testkit import docs

CHECKOUT = Path(__file__).resolve().parents[3]
SITE_URL = "https://example.github.io/probe/"
REPO_URL = "https://github.com/o/r"


def git(root: Path, *args: str) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def repository(root: Path, files: dict[str, str | bytes]) -> Path:
    """A Git repository whose index holds ``files``, which is all staging reads of Git."""
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    return root


def staged(root: Path, out: Path, *, token: str = "") -> dict[str, str]:
    return docs_site.stage(root, out, home_titles={"en": "Home title", "zh-TW": "首頁"}, token=token,
                           repo_url=REPO_URL)


def front(text: str) -> dict:
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---\n")[1])


# Site paths


@pytest.mark.unit
@pytest.mark.parametrize(("source", "root", "expected"), [
    ("README.md", "", "index.md"),
    ("README.zh-TW.md", "", "zh-TW/index.md"),
    ("SCOPE.md", "", "scope.md"),
    ("docs/ATC_HOME.md", "docs", "atc-home.md"),
    ("docs/WORKFLOW.md", "docs", "workflow.md"),
    ("docs/releases/v0.7.0.md", "docs", "releases/v0.7.0.md"),
    ("docs/assets/atc-banner.png", "docs", "assets/atc-banner.png"),
])
def test_a_site_path_is_the_source_lower_cased_with_dashes_and_readmes_as_indexes(source, root, expected):
    assert docs_site.site_path(source, root) == expected


@pytest.mark.unit
def test_the_publication_table_leaves_design_docs_records_and_governance_out():
    tracked = ["README.md", "README.zh-TW.md", "SCOPE.md", "GLOSSARY.md", "AGENTS.md", "LICENSE",
               "docs/WORKFLOW.md", "docs/design/ATC-0060.md", "docs/releases/v1.0.0.md", "docs/assets/a.png",
               ".atc/tasks/ATC-0060.yaml", "shared/governance/CORE.md", "adapters/python/README.md"]
    assert docs_site.published(tracked) == {
        "README.md": "index.md", "README.zh-TW.md": "zh-TW/index.md", "SCOPE.md": "scope.md",
        "GLOSSARY.md": "glossary.md", "docs/WORKFLOW.md": "workflow.md",
        "docs/releases/v1.0.0.md": "releases/v1.0.0.md", "docs/assets/a.png": "assets/a.png"}


@pytest.mark.unit
def test_two_sources_on_one_site_path_stop():
    with pytest.raises(docs_site.SiteError, match="both publish to workflow.md"):
        docs_site.published(["docs/WORKFLOW.md", "docs/workflow.md"])


# Links


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return repository(tmp_path / "repo", {
        "README.md": "# R\n", "LICENSE": "MIT\n", "docs/WORKFLOW.md": "# W\n\n## Steps\n",
        "docs/design/X.md": "# X\n", "docs/assets/a.png": b"png", "adapters/python/pyproject.toml": "",
        "shared/governance/CORE.md": "# C\n"})


def rewritten(repo: Path, source: str, text: str) -> str:
    tracked = docs_site.tracked_files(repo)
    return docs_site.rewrite_links(
        source, text, docs_site.Repository(set(tracked), docs_site.published(tracked), REPO_URL))


@pytest.mark.unit
@pytest.mark.parametrize(("source", "text", "expected"), [
    ("README.md", "[w](docs/WORKFLOW.md#steps)\n", "[w](workflow.md#steps)\n"),
    ("docs/WORKFLOW.md", "[r](../README.md)\n", "[r](index.md)\n"),
    ("README.md", "![b](docs/assets/a.png)\n", "![b](assets/a.png)\n"),
    ("README.md", "[l](LICENSE)\n", f"[l]({REPO_URL}/blob/main/LICENSE)\n"),
    ("README.md", "[d](docs/design/X.md)\n", f"[d]({REPO_URL}/blob/main/docs/design/X.md)\n"),
    ("README.md", "[g](shared/governance)\n", f"[g]({REPO_URL}/tree/main/shared/governance)\n"),
    ("README.md", "[![v](https://img/x.svg)](adapters/python/pyproject.toml)\n",
     f"[![v](https://img/x.svg)]({REPO_URL}/blob/main/adapters/python/pyproject.toml)\n"),
    ("README.md", "[ref]: docs/WORKFLOW.md\n", "[ref]: workflow.md\n"),
    ("README.md", "[w](<docs/WORKFLOW.md>)\n", "[w](<workflow.md>)\n"),
    ("README.md", "[x](https://example.com/a.md) [h](#here)\n", "[x](https://example.com/a.md) [h](#here)\n"),
    ("README.md", "```\n[l](LICENSE)\n```\n", "```\n[l](LICENSE)\n```\n"),
    ("README.md", "`[l](LICENSE)`\n", "`[l](LICENSE)`\n"),
])
def test_links_point_into_the_site_or_at_github_and_code_keeps_its_links(repo, source, text, expected):
    assert rewritten(repo, source, text) == expected


@pytest.mark.unit
@pytest.mark.parametrize(("text", "match"), [
    ("[gone](docs/GONE.md)\n", "Git does not track"),
    ("[out](../elsewhere.md)\n", "leaves the repository"),
])
def test_a_link_to_no_tracked_path_stops_staging(repo, text, match):
    with pytest.raises(docs_site.SiteError, match=match):
        rewritten(repo, "README.md", text)


@pytest.mark.unit
def test_link_spans_find_what_link_targets_find_with_their_place():
    text = "[a](x.md)\n```\n[b](y.md)\n```\n[![i](img.png)](z.md)\n[r]: <w.md>\n"
    spans = docs.link_spans(text)
    assert [(span.line, span.raw) for span in spans] == [(t.line, t.raw) for t in docs.link_targets(text)]
    assert [text[span.start:span.end] for span in spans] == ["x.md", "img.png", "z.md", "<w.md>"]


# Headings, titles and descriptions


@pytest.mark.unit
@pytest.mark.parametrize(("text", "lines"), [
    ("# Task records\n\n## Task records\n", (1, 3)),
    ("## Run tests\n\n### Run tests!\n", (1, 3)),
])
def test_two_headings_with_one_anchor_stop_staging(text, lines):
    with pytest.raises(docs_site.SiteError, match=f"lines {lines[0]} and {lines[1]} share the anchor"):
        docs_site.duplicate_headings("docs/X.md", text)


@pytest.mark.unit
def test_a_heading_inside_a_fence_and_one_heading_per_document_are_no_duplicate():
    docs_site.duplicate_headings("docs/X.md", "# Task records\n\n```markdown\n## Task records\n```\n")


@pytest.mark.unit
def test_the_description_is_the_first_prose_paragraph_plain_and_cut_at_a_word():
    text = ("# Title\n\n![banner](a.png)\n\n[![b](x.svg)](LICENSE)\n![c](y.svg)\n\n[Other language](README.zh-TW.md)\n\n"
            "```\ncode first\n```\n\n**Bold** words with `code` and a [link](x.md) " + "word " * 40 + "\n\nSecond.\n")
    first = docs_site.paragraphs(text)[0]
    assert first.startswith("Bold words with code and a link word")
    short = docs_site.shorten(first, docs_site.DESCRIPTION_LENGTH)
    assert len(short) <= docs_site.DESCRIPTION_LENGTH and short.endswith("word…")


@pytest.mark.unit
@pytest.mark.parametrize(("block", "expected"), [
    ("Agent Traffic Control（`atc`）是一套治理層，\n讓多個 coding agent 在同一批 repository 上工作：\n每條 branch",
     "Agent Traffic Control（atc）是一套治理層，讓多個 coding agent 在同一批 repository 上工作：每條 branch"),
    ("one writer per\nbranch and git\n  worktree", "one writer per branch and git worktree"),
    ("中文結尾\nEnglish start", "中文結尾English start"),
])
def test_a_line_break_is_a_space_between_words_and_nothing_next_to_cjk(block, expected):
    assert docs_site.plain(block) == expected


@pytest.mark.unit
def test_a_cjk_description_without_spaces_is_cut_at_the_limit():
    short = docs_site.shorten("治理" * 100, docs_site.DESCRIPTION_LENGTH)
    assert len(short) == docs_site.DESCRIPTION_LENGTH and short.endswith("…")


@pytest.mark.unit
def test_a_home_page_in_a_language_without_a_configured_title_stops(tmp_path):
    root = repository(tmp_path / "repo", {"README.md": "# R\n", "README.ja.md": "# R\n"})
    with pytest.raises(docs_site.SiteError, match="names no title for ja"):
        staged(root, tmp_path / "out")


@pytest.mark.unit
def test_staging_writes_front_matter_and_only_the_home_page_carries_the_token(tmp_path):
    root = repository(tmp_path / "repo", {
        "README.md": "# R\n\nWhat it is.\n", "README.zh-TW.md": "# R\n\n是什麼。\n",
        "docs/releases/v1.0.0.md": "atc 1.0.0 (atc-testkit 2.0.0)\n\nInstalled from its tag.\n"})
    pages = staged(root, tmp_path / "out", token="tok")
    assert front(pages["index.md"]) == {"title": "Home title", "home_page": True, "description": "What it is.",
                                         "google_site_verification": "tok"}
    assert front(pages["zh-TW/index.md"]) == {"title": "首頁", "home_page": True, "language": "zh-TW",
                                              "description": "是什麼。"}
    assert front(pages["releases/v1.0.0.md"]) == {"title": "atc 1.0.0 (atc-testkit 2.0.0)",
                                                  "description": "Installed from its tag."}
    assert (tmp_path / "out" / "releases" / "v1.0.0.md").read_text(encoding="utf-8") == pages["releases/v1.0.0.md"]


# The build and the check, with this checkout's site/ configuration


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, str], str]:
    root = tmp_path_factory.mktemp("site") / "repo"
    shutil.copytree(CHECKOUT / "site", root / "site")
    config = (root / docs_site.CONFIG).read_text(encoding="utf-8")
    (root / docs_site.CONFIG).write_text(
        re.sub(r'(?m)^site_url = .*$', f'site_url = "{SITE_URL}"', config), encoding="utf-8")
    repository(root, {
        "README.md": ("# Probe\n\n[繁體中文](README.zh-TW.md)\n\nProbe is a tool.\n\n## 安裝 與 設定\n\n"
                      "## What `atc` does, really?\n\n[guide](docs/GUIDE_ONE.md#first-step) [l](LICENSE)\n"),
        "README.zh-TW.md": "# Probe\n\n[English](README.md)\n\n工具。\n\n## 安裝\n",
        "docs/GUIDE_ONE.md": "# Guide\n\nHow to.\n\n## First step\n",
        "docs/releases/v1.0.0.md": "atc 1.0.0 (atc-testkit 2.0.0)\n\nNotes.\n",
        "docs/assets/atc-banner.png": b"\x89PNG\r\n", "LICENSE": "MIT\n"})
    site, pages, _ = docs_site.build(root, root.parent / "out", token="tok")
    return site, pages, "tok"


@pytest.mark.integration
def test_the_built_site_passes_its_check(built):
    site, pages, token = built
    assert docs_site.check(site, pages, SITE_URL, token=token) == []
    home = (site / "index.html").read_text(encoding="utf-8")
    assert re.search(r"<title>Home title|<title>Agent Traffic Control: ", home)
    page = docs_site.read_pages(site)["index.html"]
    assert {"安裝-與-設定", "what-atc-does-really"} <= page.headings
    zh = docs_site.read_pages(site)["zh-TW/index.html"]
    config = docs_site.tomllib.loads((CHECKOUT / docs_site.CONFIG).read_text(encoding="utf-8"))["project"]
    assert (zh.language, zh.titles) == ("zh-TW", [config["extra"]["home_title"]["zh-TW"]])
    assert page.language == "en"
    assert (site / "sitemap.xml").is_file()
    assert (site / "robots.txt").read_text(encoding="utf-8").endswith(f"Sitemap: {SITE_URL}sitemap.xml\n")


@pytest.mark.integration
def test_the_home_title_is_the_configured_one_without_the_site_name_again(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(CHECKOUT / "site", root / "site")
    repository(root, {"README.md": "# Probe\n\nProbe.\n"})
    site, _, config = docs_site.build(root, tmp_path / "out")
    title = docs_site.read_pages(site)["index.html"].title
    assert title == config["extra"]["home_title"]["en"]


def broken(site: Path, tmp_path: Path, page: str, pattern: str, replacement: str) -> Path:
    copy = tmp_path / "site"
    shutil.copytree(site, copy)
    path = copy / page
    text, count = re.subn(pattern, replacement, path.read_text(encoding="utf-8"), count=1)
    assert count == 1, pattern
    path.write_text(text, encoding="utf-8")
    return copy


@pytest.mark.integration
@pytest.mark.parametrize(("page", "pattern", "replacement", "problem"), [
    ("index.html", r"</body>", '<a href="nowhere/">x</a></body>', "index.html: nowhere/ names no built file"),
    ("index.html", r"</body>", '<a href="guide-one/#no-such">x</a></body>', "names no id on guide-one/index.html"),
    ("index.html", r'<meta name="?description"?[^>]*>', "", "index.html: description appears 0 times"),
    ("index.html", r"</head>", '<meta name="description" content="again"></head>', "description appears 2 times"),
    ("index.html", r"</head>", "<title>again</title></head>", "<title> appears 2 times"),
    ("guide-one/index.html", r"<title>[^<]*</title>", "<title>Probe - Probe</title>", "repeats itself"),
    ("zh-TW/index.html", r'<html lang="?zh-TW"?', '<html lang="en"', "<html lang> is 'en', not 'zh-TW'"),
    ("index.html", r'<link rel="?canonical"?[^>]*>', "", "index.html: canonical [] is not"),
    ("index.html", r"</head>", '<meta property="og:title" content="again"></head>', "og:title appears 2 times"),
    ("guide-one/index.html", r'<meta property="?og:image"?[^>]*>', "", "og:image appears 0 times"),
    ("index.html", r'<meta name="?google-site-verification"?[^>]*>', "", "google-site-verification"),
    ("guide-one/index.html", r'id="?first-step"?', 'id="first-step_x"', "no heading id for GitHub's anchors first-step"),
    ("sitemap.xml", r"<url>\s*<loc>[^<]*/guide-one/</loc>\s*</url>", "", "guide-one/ missing"),
    ("robots.txt", r"Sitemap: .*", "", "robots.txt: no line Sitemap"),
])
def test_the_check_names_each_kind_of_broken_page(built, tmp_path, page, pattern, replacement, problem):
    site, pages, token = built
    problems = docs_site.check(broken(site, tmp_path, page, pattern, replacement), pages, SITE_URL, token=token)
    assert any(problem in line for line in problems), problems


@pytest.mark.integration
def test_a_strict_build_failure_stops_with_zensical_output(tmp_path, capsys):
    root = tmp_path / "repo"
    shutil.copytree(CHECKOUT / "site", root / "site")
    repository(root, {"README.md": "# Probe\n\n[g](docs/GUIDE.md#missing)\n", "docs/GUIDE.md": "# Guide\n"})
    assert docs_site.main(["--root", str(root), "build", str(tmp_path / "out")]) == 1
    assert "SITE_FAILED zensical build --strict exited 1" in capsys.readouterr().err


# The triggers


def github_glob(pattern: str) -> re.Pattern[str]:
    """A GitHub Actions ``paths`` filter: ``**`` any characters, ``*`` any but ``/``."""
    parts = (re.escape(part).replace(r"\*", "[^/]*") for part in pattern.split("**"))
    return re.compile(".*".join(parts) + r"\Z")


@pytest.mark.unit
def test_the_pages_workflow_and_the_docs_site_hook_watch_the_same_paths():
    workflow = yaml.safe_load((CHECKOUT / ".github/workflows/pages.yml").read_text(encoding="utf-8"))
    paths = [github_glob(pattern) for pattern in workflow[True]["push"]["paths"]]
    hooks = yaml.safe_load((CHECKOUT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    files = next(hook["files"] for repo in hooks["repos"] for hook in repo["hooks"] if hook["id"] == "docs-site")
    probes = ["README.md", "README.zh-TW.md", "SCOPE.md", "GLOSSARY.md", "docs/WORKFLOW.md",
              "docs/design/ATC-0060.md", "docs/assets/atc-banner.png", "site/zensical.toml",
              "site/overrides/main.html", "adapters/python/scripts/docs_site.py",
              "adapters/python/atc-testkit/src/atc_testkit/docs.py", "adapters/python/pyproject.toml",
              "adapters/python/uv.lock", ".github/workflows/pages.yml", "AGENTS.md", "CLAUDE.md", "LICENSE",
              "shared/governance/CORE.md", "adapters/python/scripts/select_tests.py", "adapters/python/README.md",
              ".github/workflows/validate.yml", "docsx/a.md"]
    for probe in probes:
        assert bool(re.search(files, probe)) == any(path.match(probe) for path in paths), probe

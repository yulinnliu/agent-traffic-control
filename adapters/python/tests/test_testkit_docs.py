"""docs/TESTKIT.md § Documentation check: the relative links of Markdown, the rules the three earlier copies held."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from atc_testkit import docs


def write(root: Path, files: dict[str, str | bytes]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return root


def problems(root: Path, text: str, *, anchors: bool = False, **others: str) -> list[str]:
    write(root, {"docs/page.md": text, **{name.replace("__", "/").replace("_md", ".md"): body
                                          for name, body in others.items()}})
    return docs.relative_link_problems(root, ["docs/page.md"], anchors=anchors)


@pytest.mark.unit
@pytest.mark.parametrize(("text", "expected"), [
    ("[ok](other.md)\n", []),
    ("[gone](missing.md)\n", ["docs/page.md:1: broken relative link: missing.md"]),
    ("x\n\n![img](pic.png)\n", ["docs/page.md:3: broken relative link: pic.png"]),
    ("[ref]: missing.md\n", ["docs/page.md:1: broken relative link: missing.md"]),
    ("[t](other.md \"a title\")\n", []),
    ("[sp](<my file.md>)\n", []),
    ("[pct](my%20file.md)\n", []),
    ("[root](/top.md)\n", []),
    ("[up](../top.md)\n", []),
    ("[out](../../outside.md)\n", ["docs/page.md:1: relative link escapes the tree: ../../outside.md"]),
    ("```\n[gone](missing.md)\n```\n", []),
    ("~~~md\n[gone](missing.md)\n~~~\n", []),
    ("`[gone](missing.md)` and ``[gone](x.md)``\n", []),
    ("[web](https://example.com/x.md) [mail](mailto:a@b) [here](#nowhere)\n", []),
    ("[dir](../sub)\n", []),
    ("[q](other.md?raw=1)\n", []),
])
def test_relative_links(tmp_path: Path, text: str, expected: list[str]) -> None:
    write(tmp_path, {"docs/other.md": "# Other\n", "docs/my file.md": "x\n", "top.md": "x\n", "sub/a.md": "x\n"})
    assert problems(tmp_path, text) == expected


@pytest.mark.unit
def test_links_out_of_the_tree_are_left_unchecked_when_allowed(tmp_path: Path) -> None:
    write(tmp_path, {"docs/page.md": "[sibling](../../other-repo/x.md)\n[gone](gone.md)\n"})
    assert docs.relative_link_problems(tmp_path, ["docs/page.md"], allow_outside=True) == [
        "docs/page.md:2: broken relative link: gone.md"]


@pytest.mark.unit
def test_a_changed_file_that_is_missing_empty_or_not_utf8_is_a_problem(tmp_path: Path) -> None:
    write(tmp_path, {"empty.md": " \n", "latin.md": b"caf\xe9\n"})
    found = docs.relative_link_problems(tmp_path, ["absent.md", "empty.md", "latin.md"])
    assert found[0] == "absent.md: Markdown file is missing" and found[1] == "empty.md: Markdown file is empty"
    assert found[2].startswith("latin.md: cannot read UTF-8 Markdown:") and len(found) == 3


@pytest.mark.unit
def test_anchors_are_checked_only_when_asked(tmp_path: Path) -> None:
    write(tmp_path, {"docs/other.md": "# Run `atc` now!\n\n## Twice\n\n## Twice\n\n<a id=\"kept\"></a>\n"
                                      "```\n# Not a heading\n```\n"})
    text = ("[a](other.md#run-atc-now) [b](other.md#twice-1) [c](other.md#kept) [d](#local)\n"
            "# Local\n[e](other.md#not-a-heading) [f](other.md#twice-2) [g](#gone)\n")
    assert problems(tmp_path, text) == []
    assert problems(tmp_path, text, anchors=True) == [
        "docs/page.md:3: missing anchor: other.md#not-a-heading",
        "docs/page.md:3: missing anchor: other.md#twice-2",
        "docs/page.md:3: missing anchor: #gone"]


FROZEN = "hyp/H00[1-6]_*.md"


@pytest.mark.unit
@pytest.mark.parametrize(("name", "expected"), [
    ("hyp/H001_a.md", True), ("hyp\\H006_b.md", True), ("./hyp/H003_c.md", True),
    ("hyp/H007_a.md", False), ("HYP/H001_a.md", False), ("other/hyp/H001_a.md", False), ("hyp/H001.md", False),
])
def test_an_exclusion_glob_matches_the_repo_relative_posix_path(name: str, expected: bool) -> None:
    assert docs.excluded(name, ["unused/*.md", FROZEN]) is expected
    assert docs.excluded(name, []) is False


@pytest.mark.unit
def test_an_excluded_file_is_no_link_source_but_links_into_it_are_checked(tmp_path: Path) -> None:
    write(tmp_path, {"hyp/H001_a.md": "# Kept\n[gone](gone.md)\n", "hyp/H007_a.md": "[gone](gone.md)\n",
                     "page.md": "[in](hyp/H001_a.md#kept) [bad](hyp/H001_a.md#gone) [none](hyp/H002_a.md)\n"})
    files = ["hyp/H001_a.md", "hyp/H002_a.md", "hyp/H007_a.md", "page.md"]
    assert docs.relative_link_problems(tmp_path, files, anchors=True, exclude=[FROZEN, "stale/*.md"]) == [
        "hyp/H007_a.md:1: broken relative link: gone.md",
        "page.md:1: missing anchor: hyp/H001_a.md#gone",
        "page.md:1: broken relative link: hyp/H002_a.md"]


def git(root: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=root, env=env,
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> tuple[Path, str]:
    write(tmp_path, {"a.md": "[b](b.md)\n", "b.md": "x\n", "c.md": "[old](old.txt)\n", "old.txt": "x\n",
                     "code.py": "x = 1\n"})
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path, git(tmp_path, "rev-parse", "HEAD")


def docs_command(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run([sys.executable, "-m", "atc_testkit.docs", *args], cwd=root, capture_output=True, text=True,
                          env=env)


@pytest.mark.integration
def test_tracked_reads_every_markdown_file_git_tracks(repo: tuple[Path, str]) -> None:
    root, _base = repo
    write(root, {"untracked.md": "[gone](gone.md)\n"})
    git(root, "rm", "-q", "old.txt")
    git(root, "commit", "-qm", "delete")
    assert docs.tracked_markdown(root) == ["a.md", "b.md", "c.md"]
    run = docs_command(root, "--tracked")
    assert (run.returncode, run.stdout) == (1, "c.md:1: broken relative link: old.txt\n")


@pytest.mark.integration
def test_the_command_takes_tracked_or_paths(repo: tuple[Path, str]) -> None:
    root, _base = repo
    assert docs_command(root, "a.md", "b.md").returncode == 0
    both = docs_command(root, "--tracked", "a.md")
    neither = docs_command(root)
    assert both.returncode == neither.returncode == 2 and "--tracked or paths" in both.stderr


@pytest.mark.integration
def test_the_command_passes_each_exclusion_in_both_modes(repo: tuple[Path, str]) -> None:
    root, _base = repo
    write(root, {"hyp/H001_a.md": "[gone](gone.md)\n"})
    git(root, "add", ".")
    git(root, "rm", "-q", "old.txt")
    git(root, "commit", "-qm", "delete")
    tracked = docs_command(root, "--tracked", "--exclude", FROZEN, "--exclude", "c.md", "--exclude", "stale/*.md")
    assert (tracked.returncode, tracked.stdout) == (0, "")
    assert docs_command(root, "--tracked", "--exclude", "c.md").stdout == "hyp/H001_a.md:1: broken relative link: gone.md\n"
    files = docs_command(root, "--exclude", FROZEN, "a.md", "hyp/H001_a.md")
    assert (files.returncode, files.stdout) == (0, "")

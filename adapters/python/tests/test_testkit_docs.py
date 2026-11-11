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


@pytest.mark.integration
def test_a_range_checks_its_changed_markdown(repo: tuple[Path, str]) -> None:
    root, base = repo
    write(root, {"a.md": "[gone](gone.md)\n", "code.py": "x = 2\n"})
    git(root, "commit", "-qam", "edit")
    assert docs.markdown_to_check(root, base, "HEAD") == ["a.md"]
    assert docs.documentation_problems(root, base, "HEAD") == ["a.md:1: broken relative link: gone.md"]


@pytest.mark.integration
def test_a_range_that_deletes_a_path_checks_every_tracked_markdown(repo: tuple[Path, str]) -> None:
    root, base = repo
    git(root, "rm", "-q", "old.txt")
    git(root, "commit", "-qm", "delete")
    assert docs.markdown_to_check(root, base, "HEAD") == ["a.md", "b.md", "c.md"]
    assert docs.documentation_problems(root, base, "HEAD") == ["c.md:1: broken relative link: old.txt"]


@pytest.mark.integration
def test_the_command_reads_the_verifiers_range(repo: tuple[Path, str]) -> None:
    root, base = repo
    git(root, "rm", "-q", "old.txt")
    git(root, "commit", "-qm", "delete")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    head = git(root, "rev-parse", "HEAD")
    run = subprocess.run([*docs.DOCUMENTATION_CHECK.argv], cwd=root, capture_output=True, text=True,
                         env={**env, "ATC_EXPECTED_MAIN_SHA": base, "ATC_INTEGRATION_SHA": head})
    assert (run.returncode, run.stdout) == (1, "c.md:1: broken relative link: old.txt\n")
    files = subprocess.run([sys.executable, "-m", "atc_testkit.docs", "a.md", "b.md"], cwd=root, capture_output=True,
                           text=True, env=env)
    assert (files.returncode, files.stdout) == (0, "")
    usage = subprocess.run([sys.executable, "-m", "atc_testkit.docs", "--range"], cwd=root, capture_output=True,
                           text=True, env=env)
    assert usage.returncode == 2 and "ATC_EXPECTED_MAIN_SHA" in usage.stderr
    assert docs.DOCUMENTATION_CHECK.token == "DOCUMENTATION_REFERENCE_FAILED"

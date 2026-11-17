"""`atc-maintain release prepare|check` (docs/development/RELEASING.md; design docs/design/ATC-0064.md)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

import release_check as release

PINNED = {
    "adapters/python/pyproject.toml": '[project]\nname = "agent-traffic-control"\nversion = "0.8.0"\n',
    "adapters/python/uv.lock": 'version = 1\n\n[[package]]\nname = "agent-traffic-control"\nversion = "0.8.0"\n',
    "README.md": "## Status\n\nVersion 0.8.0, the version the Python adapter carries.\n",
    "README.zh-TW.md": "## 狀態\n\n目前版本是 0.8.0，也就是 Python adapter 的版號。\n",
    "adapters/python/atc-testkit/src/atc_testkit/__init__.py": '__version__ = "0.15.0"\n',
    "adapters/python/atc-testkit/README.md": "This document describes `atc-testkit` 0.15.0; update it.\n\n"
                                             "Each consumer pins `atc-testkit==0.15.0` in its dependencies.\n",
    "adapters/python/atc-testkit/pyproject.toml": '[project]\nname = "atc-testkit"\ndynamic = ["version"]\n\n'
                                                  '[tool.setuptools.dynamic]\n'
                                                  'version = { attr = "atc_testkit.__version__" }\n',
    "atc-manifest.yaml": "manifest: 1\nmin_atc: 0.8.0\nproject: []\n",
    "docs/releases/v0.8.0.md": "atc 0.8.0 (atc-testkit 0.15.0)\n\nThe notes of 0.8.0.\n",
    ".atc/tasks/ATC-0001.yaml": "task_id: ATC-0001\n",
}


def git(root: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                          env=env, check=True).stdout.strip()


def put(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def commit(root: Path, files: dict[str, str], message: str = "change") -> str:
    put(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def integrate(root: Path, task: str) -> str:
    """A task's integration as the tree holds it: its record reaches main."""
    return commit(root, {f".atc/tasks/{task}.yaml": f"task_id: {task}\n"}, f"chore(tasks): open {task}")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout released as v0.8.0 (atc-testkit 0.15.0), then ATC-0002 and ATC-0003 integrated."""
    root = tmp_path / "atc"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, PINNED, "release 0.8.0")
    git(root, "tag", "v0.8.0")
    integrate(root, "ATC-0002")
    integrate(root, "ATC-0003")
    return root


def problems(root: Path) -> list[str]:
    return release.check(root)[0]


def tokens(root: Path) -> list[str]:
    return [problem.split()[0] for problem in problems(root)]


def filled(root: Path, atc: str) -> str:
    """The prepared notes with every placeholder written."""
    path = root / f"docs/releases/v{atc}.md"
    text = path.read_text(encoding="utf-8").replace(release.PLACEHOLDER, "written")
    path.write_text(text, encoding="utf-8", newline="\n")
    return text


def edit(root: Path, name: str, old: str, new: str) -> None:
    path = root / name
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


# released: the version is tagged

@pytest.mark.integration
def test_a_released_version_checks_its_pins_and_no_task_coverage(repo: Path) -> None:
    """Between releases the tasks integrated since the tag are in no notes yet, and that is no problem."""
    assert problems(repo) == []


@pytest.mark.integration
@pytest.mark.parametrize(("name", "old", "new"), [
    ("README.md", "Version 0.8.0,", "Version 0.8.1,"),
    ("adapters/python/uv.lock", 'version = "0.8.0"', 'version = "0.7.0"'),
    ("adapters/python/atc-testkit/README.md", "`atc-testkit==0.15.0`", "`atc-testkit==0.14.0`"),
    ("docs/releases/v0.8.0.md", "atc-testkit 0.15.0", "atc-testkit 0.14.0"),
])
def test_a_pin_that_disagrees_is_a_version_mismatch(repo: Path, name: str, old: str, new: str) -> None:
    edit(repo, name, old, new)
    assert tokens(repo) == ["RELEASE_VERSION_MISMATCH"]


@pytest.mark.integration
def test_a_missing_pin_and_a_static_testkit_version_are_version_mismatches(repo: Path) -> None:
    edit(repo, "README.zh-TW.md", "目前版本是 0.8.0，", "版本 0.8.0。")
    edit(repo, "adapters/python/atc-testkit/pyproject.toml", 'dynamic = ["version"]', 'version = "0.15.0"')
    found = problems(repo)
    assert [p.split()[0] for p in found] == ["RELEASE_VERSION_MISMATCH"] * 2
    assert "README.zh-TW.md: 0 matches" in found[0] and "must be dynamic" in found[1]


@pytest.mark.integration
def test_min_atc_above_the_version_is_refused(repo: Path) -> None:
    edit(repo, "atc-manifest.yaml", "min_atc: 0.8.0", "min_atc: 0.8.1")
    assert tokens(repo) == ["RELEASE_MIN_ATC"]


@pytest.mark.integration
def test_the_notes_of_the_version_must_exist(repo: Path) -> None:
    (repo / "docs/releases/v0.8.0.md").unlink()
    assert tokens(repo) == ["RELEASE_NOTES_MISSING"]


@pytest.mark.integration
def test_without_a_tag_in_the_history_only_the_pins_are_checked(tmp_path: Path) -> None:
    """An export with no history, such as `atc test ID --lint --sha`, checks what needs no tag."""
    root = tmp_path / "export"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, PINNED)
    found, notes = release.check(root)
    assert found == [] and notes[0].startswith("NOTE no release tag")


# prepared: the version is not tagged yet

@pytest.mark.integration
def test_prepare_bumps_every_pin_and_scaffolds_the_notes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert release.main(["prepare", "0.9.0", "--testkit", "0.16.0", "--min-atc", "0.9.0"], root=repo) == 0
    out = capsys.readouterr().out
    assert out.count("UPDATED ") == len(release.PINS) + 1 and "WROTE docs/releases/v0.9.0.md (2 tasks)" in out
    found, _ = release.pinned(repo)
    assert found == {"atc": {"0.9.0"}, "testkit": {"0.16.0"}}
    assert "min_atc: 0.9.0" in (repo / "atc-manifest.yaml").read_text(encoding="utf-8")
    notes = (repo / "docs/releases/v0.9.0.md").read_text(encoding="utf-8")
    assert notes.startswith("atc 0.9.0 (atc-testkit 0.16.0)\n") and "## atc-testkit 0.16.0" in notes
    assert "- ATC-0002: " in notes and "- ATC-0003: " in notes and "- ATC-0001" not in notes
    assert set(tokens(repo)) == {"RELEASE_PLACEHOLDER"}, "the scaffold cannot pass until it is written"
    filled(repo, "0.9.0")
    assert problems(repo) == []


@pytest.mark.integration
def test_a_task_missing_from_the_notes_fails(repo: Path) -> None:
    release.main(["prepare", "0.9.0"], root=repo)
    edit(repo, "docs/releases/v0.9.0.md", "- ATC-0003: ", "- ATC-0003 was: ")
    filled(repo, "0.9.0")
    assert problems(repo) == ["RELEASE_TASK_MISSING docs/releases/v0.9.0.md: ATC-0003 (its record was added since "
                              "v0.8.0)"]


@pytest.mark.integration
def test_a_task_integrated_after_the_notes_were_written_fails(repo: Path) -> None:
    release.main(["prepare", "0.9.0"], root=repo)
    filled(repo, "0.9.0")
    integrate(repo, "ATC-0004")
    assert tokens(repo) == ["RELEASE_TASK_MISSING"]


@pytest.mark.integration
def test_a_staged_record_counts_before_its_commit(repo: Path) -> None:
    """The commit hook checks the index: the release task's own record is staged with its notes."""
    release.main(["prepare", "0.9.0"], root=repo)
    filled(repo, "0.9.0")
    put(repo, {".atc/tasks/ATC-0005.yaml": "task_id: ATC-0005\n"})
    assert problems(repo) == []
    git(repo, "add", ".atc/tasks/ATC-0005.yaml")
    assert tokens(repo) == ["RELEASE_TASK_MISSING"]


@pytest.mark.integration
def test_a_task_listed_without_a_record_since_the_tag_fails(repo: Path) -> None:
    release.main(["prepare", "0.9.0"], root=repo)
    edit(repo, "docs/releases/v0.9.0.md", "- ATC-0003: ", "- ATC-0001: an earlier release's task\n- ATC-0003: ")
    filled(repo, "0.9.0")
    assert problems(repo) == ["RELEASE_TASK_EXTRA docs/releases/v0.9.0.md: ATC-0001 (no record added since v0.8.0)"]


@pytest.mark.integration
def test_the_testkit_section_is_required_only_when_the_testkit_version_changes(repo: Path) -> None:
    release.main(["prepare", "0.9.0"], root=repo)
    assert "## atc-testkit" not in filled(repo, "0.9.0")
    assert problems(repo) == []
    edit(repo, "adapters/python/atc-testkit/src/atc_testkit/__init__.py", "0.15.0", "0.16.0")
    edit(repo, "adapters/python/atc-testkit/README.md", "`atc-testkit` 0.15.0", "`atc-testkit` 0.16.0")
    edit(repo, "adapters/python/atc-testkit/README.md", "`atc-testkit==0.15.0`", "`atc-testkit==0.16.0`")
    edit(repo, "docs/releases/v0.9.0.md", "atc-testkit 0.15.0", "atc-testkit 0.16.0")
    assert problems(repo) == ["RELEASE_SECTION_MISSING docs/releases/v0.9.0.md: ## atc-testkit 0.16.0"]


@pytest.mark.integration
@pytest.mark.parametrize(("old", "new", "problem"), [
    ("## Breaking\n", "## Changes\n", "## Breaking"),
    ("## Assets\n", "## Downloads\n", "## Assets"),
    ("### Projects\n", "### Repositories\n", "### Projects under ## Upgrade"),
    (release.PROJECTS_LINK, "../TESTKIT.md", f"### Projects does not link to {release.PROJECTS_LINK}"),
])
def test_each_required_section_is_named_when_missing(repo: Path, old: str, new: str, problem: str) -> None:
    release.main(["prepare", "0.9.0"], root=repo)
    filled(repo, "0.9.0")
    edit(repo, "docs/releases/v0.9.0.md", old, new)
    assert problems(repo) == [f"RELEASE_SECTION_MISSING docs/releases/v0.9.0.md: {problem}"]


@pytest.mark.integration
def test_an_untagged_version_not_above_the_last_tag_is_not_ahead(repo: Path) -> None:
    for name, old in (("adapters/python/pyproject.toml", 'version = "0.8.0"'),
                      ("adapters/python/uv.lock", 'version = "0.8.0"'), ("README.md", "Version 0.8.0,"),
                      ("README.zh-TW.md", "目前版本是 0.8.0")):
        edit(repo, name, old, old.replace("0.8.0", "0.7.9"))
    edit(repo, "atc-manifest.yaml", "0.8.0", "0.7.9")
    put(repo, {"docs/releases/v0.7.9.md": "atc 0.7.9 (atc-testkit 0.15.0)\n"})
    assert "RELEASE_NOT_AHEAD" in tokens(repo)


@pytest.mark.integration
@pytest.mark.parametrize(("argv", "token"), [
    (["prepare", "0.8.0"], "RELEASE_NOT_AHEAD"),
    (["prepare", "0.9.0", "--min-atc", "0.9.1"], "RELEASE_MIN_ATC"),
    (["prepare", "0.9"], "RELEASE_USAGE"),
])
def test_prepare_refuses_without_writing(repo: Path, capsys: pytest.CaptureFixture[str], argv: list[str],
                                         token: str) -> None:
    assert release.main(argv, root=repo) == 2
    assert f"ERROR {token}" in capsys.readouterr().out
    assert git(repo, "status", "--porcelain") == ""


@pytest.mark.integration
def test_a_manifest_without_min_atc_is_refused_before_any_pin_is_written(
        repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit(repo, {"atc-manifest.yaml": "manifest: 1\nproject: []\n"})
    assert release.main(["prepare", "0.9.0", "--testkit", "0.16.0", "--min-atc", "0.9.0"], root=repo) == 2
    out = capsys.readouterr().out
    assert "ERROR RELEASE_MIN_ATC atc-manifest.yaml: no min_atc line" in out and "UPDATED" not in out
    assert git(repo, "status", "--porcelain") == ""


@pytest.mark.integration
def test_prepare_never_overwrites_notes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    put(repo, {"docs/releases/v0.9.0.md": "kept\n"})
    assert release.main(["prepare", "0.9.0"], root=repo) == 2
    assert "ERROR RELEASE_NOTES_EXIST" in capsys.readouterr().out
    assert (repo / "docs/releases/v0.9.0.md").read_text(encoding="utf-8") == "kept\n"
    assert release.pinned(repo)[0]["atc"] == {"0.8.0"}


@pytest.mark.integration
def test_prepare_orders_by_the_ledger_and_names_what_only_one_source_holds(
        repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The ledger orders the tasks it records; a record it lacks (this release's own task) and a ledger line with no
    record since the tag are each a NOTE, and the notes list the records."""
    three = git(repo, "rev-parse", "HEAD")
    two = git(repo, "rev-parse", "HEAD~1")
    ledger = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / release.LEDGER
    ledger.parent.mkdir(parents=True)
    lines = [{"task": "ATC-0003", "candidate_sha": three, "commits": [three], "records_only": False},
             {"task": "ATC-0002", "candidate_sha": two, "commits": [two], "records_only": False},
             {"task": "ATC-0008", "candidate_sha": two, "records_only": True},
             {"task": "ATC-0009", "candidate_sha": three},
             {"kind": "report", "task": "ATC-0003", "sha": three}]
    ledger.write_text("".join(json.dumps(line) + "\n" for line in lines) + "not json\n", encoding="utf-8")
    integrate(repo, "ATC-0006")
    assert release.main(["prepare", "0.9.0"], root=repo) == 0
    out = capsys.readouterr().out
    assert "NOTE not in the integration ledger since v0.8.0: ATC-0006 " in out
    assert "NOTE in the ledger but no record added since v0.8.0: ATC-0009;" in out and "ATC-0008" not in out
    notes = (repo / "docs/releases/v0.9.0.md").read_text(encoding="utf-8")
    assert [line[2:10] for line in notes.splitlines() if line.startswith("- ATC-")] == \
        ["ATC-0003", "ATC-0002", "ATC-0006"]


@pytest.mark.integration
def test_check_prints_one_line_per_problem_and_exits_one(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    edit(repo, "atc-manifest.yaml", "min_atc: 0.8.0", "min_atc: 0.9.0")
    assert release.main(["check"], root=repo) == 1
    assert capsys.readouterr().out.splitlines() == ["RELEASE_MIN_ATC min_atc 0.9.0 is above atc 0.8.0",
                                                    "PROBLEMS 1 release check"]
    assert release.main(["check", "--json"], root=repo) == 1
    assert json.loads(capsys.readouterr().out)["problems"] == ["RELEASE_MIN_ATC min_atc 0.9.0 is above atc 0.8.0"]

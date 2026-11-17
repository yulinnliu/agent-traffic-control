"""docs/TESTKIT.md § Input evidence: a check's result keyed by its identity and the contents of the files it reads."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from atc_testkit import evidence, inputs, scratch, stages, testrun, verifier
from atc_testkit.project import Project

ENTRIES = [("100644", "a" * 40, "src/x.py"), ("100644", "b" * 40, "docs/a.md")]


def environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return {**env, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
                          env=environment()).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests/test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs/notes.md").write_text("notes\n", encoding="utf-8")
    git(root.parent, "init", "-q", "-b", "main", str(root))
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    return root


def commit(root: Path, name: str, text: str) -> str:
    (root / name).write_text(text, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", name)
    return git(root, "rev-parse", "HEAD")


@pytest.mark.unit
def test_the_digest_reads_each_input_once_in_any_order_and_skips_the_excluded() -> None:
    assert inputs.digest(ENTRIES) == inputs.digest(list(reversed(ENTRIES)))
    assert inputs.digest(ENTRIES, ["docs/a.md"]) == inputs.digest(ENTRIES[:1])
    assert inputs.digest(ENTRIES, ["docs/a.md"]) != inputs.digest([("100644", "c" * 40, "src/x.py")], ["docs/a.md"])
    assert inputs.digest(ENTRIES) != inputs.digest([("100755", "a" * 40, "src/x.py"), ENTRIES[1]])


@pytest.mark.unit
@pytest.mark.parametrize(("recorded", "wanted", "covered"), [
    ("", stages.PARALLEL.marker, True),
    ("", "", True),
    (stages.PRIVATE_EXCLUSION, stages.PARALLEL.marker, True),
    (stages.PRIVATE_EXCLUSION, stages.SERIAL.marker, True),
    (stages.PRIVATE_EXCLUSION, stages.INTERACTIVE.marker, False),
    (stages.PARALLEL.marker, stages.PARALLEL.marker, True),
    (stages.PARALLEL.marker, stages.SERIAL.marker, False),
    (stages.PARALLEL.marker, "", False),
])
def test_a_recorded_expression_covers_a_stage_whose_every_test_it_selects(recorded: str, wanted: str,
                                                                          covered: bool) -> None:
    assert stages.covers(recorded, wanted) is covered


@pytest.mark.integration
def test_a_record_is_found_for_a_commit_whose_inputs_are_equal_and_for_no_other(repository: Path) -> None:
    common = inputs.common_directory(repository)
    first = git(repository, "rev-parse", "HEAD")
    identity = evidence.test_file_identity("tests/test_a.py", "", {"uv.lock": "absent"}, 2.0)
    tree = inputs.entries(inputs.git_reader(repository), first)
    path = inputs.write(common, identity, tree, ["docs/notes.md"], first)
    assert path.name.startswith(inputs.identity_name(identity)) and len(list(inputs.store(common).glob("*.json"))) == 1
    assert inputs.write(common, identity, tree, ["docs/notes.md"], first) == path

    documented = commit(repository, "docs/notes.md", "changed\n")
    finder = inputs.Finder(common, inputs.entries(inputs.git_reader(repository), documented))
    assert finder.find(identity)["ran_on"] == first
    assert finder.find({**identity, "marker": stages.PRIVATE_EXCLUSION}) is None
    assert finder.find({**identity, "time_slack": 3.0}) is None
    changed = commit(repository, "tests/test_a.py", "def test_a():\n    assert True\n")
    assert inputs.Finder(common, inputs.entries(inputs.git_reader(repository), changed)).find(identity) is None


@pytest.mark.integration
def test_a_tampered_record_or_a_missing_excluded_list_is_never_found(repository: Path) -> None:
    common = inputs.common_directory(repository)
    sha = git(repository, "rev-parse", "HEAD")
    identity = evidence.test_file_identity("tests/test_a.py", "", {}, 2.0)
    tree = inputs.entries(inputs.git_reader(repository), sha)
    path = inputs.write(common, identity, tree, [], sha)
    record = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**record, "result": "FAIL"}), encoding="utf-8")
    assert inputs.Finder(common, tree).find(identity) is None
    path.write_text(json.dumps({**record, "key": "0" * 64}), encoding="utf-8")
    assert inputs.Finder(common, tree).find(identity) is None
    path.write_text(json.dumps(record), encoding="utf-8")
    (inputs.store(common) / inputs.EXCLUDED / f"{record['excluded']}.json").unlink()
    assert inputs.Finder(common, tree).find(identity) is None


@pytest.mark.integration
def test_records_older_than_fourteen_days_are_pruned_and_a_shared_excluded_list_stays(repository: Path) -> None:
    common = inputs.common_directory(repository)
    sha = git(repository, "rev-parse", "HEAD")
    tree = inputs.entries(inputs.git_reader(repository), sha)
    old = inputs.write(common, evidence.test_file_identity("a", "", {}, 2.0), tree, ["docs/notes.md"], sha)
    stale = time.time() - inputs.KEEP_SECONDS - 60
    os.utime(old, (stale, stale))
    listing = inputs.store(common) / inputs.EXCLUDED / f"{inputs.keep_excluded(common, ['docs/notes.md'])}.json"
    os.utime(listing, (stale, stale))
    new = inputs.write(common, evidence.test_file_identity("b", "", {}, 2.0), tree, ["docs/notes.md"], sha)
    inputs.prune(common)
    assert not old.exists() and new.exists() and listing.exists()


@pytest.mark.unit
def test_a_plugin_listing_names_files_from_the_repository_top(tmp_path: Path) -> None:
    listing = tmp_path / "listing.json"
    listing.write_text(json.dumps({"rootdir": str(tmp_path / "adapters/python"),
                                   "files": {"tests/test_a.py": "passed", "tests/test_b.py": "failed"}}), encoding="utf-8")
    assert inputs.repository_files(listing, tmp_path, "passed") == ["adapters/python/tests/test_a.py"]
    listing.write_text(json.dumps({"rootdir": str(tmp_path), "files": ["tests/test_a.py"]}), encoding="utf-8")
    assert inputs.repository_files(listing, tmp_path) == ["tests/test_a.py"]
    assert inputs.repository_files(listing, tmp_path / "other") is None
    assert inputs.repository_files(tmp_path / "missing.json", tmp_path) is None


@pytest.mark.integration
def test_the_classifier_listing_must_name_the_commit_asked_for(tmp_path: Path) -> None:
    def argv(listing: dict) -> list[str]:
        return [sys.executable, "-c", f"print('noise'); print({json.dumps(json.dumps(listing))})"]
    good = {"commit": "c" * 40, "excluded": ["docs/a.md"], "dependency_files": ["uv.lock"]}
    assert inputs.classify(argv(good), tmp_path, {}, "c" * 40) == (["docs/a.md"], ["uv.lock"])
    assert inputs.classify(argv(good), tmp_path, {}, "d" * 40) is None
    assert inputs.classify(argv({**good, "excluded": "docs"}), tmp_path, {}, "c" * 40) is None
    assert inputs.classify([sys.executable, "-c", "raise SystemExit(1)"], tmp_path, {}, "c" * 40) is None


@pytest.mark.integration
def test_the_plugin_writes_each_files_outcome_and_the_collected_files(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text('pytest_plugins = ("atc_testkit.plugin",)\n', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (tmp_path / "test_good.py").write_text(
        "import pytest\n\n\n@pytest.mark.unit\ndef test_one():\n    pass\n\n\n@pytest.mark.unit\n"
        "@pytest.mark.skip\ndef test_two():\n    pass\n", encoding="utf-8")
    (tmp_path / "test_bad.py").write_text(
        "import pytest\n\n\n@pytest.mark.unit\ndef test_one():\n    pass\n\n\n@pytest.mark.unit\n"
        "def test_two():\n    assert False\n", encoding="utf-8")
    outcomes, collected = tmp_path / "outcomes.json", tmp_path / "collected.json"
    env = {**os.environ, inputs.OUTCOMES: str(outcomes), inputs.COLLECTED: str(collected),
           "PYTEST_DEBUG_TEMPROOT": str(tmp_path)}
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
                          "--basetemp", str(tmp_path / "bt")], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert run.returncode == 1, run.stdout
    assert json.loads(outcomes.read_text(encoding="utf-8"))["files"] == {"test_bad.py": "failed",
                                                                          "test_good.py": "passed"}
    assert json.loads(collected.read_text(encoding="utf-8"))["files"] == ["test_bad.py", "test_good.py"]


@pytest.mark.integration
def test_a_run_that_ends_other_than_passed_or_failed_writes_no_outcomes(tmp_path: Path) -> None:
    """A collection error interrupts the session before every file ran: the files that did run say nothing about
    a whole file's outcome."""
    (tmp_path / "conftest.py").write_text('pytest_plugins = ("atc_testkit.plugin",)\n', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (tmp_path / "test_good.py").write_text("import pytest\n\n\n@pytest.mark.unit\ndef test_one():\n    pass\n",
                                           encoding="utf-8")
    (tmp_path / "test_broken.py").write_text("def test_one(:\n", encoding="utf-8")
    outcomes = tmp_path / "outcomes.json"
    env = {**os.environ, inputs.OUTCOMES: str(outcomes), "PYTEST_DEBUG_TEMPROOT": str(tmp_path)}
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
                          "--basetemp", str(tmp_path / "bt")], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert run.returncode == pytest.ExitCode.INTERRUPTED, run.stdout
    assert not outcomes.exists()


@pytest.mark.integration
def test_a_run_records_each_passed_file_only_when_the_tree_is_its_commit(repository: Path, tmp_path: Path) -> None:
    argv_file = tmp_path / "verify.json"
    script = ("import json, os; print(json.dumps({'commit': os.environ['ATC_INPUTS_OF'], "
              "'excluded': ['docs/notes.md'], 'dependency_files': ['uv.lock']}))")
    argv_file.write_text(json.dumps([sys.executable, "-c", script]), encoding="utf-8")
    outcomes = tmp_path / "outcomes.json"
    outcomes.write_text(json.dumps({"rootdir": str(repository), "files": {"tests/test_a.py": "passed"}}),
                        encoding="utf-8")
    sha = git(repository, "rev-parse", "HEAD")
    source = testrun.RunSource.of(repository, tmp_path, None)
    line = testrun.record_inputs(source, argv_file, outcomes, True, {})
    assert line.startswith(f"EVIDENCE 1 test files recorded for {sha[:12]} (inputs ")
    (record,) = [json.loads(path.read_text(encoding="utf-8"))
                 for path in inputs.store(inputs.common_directory(repository)).glob("*.json")]
    assert record["identity"]["marker"] == stages.PRIVATE_EXCLUSION
    assert record["identity"]["dependency_inputs"] == {"uv.lock": "absent"} and record["ran_on"] == sha

    (repository / "tests/test_a.py").write_text("changed\n", encoding="utf-8")
    dirty = testrun.RunSource.of(repository, tmp_path, None)
    assert testrun.record_inputs(dirty, argv_file, outcomes, True, {}).startswith(
        f"{testrun.INPUTS_NOT_RECORDED} the tree is not {sha[:12]} (changed: M tests/test_a.py)")


@pytest.mark.integration
def test_an_export_run_is_classified_by_its_own_tree_whatever_the_source_worktree_holds(
        repository: Path, tmp_path: Path) -> None:
    """An export is no Git checkout: its own verifier classifies it in the export, Git reads served from the source
    repository's objects, while the source worktree has moved on and holds an uncommitted edit; nothing is left in
    the export or the worktree (docs/TESTKIT.md § Input evidence)."""
    argv_file = tmp_path / "verify.json"
    # The stand-in verifier reads the commit from Git and its classification from a file of the tree it runs in.
    script = ("import json, os, subprocess; commit = os.environ['ATC_INPUTS_OF']; "
              "subprocess.run(['git', 'cat-file', '-e', commit], check=True); "
              "excluded = open('classify.txt', encoding='utf-8').read().split(); "
              "print(json.dumps({'commit': commit, 'excluded': excluded, 'dependency_files': []}))")
    argv_file.write_text(json.dumps([sys.executable, "-c", script]), encoding="utf-8")
    sha = commit(repository, "classify.txt", "docs/notes.md\n")
    place = tmp_path / "scratch"
    export = place / "tree"
    for path in ("tests/test_a.py", "docs/notes.md", "classify.txt"):
        (export / path).parent.mkdir(parents=True, exist_ok=True)
        (export / path).write_bytes((repository / path).read_bytes())
    moved = commit(repository, "classify.txt", "tests/test_a.py\n")
    (repository / "classify.txt").write_text("docs/notes.md tests/test_a.py\n", encoding="utf-8")
    status = git(repository, "status", "--porcelain")
    outcomes = tmp_path / "outcomes.json"
    outcomes.write_text(json.dumps({"rootdir": str(export), "files": {"tests/test_a.py": "passed"}}), encoding="utf-8")
    row = scratch.Row("p", str(place), str(export), "2026-11-17T00:00:00Z", "ATC-0001", str(repository), sha)
    source = testrun.RunSource.of(export, place, row)
    assert source is not None and not source.worktree
    line = testrun.record_inputs(source, argv_file, outcomes, False, environment())
    assert line.startswith(f"EVIDENCE 1 test files recorded for {sha[:12]} (inputs "), line
    common = inputs.common_directory(repository)
    (record,) = [json.loads(path.read_text(encoding="utf-8")) for path in inputs.store(common).glob("*.json")]
    assert record["ran_on"] == sha and inputs.read_excluded(common, record["excluded"]) == ["docs/notes.md"]
    assert not os.path.lexists(export / ".git") and not (place / testrun.CLASSIFY_GIT).exists()
    assert git(repository, "rev-parse", "HEAD") == moved and git(repository, "status", "--porcelain") == status
    # an export that holds a .git of its own is not classified: nothing is recorded
    (export / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    assert testrun.classify_export(json.loads(argv_file.read_text(encoding="utf-8")), export, repository,
                                   environment(), sha) is None
    assert (export / ".git").read_text(encoding="utf-8") == "gitdir: elsewhere\n"


@pytest.mark.integration
def test_the_verifier_in_classifier_mode_prints_the_excluded_paths_and_runs_no_check(
        repository: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = Project(root=repository, argv_file=repository / "verify.json",
                      documentation_only=lambda paths: all(path.endswith(".md") for path in paths),
                      dependency_files=("uv.lock",))
    sha = git(repository, "rev-parse", "HEAD")
    assert verifier.main(project, {inputs.CLASSIFY: sha}) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {
        "commit": sha, "excluded": ["docs/notes.md"], "dependency_files": ["uv.lock"]}

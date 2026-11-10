"""docs/ATC_HOME.md § Scratch registry and § Deleting: ``atc tmp list``, ``clean`` and ``prune``."""
from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import subprocess

import pytest

import atc_testkit
from atc_testkit import isolation, locations, scratch


def link_directory(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def unlink_directory(link: Path) -> None:
    os.rmdir(link) if os.name == "nt" else link.unlink()


def write(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def target(tmp_path: Path, name: str) -> Path:
    write(tmp_path / "outer" / name / "keep.txt", name)
    return tmp_path / "outer" / name


def scratch_dir(root: Path, name: str) -> Path:
    write(root / name / "tree/src/pkg/__init__.py")
    write(root / name / "qdr/data.txt")
    return root / name


def clean(*dirs: Path | str, tree_name: str | None = None) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = scratch.clean([str(item) for item in dirs], tree_name, say=lines.append)
    return code, lines


def add(root: Path, *directories: Path, project: str = "tree") -> None:
    for directory in directories:
        scratch.register(root, directory, directory / project)


def registered_names(root: Path) -> list[str]:
    return [Path(row.scratch).name for row in scratch.registered(root)]


def test_expected_links_are_unlinked_and_their_targets_survive(tmp_path: Path, atc_tmp: Path) -> None:
    real = scratch_dir(atc_tmp, "real")
    write(real / ".venv/Lib/site.py")
    venv = scratch_dir(atc_tmp, "venv")
    link_directory(venv / ".venv", target(tmp_path, "venv"))
    tree_venv = scratch_dir(atc_tmp, "treevenv")
    link_directory(tree_venv / "tree/.venv", target(tmp_path, "treevenv"))
    bt = scratch_dir(atc_tmp, "bt")
    link_directory(bt / "bt/test_x0/link", target(tmp_path, "bt"))
    write(bt / "bt/test_x0/file.txt")
    other = atc_tmp / "other"
    add(atc_tmp, real, venv, tree_venv, bt, other)
    code, lines = clean(real, venv, tree_venv, bt)
    assert code == 0, lines
    assert lines == [f"DELETED {d}" for d in (real, venv, tree_venv, bt)]
    assert not real.exists() and not venv.exists() and not tree_venv.exists() and not bt.exists()
    for name in ("venv", "treevenv", "bt"):
        assert (tmp_path / "outer" / name / "keep.txt").read_text(encoding="utf-8") == name
    assert registered_names(atc_tmp) == ["other"]


@pytest.mark.integration
def test_a_venv_beside_a_pyproject_below_tree_is_unlinked_even_when_its_target_is_gone(tmp_path: Path,
                                                                                        atc_tmp: Path) -> None:
    whole = scratch_dir(atc_tmp, "whole")
    write(whole / "tree/adapters/python/pyproject.toml")
    link_directory(whole / "tree/adapters/python/.venv", target(tmp_path, "venv"))
    gone = scratch_dir(atc_tmp, "gone")
    write(gone / "tree/adapters/python/pyproject.toml")
    link_directory(gone / "tree/adapters/python/.venv", target(tmp_path, "removed"))
    for path in (tmp_path / "outer/removed/keep.txt", tmp_path / "outer/removed"):
        os.remove(path) if path.is_file() else os.rmdir(path)
    code, lines = clean(whole, gone)
    assert (code, lines) == (0, [f"DELETED {whole}", f"DELETED {gone}"])
    assert not whole.exists() and not gone.exists()
    assert (tmp_path / "outer/venv/keep.txt").read_text(encoding="utf-8") == "venv"


@pytest.mark.integration
@pytest.mark.parametrize("case", ["no_pyproject", "pyproject_is_a_directory", "other_name", "outside_tree"])
def test_a_nested_venv_without_its_pyproject_is_an_anomaly(tmp_path: Path, atc_tmp: Path, case: str) -> None:
    odd = scratch_dir(atc_tmp, "odd")
    place = {"outside_tree": odd / "qdr/sub"}.get(case, odd / "tree/adapters/python")
    if case == "pyproject_is_a_directory":
        (place / "pyproject.toml").mkdir(parents=True)
    elif case != "no_pyproject":
        write(place / "pyproject.toml")
    link = place / ("venv" if case == "other_name" else ".venv")
    link_directory(link, target(tmp_path, "venv"))
    assert clean(odd) == (1, [f"ANOMALY {link}", f"REFUSED {odd} (1 unexpected links)"])
    assert (link / "keep.txt").is_file() and (odd / "qdr/data.txt").is_file()
    unlink_directory(link)


@pytest.mark.integration
def test_a_nested_venv_beside_a_link_elsewhere_is_refused_with_both_untouched(tmp_path: Path, atc_tmp: Path) -> None:
    mixed = scratch_dir(atc_tmp, "mixed")
    write(mixed / "tree/adapters/python/pyproject.toml")
    link_directory(mixed / "tree/adapters/python/.venv", target(tmp_path, "venv"))
    link_directory(mixed / "tree/src/hidden", target(tmp_path, "hidden"))
    assert clean(mixed) == (1, [f"ANOMALY {mixed / 'tree/src/hidden'}", f"REFUSED {mixed} (1 unexpected links)"])
    for name, link in (("venv", "tree/adapters/python/.venv"), ("hidden", "tree/src/hidden")):
        assert (mixed / link / "keep.txt").read_text(encoding="utf-8") == name
        assert (tmp_path / "outer" / name / "keep.txt").is_file()
    assert (mixed / "qdr/data.txt").is_file()
    for link in ("tree/adapters/python/.venv", "tree/src/hidden"):
        unlink_directory(mixed / link)


def test_a_link_elsewhere_refuses_that_tree_untouched_and_the_rest_continue(tmp_path: Path, atc_tmp: Path) -> None:
    odd = scratch_dir(atc_tmp, "odd")
    link_directory(odd / ".venv", target(tmp_path, "venv"))
    link_directory(odd / "bt/test_x0/link", target(tmp_path, "bt"))
    link_directory(odd / "tree/src/hidden", target(tmp_path, "hidden"))
    inner = scratch_dir(atc_tmp, "inner")
    write(inner / ".venv/Lib/site.py")
    link_directory(inner / ".venv/Lib/link", target(tmp_path, "inner"))
    plain = scratch_dir(atc_tmp, "plain")
    add(atc_tmp, odd, plain)
    code, lines = clean(odd, inner, plain)
    assert code == 1
    assert lines == [f"ANOMALY {odd / 'tree/src/hidden'}", f"REFUSED {odd} (1 unexpected links)",
                     f"ANOMALY {inner / '.venv/Lib/link'}", f"REFUSED {inner} (1 unexpected links)", f"DELETED {plain}"]
    assert (inner / ".venv/Lib/link/keep.txt").is_file() and (inner / ".venv/Lib/site.py").is_file()
    for link in (odd / ".venv", odd / "bt/test_x0/link", odd / "tree/src/hidden"):
        assert (link / "keep.txt").is_file()
    assert (odd / "qdr/data.txt").is_file() and not plain.exists()
    assert registered_names(atc_tmp) == ["odd"]


@pytest.mark.parametrize("where", ["outside", "root", "dotdot", "under_link", "nested", "systmp", "candidates",
                                   "SYSTMP", "named_dotdot"])
def test_a_path_not_inside_the_root_refuses_the_whole_run(tmp_path: Path, atc_tmp: Path, where: str) -> None:
    inside = scratch_dir(atc_tmp, "inside")
    outside = scratch_dir(tmp_path, "outside")
    link_directory(atc_tmp / "link", outside)
    (atc_tmp / "systmp").mkdir()
    bad = {"outside": outside, "root": atc_tmp, "dotdot": f"{atc_tmp}{os.sep}..{os.sep}..{os.sep}outside",
           "under_link": atc_tmp / "link" / "tree", "nested": inside / "tree", "systmp": atc_tmp / "systmp",
           "candidates": "candidates", "SYSTMP": "SYSTMP", "named_dotdot": ".."}[where]
    code, lines = clean(inside, bad)
    assert code == 2
    assert len(lines) == 1 and lines[0].startswith("REFUSED ")
    assert (inside / "qdr/data.txt").is_file() and (outside / "tree/src/pkg/__init__.py").is_file()
    assert (atc_tmp / "systmp").is_dir()
    unlink_directory(atc_tmp / "link")


def test_a_directory_under_a_link_back_into_the_root_is_refused(atc_tmp: Path) -> None:
    victim = scratch_dir(atc_tmp, "victim")
    link_directory(atc_tmp / "loop", atc_tmp)
    code, lines = clean(atc_tmp / "loop" / "victim")
    assert code == 2 and lines[0].startswith("REFUSED ") and victim.is_dir()
    unlink_directory(atc_tmp / "loop")


def test_a_bare_name_and_a_short_form_resolve_to_the_child(atc_tmp: Path) -> None:
    named = scratch_dir(atc_tmp, "named")
    assert clean("named") == (0, [f"DELETED {named}"])
    if os.name == "nt":
        import ctypes
        buffer = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetShortPathNameW(str(atc_tmp), buffer, 1024)
        shorter = scratch_dir(atc_tmp, "shorter")
        assert clean(f"{buffer.value}\\shorter") == (0, [f"DELETED {shorter}"])


def short_form(path: Path) -> str:
    """The 8.3 form of ``path``; the test skips on a volume that has none."""
    import ctypes
    buffer = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 1024)
    if os.path.basename(buffer.value).lower() == path.name.lower():
        pytest.skip("this volume keeps no 8.3 names")
    return buffer.value


windows = pytest.mark.skipif(os.name != "nt", reason="a form of a name only Windows resolves")


@windows
def test_a_reserved_name_typed_in_its_short_form_is_refused(atc_tmp: Path) -> None:
    held = write(atc_tmp / "candidates/keep.txt")
    code, lines = clean(short_form(atc_tmp / "candidates"))
    assert code == 2 and len(lines) == 1 and lines[0].startswith("REFUSED ") and "reserved name" in lines[0]
    assert held.is_file()


@windows
def test_a_scratch_typed_in_its_short_form_is_the_scratch(atc_tmp: Path) -> None:
    directory = scratch_dir(atc_tmp, "a-long-scratch-name")
    add(atc_tmp, directory)
    assert clean(short_form(directory)) == (0, [f"DELETED {directory}"])
    assert not directory.exists() and registered_names(atc_tmp) == []


@windows
@pytest.mark.parametrize("name", ["systmp", "plain"])
@pytest.mark.parametrize("stream", ["::$INDEX_ALLOCATION", ":$I30:$INDEX_ALLOCATION", ":name"])
@pytest.mark.parametrize("bare", [True, False])
def test_a_stream_form_is_refused(atc_tmp: Path, name: str, stream: str, bare: bool) -> None:
    held = write(atc_tmp / name / "keep.txt")
    code, lines = clean(name + stream if bare else f"{atc_tmp}{os.sep}{name}{stream}")
    assert code == 2 and len(lines) == 1 and lines[0].startswith("REFUSED ")
    assert held.is_file()


@windows
@pytest.mark.parametrize("typed", ["systmp.", "systmp ", "systmp...", "CANDIDATES."])
def test_a_name_windows_trims_to_a_reserved_one_is_refused(atc_tmp: Path, typed: str) -> None:
    (atc_tmp / "candidates").mkdir()
    held = write(atc_tmp / "systmp/keep.txt")
    code, lines = clean(typed)
    assert code == 2 and lines[0].startswith("REFUSED ") and held.is_file()


@windows
@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\"])
def test_a_device_form_of_a_reserved_name_is_refused(atc_tmp: Path, prefix: str) -> None:
    held = write(atc_tmp / "systmp/keep.txt")
    code, lines = clean(f"{prefix}{atc_tmp}{os.sep}systmp")
    assert code == 2 and lines[0].startswith("REFUSED ") and held.is_file()


def test_a_name_outside_the_scratch_form_is_refused(atc_tmp: Path) -> None:
    held = write(atc_tmp / "a name/keep.txt")
    code, lines = clean(atc_tmp / "a name")
    assert code == 2 and "not a scratch name" in lines[0] and held.is_file()


def test_the_registry_passes_the_same_name_checks(atc_tmp: Path) -> None:
    held = write(atc_tmp / "systmp/keep.txt")
    scratch.register(atc_tmp, f"{atc_tmp}{os.sep}systmp.x:y", atc_tmp)
    code, lines = clean(tree_name=atc_tmp.name)
    assert code == 2 and lines[0].startswith("REFUSED ") and held.is_file()


def test_the_recursive_delete_refuses_a_directory_that_is_a_link(tmp_path: Path, atc_tmp: Path) -> None:
    link = atc_tmp / "link"
    link_directory(link, target(tmp_path, "victim"))
    lines: list[str] = []
    assert scratch.remove_tree(link, scratch.no_link, lines.append) == "is itself a link"
    assert (link / "keep.txt").is_file()


@windows
def test_the_run_refuses_the_same_forms_of_a_reserved_name(atc_tmp: Path) -> None:
    (atc_tmp / "candidates").mkdir()
    for typed in (short_form(atc_tmp / "candidates"), f"{atc_tmp}{os.sep}candidates::$INDEX_ALLOCATION", "candidates."):
        with pytest.raises(scratch.Refusal) as refused:
            scratch.resolve_scratch(typed, atc_tmp)
        assert refused.value.token == "SCRATCH_OUTSIDE_TMP"


@pytest.mark.skipif(os.name != "nt", reason="a read-only reparse point is a Windows state")
def test_a_link_that_survives_removal_blocks_the_recursive_delete(tmp_path: Path, atc_tmp: Path) -> None:
    stuck = scratch_dir(atc_tmp, "stuck")
    link = stuck / "bt/test_x0/link"
    link_directory(link, target(tmp_path, "bt"))
    subprocess.run(["cmd", "/c", "attrib", "+r", str(link), "/L"], check=True, capture_output=True)
    try:
        code, lines = clean(stuck)
        assert code == 1
        assert lines == [f"LEFT {link}", f"REFUSED {stuck} (1 links remain after link removal)"]
        assert (link / "keep.txt").is_file() and (stuck / "qdr/data.txt").is_file()
    finally:
        subprocess.run(["cmd", "/c", "attrib", "-r", str(link), "/L"], capture_output=True)


def test_a_link_removal_failure_is_left_to_the_rescan(tmp_path: Path, atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stuck = scratch_dir(atc_tmp, "stuck")
    link = stuck / "bt/link"
    link_directory(link, target(tmp_path, "bt"))
    monkeypatch.setattr(scratch, "_remove_link", lambda _path: None)
    code, lines = clean(stuck)
    assert code == 1 and lines == [f"LEFT {link}", f"REFUSED {stuck} (1 links remain after link removal)"]
    assert (stuck / "qdr/data.txt").is_file()
    monkeypatch.undo()


def test_a_dir_that_is_itself_a_link_is_refused(tmp_path: Path, atc_tmp: Path) -> None:
    link = atc_tmp / "link"
    link_directory(link, target(tmp_path, "whole"))
    code, lines = clean(link)
    assert code == 1 and lines == [f"REFUSED {link} (is itself a link)"]
    assert (link / "keep.txt").is_file()


def test_a_file_is_refused_as_not_a_directory(atc_tmp: Path) -> None:
    item = write(atc_tmp / "item.txt")
    assert clean(item) == (1, [f"REFUSED {item} (not a directory)"]) and item.is_file()


def test_a_missing_dir_is_skipped_and_drops_its_registry_line(atc_tmp: Path) -> None:
    gone = atc_tmp / "gone"
    add(atc_tmp, gone)
    assert clean(gone) == (0, [f"SKIPPED {gone} (not found)"])
    assert registered_names(atc_tmp) == []


def test_an_error_while_handling_a_dir_is_its_refusal_and_the_rest_continue(atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = scratch_dir(atc_tmp, "first"), scratch_dir(atc_tmp, "second")
    original = scratch.remove_tree

    def failing(directory, expected, say):
        if str(directory) == str(first):
            raise OSError("boom")
        return original(directory, expected, say)
    monkeypatch.setattr(scratch, "remove_tree", failing)
    code, lines = clean(first, second)
    assert code == 1 and lines == [f"REFUSED {first} (error: boom)", f"DELETED {second}"]


def test_read_only_files_are_deleted(atc_tmp: Path) -> None:
    directory = scratch_dir(atc_tmp, "git")
    item = write(directory / "tree/.git/objects/ab/cd")
    item.chmod(0o400)
    assert clean(directory) == (0, [f"DELETED {directory}"])


@pytest.mark.skipif(os.name != "nt", reason="the Windows path limit")
def test_a_tree_beyond_the_windows_path_limit_is_deleted(atc_tmp: Path) -> None:
    directory = scratch_dir(atc_tmp, "deep")
    deep = "\\\\?\\" + str(directory / "bt") + ("\\" + "d" * 100) * 4
    os.makedirs(deep)
    with open(deep + "\\file.txt", "w", encoding="utf-8") as stream:
        stream.write("x")
    assert clean(directory) == (0, [f"DELETED {directory}"])


def test_the_only_recursive_delete_rescans_for_links_first() -> None:
    package = Path(atc_testkit.__file__).parent
    calls = [(path.name, node) for path in package.glob("*.py") for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "rmtree"]
    assert [name for name, _node in calls] == ["scratch.py"]
    source = (package / "scratch.py").read_text(encoding="utf-8")
    body = source[source.index("def remove_tree"):source.index("shutil.rmtree(")]
    assert "left = _links(directory)" in body and "if left:" in body and "return f\"{len(left)} links remain" in body


@pytest.mark.unit
def test_clean_by_tree_name_takes_its_directories_from_the_registry(atc_tmp: Path) -> None:
    one, two = scratch_dir(atc_tmp, "one"), scratch_dir(atc_tmp, "two")
    scratch.register(atc_tmp, one, write(one / "tree/pyproject.toml", '[project]\nname = "alpha"\n').parent)
    scratch.register(atc_tmp, two, write(two / "tree/pyproject.toml", '[project]\nname = "beta"\n').parent)
    assert clean(tree_name="alpha") == (0, [f"DELETED {one}"])
    assert two.is_dir() and registered_names(atc_tmp) == ["two"]
    assert clean(tree_name="absent") == (0, [])


@pytest.mark.unit
def test_clean_by_tree_name_passes_the_same_checks(atc_tmp: Path, tmp_path: Path) -> None:
    outside = scratch_dir(tmp_path, "outside")
    scratch.register(atc_tmp, outside, outside / "tree")
    code, lines = clean(tree_name="tree")
    assert code == 2 and lines[0].startswith("REFUSED ") and outside.is_dir()


# the registry


def test_the_registry_format(atc_tmp: Path, tmp_path: Path) -> None:
    tree = write(tmp_path / "t/pyproject.toml", '[project]\nname = "alpha"\n').parent
    scratch.register(atc_tmp, atc_tmp / "s", tree, datetime(2026, 11, 6, 1, 2, 3, tzinfo=UTC))
    raw = (atc_tmp / "scratch-dirs.tsv").read_bytes()
    assert raw == (f"project\tscratch\ttree\tcreated_utc\ttask\tsource\tcommit\n"
                   f"alpha\t{atc_tmp / 's'}\t{tree}\t2026-11-06T01:02:03Z\t\t\t\n").encode()
    assert not list(atc_tmp.glob("scratch-dirs.tsv.*"))
    scratch.register(atc_tmp, atc_tmp / "t", tree, datetime(2026, 11, 6, 1, 2, 3, tzinfo=UTC), task="ATC-0008",
                     source=str(tmp_path), commit="a" * 40, project="beta")
    assert scratch.registered(atc_tmp)[1] == scratch.Row("beta", str(atc_tmp / "t"), str(tree), "2026-11-06T01:02:03Z",
                                                         "ATC-0008", str(tmp_path), "a" * 40)


@pytest.mark.unit
def test_a_four_column_registry_is_read_and_kept(atc_tmp: Path, tmp_path: Path) -> None:
    old = f"project\tscratch\ttree\tcreated_utc\nalpha\t{atc_tmp / 'old'}\t{tmp_path}\t2026-11-06T01:02:03Z\n"
    (atc_tmp / "scratch-dirs.tsv").write_bytes(old.encode())
    assert scratch.registered(atc_tmp) == [scratch.Row("alpha", str(atc_tmp / "old"), str(tmp_path),
                                                       "2026-11-06T01:02:03Z")]
    scratch.register(atc_tmp, atc_tmp / "new", tmp_path, task="ATC-0008")
    lines = (atc_tmp / "scratch-dirs.tsv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == scratch.REGISTRY_HEADER and lines[1] == old.splitlines()[1]
    assert [row.task for row in scratch.registered(atc_tmp)] == ["", "ATC-0008"]


def test_a_scratch_gets_one_line(atc_tmp: Path, tmp_path: Path) -> None:
    add(atc_tmp, atc_tmp / "s", atc_tmp / "s")
    assert registered_names(atc_tmp) == ["s"]


def test_the_project_falls_back_to_the_tree_name(tmp_path: Path) -> None:
    tree = tmp_path / "named"
    tree.mkdir()
    assert scratch.project_name(tree) == "named"
    write(tree / "pyproject.toml", "[tool.x]\n")
    assert scratch.project_name(tree) == "named"
    write(tree / "pyproject.toml", "not toml [")
    assert scratch.project_name(tree) == "named"


def test_concurrent_changes_keep_every_line(atc_tmp: Path, tmp_path: Path) -> None:
    import threading
    threads = [threading.Thread(target=scratch.register, args=(atc_tmp, atc_tmp / f"s{i}", tmp_path)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(scratch.registered(atc_tmp)) == 20


def test_list_prints_the_registry_with_sizes_and_deletes_nothing(atc_tmp: Path, tmp_path: Path) -> None:
    present = atc_tmp / "present"
    write(present / "file.txt", "12345")
    add(atc_tmp, present, atc_tmp / "absent")
    lines: list[str] = []
    assert scratch.list_scratches(say=lines.append) == 0
    assert lines[0] == "project\tscratch\ttree\tcreated_utc\ttask\tsource\tcommit\tsize"
    assert lines[1].endswith("\t5") and lines[2].endswith("\tmissing")
    assert present.is_dir()


# prune


NOW = datetime.now(UTC) + timedelta(days=8, minutes=1)


def age(path: Path, days: float) -> Path:
    """A link keeps its creation time, eight days before ``NOW``; the rest take the given age."""
    stamp = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def prune() -> tuple[int, list[str]]:
    lines: list[str] = []
    return scratch.prune(say=lines.append, now=NOW), lines


def test_prune_takes_old_entries_of_systmp_except_pytest(atc_tmp: Path) -> None:
    systmp = atc_tmp / "systmp"
    old_file = age(write(systmp / "old.txt"), 8)
    new_file = age(write(systmp / "new.txt"), 6)
    write(systmp / "w1/stage/log.txt")
    old_dir = age(systmp / "w1", 8)
    write(systmp / "pytest-of-me/pytest-0/x")
    pytest_dir = age(systmp / "pytest-of-me", 30)
    code, lines = prune()
    assert code == 0 and sorted(lines) == sorted([f"DELETED {old_file}", f"DELETED {old_dir}"])
    assert not old_file.exists() and not old_dir.exists() and new_file.exists() and pytest_dir.exists()


def test_prune_refuses_a_link_and_a_directory_that_holds_one(tmp_path: Path, atc_tmp: Path) -> None:
    systmp = atc_tmp / "systmp"
    link_directory(systmp / "alink", target(tmp_path, "a"))
    age(systmp / "alink", 8)
    held = write(systmp / "held/file.txt").parent
    link_directory(held / "bt/inner", target(tmp_path, "b"))
    age(held, 8)
    code, lines = prune()
    assert code == 1
    assert lines == [f"REFUSED {systmp / 'alink'} (is itself a link)", f"ANOMALY {held / 'bt/inner'}",
                     f"REFUSED {held} (1 unexpected links)"]
    assert (systmp / "alink/keep.txt").is_file() and (held / "file.txt").is_file()


@pytest.mark.integration
@pytest.mark.parametrize("stage", ["tests", "parallel", "serial", "interactive"])
def test_prune_takes_a_kept_working_directory_and_never_enters_its_links(tmp_path: Path, atc_tmp: Path, stage: str) -> None:
    work = write(atc_tmp / "systmp/wab12cd" / stage / "log.txt").parent.parent
    kept = target(tmp_path, "a")
    (work / stage / "bt/test0").mkdir(parents=True)
    link_directory(work / stage / "bt/test0/data", kept)
    age(work, 8)
    assert prune() == (0, [f"DELETED {work}"])
    assert not work.exists() and (kept / "keep.txt").is_file()


@pytest.mark.integration
def test_prune_refuses_a_working_directory_with_a_link_outside_a_base_directory(tmp_path: Path, atc_tmp: Path) -> None:
    work = write(atc_tmp / "systmp/wab12cd/tests/log.txt").parent.parent
    link_directory(work / "tests/data", target(tmp_path, "a"))
    age(work, 8)
    assert prune() == (1, [f"ANOMALY {work / 'tests/data'}", f"REFUSED {work} (1 unexpected links)"])
    assert (work / "tests/log.txt").is_file()


@pytest.mark.integration
@pytest.mark.parametrize("first", ["home", "collection", "static", "slow"])
def test_prune_refuses_a_link_under_a_bt_of_a_directory_that_names_no_stage(tmp_path: Path, atc_tmp: Path, first: str) -> None:
    work = write(atc_tmp / "systmp/wab12cd" / first / "log.txt").parent.parent
    kept = target(tmp_path, "a")
    (work / first / "bt").mkdir()
    link_directory(work / first / "bt/x", kept)
    age(work, 8)
    assert prune() == (1, [f"ANOMALY {work / first / 'bt/x'}", f"REFUSED {work} (1 unexpected links)"])
    assert (kept / "keep.txt").is_file() and (work / first / "log.txt").is_file()


@pytest.mark.integration
def test_prune_refuses_a_base_directory_that_is_itself_a_link(tmp_path: Path, atc_tmp: Path) -> None:
    work = write(atc_tmp / "systmp/wab12cd/tests/log.txt").parent.parent
    kept = target(tmp_path, "a")
    link_directory(work / "tests/bt", kept)
    age(work, 8)
    assert prune() == (1, [f"ANOMALY {work / 'tests/bt'}", f"REFUSED {work} (1 unexpected links)"])
    assert (kept / "keep.txt").is_file() and (work / "tests/log.txt").is_file()


@pytest.mark.integration
@pytest.mark.parametrize("name", ["wab12c", "wab12cd0", "xab12cd", "wAB12CD"])
def test_prune_expects_no_link_in_a_directory_not_named_as_a_working_directory(tmp_path: Path, atc_tmp: Path, name: str) -> None:
    held = write(atc_tmp / "systmp" / name / "tests/log.txt").parent.parent
    (held / "tests/bt").mkdir()
    link_directory(held / "tests/bt/data", target(tmp_path, "a"))
    age(held, 8)
    assert prune() == (1, [f"ANOMALY {held / 'tests/bt/data'}", f"REFUSED {held} (1 unexpected links)"])


@pytest.mark.integration
def test_a_created_working_directory_has_the_name_prune_knows(atc_tmp: Path) -> None:
    assert isolation.WORK_NAME.fullmatch(isolation.create_working_directory().name)


def test_prune_reports_old_registered_scratches_and_deletes_none(atc_tmp: Path) -> None:
    old, new = scratch_dir(atc_tmp, "old"), scratch_dir(atc_tmp, "new")
    scratch.register(atc_tmp, old, old / "tree", NOW - timedelta(days=8))
    scratch.register(atc_tmp, new, new / "tree", NOW - timedelta(days=1))
    code, lines = prune()
    assert code == 0 and lines == [f"OLD {old} (tree, created {(NOW - timedelta(days=8)).strftime(scratch.TIME_FORMAT)})"]
    assert old.is_dir() and new.is_dir()


def git(cwd: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root / "a.txt")
    (root / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "x")
    return root


def test_prune_removes_a_candidate_through_its_repository(repository: Path, atc_tmp: Path) -> None:
    tree = atc_tmp / "candidates/abc"
    git(repository, "worktree", "add", "-q", "--detach", str(tree))
    write(tree / ".venv/Lib/site.py")
    report = age(write(atc_tmp / "candidates/abc.report.json"), 8)
    age(tree, 8)
    code, lines = prune()
    assert code == 0 and sorted(lines) == sorted([f"DELETED {tree}", f"DELETED {report}"])
    assert not tree.exists() and tree.as_posix() not in git(repository, "worktree", "list", "--porcelain")


def test_a_candidate_git_refuses_is_left_with_gits_message(repository: Path, atc_tmp: Path) -> None:
    tree = atc_tmp / "candidates/dirty"
    git(repository, "worktree", "add", "-q", "--detach", str(tree))
    write(tree / "a.txt", "changed\n")
    age(tree, 8)
    code, lines = prune()
    assert code == 1 and lines[0].startswith(f"REFUSED {tree} (") and "fatal" in lines[0]
    assert (tree / "a.txt").is_file()
    git(repository, "worktree", "remove", "--force", str(tree))


def test_a_tree_git_no_longer_knows_is_any_other_entry(repository: Path, atc_tmp: Path) -> None:
    tree = atc_tmp / "candidates/orphan"
    git(repository, "worktree", "add", "-q", "--detach", str(tree))
    git(repository, "worktree", "remove", "--force", str(tree))
    write(tree / ".git", "gitdir: " + str(repository / ".git/worktrees/orphan") + "\n")
    age(tree, 8)
    plain = write(atc_tmp / "candidates/plain/file.txt").parent
    age(plain, 8)
    code, lines = prune()
    assert code == 0 and sorted(lines) == sorted([f"DELETED {tree}", f"DELETED {plain}"])


@pytest.fixture()
def parent_appears_after_locate(atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A parent that turns into a link or a file after ``locate`` read it reaches prune's own guards."""
    monkeypatch.setattr(scratch.locations, "locate", lambda _environ=None: locations.Locations(atc_tmp, atc_tmp / "systmp"))


@pytest.mark.parametrize("parent", ["systmp", "candidates"])
def test_prune_refuses_a_parent_that_is_a_link_and_never_enters_it(tmp_path: Path, atc_tmp: Path, parent: str,
                                                                   parent_appears_after_locate) -> None:
    victim = target(tmp_path, "victim")
    old = age(write(victim / "old.txt"), 30)
    age(write(victim / "dir/file.txt").parent, 30)
    if parent == "systmp":
        (atc_tmp / "systmp").rmdir() if (atc_tmp / "systmp").exists() else None
    link_directory(atc_tmp / parent, victim)
    code, lines = prune()
    assert code == 1 and lines == [f"REFUSED {atc_tmp / parent} (is itself a link)"]
    assert old.is_file() and (victim / "dir/file.txt").is_file() and (victim / "keep.txt").is_file()


def test_a_linked_parent_leaves_the_other_parent_and_the_report_to_run(tmp_path: Path, atc_tmp: Path,
                                                                       parent_appears_after_locate) -> None:
    link_directory(atc_tmp / "candidates", target(tmp_path, "victim"))
    taken = age(write(atc_tmp / "systmp/old.txt"), 8)
    scratch.register(atc_tmp, atc_tmp / "s", atc_tmp / "s/tree", NOW - timedelta(days=8))
    code, lines = prune()
    assert code == 1 and lines[0] == f"DELETED {taken}" and lines[1].startswith("REFUSED ") and lines[2].startswith("OLD ")


def test_prune_refuses_a_parent_that_is_a_file(atc_tmp: Path, parent_appears_after_locate) -> None:
    write(atc_tmp / "candidates")
    assert prune() == (1, [f"REFUSED {atc_tmp / 'candidates'} (not a directory)"])


def test_prune_passes_over_a_missing_parent(atc_tmp: Path) -> None:
    assert prune() == (0, [])


def test_a_link_is_refused_before_the_delete_is_reached(tmp_path: Path, atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def reached(*_args):
        raise AssertionError("the recursive delete was reached")
    monkeypatch.setattr(scratch, "remove_tree", reached)
    victim = target(tmp_path, "victim")
    link_directory(atc_tmp / "link", victim)
    link_directory(atc_tmp / "systmp/old", victim)
    assert clean(atc_tmp / "link") == (1, [f"REFUSED {atc_tmp / 'link'} (is itself a link)"])
    assert prune() == (1, [f"REFUSED {atc_tmp / 'systmp/old'} (is itself a link)"])


class UnreadableEntry:
    name, path = "broken", "the-entry"

    def stat(self, *, follow_symlinks: bool):
        raise PermissionError("cannot read")


def test_an_error_on_an_entry_is_its_refusal_and_the_rest_continue(atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    taken = age(write(atc_tmp / "systmp/old.txt"), 8)
    real = os.scandir

    def with_a_broken_entry(path):
        return [UnreadableEntry(), *real(path)] if Path(path) == atc_tmp / "systmp" else real(path)
    with monkeypatch.context() as patch:
        patch.setattr(scratch.os, "scandir", with_a_broken_entry)
        code, lines = prune()
    assert code == 1 and lines == ["REFUSED the-entry (error: cannot read)", f"DELETED {taken}"]


def test_an_error_in_a_candidates_handler_is_its_refusal(atc_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree = age(write(atc_tmp / "candidates/tree/.git", "gitdir: x\n").parent, 8)

    def no_git(_tree):
        raise FileNotFoundError("git is missing")
    monkeypatch.setattr(scratch, "_owning_repository", no_git)
    code, lines = prune()
    assert code == 1 and lines == [f"REFUSED {tree} (error: git is missing)"]


@pytest.mark.parametrize("failing", ["lstat", "scandir"])
def test_an_error_reading_a_parent_is_its_refusal(atc_tmp: Path, monkeypatch: pytest.MonkeyPatch, failing: str,
                                                  parent_appears_after_locate) -> None:
    (atc_tmp / "candidates").mkdir()
    real = getattr(os, failing)

    def refuse(path, *args, **kwargs):
        if Path(path) == atc_tmp / "candidates":
            raise PermissionError("cannot read")
        return real(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(scratch.os, failing, refuse)
        code, lines = prune()
    assert code == 1 and lines == [f"REFUSED {atc_tmp / 'candidates'} (error: cannot read)"]

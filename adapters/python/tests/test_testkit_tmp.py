"""docs/ATC_HOME.md § Scratch registry and § Deleting: ``atc tmp list``, ``clean`` and ``prune``."""
from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import atc_testkit
from atc_testkit import commands, isolation, locations, owner, scratch


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


def clean(*dirs: Path | str) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = scratch.clean([str(item) for item in dirs], say=lines.append)
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
    assert code == 1
    assert len(lines) == 1 and lines[0].startswith("REFUSED ")
    assert (inside / "qdr/data.txt").is_file() and (outside / "tree/src/pkg/__init__.py").is_file()
    assert (atc_tmp / "systmp").is_dir()
    unlink_directory(atc_tmp / "link")


def test_a_directory_under_a_link_back_into_the_root_is_refused(atc_tmp: Path) -> None:
    victim = scratch_dir(atc_tmp, "victim")
    link_directory(atc_tmp / "loop", atc_tmp)
    code, lines = clean(atc_tmp / "loop" / "victim")
    assert code == 1 and lines[0].startswith("REFUSED ") and victim.is_dir()
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
    assert code == 1 and len(lines) == 1 and lines[0].startswith("REFUSED ") and "reserved name" in lines[0]
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
    assert code == 1 and len(lines) == 1 and lines[0].startswith("REFUSED ")
    assert held.is_file()


@windows
@pytest.mark.parametrize("typed", ["systmp.", "systmp ", "systmp...", "CANDIDATES."])
def test_a_name_windows_trims_to_a_reserved_one_is_refused(atc_tmp: Path, typed: str) -> None:
    (atc_tmp / "candidates").mkdir()
    held = write(atc_tmp / "systmp/keep.txt")
    code, lines = clean(typed)
    assert code == 1 and lines[0].startswith("REFUSED ") and held.is_file()


@windows
@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\"])
def test_a_device_form_of_a_reserved_name_is_refused(atc_tmp: Path, prefix: str) -> None:
    held = write(atc_tmp / "systmp/keep.txt")
    code, lines = clean(f"{prefix}{atc_tmp}{os.sep}systmp")
    assert code == 1 and lines[0].startswith("REFUSED ") and held.is_file()


def test_a_name_outside_the_scratch_form_is_refused(atc_tmp: Path) -> None:
    held = write(atc_tmp / "a name/keep.txt")
    code, lines = clean(atc_tmp / "a name")
    assert code == 1 and "not a scratch name" in lines[0] and held.is_file()


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


@pytest.mark.integration
def test_list_prints_one_line_per_scratch_and_deletes_nothing(atc_tmp: Path, tmp_path: Path) -> None:
    created = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    present = atc_tmp / "present"
    write(present / "file.txt", "12345")
    scratch.register(atc_tmp, present, present / "tree", created, task="ATC-0008")
    scratch.register(atc_tmp, atc_tmp / "absent", atc_tmp / "absent" / "tree", created)
    scratch.register(atc_tmp, atc_tmp / "outside", tmp_path / "elsewhere", created)
    write(atc_tmp / "outside" / "f")
    lines: list[str] = []
    assert scratch.list_scratches(say=lines.append, now=created + timedelta(days=3, hours=2)) == 0
    assert lines == [
        "present  tree  3d  ATC-0008",
        "absent  tree  3d  missing",
        f"outside  {tmp_path / 'elsewhere'}  3d",
        "1 missing; atc tmp clean absent drops their rows",
    ]
    assert present.is_dir()


@pytest.mark.integration
def test_list_without_a_missing_scratch_has_no_count_line(atc_tmp: Path) -> None:
    write(atc_tmp / "here" / "f")
    scratch.register(atc_tmp, atc_tmp / "here", atc_tmp / "here")
    lines: list[str] = []
    scratch.list_scratches(say=lines.append)
    assert len(lines) == 1 and lines[0].startswith("here  .  0m")


@pytest.mark.integration
def test_list_json_carries_every_row_field_and_the_size(atc_tmp: Path) -> None:
    import json
    write(atc_tmp / "present" / "file.txt", "12345")
    scratch.register(atc_tmp, atc_tmp / "present", atc_tmp / "present", task="ATC-1", source="src", commit="c" * 40)
    scratch.register(atc_tmp, atc_tmp / "absent", atc_tmp / "absent")
    lines: list[str] = []
    assert scratch.list_scratches(say=lines.append, as_json=True) == 0
    rows = json.loads("\n".join(lines))
    assert [set(row) for row in rows] == [set(scratch.Row._fields) | {"size"}] * 2
    assert (rows[0]["task"], rows[0]["source"], rows[0]["commit"], rows[0]["size"]) == ("ATC-1", "src", "c" * 40, 5)
    assert rows[1]["size"] is None


@pytest.mark.integration
def test_clean_takes_the_name_tmp_list_prints(atc_tmp: Path) -> None:
    directory = write(atc_tmp / "named" / "f").parent
    scratch.register(atc_tmp, directory, directory)
    lines: list[str] = []
    scratch.list_scratches(say=lines.append)
    name = lines[0].split("  ")[0]
    code, out = clean(name)
    assert code == 0 and not directory.exists() and out == [f"DELETED {directory}"]


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


@pytest.mark.integration
def test_a_dirty_or_locked_candidate_is_removed(repository: Path, atc_tmp: Path) -> None:
    tree = atc_tmp / "candidates/dirty"
    git(repository, "worktree", "add", "-q", "--detach", str(tree))
    git(repository, "worktree", "lock", str(tree))
    write(tree / "a.txt", "changed\n")
    write(tree / "untracked.txt")
    age(tree, 8)
    code, lines = prune()
    assert (code, lines) == (0, [f"DELETED {tree}"])
    assert not tree.exists() and tree.as_posix() not in git(repository, "worktree", "list", "--porcelain")


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


@pytest.mark.unit
def test_dependent_runs_are_listed_and_taken_with_runs(atc_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review, run, other = scratch_dir(atc_tmp, "review"), scratch_dir(atc_tmp, "run"), scratch_dir(atc_tmp, "other")
    scratch.register(atc_tmp, review, review / "tree")
    scratch.register(atc_tmp, run, review / "tree")
    scratch.register(atc_tmp, other, other / "tree")
    assert clean(review) == (0, [f"DEPENDENT {run} (its tree is inside {review}; --with-runs deletes it)",
                                 f"DELETED {review}"])
    assert run.is_dir() and registered_names(atc_tmp) == ["run", "other"]
    review = scratch_dir(atc_tmp, "review")
    scratch.register(atc_tmp, review, review / "tree")
    assert commands.main(["tmp", "clean", "--with-runs", "review"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"DELETED {run}", f"DELETED {review}"]
    assert other.is_dir() and registered_names(atc_tmp) == ["other"]
    assert commands.main(["tmp", "clean", "--with-runs"]) == 2


# candidates and their owners


def candidate(repository: Path, atc_tmp: Path, name: str, created: str | None) -> Path:
    """A candidate worktree; with ``created`` an owner marker of this process's PID, the real creation time for a
    live owner, any other for a gone one."""
    tree = atc_tmp / "candidates" / name
    git(repository, "worktree", "add", "-q", "--detach", str(tree))
    if created is not None:
        owner.write_marker(tree, "verify", repository, "ATC-0001")
        data = json.loads(owner.marker(tree).read_text(encoding="utf-8"))
        owner.marker(tree).write_text(json.dumps({**data, "created": created}), encoding="utf-8")
    return tree


@pytest.mark.integration
def test_an_owner_is_its_pid_and_creation_time(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = owner.created(os.getpid())
    assert mine not in (None, owner.DENIED) and owner.alive(os.getpid(), mine)
    assert not owner.alive(os.getpid(), "0") and not owner.alive(0, mine)
    finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    assert owner.created(int(finished.stdout)) is None or owner.created(int(finished.stdout)) != mine
    monkeypatch.setattr(owner, "created", lambda _pid: owner.DENIED)
    assert owner.alive(1234, "anything")


@pytest.mark.integration
def test_prune_removes_candidates_whose_owner_is_gone_at_any_age(repository: Path, atc_tmp: Path) -> None:
    gone = candidate(repository, atc_tmp, "gone", "0")
    live = candidate(repository, atc_tmp, "live", owner.created(os.getpid()))
    young = candidate(repository, atc_tmp, "young", None)
    old = candidate(repository, atc_tmp, "old", None)
    stamp = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(old, (stamp, stamp))
    week = (datetime.now(UTC) - timedelta(days=8)).timestamp()
    os.utime(live, (week, week))
    lines: list[str] = []
    assert scratch.prune(say=lines.append) == 0
    assert sorted(lines) == sorted([f"DELETED {gone}", f"DELETED {old}"])
    assert not owner.marker(gone).exists() and live.is_dir() and owner.marker(live).exists() and young.is_dir()
    listed = git(repository, "worktree", "list", "--porcelain")
    assert "gone" not in listed and "/old" not in listed and "live" in listed


@pytest.mark.integration
def test_prune_drops_a_marker_left_without_its_candidate_and_only_that_record(repository: Path, atc_tmp: Path) -> None:
    lost = candidate(repository, atc_tmp, "lost", "0")
    other = candidate(repository, atc_tmp, "other", owner.created(os.getpid()))
    shutil.rmtree(lost)
    shutil.rmtree(other)
    lines: list[str] = []
    assert scratch.prune(say=lines.append) == 0
    assert lines == [f"DELETED {lost} (worktree record)", f"DELETED {owner.marker(lost)}"]
    listed = git(repository, "worktree", "list", "--porcelain")
    assert "lost" not in listed and "other" in listed and owner.marker(other).exists()


@pytest.mark.integration
def test_list_names_leftover_candidates_and_the_missing_scratches_to_clean(repository: Path, atc_tmp: Path) -> None:
    gone = candidate(repository, atc_tmp, "gone", "0")
    candidate(repository, atc_tmp, "live", owner.created(os.getpid()))
    write(atc_tmp / "candidates" / "x.verify.log")
    for name in ("a1", "b2"):
        scratch.register(atc_tmp, atc_tmp / name, atc_tmp / name / "tree")
    lines: list[str] = []
    scratch.list_scratches(say=lines.append)
    assert lines[2] == "2 missing; atc tmp clean a1 b2 drops their rows"
    assert lines[3].split("  ")[0::2] == [f"candidates/{gone.name}", "leftover", str(repository)]
    assert lines[3].split("  ")[3] == f"verify ATC-0001, pid {os.getpid()}"
    assert lines[4:] == ["1 leftover candidates; atc tmp prune removes them"]


@pytest.mark.integration
def test_record_only_cleanup_compares_paths_without_case(repository: Path, atc_tmp: Path) -> None:
    tree = candidate(repository, atc_tmp, "Casey", None)
    shutil.rmtree(tree)
    spelled = Path(str(tree).upper()) if os.name == "nt" else tree
    assert scratch.forget_worktree(repository / ".git", spelled)
    assert "Casey" not in git(repository, "worktree", "list", "--porcelain")


@pytest.mark.integration
def test_an_owner_that_cannot_be_read_keeps_its_candidate_for_seven_days(repository: Path, atc_tmp: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    old = candidate(repository, atc_tmp, "old", "1")
    young = candidate(repository, atc_tmp, "young", "1")
    week = (datetime.now(UTC) - timedelta(days=8)).timestamp()
    os.utime(old, (week, week))
    monkeypatch.setattr(owner, "created", lambda _pid: owner.DENIED)
    assert owner.state(old)[0] == owner.state(young)[0] == "in use"
    lines: list[str] = []
    assert scratch.prune(say=lines.append) == 0
    assert lines == [f"DELETED {old}"]
    assert not old.exists() and not owner.marker(old).exists() and young.is_dir() and owner.marker(young).exists()

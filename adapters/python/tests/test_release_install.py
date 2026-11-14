"""`atc install` (docs/WORKFLOW.md § Installing a release): the release copy, the uv install and their refusals."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import atc_cli
import doctor
import release_install as ri

pytestmark = pytest.mark.integration


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit_version(repo: Path, version: str) -> str:
    pyproject = repo / "adapters" / "python" / "pyproject.toml"
    pyproject.parent.mkdir(parents=True, exist_ok=True)
    pyproject.write_text(f'[project]\nname = "agent-traffic-control"\nversion = "{version}"\n', encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"release {version}")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """A canonical checkout on main whose tag v0.6.0 carries atc 0.6.0, with one later commit on main."""
    checkout = tmp_path / "workspace"
    checkout.mkdir()
    git(checkout, "init", "-q", "-b", "main")
    git(checkout, "config", "user.email", "t@example.invalid")
    git(checkout, "config", "user.name", "t")
    (checkout / ".git" / "info" / "exclude").write_text("workspace.yaml\n", encoding="utf-8")
    commit_version(checkout, "0.6.0")
    git(checkout, "tag", "v0.6.0")
    commit_version(checkout, "0.7.0.dev0")
    (checkout / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    return checkout


@pytest.fixture()
def releases(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".atc" / "releases"


class Uv:
    """A stand-in for uv: records each argv and answers with ``code``."""

    def __init__(self, code: int = 0, stderr: str = "", bin_dir: Path | None = None) -> None:
        self.calls: list[list[str]] = []
        self.code, self.stderr, self.bin_dir = code, stderr, bin_dir

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[1:4] == ["tool", "dir", "--bin"]:
            return subprocess.CompletedProcess(argv, 0, f"{self.bin_dir}\n", "")
        return subprocess.CompletedProcess(argv, self.code, "", self.stderr)


@pytest.fixture()
def uv(monkeypatch: pytest.MonkeyPatch) -> Uv:
    monkeypatch.setattr(ri.shutil, "which", lambda name: "uv" if name == "uv" else None)
    monkeypatch.setattr(ri, "WINDOWS", False)
    return Uv()


def install(root: Path, releases: Path, uv: Uv, *args: str, now: tuple[str, Path] | None = None,
            home: Path | None = None) -> int:
    return ri.main([*args], root=root, releases=releases, now=now or ("0.5.0", root),
                   home=home or releases.parent.parent, run=uv)


def test_install_creates_a_locked_detached_copy_at_the_tag_and_installs_it(root, releases, uv, capsys):
    assert install(root, releases, uv, "v0.6.0") == 0
    copy = releases / "v0.6.0"
    tag = git(root, "rev-parse", "v0.6.0^{commit}")
    listing = git(root, "worktree", "list", "--porcelain")
    block = next(b for b in listing.split("\n\n") if Path(b.splitlines()[0].removeprefix("worktree ")) == copy)
    assert f"HEAD {tag}" in block and "detached" in block and "locked atc release v0.6.0" in block
    assert uv.calls == [["uv", "tool", "install", "-e", str(copy / "adapters" / "python"), "--force"]]
    assert capsys.readouterr().out == f"INSTALLED atc 0.6.0 (v0.6.0) from {copy}; was 0.5.0 from {root}\n"


def test_install_names_the_guard_step_when_a_hook_is_installed(root, releases, uv, capsys):
    table = releases.parent / "enforcement" / "pretooluse.json"
    table.parent.mkdir(parents=True)
    table.write_text("{}", encoding="utf-8")
    assert install(root, releases, uv, "v0.6.0") == 0
    assert capsys.readouterr().out.endswith("; the hook runs it after atc guard --install\n")


def test_check_changes_nothing_and_reports_both_versions(root, releases, uv, capsys):
    assert install(root, releases, uv, "v0.6.0", "--check") == 0
    assert not (releases / "v0.6.0").exists() and uv.calls == []
    short = git(root, "rev-parse", "v0.6.0")[:12]
    assert capsys.readouterr().out == (f"CHECK atc 0.6.0 (v0.6.0, {short}): would create {releases / 'v0.6.0'} and "
                                       f"install; now 0.5.0 from {root}\n")


def test_json_reports_the_old_and_new_install(root, releases, uv, capsys):
    assert install(root, releases, uv, "v0.6.0", "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verdict"] == "INSTALLED" and result["created"] is True
    assert result["old"] == {"version": "0.5.0", "from": str(root)}
    assert result["new"] == {"version": "0.6.0", "from": str(releases / "v0.6.0")}


def test_an_existing_copy_is_reused_and_the_running_copy_is_current(root, releases, uv, capsys):
    assert install(root, releases, uv, "v0.6.0") == 0
    capsys.readouterr()
    assert install(root, releases, uv, "v0.6.0", "--check") == 0
    assert f"would install from {releases / 'v0.6.0'};" in capsys.readouterr().out
    assert install(root, releases, uv, "v0.6.0") == 0 and len(uv.calls) == 2
    capsys.readouterr()
    assert install(root, releases, uv, "v0.6.0", now=("0.6.0", releases / "v0.6.0")) == 0
    assert len(uv.calls) == 2
    assert capsys.readouterr().out == f"CURRENT atc 0.6.0 (v0.6.0) from {releases / 'v0.6.0'}\n"


@pytest.mark.parametrize("tag", ["0.6.0", "v0.6", "v0.6.0-rc1", "main"])
def test_a_name_that_is_no_release_tag_is_an_error(root, releases, uv, capsys, tag):
    assert install(root, releases, uv, tag) == 2
    assert capsys.readouterr().err.startswith(f"ERROR TAG_INVALID {tag} is not a release tag")
    assert uv.calls == []


def test_an_unknown_tag_stops(root, releases, uv, capsys):
    assert install(root, releases, uv, "v9.9.9") == 1
    err = capsys.readouterr().err
    assert err.startswith(f"STOP TAG_UNKNOWN: {root} has no tag v9.9.9") and "fetch --tags" in err
    assert not releases.exists()


def test_a_tag_off_main_stops(root, releases, uv, capsys):
    git(root, "switch", "-q", "-c", "side")
    commit_version(root, "0.9.0")
    git(root, "tag", "v0.9.0")
    git(root, "switch", "-q", "main")
    assert install(root, releases, uv, "v0.9.0") == 1
    assert capsys.readouterr().err.startswith("STOP TAG_NOT_ON_MAIN: v0.9.0 ")
    assert not releases.exists() and uv.calls == []


def test_a_root_that_is_no_main_worktree_is_an_error(root, releases, uv, tmp_path, capsys):
    linked = tmp_path / "linked"
    git(root, "worktree", "add", "-q", "--detach", str(linked))
    assert install(linked, releases, uv, "v0.6.0") == 2
    assert capsys.readouterr().err.startswith(f"ERROR ROOT_NOT_MAIN_WORKTREE {linked} ")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert install(plain, releases, uv, "v0.6.0") == 2
    assert not releases.exists() and uv.calls == []


def test_a_clone_without_a_workspace_map_installs(root, releases, uv, capsys):
    (root / "workspace.yaml").unlink()
    assert install(root, releases, uv, "v0.6.0") == 0
    assert capsys.readouterr().out.startswith("INSTALLED atc 0.6.0 (v0.6.0) ")


def plain_directory(root: Path, copy: Path) -> str:
    copy.mkdir(parents=True)
    return "it is not a worktree of"


def other_commit(root: Path, copy: Path) -> str:
    git(root, "worktree", "add", "-q", "--detach", str(copy), "main")
    return "it is at"


def on_a_branch(root: Path, copy: Path) -> str:
    git(root, "worktree", "add", "-q", "-b", "topic", str(copy), "v0.6.0")
    return "it has the branch topic checked out"


def modified(root: Path, copy: Path) -> str:
    git(root, "worktree", "add", "-q", "--detach", str(copy), "v0.6.0")
    (copy / "adapters" / "python" / "pyproject.toml").write_text("edited\n", encoding="utf-8")
    return "it has tracked modifications"


@pytest.mark.parametrize("make", [plain_directory, other_commit, on_a_branch, modified],
                         ids=lambda make: make.__name__)
def test_a_copy_that_does_not_match_the_tag_stops(root, releases, uv, capsys, make):
    copy = releases / "v0.6.0"
    copy.parent.mkdir(parents=True)
    problem = make(root, copy)
    assert install(root, releases, uv, "v0.6.0") == 1
    err = capsys.readouterr().err
    assert err.startswith(f"STOP RELEASE_COPY_INVALID: {copy}: {problem}")
    assert "worktree remove --force --force" in err and uv.calls == []


def test_a_failing_uv_is_an_error_naming_the_manual_command(root, releases, uv, capsys):
    uv.code, uv.stderr = 1, "first\nerror: failed to remove atc.exe"
    assert install(root, releases, uv, "v0.6.0") == 2
    err = capsys.readouterr().err
    adapter = releases / "v0.6.0" / "adapters" / "python"
    assert err.startswith("ERROR INSTALL_FAILED error: failed to remove atc.exe\n")
    assert f'hint: run uv tool install -e "{adapter}" --force from a terminal where no atc runs' in err


def test_a_missing_uv_is_an_error(root, releases, uv, capsys, monkeypatch):
    monkeypatch.setattr(ri.shutil, "which", lambda name: None)
    assert install(root, releases, uv, "v0.6.0") == 2
    assert capsys.readouterr().err.startswith("ERROR UV_MISSING ")


def test_on_windows_the_running_launchers_are_set_aside_before_uv(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "atc.exe").write_bytes(b"launcher")
    (bin_dir / "atc.exe.1.old").write_bytes(b"stale")
    (bin_dir / "other.exe.1.old").write_bytes(b"not ours")
    monkeypatch.setattr(ri.shutil, "which", lambda name: "uv")
    seen: list[list[str]] = []
    uv = Uv(bin_dir=bin_dir)

    def run(argv, **kwargs):
        if argv[1:3] == ["tool", "install"]:
            seen.append(sorted(p.name for p in bin_dir.iterdir()))
        return uv(argv, **kwargs)

    assert ri.install_tool(tmp_path / "copy", run, windows=True) is None
    assert uv.calls[0] == ["uv", "tool", "dir", "--bin"]
    pid = ri.os.getpid()
    assert seen == [sorted(["atc.exe", f"atc.exe.{pid}.old", "other.exe.1.old"])]
    assert (bin_dir / "atc.exe").read_bytes() == b"launcher"
    assert sorted(p.name for p in bin_dir.iterdir()) == ["atc.exe", "other.exe.1.old"]


def test_a_launcher_that_cannot_be_copied_back_is_restored_before_the_error(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "atc.exe").write_bytes(b"launcher")
    monkeypatch.setattr(ri.shutil, "which", lambda name: "uv")

    def copy_fails(source, target):
        raise PermissionError("denied")

    monkeypatch.setattr(ri.shutil, "copy2", copy_fails)
    uv = Uv(bin_dir=bin_dir)
    refusal = ri.install_tool(tmp_path / "copy", uv, windows=True)
    assert (refusal.token, refusal.code) == ("INSTALL_FAILED", ri.cli_conventions.ERROR)
    assert refusal.detail == "cannot set the running launcher aside (denied)"
    assert sorted(p.name for p in bin_dir.iterdir()) == ["atc.exe"]
    assert (bin_dir / "atc.exe").read_bytes() == b"launcher"
    assert [call[1:3] for call in uv.calls] == [["tool", "dir"]]


def test_elsewhere_no_launcher_is_touched(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.shutil, "which", lambda name: "uv")
    uv = Uv()
    assert ri.install_tool(tmp_path / "copy", uv, windows=False) is None
    assert [call[1:3] for call in uv.calls] == [["tool", "install"]]


def test_install_is_an_atc_command():
    assert atc_cli.COMMANDS["install"] == "release_install"
    assert "  install " in atc_cli.usage()


def test_doctor_names_the_copy_the_installed_atc_runs(root, tmp_path, monkeypatch):
    """A release copy's line is in test_release_pin.py, with the copy's integrity."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    check = doctor.install_check(root, "0.6.0")
    assert check.status == "WARN" and "runs the canonical checkout" in check.detail
    assert doctor.install_check(tmp_path, "0.6.0").detail.endswith(", a development copy")

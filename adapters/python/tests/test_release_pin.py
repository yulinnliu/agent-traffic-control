"""The release-pin guard (src/atc_cli/scripts/release_pin.py, docs/WORKFLOW.md § Installing a release): an installed release
acts on a project only while its `.atc/atc.lock` pins that release, and doctor reports the pins and the install."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import atc_cli
import doctor
import release_pin

pytestmark = pytest.mark.integration

NOT_CANONICAL = Path(__file__).resolve().parent


def git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env,
                          check=True).stdout.strip()


def put(repo: Path, files: dict[str, str], message: str = "edit") -> str:
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        git(repo, "add", "--", relative)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def lock(revision: str) -> str:
    return f'version = 1\nsource = "example/governance"\nrevision = "{revision}"\n'


SOURCE = "file:///C:/hosts/governance"


def record(commit: str, revision: str = "v0.6.0", url: str = SOURCE) -> dict:
    """`direct_url.json` as `uv tool install` writes it for a Git tag (PEP 610)."""
    return {"url": url, "vcs_info": {"vcs": "git", "commit_id": commit, "requested_revision": revision},
            "subdirectory": "adapters/python"}


class Ws:
    def __init__(self, root: Path, project: Path, release: release_pin.Release, old: str, new: str) -> None:
        self.root, self.project, self.release, self.old, self.new = root, project, release, old, new

    def pin(self, revision: str | None) -> None:
        path = self.project / ".atc" / "atc.lock"
        if revision is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(lock(revision), encoding="utf-8", newline="\n")

    def check(self, command: str, *rest: str, release: object = "installed"):
        release = self.release if release == "installed" else release
        return release_pin.check(command, list(rest), self.root, release, str(self.root), dict(os.environ))


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Ws:
    """A root with releases v0.5.0 and v0.6.0, v0.6.0 installed by `uv tool install` from its tag, and project demo
    (prefix DEMO) pinning v0.5.0."""
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "workspace"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    old = put(root, {"AGENTS.md": "# AGENTS.md\n\n## Task records\n\n- Task prefix: ATC\n",
                     ".gitignore": "workspace.yaml\nprojects/\nworktrees/\nstate/\n"})
    git(root, "tag", "v0.5.0")
    new = put(root, {"README.md": "0.6.0\n"})
    git(root, "tag", "v0.6.0")
    (root / "shared" / "governance").mkdir(parents=True)
    (root / "workspace.yaml").write_text(
        f"version: 1\nworkspace_root: '{root}'\ngovernance_source: example/governance\nprojects:\n"
        "  demo:\n    repo: projects/demo\n    worktrees: worktrees/demo\n    state: state/demo-data\n",
        encoding="utf-8")
    project = root / "projects" / "demo"
    project.mkdir(parents=True)
    git(project, "init", "-q", "-b", "main")
    put(project, {"AGENTS.md": "# AGENTS.md\n\n## Task records\n\n- Task prefix: DEMO\n", ".atc/atc.lock": lock(old)})
    return Ws(root, project, release_pin.installed(record(new)), old, new)


GUARDED = [
    ("task", "create", "the widget", "--project", "demo"),
    ("task", "create", "the widget", "--project=demo"),
    ("task", "update", "DEMO-0001", "--decision", "x"),
    ("task", "update", "DEMO-0001", "--decision", "ATC-0001"),
    ("task", "create", "ATC-0001", "--project", "demo"),
    ("task", "update", "--title", "ATC-0001", "DEMO-0001"),
    ("task", "resume", "DEMO-0001"),
    ("task", "close", "DEMO-0001", "--check"),
    ("test", "DEMO-0001"),
    ("test", "DEMO-0001", "--mutants", "m.json"),
    ("test", "DEMO-0001", "--candidate"),
    ("integrate", "DEMO-0001"),
    ("integrate", "DEMO-0001", "--check"),
    ("push", "demo"),
    ("push", "--check"),
    ("push", "--all"),
]


@pytest.mark.parametrize("call", GUARDED, ids=" ".join)
def test_a_task_flow_command_on_a_project_pinned_to_another_release_stops(ws: Ws, call: tuple[str, ...]) -> None:
    refusal = ws.check(*call)
    assert refusal is not None and refusal.token == "RELEASE_MISMATCH" and refusal.code == 1
    assert refusal.detail == (f"demo pins v0.5.0 ({ws.old[:12]}) in .atc/atc.lock; the installed atc is v0.6.0 "
                              f"({ws.new[:12]})")
    assert refusal.hint == "atc sync demo moves it to the installed release"


EXEMPT = [
    ("doctor",), ("doctor", "-v"), ("sync",), ("sync", "demo"), ("guard",),
    ("guard", "--install"), ("init",), ("add", "demo"), ("remove", "demo"),
    ("task", "list"), ("task", "list", "DEMO-0001"),
    ("test", "DEMO-0001", "--clean"), ("test", "--list", "DEMO-0001"), ("test", "--private", "--", "x"),
    ("task", "update", "DEMO-0001", "--help"), ("integrate", "-h"), ("push", "demo", "--help"),
]


@pytest.mark.parametrize("call", EXEMPT, ids=" ".join)
def test_reading_fixing_and_map_commands_stay_usable_under_a_mismatch(ws: Ws, call: tuple[str, ...]) -> None:
    assert ws.check(*call) is None


@pytest.mark.parametrize("call", [("push", "root"), ("integrate", "ATC-0001"), ("task", "create", "x", "--project",
                                                                                    "root"),
                                  ("task", "update", "ATC-0001", "--decision", "DEMO-0001"),
                                  ("task", "update", "ATC-0001", "--title", "DEMO-0001"),
                                  ("task", "update", "--title", "DEMO-0001", "ATC-0001"),
                                  ("test", "ATC-0001", "--", "-k", "DEMO-0001")], ids=" ".join)
def test_the_workspace_root_is_never_compared(ws: Ws, call: tuple[str, ...]) -> None:
    assert ws.check(*call) is None


def test_an_install_without_a_recorded_commit_is_not_guarded(ws: Ws, tmp_path: Path) -> None:
    """`uv run atc` in a topic worktree (editable), a local or wheel install, or no record at all: no installed
    release to compare."""
    editable = {"url": (tmp_path / "worktree" / "adapters" / "python").as_uri(), "dir_info": {"editable": True}}
    for found in (editable, {"url": "file:///C:/wheel.whl", "archive_info": {}}, None, {"vcs_info": {}}):
        assert release_pin.installed(found) is None
        assert ws.check("integrate", "DEMO-0001", release=release_pin.installed(found)) is None
    assert release_pin.editable(editable) == tmp_path / "worktree" / "adapters" / "python"
    assert release_pin.editable(record(ws.new)) is None


def test_the_installed_release_is_the_recorded_commit_and_tag(ws: Ws) -> None:
    release = release_pin.installed(record(ws.new))
    assert (release.commit, release.tag, release.url, release.name) == (ws.new, "v0.6.0", SOURCE, "v0.6.0")
    by_sha = release_pin.installed(record(ws.new, revision=ws.new[:12]))
    assert (by_sha.tag, by_sha.name) == (None, ws.new[:12])
    assert release_pin.reinstall_command(record(ws.new)) == (
        f'uv tool install --force "git+{SOURCE}@v0.6.0#subdirectory=adapters/python"')


def test_the_running_atc_reads_its_own_direct_url(monkeypatch: pytest.MonkeyPatch, ws: Ws) -> None:
    from importlib import metadata

    class Distribution:
        def read_text(self, name: str) -> str | None:
            return __import__("json").dumps(record(ws.new)) if name == "direct_url.json" else None
    monkeypatch.setattr(metadata, "distribution", lambda name: Distribution())
    assert release_pin.installed() == ws.release
    monkeypatch.setattr(metadata, "distribution", lambda name: (_ for _ in ()).throw(metadata.PackageNotFoundError(name)))
    assert release_pin.installed() is None and release_pin.direct_url() is None


def test_the_installed_pin_passes_and_a_missing_lock_stops(ws: Ws) -> None:
    ws.pin(ws.new)
    assert ws.check("integrate", "DEMO-0001") is None
    ws.pin(ws.new.upper())
    assert ws.check("integrate", "DEMO-0001") is None
    ws.pin(None)
    refusal = ws.check("integrate", "DEMO-0001")
    assert refusal.detail.startswith("demo pins no revision (.atc/atc.lock)") and refusal.hint == "atc sync demo"


def test_a_project_ahead_of_the_installed_release_names_the_install(ws: Ws) -> None:
    """Sync never moves a project back without --revision, so a pin ahead of the installed release is the Owner's
    install, of the pinned tag or of the release tagged at the pinned commit."""
    other = put(ws.root, {"README.md": "next\n"})
    ws.pin(other)
    refusal = ws.check("integrate", "DEMO-0001")
    assert refusal.detail.startswith(f"demo pins no release ({other[:12]})")
    assert refusal.hint == (f"the project is ahead of the installed release; the Owner runs uv tool install of "
                            f"the release tag at {other[:12]}")
    git(ws.root, "tag", "v0.7.0")
    assert ws.check("integrate", "DEMO-0001").hint == (
        "the project is ahead of the installed release; the Owner runs "
        f'uv tool install --force "git+{SOURCE}@v0.7.0#subdirectory=adapters/python"')


def test_a_host_reads_the_release_history_of_its_governance_checkout(ws: Ws, tmp_path: Path) -> None:
    """A workspace directory that is no Git checkout reads the tags of the checkout `governance_checkout` names."""
    host = tmp_path / "host"
    host.mkdir()
    (host / "workspace.yaml").write_text(f"version: 1\ngovernance_checkout: '{ws.root.as_posix()}'\n",
                                         encoding="utf-8")
    assert release_pin.history(host) == ws.root.resolve()
    demo = SimpleNamespace(name="demo", repo=str(ws.project))
    assert release_pin.mismatch(demo, ws.release, release_pin.history(host)).detail.startswith("demo pins v0.5.0")
    (host / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    assert release_pin.history(host) == host


def test_the_dispatcher_stops_before_the_command_runs(ws: Ws, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    import git_integrate
    ran: list = []
    monkeypatch.setattr(git_integrate, "main", lambda rest, root: ran.append(rest) or 0)
    monkeypatch.setattr(release_pin, "direct_url", lambda: record(ws.new))
    assert atc_cli.main(["--root", str(ws.root), "integrate", "DEMO-0001"], checkout=NOT_CANONICAL) == 1
    err = capsys.readouterr().err
    assert err.startswith("STOP RELEASE_MISMATCH: demo pins v0.5.0") and "hint: atc sync demo moves it to the installed release" in err
    assert ran == []
    ws.pin(ws.new)
    assert atc_cli.main(["--root", str(ws.root), "integrate", "DEMO-0001"], checkout=NOT_CANONICAL) == 0
    assert ran == [["DEMO-0001"]]


def test_doctor_reports_each_pin_against_the_installed_release(ws: Ws) -> None:
    installed = ws.release
    demo = SimpleNamespace(name="demo", repo=str(ws.project))
    check = doctor.pin_check(demo, installed, ws.root)
    assert check == doctor.Check("projects.demo.pin", "ERROR", (
        f"demo pins v0.5.0 ({ws.old[:12]}) in .atc/atc.lock; the installed atc is v0.6.0 ({ws.new[:12]}); task "
        "commands refuse; fix: atc sync demo moves it to the installed release"))
    ws.pin(ws.new)
    assert doctor.pin_check(demo, installed, ws.root) == doctor.Check(
        "projects.demo.pin", "READY", f"pins v0.6.0 ({ws.new[:12]}), the installed release")
    assert doctor.pin_check(demo, None, ws.root) == doctor.Check(
        "projects.demo.pin", "READY", f"pins {ws.new[:12]}; atc runs no installed release")


def test_doctor_reports_the_installed_commit_a_development_install_or_neither(ws: Ws, tmp_path: Path) -> None:
    import atc_cli
    version = atc_cli.package_version()
    assert doctor.skew_check(ws.root, record(ws.new)) == (doctor.Check(
        "installed atc", "READY", f"atc {version} at v0.6.0 ({ws.new[:12]}), main's latest release"), None)
    source = tmp_path / "worktree" / "adapters" / "python"
    assert doctor.skew_check(ws.root, {"url": source.as_uri(), "dir_info": {"editable": True}}) == (doctor.Check(
        "installed atc", "READY", f"atc {version}, a development install from {source}; not compared with main"),
        None)
    check, state = doctor.skew_check(ws.root, {"url": "file:///C:/wheel.whl", "archive_info": {}})
    assert state is None
    assert check.status == "WARN" and "records no installed commit" in check.detail
    assert check.detail.endswith('uv tool install --force "git+<source>@<tag>#subdirectory=adapters/python"')


# Version skew first (docs/WORKFLOW.md § Installing a release; docs/design/ATC-0053.md § 6.1, G62-G74)


def test_g65_an_installed_commit_off_main_is_an_error(ws: Ws) -> None:
    git(ws.root, "checkout", "-q", "-b", "side")
    side = put(ws.root, {"side.txt": "x\n"})
    git(ws.root, "checkout", "-q", "main")
    check, state = doctor.skew_check(ws.root, record(side))
    assert (check.status, state) == ("ERROR", "ATC_NOT_ON_MAIN") and "uv tool install" in check.detail


def test_g62_an_installed_release_older_than_mains_latest_is_one_error(ws: Ws) -> None:
    check, state = doctor.skew_check(ws.root, record(ws.old, "v0.5.0"))
    assert (check.status, state) == ("ERROR", "ATC_BEHIND_RELEASE")
    assert check.detail.endswith(f'uv tool install --force "git+{SOURCE}@v0.6.0#subdirectory=adapters/python"')


def test_g64_g71_an_installed_atc_below_min_atc_comes_first(ws: Ws) -> None:
    put(ws.root, {"atc-manifest.yaml": "version: 1\nmin_atc: 999.0.0\n"})
    check, state = doctor.skew_check(ws.root, record(ws.old, "v0.5.0"))
    assert (check.status, state) == ("ERROR", "ATC_BELOW_MIN") and "999.0.0" in check.detail
    put(ws.root, {"atc-manifest.yaml": "version: 1\nmin_atc: 0.0.1\n"})
    assert doctor.skew_check(ws.root, record(ws.old, "v0.5.0"))[1] == "ATC_BEHIND_RELEASE"


def test_g66_only_commits_changing_what_atc_runs_count_behind_main(ws: Ws) -> None:
    put(ws.root, {".atc/tasks/ATC-0001.yaml": "id: ATC-0001\n"})
    check, state = doctor.skew_check(ws.root, record(ws.new))
    assert (check.status, state) == ("READY", None)
    put(ws.root, {"adapters/python/x.py": "x = 1\n"})
    check, state = doctor.skew_check(ws.root, record(ws.new))
    assert (check.status, state) == ("WARN", "ATC_BEHIND_MAIN") and "main has 1 commits after" in check.detail


def test_g69_a_commit_the_history_lacks_is_unknown(ws: Ws) -> None:
    check, state = doctor.skew_check(ws.root, record("f" * 40))
    assert (check.status, state) == ("WARN", None) and check.detail.startswith("ATC_SKEW_UNKNOWN")


DRIFT = [doctor.Check("workspace root", "READY", "/w"),
         doctor.Check("workspace-managed adapters", "ERROR", "2 drifted"),
         doctor.Check("projects.demo.integration", "ERROR", "1 missing or drifted items"),
         doctor.Check("projects.demo.testkit", "ERROR", "old testkit"),
         doctor.Check("projects.demo.pin", "ERROR", "the project is ahead of the installed release; fix"),
         doctor.Check("projects.other.pin", "ERROR", "demo pins v0.5.0; fix"),
         doctor.Check("pretooluse table", "ERROR", "POLICY_INVALID: bad"),
         doctor.Check("uv", "READY", "uv")]
ROOT_LINE = doctor.Check("installed atc", "ERROR", "ATC_BEHIND_RELEASE: older")


def test_g62_g72_g74_under_a_skew_derived_errors_become_notes_below_the_root_line() -> None:
    checks = doctor.apply_skew(DRIFT, ROOT_LINE, "ATC_BEHIND_RELEASE")
    assert [c.name for c in checks][:6] == ["workspace root", "installed atc", "workspace-managed adapters",
                                            "projects.demo.integration", "projects.demo.testkit", "projects.demo.pin"]
    assert [c.status for c in checks[2:6]] == ["NOTE"] * 4
    assert all(c.detail.startswith("follows from installed atc: ") for c in checks[2:6])
    rest = {c.name: c for c in checks[6:]}
    assert rest["projects.other.pin"].status == "ERROR"
    assert rest["pretooluse table"].status == "ERROR" and rest["pretooluse table"].detail.endswith(
        "the installed atc is older than main; installing the latest release may fix it")


def test_g63_without_a_skew_drift_stays_an_error() -> None:
    checks = doctor.apply_skew(DRIFT, doctor.Check("installed atc", "READY", "latest"), None)
    assert [c.name for c in checks][:2] == ["workspace root", "installed atc"]
    assert "NOTE" not in [c.status for c in checks] and sum(c.status == "ERROR" for c in checks) == 6


@pytest.mark.parametrize("root_line, extra, code", [
    (doctor.Check("installed atc", "WARN", "ATC_BEHIND_MAIN: x"), doctor.Check("x", "NOTE", "follows from installed atc: y"), 1),
    (doctor.Check("installed atc", "WARN", "ATC_BEHIND_MAIN: x"), doctor.Check("x", "READY", "y"), 0),
])
def test_g67_g73_a_note_exits_1_and_is_counted(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                               root_line: doctor.Check, extra: doctor.Check, code: int) -> None:
    checks = [doctor.Check("workspace root", "READY", "/w"), root_line, extra]
    monkeypatch.setattr(doctor, "diagnose", lambda root: checks)
    assert doctor.main([]) == code
    out = capsys.readouterr().out
    assert out.splitlines()[0].endswith(f"{int(code)} note(s)")
    assert doctor.main(["--json"]) == code
    result = json.loads(capsys.readouterr().out)
    assert result["notes"] == code and [c["name"] for c in result["checks"]][:2] == ["workspace root", "installed atc"]
    assert [c.get("follows") for c in result["checks"]] == [None, None, "installed atc" if code else None]

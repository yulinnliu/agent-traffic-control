"""The release-pin guard (scripts/release_pin.py, docs/WORKFLOW.md § Installing a release): an installed release
acts on a project only while its `.atc/atc.lock` pins that release, and doctor reports the pins and the copy."""
from __future__ import annotations

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


class Ws:
    def __init__(self, root: Path, project: Path, copy: Path, old: str, new: str) -> None:
        self.root, self.project, self.copy, self.old, self.new = root, project, copy, old, new

    def pin(self, revision: str | None) -> None:
        path = self.project / ".atc" / "atc.lock"
        if revision is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(lock(revision), encoding="utf-8", newline="\n")

    def check(self, command: str, *rest: str, code: Path | None = None):
        return release_pin.check(command, list(rest), self.root, code or self.copy, str(self.root), dict(os.environ))


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Ws:
    """A root with releases v0.5.0 and v0.6.0, v0.6.0 installed as a release copy, and project demo (prefix DEMO)
    pinning v0.5.0."""
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
    copy = home / ".atc" / "releases" / "v0.6.0"
    copy.parent.mkdir(parents=True)
    git(root, "worktree", "add", "-q", "--detach", str(copy), "v0.6.0")
    return Ws(root, project, copy, old, new)


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
    ("doctor",), ("doctor", "-v"), ("sync",), ("sync", "demo"), ("install", "v0.5.0"), ("guard",),
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


def test_a_development_copy_is_not_guarded(ws: Ws, tmp_path: Path) -> None:
    """`uv run atc` in a topic worktree, or an atc still installed from the canonical checkout, has no installed
    release to compare."""
    assert ws.check("integrate", "DEMO-0001", code=ws.root) is None
    assert ws.check("integrate", "DEMO-0001", code=tmp_path / "home" / ".atc" / "other" / "v0.6.0") is None


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
    assert refusal.hint == (f"the project is ahead of the installed release; the Owner runs atc install of the "
                            f"release tag at {other[:12]}")
    git(ws.root, "tag", "v0.7.0")
    assert ws.check("integrate", "DEMO-0001").hint == ("the project is ahead of the installed release; the Owner "
                                                       "runs atc install v0.7.0")


def test_the_dispatcher_stops_before_the_command_runs(ws: Ws, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    import git_integrate
    ran: list = []
    monkeypatch.setattr(git_integrate, "main", lambda rest, root: ran.append(rest) or 0)
    monkeypatch.setattr(atc_cli, "CHECKOUT", ws.copy)
    assert atc_cli.main(["--root", str(ws.root), "integrate", "DEMO-0001"], checkout=NOT_CANONICAL) == 1
    err = capsys.readouterr().err
    assert err.startswith("STOP RELEASE_MISMATCH: demo pins v0.5.0") and "hint: atc sync demo moves it to the installed release" in err
    assert ran == []
    ws.pin(ws.new)
    assert atc_cli.main(["--root", str(ws.root), "integrate", "DEMO-0001"], checkout=NOT_CANONICAL) == 0
    assert ran == [["DEMO-0001"]]


def test_doctor_reports_each_pin_against_the_installed_release(ws: Ws) -> None:
    installed = release_pin.installed(ws.copy)
    assert (installed.tag, installed.commit) == ("v0.6.0", ws.new)
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


def test_doctor_reports_a_release_copy_that_is_not_clean_at_its_tag(ws: Ws) -> None:
    assert doctor.install_check(ws.copy, "0.6.0") == doctor.Check(
        "atc install", "READY", f"atc 0.6.0 from the release copy {ws.copy}")
    (ws.copy / "README.md").write_text("edited\n", encoding="utf-8")
    check = doctor.install_check(ws.copy, "0.6.0")
    assert check.status == "ERROR" and "it has tracked modifications" in check.detail
    git(ws.copy, "checkout", "-q", "--", "README.md")
    git(ws.copy, "checkout", "-q", "--detach", "v0.5.0")
    check = doctor.install_check(ws.copy, "0.6.0")
    assert check.status == "ERROR" and f"HEAD {ws.old[:12]} is not v0.6.0 ({ws.new[:12]})" in check.detail
    assert check.detail.endswith("atc install v0.6.0")

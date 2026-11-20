"""The integration verifier skeleton (docs/TESTKIT.md § Integration verifier); a project's script calls ``main``."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import importlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import pkgutil
import re
import subprocess
import sys
import time

from . import elapsed, evidence, inputs, isolation, lint, locations, managed, scratch, stages, timing, \
    workers as workers_module
from .git import SelectionError, baseline_deltas, changed_paths, git_env
from .project import Project, StaticCheck
from .selection import Selection, describe

REQUIRED_ENV = ("ATC_EXPECTED_MAIN_SHA", "ATC_INTEGRATION_SHA", "ATC_CANDIDATE_DIR", "ATC_REVIEWED_SOURCE_SHA")
CLOCK_COMMAND = "verify"
CHECKS_STAGE = "checks"
FULL_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
AUTOMATION_MARKDOWN = frozenset({"agents.md", "claude.md"})
MARKDOWN_SUFFIXES = (".md", ".markdown")
RANGE_UNREADABLE = "RANGE_UNREADABLE"
REPORTED_LINES = 20


class VerifyError(Exception):
    """A refusal: ``token`` is the first stderr line, ``lines`` the summary after it."""

    def __init__(self, token: str, *lines: str):
        super().__init__(token)
        self.token, self.lines = token, lines


def require_env(environ: Mapping[str, str]) -> dict[str, str]:
    missing = [name for name in REQUIRED_ENV if not environ.get(name)]
    if missing:
        raise VerifyError("VERIFY_ENV_MISSING", "missing: " + ", ".join(missing))
    values = {name: environ[name] for name in REQUIRED_ENV}
    invalid = [name for name in REQUIRED_ENV if name.endswith("_SHA") and not FULL_OBJECT_ID.fullmatch(values[name])]
    if invalid:
        raise VerifyError("VERIFY_ENV_INVALID", *(f"{name} {values[name]} is not a full object ID" for name in invalid))
    return values


def inside(path: str | Path, root: str | Path) -> bool:
    """Whether ``path`` is ``root`` or below it, compared by long resolved names."""
    path, root = (Path(os.path.normcase(Path(value).resolve())) for value in (path, root))
    return path.is_relative_to(root)


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, env=git_env(),
                          stdin=subprocess.DEVNULL, creationflags=scratch.WINDOW_HIDDEN)


def check_candidate(root: Path, values: Mapping[str, str], cwd: Path) -> None:
    """The verifier's tree, its working directory and ``HEAD`` are the candidate the adapter named."""
    candidate = values["ATC_CANDIDATE_DIR"]
    if not (inside(root, candidate) and inside(candidate, root) and inside(cwd, root) and inside(root, cwd)):
        raise VerifyError("CANDIDATE_DIR_MISMATCH", f"candidate: {candidate}", f"verifier tree: {root}", f"cwd: {cwd}")
    sha = values["ATC_INTEGRATION_SHA"]
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    if head != sha:
        raise VerifyError("CANDIDATE_HEAD_MISMATCH", f"HEAD {head or '<none>'}, ATC_INTEGRATION_SHA {sha}")
    for name, token in (("ATC_EXPECTED_MAIN_SHA", "EXPECTED_MAIN_NOT_IN_CANDIDATE"),
                        ("ATC_REVIEWED_SOURCE_SHA", "REVIEWED_SOURCE_NOT_IN_CANDIDATE")):
        if git(root, "merge-base", "--is-ancestor", values[name], sha).returncode != 0:
            raise VerifyError(token, f"{name} {values[name]} is not an ancestor of {sha}")


def _source_roots(project: Project) -> list[Path]:
    roots = [path for pattern in project.source_roots for path in project.root.glob(pattern) if path.is_dir()]
    return sorted(set(roots))


def _tree_entries(project: Project) -> list[Path]:
    """Every top-level module or package under the source roots whose name carries a product prefix."""
    found = []
    for source in _source_roots(project):
        for entry in source.iterdir():
            name = entry.stem if entry.suffix == ".py" else entry.name
            if name.startswith(project.product_module_prefixes) and name.isidentifier() and (
                    entry.is_dir() or entry.suffix == ".py"):
                found.append(entry)
    return found


def module_names(project: Project) -> list[str]:
    """Every product module the tree defines and every one the search path offers under the prefixes."""
    names = {entry.stem if entry.suffix == ".py" else entry.name for entry in _tree_entries(project)}
    names.update(module.name for module in pkgutil.iter_modules()
                 if module.name.startswith(project.product_module_prefixes))
    return sorted(names)


def import_violations(names: Iterable[str], candidate: str | Path) -> list[str]:
    """``name: location`` for each module whose file or package directory lies outside ``candidate``."""
    importlib.invalidate_caches()
    violations = []
    for name in names:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError) as error:
            violations.append(f"{name}: {type(error).__name__}: {error}")
            continue
        found = [] if spec is None else [*([spec.origin] if spec.has_location else []),
                                          *(spec.submodule_search_locations or ())]
        if not found:
            violations.append(f"{name}: not importable")
        violations.extend(f"{name}: {location}" for location in found if not inside(location, candidate))
    return violations


def namespace_violations(project: Project) -> list[str]:
    """Each source root of a product namespace package that its namespace does not import from."""
    violations = []
    for entry in _tree_entries(project):
        if not entry.is_dir() or (entry / "__init__.py").exists():
            continue
        try:
            spec = importlib.util.find_spec(entry.name)
        except (ImportError, ValueError) as error:
            violations.append(f"{entry.name}: {type(error).__name__}: {error}")
            continue
        active = [] if spec is None else list(spec.submodule_search_locations or ())
        if not any(os.path.normcase(Path(place).resolve()) == os.path.normcase(entry.resolve()) for place in active):
            violations.append(f"{entry.name}: {entry} is not among {active}")
    return violations


def check_imports(project: Project, candidate: str) -> None:
    """The test runner imports the candidate's code: checked on its search path before any test starts."""
    saved = sys.path[:]
    sys.path[:0] = [str(entry) for entry in project.tests_toolchain().search_path(project.root)]
    try:
        violations = [*import_violations(module_names(project), candidate), *namespace_violations(project)]
    finally:
        sys.path[:] = saved
    if violations:
        raise VerifyError("CANDIDATE_IMPORT_MISMATCH", *violations)


def _git_stdout(root: Path) -> Callable[..., str]:
    def run(*args: str) -> str:
        result = git(root, *args)
        if result.returncode != 0:
            raise SelectionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout
    return run


def read_range(project: Project, base: str, sha: str):
    """The range's paths and unmarked-baseline deltas, read as one pushed ref; an empty range is refused."""
    unreadable = RANGE_UNREADABLE if project.select is None else "SELECTION_FAILED"
    lines = [f"refs/heads/candidate {sha} refs/heads/main {base}"]
    run_git = _git_stdout(project.root)
    try:
        paths = changed_paths(lines, run_git, missing_remote="full")
        if paths is None:
            raise SelectionError("the expected main is not in the candidate")
        if not paths:
            raise VerifyError("EMPTY_INTEGRATION_DIFF", f"range {base}..{sha} changes no path")
        touched = project.baseline_files.intersection(paths)
        return paths, baseline_deltas(lines, run_git, touched, missing_remote="full") if touched else None
    except SelectionError as error:
        raise VerifyError(unreadable, f"range {base}..{sha} is not readable in the candidate", str(error)) from error


def is_automation_markdown(path: str, prefixes: Sequence[str]) -> bool:
    normalized = path.replace("\\", "/").lower()
    return (normalized.rsplit("/", 1)[-1] in AUTOMATION_MARKDOWN
            or normalized.startswith(tuple(prefix.lower() for prefix in prefixes)))


def documentation_only(paths: Sequence[str], pytest_paths: list[str] | None, prefixes: Sequence[str],
                       owned: Callable[[str], bool] = lambda _path: False) -> bool:
    """The selection names no test, and every path is ATC-managed or Markdown that is not automation Markdown."""
    return (bool(paths) and pytest_paths is None
            and all(owned(path) or (path.lower().endswith(MARKDOWN_SUFFIXES)
                                      and not is_automation_markdown(path, prefixes)) for path in paths))


def is_test_input(path: str, testpaths: Sequence[str]) -> bool:
    """A test, test helper, fixture or ``conftest.py``: a path below a test path, or a package's tests."""
    parts = PurePosixPath(path).parts
    return (parts[-1] == "conftest.py" or any(path == group or path.startswith(group.rstrip("/") + "/")
                                              for group in testpaths)
            or (len(parts) > 3 and parts[0] == "packages" and parts[2] == "tests"))


def non_input(project: Project, commit: str) -> Callable[[str], bool]:
    """The project's documentation-only classifier for one path of ``commit``: a path no check reads
    (docs/TESTKIT.md § Input evidence)."""
    if project.select is None:
        return lambda path: project.documentation_only([path])
    prefixes = project.automation_markdown_prefixes

    def judge(path: str) -> bool:
        owned = managed.is_managed(path, project.root, commit)
        if not owned and not (path.lower().endswith(MARKDOWN_SUFFIXES) and not is_automation_markdown(path, prefixes)):
            return False
        return documentation_only([path], project.select([path], None).pytest_paths(), prefixes, lambda _path: owned)
    return judge


def excluded_paths(project: Project, commit: str) -> list[str]:
    """The paths of ``commit`` outside the input set."""
    tree = inputs.entries(inputs.git_reader(project.root), commit)
    return inputs.non_inputs((path for _mode, _blob, path in tree), non_input(project, commit))


def _classify(project: Project, commit: str) -> int:
    """``ATC_INPUTS_OF``: print the excluded paths of ``commit`` as one JSON line, and run no check."""
    try:
        excluded = excluded_paths(project, commit)
    except Exception as error:  # noqa: BLE001 - a reader treats a failed classification as no excluded list
        print(f"INPUTS_UNREADABLE {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"commit": commit, "excluded": excluded,
                      "dependency_files": list(project.tests_dependency_files())}), flush=True)
    return 0


class _Reuse:
    """The stages' input evidence: each stage runs only the files no record covers, and the files a passed stage
    ran are recorded after the final-state check (docs/TESTKIT.md § Input evidence)."""

    def __init__(self, project: Project, work: Path, sha: str, environ: Mapping[str, str]):
        self.project, self.work, self.sha = project, work, sha
        self.common = inputs.common_directory(project.root)
        self.tree = inputs.entries(inputs.git_reader(project.root), sha)
        self.excluded = excluded_paths(project, sha)
        self.dependencies = evidence.dependency_digests(project.root, project.tests_dependency_files())
        self.slack = timing.slack(environ)
        self.finder = inputs.Finder(self.common, self.tree) if inputs.has_records(self.common) else None
        self.ran: list[stages.Stage] = []
        self.counted: dict[str, tuple[int, int]] = {}

    @classmethod
    def create(cls, project: Project, work: Path, sha: str, environ: Mapping[str, str]) -> _Reuse | None:
        """``None``, after a note, when the candidate's input set cannot be read: the stages run every file and the
        report names no inputs."""
        try:
            return cls(project, work, sha, environ)
        except Exception as error:  # noqa: BLE001 - evidence never decides a verdict
            print(f"input evidence: off ({type(error).__name__}: {error})", flush=True)
            return None

    def report(self) -> dict:
        return inputs.summary(self.tree, self.excluded, self.sha)

    def identity(self, file: str, marker: str) -> dict:
        return evidence.test_file_identity(file, marker, self.dependencies, self.slack)

    def covered(self, file: str, marker: str) -> bool:
        assert self.finder is not None
        return any(stages.covers(recorded, marker) and self.finder.find(self.identity(file, recorded)) is not None
                   for recorded in dict.fromkeys(("", stages.PRIVATE_EXCLUSION, marker)))

    def outcomes(self, stage: stages.Stage) -> Path:
        return self.work / f"{stage.name}-outcomes.json"

    def paths(self, stage: stages.Stage, paths: Sequence[str], cwd: Path, env: Mapping[str, str],
              command: Sequence[str]) -> Sequence[str] | None:
        """The paths ``stage`` runs: ``paths`` when no record exists or its files cannot be listed, the files no
        record covers, or ``None`` when records cover every file."""
        if self.finder is None:
            return paths
        listing = self.work / f"{stage.name}-collected.json"
        done = subprocess.run(list(command), cwd=cwd, env={**env, inputs.COLLECTED: str(listing)},
                              capture_output=True, timeout=stages.COLLECT_TIMEOUT, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        files = inputs.repository_files(listing, self.project.root) if done.returncode in (0, 5) else None
        if files is None:
            print(f"{stage.name}: input evidence not read: its files could not be listed", flush=True)
            return paths
        prefix = Path(os.path.relpath(os.path.realpath(cwd), os.path.realpath(self.project.root))).as_posix()
        missing = [file for file in files if not self.covered(file, stage.marker)]
        print(f"{stage.name}: REUSED {len(files) - len(missing)} files, RUN {len(missing)} files", flush=True)
        self.counted[stage.name] = (len(files) - len(missing), len(missing))
        if not missing:
            return None
        return [file if prefix == "." else file.removeprefix(f"{prefix}/") for file in missing]

    def record(self) -> None:
        """Write a record for every file each passed stage ran; a failure leaves the verdict a pass."""
        try:
            for stage in self.ran:
                for file in inputs.repository_files(self.outcomes(stage), self.project.root, "passed") or []:
                    inputs.write(self.common, self.identity(file, stage.marker), self.tree, self.excluded, self.sha)
            inputs.prune(self.common)
        except Exception as error:  # noqa: BLE001 - the verification already passed
            print(f"input evidence: not written ({type(error).__name__}: {error})", flush=True)


@dataclass(frozen=True)
class _Row:
    token: str
    argv: tuple[str, ...]
    cwd: str = "."


class _Checks:
    """Runs one check or the stages of a run, refusing with the row's token."""

    def __init__(self, project: Project, work: Path, isolated: isolation.IsolatedRun, run: stages.Run,
                 workers: stages.Workers | None = None, budget: workers_module.Budget | None = None,
                 clock: elapsed.Clock | None = None, reuse: _Reuse | None = None):
        self.project, self.work, self.isolated, self.run_logged = project, work, isolated, run
        self.workers, self.budget, self.clock, self.reuse = workers, budget, clock, reuse
        self.check_seconds, self.checks_run = 0.0, 0

    def row(self, row: _Row | StaticCheck, name: str | None = None) -> None:
        log = self.work / f"{(name or row.token).lower()}.log"
        started = time.monotonic()
        code = self.run_logged(list(row.argv), cwd=self.project.root / row.cwd,
                               env=self.isolated.environment("static"), log=log)
        self.check_seconds += time.monotonic() - started
        self.checks_run += 1
        if code:
            self.report_checks("FAIL")
            raise VerifyError(row.token, f"{' '.join(row.argv)} exited {code}; log {log}", *stages.failure_lines(log))

    def report_checks(self, result: str = "PASS") -> None:
        """The static checks run since the last report, as one ``checks`` stage of the clock."""
        if self.clock is not None and self.checks_run:
            self.clock.stage(CHECKS_STAGE, self.check_seconds, 1, result)
        self.check_seconds, self.checks_run = 0.0, 0

    def select(self, kind: str, reasons: Sequence[str], changed: int) -> None:
        """The run's selection on the clock's records (docs/TESTKIT.md § Stage times)."""
        if self.clock is not None:
            self.clock.select(kind, elapsed.selection_reason(kind, reasons, changed))

    def static(self, checks: Iterable[StaticCheck], paths: Sequence[str]) -> None:
        for check in checks:
            if check.applies(paths):
                self.row(check, check.name)

    def collection(self) -> None:
        options = self.isolated.run_options("collection")
        self.row(_Row("COLLECTION_FAILED", tuple(self.isolated.toolchain.collect_command(options))))

    def stages(self, pytest_paths: list[str], cwd: Path | None = None) -> None:
        project = self.project
        cwd = project.root if cwd is None else cwd
        python = self.isolated.toolchain.name == "python"
        if self.reuse is not None and not python:
            print(f"input evidence: not used by the {self.isolated.toolchain.name} toolchain", flush=True)
        reuse = self.reuse if python else None
        self.report_checks()

        def env_for(stage: stages.Stage) -> dict[str, str]:
            """The isolated environment, which never holds an outcomes file, plus the stage's own when its input
            evidence is recorded."""
            env = dict(self.isolated.environment(stage.name))
            if reuse is not None:
                env[inputs.OUTCOMES] = str(reuse.outcomes(stage))
            return env

        def paths_for(stage: stages.Stage) -> Sequence[str] | None:
            command = self.isolated.toolchain.stage_command(
                project.stage_entry(), stage, None, [*self.isolated.run_options(stage.name), "--collect-only", "-q"],
                pytest_paths)
            own = pytest_paths if reuse is None else reuse.paths(stage, pytest_paths, cwd, env_for(stage), command)
            if reuse is not None and self.clock is not None and stage.name in reuse.counted:
                reused, run = reuse.counted[stage.name]
                self.clock.note(stage.name, reused_files=reused, run_files=run)
            if reuse is not None and own is not None:
                reuse.ran.append(stage)
            return own

        try:
            outcome = stages.run_stages(
                project.stage_entry(), pytest_paths, cwd=cwd,
                stage_list=project.stage_list(), env_for=env_for,
                options_for=lambda stage: self.isolated.run_options(stage.name),
                log_for=lambda stage: self.work / self.isolated.toolchain.stage_log(stage.name), workers=self.workers,
                run=self.run_logged, toolchain=self.isolated.toolchain, budget=self.budget,
                count=stages.collected_items if self.run_logged is stages.run_logged else None, clock=self.clock,
                paths_for=paths_for)
        except workers_module.WaitTimeout as refusal:
            raise VerifyError(refusal.token, *refusal.detail.splitlines()) from refusal
        if outcome.token == stages.NO_TESTS_COLLECTED:
            raise VerifyError(outcome.token, f"selected {' '.join(pytest_paths) or 'the default suite'}; "
                                             f"logs in {self.work}")
        if outcome.token:
            raise VerifyError(outcome.token, f"{outcome.stage.name} stage exited {outcome.code}; log {outcome.log}",
                              *stages.failure_lines(outcome.log))


def check_final_state(root: Path, sha: str) -> None:
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    if head != sha:
        raise VerifyError("CANDIDATE_HEAD_MOVED", f"HEAD {head or '<none>'}, ATC_INTEGRATION_SHA {sha}")
    dirty = git(root, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
    if dirty:
        raise VerifyError("CANDIDATE_DIRTY", *dirty[:REPORTED_LINES])


def _write_report(environ: Mapping[str, str], project: Project, pytest_paths: list[str] | None, doc_only: bool,
                  reuse: _Reuse | None) -> None:
    """A report that cannot be written leaves the verdict a pass."""
    try:
        path = evidence.write_report(environ, project.root, pytest_paths, project.stage_list(),
                                     dependency_files=project.tests_dependency_files(), inputs=None if reuse is None else reuse.report(),
                                     classification="documentation-only" if doc_only else "impact-selected")
    except Exception as error:  # noqa: BLE001 - the verification already passed
        print(f"verification report: not written ({type(error).__name__}: {error})", flush=True)
        return
    if path is not None:
        print(f"verification report: {path}", flush=True)


def _lint(checks: _Checks, root: Path) -> None:
    """The lint stage over the whole candidate tree, in the candidate's own environment (docs/TESTKIT.md § Lint
    stage); a candidate without its config has not adopted it."""
    if not lint.configured(root):
        print(lint.not_configured_line(root), flush=True)
        return
    checks.row(_Row(lint.TOKEN, tuple(lint.tree_argv(sys.executable))), "lint")


def _consumer_checks(project: Project, checks: _Checks, base: str, paths: list[str],
                     consumer_selection: Selection) -> bool:
    """The rows from the selection to the stages, for a project that carries the testkit; whether documentation-only."""
    pytest_paths = consumer_selection.pytest_paths()
    doc_only = documentation_only(paths, pytest_paths, project.automation_markdown_prefixes,
                                  lambda path: managed.is_managed(path, project.root, base))
    testpaths = project.tests_toolchain().test_paths(project.root)
    collect = any(is_test_input(path, testpaths) for path in paths)
    print(describe(consumer_selection), flush=True)
    checks.select(elapsed.DOCUMENTATION_ONLY if doc_only else elapsed.kind_of(pytest_paths),
                  consumer_selection.reasons, len(paths))
    print(f"classification: {'documentation-only' if doc_only else 'impact-selected'}; "
          f"repository-wide collection: {'check' if collect else 'skip'}", flush=True)
    checks.static((check for check in project.checks if check.first), paths)
    checks.row(_Row("UV_LOCK_CHECK_FAILED", checks.isolated.toolchain.lock_check_argv))
    _lint(checks, project.root)
    if doc_only:
        return doc_only
    checks.row(_Row("TESTKIT_CONTRACT_FAILED", (sys.executable, "-m", "atc_testkit.check", "--installed")))
    if collect:
        checks.collection()
    checks.static((check for check in project.checks if not check.first), paths)
    if pytest_paths is not None:
        checks.stages(pytest_paths)
    return doc_only


def _root_reasons(project: Project, paths: list[str]) -> Sequence[str]:
    """The root form's selection reasons, for the stage-time records only; none when they cannot be read."""
    if project.test_reasons is None:
        return ()
    try:
        return project.test_reasons(paths)
    except Exception:  # noqa: BLE001 - a record field never refuses the candidate
        return ()


def _root_checks(project: Project, checks: _Checks, paths: list[str]) -> tuple[bool, list[str] | None]:
    """The root form: its static checks, then the stages over its selection unless the range is documentation-only;
    whether it was, and the pytest paths the stages ran."""
    doc_only = project.documentation_only(paths)
    print(f"classification: {'documentation-only' if doc_only else 'full'}", flush=True)
    pytest_paths = None
    if not doc_only:
        try:
            pytest_paths = project.test_paths(paths)
        except Exception as error:  # noqa: BLE001 - a selection failure refuses the candidate
            raise VerifyError("SELECTION_FAILED", f"{type(error).__name__}: {error}") from error
    checks.select(elapsed.DOCUMENTATION_ONLY if doc_only else elapsed.kind_of(pytest_paths),
                  _root_reasons(project, paths) if pytest_paths == [] else (), len(paths))
    checks.static((check for check in project.checks if check.first), paths)
    _lint(checks, project.root)
    checks.static((check for check in project.checks if not check.first), paths)
    if pytest_paths is not None:
        checks.stages(pytest_paths, project.root / project.tests_dir)
    elif not doc_only:
        print("selection: no test selected", flush=True)
    return doc_only, pytest_paths


def main(project: Project, environ: Mapping[str, str] | None = None, run: stages.Run = stages.run_logged) -> int:
    """Run the checks in the section's order; exit 0, 1 for a refusal, 9 for an unsuitable temporary root."""
    environ = os.environ if environ is None else environ
    if environ.get(inputs.CLASSIFY):
        return _classify(project, environ[inputs.CLASSIFY])
    work = None
    try:
        isolation.drop_hami_variables(os.environ)
        locations.enter()
        workers = stages.resolve_workers(environ=environ, root=project.root / project.tests_dir)
        timing.resolve(environ)
        budget = stages.open_budget(f"verifier {project.root.name}", environ, root=project.root / project.tests_dir)
        toolchain = project.tests_toolchain()
        values = require_env(environ)
        check_candidate(project.root, values, Path.cwd())
        check_imports(project, values["ATC_CANDIDATE_DIR"])
        base, sha = values["ATC_EXPECTED_MAIN_SHA"], values["ATC_INTEGRATION_SHA"]
        paths, deltas = read_range(project, base, sha)
        chosen, pytest_paths = None, None
        if project.select is not None:
            try:
                chosen = project.select(paths, deltas)
                pytest_paths = chosen.pytest_paths()
            except Exception as error:  # noqa: BLE001 - a selection failure refuses the candidate
                raise VerifyError("SELECTION_FAILED", f"{type(error).__name__}: {error}") from error
        work = isolation.create_working_directory()
        locations.enter(work)
        isolated = isolation.IsolatedRun(work, credentials=project.credential_variables,
                                         data_roots=project.data_root_variables, toolchain=toolchain)
        clock = elapsed.Clock.create(elapsed.task_of(environ), sha, CLOCK_COMMAND, stages.pool_directory(environ),
                                     environ, project.root)
        reuse = _Reuse.create(project, work, sha, environ)
        checks = _Checks(project, work, isolated, run, workers, budget, clock, reuse)
        if project.select is None:
            doc_only, pytest_paths = _root_checks(project, checks, paths)
        else:
            doc_only = _consumer_checks(project, checks, base, paths, chosen)
        checks.report_checks()
        check_final_state(project.root, sha)
        if reuse is not None:
            reuse.record()
        _write_report(environ, project, pytest_paths, doc_only, reuse)
    except locations.Refusal as refusal:
        print(refusal.token, file=sys.stderr)
        print(refusal.detail, file=sys.stderr)
        return refusal.code
    except VerifyError as error:
        print(error.token, file=sys.stderr)
        for line in error.lines:
            print(line, file=sys.stderr)
        return _kept(work)
    except Exception as error:  # noqa: BLE001 - any verifier fault refuses the candidate
        print("VERIFIER_ERROR", file=sys.stderr)
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return _kept(work)
    why = scratch.remove_tree(work, scratch.working_directory_links(work))
    if why:
        print(f"working directory left: {work} ({why})", flush=True)
    print(f"integration verification: PASS {sha}", flush=True)
    return 0


def _kept(work: Path | None) -> int:
    if work is not None:
        print(f"working directory kept: {work}", file=sys.stderr)
    return 1

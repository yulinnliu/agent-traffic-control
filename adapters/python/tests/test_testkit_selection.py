from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / 'hami-testkit/src'))

from dataclasses import replace
import pytest
from hami_testkit import selection as s
import subprocess
from hami_testkit.git import BaselineDelta, baseline_deltas, changed_paths, SelectionError, verify_push_checkout, git_env


@pytest.fixture
def policy():
    return s.SelectionPolicy(
        dependencies={'core': (), 'cli': ('core',), 'independent': ()},
        test_order=('tests', 'packages/core/tests', 'packages/cli/tests'), root_tests='tests',
        full_files=frozenset({'pyproject.toml'}), full_prefixes=('config/',),
        root_prefixes=('scripts/',), root_files=frozenset({'main.py'}),
        ci_files=frozenset({'.github/workflows/ci.yml'}),
        referenced_prefixes=('docs/',), referenced_files=frozenset({'README.md'}))


def choose(policy, path, refs=lambda name: set()):
    result = s.Selection(policy.test_order)
    s.classify(path.replace('\\', '/'), result, refs, policy)
    return result


@pytest.mark.parametrize(('path', 'expected'), [
    ('packages/core/src/x.py', []),
    ('packages/cli/src/x.py', ['tests', 'packages/cli/tests']),
    ('packages/cli/tests/test_x.py', ['packages/cli/tests']),
    ('packages/cli/tests/conftest.py', ['packages/cli/tests']),
    ('packages/cli/pyproject.toml', []),
    ('packages/cli/tests_archived/test_x.py', None),
    ('packages/independent/tests/test_x.py', None),
    ('packages/new/src/x.py', []),
    ('tests/test_x.py', ['tests']), ('tests/conftest.py', []),
    ('main.py', ['tests']), ('scripts/task.py', ['tests']),
    ('config/runtime.json', []), ('pyproject.toml', []),
    ('new.py', []), ('docs/unreferenced.md', None),
    ('.github/workflows/ci.yml', ['tests']),
])
def test_selection_contract(policy, path, expected):
    assert choose(policy, path).pytest_paths() == expected


def test_special_inputs_and_reference_union(policy):
    policy = replace(policy, package_inputs={'research/': 'cli'},
                     fixture_inputs={'fixtures/': ('core',)})
    assert choose(policy, 'research/rules.md').pytest_paths() == ['tests', 'packages/cli/tests']
    assert choose(policy, 'fixtures/data.json').pytest_paths() == ['packages/core/tests']
    assert choose(policy, 'docs/spec.md', lambda name: {'packages/cli/tests'}).pytest_paths() == ['packages/cli/tests']
    assert choose(policy, 'main.py', lambda name: {'packages/cli/tests'}).pytest_paths() == ['tests', 'packages/cli/tests']


def test_unknown_path_widens_an_existing_selection(policy):
    selected = choose(policy, 'packages/cli/src/x.py')
    s.classify('unknown', selected, lambda name: set(), policy)
    assert selected.pytest_paths() == []
    assert 'no selection rule -> full suite' in s.describe(selected)


def test_reference_scanner_excludes_prose_and_selector_examples(tmp_path):
    folder = tmp_path / 'tests'
    folder.mkdir()
    (folder / 'test_input.py').write_text('"""DOC.md"""\n# COMMENT.md\nPATH = "INPUT.md"')
    (folder / 'test_prepush_select.py').write_text('EXAMPLE = "EXAMPLE.md"')
    lookup = s.reference_lookup(s.test_sources(tmp_path, ('tests',)))
    assert lookup('INPUT.md') == {'tests'}
    assert all(lookup(name) == set() for name in ('DOC.md', 'COMMENT.md', 'EXAMPLE.md'))


@pytest.mark.parametrize(('mode', 'expected'), [('full', None), ('merge-base', ['changed.py'])])
def test_missing_remote_is_explicit_policy(mode, expected):
    def git(*args):
        if args[0] == 'cat-file': raise SelectionError('missing')
        if args[0] == 'merge-base': return 'base'
        if args[0] == 'diff': return 'changed.py\n'
        raise AssertionError(args)
    assert changed_paths(['refs/heads/x local refs/heads/x remote'], git, missing_remote=mode) == expected


def test_git_hook_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv('GIT_DIR', 'wrong')
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    assert not any(key.upper().startswith('GIT_') for key in git_env())


@pytest.mark.parametrize(('head', 'status'), [('different', ''), ('commit', ' M file')])
def test_checkout_identity_and_cleanliness_are_required(head, status):
    def git(*args):
        if args[0] == 'status': return status
        if args == ('rev-parse', 'HEAD'): return head
        return 'commit'
    with pytest.raises(SelectionError):
        verify_push_checkout(['refs/heads/x commit refs/heads/x old'], git)



@pytest.mark.parametrize('changes', [
    {'root_tests': 'misspelled/tests'},
    {'test_order': ()},
    {'test_order': ('tests', 'tests')},
    {'dependencies': {'core': ('missing',)}},
    {'fixture_inputs': {'fixtures/': ('misspelled-package',)}},
    {'fixture_inputs': {'fixtures/': ('independent',)}},
    {'fixture_inputs': {'fixtures/': ()}},
    {'package_inputs': {'research/': 'missing'}},
])
def test_invalid_policy_blocks_selection(policy, changes):
    with pytest.raises(ValueError):
        replace(policy, **changes)


def test_unknown_reference_consumer_blocks_selection(policy):
    with pytest.raises(ValueError, match='unknown selected test groups'):
        choose(policy, 'docs/spec.md', lambda name: {'misspelled/tests'})


def test_unknown_selected_group_is_rejected_even_when_other_groups_are_valid(policy):
    selected = s.Selection(policy.test_order)
    with pytest.raises(ValueError, match='unknown selected test groups'):
        selected.add('source.py', {'tests', 'misspelled/tests'}, 'input reference')
    assert not selected.test_dirs
    selected.test_dirs.update({'tests', 'misspelled/tests'})
    selected.full = True
    with pytest.raises(ValueError, match='unknown selected test groups'):
        selected.pytest_paths()



@pytest.mark.parametrize('kind', ['missing','empty','helper_only','escape'])
def test_required_test_inputs_cannot_be_silently_empty(tmp_path, kind):
    if kind in {'empty','helper_only'}:
        (tmp_path/'tests').mkdir()
    if kind=='helper_only':
        (tmp_path/'tests/helper.py').write_text('VALUE = 1')
    group='../outside' if kind=='escape' else 'tests'
    with pytest.raises(ValueError):
        s.test_sources(tmp_path,(group,))


def test_important_input_does_not_depend_on_literal_filename_matching(policy):
    policy=replace(policy,critical_prefixes=('docs/specs/',))
    references=s.reference_lookup({'tests':s.string_literals("NAME = 'CON' + 'TRACT.md'")})
    assert not references('CONTRACT.md')
    assert choose(policy,'docs/specs/CONTRACT.md',references).pytest_paths()==[]
    assert choose(policy,'docs/TIMELINE.md',references).pytest_paths() is None


BASELINE = 'tests/baseline.txt'


def baseline_choose(policy, delta, path=BASELINE):
    policy = replace(policy, baseline_files=frozenset({BASELINE}))
    result = s.Selection(policy.test_order)
    s.classify(path, result, lambda name: set(), policy, None if delta is None else {BASELINE: delta})
    return result


@pytest.mark.parametrize(('delta', 'expected'), [
    (None, []),
    (BaselineDelta(added=True), []),
    (BaselineDelta(added=True, removed_files=frozenset({'packages/cli/tests/test_x.py'})), []),
    (BaselineDelta(), None),
    (BaselineDelta(removed_files=frozenset({'packages/cli/tests/test_x.py'})), ['packages/cli/tests']),
    (BaselineDelta(removed_files=frozenset({'packages/cli/tests/test_x.py', 'packages/core/tests/test_y.py'})),
     ['packages/core/tests', 'packages/cli/tests']),
    (BaselineDelta(removed_files=frozenset({'mystery/test_z.py'})), []),
])
def test_baseline_widens_only_when_it_grows_or_is_unknown(policy, delta, expected):
    assert baseline_choose(policy, delta).pytest_paths() == expected


def test_baseline_cannot_also_be_a_full_suite_input(policy):
    with pytest.raises(ValueError):
        replace(policy, baseline_files=frozenset({'pyproject.toml'}))


def git_in(repo):
    def run(*args):
        done = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, env=git_env())
        if done.returncode:
            raise SelectionError(done.stderr)
        return done.stdout
    return run


def commit_baseline(git, repo, text):
    (repo / 'tests').mkdir(exist_ok=True)
    (repo / BASELINE).write_text(text)
    git('add', '-A')
    git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'c')
    return git('rev-parse', 'HEAD').strip()


@pytest.mark.parametrize(('after', 'added', 'removed'), [
    ('# note\npackages/core/tests/test_b.py::two\n', False, {'packages/cli/tests/test_a.py'}),
    ('# note\npackages/core/tests/test_b.py::two\nnew/tests/test_c.py::three\n', True, {'packages/cli/tests/test_a.py'}),
    ('# renamed note\npackages/cli/tests/test_a.py::T::one\npackages/core/tests/test_b.py::two\n', False, set()),
])
def test_baseline_deltas_read_real_diffs(tmp_path, after, added, removed):
    git = git_in(tmp_path)
    git('init', '-q')
    before = commit_baseline(git, tmp_path, '# note\npackages/cli/tests/test_a.py::T::one\npackages/core/tests/test_b.py::two\n')
    tip = commit_baseline(git, tmp_path, after)
    deltas = baseline_deltas([f'refs/heads/x {tip} refs/heads/x {before}'], git, [BASELINE])
    assert deltas == {BASELINE: BaselineDelta(added, frozenset(removed))}


def test_baseline_deltas_ignore_untouched_files_and_unknown_ranges(tmp_path):
    git = git_in(tmp_path)
    git('init', '-q')
    before = commit_baseline(git, tmp_path, 'a::b\n')
    (tmp_path / 'other.txt').write_text('x')
    git('add', '-A')
    git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'o')
    tip = git('rev-parse', 'HEAD').strip()
    assert baseline_deltas([f'refs/heads/x {tip} refs/heads/x {before}'], git, [BASELINE]) == {}
    missing = '1' * 40
    assert baseline_deltas([f'refs/heads/x {tip} refs/heads/x {missing}'], git, [BASELINE]) is None

"""Release identities change with canonical source and preserve immutable artifacts."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from build_testkit_release import publish_local, source_record


def test_source_identity_tracks_metadata_and_python_sources(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="1"\n')
    source = tmp_path / "src/atc_testkit"
    source.mkdir(parents=True)
    module = source / "__init__.py"
    module.write_text('VALUE = 1\n')
    before = source_record(tmp_path)
    (tmp_path / "README.md").write_text('Operational prose')
    assert source_record(tmp_path) == before
    module.write_text('VALUE = 2\n')
    assert source_record(tmp_path)["source_sha256"] != before["source_sha256"]
    assert before["source_files"] == ["pyproject.toml", "src/atc_testkit/__init__.py"]


def test_local_release_is_repeatable_and_rejects_changed_content(tmp_path):
    artifact = tmp_path / "release.whl"
    publish_local(artifact, b"reviewed")
    publish_local(artifact, b"reviewed")
    with pytest.raises(ValueError, match="immutable release differs"):
        publish_local(artifact, b"changed")
    assert artifact.read_bytes() == b"reviewed"



def test_release_build_uses_clean_source_and_excludes_removed_modules(tmp_path, monkeypatch):
    import json
    import zipfile
    import build_testkit_release as builder
    package = tmp_path / 'package'
    package.mkdir()
    (package / 'pyproject.toml').write_bytes((builder.PACKAGE / 'pyproject.toml').read_bytes())
    source = package / 'src/atc_testkit'
    source.mkdir(parents=True)
    (source / '__init__.py').write_text('__version__ = "7.8.9"\n', encoding='utf-8')
    nested = source / 'nested'
    nested.mkdir()
    (nested / '__init__.py').write_text('NESTED = True\n', encoding='utf-8')
    stale = package / 'build/lib/atc_testkit/removed.py'
    stale.parent.mkdir(parents=True)
    stale.write_text('REMOVED_IMPLEMENTATION = True\n', encoding='utf-8')
    stale_metadata = package / 'src/atc_testkit.egg-info/SOURCES.txt'
    stale_metadata.parent.mkdir()
    stale_metadata.write_text('build/lib/atc_testkit/removed.py\n', encoding='utf-8')
    monkeypatch.setattr(builder, 'PACKAGE', package)
    output = tmp_path / 'release'
    assert builder.main(['--output-dir', str(output)]) == 0
    release = json.loads((output / 'atc-testkit.json').read_text(encoding='utf-8'))
    assert (release['name'], release['version']) == ('atc-testkit', '7.8.9')
    with zipfile.ZipFile(output / release['wheel']) as archive:
        payload = {name for name in archive.namelist() if name.endswith('.py')}
        assert payload == {'atc_testkit/__init__.py', 'atc_testkit/nested/__init__.py'}
    assert stale.exists() and stale_metadata.exists()
    assert release['source_sha256'] == builder.source_record(package)['source_sha256']
    assert 'src/atc_testkit/nested/__init__.py' in release['source_files']


@pytest.mark.parametrize(('payload', 'message'), [
    ({}, 'missing='),
    ({'atc_testkit/__init__.py': b'VALUE = 1\n', 'atc_testkit/removed.py': b'old'}, 'unexpected='),
    ({'atc_testkit/__init__.py': b'VALUE = 1\n', 'unrelated.py': b'old'}, 'unexpected='),
    ({'atc_testkit/__init__.py': b'VALUE = 2\n'}, 'modified='),
])
def test_wheel_payload_must_match_source_exactly(tmp_path, payload, message):
    import zipfile
    from build_testkit_release import validate_wheel
    source = tmp_path / 'src/atc_testkit'
    source.mkdir(parents=True)
    (source / '__init__.py').write_bytes(b'VALUE = 1\n')
    wheel = tmp_path / 'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('atc_testkit-0.1.1.dist-info/METADATA', b'Name: atc-testkit')
        for name, raw in payload.items():
            archive.writestr(name, raw)
    with pytest.raises(ValueError, match=message):
        validate_wheel(wheel, tmp_path)


def test_duplicate_wheel_entries_are_rejected(tmp_path):
    import zipfile
    from build_testkit_release import validate_wheel
    source = tmp_path / 'src/atc_testkit'
    source.mkdir(parents=True)
    (source / '__init__.py').write_bytes(b'VALUE = 1\n')
    wheel = tmp_path / 'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('atc_testkit/__init__.py', b'VALUE = 1\n')
        with pytest.warns(UserWarning, match='Duplicate name'):
            archive.writestr('atc_testkit/__init__.py', b'VALUE = 2\n')
    with pytest.raises(ValueError, match='duplicate payload'):
        validate_wheel(wheel, tmp_path)


def test_one_constant_sets_the_version_and_the_readme_names_it():
    import tomllib
    import build_testkit_release as builder
    import atc_testkit
    project = tomllib.loads((builder.PACKAGE / 'pyproject.toml').read_text(encoding='utf-8'))
    assert 'version' not in project['project'] and project['project']['dynamic'] == ['version']
    assert project['tool']['setuptools']['dynamic']['version'] == {'attr': 'atc_testkit.__version__'}
    assert f'`atc-testkit` {atc_testkit.__version__}' in (builder.PACKAGE / 'README.md').read_text(encoding='utf-8')

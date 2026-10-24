"""Build an immutable universal testkit wheel and its consumer release record."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


WORKSPACE = Path(__file__).resolve().parents[3]
PACKAGE = WORKSPACE / "adapters/python/hami-testkit"


def source_record(package: Path) -> dict:
    files = [package / "pyproject.toml", *sorted((package / "src/hami_testkit").rglob("*.py"))]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(package).as_posix().encode() + b"\0" + path.read_bytes())
    return {
        "managed_by": "agent-traffic-control",
        "source_repository": "yulinnliu/agent-traffic-control",
        "source_path": "adapters/python/hami-testkit",
        "source_sha256": digest.hexdigest(),
        "source_files": [path.relative_to(package).as_posix() for path in files],
    }


def snapshot_source(package: Path, snapshot: Path) -> dict:
    """Copy the canonical build inputs into an isolated source tree."""
    record = source_record(package)
    for relative in record["source_files"]:
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((package / relative).read_bytes())
    if source_record(snapshot) != record:
        raise ValueError("canonical source changed while creating the snapshot")
    return record


def validate_wheel(wheel: Path, snapshot: Path) -> None:
    """Require the complete wheel payload to match the canonical source files."""
    expected = {
        path.relative_to(snapshot / "src").as_posix(): path.read_bytes()
        for path in (snapshot / "src/hami_testkit").rglob("*.py")
    }
    with zipfile.ZipFile(wheel) as archive:
        payload = [name for name in archive.namelist()
                   if not name.endswith("/") and not name.split("/", 1)[0].endswith(".dist-info")]
        if len(payload) != len(set(payload)):
            raise ValueError("wheel contains duplicate payload entries")
        missing = expected.keys() - set(payload)
        unexpected = set(payload) - expected.keys()
        if missing or unexpected:
            raise ValueError(f"wheel source mismatch: missing={sorted(missing)}, unexpected={sorted(unexpected)}")
        for name, raw in expected.items():
            if archive.read(name) != raw:
                raise ValueError(f"wheel source mismatch: modified={name}")


def publish_local(path: Path, data: bytes) -> None:
    """Retain identical artifacts and refuse replacement of a released version."""
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"immutable release differs: {path.name}; increment the package version")
        return
    with path.open("xb") as stream:
        stream.write(data)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=WORKSPACE / ".tooling/testkit-dist")
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SOURCE_DATE_EPOCH": "315532800"}
    with tempfile.TemporaryDirectory(prefix="hami-testkit-") as temporary:
        snapshot = Path(temporary) / "source"
        source = snapshot_source(PACKAGE, snapshot)
        output = Path(temporary) / "dist"
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(output), str(snapshot)],
                       env=env, check=True)
        wheel, = output.glob("*.whl")
        validate_wheel(wheel, snapshot)
        if source_record(snapshot) != source:
            raise ValueError("build changed the canonical source snapshot")
        import tomllib
        project = tomllib.loads((snapshot / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        release = {"name": project["name"], "version": project["version"], **source,
                   "wheel": wheel.name, "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
        record = (json.dumps(release, indent=2) + "\n").encode()
        # Check both identities before writing either release component.
        destination = args.output_dir / wheel.name
        manifest = args.output_dir / "hami-testkit.json"
        for path, data in ((destination, wheel.read_bytes()), (manifest, record)):
            if path.exists() and path.read_bytes() != data:
                raise ValueError(f"immutable release differs: {path.name}; use a fresh version/output directory")
        publish_local(destination, wheel.read_bytes())
        publish_local(manifest, record)
    print(destination)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Apply each mutant alone to the scratch copy, run pytest, restore, prove the bytes equal Git.

usage: mutants.py SCRATCH SHA WORKTREE MUTANTS_JSON --python PY --tests PATH [PATH ...]
(the target venv needs pytest-xdist: run_tests.sh passes `-n 0`)

SCRATCH/tree is the export of SHA (`git archive SHA | tar -x -C SCRATCH/tree`).
MUTANTS_JSON is a list of {"label": str, "edits": [{"path", "old", "new"}]}; every
`old` must occur exactly once in its file, otherwise the run aborts.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).with_name("run_tests.sh")


def blob(worktree, sha, path):
    return subprocess.run(["git", "-C", worktree, "show", f"{sha}:{path}"], capture_output=True, check=True).stdout


def digest(data):
    return hashlib.sha256(data.replace(b"\r", b"")).hexdigest()


def run_pytest(args, label, pytest_args):
    tree = str(Path(args.scratch) / "tree")
    run = subprocess.run(["bash", str(RUNNER), tree, args.scratch, args.python, label, *pytest_args],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
    if run.returncode == 8:
        sys.exit(run.stdout.strip() or "COPY_IMPORT_MISMATCH")
    out = run.stdout.splitlines()
    failed = [line for line in out if line.startswith(("FAILED", "ERROR"))]
    last = out[-1] if out else run.stderr[-200:]
    passed = not failed and "passed" in last and "failed" not in last and "error" not in last
    return passed, failed[0][:150] if failed else last


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scratch")
    parser.add_argument("sha")
    parser.add_argument("worktree")
    parser.add_argument("mutants")
    parser.add_argument("--python", required=True)
    parser.add_argument("--tests", nargs="+", required=True)
    args = parser.parse_args()
    tree = Path(args.scratch) / "tree"
    if not tree.is_dir():
        sys.exit(f"missing {tree}: export it with `git archive {args.sha} | tar -x -C {tree}`")
    mutants = json.loads(Path(args.mutants).read_text(encoding="utf-8"))
    pytest_args = ["-x", *args.tests]

    passed, summary = run_pytest(args, "baseline", pytest_args)
    print(f"{'BASELINE-OK' if passed else 'BASELINE-RED'} {summary}", flush=True)
    if not passed:
        sys.exit(1)

    touched, survivors = set(), []
    for mutant in mutants:
        originals = {}
        try:
            for edit in mutant["edits"]:
                target = tree / edit["path"]
                originals.setdefault(target, target.read_bytes())
                data = target.read_bytes()
                old, new = edit["old"].encode(), edit["new"].encode()
                if b"\r\n" in data:
                    old, new = old.replace(b"\n", b"\r\n"), new.replace(b"\n", b"\r\n")
                if data.count(old) != 1:
                    sys.exit(f"{mutant['label']}: `old` occurs {data.count(old)} times in {edit['path']}, need exactly 1")
                target.write_bytes(data.replace(old, new))
                touched.add(edit["path"])
            passed, summary = run_pytest(args, mutant["label"], pytest_args)
            print(f"{'SURVIVED' if passed else 'KILLED  '} {mutant['label']} -> {summary}", flush=True)
            if passed:
                survivors.append(mutant["label"])
        finally:
            for target, data in originals.items():
                target.write_bytes(data)

    mismatched = False
    for path in sorted(touched):
        same = digest((tree / path).read_bytes()) == digest(blob(args.worktree, args.sha, path))
        mismatched |= not same
        print("restored-ok" if same else "RESTORE-MISMATCH", path, flush=True)
    print(f"done: {len(mutants)} mutants, {len(survivors)} survived", *survivors, flush=True)
    sys.exit(1 if mismatched else 0)


if __name__ == "__main__":
    main()

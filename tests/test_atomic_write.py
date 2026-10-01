#!/usr/bin/env python3
"""
test_atomic_write.py -- Phase 2/Part 3 tests for atomic_write.py.

Run: python3 tests/test_atomic_write.py   (from repo root)
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from atomic_write import AtomicWriteError, atomic_write_json, read_json_or_none  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def test_successful_atomic_replacement():
    print("1. successful atomic replacement")
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / "artifact.json"
        atomic_write_json(dest, {"v": 1})
        check("file exists after first write", dest.exists())
        check("content matches first write", json.loads(dest.read_text()) == {"v": 1})
        atomic_write_json(dest, {"v": 2})
        check("content matches second write (replaced, not appended)", json.loads(dest.read_text()) == {"v": 2})
        remaining = [p for p in Path(d).iterdir() if p.name.startswith(".artifact.json.")]
        check("no leftover temp file after a successful write", len(remaining) == 0, f"found {remaining}")


def test_failure_before_replacement_leaves_no_partial_file():
    print("2. failure before replacement leaves no partial file")
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / "artifact.json"
        # NaN is not JSON-serializable with allow_nan=False -- this must fail
        # BEFORE any file touches disk.
        raised = False
        try:
            atomic_write_json(dest, {"v": float("nan")})
        except AtomicWriteError:
            raised = True
        check("atomic_write_json raises AtomicWriteError on unserializable data", raised)
        check("destination file was never created", not dest.exists())
        leftover = list(Path(d).iterdir())
        check("no temp file left behind after a failed write", len(leftover) == 0, f"found {leftover}")


def test_preservation_of_previous_valid_artifact():
    print("3. previous valid artifact is preserved when a new write fails")
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / "artifact.json"
        atomic_write_json(dest, {"v": "good", "n": 1})
        good_content = dest.read_text()
        try:
            atomic_write_json(dest, {"v": float("inf")})
        except AtomicWriteError:
            pass
        check("previous valid artifact is byte-for-byte unchanged after a failed subsequent write",
              dest.read_text() == good_content)
        check("previous valid artifact still parses as valid JSON", json.loads(dest.read_text())["v"] == "good")


def test_invalid_json_never_becomes_canonical():
    print("4. invalid JSON never becomes the canonical artifact")
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / "artifact.json"
        # simulate a caller trying to write a raw, already-broken JSON string
        # via the bytes path -- this should still land as a real file (since
        # atomic_write_bytes doesn't itself validate JSON-ness -- that's
        # atomic_write_json's job), but atomic_write_json itself must never
        # let a bad object through.
        raised = False
        try:
            atomic_write_json(dest, object())  # not JSON-serializable at all
        except AtomicWriteError:
            raised = True
        check("a non-serializable object raises AtomicWriteError, not a silent partial write", raised)
        check("no file was created", not dest.exists())


def test_fsync_and_replace_are_actually_used():
    print("5. implementation actually calls fsync + os.replace (not just open().write())")
    src = (REPO_ROOT / "atomic_write.py").read_text()
    check("uses os.fsync", "os.fsync(" in src)
    check("uses os.replace (atomic rename), not shutil.move or plain rename", "os.replace(" in src)
    check("writes to a temp file in the destination directory (same filesystem)",
          "tempfile.mkstemp(" in src and "dir=str(dest.parent)" in src)


def test_read_json_or_none_helper():
    print("6. read_json_or_none never raises, supports 'preserve last known good' pattern")
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "nope.json"
        check("returns None for a missing file", read_json_or_none(missing) is None)
        broken = Path(d) / "broken.json"
        broken.write_text("{not valid json")
        check("returns None for unparseable JSON, does not raise", read_json_or_none(broken) is None)
        good = Path(d) / "good.json"
        good.write_text(json.dumps({"ok": True}))
        check("returns parsed content for a valid file", read_json_or_none(good) == {"ok": True})


if __name__ == "__main__":
    for fn in [test_successful_atomic_replacement, test_failure_before_replacement_leaves_no_partial_file,
               test_preservation_of_previous_valid_artifact, test_invalid_json_never_becomes_canonical,
               test_fsync_and_replace_are_actually_used, test_read_json_or_none_helper]:
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)

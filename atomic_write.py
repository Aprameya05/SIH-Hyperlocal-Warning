#!/usr/bin/env python3
"""
atomic_write.py -- a small, reusable atomic JSON writer.

Root problem this replaces: every canonical production artifact in this
repo (forecast.json, data/pan_india_grid.json) was written with a bare
`open(path, "w")` + `json.dump(...)`. If the process is killed mid-write
(OOM, runner timeout, disk full), the file on disk is left truncated --
syntactically broken JSON, or worse, a syntactically valid but incomplete
fragment -- and every downstream reader (the frontend, this repo's own
compatibility-artifact derivation) has no way to tell that apart from a
genuine, complete forecast.

This module does the standard safe-write dance and nothing more:
  1. Write to a temp file in the SAME directory (so the final os.replace()
     is on the same filesystem and therefore atomic on POSIX).
  2. flush() + os.fsync() the temp file's contents to disk.
  3. os.replace(tmp, dest) -- atomic rename; readers either see the old
     complete file or the new complete file, never a partial one.
  4. On any exception before step 3, the temp file is removed and the
     previous file at `dest` (if any) is left completely untouched.

Deliberately NOT included, to avoid over-engineering: locking across
processes, retries, backups/versioning (the previous file already IS the
implicit backup, since it's never touched until the new one is ready),
or a generic multi-format writer. This is a JSON writer because every
current caller writes JSON; if a caller needs raw bytes, `atomic_write_bytes`
below is the same primitive without the json.dumps step.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class AtomicWriteError(Exception):
    """Raised when the write could not be completed. The destination file,
    if it existed before this call, is guaranteed unchanged."""


def atomic_write_bytes(dest: "str | Path", data: bytes) -> None:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=str(dest.parent))
    tmp_path = Path(tmp_path)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dest)
    except BaseException as e:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise AtomicWriteError(f"atomic write to {dest} failed, previous file (if any) left untouched: {e}") from e


def atomic_write_json(dest: "str | Path", obj: Any, *, indent: int = 2) -> None:
    """Serializes obj to JSON first (so a serialization error -- e.g. a NaN,
    a non-serializable object -- is caught BEFORE any file is touched, not
    after a partial write), then writes it atomically via atomic_write_bytes.
    """
    # allow_nan=False: this repo's own integrity rule is "no NaN, no Infinity"
    # in any published forecast artifact (see docs/CANONICAL_FORECAST_SCHEMA.md)
    # -- catching it here, at serialization time, is earlier and cheaper than
    # catching it in a separate downstream validator. This must fail BEFORE
    # any file is touched, so it is deliberately outside the try/except in
    # atomic_write_bytes (which only guards the actual disk write).
    try:
        text = json.dumps(obj, indent=indent, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise AtomicWriteError(f"refusing to write {dest}: object is not valid JSON ({e})") from e
    atomic_write_bytes(dest, text.encode("utf-8"))


def read_json_or_none(path: "str | Path"):
    """Convenience for callers that want to preserve 'the last known good
    artifact' semantics: returns the parsed JSON at path, or None if the
    file doesn't exist or fails to parse (never raises)."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

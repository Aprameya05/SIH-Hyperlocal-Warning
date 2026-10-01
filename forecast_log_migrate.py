#!/usr/bin/env python3
"""
forecast_log_migrate.py — safe schema migration for data/forecast_log.csv.

Root cause under test (2026-09-30 root-cause report, Phase 3):
forecast_action.py used to compare the CSV's existing header line against
its current hardcoded column list; on any mismatch it copied the file to
forecast_log.csv.bak, deleted the original, and started a brand-new file
containing only that run's rows. That silently destroyed every previously
accumulated day of verification history at least once (git history shows
the header actually changed), which is why verify_today.py and
populate_skill_scores.py found only a handful of same-day rows and could
not compute any real skill metrics.

This module replaces that reset-on-mismatch behaviour with an in-place,
additive migration:
  - columns shared between the old and new schema are preserved as-is
  - new columns introduced by the current schema are added, filled with ""
    (never fabricated) for every pre-existing row
  - old columns no longer in the current schema are KEPT (appended after
    the current columns) rather than dropped, for historical traceability
  - if the file cannot be parsed at all (genuinely corrupt), nothing is
    deleted: a timestamped backup is made, the original is left in place,
    and migrate_forecast_log() reports failure so the caller can fail
    visibly instead of silently wiping history

No row is ever deleted by this module.
"""

from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


@dataclass
class MigrationResult:
    ok: bool
    action: str                 # "no_file" | "no_op" | "migrated" | "failed"
    message: str
    rows_preserved: int = 0
    columns_added: List[str] = field(default_factory=list)
    columns_kept_legacy: List[str] = field(default_factory=list)
    backup_path: Optional[Path] = None


def _read_existing_rows(log_path: Path):
    """Read the existing CSV permissively. Returns (header, rows) where rows
    is a list of dicts keyed by the existing header. Raises on genuine
    corruption (e.g. the file isn't CSV at all)."""
    with open(log_path, "r", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return [], []
    header = [c.strip() for c in rows[0]]
    data_rows = []
    for r in rows[1:]:
        if not r:
            continue
        # Tolerate short/long rows rather than raising -- pad/truncate to header length.
        r = (r + [""] * len(header))[:len(header)]
        data_rows.append(dict(zip(header, r)))
    return header, data_rows


def migrate_forecast_log(log_path: Path, current_cols: List[str]) -> MigrationResult:
    """
    Ensure log_path's on-disk schema is compatible with current_cols WITHOUT
    losing any existing rows. Rewrites the file in place when migration is
    needed. Never deletes the file. Returns a MigrationResult describing
    what happened so the caller can log it.
    """
    if not log_path.exists():
        return MigrationResult(ok=True, action="no_file", message="no existing file — will be created fresh")

    try:
        existing_header, existing_rows = _read_existing_rows(log_path)
    except Exception as e:
        # Genuine corruption: do NOT touch the original. Back it up (versioned,
        # never overwriting a prior backup) and fail visibly.
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = log_path.with_name(f"{log_path.stem}.corrupt-{ts}{log_path.suffix}")
        try:
            shutil.copy2(log_path, backup_path)
        except Exception:
            backup_path = None
        return MigrationResult(
            ok=False, action="failed",
            message=f"could not parse existing {log_path.name} ({e!r}) — left untouched, "
                    f"backup {'written to ' + backup_path.name if backup_path else 'FAILED'}",
            backup_path=backup_path,
        )

    if not existing_rows:
        # Header-only or truly empty file — safe to just standardize the header.
        if existing_header == current_cols:
            return MigrationResult(ok=True, action="no_op", message="schema already current; file has no data rows")
        with open(log_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=current_cols)
            writer.writeheader()
        return MigrationResult(ok=True, action="migrated", message="empty/header-only file — header standardized, no rows to preserve")

    if existing_header == current_cols:
        return MigrationResult(ok=True, action="no_op", message="schema already current")

    # Union schema: current columns first (so new appends via DictWriter with
    # current_cols keep working unchanged), then any legacy columns not in the
    # current schema, preserved at the end for historical traceability.
    legacy_only_cols = [c for c in existing_header if c not in current_cols]
    union_cols = list(current_cols) + legacy_only_cols
    new_cols_added = [c for c in current_cols if c not in existing_header]

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = log_path.with_name(f"{log_path.stem}.pre-migration-{ts}{log_path.suffix}")
    try:
        shutil.copy2(log_path, backup_path)
    except Exception:
        backup_path = None

    migrated_rows = []
    for row in existing_rows:
        new_row = {col: row.get(col, "") for col in union_cols}
        migrated_rows.append(new_row)

    tmp_path = log_path.with_suffix(".csv.migrating")
    with open(tmp_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=union_cols)
        writer.writeheader()
        for row in migrated_rows:
            writer.writerow(row)
    tmp_path.replace(log_path)

    return MigrationResult(
        ok=True, action="migrated",
        message=f"migrated {len(migrated_rows)} existing row(s) to current schema "
                f"(+{len(new_cols_added)} new col(s), kept {len(legacy_only_cols)} legacy col(s))",
        rows_preserved=len(migrated_rows),
        columns_added=new_cols_added,
        columns_kept_legacy=legacy_only_cols,
        backup_path=backup_path,
    )

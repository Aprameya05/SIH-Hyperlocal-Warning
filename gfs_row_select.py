#!/usr/bin/env python3
"""
gfs_row_select.py — single source of truth for "which row of
data/gfs_realtime_43295.csv is the freshest valid GFS input".

Background (see docs/PIPELINE_OWNERSHIP.md and the 2026-09-30 root-cause
report): gfs_fetcher.py appends one row per slot per day to
data/gfs_realtime_43295.csv and never re-sorts the file. forecast_action.py
and compute_realtime_shap.py both used to take gfs_df.iloc[0] after
filtering to today's date, which is whichever row happened to be appended
first that day — NOT the most recently fetched row. This module replaces
that with an explicit, timestamp-based selection so both consumers agree on
exactly one "latest" row regardless of on-disk row order.

Authoritative fields (traced from gfs_fetcher.py's write_gfs_realtime_csv):
  fetched_at_utc  -- "YYYY-MM-DD HH:MM", naive, UTC. The real recency signal.
  gfs_cycle       -- "YYYYMMDD HHZ fNNN"-ish free text, e.g. "2026-09-30 00Z f012".
                     Used only as a secondary tiebreaker via the embedded cycle
                     time + forecast hour (i.e. GFS valid time), never as the
                     primary sort key, since fetched_at_utc is the only field
                     that reliably reflects when this specific row was written.
  date            -- calendar date this row belongs to (station-local "today").
  slot            -- which forecast slot's run produced this row (0-3).
fetched_at (IST, human-readable) exists in the schema but is frequently blank
in practice, so it is not relied on for selection.

There is no station-id column in this file: it is a single-station
(WMO 43295 / VOBL) series by construction, so "correct station" is
guaranteed by construction rather than filtered here.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

import pandas as pd

CYCLE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{2})Z(?:\s+f(\d{3}))?")

# Phase 4.5 Part 3: maximum age (hours) a GFS row's fetched_at_utc may have
# and still be accepted as a current forecast input. Deterministic
# "newest available" selection and "fresh enough to trust" are different
# concepts -- this repo's GFS cycles post every 6 hours (00/06/12/18Z) and
# forecast_update.yml/update_grid.yml both run on a ~6-hourly cadence
# aligned to that, so a row that is more than roughly one cycle old was
# fetched before the CURRENT cycle would even be expected to have posted.
# This is deliberately the SAME number as canonical_forecast_writer.py's
# GFS_STALE_AFTER_HOURS (one 6h cycle + a 1h buffer for scheduling jitter,
# documented there), not a separately-invented threshold -- both places
# encode the same real cadence fact and must not silently drift apart.
GFS_MAX_AGE_HOURS = 7.0


def check_gfs_freshness(fetched_at_utc, now_utc=None, max_age_hours: float = GFS_MAX_AGE_HOURS) -> str:
    """Classifies a single fetched_at_utc timestamp as one of:
      - "VALID"   -- present, parseable, and within max_age_hours of now_utc.
      - "STALE"   -- present, parseable, but older than max_age_hours.
      - "INVALID" -- missing, unparseable, or in the future beyond a small
                     clock-skew allowance (a "fresh" timestamp that is
                     actually impossible is treated as untrustworthy, not
                     silently accepted as extra-fresh).
    Never raises; never guesses a timestamp that isn't there.
    """
    if now_utc is None:
        now_utc = pd.Timestamp.utcnow().tz_localize(None)
    if fetched_at_utc is None:
        return "INVALID"
    try:
        ts = pd.Timestamp(fetched_at_utc)
        if ts.tzinfo is not None:
            ts = ts.tz_convert(None)
    except (TypeError, ValueError):
        return "INVALID"
    if pd.isna(ts):
        return "INVALID"
    age_hours = (now_utc - ts).total_seconds() / 3600.0
    # A small allowance (10 min) absorbs ordinary clock skew between the
    # fetching runner and whatever evaluates freshness later; anything
    # further in the future than that is not a real fetch time.
    if age_hours < -(10 / 60):
        return "INVALID"
    if age_hours > max_age_hours:
        return "STALE"
    return "VALID"

# Columns whose presence/finiteness we require for a row to be considered
# usable model input. Chosen to match what forecast_action.py's monsoon-regime
# pre-detection and feature build actually depend on first.
REQUIRED_FINITE_COLS = ("CAPE", "K_INDEX")


@dataclass
class GFSSelection:
    row: "pd.Series"
    fetched_at_utc: Optional[pd.Timestamp]
    gfs_cycle: str
    gfs_fhour: Optional[int]
    n_candidates: int
    n_valid: int
    freshness: str = "VALID"  # Phase 4.5: always "VALID" here -- a
    # GFSSelection is only ever constructed for a row that passed the
    # max-age gate (see select_latest_gfs). Kept as an explicit field
    # (rather than implied) so callers/tests never have to assume it.

    def log_line(self) -> str:
        # self.gfs_cycle already embeds the forecast hour (e.g. "2026-09-30
        # 00Z f012") when the source string had one, so gfs_fhour is shown
        # separately only as a parsed/validated echo, not appended again.
        fetched = self.fetched_at_utc.strftime("%Y-%m-%d %H:%M") if self.fetched_at_utc is not None else "unknown"
        fh = f"f{self.gfs_fhour:03d}" if self.gfs_fhour is not None else "unparsed"
        return (f"  GFS selected: cycle={self.gfs_cycle}  (parsed fhour={fh})  "
                f"fetched_at_utc={fetched} UTC  freshness={self.freshness}  "
                f"({self.n_valid}/{self.n_candidates} rows valid for today)")


def _parse_cycle_valid_time(gfs_cycle: str):
    """Return (cycle_dt, fhour) parsed out of a 'YYYY-MM-DD HHZ fNNN' string, or (None, None)."""
    if not isinstance(gfs_cycle, str):
        return None, None
    m = CYCLE_RE.search(gfs_cycle)
    if not m:
        return None, None
    date_str, hour_str, fhour_str = m.groups()
    try:
        cycle_dt = pd.Timestamp(f"{date_str} {hour_str}:00")
    except Exception:
        return None, None
    fhour = int(fhour_str) if fhour_str is not None else None
    return cycle_dt, fhour


def _row_is_valid(row: "pd.Series") -> bool:
    for col in REQUIRED_FINITE_COLS:
        if col not in row:
            return False
        v = row[col]
        try:
            v = float(v)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(v):
            return False
    return True


def _valid_sorted(gfs_df: "pd.DataFrame", date_str: Optional[str] = None):
    """Shared filter+validate+sort step used by both select_latest_gfs() and
    compute_cape_tendency(). Returns (df_sorted_newest_first, n_candidates,
    n_valid) where df_sorted_newest_first may be empty."""
    if gfs_df is None or len(gfs_df) == 0:
        return gfs_df.iloc[0:0] if gfs_df is not None else pd.DataFrame(), 0, 0

    df = gfs_df.copy()
    n_candidates = len(df)

    if date_str is not None and "date" in df.columns:
        df = df[df["date"] == date_str]

    if "fetched_at_utc" in df.columns:
        df["_fetched_at_utc_parsed"] = pd.to_datetime(df["fetched_at_utc"], errors="coerce")
    else:
        df["_fetched_at_utc_parsed"] = pd.NaT

    if "gfs_cycle" in df.columns:
        parsed = df["gfs_cycle"].apply(_parse_cycle_valid_time)
        df["_cycle_dt"] = [p[0] for p in parsed]
        df["_fhour"] = [p[1] for p in parsed]
        df["_valid_time"] = [
            (cdt + pd.Timedelta(hours=fh)) if (cdt is not None and fh is not None) else pd.NaT
            for cdt, fh in zip(df["_cycle_dt"], df["_fhour"])
        ]
    else:
        df["_cycle_dt"] = None
        df["_fhour"] = None
        df["_valid_time"] = pd.NaT

    valid_mask = df.apply(_row_is_valid, axis=1)
    n_valid = int(valid_mask.sum())
    df_valid = df[valid_mask]

    if len(df_valid) == 0:
        return df_valid, n_candidates, n_valid

    df_sorted = df_valid.sort_values(
        by=["_fetched_at_utc_parsed", "_valid_time"],
        ascending=[False, False],
        na_position="last",
    )
    return df_sorted, n_candidates, n_valid


def select_latest_gfs(gfs_df: "pd.DataFrame", date_str: Optional[str] = None,
                       now_utc=None, max_age_hours: float = GFS_MAX_AGE_HOURS) -> Optional[GFSSelection]:
    """
    Given the raw gfs_realtime_43295.csv dataframe (as read by pd.read_csv),
    return a GFSSelection wrapping the single freshest, valid, AND FRESH
    ENOUGH row, or None if no such row exists.

    Deterministic ordering (never relies on append/row order in the file):
      1. filter to date_str if given (existing "today only" behaviour)
      2. drop rows failing the finiteness check (NaN/inf CAPE or K_INDEX)
      3. sort by fetched_at_utc descending (primary key — actual fetch recency)
      4. tie-break by the GFS valid time embedded in gfs_cycle
         (cycle time + forecast hour) descending

    Phase 4.5 Part 3: deterministic "newest" selection and "fresh enough to
    trust" are different concepts. The newest candidate's fetched_at_utc is
    checked against max_age_hours via check_gfs_freshness(). If it is
    STALE or INVALID, this function FAILS EXPLICITLY (returns None with an
    explicit printed reason) -- it does NOT silently fall back to an even
    OLDER row in df_sorted, since an older row is never a legitimate
    substitute for "the current cycle wasn't fetched in time". The caller
    (forecast_action.py) already treats a None return as "no valid GFS
    input for today" and degrades to its existing, explicitly-labeled
    climatology fallback -- that decision point is unchanged by this phase.
    """
    df_sorted, n_candidates, n_valid = _valid_sorted(gfs_df, date_str=date_str)
    if len(df_sorted) == 0:
        return None

    best = df_sorted.iloc[0]
    fetched_at = best["_fetched_at_utc_parsed"] if pd.notna(best["_fetched_at_utc_parsed"]) else None
    freshness = check_gfs_freshness(fetched_at, now_utc=now_utc, max_age_hours=max_age_hours)
    if freshness != "VALID":
        age_str = "unknown" if fetched_at is None else f"{(pd.Timestamp.utcnow().tz_localize(None) if now_utc is None else now_utc) - fetched_at}"
        print(f"  GFS: freshest candidate row rejected as {freshness} "
              f"(fetched_at_utc={fetched_at}, age={age_str}, max_age_hours={max_age_hours}) -- "
              f"NOT substituting an older row; treating today as having no valid GFS input")
        return None

    return GFSSelection(
        row=best.drop(labels=["_fetched_at_utc_parsed", "_cycle_dt", "_fhour", "_valid_time"], errors="ignore"),
        fetched_at_utc=fetched_at,
        gfs_cycle=str(best.get("gfs_cycle", "N/A")),
        gfs_fhour=int(best["_fhour"]) if pd.notna(best["_fhour"]) else None,
        n_candidates=n_candidates,
        n_valid=n_valid,
        freshness=freshness,
    )


def latest_gfs_frame(gfs_df: "pd.DataFrame", date_str: Optional[str] = None,
                      now_utc=None) -> "pd.DataFrame":
    """
    Convenience wrapper for call sites that want to keep using `.iloc[0]`
    idiom (forecast_action.py has several) without touching each one:
    returns a 1-row (or 0-row) DataFrame containing only the selected latest
    valid row, so `.iloc[0]` on the result is always correct by construction.

    `now_utc` is forwarded to select_latest_gfs() (default None = real
    wall-clock time, unchanged production behaviour) so tests can pin "now"
    the same way they already do for select_latest_gfs() directly, instead
    of the freshness gate silently aging a fixed-date test fixture past
    GFS_MAX_AGE_HOURS as real time moves forward.
    """
    sel = select_latest_gfs(gfs_df, date_str=date_str, now_utc=now_utc)
    if sel is None:
        return gfs_df.iloc[0:0]
    return pd.DataFrame([sel.row])


@dataclass
class CapeTendency:
    value_jkgh: Optional[float]
    available: bool
    reason: str
    newer_fetched_at_utc: Optional["pd.Timestamp"] = None
    older_fetched_at_utc: Optional["pd.Timestamp"] = None
    dt_hours: Optional[float] = None


def compute_cape_tendency(gfs_df: "pd.DataFrame", date_str: Optional[str] = None,
                           min_dt_hours: float = 0.25) -> CapeTendency:
    """
    CAPE tendency (J/kg/h, positive = instability growing), computed from the
    two most recent VALID rows of data/gfs_realtime_43295.csv for date_str.

    This replaces the old data/gfs_history_43295.json dependency (dead since
    2026-07-26, non-ISO timestamps, nothing writes it any more -- see the
    2026-09-30 root-cause report, Phase 2). gfs_realtime_43295.csv already
    accumulates one row per forecast slot per day with a real fetched_at_utc,
    which is a legitimate same-day trend signal once at least two slots have
    run today -- no new data source is introduced.

    Returns CapeTendency.available=False (never a fabricated number) when
    fewer than two valid, distinctly-timed rows exist yet for date_str --
    this is expected and normal early in the day (only slot 0 has run).
    """
    df_sorted, n_candidates, n_valid = _valid_sorted(gfs_df, date_str=date_str)

    if n_valid < 2:
        return CapeTendency(
            value_jkgh=None, available=False,
            reason=f"only {n_valid} valid GFS row(s) so far today (need >= 2 distinct fetches)",
        )

    newer = df_sorted.iloc[0]
    older = df_sorted.iloc[1]

    t_new = newer["_fetched_at_utc_parsed"]
    t_old = older["_fetched_at_utc_parsed"]
    if pd.isna(t_new) or pd.isna(t_old):
        return CapeTendency(
            value_jkgh=None, available=False,
            reason="most recent two rows are missing a parsable fetched_at_utc",
        )

    dt_hours = (t_new - t_old).total_seconds() / 3600.0
    if dt_hours < min_dt_hours:
        return CapeTendency(
            value_jkgh=None, available=False,
            reason=f"two most recent rows are only {dt_hours*60:.0f} min apart (< {min_dt_hours*60:.0f} min minimum)",
            newer_fetched_at_utc=t_new, older_fetched_at_utc=t_old, dt_hours=dt_hours,
        )

    try:
        c_new = float(newer["CAPE"])
        c_old = float(older["CAPE"])
    except (TypeError, ValueError, KeyError):
        return CapeTendency(
            value_jkgh=None, available=False,
            reason="CAPE missing/non-numeric on one of the two most recent rows",
            newer_fetched_at_utc=t_new, older_fetched_at_utc=t_old, dt_hours=dt_hours,
        )

    tendency = round((c_new - c_old) / dt_hours, 1)
    return CapeTendency(
        value_jkgh=tendency, available=True,
        reason=f"computed from gfs_realtime_43295.csv rows fetched {t_old} -> {t_new} UTC",
        newer_fetched_at_utc=t_new, older_fetched_at_utc=t_old, dt_hours=dt_hours,
    )

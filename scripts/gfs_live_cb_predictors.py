#!/usr/bin/env python3
"""
scripts/gfs_live_cb_predictors.py
===================================
Phase 3 (2026-10-06 takeover pass): builds a LIVE, current-cycle
replacement for data/external/historical_gfs/phase20_full_predictor_dataset.csv
-- the long-format (cycle, lead, cell_id) GFS predictor table that
scripts/panindia_cb_features.engineer_daily_features() consumes to
produce the exact 34-column feature_cols panindia_cb_v1 was trained
on (models/panindia_cb_v1/panindia_cb_v1_feature_list.json).

This does NOT make cloudburst a lead-aware (2-6h) model -- that
remains explicitly future work (Phase 6 of the takeover plan). The CB
model is, and stays, a DAILY model: it was trained on one row per
(cycle, cell_id) built from 8 intra-day snapshots (f006..f027, see
panindia_cb_features.LEAD_ORDER). What this module changes is WHICH
day's data feeds that same daily model: the real current GFS cycle,
not the fixed 2024-08-01 validation cycle.

Source priority (per takeover Phase 3 instruction):
  1. PRIMARY: NOAA AWS Open Data GFS mirror (scripts/gfs_aws_source.py)
     -- free, public, no credentials, byte-range subset.
  2. SECONDARY FALLBACK: NOMADS filter CGI (server-side subsetting,
     one small request per lead) -- used only if AWS fails for a lead.
  3. If both are unavailable for a lead: that lead is marked MISSING
     for that cell -- never fabricated, never silently substituted
     with the historical 2024-08-01 row. The caller
     (scripts/phase34_build_unified_forecast.py) decides whether
     partial live data is usable or whether to fall back entirely to
     the fixed validation cycle.

Variable -> raw column mapping was derived by inspecting the real
Phase 19/20 historical extraction columns required by
panindia_cb_features.engineer_daily_features and verified against the
live NOAA .idx vocabulary (scripts/gfs_aws_source.py's REQUIRED_FIELDS
pattern), not invented.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import gfs_aws_source as aws_src  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# 8 intra-day snapshots the CB daily feature engineering requires
# (panindia_cb_features.LEAD_ORDER) -- NOT the 2-6h operational lead
# window; this is what the DAILY CB model was trained on.
CB_LEADS_HOURS = [6, 9, 12, 15, 18, 21, 24, 27]

# (variable, level) -> raw column name, matching Phase 19/20 schema
# exactly (panindia_cb_features.py's agg_spec / FEATURE_DOCS keys).
CB_FIELD_MAP = {
    ("CAPE", "surface"): "cape_sfc_jkg",
    ("CIN", "surface"): "cin_sfc_jkg",
    ("TMP", "2 m above ground"): "t2m_k",
    ("RH", "2 m above ground"): "rh2m_pct",
    ("SPFH", "2 m above ground"): "q2m_kgkg",
    ("DPT", "2 m above ground"): "td2m_k",
    ("PRES", "surface"): "sp_pa",
    ("PRMSL", "mean sea level"): "mslp_pa",
    ("PWAT", "entire atmosphere (considered as a single layer)"): "pwat_mm",
    ("UGRD", "850 mb"): "u850_ms", ("VGRD", "850 mb"): "v850_ms",
    ("UGRD", "500 mb"): "u500_ms", ("VGRD", "500 mb"): "v500_ms",
    ("UGRD", "200 mb"): "u200_ms", ("VGRD", "200 mb"): "v200_ms",
    ("HGT", "850 mb"): "gh850_gpm", ("HGT", "500 mb"): "gh500_gpm", ("HGT", "200 mb"): "gh200_gpm",
    ("APCP", "surface"): "target_day_precip_tp_mm",
    ("ACPCP", "surface"): "target_day_precip_acpcp_mm",
}
CB_REQUIRED_FIELDS = list(CB_FIELD_MAP.keys())

# cfgrib typeOfLevel/level filter groups needed to decode CB_FIELD_MAP
# without the merge conflicts seen when opening everything generically
# (see scripts/gfs_aws_source.py module docstring / Phase 3 testing).
_DECODE_GROUPS = [
    {"typeOfLevel": "surface", "stepType": "instant"},   # cape, cin, sp
    {"typeOfLevel": "meanSea"},                            # mslp
    {"typeOfLevel": "atmosphereSingleLayer"},              # pwat
    {"typeOfLevel": "heightAboveGround", "level": 2},      # t2m, d2m, r2, q2m... (RH/SPFH/TMP/DPT @2m)
    {"typeOfLevel": "isobaricInhPa", "level": 850},
    {"typeOfLevel": "isobaricInhPa", "level": 500},
    {"typeOfLevel": "isobaricInhPa", "level": 200},
    {"typeOfLevel": "surface", "stepType": "accum"},       # APCP, ACPCP
]

_VARNAME_TO_COL_BY_LEVELTYPE = {
    "cape": "cape_sfc_jkg", "cin": "cin_sfc_jkg", "sp": "sp_pa",
    "prmsl": "mslp_pa", "mslet": "mslp_pa",
    "pwat": "pwat_mm",
    "t2m": "t2m_k", "d2m": "td2m_k", "r2": "rh2m_pct", "sh2": "q2m_kgkg",
    "tp": "target_day_precip_tp_mm", "acpcp": "target_day_precip_acpcp_mm",
}


def _open_filtered(path, filter_keys):
    import xarray as xr
    try:
        return xr.open_dataset(str(path), engine="cfgrib",
                                backend_kwargs={"filter_by_keys": filter_keys, "indexpath": ""})
    except Exception as exc:
        log.warning(f"  decode group {filter_keys} failed: {exc}")
        return None


def _extract_cells_vectorized(ds, varname, cells: list[dict]) -> np.ndarray:
    """Nearest-neighbour extraction for ALL cells in one vectorised
    xarray call -- never re-opens or re-decodes the file per cell."""
    import xarray as xr
    lats = xr.DataArray([c["lat"] for c in cells], dims="cell")
    lons = xr.DataArray([c["lon"] % 360 for c in cells], dims="cell")
    try:
        vals = ds[varname].sel(latitude=lats, longitude=lons, method="nearest").values
        return np.asarray(vals, dtype=float)
    except Exception as exc:
        log.warning(f"  vectorised extraction of {varname} failed: {exc}")
        return np.full(len(cells), np.nan)


def _decode_subset_to_rows(grib_path: Path, cells: list[dict]) -> dict[str, np.ndarray]:
    """Opens the byte-range subset file once per decode group (handles
    the isobaricInhPa/heightAboveGround/surface merge conflicts cfgrib
    has when asked to merge everything at once) and extracts every
    required raw column, vectorised across all cells."""
    columns: dict[str, np.ndarray] = {}
    for group in _DECODE_GROUPS:
        ds = _open_filtered(grib_path, group)
        if ds is None:
            continue
        level = group.get("level")
        for varname in list(ds.data_vars):
            if group.get("typeOfLevel") == "isobaricInhPa":
                suffix = {850: "850", 500: "500", 200: "200"}.get(level)
                mapping = {"u": f"u{suffix}_ms", "v": f"v{suffix}_ms", "gh": f"gh{suffix}_gpm"}
                col = mapping.get(varname)
            else:
                col = _VARNAME_TO_COL_BY_LEVELTYPE.get(varname)
            if col is None:
                continue
            columns[col] = _extract_cells_vectorized(ds, varname, cells)
        try:
            ds.close()
        except Exception:
            pass
    return columns


def fetch_one_lead(cycle: "aws_src.CycleInfo", fhour: int, cells: list[dict],
                    out_dir: Path) -> tuple[Optional[dict[str, np.ndarray]], dict]:
    """Returns (columns_dict_or_None, meta). Tries AWS first, then
    NOMADS. columns_dict is None only if both sources failed to
    produce a readable file for this lead -- never a half-fabricated
    row."""
    meta = {"fhour": fhour, "source_used": None, "bytes": 0, "elapsed_s": None,
            "fields_found": 0, "fields_missing": []}
    t0 = time.time()

    out_path = out_dir / f"cb_subset_f{fhour:03d}.grib2"
    manifest = aws_src.fetch_cycle_subset(cycle, fhour, out_path, fields=CB_REQUIRED_FIELDS)
    if manifest["status"] in ("OK", "PARTIAL") and out_path.exists() and out_path.stat().st_size > 0:
        columns = _decode_subset_to_rows(out_path, cells)
        if columns:
            meta.update(source_used="LIVE_AWS_GFS", bytes=manifest["bytes_downloaded"],
                        fields_found=len(manifest["fields_found"]), fields_missing=manifest["fields_missing"],
                        elapsed_s=round(time.time() - t0, 1))
            out_path.unlink(missing_ok=True)
            return columns, meta
    out_path.unlink(missing_ok=True)
    log.warning(f"  AWS source failed for f{fhour:03d}, trying NOMADS fallback...")

    columns = _fetch_lead_via_nomads(cycle, fhour, cells, out_dir)
    if columns:
        meta.update(source_used="LIVE_NOMADS_GFS", elapsed_s=round(time.time() - t0, 1))
        return columns, meta

    meta.update(source_used="UNAVAILABLE", elapsed_s=round(time.time() - t0, 1))
    return None, meta


def _fetch_lead_via_nomads(cycle: "aws_src.CycleInfo", fhour: int, cells: list[dict],
                            out_dir: Path) -> Optional[dict[str, np.ndarray]]:
    """Secondary fallback: NOMADS's own filter CGI does server-side
    bbox+variable subsetting, so this is a single small request (no
    byte-range needed) -- used only when the AWS mirror fails."""
    NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
    fname = f"gfs.t{cycle.cycle_hour}z.pgrb2.0p25.f{fhour:03d}"
    params = (
        f"file={fname}"
        f"&lev_surface=on&lev_2_m_above_ground=on&lev_mean_sea_level=on"
        f"&lev_850_mb=on&lev_500_mb=on&lev_200_mb=on"
        f"&lev_entire_atmosphere_%28considered_as_a_single_layer%29=on"
        f"&var_CAPE=on&var_CIN=on&var_PWAT=on&var_TMP=on&var_RH=on&var_SPFH=on"
        f"&var_DPT=on&var_PRES=on&var_PRMSL=on&var_UGRD=on&var_VGRD=on&var_HGT=on"
        f"&var_APCP=on&var_ACPCP=on"
        f"&leftlon=68&rightlon=98&toplat=37&bottomlat=6"
        f"&dir=%2Fgfs.{cycle.date_str}%2F{cycle.cycle_hour}%2Fatmos"
    )
    url = f"{NOMADS_BASE}?{params}"
    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": "SIH-Hyperlocal-DRIFT/1.0"})
        if r.status_code != 200 or len(r.content) < 1000 or r.content[:4] == b"<htm":
            return None
        out_path = out_dir / f"cb_subset_nomads_f{fhour:03d}.grib2"
        out_path.write_bytes(r.content)
        columns = _decode_subset_to_rows(out_path, cells)
        out_path.unlink(missing_ok=True)
        return columns or None
    except Exception as exc:
        log.warning(f"  NOMADS fallback failed for f{fhour:03d}: {exc}")
        return None


CACHE_DIR = REPO_ROOT / "data" / "_gfs_live_cb_cache"


def _cache_paths(cycle_str: str) -> tuple[Path, Path]:
    return (CACHE_DIR / f"cb_longform_{cycle_str}.parquet",
            CACHE_DIR / f"cb_longform_{cycle_str}.meta.json")


def _load_cache(cycle_str: str) -> Optional[dict]:
    import json as _json
    parquet_path, meta_path = _cache_paths(cycle_str)
    if not (parquet_path.exists() and meta_path.exists()):
        return None
    try:
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        meta["dataframe"] = pd.read_parquet(parquet_path)
        meta["from_cache"] = True
        return meta
    except Exception as exc:
        log.warning(f"  cache read failed for cycle {cycle_str}, refetching: {exc}")
        return None


def _save_cache(cycle_str: str, result: dict) -> None:
    import json as _json
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path, meta_path = _cache_paths(cycle_str)
    df = result.get("dataframe")
    if df is None:
        return
    try:
        df.to_parquet(parquet_path)
        meta = {k: v for k, v in result.items() if k != "dataframe"}
        meta_path.write_text(_json.dumps(meta, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning(f"  cache write failed for cycle {cycle_str}: {exc}")


def build_live_cb_longform_table(cells: list[dict], out_dir: Optional[Path] = None,
                                  use_cache: bool = True) -> dict:
    """The main entry point. Returns a dict:
      {"status": "LIVE_AWS_GFS" | "LIVE_NOMADS_GFS" | "MIXED_LIVE_SOURCES" | "UNAVAILABLE",
       "cycle_str": ..., "init_time_utc": ..., "dataframe": pd.DataFrame or None,
       "per_lead_meta": [...], "n_cells": ..., "n_leads_live": ..., "total_bytes": ...,
       "elapsed_s": ...}
    dataframe is long-format: one row per (cell_id, lead), columns
    matching Phase 19/20's schema exactly, ready for
    panindia_cb_features.engineer_daily_features().

    use_cache=True (default) reuses a prior successful fetch for the
    SAME resolved cycle within this repo checkout (data/_gfs_live_cb_cache/)
    rather than re-downloading ~110MB / re-paying ~11 minutes per
    scheduled run that calls this more than once for the same cycle.
    The cache is keyed by the exact cycle string (e.g. "2026100600"), so
    a new GFS cycle (every 6h) always triggers a fresh fetch."""
    t0 = time.time()
    out_dir = out_dir or (REPO_ROOT / "data" / "_gfs_live_tmp")
    out_dir.mkdir(parents=True, exist_ok=True)

    cycle = aws_src.find_latest_available_cycle()
    if cycle is None:
        return {"status": "UNAVAILABLE", "reason": "no current cycle discoverable on AWS mirror",
                "dataframe": None, "per_lead_meta": [], "elapsed_s": round(time.time() - t0, 1)}

    if use_cache:
        cached = _load_cache(cycle.cycle_str)
        if cached is not None:
            cached["elapsed_s"] = round(time.time() - t0, 1)
            return cached

    init_time_utc = datetime.strptime(cycle.cycle_str, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    target_date = init_time_utc.strftime("%Y-%m-%d")

    rows = []
    per_lead_meta = []
    sources_used = set()
    total_bytes = 0
    for fhour in CB_LEADS_HOURS:
        columns, meta = fetch_one_lead(cycle, fhour, cells, out_dir)
        per_lead_meta.append(meta)
        total_bytes += meta.get("bytes", 0)
        if columns is None:
            continue
        sources_used.add(meta["source_used"])
        for i, cell in enumerate(cells):
            row = {"cycle": cycle.cycle_str, "cell_id": cell["cell_id"], "lat": cell["lat"], "lon": cell["lon"],
                   "lead": f"f{fhour:03d}", "target_date": target_date,
                   "gfs_format_vintage": "aws_s3_live", "label_status": "NOT_APPLICABLE_LIVE_INFERENCE",
                   "cb_label_daily": np.nan, "cb_label_source": "none (live inference, no historical label)",
                   "cb_label_temporal_resolution": "daily"}
            for col_name, arr in columns.items():
                row[col_name] = float(arr[i]) if i < len(arr) and not np.isnan(arr[i]) else np.nan
            rows.append(row)

    try:
        for f in out_dir.glob("*.grib2"):
            f.unlink()
    except Exception:
        pass

    if not rows:
        return {"status": "UNAVAILABLE", "reason": "no lead succeeded via AWS or NOMADS",
                "cycle_str": cycle.cycle_str, "init_time_utc": init_time_utc.isoformat(),
                "dataframe": None, "per_lead_meta": per_lead_meta, "elapsed_s": round(time.time() - t0, 1)}

    df = pd.DataFrame(rows)
    n_leads_live = len({m["fhour"] for m in per_lead_meta if m.get("source_used") not in (None, "UNAVAILABLE")})

    for var, out_name, u, v in [("wind_speed_850_ms", None, "u850_ms", "v850_ms"),
                                 ("wind_speed_500_ms", None, "u500_ms", "v500_ms"),
                                 ("wind_speed_200_ms", None, "u200_ms", "v200_ms")]:
        if u in df.columns and v in df.columns:
            df[var] = np.sqrt(df[u] ** 2 + df[v] ** 2)
    if all(c in df.columns for c in ("u200_ms", "u850_ms", "v200_ms", "v850_ms")):
        df["shear_mag_850_200_ms"] = np.sqrt((df["u200_ms"] - df["u850_ms"]) ** 2 +
                                              (df["v200_ms"] - df["v850_ms"]) ** 2)

    status = ("LIVE_AWS_GFS" if sources_used == {"LIVE_AWS_GFS"}
              else "LIVE_NOMADS_GFS" if sources_used == {"LIVE_NOMADS_GFS"}
              else "MIXED_LIVE_SOURCES" if sources_used else "UNAVAILABLE")

    result = {
        "status": status, "cycle_str": cycle.cycle_str, "init_time_utc": init_time_utc.isoformat(),
        "target_date": target_date, "dataframe": df, "per_lead_meta": per_lead_meta,
        "n_cells": len(cells), "n_leads_requested": len(CB_LEADS_HOURS), "n_leads_live": n_leads_live,
        "total_bytes": total_bytes, "elapsed_s": round(time.time() - t0, 1), "from_cache": False,
    }
    if use_cache and status != "UNAVAILABLE":
        _save_cache(cycle.cycle_str, result)
    return result


if __name__ == "__main__":
    import json as _json
    with open(REPO_ROOT / "data" / "pan_india_common_grid_992.json") as f:
        grid = _json.load(f)
    result = build_live_cb_longform_table(grid["cells"])
    df = result.pop("dataframe")
    print(_json.dumps(result, indent=2))
    if df is not None:
        print(f"dataframe shape: {df.shape}, unique cells: {df['cell_id'].nunique()}")

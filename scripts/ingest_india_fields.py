#!/usr/bin/env python3
"""
scripts/ingest_india_fields.py
==============================
Acquires the latest complete GFS 0.25 deg cycle (00Z/12Z) for India and extracts every field at every DRIFT land cell.

  python scripts/ingest_india_fields.py [--fhours 3,6,...,24] [--cycle 2026103100] [--out data/india_fields_latest.json]

Guarantees
  * all-or-nothing: if any forecast hour fails download or verification, NOTHING is published (the previous artifact is
    untouched). The artifact is written to a temp file and atomically renamed.
  * every GRIB message is cross-checked against the request (init time, step, level) by scripts/india_gfs.py
  * per-file provenance: S3 Last-Modified (publication time on the mirror), acquisition time, bytes, APCP windows
  * missing values are null, never 0; soil-moisture water-point sentinels are null
  * incremental: decoded hours are cached under cache/fields/<init>/ so a rerun does not re-download
Resolution: cell centres coincide with GFS grid points, so extraction is exact (no interpolation).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import india_gfs as g  # noqa: E402

GRID = ROOT / "data" / "india_grid_025.json"
DEFAULT_OUT = ROOT / "data" / "india_fields_latest.json"
SCHEMA = 1
FHOURS = list(range(3, 25, 3))

# registry: how each stored variable is defined. `kind` vocabulary: RAW_MODEL_OUTPUT, DERIVED_PHYSICAL, MODEL_DIAGNOSTIC
REGISTRY = {
    "cape": ("J/kg", "RAW_MODEL_OUTPUT", "GFS CAPE, surface-based parcel", 0),
    "cin": ("J/kg", "RAW_MODEL_OUTPUT", "GFS CIN, surface-based parcel (negative = inhibition)", 0),
    "lftx": ("K", "RAW_MODEL_OUTPUT", "GFS surface lifted index (negative = unstable)", 1),
    "pwat": ("mm", "RAW_MODEL_OUTPUT", "GFS precipitable water, entire atmosphere", 1),
    "t2m_c": ("degC", "RAW_MODEL_OUTPUT", "GFS 2 m temperature", 1),
    "d2m_c": ("degC", "RAW_MODEL_OUTPUT", "GFS 2 m dew point", 1),
    "rh2m": ("%", "RAW_MODEL_OUTPUT", "GFS 2 m relative humidity", 0),
    "ws10": ("m/s", "DERIVED_PHYSICAL", "sqrt(u10^2+v10^2) of GFS 10 m wind", 1),
    "wd10": ("deg", "DERIVED_PHYSICAL", "meteorological direction the 10 m wind blows FROM", 0),
    "mslp_hpa": ("hPa", "RAW_MODEL_OUTPUT", "GFS mean sea-level pressure (reduced; not station pressure)", 1),
    "k_index": ("degC", "DERIVED_PHYSICAL", "George K-index from GFS T and RH at 850/700/500 hPa", 1),
    "total_totals": ("degC", "DERIVED_PHYSICAL", "Total Totals from GFS T and RH at 850/500 hPa", 1),
    "lapse_850_500": ("K/km", "DERIVED_PHYSICAL", "(T850-T500)/3.5 km; approximate layer depth", 2),
    "shear_850_500": ("m/s", "DERIVED_PHYSICAL", "|V500-V850| bulk wind difference. NOT 0-6 km shear", 1),
    "w850": ("Pa/s", "RAW_MODEL_OUTPUT", "GFS vertical velocity 850 hPa (negative = ascent)", 2),
    "w700": ("Pa/s", "RAW_MODEL_OUTPUT", "GFS vertical velocity 700 hPa (negative = ascent)", 2),
    "w500": ("Pa/s", "RAW_MODEL_OUTPUT", "GFS vertical velocity 500 hPa (negative = ascent)", 2),
    "rh850": ("%", "RAW_MODEL_OUTPUT", "GFS RH 850 hPa", 0), "rh700": ("%", "RAW_MODEL_OUTPUT", "GFS RH 700 hPa", 0),
    "rh500": ("%", "RAW_MODEL_OUTPUT", "GFS RH 500 hPa", 0),
    "soilw0_10": ("m3/m3", "MODEL_DIAGNOSTIC", "GFS volumetric soil moisture 0-10 cm (land-surface model state, not observed)", 3),
    "apcp_cum": ("mm", "RAW_MODEL_OUTPUT", "GFS accumulated precipitation from init to valid time (window verified from GRIB: start step 0)", 1),
    "precip_3h": ("mm", "DERIVED_PHYSICAL", "apcp_cum(f) - apcp_cum(f-3); the first hour equals apcp_cum", 1),
}


def _round(a, nd):
    out = np.round(a.astype("float64"), nd)
    return [None if not np.isfinite(x) else (int(x) if nd == 0 else float(x)) for x in out]


def ingest(init: datetime, fhours, workdir: Path, log=print):
    grid = json.load(open(GRID))
    cells = grid["cells"]
    lat_c = np.array([c[1] for c in cells]); lon_c = np.array([c[2] for c in cells])
    files, fields, valid = [], {k: [] for k in REGISTRY}, []
    prev_cum = None
    for fh in fhours:
        part = workdir / f"{init:%Y%m%d%H}_f{fh:03d}.npz"
        meta_p = part.with_suffix(".json")
        if part.exists() and meta_p.exists():
            z = np.load(part); meta = json.load(open(meta_p)); data = {k: z[k] for k in z.files}
        else:
            gp = workdir / f"{init:%Y%m%d%H}_f{fh:03d}.grib2"
            prov = g.download_subset(init, fh, gp)
            try:
                dec = g.decode(gp, init, fh)
            finally:
                gp.unlink(missing_ok=True)
            der = g.derive(dec)
            la, lo = dec["lat"], dec["lon"]
            ii = np.rint((la[0] - lat_c) / 0.25).astype(int); jj = np.rint((lon_c - lo[0]) / 0.25).astype(int)
            if ii.min() < 0 or ii.max() >= len(la) or jj.min() < 0 or jj.max() >= len(lo):
                raise g.GfsError("grid cells fall outside the downloaded India box")
            if not (np.allclose(la[ii], lat_c) and np.allclose(lo[jj], lon_c)):
                raise g.GfsError("cell centres do not coincide with GFS grid points")
            ex = lambda a: a[ii, jj]
            f = dec["fields"]
            data = {k: ex(f[k]) for k in ("cape", "cin", "lftx", "pwat", "rh2m", "rh850", "rh700", "rh500", "w850", "w700", "w500", "soilw0_10")}
            for k in ("t2m_c", "d2m_c", "ws10", "wd10", "mslp_hpa", "k_index", "total_totals", "lapse_850_500", "shear_850_500"):
                data[k] = ex(der[k])
            wins = [(a["start"], a["end"]) for a in dec["apcp"]]
            cum = [a for a in dec["apcp"] if a["start"] == 0 and a["end"] == fh]
            if len(cum) != 1:
                raise g.GfsError(f"f{fh:03d}: expected exactly one 0-{fh} APCP window, GRIB states {wins}")
            data["apcp_cum"] = ex(cum[0]["values"])
            meta = {"fhour": fh, "valid_time_utc": (init + timedelta(hours=fh)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "published_at_utc": prov["published_at_utc"].strftime("%Y-%m-%dT%H:%M:%SZ") if prov["published_at_utc"] else None,
                    "acquired_at_utc": prov["acquired_at_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"), "bytes": prov["bytes"],
                    "url": prov["url"], "apcp_windows": wins, "messages_verified": True}
            workdir.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(part, **data); json.dump(meta, open(meta_p, "w"))
        files.append(meta); valid.append(meta["valid_time_utc"])
        cum = data["apcp_cum"]
        data["precip_3h"] = cum if prev_cum is None else np.maximum(cum - prev_cum, 0)
        prev_cum = cum
        for k, (_, _, _, nd) in REGISTRY.items():
            fields[k].append(_round(data[k], nd))
        log(f"  f{fh:03d} ok  published={meta['published_at_utc']}  {meta['bytes']/1e6:.1f} MB")
    return grid, files, fields


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fhours", default=",".join(map(str, FHOURS)))
    ap.add_argument("--cycle", help="YYYYMMDDHH; default = newest 00Z/12Z cycle whose last requested hour is published")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    fh = [int(x) for x in a.fhours.split(",")]
    init = datetime.strptime(a.cycle, "%Y%m%d%H").replace(tzinfo=timezone.utc) if a.cycle else g.latest_cycle(need_fhour=max(fh))
    if init is None:
        print("ERROR: no complete cycle found; nothing published"); return 2
    print("cycle", init.isoformat(), "fhours", fh)
    started = datetime.now(timezone.utc)
    try:
        grid, files, fields = ingest(init, fh, ROOT / "cache" / "fields" / f"{init:%Y%m%d%H}")
    except Exception as e:  # noqa: BLE001
        print("ERROR: ingestion failed, previous artifact left untouched:", e); return 1
    ids = [c[0] for c in grid["cells"]]
    doc = {
        "meta": {"schema": SCHEMA, "kind": "INDIA_GFS_FIELDS", "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "ingest_started_at_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "source": "NOAA GFS 0.25 deg, AWS Open Data mirror (noaa-gfs-bdp-pds), byte-range subset",
                 "cycle_init_utc": init.strftime("%Y-%m-%dT%H:%M:%SZ"), "fhours": fh, "n_cells": len(ids),
                 "grid_sha256": hashlib.sha256(open(GRID, "rb").read()).hexdigest()[:16],
                 "extraction": "exact: DRIFT cell centres coincide with GFS 0.25 deg grid points (no interpolation)",
                 "resolution_deg": 0.25, "files": files,
                 "availability_note": "published_at_utc is the S3 Last-Modified of the NWP file on the AWS mirror (observed). It is the "
                                      "availability of the model INPUT, not the time DRIFT published anything to a user."},
        "registry": {k: {"unit": u, "kind": kd, "definition": d, "decimals": nd} for k, (u, kd, d, nd) in REGISTRY.items()},
        "cells": ids, "fields": fields,
    }
    outp = Path(a.out); tmp = outp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")))
    # validate what we are about to publish before replacing the last good artifact
    chk = json.load(open(tmp))
    if len(chk["cells"]) != len(ids) or any(len(v) != len(fh) or any(len(h) != len(ids) for h in v) for v in chk["fields"].values()):
        tmp.unlink(); print("ERROR: artifact shape invalid; not published"); return 1
    os.replace(tmp, outp)
    print(f"wrote {outp}  {outp.stat().st_size/1e6:.1f} MB  cells={len(ids)} hours={len(fh)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

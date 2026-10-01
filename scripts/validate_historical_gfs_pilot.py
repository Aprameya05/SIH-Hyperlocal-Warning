#!/usr/bin/env python3
"""
validate_historical_gfs_pilot.py -- Phase 0.4.3/0.4.3A RESEARCH-ONLY validator.

Validates a locally-downloaded historical GFS (NCAR GDEX d084001) pilot
against:
  1. real GRIB2 message enumeration via ecCodes (NOT cfgrib.open_dataset on
     the whole file -- that fails with "multiple values for unique key" on a
     real multi-level-type GFS GRIB2, which is exactly what was hit in
     Phase 0.4.3's first run; this version enumerates messages one at a time)
  2. the real canonical 992-cell grid (data/pan_india_grid.json's actual
     "grid_cells" key -- the earlier version of this script looked for a
     "cells" key that does not exist in the real file, which is exactly why
     it reported CANONICAL_GRID_NOT_FOUND; fixed here, read-only)
  3. a real small Bengaluru-area pilot mapping using this project's own
     existing regrid.py::regrid_to_cell/cell_id_for utilities (reused, not
     reimplemented) against REAL extracted GFS field values -- no synthetic
     data anywhere in this script

This script does NOT fabricate results. If no pilot file is present, it
reports NO_PILOT_FILE_FOUND and exits cleanly. If a GRIB reader (ecCodes) is
unavailable, it reports GRIB_READER_UNAVAILABLE rather than hand-rolling a
parser. It never modifies backend/pipeline.py, forecast_action.py,
canonical_forecast_writer.py, the production grid file, or any schema.
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_DIR = Path("data/external/historical_gfs")
CANONICAL_GRID_PATH = Path("data/pan_india_grid.json")

# Variable groups this project's live pipeline cares about (gfs_row_select.py /
# forecast_action.py / canonical_forecast_writer.py), matched against real
# GRIB shortName/name substrings found in the actual files. This is matching
# logic only -- it does not invent a variable's presence.
VARIABLE_SEARCH = {
    "temperature_2m": (["2t"], "DIRECTLY_PRESENT"),
    "relative_humidity": (["2r", "r"], "DIRECTLY_PRESENT"),
    "specific_humidity": (["2sh", "q"], "DIRECTLY_PRESENT"),
    "surface_pressure": (["sp", "mslet", "prmsl"], "DIRECTLY_PRESENT"),
    "geopotential_height": (["gh"], "DIRECTLY_PRESENT"),
    "u_wind": (["u", "10u"], "DIRECTLY_PRESENT"),
    "v_wind": (["v", "10v"], "DIRECTLY_PRESENT"),
    "cape": (["cape"], "DIRECTLY_PRESENT"),
    "cin": (["cin"], "DIRECTLY_PRESENT"),
    "precipitable_water": (["pwat"], "DIRECTLY_PRESENT"),
    "precipitation_accum": (["tp", "apcp", "acpcp", "prate"], "DIRECTLY_PRESENT"),
    "wind_shear": ([], "DERIVABLE"),  # from u/v at two pressure levels, same method as live pipeline
}


def find_pilot_files(pilot_dir: Path):
    raw_dir = pilot_dir / "raw"
    if not raw_dir.exists():
        return []
    return sorted(raw_dir.glob("*.grib2"))


def try_import_eccodes():
    try:
        import eccodes  # noqa: F401
        return True
    except ImportError:
        return False


def enumerate_grib_messages(path: Path):
    """Enumerate every GRIB message's key metadata via raw ecCodes calls --
    never loads the whole multi-level-type file into one xarray Dataset."""
    import eccodes as ec

    msgs = []
    with open(path, "rb") as f:
        while True:
            gid = ec.codes_grib_new_from_file(f)
            if gid is None:
                break

            def g(key, default=None):
                try:
                    return ec.codes_get(gid, key)
                except Exception:  # noqa: BLE001
                    return default

            rec = {
                "shortName": g("shortName"),
                "name": g("name"),
                "typeOfLevel": g("typeOfLevel"),
                "level": g("level"),
                "units": g("units"),
                "dataDate": g("dataDate"),
                "dataTime": g("dataTime"),
                "forecastTime": g("forecastTime"),
                "stepUnits": g("stepUnits"),
                "validityDate": g("validityDate"),
                "validityTime": g("validityTime"),
                "Nx": g("Nx"),
                "Ny": g("Ny"),
                "latFirst": g("latitudeOfFirstGridPointInDegrees"),
                "lonFirst": g("longitudeOfFirstGridPointInDegrees"),
                "latLast": g("latitudeOfLastGridPointInDegrees"),
                "lonLast": g("longitudeOfLastGridPointInDegrees"),
                "dLat": g("jDirectionIncrementInDegrees"),
                "dLon": g("iDirectionIncrementInDegrees"),
                "_gid": gid,  # kept only for optional value extraction below; not JSON-serialized
            }
            msgs.append(rec)
            # release happens in caller after any value extraction, to allow
            # reuse of gid for codes_get_values on a chosen subset of messages
    return msgs


def extract_values_for_bbox(gid, lat_min, lat_max, lon_min, lon_max):
    """Pull real grid values for one GRIB message, restricted to a bbox, using
    ecCodes' own lat/lon/value arrays (real data, no synthetic fallback)."""
    import eccodes as ec
    import numpy as np

    lats = np.array(ec.codes_get_array(gid, "latitudes"))
    lons = np.array(ec.codes_get_array(gid, "longitudes"))
    values = np.array(ec.codes_get_array(gid, "values"))
    mask = (lats >= lat_min) & (lats <= lat_max) & (lons >= lon_min) & (lons <= lon_max)
    return lats[mask], lons[mask], values[mask]


def classify_variables(msgs):
    found = {}
    for label, (patterns, status) in VARIABLE_SEARCH.items():
        if not patterns:
            found[label] = {"status": status, "matches": []}
            continue
        matches = []
        for m in msgs:
            sn = (m["shortName"] or "")
            if sn in patterns:
                matches.append({"shortName": sn, "typeOfLevel": m["typeOfLevel"], "level": m["level"], "units": m["units"]})
        found[label] = {
            "status": status if matches else "NOT_FOUND",
            "matches": matches[:10],
            "n_matches": len(matches),
        }
    return found


def load_canonical_cells():
    """Loads the REAL production grid file, using its ACTUAL key
    ("grid_cells", confirmed by direct inspection this phase) -- not the
    "cells" key the earlier version of this script incorrectly assumed."""
    if not CANONICAL_GRID_PATH.exists():
        return None, None, None
    doc = json.loads(CANONICAL_GRID_PATH.read_text())
    cells = doc.get("grid_cells")
    bounds = doc.get("bounds")
    n_cells_field = doc.get("n_cells")
    return cells, bounds, n_cells_field


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help=f"Pilot directory (default: {DEFAULT_DIR})")
    ap.add_argument("--bbox", nargs=4, type=float, default=[10.0, 16.0, 74.0, 80.0],
                     metavar=("SOUTH", "NORTH", "WEST", "EAST"),
                     help="Bengaluru-area bbox for the small real pilot mapping (default: 10 16 74 80)")
    args = ap.parse_args()

    pilot_dir = Path(args.dir)
    files = find_pilot_files(pilot_dir)
    report = {"status": None, "pilot_dir": str(pilot_dir), "files_found": [str(f) for f in files]}

    if not files:
        report["status"] = "NO_PILOT_FILE_FOUND"
        print(json.dumps(report, indent=2))
        sys.exit(0)

    if not try_import_eccodes():
        report["status"] = "GRIB_READER_UNAVAILABLE"
        print(json.dumps(report, indent=2))
        print("\nInstall: pip install cfgrib eccodes")
        sys.exit(1)

    import eccodes as ec

    # --- Step 1: real message enumeration per file ---
    per_file = {}
    for f in files:
        msgs = enumerate_grib_messages(f)
        n = len(msgs)
        groups = sorted(set((m["shortName"], m["typeOfLevel"]) for m in msgs))
        first = msgs[0] if msgs else {}
        per_file[f.name] = {
            "n_messages": n,
            "n_unique_variable_level_groups": len(groups),
            "grid": {
                "Nx": first.get("Nx"), "Ny": first.get("Ny"),
                "lat_first": first.get("latFirst"), "lat_last": first.get("latLast"),
                "lon_first": first.get("lonFirst"), "lon_last": first.get("lonLast"),
                "d_lat": first.get("dLat"), "d_lon": first.get("dLon"),
            },
            "forecast_semantics": {
                "dataDate": first.get("dataDate"),
                "dataTime": first.get("dataTime"),
                "init_utc": f"{first.get('dataDate')} {first.get('dataTime'):04d}" if first.get("dataDate") else None,
                "forecastTime_hours": first.get("forecastTime"),
                "validityDate": first.get("validityDate"),
                "validityTime": first.get("validityTime"),
                "valid_utc": f"{first.get('validityDate')} {first.get('validityTime'):04d}" if first.get("validityDate") else None,
            },
            "variables": classify_variables(msgs),
            "_msgs": msgs,  # internal only, stripped before JSON dump below
        }

    # --- Step 2: canonical grid (fixed key) ---
    cells, bounds, n_cells_field = load_canonical_cells()
    if cells is None:
        report["status"] = "CANONICAL_GRID_NOT_FOUND"
        print(json.dumps({k: v for k, v in report.items()}, indent=2))
        sys.exit(0)

    try:
        from regrid import cell_id_for, regrid_to_cell
    except ImportError as e:
        report["status"] = "REGRID_MODULE_IMPORT_FAILED"
        report["error"] = str(e)
        print(json.dumps(report, indent=2))
        sys.exit(1)

    ids = [cell_id_for(c["lat"], c["lon"]) for c in cells]
    duplicates = len(ids) != len(set(ids))
    report["canonical_grid"] = {
        "source": str(CANONICAL_GRID_PATH),
        "key_used": "grid_cells",
        "cell_count": len(cells),
        "expected_992": len(cells) == 992,
        "duplicate_cell_ids": duplicates,
        "bounds": bounds,
    }

    # --- Step 3: real small Bengaluru-area pilot mapping ---
    s, n, w, e = args.bbox
    bengaluru_cells = [c for c in cells if s <= c["lat"] <= n and w <= c["lon"] <= e]
    report["pilot_bbox"] = {"south": s, "north": n, "west": w, "east": e, "n_canonical_cells_in_bbox": len(bengaluru_cells)}

    # Pick real messages to extract: 2t, cape, pwat, u/v at 850hPa (for shear),
    # from f003 file only (same mapping logic applies identically to f006).
    f003_name = next((f.name for f in files if ".f003." in f.name), None)
    mapping_rows = []
    if f003_name and bengaluru_cells:
        msgs = per_file[f003_name]["_msgs"]
        wanted = [
            ("2t", "heightAboveGround", 2),
            ("cape", "surface", 0),
            ("pwat", "atmosphereSingleLayer", 0),
            ("u", "isobaricInhPa", 850),
            ("v", "isobaricInhPa", 850),
        ]
        init = per_file[f003_name]["forecast_semantics"]["init_utc"]
        valid = per_file[f003_name]["forecast_semantics"]["valid_utc"]
        lead = per_file[f003_name]["forecast_semantics"]["forecastTime_hours"]

        for shortName, typeOfLevel, level in wanted:
            match = next((m for m in msgs if m["shortName"] == shortName and m["typeOfLevel"] == typeOfLevel and m["level"] == level), None)
            if match is None:
                mapping_rows.append({"variable": shortName, "level": level, "status": "NOT_FOUND_IN_FILE"})
                continue
            lats, lons, values = extract_values_for_bbox(match["_gid"], s, n, w, e + 2)
            # GRIB longitudes are 0-360; India (74-80E) needs no wraparound
            # adjustment here, but values array is indexed, not a dense grid,
            # so build a lookup for regrid_to_cell's expected 2D-indexable
            # interface via nearest-neighbor on the extracted point cloud.
            if len(lats) == 0:
                mapping_rows.append({"variable": shortName, "level": level, "status": "NO_POINTS_IN_BBOX"})
                continue
            import numpy as np
            uniq_lats = np.array(sorted(set(lats.tolist()), reverse=True))
            uniq_lons = np.array(sorted(set(lons.tolist())))
            grid_2d = np.full((len(uniq_lats), len(uniq_lons)), np.nan)
            lat_idx = {v: i for i, v in enumerate(uniq_lats)}
            lon_idx = {v: i for i, v in enumerate(uniq_lons)}
            for la, lo, val in zip(lats, lons, values):
                grid_2d[lat_idx[la], lon_idx[lo]] = val

            for cell in bengaluru_cells[:5]:  # small pilot: first 5 in-bbox cells only
                # regrid.py's SUPPORTED_SOURCES is a fixed tuple
                # ("gfs","himawari","dem","insat","imdaa"); passing anything
                # else (e.g. a made-up "gdex_d084001_<var>" string, as an
                # earlier version of this script did) silently hits
                # regrid_point's "unknown source" branch and returns
                # method_used="none"/available=False for every single cell --
                # that was a real bug in this script, not a missing-data
                # finding about the GFS archive. This data genuinely is GFS
                # (same model family as the live pipeline, per
                # docs/PHASE_0_4_2_HISTORICAL_GFS_FEASIBILITY.md), so
                # source_name="gfs" is the accurate value; the fact that it
                # comes from the NCAR GDEX d084001 *historical* archive rather
                # than a live fetch is preserved in the separate
                # "archive_source" field below, not smuggled into source_name.
                res = regrid_to_cell(
                    source_name="gfs",
                    source_lats=uniq_lats.tolist(),
                    source_lons=uniq_lons.tolist(),
                    source_values=grid_2d.tolist(),
                    target_lat=cell["lat"], target_lon=cell["lon"],
                    source_timestamp=valid,
                    source_resolution_deg=0.25,
                    target_resolution_deg=1.0,
                    method="nearest",
                )
                mapping_rows.append({
                    "variable": shortName, "level": level, "units": match["units"],
                    "archive_source": "NCAR_GDEX_d084001",
                    "target_cell_id": res.cell_id,
                    "target_lat": cell["lat"], "target_lon": cell["lon"],
                    "method_used": res.method_used,
                    "method_requested": res.method_requested,
                    "missing_reason": res.missing_reason,
                    "distance_deg": res.distance_deg,
                    "init_utc": init, "valid_utc": valid, "lead_hours": lead,
                    "value": res.value, "missing": res.missing_flag,
                })
        for gid_rec in msgs:
            pass  # gids released below in cleanup

    report["pilot_mapping_f003"] = mapping_rows

    # Strip internal gid handles before releasing + before JSON dump
    for fname, info in per_file.items():
        for m in info["_msgs"]:
            try:
                ec.codes_release(m["_gid"])
            except Exception:  # noqa: BLE001
                pass
        del info["_msgs"]

    report["grib_files"] = per_file
    report["status"] = "VALIDATED_FROM_ACTUAL_FILE"
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()

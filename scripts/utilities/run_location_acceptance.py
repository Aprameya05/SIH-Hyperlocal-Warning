#!/usr/bin/env python3
"""
National location acceptance run (writes docs/evaluation/location_acceptance_results.json and the table in
docs/NATIONAL_COVERAGE_AND_LOCATION_TESTS.md).

For each location it records: requested coordinates, resolved cell, the GFS source point, source timestamps, the actual returned
variables and the missing ones, hazard outputs with provenance, the API response, and an INDEPENDENT check: the same raw GRIB file
is re-downloaded and read with eccodes' own nearest-point search (`codes_grib_find_nearest`) -- no DRIFT indexing code -- and the
values must equal the artifact's values for the resolved cell. This is what proves that "the cell for Mumbai holds Mumbai's data".
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

NAMED = ["Bengaluru", "Mumbai", "Delhi", "Chennai", "Kolkata", "Hyderabad", "Pune", "Jaipur", "Ahmedabad", "Guwahati", "Shillong", "Srinagar", "Leh",
         "Dehradun", "Kochi", "Visakhapatnam", "Bhopal", "Lucknow", "Bhubaneswar", "Port Blair"]
POINTS = {"rural Rajasthan (Thar)": (26.1, 74.1), "rural Chhattisgarh forest": (20.5, 82.0), "rural Deccan plateau": (16.5, 77.0),
          "coastal Sundarbans edge": (22.0, 89.0), "coastal Konkan": (17.0, 73.4), "mountain Himalaya (Uttarakhand)": (30.5, 79.0),
          "mountain Western Ghats": (11.0, 76.5), "northeast hills (Arunachal)": (28.0, 94.5)}
# GRIB shortName (as eccodes reports it for the requested message) -> artifact variable
CHECK = {"cape": "cape", "2t": "t2m_c", "pwat": "pwat", "2r": "rh2m", "cin": "cin"}


def raw_nearest(grib: Path, points: dict) -> dict:
    """{label: {var: (value, nearest_lat, nearest_lon, distance_km)}} read ONLY with eccodes."""
    from eccodes import codes_get, codes_grib_find_nearest, codes_grib_new_from_file, codes_release
    out = {k: {} for k in points}
    with open(grib, "rb") as f:
        while True:
            g = codes_grib_new_from_file(f)
            if g is None:
                break
            try:
                sn = codes_get(g, "shortName")
                if sn in CHECK:
                    for label, (la, lo) in points.items():
                        n = codes_grib_find_nearest(g, la, lo)[0]
                        v = float(n.value)
                        v = v - 273.15 if sn == "2t" else v
                        out[label][CHECK[sn]] = (v, float(n.lat), float(n.lon) if n.lon <= 180 else float(n.lon), float(n.distance))
            finally:
                codes_release(g)
    return out


def run(hour_index: int = 3, with_raw: bool = True):
    from backend.location_service import Store
    from pipelines.ingestion import gfs_reader as g
    s = Store()
    assert s.fields_ok, s.fields_problems
    m = s.fields_doc["meta"]
    init = datetime.fromisoformat(m["cycle_init_utc"].replace("Z", "+00:00")).astimezone(timezone.utc)
    fh = m["fhours"][hour_index]
    locs = {}
    for n in NAMED:
        r = next((x for x in s.search(n, 5) if x["type"] == "place" and x["name"].lower() == n.lower()), None) or next((x for x in s.search(n, 5) if x["type"] == "place"), None)
        if r:
            locs[n] = (r["lat"], r["lon"], "gazetteer")
    for n, (la, lo) in POINTS.items():
        locs[n] = (la, lo, "map coordinate")
    raw = {}
    if with_raw:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "f.grib2"
            g.download_subset(init, fh, p)
            raw = raw_nearest(p, {k: (v[0], v[1]) for k, v in locs.items()})
    rows = []
    for name, (la, lo, kind) in locs.items():
        res = s.resolve(la, lo)
        row = {"location": name, "kind": kind, "requested": {"lat": la, "lon": lo}, "resolved": res}
        if not res["covered"]:
            row["status"] = "NOT_COVERED"; rows.append(row); continue
        rec = s.record(res["cell_id"], hour_index)
        row["cell"] = rec["location"]; row["forecast"] = {"model_init_utc": rec["cycle"]["model_init_utc"], "forecast_hour": fh, "valid_time_utc": rec["selected_hour"]["valid_time_utc"],
                                                          "published_at_utc": rec["selected_hour"]["published_at_utc"], "acquired_at_utc": rec["selected_hour"]["acquired_at_utc"],
                                                          "status": rec["selected_hour"]["status"]}
        vals = {k: v["value"] for k, v in rec["fields"].items()}
        row["returned_variables"] = {k: v for k, v in vals.items() if v is not None}
        row["missing_variables"] = [k for k, v in vals.items() if v is None]
        row["hazards"] = {"thunderstorm_model": rec["hazards"]["thunderstorm"]["model"].get("status"), "thunderstorm_indicators_cape": rec["hazards"]["thunderstorm"]["indicators"]["cape"],
                          "extreme_rainfall_short_duration": rec["hazards"]["extreme_rainfall"]["short_duration_extreme_rainfall"]["status"],
                          "cloudburst": rec["hazards"]["extreme_rainfall"]["cloudburst"]["status"], "flash_flood_prediction": rec["hazards"]["flash_flood"]["prediction"]["status"],
                          "flash_flood_screening_index": (rec["hazards"]["flash_flood"]["screening_index"] or {}).get("value")}
        row["static"] = {k: rec["static"].get(k) for k in ("elev_mean_m", "slope_p90_deg", "nearest_gauge_km")}
        if raw:
            checks = {}
            for var, (rv, nlat, nlon, dist) in raw[name].items():
                av = vals.get(var)
                checks[var] = {"raw_value_eccodes_nearest": round(rv, 3), "artifact_value": av, "raw_source_point": {"lat": nlat, "lon": nlon, "distance_km": round(dist, 2)},
                               "cell_centre": {"lat": rec["location"]["lat"], "lon": rec["location"]["lon"]},
                               "source_point_is_cell_centre": abs(nlat - rec["location"]["lat"]) < 1e-3 and abs(nlon - rec["location"]["lon"]) < 1e-3,
                               "equal": av is not None and abs(av - rv) <= max(0.51, 0.011 * abs(rv))}      # artifact rounding is 0-2 decimals
            row["independent_grib_check"] = checks
        rows.append(row)
    return {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "cycle": m["cycle_init_utc"], "forecast_hour": fh, "locations": rows}


def main():
    res = run()
    (ROOT / "docs" / "evaluation").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "evaluation" / "location_acceptance_results.json").write_text(json.dumps(res, indent=1))
    ok = [r for r in res["locations"] if r.get("independent_grib_check")]
    good = sum(1 for r in ok if all(c["equal"] and c["source_point_is_cell_centre"] for c in r["independent_grib_check"].values()))
    print(f"{len(res['locations'])} locations; {good}/{len(ok)} pass the independent GRIB check")
    md = ["# National coverage and location tests\n",
          f"Generated by `scripts/utilities/run_location_acceptance.py` against GFS cycle **{res['cycle']}**, forecast hour **f{res['forecast_hour']:02d}**. "
          "Each row: the requested coordinates, the resolved DRIFT cell and its GFS source point, the values the artifact holds for that cell and the values read "
          "**independently** from the same raw GRIB file with eccodes' own nearest-point search (no DRIFT indexing code).\n",
          "| Location | Requested | Cell (centre) | State / district | Valid UTC | CAPE art / raw | T2m C art / raw | PWAT art / raw | RH2m art / raw | source point = cell centre | all equal | missing vars |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["locations"]:
        if r.get("status") == "NOT_COVERED":
            md.append(f"| {r['location']} | {r['requested']['lat']}, {r['requested']['lon']} | NOT COVERED | | | | | | | | | |"); continue
        c = r["independent_grib_check"]; f = lambda k: f"{c[k]['artifact_value']} / {c[k]['raw_value_eccodes_nearest']}"
        md.append(f"| {r['location']} | {r['requested']['lat']}, {r['requested']['lon']} | `{r['cell']['cell_id']}` ({r['cell']['lat']}, {r['cell']['lon']}) | {r['cell']['state']} / {r['cell']['district']} | "
                  f"{r['forecast']['valid_time_utc'][5:16]} | {f('cape')} | {f('t2m_c')} | {f('pwat')} | {f('rh2m')} | "
                  f"{all(x['source_point_is_cell_centre'] for x in c.values())} | {all(x['equal'] for x in c.values())} | {', '.join(r['missing_variables']) or '-'} |")
    md.append(f"\n**{good} of {len(ok)} locations pass the independent source-coordinate and value check.**")
    (ROOT / "docs" / "NATIONAL_COVERAGE_AND_LOCATION_TESTS.md").write_text("\n".join(md) + "\n")


if __name__ == "__main__":
    main()

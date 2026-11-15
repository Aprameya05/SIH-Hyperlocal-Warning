#!/usr/bin/env python3
"""Generates docs/data_sources/NATIONAL_DATA_REPORT.md FROM THE ARTIFACTS (nothing typed by hand): source-to-output trace, coverage matrix,
a multi-region location acceptance table and the model evaluation tables."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.location_service import Store  # noqa: E402
from backend.coverage import coverage_matrix  # noqa: E402

PLACES = {"North": ["Delhi", "Srinagar", "Chandigarh"], "South": ["Bengaluru", "Chennai", "Thiruvananthapuram"], "East": ["Kolkata", "Bhubaneswar", "Patna"],
          "West": ["Mumbai", "Ahmedabad", "Jaisalmer"], "Central": ["Bhopal", "Nagpur", "Raipur"], "Northeast": ["Guwahati", "Imphal", "Shillong"]}
RURAL = {"Rajasthan desert": (26.0, 74.0), "Western Ghats": (11.0, 76.5), "Himalaya": (30.5, 79.0), "Chhattisgarh forest": (20.5, 82.0), "Sundarbans edge": (22.0, 89.0), "Deccan plateau": (16.5, 77.0)}


def main():
    s = Store(); now = datetime.now(timezone.utc); out = []
    w = out.append
    m = s.fields_doc["meta"]
    w("# DRIFT national data report\n")
    w(f"Generated {now:%Y-%m-%d %H:%M}Z from the committed artifacts by `scripts/utilities/generate_national_report.py`. GFS cycle **{m['cycle_init_utc']}**; "
      f"{len(s.cells)} India land cells at 0.25 deg; forecast hours {m['fhours']}.\n")
    w("## 1. Source-to-output trace\n")
    w("Every value in a location panel comes from one of the rows below. **Where**: the selected 0.25 deg cell (cell centre = one GFS grid point, exact extraction, no interpolation). "
      "**Times** (per forecast hour, in the artifact and the panel): model init, valid time, S3 publication time of the NWP file (observed), acquisition time. "
      "Statuses (CURRENT / PAST_VALID_TIME / STALE) are recomputed from these times and the clock, never read from the producer.\n")
    w("| Variable | Unit | Kind | Definition / source message | Resolution | Safe as current? |\n|---|---|---|---|---|---|")
    for k, r in s.fields_doc["registry"].items():
        w(f"| `{k}` | {r['unit']} | {r['kind']} | {r['definition']} | 0.25 deg GFS point | only while status = CURRENT |")
    w("\nStatic layers (not forecasts): elevation / relief / slope (AWS Terrain Tiles, SRTM-derived, ~1.2 km pixels, cell statistics); nearest INDOFLOODS gauge, its distance and recorded flood events "
      "(106 gauges, historical, nulled beyond 100 km). Daily heavy-rain-day outlook: `panindia_cb_v1`, **1 deg** cells, uncalibrated score. Model outputs: see section 4. "
      "None of the values is an observation; none except the model outputs is a probability; no heuristic is presented as one.\n")
    w("**Provenance of each artifact**\n")
    w("| Time | Per forecast hour |\n|---|---|")
    for f in m["files"]:
        w(f"| f{f['fhour']} valid {f['valid_time_utc']} | published (S3 Last-Modified) {f['published_at_utc']} · acquired {f['acquired_at_utc']} · APCP windows {f['apcp_windows']} |")
    w("\nOperational lead time: **unavailable** - DRIFT records no time at which a forecast was made available to a user; the S3 time is the availability of the model input only.\n")
    cov = coverage_matrix(s)
    w("## 2. Coverage matrix\n")
    w("| State / UT | cells | GFS fields | soil moisture (model) | terrain | gauge < 100 km | heavy-rain-day (1 deg) | TS model | short-duration rain model |\n|---|---|---|---|---|---|---|---|---|")
    for st, r in cov["by_state"].items():
        w(f"| {st} | {r['cells']} | {r['gfs_fields']} | {r['soil_moisture']} | {r['terrain']} | {r['gauge_within_100km']} | {r['heavy_rain_day_outlook']} | {r['ts_model']} | {r['short_duration_rain_model']} |")
    w("\n| Hazard | Inputs | Model / prediction status | Lead-specific | Validated for alerting |\n|---|---|---|---|---|")
    h = cov["hazards"]
    w(f"| Thunderstorm | {h['thunderstorm']['inputs']} | {h['thunderstorm']['model']} | {h['thunderstorm']['lead_specific']} | no |")
    w(f"| Extreme rainfall | {h['extreme_rainfall']['inputs']} | heavy-rain-day: {h['extreme_rainfall']['heavy_rain_day']}; short-duration heavy: {h['extreme_rainfall']['short_duration_heavy_rainfall']}; extreme short-duration: UNAVAILABLE; cloudburst: UNAVAILABLE | {h['extreme_rainfall']['lead_specific']} | no |")
    w(f"| Flash flood | {h['flash_flood']['inputs']} | prediction UNAVAILABLE; heuristic screening index (unvalidated) | no | no |")
    w("\nCells with no gauge within 100 km have no hydrological context; soil moisture is a GFS land-surface model state (missing over water pixels); wind shear is the 850-500 hPa bulk difference, not 0-6 km shear.\n")
    w("## 3. Location acceptance table (different locations -> different underlying data)\n")
    hr = s.default_hour(now); w(f"Selected forecast hour f{s.fields_doc['meta']['files'][hr]['fhour']} (valid {s.fields_doc['meta']['files'][hr]['valid_time_utc']}), clock {now:%H:%M}Z.\n")
    w("| Region | Location | Cell | State / district | Elev m | CAPE | CIN | PWAT mm | T2m C | RH % | K-index | Shear m/s | Rain 3h mm | Soil m3/m3 | TS p (+6h) | FF screen |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    rows = []
    for reg, names in PLACES.items():
        for n in names:
            r = next((x for x in s.search(n, 5) if x["type"] == "place"), None)
            if r:
                rows.append((reg, n, r["cell_id"]))
    for n, (la, lo) in RURAL.items():
        r = s.resolve(la, lo)
        if r["covered"]:
            rows.append(("rural", n, r["cell_id"]))
    for reg, n, cid in rows:
        rec = s.record(cid, hr, now); f = rec["fields"]; st = rec["static"]; L = rec["location"]
        ts = rec["hazards"]["thunderstorm"]["model"]; p6 = ts["leads"]["6"]["value"] if ts.get("status") == "AVAILABLE_EVALUATED" and "6" in ts["leads"] else None
        ff = rec["hazards"]["flash_flood"]["screening_index"]
        v = lambda k, d=1: "n/a" if f[k]["value"] is None else f"{f[k]['value']:.{d}f}"
        w(f"| {reg} | {n} | `{cid}` | {L['state']} / {L['district']} | {st['elev_mean_m']} | {v('cape', 0)} | {v('cin', 0)} | {v('pwat')} | {v('t2m_c')} | {v('rh2m', 0)} | {v('k_index')} | {v('shear_850_500')} | {v('precip_3h')} | {v('soilw0_10', 3)} | {'n/a' if p6 is None else format(p6, '.3f')} | {'n/a' if not ff else ff['value']} |")
    w("\n## 4. Model evaluation (reproducible: `pipelines/feature_engineering/build_thunderstorm_training_dataset.py` -> `models/thunderstorm/train_and_evaluate_thunderstorm_model.py`; `pipelines/feature_engineering/build_rainfall_training_dataset.py` -> `models/extreme_rainfall/train_and_evaluate_extreme_rainfall_model.py`)\n")
    for name, key, title in (("ts_panindia_evaluation.json", "leads", "Thunderstorm (pan-India, lead-specific)"), ("rain_panindia_evaluation.json", "targets", "Short-duration heavy rainfall (3 h, CMORPH-labelled)")):
        ev = json.load(open(ROOT / "data" / name)); w(f"### {title}\n")
        w(f"Data: {json.dumps(ev['data'])}\n")
        w(f"Validation: {json.dumps(ev.get('validation', ev.get('target')))}\n")
        w("| Target | n | events | AUROC | PR-AUC (no skill) | Brier | Brier skill vs clim. | POD | FAR | CSI | HSS | leave-region-out AUROC / BSS | gate |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for k, r in ev[key].items():
            if "A_temporal_holdout" not in r:
                w(f"| {k} | - | - | insufficient events | | | | | | | | | withheld |"); continue
            x = r["A_temporal_holdout"]["xgboost_calibrated"]; b = r["B_pooled"]
            w(f"| {k} | {x['n']} | {x['n_events']} | {x['auroc']} | {x['pr_auc']} ({x['pr_auc_no_skill']}) | {x['brier']} | {x['bss_vs_climatology']} | {x['pod']} | {x['far']} | {x['csi']} | {x['hss']} | {b.get('auroc')} / {b.get('bss_vs_climatology')} | {'PUBLISHED' if r['passes_gate'] else 'WITHHELD'} |")
        w(f"\nLimits: {'; '.join(ev['limits'])}\n")
        w("Reliability (first published target, 2025 holdout; mean forecast vs observed frequency): " + json.dumps(next((r["A_temporal_holdout"]["reliability_xgboost"] for r in ev[key].values() if "A_temporal_holdout" in r and r["passes_gate"]), [])) + "\n")
    w("## 5. What is not available (and what it needs)\n")
    w("- **Cloudburst / sub-hourly extreme rainfall**: needs observed >=100 mm/h events and sub-hourly rainfall at ~10 km (e.g. dense AWS/ARG gauge or radar archives). CMORPH 0.25 deg / 3 h cannot verify them.\n"
      "- **Flash-flood prediction**: needs time-stamped observed flash-flood events (location, onset), observed soil moisture and live river levels, drainage/catchment hydrology beyond 106 gauges, then a validated model.\n"
      "- **Operational 2-6 h warnings**: needs a recorded publication time for DRIFT's own forecasts and an approved alert policy (registries are empty by decision).\n"
      "- **0-6 km wind shear, observed radar/satellite grid fields, per-cell observations**: not integrated.\n")
    (ROOT / "docs" / "data_sources" / "NATIONAL_DATA_REPORT.md").write_text("\n".join(out))
    print("wrote docs/data_sources/NATIONAL_DATA_REPORT.md", len(rows), "locations")


if __name__ == "__main__":
    main()

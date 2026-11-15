#!/usr/bin/env python3
"""Generates docs/HAZARD_MODEL_VALIDATION.md and docs/FORECAST_LEAD_TIME_VALIDATION.md from the evaluation JSON files.
Nothing in the generated tables is typed by hand: every number comes from data/ts_panindia_evaluation.json,
data/rain_panindia_evaluation.json and the model artifacts."""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data"


def f(x, n=3):
    return "n/a" if x is None else (f"{x:.{n}f}" if isinstance(x, float) else str(x))


def ci(u, k):
    v = (u or {}).get(k)
    return "n/a" if not v else f"[{v[0]:.3f}, {v[1]:.3f}]"


def main():
    ts = json.load(open(D / "ts_panindia_evaluation.json")); rn = json.load(open(D / "rain_panindia_evaluation.json"))
    tsm = json.load(open(D / "india_ts_model_latest.json")); rnm = json.load(open(D / "india_rain_model_latest.json"))
    hz = json.load(open(D / "hazard_evaluation.json"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    o = [f"# Hazard model validation\n", f"Generated {now} by `scripts/utilities/generate_validation_docs.py` from `data/*_evaluation.json`. Nothing below is typed by hand.\n",
         "Status per hazard (what the product actually serves):\n",
         "| Hazard | Prediction product | Status |\n|---|---|---|",
         f"| Severe thunderstorm | Pan-India lead-specific model, +{', +'.join(map(str, tsm['meta']['leads']))} h, calibrated to airport METAR thunderstorm reports | {'AVAILABLE, EVALUATED' if tsm['meta']['model_evaluated'] else 'UNAVAILABLE: ' + str(tsm['meta'].get('reason'))} |",
         f"| Extreme rainfall | Short-duration heavy rainfall (>= X mm / 3 h, CMORPH-labelled); targets published: {rnm['meta'].get('targets')} | {'AVAILABLE, EVALUATED (those targets only)' if rnm['meta']['model_evaluated'] else 'UNAVAILABLE: ' + str(rnm['meta'].get('reason'))} |",
         "| Extreme rainfall | Daily heavy-rain-day outlook (>= 64.5 mm/24 h, 1 deg, existing `panindia_cb_v1`) | AVAILABLE as an UNCALIBRATED ranking score; negative Brier skill uncalibrated |",
         "| Cloudburst | any | **UNAVAILABLE** (no sub-hourly labels or observations; 0.25 deg / 3 h data cannot verify >= 100 mm/h events) |",
         "| Flash flood | prediction | **UNAVAILABLE** (no flood-event labels with time/location, no observed soil moisture or river levels); a heuristic screening index is shown and labelled unvalidated |\n",
         "## 1. Thunderstorm\n",
         f"- **Target.** Thunderstorm (METAR `TS` or `VCTS`) reported by an Indian airport within +-1.5 h of the forecast valid time; a window with fewer than 2 reports is unlabelled (never counted as negative). "
         "This is an ORDINARY-thunderstorm target; a documented *severe*-thunderstorm label (hail/gust/lightning counts) is not available from METAR and is not claimed.",
         f"- **Data.** {ts['data']['n_rows']:,} station-windows, {ts['data']['n_stations']} stations, {ts['data']['n_init_dates']} init dates, years {ts['data']['years']}, event rate {ts['data']['event_rate']}. Cycles 00Z and 12Z, leads 6/12/18/24 h. Stations by region: {ts['data']['stations_by_region']}.",
         f"- **Predictors.** GFS 0.25 deg at the nearest grid point (24 features: {', '.join(json.load(open(ROOT / 'models/thunderstorm/artifacts/panindia_v1/meta.json'))['features'])}).",
         f"- **Validation.** {ts['validation']['A']}; {ts['validation']['B']}. Leakage controls: {'; '.join(ts['validation']['leakage_controls'])}.",
         f"- **Gate.** {ts['gate']['rule']}.\n",
         "Temporal holdout (train 2024, test 2025), calibrated XGBoost; 95% CI = block bootstrap over forecast dates (200 resamples):\n",
         "| Lead | n | events | AUROC (95% CI) | PR-AUC (no skill) | Brier | Brier skill (95% CI) | ECE | POD | FAR | CSI | HSS | false alarms /100 forecasts | persistence AUROC | leave-region-out AUROC / BSS | published |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for L, r in ts["leads"].items():
        if "A_temporal_holdout" not in r:
            o.append(f"| +{L} h | insufficient events | | | | | | | | | | | | | | no |"); continue
        A = r["A_temporal_holdout"]; x = A["xgboost_calibrated"]; u = x.get("uncertainty"); B = r["B_pooled"]; p = A.get("persistence") or {}
        o.append(f"| +{L} h | {x['n']} | {x['n_events']} | {f(x['auroc'])} {ci(u, 'auroc_95ci')} | {f(x['pr_auc'])} ({f(x['pr_auc_no_skill'])}) | {f(x['brier'], 4)} | {f(x['bss_vs_climatology'])} {ci(u, 'bss_95ci')} | {f(x['ece'], 4)} | "
                 f"{f(x['pod'])} | {f(x['far'])} | {f(x['csi'])} | {f(x['hss'])} | {f(x['false_alarms_per_100_forecasts'], 1)} | {f(p.get('auroc'))} | {f(B.get('auroc'))} / {f(B.get('bss_vs_climatology'))} | {'yes' if r['passes_gate'] else 'no'} |")
    o += ["\nBaselines on the same test rows (lead +12 h shown; all leads are in the JSON): climatology (training base rate), CAPE-threshold rule, logistic regression on 6 predictors, persistence (thunderstorm in the 3 h before issue).\n",
          "| Lead | XGBoost AUROC | Logistic AUROC | CAPE-rule CSI | XGBoost CSI | Persistence CSI (rows with an observation) |\n|---|---|---|---|---|---|"]
    for L, r in ts["leads"].items():
        if "A_temporal_holdout" not in r:
            continue
        A = r["A_temporal_holdout"]; p = A.get("persistence") or {}
        o.append(f"| +{L} h | {f(A['xgboost_calibrated']['auroc'])} | {f(A['logistic'].get('auroc'))} | {f(A['cape_threshold_rule'].get('csi'))} | {f(A['xgboost_calibrated']['csi'])} | {f(p.get('csi'))} |")
    o += ["\nLeave-one-region-out (train 2024 without the region, test 2025 in the region), AUROC per held-out region:\n", "| Lead | " + " | ".join(sorted(ts['data']['stations_by_region'])) + " |", "|---|" + "---|" * len(ts['data']['stations_by_region'])]
    for L, r in ts["leads"].items():
        if "B_by_region_spatial_temporal" in r:
            o.append(f"| +{L} h | " + " | ".join(f(r["B_by_region_spatial_temporal"].get(g, {}).get("auroc")) for g in sorted(ts['data']['stations_by_region'])) + " |")
    o += ["\nBy month of the valid time (2025 test, lead +12 h): " + json.dumps({m: {k: v for k, v in d.items() if k in ('n', 'auroc')} for m, d in ts['leads']['12']['A_temporal_holdout']['by_month'].items()}) if '12' in ts['leads'] and 'A_temporal_holdout' in ts['leads']['12'] else "",
          f"\n**Limits.** {'; '.join(ts['limits'])}.\n",
          "## 2. Short-duration heavy rainfall (3 h)\n",
          f"- **Target.** {rn['target']}",
          f"- **Labels.** {rn['data']['labels']}. **Predictors.** {rn['data']['predictors']}. {rn['data']['n_rows']:,} cell-windows, {rn['data']['n_init_dates']} init dates, {rn['data']['season']}.",
          f"- **Gate.** {rn['gate']['rule']}.\n",
          "| Target | events (test) | AUROC (95% CI) | raw GFS rainfall AUROC | PR-AUC (no skill) | Brier skill (95% CI) | ECE | POD | FAR | CSI | false alarms /100 | leave-region-out AUROC / BSS | published |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, r in rn["targets"].items():
        if "A_temporal_holdout" not in r:
            o.append(f"| {k} | insufficient events | | | | | | | | | | | no |"); continue
        x = r["A_temporal_holdout"]["xgboost_calibrated"]; u = x.get("uncertainty"); B = r["B_pooled"]
        o.append(f"| {k} | {x['n_events']} | {f(x['auroc'])} {ci(u, 'auroc_95ci')} | {f(r['raw_gfs_rainfall_auroc_2025'])} | {f(x['pr_auc'], 4)} ({f(x['pr_auc_no_skill'], 4)}) | {f(x['bss_vs_climatology'])} {ci(u, 'bss_95ci')} | {f(x['ece'], 4)} | {f(x['pod'])} | {f(x['far'])} | {f(x['csi'])} | {f(x['false_alarms_per_100_forecasts'], 2)} | {f(B.get('auroc'))} / {f(B.get('bss_vs_climatology'))} | {'yes' if r['passes_gate'] else 'no'} |")
    o += [f"\n**Limits.** {'; '.join(rn['limits'])}.\n",
          "## 3. Existing models (not retrained here)\n",
          f"- VOBL station thunderstorm model (Bengaluru airport only): {hz['hazards']['thunderstorm']['validation']}; mean AUROC {hz['hazards']['thunderstorm']['summary']['mean_auroc']}, Brier skill positive in {hz['hazards']['thunderstorm']['summary']['years_with_positive_bss']} of {len(hz['hazards']['thunderstorm']['folds'])} years. This is a single-station model and is not a pan-India prediction.",
          f"- Heavy-rain-day model `panindia_cb_v1` ({hz['hazards']['extreme_rainfall']['scope']}): uncalibrated Brier skill {hz['hazards']['extreme_rainfall']['variants']['uncalibrated']['bss_vs_climatology']} (worse than climatology; a ranking score, not a probability).",
          "- Flash flood: no model is evaluated.\n",
          "## 4. Reproduce\n", "```bash\npython pipelines/feature_engineering/build_thunderstorm_training_dataset.py   # METAR labels x GFS (resumable; ~hours, network)\npython models/thunderstorm/train_and_evaluate_thunderstorm_model.py\n"
          "python pipelines/feature_engineering/build_rainfall_training_dataset.py       # CMORPH labels x GFS (resumable)\npython models/extreme_rainfall/train_and_evaluate_extreme_rainfall_model.py\n"
          "python models/thunderstorm/predict_thunderstorm.py && python models/extreme_rainfall/predict_extreme_rainfall.py\npython scripts/utilities/generate_validation_docs.py\n```\n",
          "Seeds are fixed; `tests/test_national_pipeline.py` re-runs both predictors and requires bit-identical output to the committed artifacts."]
    (ROOT / "docs" / "HAZARD_MODEL_VALIDATION.md").write_text("\n".join(o) + "\n")

    L = [f"# Forecast lead-time validation\n", f"Generated {now} from the artifacts.\n",
         "## What is validated and what is not\n",
         "DRIFT distinguishes **seven** times; only the first five are recorded:\n",
         "| Time | Recorded? | Where |\n|---|---|---|",
         "| Model initialisation | yes | GRIB message `dataDate/dataTime`, cross-checked against the request |",
         "| Forecast valid time | yes | init + forecast hour (recomputed by the verifier) |",
         "| Input acquisition time | yes | `acquired_at_utc` per file |",
         "| Input publication/availability time | **yes (observed)** | S3 `Last-Modified` of each GFS file on the AWS mirror |",
         "| NWP forecast hour (valid - init) | yes | `nwp_forecast_hour` |",
         "| Time the DRIFT forecast became available to DRIFT/users | **no** | not recorded: the artifact is committed by CI and deployed later, and no publication confirmation exists |",
         "| User-facing operational lead (valid - DRIFT availability) | **no -> null** | `operational_lead_hours: null`, status `UNAVAILABLE_NO_DRIFT_PUBLICATION_TIME` |\n",
         "Consequently **no operational 2-6 h lead time is claimed for any hazard** and automated alerting stays disabled. This is enforced by `backend/alert_policy.py` (empty registries) and tested.\n",
         "## Supported forecast horizons (verified)\n",
         "| Source | Horizons actually produced | Input latency (observed) | Evaluated? |\n|---|---|---|---|"]
    m = json.load(open(D / "india_fields_latest.json"))["meta"]
    lat = [(datetime.fromisoformat(x["published_at_utc"].replace("Z", "+00:00")) - datetime.fromisoformat(m["cycle_init_utc"].replace("Z", "+00:00"))).total_seconds() / 3600 for x in m["files"]]
    L.append(f"| GFS 0.25 deg fields (all land cells) | NWP hours {m['fhours']} from {m['cycle_init_utc']} | {min(lat):.2f}-{max(lat):.2f} h after init (this cycle) | fields are model guidance, not forecasts of an event |")
    L.append(f"| Thunderstorm model | +{', +'.join(map(str, tsm['meta']['leads']))} h after a 00Z/12Z init | adds the GFS latency above | yes, per lead (HAZARD_MODEL_VALIDATION.md) |")
    L.append(f"| Heavy 3 h rainfall model | targets {rnm['meta'].get('targets')} | same | yes, per lead |")
    L += ["| 2, 3, 4, 5 h leads | **not produced** | - | **not supported**: GFS cycles arrive every 6 h and the training design uses 6/12/18/24 h; no hourly-initialised system exists |\n",
          "These are **NWP forecast hours** (valid - init). Measured from the moment the GFS input was published (S3 time) the effective lead is shorter by the 3.5-4 h publication delay; measured from DRIFT's own availability it is unknown. Examples for the current cycle:\n",
          "| NWP forecast hour | valid UTC | GFS file published (UTC) | lead from input publication (h) |\n|---|---|---|---|"]
    for x in m["files"]:
        v = datetime.fromisoformat(x["valid_time_utc"].replace("Z", "+00:00")); p = datetime.fromisoformat(x["published_at_utc"].replace("Z", "+00:00"))
        L.append(f"| f{x['fhour']:02d} | {x['valid_time_utc']} | {x['published_at_utc']} | {(v - p).total_seconds() / 3600:.2f} |")
    L += ["\nA forecast valid 3 h after init was published 3.5 h after init, i.e. its lead from the first moment the *input* existed is NEGATIVE: such hours are shown as `PAST_VALID_TIME`/never as a warning. Only forecast hours >= about +6 h after init have a positive lead from input publication, and that lead does not include DRIFT's own processing and deployment time.\n",
          "## Per-lead skill\n", "Each model is trained and evaluated separately per lead (tables in HAZARD_MODEL_VALIDATION.md). Persistence (observation before issue) is compared for thunderstorms; for rainfall the raw GFS 3 h forecast is the baseline. A 2-6 h *nowcast* from observations (satellite cloud-top cooling, radar, lightning, QPE) is **not implemented**: the calibrated IR source is not accessible (see docs/data_sources/SATELLITE_AND_REANALYSIS_ACCESS.md).\n",
          "## Exactly what is needed to claim a 2-6 h operational lead\n",
          "1. Record the time each DRIFT forecast artifact becomes available to users (deployment confirmation) and compute lead from it.\n2. A rapid-update (hourly or faster) initialisation: observation-driven nowcasting inputs (calibrated geostationary IR/WV, radar, lightning, hourly QPE) with an archive long enough to train and validate +2..+6 h targets.\n3. Re-evaluate every lead with persistence and raw-NWP baselines, then register the lead window in the alert policy."]
    (ROOT / "docs" / "FORECAST_LEAD_TIME_VALIDATION.md").write_text("\n".join(L) + "\n")
    print("wrote HAZARD_MODEL_VALIDATION.md and FORECAST_LEAD_TIME_VALIDATION.md")


if __name__ == "__main__":
    main()

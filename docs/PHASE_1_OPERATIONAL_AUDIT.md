# Phase 1 — Operational Audit (read-only) — 2026-09-30

No file was changed to produce this report. Every claim below was checked directly against the code, the live data files, and (for the INDOFLOODS question) the actual connected PC repo folder — not assumed from prior passes' documentation.

---

## A. What is actually live right now

- **GFS 0.25° forecast data**: real, fetched by `gfs_fetcher.py` (VOBL point) and `pan_india_gfs_fetcher.py` (pan-India grid) from NOAA NOMADS, on a real schedule (`forecast_update.yml`).
- **Himawari-9**: real when reachable — `fetch_himawari_realtime.py` pulls Band 13 from NOAA S3 with a JAXA HTTP fallback. Confirmed genuinely honest on total failure: it writes an explicit placeholder (`vobl_bt_celsius: null`, `data_source: "Himawari-9 — UNAVAILABLE (S3/JAXA unreachable)"`), never a fabricated brightness temperature.
- **METAR**: real, `fetch_metar.py`, VOBL station only, with an explicit fallback path on empty response.
- **SRTM terrain**: real, `fetch_dem_terrain.py`, but **Bengaluru-region only by construction** (its own log line: `"Generating terrain data for BLR region"`) — not pan-India.
- **VOBL XGBoost model**: real, trained, versioned (`v6_temporal`), the one genuinely validated ML path in this repo.
- **Pan-India physics/proxy hazard engine**: real and running (`backend/pipeline.py::hazard_probabilities()`), but it is a **hand-weighted linear scoring formula, not a trained or calibrated model** — every coefficient (e.g. CAPE weighted 0.30, K-index 0.22, totals-totals 0.18 for thunderstorm; PWAT 0.40, CAPE 0.25, CTT 0.20 for cloudburst) is a fixed constant with no documented derivation and no calibration test anywhere in the repo.
- **Cloudflare Pages deployment**: real, `forecast_update.yml`'s final step deploys via `wrangler pages deploy` on every successful run.
- **Alert delivery, WhatsApp path**: real and already truthful — `alert_delivery.py` implements exactly four explicit states (`SUCCESS`, `FAILED`, `NOT_CONFIGURED`, `SKIPPED_NO_ALERT`), replacing an earlier boolean-collapsing bug. This already matches the standard requested for Phase 7.
- **Alert delivery, SMS path**: a **second, separate, older system** (`backend/dispatch_alerts.py`, Twilio, pan-India grid-triggered). It distinguishes sent/failed/no-recipients/no-credentials by print statements and a `tuple[bool,str]` return, but does **not** persist a structured delivery-status artifact and does **not** use the same four-state vocabulary as `alert_delivery.py`. Two alert systems, two different truthfulness models.
- **Deterministic GFS row selection**: real and already fixed — `gfs_row_select.py` replaced an earlier `iloc[0]`-on-unsorted-CSV bug with an explicit `fetched_at_utc`-based selector. Verified this actually is used everywhere it matters: `forecast_action.py` and `compute_realtime_shap.py` both call `select_latest_gfs`/`latest_gfs_frame` before any `.iloc[0]` access, so the several remaining `gfs_df.iloc[0]` call sites in `forecast_action.py` are safe by construction (they operate on the already-reduced 1-row frame, not the raw multi-row CSV).

## B. What runs on every forecast cycle

`forecast_update.yml` (4x/day + 1 extra dashboard refresh): `clean_stale_data.py` → `gfs_fetcher.py` → `pan_india_gfs_fetcher.py` → `fetch_himawari_realtime.py` → `fetch_dem_terrain.py` (BLR only) → `fetch_metar.py` → `forecast_action.py` → `compute_realtime_shap.py` → `verify_today.py` → `populate_skill_scores.py` → `generate_alert_log.py` → `send_alerts.py` (WhatsApp) → ntfy push (best-effort) → commit/push → Cloudflare deploy.

`update_grid.yml` (separately, 4x/day, different schedule): `backend/pipeline.py` (writes `data/pan_india_grid.json` + `data/ctt_grid.json`, the pan-India physics-proxy grid) → `backend/dispatch_alerts.py` (Twilio SMS).

**These are two independent pipelines on two independent schedules, writing two independent artifacts** (`forecast.json` — VOBL station document — vs. `pan_india_grid.json` — pan-India cell array), each with its own alert system. They are not currently unified into one flow, which is exactly the gap Phase 2 (canonical schema) is meant to close.

## C. What is static

- Bengaluru SRTM terrain (`data/blr_terrain.json`) — regenerated only when `fetch_dem_terrain.py::should_regenerate()` decides it's needed, not every cycle.
- The 12 `*_training_baseline.npy` climatology files — fixed reference distributions, not updated per cycle.
- `data/gfs_history_43295.json` — confirmed **dead**: nothing has written to it since 2026-07-26, and its timestamp strings aren't valid ISO (documented directly in `forecast_action.py`'s own comment, which is why CAPE tendency was moved off of it onto `gfs_realtime_43295.csv`).

## D. What is stale/broken

- `data/gfs_history_43295.json`, as above — dead weight, already bypassed by the CAPE-tendency code, but never removed or marked deprecated in its own right.
- **`forecast.json` and `data/pan_india_grid.json` are written non-atomically** — both use a direct `open(path, "w")` + `json.dump(...)`, with no write-to-temp-then-rename. A crash or truncated write mid-cycle (e.g. runner killed, disk full) could leave a partially-written file on disk that the frontend or a downstream consumer then reads as valid JSON and fails to parse, or worse, parses a truncated-but-syntactically-valid fragment. `forecast_action.py` does have a documented "emergency forecast.json" fallback on a caught crash (writes a full, valid replacement with a `crashed` flag) — but that only covers exceptions raised *inside* Python; it does not make the write itself atomic against a hard process kill.
- No schema validation step runs before either `forecast.json` or `pan_india_grid.json` is committed/deployed — the "age > 3h" check in `forecast_update.yml` is a freshness warning printed to CI logs, not a hard gate that blocks a bad commit.
- Nearly every step in `forecast_update.yml` is `continue-on-error: true` (GFS fetch, pan-India fetch, Himawari, terrain, METAR, SHAP, verification, skill scores, alert log, WhatsApp send, ntfy push). This is defensible for genuinely optional enrichments, but it means the workflow has no hard gate distinguishing "a nice-to-have failed" from "the core forecast itself could not be produced" — everything degrades silently to the same green checkmark.
- One literal instance of the exact anti-pattern named in the new instructions: the ntfy push step ends in `... || true`, suppressing its own failure. This is a non-critical best-effort notification (not the real alert path, which is `send_alerts.py`/`alert_delivery.py`), but it is exactly the pattern flagged for removal.

## E. What is VOBL-only

TS/CB/FF **trained ML** (XGBoost, real validated models) · METAR · SRTM terrain/DEM · flood-susceptibility-from-terrain (`terrain_lookup.py::apply_terrain_to_ff`) · the entire `forecast.json` document itself (it is a single-location document, not a per-cell array) · Himawari CTT (cropped to a 50 km radius around VOBL by construction, not a pan-India crop) · SHAP explainability · skill-score/verification tracking.

## F. What is genuinely pan-India

The 992-cell canonical grid itself (`data/pan_india_grid.json`, locked and guarded as of Phase 4.5) · GFS-derived fields at every cell (CAPE, CIN, PWAT, K-index, totals-totals, wind shear, APCP) — these are real, live, pan-India · the CB label engine (Phase 4, genuinely pan-India from IMD gridded rainfall) · the physics-proxy hazard scoring applied to every cell.

## G. Every place where a proxy is being mistaken for an observation

None found as **active, uncorrected** claims in code or docs as of this audit — the two previously-found instances (README's MTL-ensemble claim, the requirement matrix's FF/INDOFLOODS AUROC framing) were corrected in Phase 4.5 and re-verified still correct during this audit. The live, ongoing risk is structural rather than a specific false sentence: `himawari_realtime.json`'s `storm_detected` field defaults to `false` when Himawari is completely unavailable, sitting right next to a `vobl_bt_celsius: null`. `forecast_action.py` does carry a separate, correct `"available": bool(himawari)` flag alongside it, so a careful consumer can tell "no storm" from "we don't know" — but the frontend needs to be checked in Phase 8 to confirm it actually branches on `available`, not on `storm_detected` alone, wherever it renders this signal.

## H. Every place where timestamps/freshness can become inconsistent

- `forecast.json` and `data/pan_india_grid.json` are generated on **different schedules** by **different workflows** (`forecast_update.yml` vs `update_grid.yml`), so at any given moment their `generated_at`/`generated_at_utc` values can legitimately differ by hours — a frontend that reads both without displaying each one's own freshness independently could present a stale pan-India layer next to a fresh VOBL forecast (or vice versa) without saying so.
- Within `forecast_update.yml`, `pan_india_gfs_fetcher.py` writes a third, separate, slot-scoped file (`data/pan_india_grid_slotrun.json`) that is fetched but — per its own comment — never merged into `data/pan_india_grid.json` (that path is owned solely by `backend/pipeline.py`). This is intentional (documented in `docs/PIPELINE_OWNERSHIP.md`) and not a bug, but it means there are, at any moment, potentially **three** pan-India-shaped JSON artifacts on disk with three independent freshness clocks (`pan_india_grid.json`, `pan_india_grid_slotrun.json`, and whatever `dev/pan_india_gfs_fetcher.py` would produce if anyone ran the stale duplicate flagged in Phase 4.5).
- `forecast.json`'s own internal freshness check (`_freshness()` helper, used for GFS/Himawari/METAR sub-blocks) uses a hardcoded `warn_minutes` per source with no single shared freshness policy — Himawari gets 30 minutes, other sources aren't uniformly checked the same way in the excerpt reviewed. This is a real inconsistency Phase 2's unified schema should resolve with one explicit freshness state per source.

---

## Urgent, separate finding: the INDOFLOODS files named in this request are not present

I checked both this audit's working clone and, directly, the actual connected repo folder on your PC (`device_list_dir` on `...\SIH-Hyperlocal-Warning\data`). Present: `floodevents_indofloods.csv` (469,728 bytes) and `precipitation_variables_indofloods.csv` (677,548 bytes) — both already known from Phase 4.5's audit. **Not present anywhere in the repo folder:** `catchment_characteristics_indofloods.csv`, `catchments_shapefiles_indofloods.zip`, `metadata_indofloods.csv`, `variables_description_indofloods.pdf`.

This blocks Phase 4 as written — "inspect the actual downloaded files and schemas... do not assume column names" is not possible for files that aren't in the repo. Before I can do real Phase 4 work, I need one of:
- these four files copied into the repo's `data/` folder (or wherever you've actually saved them) so I can stage and read them, or
- the actual path on your PC where they currently live, if it's outside the connected repo folder, so I can request access to that location.

`floodevents_indofloods.csv`'s real columns (verified again this audit): `EventID, Start Date, End Date, Peak Flood Level (m), Peak FL Date, Num Peak FL, Peak Discharge Q (cumec), Peak Discharge Date, Flood Volume (cumec), Event Duration (days), Time to Peak (days), Recession Time (day), Flood Type` — no lat/lon, no catchment ID column of its own. Whether it can be spatially joined to the canonical grid at all depends entirely on `catchment_characteristics_indofloods.csv` (for a catchment identifier + centroid) and/or `catchments_shapefiles_indofloods.zip` (for real catchment geometry) — neither of which is available to inspect yet.

---

## What this audit means for the rest of this request

Several things the 10-phase plan asks for already exist and are already correct: deterministic GFS row selection (Phase 3 item), truthful WhatsApp alert states (Phase 7), an honest Himawari-unavailable fallback (Phase 3 item), a locked and guarded 992-cell grid (Phases 2/9 prerequisite). The real gaps are: no single canonical per-cell forecast schema (Phase 2), non-atomic JSON writes and no pre-deploy schema validation (Phase 9), the pan-India hazard engine's coefficients are undocumented/uncalibrated (Phase 5), two independent alert systems with inconsistent truthfulness models (Phase 7), and the INDOFLOODS files needed for Phase 4 aren't actually in the repo yet.

Per your own instruction ("Do not modify anything until this audit is complete"), nothing has been changed. I'm stopping here for your review before touching Phase 2 onward — partly because that's what you asked for, and partly because Phase 4 genuinely cannot start until the missing INDOFLOODS files are located.

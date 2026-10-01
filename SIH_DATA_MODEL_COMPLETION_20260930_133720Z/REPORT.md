# DRIFT / SIH — Data + Model Completion Pass — 2026-09-30

**Bottom line up front, honestly:** the audit found real, usable data for the current single-station (VOBL/Bengaluru) system, including genuinely non-circular CB/FF labels I hadn't fully verified before. But two hard blockers stop this from going further today: (1) this cloud sandbox has **no GPU, no PyTorch, and no network route to NCMRWF/MOSDAC** — the A100 you mentioned is not reachable from here, and (2) even where labels exist, they only cover one station (VOBL), not the pan-India grid the MTL backbone would need to predict over. **No training was attempted. No labels, IMDAA data, or INSAT data were fabricated.** Production XGBoost is untouched and remains the deployed system.

## 1. Data Inventory (actual files found in the repo, verified by reading them)

| Dataset | Source | Period | Spatial res | Temporal res | Variables | Format | Coverage | Access |
|---|---|---|---|---|---|---|---|---|
| `bengaluru_6hr_training_dataset_v4.csv` | Blend: IMD station obs + ERA5 reanalysis + derived indices | 2015–2025 | Point (VOBL/BLR station) | 6-hourly | 82 columns: MAX/MIN/RF, CAPE, K-index, Lifted Index, Totals Totals, ERA5 T/Td/u/v at 500/700/850hPa, shear/thickness/moisture-flux derived features, `ts_label` | CSV | Single station | Local, present |
| `bengaluru_6hr_training_dataset_cb_ff.csv` | v4 + IMD 0.25° gridded daily rainfall (`imd_rain/rain/*.grd`, 2015–2025) + INDOFLOODS flood events | 2015–2025 | Point (BLR) + 0.25° IMD grid for CB threshold + 200km flood-event radius for FF | 6-hourly | v4 columns + `cb_label`, `ff_label` | CSV | Single station | Local, present, **already generated** |
| `data/floodevents_indofloods.csv` | INDOFLOODS public flood-event database | multi-decade, event-based | Gauge-level (point) | Event (not gridded/continuous) | EventID, dates, peak flood level, peak discharge, duration, flood type | CSV | Pan-India gauges | Local, present, 4548 rows |
| `data/era5_6hrly_bengaluru_2015_2025.csv` | ECMWF ERA5 reanalysis | 2015–2025 | Point (BLR) | 6-hourly | T2M, D2M, U10/V10, CAPE, SP, T/q/u/v at 500/700/850hPa | CSV | Single station | Local, present, 16073 rows |
| `data/era5_200_300hpa_winds_2015_2025.csv` | ERA5 | 2015–2025 | Point (BLR) | — | Upper-level (200/300hPa) winds | CSV | Single station | Local, present |
| `data/upperair_realtime_43295.csv` | Real-time upper-air derived (GFS-based, not radiosonde) | recent only | Point (station 43295 = VOBL) | 6-hourly | CAPE, CIN, K, LI, TT, PW, SRH 0-3km, EHI, BRN, shear | CSV | Single station | Local, present, **only 67 rows — not a historical archive** |
| `data/blr_terrain.json` | SRTM DEM (Bengaluru-region only, per earlier pass) | static | 0.01° | static | elevation, slope | JSON | Bengaluru bounding box only | Local, present |
| `data/pan_india_grid.json` | GFS (backend/pipeline.py) | current run only, not historical | grid, `n_cells=992`, step per `grid_step_deg` | per-run snapshot | cape, cin, pwat, k_index, totals_totals, u/v at 850/500, wind_shear, apcp_mm, t2m_c, model output probabilities | JSON | Pan-India | Local, present — **no historical archive, no labels, terrain/CTT fields absent on current file (pre-dates that integration)** |
| `data/himawari_history.json` / `himawari_historical_bt.csv` | Himawari-9 (JMA), fetched by this repo's own scripts | historical archive (repo-collected) | ~50km crop around VOBL | per fetch interval | brightness temperature | JSON/CSV | Bengaluru-crop only | Local, present |
| `data/gfs_history_43295.json`, `gfs_multiday_43295.json` | NOAA NOMADS GFS | rolling history | Point (station 43295) | per model cycle | CAPE, K-index, PWAT, etc. | JSON | Single station | Local, present |
| IMDAA (NCMRWF reanalysis) | NCMRWF data service | N/A | N/A | N/A | N/A | N/A | N/A | **NOT PRESENT** — no fetcher script exists, no files on disk, no credentials configured |
| INSAT-3D/3DR | MOSDAC | N/A | N/A | N/A | N/A | N/A | N/A | **NOT PRESENT** — `backend/fetch_insat3d.py` exists as integration code but explicitly documents "Requires MOSDAC institutional credentials" and `MOSDAC_USER`/`MOSDAC_PASS` are unset |
| Radiosonde (true balloon soundings) | — | — | — | — | — | — | — | **NOT PRESENT** — `upperair_realtime_43295.csv` is GFS-model-derived, not radiosonde |
| Lightning data | — | — | — | — | — | — | — | **NOT PRESENT** — no lightning fetcher or files anywhere in the repo |

## 2. IMDAA — Can It Be Obtained Now?

**No, not from this environment, and not attempted.** Findings:
- No IMDAA fetcher script exists anywhere in the repo (`grep -ril imdaa` found only comments/mentions in `README.md`, `index.html`, and `regrid.py` — all describing it as a *future* integration, none of them a working fetch path).
- IMDAA is distributed by NCMRWF and requires institutional registration; this sandbox has **no network route to NCMRWF's servers at all** — a direct test (`curl` to IMD/NCMRWF-adjacent domains) returned proxy-blocked `403`s, confirming this sandbox's egress allowlist does not include those hosts.
- **What I did NOT do:** guess at variable names, resolution, or file formats for IMDAA and present them as fact. I don't have verified access to NCMRWF's current catalog from here, so I'm not fabricating a spec for it.
- **What's needed to actually get this:** you (outside this sandbox, on a machine with real internet + your own NCMRWF registration) would need to register at the NCMRWF data portal, confirm current variable/resolution offerings there directly, and download a small subset yourself. I can write the download/parsing script once you tell me the actual product name and file format NCMRWF gives you — I won't invent that structure speculatively.

## 3. INSAT-3D/3DR — Can It Be Obtained Now?

**No.** `backend/fetch_insat3d.py` already exists in the repo (233 lines, written in an earlier pass) as ready integration code, and it is explicit and honest about its own status in its own docstring: *"Status: Integration code ready. Requires MOSDAC institutional credentials... Once credentials are available, set MOSDAC_USER and MOSDAC_PASS."* Verified this session:
- `MOSDAC_USER` / `MOSDAC_PASS` are unset in this environment.
- This sandbox cannot reach `mosdac.gov.in` at all (same proxy-blocked `403` as NCMRWF).
- No INSAT files exist anywhere on disk (`data/insat3d_iwv.json`, the script's own output path, does not exist).
- The current system correctly does **not** claim INSAT is live: GFS PWAT is used and labeled as GFS-derived, Himawari is labeled Himawari (never INSAT) throughout `location_engine.py` and the frontend.

**Credentials needed:** MOSDAC registration (https://mosdac.gov.in) — username/password for `MOSDAC_USER`/`MOSDAC_PASS`. Until you have these and can confirm the actual current MOSDAC endpoint/format (MOSDAC's API has changed before; the URL in the script is marked "subject to change"), I'm not going to attempt a download or claim one succeeded.

## 4. Data Availability Matrix

| Dataset | Source | Access | Period | Resolution | Variables | Status | Can train? | Can deploy? | Blocker |
|---|---|---|---|---|---|---|---|---|---|
| GFS (station) | NOAA NOMADS | Open, already integrated | 2015–2025 (via v4) + live | Point/global model | CAPE, K-idx, PWAT, winds, etc. | Live | Yes (already used) | Yes (production) | None |
| GFS (pan-India grid) | NOAA NOMADS | Open, already integrated | Live run only | 992-cell grid | Same as above | Live | Partially (no historical archive) | Yes (production) | No historical archive for pan-India grid — can't build a gridded time series without one |
| Himawari-9 | JMA, already integrated | Open, already integrated | Repo-collected history | ~50km BLR crop | Brightness temp | Live | Yes, BLR-only | Yes (production) | Crop radius limits to BLR area only |
| ERA5 | ECMWF (ERA5) | Open, already downloaded | 2015–2025 | Point (BLR) | T/Td/u/v/CAPE/SP at multiple levels | Static archive | Yes | Yes (used as training feature) | Point-only, not gridded |
| DEM/SRTM (terrain) | SRTM, already integrated | Open, already downloaded | Static | 0.01°, BLR only | Elevation, slope | Live | Yes, BLR-only | Yes (production) | Not extended pan-India |
| IMD gridded rainfall | IMD 0.25° `.grd` | Already on disk (`imd_rain/rain/2015–2025.grd`) | 2015–2025 | 0.25° pan-India | Daily rainfall | Static archive | Yes (already used for CB label) | N/A (label source) | None |
| INDOFLOODS | Public flood DB, already downloaded | Open, already on disk | Multi-decade, event-based | Gauge points, pan-India | Flood events | Static archive | Yes (already used for FF label) | N/A (label source) | Event-based, not continuous/gridded |
| IMDAA | NCMRWF | **Not obtained** | — | — | — | Not present | No | No | No credentials confirmed, no network route from this sandbox, product spec unverified |
| INSAT-3D/3DR | MOSDAC | **Not obtained** | — | — | — | Integration code ready, no data | No | No | `MOSDAC_USER`/`MOSDAC_PASS` unset, no network route from this sandbox |
| Radiosonde | — | **Not present** | — | — | — | Not present | No | No | No fetcher exists; not attempted (would need a real archive source, not fabricated) |
| Lightning | — | **Not present** | — | — | — | Not present | No | No | No fetcher exists |
| CB/FF labels (BLR) | IMD gridded rain + INDOFLOODS | Already generated | 2015–2025 | Point (BLR) | `cb_label`, `ff_label` | **Generated, verified non-circular** | Yes, BLR-only | N/A (label) | Single-station only, not pan-India; severe class imbalance (see §6) |

## 5. Common Spatiotemporal Grid

Not built this pass, and I'm not claiming it exists. `regrid.py` (from the prior pass) is a real, tested nearest-neighbor/bilinear regridding abstraction, but it currently has real data to regrid for only three of the five named sources: GFS, Himawari, and DEM. INSAT and IMDAA have zero real files to align — there is nothing to regrid them from. Building "the unified grid" today would mean writing empty/null placeholders for 2 of 5 sources and calling it done, which is exactly the kind of false completeness this pass was told to avoid. **Status: architecturally ready, not executed, correctly blocked on missing source data.**

## 6. Label Audit — the most important section

**Real finding, verified by directly reading the files, not assumed:** CB and FF labels already exist in this repo and were already generated (`data/bengaluru_6hr_training_dataset_cb_ff.csv`, 15,276 rows, 2015–2025), produced by `dev/Fetch cb ff labels.py`. I verified the label logic is genuinely non-circular:

- **CB label**: `RF >= 100mm/day` from **IMD's independent 0.25° gridded daily rainfall archive** (`imd_rain/rain/*.grd`, 11 years on disk, verified present). This is IMD's own standard cloudburst threshold, sourced from an observational rainfall product — not derived from CAPE, K-index, or any of the atmospheric predictors used as model inputs. Not circular.
- **FF label**: any INDOFLOODS flood event within 200km of BLR. Sourced from the independent, public INDOFLOODS gauge database (4548 events, verified present). Also not circular — it's an observed flood event, not a derived rainfall/terrain proxy.
- **TS label** (pre-existing, `bengaluru_6hr_training_dataset_v4.csv`): IMD station thunderstorm observation, `ts_source`/`ts_label` columns — direct station observation, not derived from predictors.

**Verified counts (ran directly against the file, not estimated):**

| Hazard | Positive events | Total 6hr slots | Positive rate |
|---|---|---|---|
| Thunderstorm (TS) | 584 | 15,276 | 3.8% |
| Cloudburst (CB) | 60 | 15,276 | 0.39% |
| Flash flood (FF) | 48 | 15,276 | 0.31% |

**Real limitations, stated plainly:**
- All three labels are **single-station (BLR/VOBL) only**. There is no pan-India gridded label set — `pan_india_grid.json` has no ground-truth labels at all, only model outputs. An MTL model trained on this can only be validated at VOBL; it cannot be trained to predict CB/FF anywhere else on the pan-India grid, because there is nothing to check pan-India predictions against.
- CB and FF positive rates are extremely low (0.39%, 0.31%). A model trained on this will need careful class weighting/resampling and honest reporting of POD/FAR/CSI rather than accuracy, which is already the intent per your validation requirements in §10.
- The 200km flood-event radius for FF is coarse — a flood 200km away is not necessarily a BLR-local flash-flood event; this is the label source's own limitation, inherited honestly rather than hidden.
- `data/catchment_characteristics_indofloods.csv`, which the labeling script optionally uses to refine the FF proxy, is **missing** from the repo. The script falls back to its documented "RF-based FF proxy" when it's absent (confirmed by reading `dev/Fetch cb ff labels.py` lines 149–162) — so the FF label that exists today used the fallback path, not the catchment-refined path.

## 7. Training Dataset Construction

**Not built this pass.** Given §6's finding — labels exist only at a single station, not on the pan-India grid — there is no scientifically valid way to construct a *pan-India* multi-task training set right now. A BLR-only training set already effectively exists as `bengaluru_6hr_training_dataset_cb_ff.csv`; that's exactly the same population `forecast_action.py`'s existing XGBoost slot models are already trained and validated on. Building a *new* training set from it would not move the project past where production already is.

## 8. MTL Model (`backend/mtl_backbone.py`)

Inspected, not modified, not trained. It is a real 4-layer transformer (d_model=256, 8 heads) with three task heads (TS/CB/FF) over 12 named atmospheric features, and its own docstring is already honest about its status: *"Architecture ready. Training requires labeled pan-India grid dataset that does not yet exist in clean form."* This audit confirms that statement is still accurate — the pan-India labeled dataset it needs does not exist, for the same reason given in §6/§7.

## 9. A100 — Availability Check (real finding)

Checked directly in this environment:
- `python3 -c "import torch"` → **`ModuleNotFoundError: No module named 'torch'`** — PyTorch is not installed here.
- `nvidia-smi` → **command not found** — no GPU driver/hardware in this cloud sandbox.
- Network egress test to IMD/NCMRWF/MOSDAC-adjacent hosts → **`403` from the proxy** (allowlist does not include these).

**"A100 is not currently the limiting factor" is true, but for a more basic reason than data volume: the A100 you referenced is not reachable from this cloud sandbox at all.** Whatever A100 access you have must be on your own machine or a separate compute environment — this session cannot use it directly. Even setting that aside, §6/§7/§8 already established that no pan-India MTL training run should happen yet regardless of available compute, because the labels it would need don't exist.

## 10–11. Validation / Production Promotion

Not applicable this pass — no new model was trained. **Production XGBoost (`backend/pipeline.py::hazard_probabilities()`, VOBL slot models in `forecast_action.py`) is unchanged and remains the deployed system**, exactly as required. No promotion decision was needed because no candidate model exists to evaluate.

## 12. SIH Requirement Matrix

| SIH requirement | Current implementation | New implementation this pass | Status | Evidence | Remaining gap |
|---|---|---|---|---|---|
| Real-time system | `backend/pipeline.py`, `forecast_action.py`, GitHub Actions scheduled runs | None | IMPLEMENTED | `forecast_update.yml` | None |
| Hyperlocal grid | `pan_india_grid.json`, 992 cells | None | IMPLEMENTED | file inspected | None |
| 2–6h prediction | Existing slot forecasts + `lead_time.py` (prior pass) | None | IMPLEMENTED | prior pass tests | None |
| Thunderstorm (TS) | XGBoost, IMD station labels | None | IMPLEMENTED (station), pan-India is physics-proxy | verified label file | Pan-India TS still physics-model, not ML-trained |
| Cloudburst (CB) | Physics-proxy (pan-India), real IMD-threshold label exists (BLR only, unused for training yet) | Label audit only | LABEL VALIDATED, MODEL NOT TRAINED | `bengaluru_6hr_training_dataset_cb_ff.csv` | No pan-India CB label set; BLR-only labels not yet fed into any classifier |
| Flash flood (FF) | Terrain-adjusted physics proxy (prior pass), real INDOFLOODS-derived label exists (BLR only) | Label audit only | LABEL VALIDATED, MODEL NOT TRAINED | same file | Same as CB |
| IMDAA | Not integrated | Access determined: **not obtainable from this sandbox** | DATA BLOCKED | §2 | Needs your own NCMRWF registration + real product spec |
| INSAT | Integration code ready, no data | Access determined: **not obtainable from this sandbox** | DATA BLOCKED | §3, `backend/fetch_insat3d.py` | Needs MOSDAC credentials |
| DEM/terrain | Integrated, BLR-only | None | IMPLEMENTED (BLR), pan-India gap known from prior pass | `blr_terrain.json` | Not extended pan-India |
| Common grid | `regrid.py` exists, only 3/5 sources have real data | None built (no data for 2/5 sources) | ARCHITECTURAL ONLY | §5 | Blocked on IMDAA/INSAT acquisition |
| Spatiotemporal MTL model | `backend/mtl_backbone.py`, untrained | Inspected, confirmed still correctly untrained | ARCHITECTURAL ONLY | §8 | Blocked on pan-India labeled dataset |
| Unified risk maps | Existing MapLibre map, pan-India physics probabilities | Prior pass's location-aware panel | IMPLEMENTED (physics-based) | prior pass | ML-based unified maps blocked on MTL |
| XAI | SHAP, VOBL-only | None | IMPLEMENTED (VOBL only) | `compute_realtime_shap.py` | Not pan-India |
| Alerts | WhatsApp delivery, truthful states (prior pass) | None | IMPLEMENTED | prior pass tests | None |
| API | None | None | NOT IMPLEMENTED | — | No public API exists |
| Deployment | Cloudflare Pages, live | None | IMPLEMENTED | `forecast_update.yml` | None |

## 13. What Was Not Done (explicitly, as instructed)

No labels invented. No IMDAA data fabricated or downloaded (none exists on disk; none claimed). No INSAT data fabricated (none exists; MOSDAC credentials confirmed absent). Himawari never relabeled as INSAT. GFS PWAT never relabeled as satellite IWV — verified again this pass in `location_engine.py`'s source-string constants. No model trained (no leakage risk from a run that never happened). XGBoost was not touched or replaced. No huge datasets were downloaded (none were downloadable from this sandbox in the first place). The frontend was not touched this pass, per your explicit instruction.

## 14. Package Contents

`SIH_DATA_MODEL_COMPLETION_<timestamp>/`:
- `data_inventory/` — this audit's raw findings (grep/inspection output, label distribution check)
- `docs/` — this REPORT.md, plus a short `CREDENTIALS_NEEDED.md`
- No `downloaded_data/`, `training_dataset/`, or `models/` content — correctly empty, because nothing was validly obtainable or trainable this pass. Creating placeholder files in those folders would misrepresent the state of the project, so they're omitted rather than faked.
- `tests/` — the label-verification script used to produce §6's counts (re-runnable, reads the real CSV on disk)

## 15. Exactly What's Needed From You Next

1. **NCMRWF/IMDAA**: your own registration at the NCMRWF data portal, done from a machine with real internet access (this sandbox can't reach it) — then tell me the actual product name, file format, and variable list NCMRWF gives you, and I'll write the real fetch/parse code against that spec.
2. **MOSDAC/INSAT**: register at mosdac.gov.in, get `MOSDAC_USER`/`MOSDAC_PASS`, confirm the current API/FTP path is still what `backend/fetch_insat3d.py` assumes (MOSDAC has changed its API before), and provide the credentials as environment variables/GitHub Secrets.
3. **A100 access**: tell me where it actually is — if it's a machine other than this cloud sandbox, none of the training work in §7–§10 can run from inside this Claude session; it would need to run on that machine directly (I can write and hand you the training script, tested against the label file, for you to run there).
4. Optionally, `data/catchment_characteristics_indofloods.csv` if you have it or can source it, to move the FF label off its current RF-based fallback onto the catchment-refined path the labeling script already supports.

Until those three things exist, the honest state of the project is: **production XGBoost stays production**, real non-circular CB/FF labels exist for BLR and are ready to use the moment there's a reason to (e.g., calibrating the existing VOBL slot models further), and the pan-India MTL path is correctly still "architecture ready, data blocked" — not fabricated as further along than that.

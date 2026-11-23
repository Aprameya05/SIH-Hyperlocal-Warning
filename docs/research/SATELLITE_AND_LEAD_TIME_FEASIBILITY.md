# Satellite inputs and 2-6 h lead time: feasibility (SIH26077 / DRIFT)

Research note, 2026-11-23. Repo state: commit `43d85e9`. All HTTP checks below were made on 2026-11-23 between
07:18 and 07:23 UTC, anonymously (no credentials, HEAD or small listing/GET requests only; no datasets downloaded).
No performance numbers in this note are new: every metric quoted is read from `data/ts_panindia_evaluation.json`.

Access classes used throughout: **(a)** achievable now with open anonymous data, **(b)** needs free registration /
credentials, **(c)** not accessible (or not verified accessible) from this environment.

---

## 1. Genuine 2-6 h lead time

### 1.1 What the committed artifact actually gives (computed)

From `data/india_fields_latest.json` `meta` (cycle `2026-11-23T00Z`, acquired 04:57:38-04:57:54Z) and
`data/india_ts_model_latest.json` (`generated_at_utc` 04:57:57Z):

| fhour | valid (UTC) | S3 published (UTC) | pub - init (h) | **valid - acquired (h)** | TS model lead? | TS label window (valid +-1.5 h) vs issue 04:58Z |
|---|---|---|---|---|---|---|
| 3 | 03:00 | 03:49 | 3.82 | **-1.96** (already past) | no | - |
| 6 | 06:00 | 03:35 | 3.59 | **+1.04** | yes ("6 h") | window 04:30-07:30 **opened 0.5 h before issue** |
| 9 | 09:00 | 03:51 | 3.85 | **+4.04** | no | - |
| 12 | 12:00 | 03:37 | 3.62 | **+7.04** | yes ("12 h") | window opens 5.5 h after issue |
| 15 | 15:00 | 03:37 | 3.62 | +10.04 | no | - |
| 18 | 18:00 | 03:39 | 3.64 | +13.04 | yes ("18 h") | - |
| 21 | 21:00 | 03:55 | 3.91 | +16.04 | no | - |
| 24 | 00:00+1 | 03:43 | 3.71 | +19.04 | yes ("24 h") | - |

So the labels "6/12/18/24 h" are **forecast hours from model init**, not warning lead. The real lead is
`fhour - ~4.96 h` for this run: the "6 h" product gives **~1 h** to the valid instant and **negative** lead to the start
of the event window it is verified against. The only artifact hour inside a 2-6 h real lead is **f009 (4.0 h)**, and the
TS model has no f009 model.

### 1.2 What in the repo prevents a 2-6 h lead today

| Blocker | Where | Effect |
|---|---|---|
| Only 00Z/12Z cycles | `gfs_reader.latest_cycle(cycles=(0, 12))`; `--cycle` doc "00Z/12Z"; `india_fields.yml` cron `40 4,16 * * *` | freshest data is 5-17 h old; 06Z/18Z are never used |
| Cron at init+4h40m, and it waits for f024 | `latest_cycle(need_fhour=max(fh))`, cron 04:40/16:40 | ~1 h of avoidable delay after files exist (observed pub 03:35-03:55) plus GitHub-schedule jitter (not measured here) |
| 3-hourly fhours only | `FHOURS = list(range(3, 25, 3))` | no f007/f008/f010/f011 although they exist (verified below) |
| TS models at f006/12/18/24 only, trained on 00Z/12Z only | `FEATS`/`LEADS` in `train_and_evaluate_thunderstorm_model.py`, `build_thunderstorm_training_dataset.py` | the only short "lead" (f006) is ~1 h real lead; lead and time of day are confounded (stated in `limits`) |
| No record of when DRIFT itself issued, nor lead to the user | `meta.files[]` has `published_at_utc`, `acquired_at_utc` only; TS artifact has `valid_times` but no lead/issue fields per lead | the UI cannot state an honest lead |
| `precip_3h` = cum(f) - cum(f_prev) assumes a 3-h step | `ingest()` loop | becomes a 1-h amount if hourly fhours are added; must be computed as cum(f) - cum(f-3) explicitly |
| No season gate | `predict_thunderstorm.py` applies the Apr-Jun-trained model to any date (today's artifact is November, `model_evaluated: true`) | out-of-domain probabilities published as evaluated |

### 1.3 Which GFS cycles/hours give a 2-6 h real lead (verified on the AWS mirror)

Observed S3 `Last-Modified` minus init, for files checked today (all HTTP 200):

| URL (noaa-gfs-bdp-pds) | HTTP | Last-Modified | pub - init |
|---|---|---|---|
| `gfs.20261123/00/atmos/gfs.t00z.pgrb2.0p25.f000` | 200 | 03:48:07 | 3.80 h |
| `.../gfs.20261123/00/.../f005` | 200 | 03:34:27 | 3.57 h |
| `.../gfs.20261123/00/.../f007.idx` | 200 | 03:36:12 | 3.60 h |
| `.../gfs.20261123/00/.../f010` | 200 | 03:36:18 | 3.61 h |
| `gfs.20261122/12/.../f000`, `f006`, `f009` | 200 | 15:33:53, 15:50:00, 15:36:07 | 3.56 / 3.83 / 3.60 h |
| `gfs.20261122/18/.../f000`, `f006`, `f010` | 200 | 21:33:57, 21:36:32, 21:51:15 | 3.57 / 3.61 / 3.85 h |
| `gfs.20261123/06/.../f000`, `f006`, `f010` | 404 at 07:18Z | (not yet; 1.3 h after init) | - |
| `gfs.20240501/06/.../f007.idx`, `gfs.20210501/06/.../f007.idx` | 200 | 2024-05-01 09:30, 2021-05-01 09:35 | archive for training exists (06Z, hourly) |

Facts: (i) the 0.25 deg `pgrb2` files are **hourly** (f005, f007, f010, f016 exist); (ii) all of f000-f010 of a cycle
land within **init + 3.55-3.86 h** (Last-Modified is not monotonic in fhour, so wait for the last needed file);
(iii) hourly files carry an exact `0-f hour acc` APCP (f007 `.idx`: `APCP:surface:6-7 hour acc` and `0-7 hour acc`), so
the existing "exactly one 0-f window" check keeps working; (iv) 06Z/18Z exist in the archive (2021, 2024 checked).

With acquisition at `A = init + 4.0 h` (achievable: files are complete by ~3.9 h):

| fhour | real lead at A | in 2-6 h? |
|---|---|---|
| f006 | 2.0 h | yes (edge) |
| f007 | 3.0 h | yes |
| f008 | 4.0 h | yes |
| f009 | 5.0 h | yes |
| f010 | 6.0 h | yes (edge) |

Because a new cycle arrives every 6 h but the 2-6 h band is only 4 h wide, **valid hours init+10..+12 cannot get a
2-6 h lead from fresh data**; to cover every valid hour, DRIFT must re-issue hourly from the latest available cycle
(e.g. at init+7 h issue f009-f013 from the same cycle). The lead is then honest (valid - issue) but the data age grows
to up to 6 h, and that must be shown. Ingest set per cycle for full coverage: **hourly f004-f016 for all four cycles
(00/06/12/18Z)**; minimal variant: f006-f010 hourly. Cost: ~21 MB of byte-range subset per file (artifact `bytes`)
-> ~13 files x 21 MB ~ 275 MB per cycle, ~1.1 GB/day, all anonymous (class a).

### 1.4 Exact changes proposed (not implemented here)

1. `gfs_reader.latest_cycle(cycles=(0, 6, 12, 18), need_fhour=...)`; `FHOURS = list(range(4, 17))` (or `6..10`).
2. `india_fields.yml` cron `5 4,10,16,22 * * *` (init + 4h05m); add a retry at +4h35m.
3. Compute `precip_3h` and `apcp_6h` from `cum(f) - cum(f-3)` / `cum(f-6)` explicitly (ingest f000-f003 cum or keep
   f-3/f-6 in the list); stop relying on list adjacency.
4. Per-file metadata additions: `forecast_available_time_utc` (= max Last-Modified of the files actually used, the
   moment the information existed publicly), keep `published_at_utc` (per file) and `acquired_at_utc`.
5. Per-lead fields in `india_ts_model_latest.json`: `issued_at_utc` (when DRIFT's artifact is written / committed;
   ideally the Pages deploy time), `valid_time_utc`, `valid_window_start_utc`/`_end_utc` (the +-1.5 h verification
   window), `lead_time_h = valid_window_start_utc - issued_at_utc`, `lead_time_to_centre_h`,
   `data_age_h = issued_at_utc - cycle_init_utc`, `forecast_hour`.
6. Retrain per **forecast hour** f004..f016 using all four cycles (removes the lead/time-of-day confounding), and add a
   season gate (refuse or mark `model_evaluated=false` outside the trained months).

**Honest lead definition:** `lead = start of the verified event window - time the warning reached the user (issue
time)`; never `valid - init`. Report it alongside `data_age`, and evaluate skill as a function of this lead, not fhour.

---

## 2. Satellite sources (accessibility checked 2026-11-23)

View zenith angle (computed, spherical Earth) shows which geostationary platform sees India well; above ~65-70 deg
IR brightness temperatures are limb-degraded and parallax-shifted (several cells at 0.25 deg):

| point | Himawari-9 (140.7E) | Meteosat-9 IODC (45.5E) | INSAT-3DR (74E, nominal) | INSAT-3DS (82E, nominal) |
|---|---|---|---|---|
| Kutch 23.2N 68.9E | 82 | 38 | 28 | 31 |
| Mumbai | 78 | 38 | 22 | 25 |
| Delhi | 75 | 48 | 34 | 34 |
| Bengaluru | 72 | 40 | 16 | 16 |
| Kolkata | 63 | 55 | 31 | 27 |
| Guwahati | 62 | 59 | 36 | 32 |

| Source / product | Access checked (URL, HTTP) | Class | Latency (observed or stated) | Res. | Archive for training | Usable for India | License (stated by provider; verify) |
|---|---|---|---|---|---|---|---|
| **Himawari-9 AHI L1b FLDK** (B13 10.4 um etc.) | `noaa-himawari9.s3.amazonaws.com/?list-type=2&prefix=AHI-L1b-FLDK/2026/11/23/0400/` -> 200; B13 R20 segments S01-S10 listed, Last-Modified 04:11-04:12 for the 04:00 scan; 0710 scan listed at 07:22 | **a** | **~11-13 min** (observed) | 2 km IR, 10 min | `noaa-himawari9` L1b: 2022-10 .. 2026; `noaa-himawari8` L1b: 2015-2022 (+ a 2025 prefix) | east/central India acceptable; **west/NW India at 72-82 deg zenith: poor** | NOAA Open Data Dissemination (open); JMA terms for Himawari data |
| Himawari-9 L2 Clouds (CHGT cloud-top height, CMSK, CPHS) | `.../?prefix=AHI-L2-FLDK-Clouds/2026/11/23/0400/` -> 200, Last-Modified 04:18 (~18 min) | **a** | ~18 min | ~2 km, 10 min | 2023-2026 prefixes | same geometry caveat; files 0.09-0.8 GB full disk (no byte-range subset) | as above |
| Himawari-8 L2 RainfallRate | `noaa-himawari8` root listing -> 200, `AHI-L2-FLDK-RainfallRate/` prefix present | **a** | archive only | - | Himawari-8 era (2015-2022) | as above | as above |
| NOAA GMGSI LW mosaic | already verified in-repo (`backend/data_sources/gmgsi.py`: `s3://noaa-gmgsi-pds/GMGSI_LW`, hourly, ~39 min) - not rechecked | a | ~39 min | ~0.07 deg, 1 h | - | includes MSG-SEVIRI over India, but **8-bit enhanced counts, no calibration** -> not a BT | NOAA open |
| **Meteosat-9 IODC SEVIRI L1.5** (45.5E) | `api.eumetsat.int/data/browse/collections/EO%3AEUM%3ADAT%3AMSG%3AHRSEVIRI-IODC?format=json` -> 200 (metadata anonymous; rights "NRTLicense, GeneralLicense"; date "2017-02-01/"); download endpoint guess -> 404 (download needs an API token) | **b** (free EUMETSAT account + token) | collection text: hourly images if latency < 3 h; 15-min images after > 3 h | 3 km IR, 15 min | from 2017-02 (collection date) | **good geometry for all of India (38-59 deg)** | EUMETSAT data policy (NRT licence terms apply) |
| **INSAT-3D/3DR/3DS** imager (TIR1 10.8 um, IMC/HEM rain) via MOSDAC | `www.mosdac.gov.in/` -> 200; `www.mosdac.gov.in/download/` -> **401**; `/internal/uops` -> 200 "Satellite Data Order" app | **b** (MOSDAC registration; approval process not verified) | not verified | 4 km TIR, ~15-30 min | INSAT-3D since 2013 (provider statement, not verified) | **best geometry (16-36 deg)**, the IMD operational source | ISRO/MOSDAC terms (not checked) |
| **GPM IMERG Early/Late** (half-hourly QPE) | `gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGHHE.07/2026/320/` -> 200 but serves a banner: "Access to GES DISC data requires all users to be registered with the Earthdata Login system"; `jsimpsonhttps.pps.eosdis.nasa.gov/imerg/early/` -> **401** | **b** (free Earthdata Login / PPS registration) | Early ~4 h, Late ~14 h (NASA documentation, not measured) | 0.1 deg, 30 min | V07 from 2000 | yes | NASA open data |
| **CMORPH CDR** (bias-corrected) | `noaa-cdr-precip-cmorph-pds.s3.amazonaws.com` -> 200; `data/30min/8km/` years **1998-2026** listed | **a** | CDR: delayed (not measured) | 8 km, 30 min | 1998-2026 | yes (60S-60N) | NOAA CDR, open |
| CMORPH via NCEI / CPC | `www.ncei.noaa.gov/data/cmorph-high-resolution-global-precipitation-estimates/access/30min/8km/` -> 200 (1998..); `ftp.cpc.ncep.noaa.gov/precip/CMORPH_RT/` -> 200 (`GLOBE/`, `ICDR/`), `.../precip/CMORPH2/` -> 200 (`CMORPH2NRT/`) | **a** | RT/NRT latency not measured | 8 km / 0.05 deg | as above | yes | NOAA open |
| **GSMaP** (JAXA) | `sharaku.eorc.jaxa.jp/`, `/GSMaP/`, `/GSMaP/index.htm` -> **404** | **c** here (normally b: JAXA G-Portal / FTP account) | NRT ~4 h; "Now" product (provider claims) not verified | 0.1 deg, 1 h | from 2000 (not verified) | yes | JAXA terms (not checked) |
| **CHIRPS** | `data.chc.ucsb.edu/products/` -> 200 (lists `CHIRPS/`, `CHIRP/`, `CHIRPS-GEFS/`); `/products/CHIRPS-2.0/` -> 404 (renamed); `/products/CHIRPS/` -> **429** rate-limited | a (pentad/daily) | days-weeks | 0.05 deg, **daily** | 1981- | yes, but **daily** - useless for 2-6 h TS; only climatology | CC (CHC states; verify) |
| GOES | not relevant (no India coverage) | - | - | - | - | no | - |

### How satellite data would enter a model

* **Predictors (nowcast 0-3 h, and blending at 2-6 h):** per 0.25 deg cell at issue time `t`: min / 10th-percentile
  IR BT (Himawari B13 or SEVIRI IR10.8 or INSAT TIR1) over the cell and a 3x3 neighbourhood; fraction of pixels < 235 K
  and < 220 K; **cooling rate** dBT/dt over 15/30/60 min (min-BT tracked over the neighbourhood to absorb motion);
  B08-B13 (WV-IR) difference > 0 (overshooting tops); upstream-advected BT using GFS 700 hPa steering wind. Parallax
  correction (shift by cloud height x tan(VZA)) is mandatory for Himawari over India and advisable for all.
* **QPE (IMERG Early / CMORPH):** antecedent 1-3 h rain as a predictor (persistence-like); and as an **independent,
  spatially complete label** for heavy-rain events (e.g. > 20 mm/h at 0.1 deg aggregated to 0.25 deg), complementing
  the 113-station METAR TS labels. IR-only QPE should not be used as a thunderstorm label (it is partly the predictor).
* Order of work: (1) Himawari-9 B13 (class a, ~12 min latency, archive 2015-2026) for eastern/central India, with a
  zenith-angle mask; (2) Meteosat-9 IODC once an EUMETSAT token is obtained (class b) for full-India coverage;
  (3) INSAT via MOSDAC (class b) as the operationally relevant source.

---

## 3. Retraining the pan-India thunderstorm model

### 3.1 Current state (from `data/ts_panindia_evaluation.json`, `train_and_evaluate_thunderstorm_model.py`)

* **Live predictors (24):** GFS cape, cin, lftx, pwat, rh2m, rh850/700/500, w850/700/500, soilw0_10, k_index,
  total_totals, lapse_850_500, ws10, shear_850_500, t2m_c, dewpoint_depression, mslp_hpa, apcp_6h, apcp_total, valid-hour
  sin/cos. All are model forecasts; **no observation enters at issue time** (persistence is only a baseline).
  Ingested but unused by TS: conv850, conv10, pwat_grad, q850, q700.
* **Labels:** IEM METAR TS/VCTS within +-1.5 h of valid time; windows with < 2 reports unlabelled (anonymous IEM CGI,
  checked: `mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=VOBL...` -> 200; example shows `M` (missing) for
  most night hours at VOBL, i.e. label gaps are real). 113 stations (SOUTH 34, NORTH 27, WEST 18, EAST 12, NE 12,
  CENTRAL 10).
* **Sample:** 19,994 rows, 43 init dates (of 62 planned every-3rd-day dates; the build stopped early), 2024 + 2025,
  00Z/12Z, f006/12/18/24; ~51 % of station-times have a label; event rate 0.0654. Test sets per lead have
  124-241 events (e.g. f006 temporal holdout n=2290, 127 events). Months: Apr-Jun (+125 rows valid 1 July at f024).
* **Limits the file itself states:** airport-biased labels; pilot sample, pre-monsoon only; lead and time of day
  confounded; calibrated to station reports. NORTH at f006 had only 4 test events (`TOO_FEW_EVENTS`).

### 3.2 Independent validation plan

| Split | Train | Test | Purpose |
|---|---|---|---|
| T1 temporal by year | 2021-2023 (GFS v16 era; 2021 archive verified) | 2024, then 2025 (rolling origin) | inter-annual generalisation |
| T2 season-held-out | pre-monsoon (Mar-May) + post-monsoon (Oct-Nov) | monsoon (Jun-Sep), and vice versa | is one model valid across regimes, or one per season |
| S1 leave-region-out | 5 regions, train years only | 6th region, test years only | spatial transfer (already done as "B"; keep) |
| S2 leave-station-cluster-out | stations grouped by 100 km clusters | held-out cluster | removes neighbouring-station leakage inside a region |
| L lead-specific | one model per fhour f004..f016 (or fhour as feature), all 4 cycles | metrics stratified by **real lead** (sec. 1.4) and by valid hour | removes lead/time-of-day confounding |

Controls to keep: date-grouped CV for calibration/threshold inside training only; fixed hyperparameters or nested CV;
block bootstrap by forecast date for CIs; baselines = climatology by station-month-hour, persistence (TS in the 3 h
before issue), CAPE rule, logistic; and, once satellite features exist, a satellite-only nowcast baseline. Report
Brier skill, AUROC, PR-AUC vs no-skill, reliability/ECE, POD/FAR/CSI at a train-fixed threshold.

### 3.3 Data volume needed (estimates, arithmetic only)

* Event counts drive precision: current per-lead tests (124-241 events) give AUROC 95 % CIs about +-0.04 (from the file).
  For per-region x season x lead-band strata to have >= ~100 test events each (6 regions x 3 seasons x 3 lead bands =
  54 strata), at an event rate near 0.06 and ~51 % labelling, roughly 54 x 100 / 0.06 ~ 90k labelled test rows ->
  ~175k station-times per test year: about **every day, 4 cycles, 4-6 fhours, 113 stations** for a full year
  (113 x 365 x 4 x 5 ~ 825k station-times, ~420k labelled). The monsoon/winter event rate is unknown here, so this is
  an order-of-magnitude figure, not a guarantee.
* GFS download for training: ~21 MB subset per file; 4 cycles x 5 fhours x 365 days ~ 7,300 files ~ **150 GB per
  year** transient (decode then delete, as the builder already does); 3-4 years ~ **0.5-0.6 TB** total.
  Anonymous (class a), but needs a long-running job, not GitHub Actions.
* Himawari B13 features: ~2.5 MB per B13 R20 segment (listing), ~3 segments cover India -> ~7.5 MB per 10-min scan;
  sampling 3 scans per issue time (t-60, t-30, t) x 4 issues/day x 365 ~ **33 GB/year**; continuous hourly ~65 GB/year.
* METAR labels: small (per-station yearly CSV from IEM, anonymous).

### 3.4 What cannot be done without new data access

* Full-India calibrated IR (west/NW India) -> needs EUMETSAT token (Meteosat-9 IODC) or MOSDAC account (INSAT). (b)
* IMERG-based labels/predictors -> Earthdata Login. (b) CMORPH CDR is an anonymous substitute for **historical**
  labels; its real-time latency was not measured.
* GSMaP -> not reachable here. (c)
* Lightning (the natural TS label, e.g. IMD/IITM lightning network) and IMD radar/AWS -> not checked, not accessible
  from this repo. (c)
* Rural/mountain TS truth: METAR stations are airports; without lightning or radar, "TS in a 0.25 deg cell" remains
  unverified away from airports.
* Anything new must be re-evaluated; **no skill claim for 2-6 h real lead or for non-Apr-Jun months exists today.**

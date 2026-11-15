# Phase 0.2 — External Data Catalog

Research-only. Compiled 2026-10-01 by web search/fetch from this sandbox (egress proxy, no
authenticated downloads attempted — every "access working" claim below is "the public
documentation says this is the access path," not "I downloaded it from here"). Each entry answers
the mandatory field list from the task brief plus the integration bar question.

Legend for classification: OFFICIAL_REQUIRED_SOURCE / SCIENTIFIC_SUBSTITUTE / SUPPLEMENTARY_SOURCE
/ NOT_SUITABLE. Label types for (D): OBSERVED_EVENT / REMOTE_SENSING_OBSERVED / MODEL_DERIVED /
RAINFALL_PROXY / SUSCEPTIBILITY_ONLY / UNKNOWN.

---

## A. Historical pan-India atmospheric predictors (sub-daily)

### A1. ERA5 (hourly, pressure + single levels)
- **URL**: https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels and
  .../reanalysis-era5-pressure-levels-timeseries
- **Host**: ECMWF / Copernicus Climate Data Store (CDS)
- **License**: Copernicus free/open license, attribution required, no registration cost
- **Coverage**: global, so pan-India fully covered; resolution ~0.25° (~31km)
- **Temporal**: hourly, 1940–present (this is the key fact: ERA5 is NOT daily-only)
- **Variables**: 2m temp, dewpoint/specific humidity, surface pressure, geopotential, U/V wind at
  multiple pressure levels, CAPE, CIN (as single-level diagnostic parameters), total column water
  vapour, convective precipitation
- **Size**: full India hourly multi-decade pull at 0.25° for ~15 variables is multi-TB if done
  naively; a 992-cell, single-level subset for a few years is GB-scale and feasible
- **Download/API**: CDS API (`cdsapi` Python client), key-based auth, free registration
- **Auth**: required (free CDS account + API key) — not tested live from this sandbox
- **Historical downloadable**: yes, per CDS documentation, this is the standard distribution
  mechanism, not just a description
- **Maps to 992-cell grid**: yes — 0.25° reanalysis can be bilinearly regridded onto the 1.0°
  canonical grid cell centers already used for GFS in this repo (same regridding pattern as
  `regrid.py` already uses for GFS)
- **2–6h support**: YES for predictors — hourly cadence resolves sub-6h evolution, unlike the
  daily IMD `.grd` rainfall currently blocking Phase 5.7. CAPE/shear/geopotential/humidity fields
  at hourly resolution are the single biggest fixable gap in the current pipeline.
- **Hazards supported**: TS, CB, FF (predictor side only, not FF labels)
- **Leakage risk**: low for predictors (reanalysis is independent of IMD station labels); must not
  reuse the same ERA5 fields already baked into `data/era5_6hrly_bengaluru_2015_2025.csv` without
  checking temporal overlap with any label windows
- **Known limitations**: reanalysis, not observation — still "model-derived" atmospheric state,
  same caveat as GFS; 0.25° resolution is coarser than convective-scale processes that actually
  drive flash floods and thunderstorms
- **SIH requirement addressed**: #13 (convergence, currently GRAY/null), #11 (CIN pan-India,
  currently YELLOW/Bengaluru-only), #1 (pan-India grid — gives it real historical ML predictors)
- **Classification**: **SCIENTIFIC_SUBSTITUTE** for IMDAA at coarser resolution (IMDAA is ~12km,
  India-specific; ERA5 is ~31km, global) — not equivalent, but closes the "no sub-daily pan-India
  historical predictor" gap today, with free, working access.
- **Would it allow a new claim?** YES: "pan-India hourly CAPE/shear/humidity/geopotential history
  back to 1940 is available via ERA5 and can be regridded onto the 992-cell grid for model
  training" — bounded, honest, does not claim IMDAA-equivalence or convective-scale resolution.

### A2. ERA5-Land
- **URL**: https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_LAND_HOURLY (also CDS)
- **Resolution**: ~9km, hourly, 1950–present
- **Coverage**: land only (fine for India), no CAPE/upper-air (land-surface variables only:
  temperature, precip, soil moisture, runoff)
- **Classification**: SUPPLEMENTARY_SOURCE — good for soil moisture/runoff antecedent-condition
  features for FF, not a CAPE/shear substitute.
- **New claim it allows**: "antecedent soil-moisture/runoff conditioning at ~9km hourly resolution
  is available pan-India via ERA5-Land" — useful as an FF feature, not a TS/CB predictor.

### A3. GFS archive (already live in production for real-time; historical archive via NOMADS/NCEI)
- Already in use (confirmed by MASTER_DATA_INVENTORY). Historical GFS analysis archives exist at
  NCEI but are not newly discovered here — not re-catalogued as a "new" candidate.

---

## B. Historical precipitation / QPE

### B1. GPM IMERG (V07)
- **URL**: https://gpm.nasa.gov/data/imerg ; https://catalog.data.gov/dataset/gpm-imerg-final-precipitation-l3-half-hourly-0-1-degree-x-0-1-degree-v07-gpm_3imerghh-at-g
- **Host**: NASA GES DISC / PPS
- **License**: free, NASA open data policy
- **Coverage**: quasi-global (90°N–S in V07), so full India coverage
- **Spatial resolution**: 0.1° (~10km)
- **Temporal resolution**: half-hourly, also 3-hourly/daily/monthly aggregates
- **Historical record**: TRMM+GPM combined record from 1998–present (continuous)
- **Latency**: Early Run ~4h, Late Run ~12–14h, Final Run (research-grade, MERRA2/ERA5-corrected)
  ~3.5 months — per NASA's own IMERG page
- **Access**: FTP/HTTP via GES DISC, free PPS account registration, `earthaccess`/`gpm_api` Python
  clients exist; NOT tested live from this sandbox (egress-restricted)
- **Historical downloadable**: yes, this is NASA's standard distribution, not merely described
- **Maps to 992-cell grid**: yes, 0.1° is finer than the 1.0° canonical grid — straightforward
  area-mean regrid
- **2–6h support**: YES — half-hourly cadence is the best-resolved precipitation product found in
  this search that is genuinely usable for nowcasting init+target windows, and Early Run's ~4h
  latency is compatible with producing a near-real-time, though not instantaneous, feed
- **Hazards**: FF (QPE/rainfall), supplementary for CB (precip intensity rate)
- **Leakage risk**: moderate if used both as input feature and indirectly as label proxy for FF —
  must be kept clearly separated from any flood-label definition the way IMD daily rainfall
  currently is
- **Known limitations**: satellite-merged precipitation estimate, not gauge-truth; known
  underestimation in complex terrain/orography (relevant for hilly flash-flood catchments);
  Final Run (the most accurate version) has a 3.5-month lag, so only Early/Late Run are usable for
  anything resembling operational nowcasting, at a real accuracy cost
- **SIH requirement addressed**: #15 (QPE, currently a GFS-APCP proxy labeled YELLOW) — this is a
  genuine satellite/multi-sensor precipitation estimate, not a forecast-accumulation proxy
- **Classification**: **OFFICIAL_REQUIRED_SOURCE-adjacent** — not IMD gauge data, but the closest
  thing to real observed sub-daily QPE that is actually downloadable without a restricted-access
  credential, which makes it the strongest QPE candidate found.
- **Would it allow a new claim?** YES: "a genuine half-hourly, satellite-based precipitation
  estimate (IMERG, not a GFS-forecast proxy) is available pan-India back to 1998 and can replace
  the GFS-APCP QPE proxy for both historical training and, with Early Run, near-real-time
  inference" — this directly un-blocks the #15 YELLOW→possible-GREEN path, pending an actual
  implementation and accuracy validation against IMD gauges, which this phase did not do.

### B2. IMD gridded rainfall (0.25°/daily, already in repo) — not re-catalogued (already known/used
in Phase 5.7, confirmed daily-only, confirmed insufficient for 2-6h per PHASE_0_1_FF_CURRENT_STATE.md).

### B3. GSMaP (JAXA)
- Found referenced in nowcasting literature but not independently fetched this pass; flagged as
  **INVESTIGATE NEXT**, not catalogued with full confidence here — would need a dedicated fetch of
  https://sharaku.eorc.jaxa.jp/GSMaP/ to confirm current access terms. Noted rather than fabricated.

---

## C. Historical satellite observations (CTT/BT/water vapor/lightning)

### C1. INSAT-3D/3DR via MOSDAC
- **URL**: https://www.mosdac.gov.in/open-data ; https://mosdac.gov.in/i-dont-have-username-and-password-mosdac-can-i-download-data
- **Status**: confirmed still **registration-gated** — the page title itself
  ("I don't have username and password of MOSDAC. Can I download data?") indicates download
  requires a MOSDAC account; an "Open Data" section exists but its exact scope (whether it covers
  INSAT-3D/3DR brightness-temperature archives at the resolution this project needs) could not be
  confirmed by this pass — the direct page fetch for the FAQ answer failed (404) in this sandbox
  and was not independently re-verified. **Do not claim MOSDAC is credential-free based on this
  research — it is not confirmed open.**
- **Classification**: still RED/blocked, matching the existing repo finding. No new claim unlocked.
- **Would it allow a new claim? NO** — access status is unchanged from the existing blocked
  determination; this research did not obtain or confirm working credential-free access.

### C2. Himawari-9 (already live in production, Bengaluru-crop only) — not re-catalogued, already
known. Pan-India Himawari coverage is geometrically possible (Himawari covers all of India from
geostationary orbit) but pan-India ingestion was not attempted/verified this pass — flagged as
**INVESTIGATE NEXT**: extending the existing Himawari pipeline's crop region to pan-India is a
credential-free, already-working-pattern option that was not evaluated for feasibility/bandwidth.

### C3. Indian Lightning Location Network (ILLN) / IITM-LLN
- **URL**: https://ildn.in/about.php ; academic papers (e.g. Biswasharma et al., ResearchGate/SSRN,
  2025) describing IITM's lightning location network
- **Host**: Indian Institute of Tropical Meteorology (IITM), government research institute
- **Access**: no public bulk-download API was found in this search; papers describe using the data
  but do not show an open download portal. **UNKNOWN / likely restricted** — not confirmed openly
  downloadable.
- **Classification**: NOT_SUITABLE for this phase (access unconfirmed), flagged INVESTIGATE NEXT
  for a future phase if IITM data-sharing terms can be confirmed directly.
- **Would it allow a new claim? NO**, not without confirming an actual access path.

---

## D. Observed flood-event labels

### D1. INDOFLOODS (full corpus, as published)
- **URL**: https://doi.org/10.5281/zenodo.14584654 ; paper:
  https://journals.ametsoc.org/view/journals/bams/106/2/BAMS-D-24-0008.1.xml
- **Host**: Zenodo (data), AMS BAMS (paper), built by IIT researchers
- **License**: Zenodo open access, AMS reuse terms (non-commercial research reuse typical for AMS;
  exact commercial-use terms not independently verified this pass)
- **Label type**: **OBSERVED_EVENT** — floods identified from actual station **water-level/discharge
  observations** crossing CWC-defined warning/danger thresholds, not rainfall or simulation
  inference. This is genuinely the strongest label type in the brief's taxonomy.
- **Scale (full corpus, per the paper, independently fetched this pass)**: 8,342 flood events
  (5,525 regular + 2,817 severe) across 214 gauge stations, 1959–2020, India-wide major river
  basins. **This differs from the repo's stated baseline (4,548 events, 620 cell/date positives
  after IMD-rainfall overlap)** — the repo figure is evidently a filtered subset (likely restricted
  to a shorter study period and/or a successful rainfall-overlap join), not a discrepancy this
  phase is resolving; flagging it for the team to reconcile which cut is in `data/floodevents_indofloods.csv`
  versus the full Zenodo release.
- **Catchment attributes**: 28 geomorphological + 19 climatological + 10 event-scale precipitation
  + soil/land-cover/lithology/anthropogenic (7) variables, plus catchment boundary shapefiles —
  this matches the repo's already-ingested `catchment_characteristics_indofloods.csv`.
- **Temporal resolution of the label itself**: event start/end/peak **times** are recorded (not
  just dates) per the paper's variable list (duration, time-to-peak, recession time) — this is
  worth the team re-checking directly: if INDOFLOODS' underlying station discharge records actually
  carry sub-daily timestamps (many CWC gauges report multiple times/day), the sub-day temporal
  localization Phase 5.7 found missing might exist in the *raw* station series even though the
  *derived* daily rainfall join (IMD `.grd`) is daily-only. This is a genuine "investigate next"
  lead, not a resolved finding — this phase did not fetch CWC's raw discharge granularity.
- **Already in use**: yes, research-only, per repo baseline.
- **Classification**: OFFICIAL_REQUIRED_SOURCE (already partially integrated) — the finding here is
  about the scale discrepancy and the CWC sub-daily-granularity lead, not a brand-new dataset.
- **Would it allow a new claim beyond current state?** CONDITIONAL: if CWC discharge data for the
  214 gauges is obtainable at sub-daily granularity directly (not just via the repo's existing
  daily IMD-rainfall join), it could support "event timing accurate to within N hours" rather than
  "event day only" — but this requires fetching CWC's raw series, not done this phase. As currently
  joined in the repo (daily IMD rainfall), the label remains day-granularity and the existing
  PHASE_0_1 finding stands unchanged.

### D2. Global Flood Database (Cloud to Street / MODIS, 2000–2018)
- **URL**: https://developers.google.com/earth-engine/datasets/catalog/GLOBAL_FLOOD_DB_MODIS_EVENTS_V1
  ; https://github.com/cloudtostreet/MODIS_GlobalFloodDatabase
- **Label type**: **REMOTE_SENSING_OBSERVED** — flood extents derived from MODIS surface-water
  detection during named flood events, not simulated — a real independent flood-extent source.
- **Coverage**: global, event-based (913 events worldwide 2000–2018, subset over India)
- **Resolution**: 250m MODIS-derived flood extent polygons/rasters, daily compositing during events
- **Access**: Google Earth Engine (free with account) and GitHub repo
- **Hazards**: FF (extent, not nowcasting lead time)
- **Leakage risk**: low, independent of IMD/GFS pipeline
- **Limitations**: event *extent* at daily MODIS compositing cadence, cloud-cover gaps common
  during active monsoon flooding (the exact time floods are happening is often when MODIS is most
  cloud-obscured) — not usable for 2–6h lead-time labeling, only as a coarse event-occurrence
  cross-check
- **Classification**: SUPPLEMENTARY_SOURCE — a good independent cross-validation source for
  "did a flood actually happen here" at the day level, not a replacement for sub-daily FF labels.
- **Would it allow a new claim?** YES, narrow one: "flood events in the INDOFLOODS label set can be
  independently cross-validated against an independent remote-sensing flood-extent source (MODIS
  Global Flood Database) for the subset of events with usable MODIS coverage" — a label-quality
  claim, not a new 2-6h-capability claim.

### D3. Dartmouth Flood Observatory (DFO) global archive (1985–2016, now largely absorbed into
successor catalogs per search results)
- **Label type**: mixed — DFO's own methodology blends news reports, government reports, and
  satellite imagery; **not purely OBSERVED_EVENT in the strict station-gauge sense** — closer to
  MODEL_DERIVED/UNKNOWN for events without direct satellite confirmation.
- **Classification**: SUPPLEMENTARY_SOURCE at best, **NOT_SUITABLE** as a primary label source given
  the project's stated skepticism bar for "anything inferring flood from... social reports."
- **Would it allow a new claim? NO** — rejected per the brief's own standard.

---

## E. Pan-India terrain/hydrology

### E1. Bhuvan/NRSC CartoDEM (Indian National DEM v2/v3, Cartosat-1)
- **URL**: https://bhuvan-app3.nrsc.gov.in (download portal), documentation at
  bhuvan-app3.nrsc.gov.in/data/download/tools/document/CartoDEMReadme*.pdf
- **Host**: ISRO/NRSC (Indian government)
- **Coverage**: pan-India, 30m resolution (CartoDEM v3), validated against GPS ground control in
  the cited validation papers
- **Access**: Bhuvan portal, free registration (government citizen login), tile-by-tile download —
  this matches what the repo's own `data/blr_terrain.json` used for Bengaluru, just not yet
  extended pan-India
- **Historical downloadable**: yes, this is NRSC's standard public distribution mechanism, widely
  cited in literature; not tested live from this sandbox
- **Maps to 992-cell grid**: YES in principle — pan-India CartoDEM tiles exist and elevation/
  slope/aspect can be computed consistently for every cell (DEM is static terrain, available
  everywhere India has land, unlike atmospheric variables with gaps)
- **Classification**: **OFFICIAL_REQUIRED_SOURCE** for pan-India DEM — this is the same DEM family
  already validated and used for Bengaluru; the gap is coverage extension, not a missing/different
  dataset.
- **Would it allow a new claim?** YES, and this is one of the clearest wins in this catalog:
  "pan-India elevation/slope/aspect at 30m can be computed for all 992 cells using the same
  CartoDEM source already validated for Bengaluru — no new dataset license or access path is
  needed, only re-running the existing terrain pipeline over the full national tile set." This
  directly targets SIH requirement #16 (YELLOW, Bengaluru-only → could become pan-India).

### E2. HydroSHEDS / MERIT-Hydro (drainage, flow accumulation, river network)
- **URL**: https://data.hydrosheds.org/ ; https://developers.google.com/earth-engine/datasets/catalog/MERIT_Hydro_v1_0_1
  ; https://www.nature.com/articles/s41597-021-00819-9 (MERIT-Basins vector river network)
- **Host**: WWF (HydroSHEDS) / University of Tokyo (MERIT-Hydro), global academic/NGO products
- **Resolution**: HydroSHEDS 30 arc-sec (~1km/~500m variants exist at finer resolution too);
  MERIT-Hydro ~90m
- **Coverage**: global including all of India
- **Access**: free direct download (HydroSHEDS) and Google Earth Engine catalog (MERIT-Hydro) —
  no registration gate found in search results for either
- **Variables**: conditioned DEM, flow direction, flow accumulation, river network, basin/catchment
  boundaries — exactly the drainage/catchment variables requirement (E) asks about
- **Maps to 992-cell grid**: YES — flow accumulation and catchment boundaries can be computed/
  aggregated consistently for all 992 cells since these are static global hydrology products with
  full India coverage, unlike a station-based or event-based dataset
- **Classification**: **OFFICIAL_REQUIRED_SOURCE-equivalent** for drainage/flow-accumulation — this
  closes a real gap: the repo currently has zero pan-India flow-accumulation/drainage layer (only
  Bengaluru terrain + per-gauge INDOFLOODS catchment polygons for 214 specific stations, not a
  continuous pan-India raster).
- **Would it allow a new claim?** YES: "pan-India flow-accumulation and drainage-network rasters,
  consistent for all 992 cells (not just the 214 INDOFLOODS gauge catchments), are available today
  via HydroSHEDS/MERIT-Hydro at no cost and no registration" — this is a genuinely new capability,
  not previously present anywhere in the repo per MASTER_DATA_INVENTORY.

---

## F/G/H/I. Code candidates

### F1. Earthformer (amazon-science/earth-forecasting-transformer)
- **URL**: https://github.com/amazon-science/earth-forecasting-transformer
- **License**: repo exists under Amazon Science org; license file not independently fetched this
  pass — **verify Apache-2.0/other before any use**, not assumed here
- **Architecture**: genuinely spatiotemporal space-time transformer (Cuboid Attention), built for
  Earth-system forecasting (precipitation nowcasting among its benchmark tasks: SEVIR radar)
- **Training code present**: yes, per repo structure (benchmark scripts for SEVIR, ICAR-ENSO, N-body)
- **Pretrained weights present**: partial — SEVIR/ICAR-ENSO checkpoints referenced in some
  variants; not independently confirmed downloadable this pass (not fetched)
- **Built for**: radar-grid nowcasting (SEVIR: 384x384 US radar mosaics, 5-min cadence) — a
  different spatial grid and sensor type than this project's 992-cell 1.0° grid
- **Genuinely spatiotemporal**: YES — real sequence-to-sequence space-time attention, unlike the
  repo's existing single-timestep MTL backbone
- **Multi-task**: no, single-variable radar nowcasting in its reference setup
- **Adaptable to 992 cells / this schema**: HIGH integration complexity — would need re-architecting
  for a much coarser, irregular 992-cell grid instead of dense radar imagery, and for this
  project's heterogeneous multimodal feature vector instead of radar-only input
- **Dependencies**: PyTorch, PyTorch-Lightning, custom CUDA-friendly cuboid-attention ops
- **Scientific risk**: trained/validated at a radar spatial scale orders of magnitude finer than
  this project's 1.0° grid; no guarantee the architecture's inductive biases (cuboid attention
  tuned for dense imagery) transfer to a sparse 992-point graph/grid
- **Classification**: SCIENTIFIC_SUBSTITUTE candidate for "genuinely spatiotemporal architecture,"
  not a drop-in
- **Would it allow a new claim?** CONDITIONAL: "a genuinely spatiotemporal (not single-timestep)
  architecture exists and is open-source, as a reference design for replacing the untrained MTL
  backbone" — true and useful, but integrating it is a real engineering/research project, not a
  swap-in; do not claim pan-India spatiotemporal capability until it is actually retrained on this
  project's grid and data.

### F2. DGMR / openclimatefix/skillful_nowcasting (DeepMind Deep Generative Model of Radar)
- **URL**: https://github.com/openclimatefix/skillful_nowcasting
- **License**: MIT (Open Climate Fix's reimplementation; verify against upstream DeepMind terms
  for any pretrained weights specifically)
- **Architecture**: generative adversarial nowcasting model, genuinely spatiotemporal (video-style
  radar sequence to sequence)
- **Training code**: yes. **Pretrained weights**: the original DeepMind weights were not fully
  open-sourced historically; the OCF reimplementation trains from scratch — confirm current weight
  availability before relying on it, not assumed here.
- **Built for**: UK/US radar composites (dense gridded reflectivity), not sparse 992-cell data
- **Genuinely spatiotemporal**: yes. **Multi-task**: no (precipitation only).
- **Integration complexity**: HIGH (needs dense radar-like input grid; India lacks the public
  radar-composite equivalent at the needed density pan-India — IMD's own radar network is not
  catalogued as a described candidate in this pass since no public bulk-download access was found).
- **Scientific risk**: same grid-mismatch problem as Earthformer, compounded by the lack of a
  dense pan-India radar input to actually feed it.
- **Classification**: NOT_SUITABLE at this project's current data scale (no pan-India radar feed to
  drive it) — REJECT for this phase, revisit only if a pan-India radar mosaic becomes available.
- **Would it allow a new claim? NO**, given the missing dense-radar precondition.

### F3. ConvLSTM reference implementations (various GitHub repos under "precipitation-nowcasting"
topic, e.g. within `awesome-precipitation-nowcasting` curated lists)
- Generic, numerous, easy to adapt to small grids (ConvLSTM operates on any regular grid shape,
  including a reshaped 992-cell/coarse grid) — LOW-MEDIUM integration complexity, genuinely
  spatiotemporal, no multi-task by default, no official pretrained weights for an India-specific
  domain (would require training from scratch on this project's own historical data).
- **Classification**: SCIENTIFIC_SUBSTITUTE — most realistic near-term spatiotemporal upgrade path
  given the 992-cell grid's coarseness, specifically because ConvLSTM does not require the dense
  imagery-scale input that Earthformer/DGMR's reference setups assume.
- **Would it allow a new claim?** YES, conditionally: "a ConvLSTM-family architecture can be trained
  directly on the existing 992-cell, 1.0° GFS/ERA5 feature stack to produce a genuinely
  sequence-aware (not single-timestep) pan-India forecast" — realistic because it matches the
  project's actual grid resolution, unlike the radar-scale models above. Still requires an actual
  training run and validation this phase did not perform.

### G/H. Multimodal fusion / multi-task learning code
- No single authoritative, pretrained, directly-reusable multimodal+MTL weather repo matching this
  project's exact schema (GFS+terrain+satellite+static catchment, 3 correlated hazard heads) was
  found in this search pass. This is a genuine negative finding, not a search failure to disclose:
  the project's specific combination (coarse grid + heterogeneous modalities + PU-labeled FF) is
  a narrow enough combination that no existing GitHub repo targets it directly. Any MTL
  architecture will need to be built using this project's own `backend/mtl_backbone.py` as a
  starting point rather than imported wholesale.
- **Classification**: NOT_SUITABLE (nothing found that clears the integration bar) — reject the
  idea of importing an external MTL repo wholesale; the existing in-repo backbone, once trained, is
  the more realistic path forward.

### I. Calibration
### I1. netcal (EFS-OpenSource/calibration-framework)
- **URL**: https://pypi.org/project/netcal/ ; https://github.com/EFS-OpenSource/calibration-framework
- **License**: Apache-2.0 (per PyPI listing)
- **What it does**: post-hoc calibration methods (isotonic regression, Platt/temperature scaling,
  Bayesian calibration) for classifiers AND regressors, plus calibration-error metrics (ECE, MCE)
- **Training code**: n/a (it's a calibration post-processing library, not a trained model)
- **Integration complexity**: LOW — same family of technique (isotonic) the repo's own `ff_slot_*`
  models already use, just a more general/maintained library than a bespoke isotonic call
- **Would it allow a new claim?** YES, narrowly: "if the Phase 5.7 PU ranking score is retrained
  with real sub-daily rainfall features (per gap #1 in PHASE_0_1_FF_CURRENT_STATE.md) and labeled
  negatives become available, netcal's calibration tooling (plus proper calibration-set evaluation,
  e.g. ECE) could convert the PU ranking score into an evaluated, calibrated probability the same
  way `ff_slot_*`'s isotonic calibration is — but this is useless until the sub-daily-resolution and
  labeled-negative problems are separately solved; calibration bolts onto those, it does not fix
  the resolution/label-quality problems that are the FF model's real blockers."

---

## Summary count

Serious dataset candidates catalogued with real evidence: **9** (ERA5, ERA5-Land, GPM IMERG, IMD
gridded rainfall — pre-existing, INSAT/MOSDAC — blocked/unresolved, INDOFLOODS full corpus,
Global Flood Database, Dartmouth/DFO, CartoDEM pan-India, HydroSHEDS/MERIT-Hydro). GSMaP and
ILLN/IITM lightning are flagged but not fully catalogued (insufficient confirmed access evidence).

Serious code candidates catalogued: **5** (Earthformer, DGMR/skillful_nowcasting, ConvLSTM-family,
the negative finding on MTL/multimodal repos, netcal for calibration).

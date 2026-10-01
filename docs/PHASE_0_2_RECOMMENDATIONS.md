# Phase 0.2 — Recommendations

Ranked by technical suitability and evidence from `docs/PHASE_0_2_EXTERNAL_DATA_CATALOG.md`, not
by popularity. "USE NOW" means a current blocker could realistically be addressed with evidence of
real, working, no-extra-credential access found this pass — not that integration work is zero.

## A. USE NOW

1. **ERA5 hourly reanalysis (CDS API)** — free registration, documented working API, hourly
   cadence (vs. the repo's current daily-only IMD rainfall and 6-hourly Bengaluru-only ERA5
   extract). Directly addresses SIH requirement #13 (convergence, currently GRAY/null) and #11
   (CIN pan-India, currently Bengaluru-only) by giving every one of the 992 cells a real historical
   hourly predictor record. This is the single most defensible "use now" item because it needs no
   new institutional relationship — only a free CDS account.

2. **Bhuvan/NRSC CartoDEM extended pan-India** — not a new dataset, the *same* DEM family already
   validated and in production for Bengaluru (`data/blr_terrain.json`). The only gap is tile
   coverage, not access or license. Lowest-risk, highest-confidence item in this catalog: it
   directly targets SIH requirement #16 (YELLOW → pan-India) using a source this project has
   already integrated and trusts.

3. **HydroSHEDS / MERIT-Hydro drainage and flow-accumulation layers** — free, no registration gate
   found, global coverage including all of India, genuinely closes a gap (the repo currently has
   zero continuous pan-India drainage/catchment raster; INDOFLOODS catchments only cover 214 gauge
   points, not all 992 cells). Addresses SIH requirement #17's pan-India half directly.

4. **GPM IMERG for historical QPE training** — free NASA access (registration, not a restricted
   credential), half-hourly, pan-India, 1998–present. For *historical model training* this is
   usable now. (Near-real-time Early Run, 4h latency, is listed here as "use now for training,"
   but promoting it to the live production pipeline is an INVESTIGATE NEXT item — see below —
   because latency, accuracy-vs-gauge validation, and pipeline integration were not tested this
   phase.)

## B. INVESTIGATE NEXT

1. **GPM IMERG Early Run for live/near-real-time QPE in production** — the historical access case
   is solid (A4 above), but using it operationally requires validating ~4h latency against the
   2-6h lead-time requirement (it eats a meaningful fraction of the window), and validating IMERG's
   accuracy against IMD gauge rainfall over Indian terrain (known literature caveats about
   orographic underestimation) before it can be trusted as a QPE input to any alert-triggering
   logic.

2. **ConvLSTM-family architecture retrained on the existing 992-cell GFS/ERA5 feature stack** —
   realistic because it matches this project's actual coarse grid resolution (unlike Earthformer/
   DGMR, which assume dense radar-scale imagery), but requires an actual training run, a defined
   train/val/test split with no leakage, and a baseline comparison against the current
   single-timestep MTL backbone and the XGBoost heads before any "genuinely spatiotemporal"
   production claim can be made.

3. **INDOFLOODS full corpus (8,342 events) vs. the repo's filtered subset (4,548 events / 620
   positives)** — this phase found the published full-corpus numbers differ from what's in the
   repo. Before any new modeling investment, the team should reconcile which filtering criteria
   produced the repo's subset and whether re-pulling the full Zenodo release recovers more usable
   positive events — this is a data-engineering task, not a new acquisition, but needs doing before
   anything downstream of FF labels is trusted.

4. **CWC raw discharge granularity for the 214 INDOFLOODS gauge stations** — the INDOFLOODS paper's
   variable list (time-to-peak, recession time) suggests the underlying station records may carry
   sub-daily timestamps even though the repo's current rainfall join is daily. If true, this could
   be the only realistic path to sub-day-granularity *observed* FF labels found in this research —
   but this phase did not fetch CWC's raw series to confirm, so it remains a lead, not a finding.

5. **ERA5-Land for antecedent soil-moisture/runoff FF features** — plausible value-add for flash
   flood (not TS/CB) given hourly ~9km land-surface fields, but no feature-engineering or
   leakage-check work was done this phase.

6. **Himawari-9 pan-India extension** — the project already has a working Himawari ingestion
   pattern (Bengaluru crop); whether it is feasible to extend the crop region pan-India (bandwidth,
   tile count, processing cost) was not evaluated this phase.

7. **GSMaP (JAXA)** — found referenced as a sub-daily precipitation alternative to IMERG in
   nowcasting literature but not independently fetched/confirmed this pass; worth a dedicated
   access check before committing to or rejecting it.

## C. REJECT

1. **MOSDAC/INSAT-3D/3DR as a near-term unblock** — still registration-gated per this phase's
   research; no new access path was found or confirmed. Nothing changes the existing RED status.
   Reject as a Phase 0.2 "use now" or even "investigate next" item — it is a credentialing/
   institutional-access problem, not a research gap this kind of search can close. (This is a
   restatement, not a new rejection — included for completeness against the task's three-bucket
   structure.)

2. **Indian Lightning Location Network (ILLN/IITM-LLN) bulk data** — academic papers describe
   using this data, but no public bulk-download/API access was found. Reject for this phase on the
   same "unconfirmed access" grounds as MOSDAC — revisit only if IITM publishes a data-sharing
   mechanism.

3. **DGMR / skillful_nowcasting (DeepMind radar nowcasting GAN)** — architecturally impressive and
   genuinely spatiotemporal, but it assumes a dense pan-India radar-composite input this project
   does not have and no candidate for was found with confirmed open bulk access. Importing this
   architecture without the radar feed it needs would be form without substance — reject.

4. **Earthformer as a direct drop-in** — not rejected as an idea (listed INVESTIGATE in spirit
   under B2's ConvLSTM entry as the more realistic alternative), but rejected specifically as a
   *drop-in* replacement: it is built and validated for dense 384×384 radar-grid sequences, a
   fundamentally different spatial data regime than this project's sparse 992-cell, 1.0° grid.
   Reusing its ideas (cuboid space-time attention) in a custom-built, project-scaled architecture
   is a legitimate future research direction; treating the existing checkpoint/repo as something
   that can be fine-tuned onto this project's grid with modest effort is not supported by evidence
   and is rejected.

5. **Dartmouth Flood Observatory (DFO) as a primary FF label source** — DFO's own methodology mixes
   news/government reports with satellite confirmation; this does not clear the project's own
   stated bar of skepticism toward "anything inferring flood from... social reports." Reject as a
   primary label; it was not even promoted to SUPPLEMENTARY given INDOFLOODS and the Global Flood
   Database are both stronger label sources already catalogued.

6. **Any external multimodal+MTL weather repo as a wholesale import** — none found in this search
   that targets this project's specific combination of coarse grid + heterogeneous modalities +
   PU-labeled FF + three correlated hazard heads. Reject the idea of importing one; the existing
   in-repo `backend/mtl_backbone.py`, trained with the newly catalogued ERA5/DEM/HydroSHEDS data,
   remains the more defensible path.

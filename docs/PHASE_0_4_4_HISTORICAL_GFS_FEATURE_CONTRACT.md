# Phase 0.4.4 — Historical GFS Feature Contract (pre-dataset-build semantic resolution)

This phase resolves (or explicitly fails to resolve, where the evidence does
not permit it) the three semantic blockers carried forward from Phase
0.4.3B, and separately investigates the stale `data/pan_india_grid.json`
artifact. No data was downloaded, no training occurred, no production code
was changed. All GRIB facts below were re-verified this phase by direct
ecCodes inspection of the two real files already on disk from Phase 0.4.3A
(`data/external/historical_gfs/raw/gfs.0p25.2020071500.f003.grib2`,
`...f006.grib2`); nothing is carried over unverified from prior phases'
written reports.

## PART 1 — Precipitation / QPE forensic (traced from actual code)

**Workflow → code path, confirmed by direct file read:**

`.github/workflows/update_grid.yml` line 65: `run: python backend/pipeline.py`
— no `--fhour` argument. `backend/pipeline.py` line 543:
`def run(cycle_override: str = None, fhour: int = 0)`, and its
`if __name__ == "__main__":` block (line 807-813) defines
`parser.add_argument("--fhour", type=int, default=0, ...)` then calls
`run(cycle_override=args.cycle, fhour=args.fhour)`. **There is no code path
by which the production workflow invocation can run with any `fhour` other
than 0.** This is now confirmed directly from the argparse definition and
the workflow's literal invocation line, not inferred.

`build_nomads_url(date_str, cycle, fhour)` (line 133) builds
`file=gfs.t{cycle}z.pgrb2.0p25.f000` when `fhour=0`. This requests an
**f000** NOMADS subset — GFS's 0-hour field for that cycle, i.e. an
analysis-equivalent field where the forecast valid time equals the
initialization time.

**Precipitation field selection** (line 603-609):
```python
apcp_arr = field(
    "tp_surface", "tp_surface_0",
    "APCP_surface", "acpcp_surface",
    "asnow_surface",  # fallback -- not ideal but avoids None
)
```
`field()` (line 585) returns the first key present in the `fields` dict built
by `read_grib_fields()`, which keys cfgrib variables as
`f"{var}_{level_type}_{level}"` or `f"{var}_{level_type}"` (no level suffix
when the field has no vertical-level dimension, which is the case for a
single surface-layer field). So the **first-choice, actually-matching key in
practice is `tp_surface`** — i.e. production's QPE proxy is GFS's `tp`
(total precipitation) shortName at `typeOfLevel=surface`, read via cfgrib.
This is the same shortName independently confirmed present in the historical
archive (Section below) — so the **field identity** (tp @ surface) is
consistent between live and historical sources. The `acpcp_surface`
(convective-only) and `asnow_surface` (snow depth — not precipitation at
all) entries are same-call fallbacks only reached if `tp` is absent; this
fallback chain is a defensive convenience, not a documented, scientifically
equivalent substitute (snow depth in particular is a different physical
quantity).

**Reference/valid time and accumulation window at f000** (verified from GRIB
accumulation semantics, not from an actual downloaded f000 file — this
sandbox does not have one, and none was downloaded this phase per the
brief's "do not download" constraint): a GRIB accumulated field's window is
`[startStep, endStep]` relative to the reference (initialization) time. At
`fhour=0`, `validityTime == dataTime` (reference time), so any
accumulation-type field requested at f000 necessarily has `startStep=endStep=0`
— a zero-width window. NCEP's own GFS APCP convention (consistent with what
this phase independently observed in the real f003/f006 files, which both
anchor their window start at step 0 — see below) accumulates from the start
of the forecast, so an f000 request's `tp` field, if present at all, carries
no accumulated precipitation by construction — **this is a structural fact
about GRIB accumulated fields, not a quirk of this one archive**, and it
directly supports (without re-downloading anything to re-prove it) the
pipeline's own defensive code: *"APCP can be negative (artifact) -- floor at
0"* (line 669-671), which reads as handling exactly this near-always-zero
case. **VERIFIED FROM CODE + GRIB ACCUMULATION SEMANTICS, NOT FROM AN ACTUAL
DOWNLOADED f000 GRIB FILE** (none exists in this repository to inspect) —
flagged honestly as the one sub-claim in this section that rests on
GRIB-convention reasoning rather than a byte-for-byte file read.

**Downstream use**: `qpe_mm = extract_point(apcp_arr, li, lj)`, floored at 0,
fed into `hazard_probabilities(..., qpe_mm=qpe_mm)` where it contributes
`min(1.0, qpe_mm/50.0)*0.07` to cloudburst score and
`min(1.0, qpe_mm/30.0)*0.10` to flash-flood score (lines 487-488, 505-506).
Given the above, in production's actual current configuration this term is
very likely contributing ~0 to both scores on essentially every run — a real
finding about the live system's actual behavior, not a hypothetical.

### Historical archive: `tp`, `acpcp`, `prate` — re-verified this phase

| Field | File | shortName | typeOfLevel | stepType | stepRange | units | Meaning |
|---|---|---|---|---|---|---|---|
| Total precip | f003 | `tp` | surface | accum | `0-3` | kg/m² | Precipitation accumulated from forecast start (0h) through valid time (3h) |
| Total precip | f006 | `tp` | surface | accum | `0-6` | kg/m² | Accumulated from 0h through 6h |
| Convective precip | f003 | `acpcp` | surface | accum | `0-3` | kg/m² | Convective-only component of the above, same window |
| Convective precip | f006 | `acpcp` | surface | accum | `0-6` | kg/m² | Convective-only, 0-6h |
| Precip rate (instantaneous) | f003 | `prate` | surface | instant | `3` | kg/m²/s | Rate at the exact valid time (3h), not an accumulation |
| Precip rate (average) | f003 | `prate` | surface | avg | `0-3` | kg/m²/s | Time-averaged rate over 0-3h |
| Precip rate (instantaneous) | f006 | `prate` | surface | instant | `6` | kg/m²/s | Rate at 6h |
| Precip rate (average) | f006 | `prate` | surface | avg | `0-6` | kg/m²/s | Time-averaged rate over 0-6h |

`kg/m²` of accumulated water is numerically equivalent to mm of liquid-depth
precipitation (same convention production already uses for `qpe_mm`), so no
unit conversion is needed for `tp`/`acpcp`. `prate`'s units (kg/m²/s) are a
*rate*, not an accumulation — multiplying by the window length (in seconds)
recovers an equivalent accumulation, but this is a different physical
quantity class than `tp` and should not be substituted for it without that
conversion.

**`tp` is NOT automatically equal to `qpe_mm`** — the field identity matches
(`tp @ surface`, same shortName production actually resolves to), but the
*quantity* `qpe_mm` currently carries in live production (a near-zero f000
value, per above) is not the same number as a 3h or 6h historical `tp`
accumulation. Treating `tp(f006)` as literally "the same `qpe_mm` production
would have produced" would be a factual misstatement about the live system.

## PART 2 — Determining a legitimate historical precipitation feature

**Testing `tp(f006) - tp(f003)` as the 3-6h interval precipitation:**

Both messages share the same reference (initialization) time
(`2020-07-15 00:00 UTC`, confirmed identical `dataDate`/`dataTime` in both
files), and both windows start at `startStep=0` (confirmed directly above —
`0-3` and `0-6`, not e.g. `3-6` and `0-6`). Because GFS's `tp` accumulation
is defined as a running total **from the start of the forecast** (step 0) —
confirmed here by the fact that neither file's `tp` message has a non-zero
`startStep` — the two values are accumulations over nested windows sharing
the same origin: `tp(f006)` = total precip over `[0,6]`, `tp(f003)` = total
precip over `[0,3]`. Standard accumulation arithmetic therefore makes
`tp(f006) - tp(f003)` = precipitation accumulated over the disjoint interval
`(3,6]`, **without requiring any additional assumption beyond what was
directly measured in the GRIB metadata** (both windows start at step 0, one
init, one model run). This is **valid** for this specific pair.

Caveat genuinely worth stating: this validity rests on there being no
accumulation reset between f003 and f006 in GFS's `pgrb2.0p25` product family
— confirmed here only by observing that both windows start at 0 (if GFS reset
accumulators at some interval shorter than 6h, the f006 message would show a
`startStep` other than 0; it does not). This is evidence from the two files
actually in hand, not a general claim verified against NCEP's full product
documentation (which this sandbox cannot fetch to cross-check beyond what
Phase 0.4.2's WebFetch-sourced research already covered).

**Resulting construction**:
- Initialization: 2020-07-15 00:00 UTC
- Lead pair: f003 (valid 03:00 UTC) and f006 (valid 06:00 UTC)
- Accumulation windows: `[0,3]` and `[0,6]` hours respectively
- Difference: `tp(f006) − tp(f003)` = precipitation accumulated during
  `(03:00, 06:00]` UTC — a genuine 3-hour interval total, valid at 06:00 UTC
  with a 3-hour reference window ending at the f006 lead.

**Comparing against current `qpe_mm`**: this differenced quantity is a real,
well-defined 3-hour interval accumulation — but it is **not** what
production's `qpe_mm` currently is (a near-zero f000 value, Part 1). It is
closer in spirit to what the pipeline's own docstring *describes* ("6-hour
accumulated precipitation") than to what the pipeline's *actual invocation*
produces. Classification: **REPLACE** — this is a scientifically sound
precipitation predictor, but adopting it means redefining what "QPE feature"
means for this project (a product decision), not reproducing today's
`qpe_mm` number as-is. Possibilities A (native accumulation at one lead) and
C (precip rate) are both also physically valid fields but represent
*cumulative-since-init* or *instantaneous/period-average rate* quantities
respectively, not an interval total; B (the difference method above) is the
option that most directly answers "how much rain fell in this specific
window," which is what a nowcasting-relevant QPE feature should represent.

## PART 3 — K-Index / Totals-Totals

**Production formulas** (`backend/pipeline.py` lines 383-394, traced
directly):
- `K-Index = (T850 − T500) + Td850 − (T700 − Td700)`
- `Totals-Totals = (T850 + Td850) − 2·T500`

Required inputs: **T850, T700, T500, Td850, Td700** (five scalars; T500's
own dewpoint, Td500, is NOT used by either formula as currently coded).

**Historical archive, re-verified this phase by direct enumeration**:
- `t` (temperature) @ `isobaricInhPa` confirmed present at levels 850, 700,
  500 (and many others) in both files.
- `r` (relative humidity) @ `isobaricInhPa` confirmed present at levels 850,
  700, 500 (and many others) in both files.
- **No `d` or `dpt` shortName exists at `isobaricInhPa` for any level in
  either file** — re-confirmed this phase by enumerating the complete
  shortName set present in the file (88 distinct shortNames; `d`/`dpt` is
  absent from that list entirely). The only dewpoint-adjacent field anywhere
  in the file is `2d` (2 m dewpoint, `heightAboveGround`), which is a
  surface-level quantity, not a pressure-level one, and is not substitutable
  for `Td850`/`Td700` without a vertical extrapolation this report does not
  attempt to justify.

**Is dewpoint derivable from T + RH at 850/700 hPa?** Yes, in principle — RH
and T are both directly available at exactly the levels needed. The standard
approach: compute actual vapor pressure `e = (RH/100) · e_s(T)` using a
saturation-vapor-pressure formula (e.g. Magnus-Tetens:
`e_s(T) = 6.112 · exp(17.67·T_C / (T_C + 243.5))`, T_C in °C, e_s in hPa),
then invert the same Magnus-Tetens form to solve for dewpoint:
`Td_C = (243.5 · ln(e/6.112)) / (17.67 − ln(e/6.112))`.

- **Formula**: Magnus-Tetens (or an equivalent, e.g. Bolton 1980's more
  precise coefficients) applied to T (converted to °C) and RH (%) at the
  same pressure level.
- **Required inputs**: `T850`, `RH850` (both confirmed present);
  `T700`, `RH700` (both confirmed present).
- **Historical availability**: both inputs for both levels are
  DIRECTLY_PRESENT per the GRIB inventory above.
- **Units**: output in °C (or K after conversion), matching what production
  already does with its direct dewpoint lookups (`Td850`/`Td700` are used
  in Kelvin-consistent arithmetic in the K-Index/TT formulas — production
  does not explicitly convert these inputs' units beyond what the raw GRIB
  field provides, so a derived Td would need to be produced in the same unit
  convention, i.e. Kelvin, to be a drop-in substitute).
- **Edge cases**: Magnus-Tetens is an approximation (typical error ~±0.1-0.3°C
  in the meteorologically common range, larger error at extreme cold
  temperatures or very low RH where the formula's assumptions weaken); RH
  values exactly at or reported above 100% (supersaturation artifacts,
  possible in raw NWP output) would need clamping before taking a log of a
  ratio ≥1 is avoided (e/e_s computed from RH≤100 is safe, but RH slightly
  >100 in the raw field would need a pre-clamp); RH=0 would make `e=0` and
  the log undefined, requiring a guard.
- **Would this preserve production's K/TT semantics?** Partially: the
  *formula* for K-Index/Totals-Totals itself would be applied unchanged, so
  the arithmetic definition is preserved exactly. But the *dewpoint inputs*
  would no longer be a direct NWP model output — they would be a derived
  approximation never produced or validated by GFS itself, introducing a
  new source of error (the Magnus-Tetens approximation error) that
  production's current live pipeline does not have (its direct `d`/`dpt`
  lookup, when it succeeds, is the model's own dewpoint field). This is a
  genuine semantic shift, not a transparent substitution, even though the
  downstream formula stays the same.

**Classification: DERIVABLE** (not EXACT_MATCH — the derivation introduces a
new approximation; not NOT_AVAILABLE — the direct path is unavailable but a
sound alternate construction exists; not UNRESOLVED — the derivation path is
fully specified and classifiable, even though this phase does not implement
it per the brief's "do not implement the derivation yet" instruction).

## PART 4 — CTT temporal tendency

**Production CTT** (`compute_ctt()`, lines 404-418): for each grid point,
scans pressure levels 250, 300, 400, 500, 700 hPa (in that order) for the
first one where GFS relative humidity ≥ 80%, and returns that level's GFS
temperature (converted to °C) as a cloud-top-temperature *proxy*. **This uses
only GFS pressure-level RH and T fields from the single current run's
GRIB file — no satellite data, and no second forecast lead.** The repository
does separately maintain real Himawari-satellite-based CTT infrastructure
(`fetch_himawari_realtime.py`, `data/himawari_realtime.json`,
`data/himawari_history.json`), but `backend/pipeline.py`'s `ctt_c` field is
**not** wired to that satellite source — it is a pure GFS-derived proxy,
confirmed by `compute_ctt()`'s signature taking only the GRIB `fields` dict
as input.

**Production CTT drop rate** (lines 553-561, 655-663, and `load_prev_ctt_map`
at lines 174-191): each run of `backend/pipeline.py` reads
`data/ctt_grid.json`, which was written by the **previous invocation of this
same script** (a different, earlier GFS *initialization cycle* — since every
invocation always requests f000 per Part 1, two successive runs are two
different analysis-equivalent snapshots, not two leads of one cycle). The
drop rate is `(prev_ctt − ctt_c) / prev_age_h`, gated by
`CTT_PREV_MAX_AGE_HOURS = 7.0` (must be ≤7h old to be used). **This is
therefore cross-cycle temporal evolution between two distinct
initializations, not a within-cycle lead-to-lead forecast difference, and
not an observation-to-observation satellite tendency.**

**Comparing the three candidate reproduction methods**:
- **A. f003 vs f006 from the same 00Z cycle** — this is a **forecast lead
  difference**: it describes how one single forecast trajectory evolves
  between two lead times of one model run, holding the initialization fixed.
  It answers "what does the model expect to happen by +6h," not "what
  actually changed between two successive analyses." **This is NOT the same
  quantity production computes.**
- **B. The same lead from two consecutive initialization cycles** (e.g.
  `...2020071418.f000` and `...2020071500.f000`, 6 hours apart, matching
  production's own f000-only, cross-cycle cadence) — **this is the correct
  historical analog of production's actual CTT-drop-rate computation**: two
  independent snapshots of the (model-estimated) atmospheric state, 6 hours
  apart, exactly mirroring `load_prev_ctt_map`'s own temporal structure.
- **C. Actual satellite observations** (Himawari CTT, present elsewhere in
  this repository) — this would be a genuine **observation-to-observation
  tendency**, a different and arguably more physically direct signal than
  either A or B, but it is **not what production's current `ctt_drop_rate_c_hr`
  feature measures** (per the code trace above, that feature is GFS-proxy-only).
  Using satellite CTT tendency as a historical substitute would be
  introducing a new, better-grounded feature, not reproducing the existing
  one's semantics.

**No CTT drop-rate feature should be built from f003/f006 alone** — doing so
would conflate a forecast-lead difference with the cross-cycle temporal
evolution production's own code actually computes, which is exactly the kind
of mislabeling ("calling a lead difference a temporal observation") this
project's standing instructions prohibit. Reproducing this feature correctly
requires downloading **at least two separate initialization cycles** (method
B above) — not attempted this phase (no downloads were performed, per the
brief).

## PART 5 — Stale `data/pan_india_grid.json`: dependency trace

**Every repository reference found** (`grep -rln "pan_india_grid.json"`,
excluding tests):

| File | Role |
|---|---|
| `backend/pipeline.py` | **Sole current writer** (per `docs/PIPELINE_OWNERSHIP.md`, confirmed by workflow wiring in Phase 0.4.3B) |
| `.github/workflows/update_grid.yml` | Invokes the writer, 4x/day |
| `pan_india_gfs_fetcher.py` (root) | **No longer writes here** — writes `pan_india_grid_slotrun.json` instead (post-Phase-4.5 fix, confirmed by reading `OUT_PATH` at line 48) |
| `dev/pan_india_gfs_fetcher.py` | A **dev-tree copy that still targets `pan_india_grid.json` directly** and still computes its own independent hazard-probability formulas (`compute_thunderstorm_probability`, `compute_cloudburst_probability`, distinct from `backend/pipeline.py`'s `hazard_probabilities()`) — confirmed **not referenced by any `.github/workflows/*.yml`** (grepped all workflow files; only the root-level `pan_india_gfs_fetcher.py` and `backend/pipeline.py` are invoked), so this is dead/legacy code from this project's perspective, not a live writer. |
| `backend/dispatch_alerts.py` | **Reader** — SMS alerting; reads `grid.get("grid_cells", [])` and `c.get("thunderstorm_probability"/"cloudburst_probability"/"flash_flood_probability", 0)`. These exact keys ARE present in `backend/pipeline.py`'s current output schema, so this reader is schema-compatible with the *current* writer. |
| `backend/mtl_backbone.py` | **Reader** (model inference input), `--input data/pan_india_grid.json` default |
| `generate_location_bundle.py` | **Reader** |
| `location_engine.py` | **Reader**, and — importantly — **already contains an explicit, documented transitional fallback** for exactly this staleness (see below) |
| `canonical_forecast_writer.py` | **Reader**, documents `pan_india_grid.json` as "written by backend/pipeline.py (via update_grid.yml)" |
| `scripts/map_indofloods_to_grid.py`, `scripts/build_panindia_dataset.py`, `atomic_write.py` | Reference it only in comments/docstrings, not as active read/write targets in the code paths inspected |

**The codebase already knows about and handles this exact situation.**
`location_engine.py` lines 166-182 contain this comment and code, found
verbatim this phase:

> "Field names below use .get() with a fallback to the older on-disk schema
> (pwat/apcp_mm) where the new fields (ctt_c/terrain/convergence_s/
> ctt_drop_rate_c_hr/qpe_mm/flash_flood_probability_terrain_adjusted) don't
> exist yet because the file predates this turn's backend/pipeline.py change
> and hasn't been regenerated by a live pipeline run. Genuinely absent
> fields stay None -- never backfilled with a guess."

with code `"pwat_mm": c.get("pwat_mm", c.get("pwat"))` and
`"qpe_mm": c.get("qpe_mm", c.get("apcp_mm"))`. **This confirms, in the
repository's own prior work, exactly the same finding Phase 0.4.3B surfaced
independently**: the on-disk file predates the `backend/pipeline.py` schema
change and has not been regenerated in this environment. This is a known,
already-mitigated transitional condition, not a silent, undiscovered bug —
`location_engine.py`'s consumers get `None` for genuinely-new fields rather
than a fabricated value, which is the correct defensive behavior.

**Why the file is actually stale, confirmed from its own metadata**: reading
`data/pan_india_grid.json` directly, `generated_at_utc = "2026-09-30T11:23:22Z"`,
`gfs_fhour = 6` — i.e. this specific on-disk file was last written by the
**root** `pan_india_gfs_fetcher.py` running at `f006` (that script's own
hazard-probability formula, confirmed present in `pan_india_gfs_fetcher.py`
lines 233-396: `compute_thunderstorm_probability`, `compute_cloudburst_probability`
— a different formula from `backend/pipeline.py`'s `hazard_probabilities()`),
**before** the Phase 4.5 (dated 2026-09-30) redirect took effect, or from a
run that predates this sandbox checkout. Since `backend/pipeline.py` needs
live NOMADS network access to regenerate this file (confirmed blocked in
this sandbox across every prior phase), **this specific checkout cannot
self-heal this file** — the real, deployed GitHub Actions environment (with
real network access, running `update_grid.yml` 4x/day) almost certainly has
already overwritten it with `backend/pipeline.py`'s current schema many times
since Sept 30, 2026. **This is most plausibly a staleness artifact of this
particular sandboxed checkout/snapshot, not necessarily a live bug in the
deployed system** — this report does not have access to the live GitHub
repository's current `data/pan_india_grid.json` to confirm that directly,
and says so rather than assuming either way.

**Dependency conclusion**:
- **Does production currently depend on this file's stale content?** No
  evidence that it is broken in deployment — `dispatch_alerts.py`'s exact
  key lookups match the *current* `backend/pipeline.py` schema, and
  `location_engine.py` already has an explicit, correct fallback for the
  stale-schema case. The risk, if this checkout's file genuinely reflects
  live production for any length of time, is silent threshold-alert
  suppression in `dispatch_alerts.py` (keys default to `0`, never crashing,
  but also never firing) — not a crash, a quiet no-op.
- **Should it be archived/removed?** Not in this phase (explicitly out of
  scope — "DO NOT modify/delete it in this phase"). A future phase's
  decision should be: either (a) trigger a live `backend/pipeline.py` run to
  regenerate it with real current data (requires real network access this
  sandbox doesn't have), or (b) if confirmed stale in the actual deployed
  repo too, treat it as a one-time manual backfill/regeneration task — not
  a recurring data-engineering problem, since `update_grid.yml` already owns
  regeneration going forward.
- **Is `dev/pan_india_gfs_fetcher.py` a live risk?** No — confirmed not
  wired into any workflow. It is dead code from a production standpoint but
  was not touched or flagged for removal this phase (out of scope).

## PART 6 — Historical feature contract

| Feature | Production semantics | Historical source | Reconstruction method | Exactness | Decision |
|---|---|---|---|---|---|
| `cape` | `cape` @ surface, J/kg, direct NOMADS fetch | `cape` @ surface, J/kg | Direct read | Exact | **DIRECT** |
| `cin` | `cin` @ surface, J/kg | `cin` @ surface, J/kg | Direct read | Exact | **DIRECT** |
| `pwat_mm` | `pwat` @ atmosphereSingleLayer, kg/m² ≡ mm | `pwat` @ atmosphereSingleLayer, kg/m² | Direct read | Exact | **DIRECT** |
| `wind_shear_ms` (850–200 hPa) | `sqrt((u200−u850)²+(v200−v850)²)` | `u`/`v` @ isobaricInhPa 850 & 200, both present | Same formula | Exact | **DIRECT** |
| `convergence_s` | Finite-difference divergence of 850 hPa U/V, negated, full grid | `u`/`v` @ isobaricInhPa 850, full global grid | Same formula | Exact | **DIRECT** |
| `ctt_c` | Highest of 250/300/400/500/700 hPa with RH≥80%, that level's T | `r`/`t` @ isobaricInhPa, all 5 levels present | Same formula | Exact | **DIRECT** |
| `k_index`, `totals_totals` | `(T850−T500)+Td850−(T700−Td700)`; `(T850+Td850)−2·T500` | T850/T700/T500 direct; Td850/Td700 **absent**, but T+RH present at those levels | Magnus-Tetens Td derivation from T+RH, then same formula | Approximate — formula exact, dewpoint input is a new derived approximation, not a model-native field | **DERIVE** (not implemented this phase) |
| `qpe_mm` (precipitation) | `tp` @ surface, read at whatever `fhour` is requested; production always requests f000, so the value is structurally ~0 in live operation today | `tp`/`acpcp` @ surface, accum, window = `[0,lead]`; `tp(f006)−tp(f003)` validly yields the 3–6h interval total | Lead-differencing (Part 2, option B) | Scientifically sound as an interval-accumulation feature, but **not numerically equivalent to today's live `qpe_mm`** (which is ~0 by construction) | **REPLACE** — redefine the feature's intended semantics (interval accumulation) rather than reproduce today's near-zero value |
| `ctt_drop_rate_c_hr` | `(prev_ctt − ctt_c)/prev_age_h` across two **separate initialization cycles** (cross-cycle, GFS-proxy-only, no satellite) | Requires 2 distinct cycles' `ctt_c`, not available from any single cycle's f003/f006 pair | Download ≥2 cycles at matching lead, apply same formula | Exact formula, but **input data not yet acquired** | **BLOCKED** (pending a multi-cycle download, out of scope this phase) |
| `thunderstorm_probability`, `cloudburst_probability`, `flash_flood_probability*` | Hand-weighted heuristic over all of the above | Depends on all of the above | N/A until inputs resolve | Inherits worst input's status | **UNRESOLVED** — cannot be honestly computed until `k_index`/`totals_totals` (DERIVE) and `qpe_mm` (REPLACE) are each actually resolved in code, and `ctt_drop_rate_c_hr` is either acquired (BLOCKED) or the formula is run with it absent (production already tolerates `None` here, so this is survivable but changes the formula's realized behavior) |
| `T850/T700/T500`, `u850/v850`, `u200/v200` (intermediates) | Direct NOMADS fetch | Direct, confirmed present | Direct read | Exact | **DIRECT** |

## PART 7 — Final gate

**1. Can historical GFS reproduce the thermodynamic core?**
Partially. CAPE, CIN, and PWAT — yes, exactly. K-Index and Totals-Totals —
only via a derived (not model-native) dewpoint, which changes their
precision characteristics even though the formula stays the same.

**2. Can historical GFS reproduce the kinematic core?**
Yes. 850–200 hPa wind shear and 850 hPa convergence are both exactly
reproducible with the same formulas and real, confirmed inputs.

**3. Can historical GFS reproduce precipitation consistently with the
production feature?**
No, not as `qpe_mm` is *currently defined in live operation* (near-zero at
f000). A scientifically meaningful precipitation feature (3h interval
accumulation via lead-differencing) IS constructible from the historical
archive, but it is a different, better-defined quantity than what today's
live pipeline actually produces — using it means replacing, not reproducing,
the live feature's realized semantics.

**4. Can K-Index and Totals-Totals be reproduced without changing their
scientific meaning?**
Not without change. The *formula* is preserved exactly, but the *dewpoint
input* would shift from a direct NWP model field to a Magnus-Tetens
approximation — a real, if typically small, source of additional error that
today's live computation does not have. This is a meaningful caveat, not a
disqualifying one.

**5. Can CTT tendency be reproduced from GFS alone?**
Yes, in principle, but not from the two files currently in hand. It requires
GFS data from **two separate initialization cycles** (not two leads of one
cycle) — a different, not-yet-performed acquisition, not a semantic
impossibility.

**6. What minimum additional data/source is required for anything that
cannot be reproduced today?**
- CTT tendency: one additional historical GFS cycle (e.g. the 18Z run
  preceding the 00Z run already downloaded), at a matching lead.
- K-Index/Totals-Totals at full model-native precision: would require a
  historical archive that actually carries isobaric dewpoint (not found in
  d084001's f003/f006 files) — absent that, the Magnus-Tetens derivation
  above is the best available substitute, with its stated approximation
  caveat.
- A live-equivalent `qpe_mm`: requires production itself to be re-scoped to
  request a non-zero-window lead (e.g. f006) rather than f000 — a product
  decision, not a historical-data gap.

**7. What features should be excluded from the first historical pilot?**
- `ctt_drop_rate_c_hr` (BLOCKED — no multi-cycle data acquired yet).
- The full hazard-probability outputs as a direct label/feature
  (`thunderstorm_probability` etc.) — these are a heuristic formula, not an
  observed ground truth, and this phase's own findings mean a chunk of their
  inputs would currently be `None` or derived-approximate, further weakening
  any claim that a reproduced probability "matches" production's.
- `qpe_mm` in its literal current-production form (it would just be ~0
  everywhere and contribute no signal) — if precipitation is wanted, the
  Part 2 interval-difference construction should be used explicitly and
  labeled as a new, re-scoped feature, not as "the same qpe_mm."

**8. What features are safe to include in the first historical pilot?**
`cape`, `cin`, `pwat_mm`, `wind_shear_ms` (850–200 hPa), `convergence_s`,
`ctt_c` (the single-cycle RH-threshold proxy, not its drop rate) — all six
are DIRECT, exactly reproducible, real fields with no approximation or
missing-data caveat. `k_index`/`totals_totals` may be *cautiously* included
if the Magnus-Tetens derivation is implemented and its approximation is
explicitly documented as such wherever the resulting training data is used
(not implemented this phase).

### FINAL STATUS: **BLOCKED_PENDING_SEMANTIC_DECISION**

Not because the archive lacks usable data — six of the ten traced
features are exactly reproducible today — but because three real product
decisions remain genuinely open and this phase is not authorized to make
them unilaterally: (1) whether K-Index/Totals-Totals should be computed from
a new, approximate dewpoint derivation or dropped from the first pilot;
(2) what precipitation should mean in a historical training context, given
that live production's current `qpe_mm` is structurally near-zero and not a
meaningful reproduction target; and (3) whether CTT tendency is worth
acquiring a second historical cycle for, given it requires new data this
phase did not fetch. Proceeding to full historical dataset construction
before these three decisions are made would risk silently baking in one
specific, unstated resolution of each (e.g. quietly dropping K-Index, or
quietly treating a lead-difference as "the same" qpe_mm) rather than making
that choice visibly and deliberately.

## Testing / safety

- Full existing suite re-run this phase:
  `pytest . --ignore=test_himawari.py --ignore=test_nomads.py
  --ignore=test_segments.py --ignore=test_segments_v2.py` →
  **151 passed**, identical to the established baseline, same 4
  pre-existing environment-only collection errors (donfig ×3, NOMADS 403 ×1),
  nothing new broken, no freshness test weakened or touched.
- No new mapping/derivation code was added this phase (the Magnus-Tetens
  derivation is specified, not implemented, per the brief's "do not
  implement the derivation yet"), so no new focused unit tests were created
  — there is no new logic to test yet. All factual claims in this report
  were verified by direct, reproducible read-only inspection (ecCodes
  message enumeration, `grep`/`Read` of actual source files and workflow
  YAML) rather than by new code paths.

## Scope confirmation

- **Files created this phase**: this report only.
- **Files modified**: none.
- **No files were downloaded.** `data/pan_india_grid.json` was read-only
  inspected, not modified or deleted, per the brief.
- No production code, model file, schema, workflow, or frontend file was
  changed. No training occurred. No historical dataset was built. Nothing
  was deployed, committed, or pushed.

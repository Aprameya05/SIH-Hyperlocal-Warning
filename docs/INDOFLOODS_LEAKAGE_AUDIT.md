# INDOFLOODS Leakage Audit — Phase 5

Classification key: **OBSERVED** (an actually-measured outcome of a flood
event — not a predictor), **PROXY** (correlates with flood risk/impact but
is not itself a physical hazard driver), **STATIC SUSCEPTIBILITY** (a
plausible time-invariant predictor of flash-flood likelihood), **NOT
SUITABLE** (should not enter a flash-flood label or feature table as
currently structured).

## Precipitation (`precipitation_variables_indofloods.csv`, T1d-T10d)

Verified from the PDF (`variables_description_indofloods.pdf`, Table 2,
"Event-scale precipitation"), not assumed:

- `T1d` = "Daily precipitation **a day before** the flood start date."
- `T2d`-`T10d` = "Cumulative 2-10 days daily precipitation **before** the
  flood start date."

**These are strictly antecedent/pre-event.** None of the `Txd` columns
include the flood event's own start-day rainfall or any rainfall during the
event window. Classification: **STATIC SUSCEPTIBILITY is wrong for these —
they are event-anchored OBSERVED-antecedent predictors**, safe to use as
predictors of that specific event's onset (they predate `Start Date` by
construction), but they exist **only for the 4,548 already-known flood
event dates**, computed as a catchment-area average from a gridded
precipitation product (EM-Earth, 0.1 deg, Tang et al. 2022) — not as a
continuous per-cell-per-day series.

**Leakage risk**: none from future information (verified: all Txd windows
end strictly before `Start Date`), but a real risk of **selection/label
leakage** if used naively: these rows exist precisely because a flood
event happened, so simply pairing (Txd, "flood occurred") without an
equal population of (Txd, "no flood occurred") non-event days/cells would
train on a dataset with no negative examples — the mere presence of a
`Txd` row already implies an event.

## Flood event fields (`floodevents_indofloods.csv`)

| Field | Classification | Reason |
|---|---|---|
| `Start Date`, `End Date` | OBSERVED | Directly define the event window — this *is* the label, not a predictor. |
| `Peak Flood Level (m)`, `Peak FL Date`, `Num Peak FL`, `Peak Discharge Q (cumec)`, `Peak Discharge Date`, `Flood Volume (cumec)`, `Event Duration (days)`, `Time to Peak (days)`, `Recession Time (day)` | OBSERVED / NOT SUITABLE as predictors | All are computed *from* the completed event (they need the event's full timeline, including its end and peak, to exist). Using any of these as an input feature to "predict" the same event is direct temporal leakage — they are post-hoc summaries of the very thing being predicted. |
| `Flood Type` | OBSERVED / NOT SUITABLE as predictor | Per the PDF, derived deterministically from `Peak Flood Level` vs. the gauge's `Danger Level` — it is a relabeling of `Peak Flood Level`, itself an outcome field. |

**None of the `floodevents_indofloods.csv` columns beyond `EventID`/gauge
linkage and `Start Date` (as the label anchor) should ever enter a
predictor feature table.** `Start Date` itself is the timestamp that
defines the positive-label day; it is the target's own metadata, not a
feature.

## Catchment characteristics (`catchment_characteristics_indofloods.csv`)

| Group | Classification |
|---|---|
| Stream-order topology, morphometric indices, drainage-density/intensity indices (see `docs/INDOFLOODS_FEATURE_CATALOG.md`) | STATIC SUSCEPTIBILITY |
| Climate normals (Bioclim temperature/precipitation) | STATIC SUSCEPTIBILITY (weak-to-moderate; long-run climatology, not event weather) |
| `Land cover`, `Soil type`, `lithology type` | STATIC SUSCEPTIBILITY |
| GDP/GDP-per-capita (all years), HDI (all years), `Night Light`, `Population Count`/`Density`, `Road Density`, `Urban percentage` | PROXY (exposure/impact, not hazard likelihood) — flagged explicitly not to be used as hazard-probability features; usable only in a separate impact/exposure layer if one is ever built |

No leakage risk from these (they are static and independent of any
specific flood event's outcome), but a **selection-bias risk**: catchment
characteristics exist only for the 155 gauges that also have flood events
recorded, i.e. gauges INDOFLOODS chose to instrument and that experienced
at least one qualifying flood — using them to build a general susceptibility
model risks learning "catchments India chose to gauge and that flooded"
rather than "catchments prone to flash flooding" in general (the other 59
metadata-only gauges have no catchment attributes to compare against).

## Metadata (`metadata_indofloods.csv`)

| Field | Classification |
|---|---|
| `Latitude`, `Longitude` | Not a predictor — spatial join key only |
| `Warning Level`, `Danger Level` | NOT SUITABLE as predictors — these define what counts as a "flood" at that gauge; using them as features would let the model see the very threshold used to construct the label |
| `Catchment Area`, `Source Catchment Area`, `Area variation (%)` | STATIC SUSCEPTIBILITY (area affects runoff concentration time) — but note `Catchment Area` here may already duplicate `Drainage Area` in the characteristics file; not cross-checked numerically in this phase |
| `River Name/...`, `Basin`, `State`, `Station`, `Reliability`, `Privacy`, `Level_Entries`, `Streamflow_Entries`, `Start_date`, `End_date` | Identifiers/metadata, NOT SUITABLE as hazard-susceptibility predictors |

## Can INDOFLOODS currently support (cell + timestamp + observed flash
flood) without using future information?

**Not as a complete, general-purpose label table — stated plainly.**

What exists:
- A real per-gauge observed-event history (4,548 events, 155 gauges, real
  dates, real severity classification) that can be joined to a grid cell
  (`processed/indofloods/indofloods_grid_events.csv`, this phase's
  deliverable) to get (cell, event start date, "flood occurred") positive
  examples for **75 distinct grid cells** (out of 992).
- Genuine antecedent precipitation for exactly those 4,548 event dates.

What's missing before it can support a defensible (cell, timestamp,
observed flash-flood) label table for training:
1. **No per-cell-per-day precipitation time series.** `Txd` values exist
   only anchored to known flood-event start dates — there is no INDOFLOODS
   data for "how much did it rain in cell X on an arbitrary day," which is
   required to construct negative (non-flood) examples on a comparable
   basis to the positive examples.
2. **No negative-class days/cells.** Every row in
   `floodevents_indofloods.csv` is, by construction, a positive
   ("flood occurred") record. Without a corresponding non-event day
   distribution (same cells, same gauges, days precipitation occurred but
   no flood was recorded), any model trained directly on this table would
   have no true negatives and cannot learn discrimination.
3. **Coarse-to-fine mismatch.** A single grid cell (~10,000-12,000 km^2 at
   these latitudes) can contain multiple gauges (up to 10, at cell
   `9.0_76.0` — see Section "mapping statistics" below) or none. A cell
   with multiple gauges/events on different dates would need an explicit
   aggregation rule (e.g. "did ANY gauge in this cell flood on this day")
   that this phase does not define, since defining it is itself a labeling
   decision Phase 5 is scoped to avoid.
4. **Gauge-point vs. catchment mismatch** (see forensic inventory Section
   4): for 89 of 155 gauges (57.4%), the catchment's geometric centroid
   falls in a different grid cell than the gauge point. Any catchment-wide
   variable (antecedent precipitation, catchment characteristics)
   attributed to the gauge's cell may not represent that cell's own
   conditions particularly well for those catchments.
5. **59 of 214 gauges have no catchment characteristics or flood-event
   history at all** — they can be geolocated but not used for either
   susceptibility features or a label.

**Bottom line**: INDOFLOODS supports positives (real, dated, geolocated
flash-flood/flood occurrences, mapped to 75 of 992 cells) and a genuine
static susceptibility feature set (for 155 of 992 cells' worth of
catchments), but does **not**, on its own, supply the negative examples or
the continuous per-cell precipitation series a defensible supervised label
table would need. It is well suited to *validating* or *cross-checking* the
existing pipeline's hazard outputs at the 75 covered cells/known event
dates, and to enriching a susceptibility layer at the 155 covered
catchments — not to directly training a new flash-flood probability model
by itself.

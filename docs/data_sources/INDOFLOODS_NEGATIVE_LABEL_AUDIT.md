# INDOFLOODS Negative-Label Audit — Phase 5.5, Part 2

Question: can a day with no recorded flood event at a gauge be treated as
a confirmed non-flood (negative label), or only as "unobserved/unknown"?

Grounded in: `pdftotext data/variables_description_indofloods.pdf` (full
re-extraction, this phase) and `data/metadata_indofloods.csv`'s
`Start_date`/`End_date`/`Level_Entries`/`Streamflow_Entries` columns,
actually loaded with `pandas` this phase.

## What the PDF says about how events are detected

Direct quotes from the extracted text (Table 1 field descriptions):

> **Start date** — "Date of the oldest record of **streamflow data
> available** based on which the flood events in INDOFLOODS are
> extracted."
>
> **End date** — "Date of the latest record of **streamflow data
> available** based on which the flood events in INDOFLOODS are
> extracted."
>
> **Level Entries** — "Number of stream level data entries **available**
> between the 'Start date' and 'End date'."
>
> **Streamflow Entries** — "Number of stream discharge data entries
> **available** between the 'Start date' and 'End date'."
>
> **Start Date** (flood event) — "Start date of the flood event - when
> the flow exceeds the flooding threshold (warning level)."

The word "available" (not "recorded daily" or "continuous") is used
consistently for the entries counts, and events are explicitly
**extracted from streamflow data**, i.e. flood events are a derived,
threshold-crossing detection layer on top of a separate, gauge-specific
streamflow record — not a data feed that itself continuously flags
flood/no-flood for every calendar day.

## What the metadata entries counts actually show (computed, not assumed)

For each of the 214 gauges, this phase computed
`span_days = (End_date - Start_date).days + 1` and compared it to
`Level_Entries` (and separately `Streamflow_Entries`):

- `ratio_level = Level_Entries / span_days`, across all 214 gauges:
  min 0.139, 25th pct 0.651, median 0.891, 75th pct 0.981, max 1.040.
- **50.5% of gauges have `ratio_level < 0.9`** (i.e. fewer level entries
  than 90% of the days in their own stated operational window).
- **17.3% of gauges have `ratio_level < 0.5`** — over half their
  operational window has no recorded level entry at all.
- Only 43.0% of gauges have `ratio_level` in [0.95, 1.05], i.e. close to
  one entry per calendar day.
- The worst cases are stark: e.g. `INDOFLOODS-gauge-485` (697-day window,
  97 entries, ratio 0.139), `INDOFLOODS-gauge-861` (17,739-day window,
  2,845 entries, ratio 0.160). These gauges plainly do **not** have a
  daily record across their stated span.
- `ratio_stream` (discharge entries) is worse on average (mean 0.623 vs.
  `ratio_level` mean 0.782), and its 25th percentile is effectively 0 —
  a large share of gauges have little to no discharge record at all
  despite an operational-window `Start_date`/`End_date`.

(Full per-gauge numbers reproducible from `data/metadata_indofloods.csv`;
see `tests/test_indofloods_phase55.py` for an automated re-check of the
headline ratios above.)

## Conclusion: Case B — unobserved/unknown, not confirmed non-flood

**INDOFLOODS does not establish continuous flood-state observation.** The
`Start_date`/`End_date` fields describe a gauge's *operational window*
during which *some* streamflow data exists, but `Level_Entries` and
`Streamflow_Entries` show that, for roughly half the gauges, actual data
coverage inside that window is well under a plausible daily-record count,
and for a meaningful fraction (17%+) it is under 50% coverage. Combined
with the PDF's own "data available" phrasing (rather than a description
of a continuous daily series) and the fact that flood events are a
derived *extraction* from whatever streamflow data exists (not a
continuous automated flood/no-flood flag), there is no basis in the
source documentation or the metadata to say that a day without a recorded
`floodevents_indofloods.csv` entry means "confirmed no flood occurred on
that day." It may equally mean the gauge simply has no streamflow record
for that day, or has a record that was never reviewed/extracted into a
flood event.

**This applies unevenly across gauges** — a minority of gauges (the ~43%
near ratio 1.0) have close to continuous coverage and a "no event" day for
*those specific gauges* is a somewhat stronger (though still not proven
here to be rigorously validated) candidate for a real non-flood day. But
there is no dataset-wide guarantee, and this phase does not attempt a
per-gauge case-by-case exception — doing so would require inspecting each
gauge's actual raw streamflow record, which the repo does not have (only
the derived event table and the entry *counts*, not the underlying
level/discharge time series itself).

**Per this phase's hard constraint and this finding: non-event
(cell, date) pairs must be treated as UNKNOWN, not as negative
(non-flood) labels, anywhere in this repository's INDOFLOODS-derived
work.** No script or document produced in Phase 5 or Phase 5.5 converts
an absence of a recorded event into a hardcoded negative label; this is
checked explicitly (grep-based) by `tests/test_indofloods_phase55.py`.

## What would move this from Case B to Case A

Access to each gauge's actual daily (or sub-daily) streamflow time series
(not just the derived event table and entry counts) would let a future
pass directly verify, per gauge and per day, whether that specific day
had a real streamflow observation below the warning level (a true
confirmed non-flood day) versus a day with no observation at all
(genuinely unknown). That raw series is not present in this repo's
INDOFLOODS files and is out of scope to fetch under this phase's
constraints.

# Phase 0.4.1 — INDOFLOODS Event-Count Forensic Reconciliation (8,342 vs 4,548)

Status: RESEARCH/FORENSIC ONLY. No project data, labels, or code were modified in this phase.

## 1. The 8,342 claim — verified directly against the primary source

Re-fetched (this phase, not reused from Phase 0.2's rate-limited attempt) via an
academia.edu-hosted copy of the peer-reviewed paper (the AMS/ametsoc.org host itself
returned HTTP 403 to direct fetch, as it did in Phase 0.3; the academia.edu mirror of
the same manuscript was reachable):

> Kuntla, S.K. & Saharia, M., "INDOFLOODS: A Comprehensive Database for Flood Events
> in India Enhanced with Catchment Attributes," *Bulletin of the American
> Meteorological Society*, Vol. 106, Issue 2 (2025).
> https://journals.ametsoc.org/view/journals/bams/106/2/BAMS-D-24-0008.1.xml

Exact figures quoted directly from the paper text:

- **8,342 total flood events** = **5,525 "Flood"** + **2,817 "Severe Flood"**.
- **214 gauge stations**.
- Temporal coverage: **1959–2020 (62 years)**.
- Event definition (quoted): *"Whenever the water level (stage) of the stream reaches
  or exceeds the 'warning level,' we consider it as the start date of a flood event.
  When the water drops below the same threshold, that day is regarded as the end
  date."* Severe Flood = peak water level exceeds the "danger level"; otherwise
  "Flood." This is the same two-class definition already documented in
  `docs/INDOFLOODS_FORENSIC_INVENTORY.md` for this project's own `Flood Type` column —
  the event semantics match exactly, so the discrepancy is not a different definition
  of what counts as an event.

**Critical new finding this phase** (the one piece of evidence Phase 0.3 could not
obtain because of the 429 rate limit): the same source states explicitly —

> *"The data from transboundary basins, such as Ganga, Brahmaputra, and Indus, are
> provided only upon request."*

This is independently corroborated by the Zenodo record page itself
(https://zenodo.org/records/14584655, fetched directly this phase):

> *"This version of INDOFLOODS doesn't contain data for the Ganga and Brahmaputra
> basins."*

Both the paper (describing the full 8,342-event research corpus) and the Zenodo
landing page (describing what was actually publicly released) independently confirm
that **the public release is a restricted subset of the full corpus the paper
describes**, with Ganga/Brahmaputra/Indus transboundary-basin data withheld from
open publication pending direct author request.

## 2. The 4,548 file — traced exactly

`data/floodevents_indofloods.csv` in this repo:
- 4,548 rows, confirmed checksum-identical to the downloaded Zenodo record 14584655
  v1.0 archive (established in Phase 0.3; re-confirmed this phase by row count only,
  not re-hashed since no new download occurred).
- Breaks down as **2,919 "Flood" + 1,629 "Severe Flood"** (counted directly from the
  `Flood Type` column this phase).
- Same two-class event semantics as the paper (see above) — no event-definition
  mismatch.
- 214 metadata gauge rows in `data/metadata_indofloods.csv` — matching the paper's
  214-station count exactly. The station count is NOT reduced; only the event
  volume is.

## 3. Does the transboundary-basin exclusion fully explain the gap? — checked directly, not assumed

Ran a direct join this phase: of the 214 metadata gauges, **75 are listed under a
Ganga/Brahmaputra/Meghna-Barak basin label** (various spelling variants present in
the raw `Basin` column: "Ganga - Brahmaputra - Meghna/Barak", "Ganga - Brahmaputra -
Meghna", "Ganga - Brahmaputra -Meghna/Barak", "Ganga"). Joining those 75 gauge IDs
against `floodevents_indofloods.csv` by the `EventID` gauge prefix finds **278 event
rows already present** for those basins in the public file — i.e., the public release
is **not** a clean, total exclusion of Ganga/Brahmaputra-labeled gauges; a nontrivial
number of their events are present.

This means the transboundary-basin restriction is real and independently documented
by two authoritative sources (the paper and the Zenodo page), but it does not, by
itself, arithmetically account for the full 8,342 → 4,548 gap (a reduction of 3,794
events, versus only 278 confirmed events already present for the basins supposedly
withheld). Plausible, unverified explanations for the remainder — none confirmed this
phase — include: partial/redacted release for transboundary gauges (some of a given
gauge's events published, the rest withheld), additional non-transboundary basins
having also been reduced for the public release, or a different counting convention
between the aggregate figures quoted in the paper's abstract/text and the specific
CSV deposited on Zenodo. No evidence was found this phase to confirm or rule out any
of these; they are documented as open explanations, not conclusions.

## 4. Archive completeness check

Re-confirmed (no other files in the repo's INDOFLOODS archive contain additional
flood-event rows): `metadata_indofloods.csv` (station info, not events),
`catchment_characteristics_indofloods.csv` (static catchment attributes, not events),
`precipitation_variables_indofloods.csv` (keyed 1:1 to the same 4,548 `EventID`s
already in `floodevents_indofloods.csv`, not an independent or larger event table),
`catchments_shapefiles_indofloods.zip` (geometry, not events). No hidden or
supplementary event table was found anywhere in the downloaded archive or the
broader repo.

## 5. Final verdict

**A. 4,548 is the correct Zenodo v1.0 event corpus, and 8,342 refers to a different
(larger, pre-publication-restriction) corpus/version** — specifically, the full
research dataset described in the peer-reviewed paper, before the explicit,
independently-documented withholding of Ganga/Brahmaputra/Indus transboundary-basin
data from the public Zenodo release.

This verdict is supported with direct quotes and URLs from two independent
authoritative sources (the AMS paper and the Zenodo record itself), both stating the
same restriction in their own words. It is **not** a full, clean reconciliation: the
exact arithmetic gap (3,794 events) is larger than the 278 Ganga/Brahmaputra-gauge
events actually found in the public file, meaning the withholding was evidently
partial rather than a complete removal of those basins' events, and/or other factors
not identified this phase also contributed. The project's own 4,548-event number is
confirmed genuine, complete-as-released, and not an artifact of this project's own
processing — any further narrowing of the residual gap would require either
submitting a data request to the INDOFLOODS authors for the full/transboundary corpus,
or re-examining the paper's supplementary tables (not available via the sources
reachable from this sandbox) for the basin-by-basin breakdown.

## Sources

- [INDOFLOODS BAMS paper, academia.edu copy](https://www.academia.edu/128272843/INDOFLOODS_A_Comprehensive_Database_for_Flood_Events_in_India_Enhanced_with_Catchment_Attributes)
- [INDOFLOODS BAMS paper, journal landing page](https://journals.ametsoc.org/view/journals/bams/106/2/BAMS-D-24-0008.1.xml)
- [Zenodo record 14584655, v1.0](https://zenodo.org/records/14584655)

## Scope confirmation

No flood labels, project data files, production code, FF model, or any downstream
`processed/` artifact was modified in this phase. This is a documentation-only
forensic resolution.

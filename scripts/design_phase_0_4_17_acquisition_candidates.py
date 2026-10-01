#!/usr/bin/env python3
"""
design_phase_0_4_17_acquisition_candidates.py -- Phase 0.4.17 AUDIT/DESIGN
ONLY. Selects the next acquisition batch's candidate cycles from the real,
already-existing ts_labels.csv. Does NOT download anything, does NOT
construct a dataset, does NOT train anything.

Selection goals (documented per-candidate, not applied silently):
  - fill TRAIN years (2015-2023) that have no acquired event yet, or only
    one season represented
  - add negative coverage across years/seasons that mirror the train years
  - reserve a small HOLDOUT (2024-2025) sample for future evaluation only
    -- flagged partition=holdout and never to be used for training
  - never duplicate an already-acquired event_group_key (Phase 0.4.16)

For every candidate, the GFS cycle/lead is chosen with the exact same rule
already used and verified in Phase 0.4.13/0.4.14/0.4.15: the latest
00/06/12/18Z cycle whose native f003 and f006 leads both land inside the
target IST slot, computed via the repository's own ist_slot_for() --
never assumed from the calendar date.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_vobl_historical_gfs_ts_join import (  # noqa: E402
    ist_slot_for, event_group_key_for, IST,
)
from historical_dataset_split import assign_partition, TRAIN, HOLDOUT  # noqa: E402

CELL_ID = "IND_13.0_78.0"
LABELS_PATH = REPO_ROOT / "SIH_PANINDIA_GRID_LABELS_20260930_143541Z" / "processed" / "labels" / "ts_labels.csv"

SLOT_LABELS = {0: "0001-0600", 1: "0601-1200", 2: "1201-1800", 3: "1801-2400"}
SLOT_LABEL_TO_ID = {v: k for k, v in SLOT_LABELS.items()}

ALREADY_ACQUIRED_EVENT_GROUPS = {
    f"{CELL_ID}|2015-03-03|1", f"{CELL_ID}|2015-03-03|2",
    f"{CELL_ID}|2020-07-15|1",
    f"{CELL_ID}|2016-01-15|1", f"{CELL_ID}|2017-04-16|2",
    f"{CELL_ID}|2019-12-10|0", f"{CELL_ID}|2021-07-24|2",
    f"{CELL_ID}|2022-08-15|3", f"{CELL_ID}|2023-11-06|3",
}


def season_of(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "pre-monsoon"
    if month in (6, 7, 8, 9):
        return "monsoon"
    return "post-monsoon"


def find_cycle_and_leads(target_ist_date: str, slot_id: int):
    """Same selection rule as Phase 0.4.13: the latest 00/06/12/18Z cycle
    whose f003 and f006 both land inside the target slot, verified via
    the real ist_slot_for() -- never assumed from the date."""
    y, m, d = map(int, target_ist_date.split("-"))
    base = datetime(y, m, d, tzinfo=timezone.utc)
    best = None
    for day_offset in range(0, -3, -1):
        day = base + timedelta(days=day_offset)
        for hh in [18, 12, 6, 0]:
            init = day.replace(hour=hh)
            if init > base + timedelta(hours=23):
                continue
            leads_in_slot = []
            for lead in (3, 6):
                valid_utc = init + timedelta(hours=lead)
                valid_ist = valid_utc.astimezone(IST)
                sid, _ = ist_slot_for(valid_ist)
                if valid_ist.strftime("%Y-%m-%d") == target_ist_date and sid == slot_id:
                    leads_in_slot.append((lead, valid_utc, valid_ist))
            if len(leads_in_slot) == 2 and (best is None or init > best[0]):
                best = (init, leads_in_slot)
    return best


def load_labels():
    df = pd.read_csv(LABELS_PATH)
    sub = df[(df["cell_id"] == CELL_ID) & (df["hazard"] == "ts")].copy()
    sub["date"] = sub["timestamp"].str.slice(0, 10)
    sub["slot_label"] = sub["timestamp"].str.slice(11)
    sub["slot_id"] = sub["slot_label"].map(SLOT_LABEL_TO_ID)
    sub["year"] = sub["date"].str.slice(0, 4).astype(int)
    sub["month"] = sub["date"].str.slice(5, 7).astype(int)
    sub["season"] = sub["month"].apply(season_of)
    sub["event_group_key"] = sub.apply(
        lambda r: event_group_key_for(CELL_ID, r["date"], int(r["slot_id"])), axis=1
    )
    return sub


def select_candidates(labels: pd.DataFrame) -> list[dict]:
    available = labels[~labels["event_group_key"].isin(ALREADY_ACQUIRED_EVENT_GROUPS)].copy()
    available["partition"] = available["date"].apply(assign_partition)

    train_pos = available[(available["label_status"] == "POSITIVE") & (available["partition"] == TRAIN)]
    train_neg = available[(available["label_status"] == "NEGATIVE_CONFIRMED") & (available["partition"] == TRAIN)]
    holdout_pos = available[(available["label_status"] == "POSITIVE") & (available["partition"] == HOLDOUT)]
    holdout_neg = available[(available["label_status"] == "NEGATIVE_CONFIRMED") & (available["partition"] == HOLDOUT)]

    candidates = []
    reasons = {}

    # --- 1 new TRAIN positive per TRAIN year 2015-2023, earliest date in a
    # season not already represented by an acquired event that year if
    # possible, else earliest available ---
    already_years_seasons = {
        (2015, "pre-monsoon"), (2016, "winter"), (2017, "pre-monsoon"),
        (2019, "winter"), (2020, "monsoon"), (2021, "monsoon"),
        (2022, "monsoon"), (2023, "post-monsoon"),
    }
    for year in range(2015, 2024):
        year_pos = train_pos[train_pos["year"] == year].sort_values("date")
        if year_pos.empty:
            reasons[("positive", year)] = f"no POSITIVE slots available for {year} in the label archive"
            continue
        preferred = year_pos[~year_pos.apply(lambda r: (year, r["season"]) in already_years_seasons, axis=1)]
        pick = preferred.iloc[0] if not preferred.empty else year_pos.iloc[0]
        candidates.append((pick, "fill TRAIN year coverage: no acquired POSITIVE event group exists for "
                                  f"{year} yet (or only one season was represented); "
                                  f"picked a {pick['season']} date to diversify season coverage"))

    # --- 1 new TRAIN negative per TRAIN year 2015-2023 not already covered ---
    # Phase 0.4.17 originally picked the *earliest* negative in each target
    # year, which happened to land on January 1st for all four of
    # 2017/2018/2021/2023 (the label archive's first slot of each year) --
    # four near-duplicate winter dates with no real temporal/seasonal
    # spread. Phase 0.4.18 (Part 2, Issue A) fixes this by picking, per
    # year, the earliest negative on/after a year-specific target month
    # chosen to diversify season coverage across the four fill years
    # (pre-monsoon / monsoon / post-monsoon / post-monsoon) instead of
    # defaulting to the earliest calendar date. A cycle/lead mapping is
    # confirmed (via find_cycle_and_leads) before a candidate is accepted;
    # if none of the dates on/after the target month map cleanly, the
    # search falls back to the full year, oldest-first, same as before.
    already_neg_years = {2015, 2016, 2019, 2020, 2022}
    neg_target_month = {2017: 4, 2018: 7, 2021: 10, 2023: 11}
    for year in range(2015, 2024):
        if year in already_neg_years:
            continue
        year_neg_all = train_neg[train_neg["year"] == year].sort_values("date")
        if year_neg_all.empty:
            continue
        target_month = neg_target_month.get(year)
        if target_month is not None:
            year_neg = year_neg_all[year_neg_all["month"] >= target_month]
            if year_neg.empty:
                year_neg = year_neg_all
        else:
            year_neg = year_neg_all
        pick = None
        for _, cand_row in year_neg.iterrows():
            if find_cycle_and_leads(cand_row["date"], int(cand_row["slot_id"])) is not None:
                pick = cand_row
                break
        if pick is None:
            continue
        reason = (f"fill TRAIN year coverage: no acquired NEGATIVE_CONFIRMED event group "
                  f"exists for {year} yet")
        if target_month is not None:
            reason += (f"; Phase 0.4.18 fix (Issue A) -- picked a {pick['season']} date "
                       f"(month>={target_month}) instead of the earliest-in-year date, to avoid "
                       f"the Phase 0.4.17 Jan-1-every-year clustering artifact")
        candidates.append((pick, reason))

    # --- extra TRAIN positive(s) for winter (sparse in the full archive) ---
    # The full label archive has only 7 winter POSITIVE slots across all 11
    # years (Step 2/7); of the 5 that fall in TRAIN, 2015-02-28 and
    # 2018-02-09 are already picked above as per-year fills, leaving only
    # the 2021-02-19(slot2)/2021-02-19(slot3)/2021-02-20(slot2) cluster --
    # three event groups within ~30 hours of each other, plausibly one
    # synoptic system. Phase 0.4.17 added two of these (slot3 and the
    # 2021-02-20 date); Phase 0.4.18 (Part 2, Issue B) found no
    # independent (non-clustered) winter TRAIN positive exists anywhere
    # else in the archive to substitute in, so rather than force a
    # same-cluster "replacement," this phase adds only the single most
    # temporally separated member of the cluster relative to the
    # already-picked 2021-02-19(slot2) fill -- 2021-02-20(slot2), ~24h
    # away -- and drops 2021-02-19(slot3), which is only ~6h from the
    # already-picked fill and adds the least independent information.
    winter_pos = train_pos[train_pos["season"] == "winter"].sort_values("date")
    picked_keys = {c[0]["event_group_key"] for c in candidates}
    cluster_keys_by_separation = [
        f"{CELL_ID}|2021-02-20|2",  # ~24h from the 2021-02-19|2 year-fill pick
        f"{CELL_ID}|2021-02-19|3",  # ~6h from the same pick -- dropped (Issue B)
    ]
    added_winter = 0
    max_extra_winter = 1  # reduced from 2 (Phase 0.4.17) to 1 (Phase 0.4.18, Issue B)
    for key in cluster_keys_by_separation:
        if added_winter >= max_extra_winter:
            break
        match = winter_pos[winter_pos["event_group_key"] == key]
        if match.empty or key in picked_keys:
            continue
        row = match.iloc[0]
        candidates.append((row, "winter TS positives are extremely rare in the full label archive "
                                 "(7 of 584 total, across all 11 years, only 5 in TRAIN, and the two "
                                 "non-clustered ones are already picked as per-year fills above) -- "
                                 "of the remaining 2021-02-19/2021-02-20 cluster, this is the member "
                                 "most temporally separated (~24h) from the already-picked "
                                 "2021-02-19|2 fill; Phase 0.4.18 (Issue B) drops the ~6h-separated "
                                 "2021-02-19|3 member that Phase 0.4.17 had also included, since no "
                                 "independent (non-clustered) winter TRAIN positive exists elsewhere "
                                 "in the archive to substitute in instead"))
        added_winter += 1

    # --- small HOLDOUT reserve: 3 positive + 3 negative, 2024-2025,
    # acquired but explicitly never to be used for training ---
    for _, row in holdout_pos.sort_values("date").head(3).iterrows():
        candidates.append((row, "HOLDOUT reserve (2024-2025): acquired for future evaluation only, "
                                 "never to be used in training per the Phase 0.4.12 partition contract"))
    for _, row in holdout_neg.sort_values("date").head(3).iterrows():
        candidates.append((row, "HOLDOUT reserve (2024-2025): acquired for future evaluation only, "
                                 "never to be used in training per the Phase 0.4.12 partition contract"))

    # de-duplicate by event_group_key (a row could satisfy >1 goal above)
    seen = set()
    deduped = []
    for row, reason in candidates:
        if row["event_group_key"] in seen:
            continue
        seen.add(row["event_group_key"])
        deduped.append((row, reason))

    return deduped


def build_manifest_rows(candidates) -> list[dict]:
    rows = []
    priority = 1
    for row, reason in candidates:
        cycle_leads = find_cycle_and_leads(row["date"], int(row["slot_id"]))
        if cycle_leads is None:
            rows.append({
                "priority": priority, "target_ist_date": row["date"], "target_slot": int(row["slot_id"]),
                "label": row["label_status"], "event_group_key": row["event_group_key"],
                "target_valid_time": "AMBIGUOUS", "selected_gfs_cycle": "AMBIGUOUS", "selected_lead": "AMBIGUOUS",
                "reason": reason + " -- FLAGGED: could not determine a cycle with both f003 and f006 "
                                    "landing in this slot; needs manual review before acquisition",
                "partition": assign_partition(row["date"]), "season": row["season"],
                "expected_file": "AMBIGUOUS", "expected_role": "AMBIGUOUS",
            })
            priority += 1
            continue
        init, leads_in_slot = cycle_leads
        cycle_str = init.strftime("%Y%m%d%H")
        for lead_hours, valid_utc, valid_ist in leads_in_slot:
            rows.append({
                "priority": priority,
                "target_ist_date": row["date"],
                "target_slot": int(row["slot_id"]),
                "label": row["label_status"],
                "event_group_key": row["event_group_key"],
                "target_valid_time": valid_ist.isoformat(),
                "selected_gfs_cycle": cycle_str,
                "selected_lead": f"f{lead_hours:03d}",
                "reason": reason,
                "partition": assign_partition(row["date"]),
                "season": row["season"],
                "expected_file": f"gfs.0p25.{cycle_str}.f{lead_hours:03d}.grib2",
                "expected_role": f"lead_hour_{lead_hours}",
            })
        priority += 1
    return rows


def main():
    labels = load_labels()
    candidates = select_candidates(labels)
    rows = build_manifest_rows(candidates)

    out_csv = REPO_ROOT / "docs" / "PHASE_0_4_17_ACQUISITION_MANIFEST.csv"
    fieldnames = ["priority", "target_ist_date", "target_slot", "label", "event_group_key",
                  "target_valid_time", "selected_gfs_cycle", "selected_lead", "reason",
                  "partition", "season", "expected_file", "expected_role"]
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    n_events = len(candidates)
    n_files = len(rows)
    n_pos_events = sum(1 for r, _ in candidates if r["label_status"] == "POSITIVE")
    n_neg_events = sum(1 for r, _ in candidates if r["label_status"] == "NEGATIVE_CONFIRMED")
    n_train_events = sum(1 for r, _ in candidates if assign_partition(r["date"]) == TRAIN)
    n_holdout_events = sum(1 for r, _ in candidates if assign_partition(r["date"]) == HOLDOUT)
    ambiguous = [r for r in rows if r["selected_gfs_cycle"] == "AMBIGUOUS"]

    summary = {
        "n_candidate_event_groups": n_events,
        "n_candidate_files": n_files,
        "n_positive_event_groups": n_pos_events,
        "n_negative_event_groups": n_neg_events,
        "n_train_event_groups": n_train_events,
        "n_holdout_event_groups": n_holdout_events,
        "n_ambiguous_flagged": len(ambiguous),
        "years_covered": sorted({r["date"][:4] for r, _ in candidates}),
        "seasons_covered": sorted({r["season"] for r, _ in candidates}),
    }
    print(json.dumps(summary, indent=2))
    (REPO_ROOT / "docs" / "PHASE_0_4_17_ACQUISITION_SUMMARY.json").write_text(json.dumps(summary, indent=2))
    print(f"\nWrote {out_csv} ({n_files} rows, {n_events} event groups)")


if __name__ == "__main__":
    main()

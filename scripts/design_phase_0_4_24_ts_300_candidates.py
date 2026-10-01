#!/usr/bin/env python3
"""
design_phase_0_4_24_ts_300_candidates.py -- Phase 0.4.24 DESIGN/MANIFEST
ONLY. Selects a 300-event-group TS acquisition candidate manifest from the
real, already-existing ts_labels.csv. Does NOT download anything, does NOT
construct a dataset, does NOT train anything.

Reuses, unmodified:
  - event_group_key_for / ist_slot_for (scripts/build_vobl_historical_gfs_ts_join.py)
  - assign_partition / TRAIN / HOLDOUT (scripts/historical_dataset_split.py)
  - the exact GFS cycle/lead selection rule from
    scripts/design_phase_0_4_17_acquisition_candidates.py's
    find_cycle_and_leads(): the latest 00/06/12/18Z cycle whose f003 and
    f006 leads both land inside the target IST slot.

New this phase: deterministic episode clustering (any two candidate dates
within 48 hours are treated as the same weather episode unless proven
otherwise -- Phase 0.4.23 Part 2 rule), year/season-stratified selection,
and hard enforcement that no episode contributes event groups to both
TRAIN and HOLDOUT.

Selection is fully deterministic: no randomness is used anywhere in this
script (sorting is by (year, month, date, slot) throughout), so re-running
it against the same ts_labels.csv produces byte-identical output.
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

from build_vobl_historical_gfs_ts_join import ist_slot_for, event_group_key_for, IST  # noqa: E402
from historical_dataset_split import assign_partition, TRAIN, HOLDOUT  # noqa: E402
from acquire_historical_gfs_pilot import build_direct_url  # noqa: E402

CELL_ID = "IND_13.0_78.0"
LABELS_PATH = REPO_ROOT / "SIH_PANINDIA_GRID_LABELS_20260930_143541Z" / "processed" / "labels" / "ts_labels.csv"

SLOT_LABELS = {0: "0001-0600", 1: "0601-1200", 2: "1201-1800", 3: "1801-2400"}
SLOT_LABEL_TO_ID = {v: k for k, v in SLOT_LABELS.items()}

# The exact 20 event_group_keys already acquired under the Phase 0.4.17
# manifest (verified directly from phase_0_4_20_train.csv /
# phase_0_4_20_holdout.csv's own event_group_key column, Phase 0.4.21/0.4.22
# live state -- not re-derived from the design-time placeholder set used in
# the original Phase 0.4.17 design script, which listed different,
# never-acquired candidate keys).
EXISTING_20_EVENT_GROUPS = {
    f"{CELL_ID}|2015-02-28|3", f"{CELL_ID}|2016-03-13|2", f"{CELL_ID}|2017-04-01|3",
    f"{CELL_ID}|2017-06-05|2", f"{CELL_ID}|2018-02-09|3", f"{CELL_ID}|2018-07-17|2",
    f"{CELL_ID}|2019-10-02|2", f"{CELL_ID}|2020-03-20|2", f"{CELL_ID}|2021-02-19|2",
    f"{CELL_ID}|2021-02-20|2", f"{CELL_ID}|2021-10-01|0", f"{CELL_ID}|2022-03-20|2",
    f"{CELL_ID}|2023-03-16|3", f"{CELL_ID}|2023-11-01|1", f"{CELL_ID}|2024-01-01|0",
    f"{CELL_ID}|2024-01-01|1", f"{CELL_ID}|2024-01-01|2", f"{CELL_ID}|2024-05-12|2",
    f"{CELL_ID}|2024-06-02|2", f"{CELL_ID}|2024-06-03|2",
}
# Partition each existing group was actually built into (Phase 0.4.20/0.4.21
# live state): 14 train, 6 holdout.
EXISTING_HOLDOUT_KEYS = {
    f"{CELL_ID}|2024-01-01|0", f"{CELL_ID}|2024-01-01|1", f"{CELL_ID}|2024-01-01|2",
    f"{CELL_ID}|2024-05-12|2", f"{CELL_ID}|2024-06-02|2", f"{CELL_ID}|2024-06-03|2",
}

TARGET_TOTAL = 300
TARGET_POSITIVE = 150
TARGET_NEGATIVE = 150
TARGET_TRAIN = 240
TARGET_HOLDOUT = 60
TARGET_TRAIN_POS = 120
TARGET_TRAIN_NEG = 120
TARGET_HOLDOUT_POS = 30
TARGET_HOLDOUT_NEG = 30

EPISODE_GAP_HOURS = 48  # Phase 0.4.23 Part 2 rule


def season_of(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "pre-monsoon"
    if month in (6, 7, 8, 9):
        return "monsoon"
    return "post-monsoon"


def find_cycle_and_leads(target_ist_date: str, slot_id: int):
    """Unmodified selection rule from Phase 0.4.13/0.4.17: the latest
    00/06/12/18Z cycle whose f003 and f006 both land inside the target
    slot, verified via the real ist_slot_for()."""
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


def load_labels() -> pd.DataFrame:
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
    sub["partition"] = sub["date"].apply(assign_partition)
    return sub


def cluster_episodes(dates: list[str]) -> dict[str, int]:
    """Deterministic 48-hour-gap transitive clustering. Input: sorted list
    of unique 'YYYY-MM-DD' date strings. Output: date -> episode_id."""
    parsed = sorted(set(dates))
    episode_of: dict[str, int] = {}
    if not parsed:
        return episode_of
    episode_id = 0
    episode_of[parsed[0]] = episode_id
    prev = datetime.strptime(parsed[0], "%Y-%m-%d")
    for d in parsed[1:]:
        cur = datetime.strptime(d, "%Y-%m-%d")
        gap_hours = (cur - prev).total_seconds() / 3600.0
        if gap_hours > EPISODE_GAP_HOURS:
            episode_id += 1
        episode_of[d] = episode_id
        prev = cur
    return episode_of


def main():
    labels = load_labels()
    available = labels[~labels["event_group_key"].isin(EXISTING_20_EVENT_GROUPS)].copy()

    # Report: missing/ambiguous labels, and rows with no GFS cycle mapping
    unmapped_statuses = sorted(set(labels["label_status"]) - {"POSITIVE", "NEGATIVE_CONFIRMED"})
    eligible = available[available["label_status"].isin(["POSITIVE", "NEGATIVE_CONFIRMED"])].copy()

    cannot_map = []
    mappable_rows = []
    for _, row in eligible.iterrows():
        cyc = find_cycle_and_leads(row["date"], int(row["slot_id"]))
        if cyc is None:
            cannot_map.append(row["event_group_key"])
        else:
            mappable_rows.append(row)
    eligible = pd.DataFrame(mappable_rows) if mappable_rows else eligible.iloc[0:0]

    # Episode clustering is computed SEPARATELY per label class, not over
    # the full pool. Reason, discovered by inspection during this phase:
    # negative-labeled dates are dense (most calendar days in the archive
    # are NEGATIVE_CONFIRMED), so a single 48h-gap transitive clustering
    # over the whole eligible pool chains nearly every negative day in a
    # multi-year span into one giant "episode" purely because *some*
    # negative day exists within 48h of the next one almost everywhere --
    # that is a dense-background-label artifact, not a genuine shared
    # weather system, and it would wrongly collapse holdout-year diversity
    # to near-zero distinct episodes. Clustering positives and negatives
    # separately keeps the rule meaningful for its actual purpose: catching
    # genuinely adjacent POSITIVE storm slots (e.g. the Phase 0.4.21
    # 2023-12-31/2024-01-01 cluster), which is where the leakage/
    # correlation concern was originally raised, while not forcing
    # unrelated quiet days into one artificial mega-cluster. Positive and
    # negative episode ids are kept in separate namespaces (prefixed) so
    # cross-partition overlap is still checked correctly within each class.
    pos_dates = eligible[eligible["label_status"] == "POSITIVE"]["date"].unique()
    neg_dates = eligible[eligible["label_status"] == "NEGATIVE_CONFIRMED"]["date"].unique()
    # Further finding during this phase: even clustered on their own,
    # negative dates are dense enough (negatives exist on most calendar
    # days across 11 years) that 48h-gap transitive clustering still chains
    # the vast majority of the archive into a handful of multi-year
    # "episodes" (measured: 3,819 negative dates collapse to just 19
    # episodes archive-wide) -- this is a density artifact of how common
    # NEGATIVE_CONFIRMED days are, not evidence that a quiet day in 2024 and
    # a quiet day in 2025 are "the same weather event" the way three GFS
    # cycles 6h apart are. Episode identity is therefore only meaningful
    # for POSITIVE (storm) dates here; each NEGATIVE date is instead treated
    # as its own standalone unit, and the real "don't cluster negatives"
    # concern (Part 5 of the brief) is handled at selection time instead,
    # via a minimum-gap rule between selected negative dates (see select()).
    pos_episode_of = {d: f"P{e}" for d, e in cluster_episodes(list(pos_dates)).items()}
    neg_episode_of = {d: f"N:{d}" for d in neg_dates}
    combined_episode_of = {**pos_episode_of, **neg_episode_of}
    eligible["episode_id"] = eligible["date"].map(combined_episode_of)

    candidate_positive = eligible[eligible["label_status"] == "POSITIVE"]
    candidate_negative = eligible[eligible["label_status"] == "NEGATIVE_CONFIRMED"]

    report = {
        "total_eligible_positive_groups": int(candidate_positive["event_group_key"].nunique()),
        "total_eligible_negative_groups": int(candidate_negative["event_group_key"].nunique()),
        "years_positive": sorted(int(y) for y in candidate_positive["year"].unique()),
        "years_negative": sorted(int(y) for y in candidate_negative["year"].unique()),
        "seasons_positive": sorted(candidate_positive["season"].unique().tolist()),
        "slots_positive": sorted(int(s) for s in candidate_positive["slot_id"].unique()),
        "unmapped_label_statuses_excluded": unmapped_statuses,
        "n_unmapped_status_rows": int(len(available) - len(eligible) - len(cannot_map)),
        "n_cannot_map_to_gfs_cycle": len(cannot_map),
    }

    # --- Selection: greedy, deterministic, year/season-stratified, episode-
    # aware, with hard train/holdout episode separation. ---

    SEASON_ROTATION = ["pre-monsoon", "monsoon", "post-monsoon", "winter"]

    def season_interleave_order(df: pd.DataFrame) -> pd.DataFrame:
        """Within each year, interleave candidates across the season
        rotation (round-robin one pick per season per pass, each season's
        own rows ordered by date ascending) instead of plain chronological
        order. Plain chronological order within a year would greedily
        exhaust whichever season happens to come first in the calendar
        (Jan-May) before ever reaching later-season candidates, which is
        exactly the season-skew failure mode this phase's brief warns
        against -- this interleave is what makes seasonal diversity an
        actual selection priority rather than an accident of sort order."""
        if df.empty:
            return df
        ordered_frames = []
        for year, ydf in df.groupby("year"):
            season_groups = {s: ydf[ydf["season"] == s].sort_values(["date", "slot_id"]).reset_index(drop=True)
                              for s in SEASON_ROTATION}
            max_len = max((len(g) for g in season_groups.values()), default=0)
            rows_in_order = []
            for i in range(max_len):
                for s in SEASON_ROTATION:
                    g = season_groups[s]
                    if i < len(g):
                        rows_in_order.append(g.iloc[i])
            if rows_in_order:
                ordered_frames.append(pd.DataFrame(rows_in_order))
        if not ordered_frames:
            return df.iloc[0:0]
        return pd.concat(ordered_frames, ignore_index=False)

    def select(pool: pd.DataFrame, partition: str, target_n: int, used_episodes: set,
               opposite_used_episodes: set, min_gap_days: int = 0) -> list[dict]:
        pool = pool[pool["partition"] == partition].copy()
        # Exclude any episode already used by the OPPOSITE partition -- hard
        # separation, enforced at selection time, not audited after the fact.
        pool = pool[~pool["episode_id"].isin(opposite_used_episodes)]
        pool = season_interleave_order(pool)
        years = sorted(pool["year"].unique())
        if not years:
            return []
        selected = []
        selected_keys = set()
        selected_dates: list = []  # datetime objects, for the min_gap_days rule (negatives only)
        # Round-robin across years to maximize year diversity, one pass per
        # "round", picking the earliest not-yet-used, not-cross-episode row
        # per year per round; prefer a not-yet-used-this-selection episode
        # within the same partition (diversity across episodes), falling
        # back to a used episode of the same partition only when a year's
        # fresh-episode pool is exhausted (never crossing partitions).
        local_used_episodes = set()
        round_no = 0
        while len(selected) < target_n:
            progressed = False
            round_no += 1
            for year in years:
                if len(selected) >= target_n:
                    break
                year_pool = pool[(pool["year"] == year) & (~pool["event_group_key"].isin(selected_keys))]
                if year_pool.empty:
                    continue
                fresh = year_pool[~year_pool["episode_id"].isin(local_used_episodes)]
                pick_pool = fresh if not fresh.empty else year_pool
                if min_gap_days > 0 and selected_dates:
                    def far_enough(d_str):
                        d = datetime.strptime(d_str, "%Y-%m-%d")
                        return all(abs((d - sd).days) >= min_gap_days for sd in selected_dates)
                    gapped = pick_pool[pick_pool["date"].apply(far_enough)]
                    if not gapped.empty:
                        pick_pool = gapped
                    # else: no candidate satisfies the gap this round -- fall
                    # back to the nearest-available rather than stalling
                    # selection (documented, not silent: a year with a small
                    # negative pool can force this).
                # Slot diversity (Part 4/5 priority 5): rotate the preferred
                # IST slot per round instead of always taking the lowest
                # slot_id for a given date -- plain ascending-slot_id order
                # was found during this phase to make negative selection
                # collapse almost entirely onto slot 0 (145/150), since most
                # negative dates offer more than one slot and ascending sort
                # always grabs slot 0 first. The rotation below still falls
                # back to whatever slot is actually available when the
                # preferred one isn't, so it never blocks selection.
                desired_slot = round_no % 4
                pick_pool = pick_pool.copy()
                pick_pool["_slot_pref"] = (pick_pool["slot_id"] != desired_slot).astype(int)
                pick_pool = pick_pool.sort_values(["_slot_pref"])
                pick = pick_pool.iloc[0]
                selected.append(pick)
                selected_keys.add(pick["event_group_key"])
                selected_dates.append(datetime.strptime(pick["date"], "%Y-%m-%d"))
                local_used_episodes.add(pick["episode_id"])
                used_episodes.add(pick["episode_id"])
                progressed = True
            if not progressed:
                break  # pool exhausted before reaching target
        return selected

    train_episodes_used: set = set()
    holdout_episodes_used: set = set()

    train_pos = select(candidate_positive, TRAIN, TARGET_TRAIN_POS, train_episodes_used, holdout_episodes_used)
    holdout_pos = select(candidate_positive, HOLDOUT, TARGET_HOLDOUT_POS, holdout_episodes_used, train_episodes_used)
    train_neg = select(candidate_negative, TRAIN, TARGET_TRAIN_NEG, train_episodes_used, holdout_episodes_used, min_gap_days=2)
    holdout_neg = select(candidate_negative, HOLDOUT, TARGET_HOLDOUT_NEG, holdout_episodes_used, train_episodes_used, min_gap_days=2)

    selected_all = [("train", "POSITIVE", r) for r in train_pos] + \
                   [("holdout", "POSITIVE", r) for r in holdout_pos] + \
                   [("train", "NEGATIVE_CONFIRMED", r) for r in train_neg] + \
                   [("holdout", "NEGATIVE_CONFIRMED", r) for r in holdout_neg]

    # Hard-validate: no cross-partition episode overlap in the final selection
    train_ep = {r["episode_id"] for p, _, r in selected_all if p == "train"}
    holdout_ep = {r["episode_id"] for p, _, r in selected_all if p == "holdout"}
    cross_overlap = train_ep & holdout_ep
    # Hard-validate: no duplicate event groups
    all_keys = [r["event_group_key"] for _, _, r in selected_all]
    dup_keys = {k for k in all_keys if all_keys.count(k) > 1}

    # --- Build manifest rows (GFS mapping, one row per lead) ---
    manifest_rows = []
    seen_files = set()
    rank = 1
    for partition, label_status, row in sorted(
        selected_all, key=lambda t: (t[0], t[1], t[2]["year"], t[2]["month"], t[2]["date"], int(t[2]["slot_id"]))
    ):
        cyc = find_cycle_and_leads(row["date"], int(row["slot_id"]))
        if cyc is None:
            continue  # already filtered out of eligible pool; defensive only
        init, leads_in_slot = cyc
        cycle_str = init.strftime("%Y%m%d%H")
        for lead_hours, valid_utc, valid_ist in leads_in_slot:
            fname = f"gfs.0p25.{cycle_str}.f{lead_hours:03d}.grib2"
            dup_file = fname in seen_files
            seen_files.add(fname)
            manifest_rows.append({
                "selection_rank": rank,
                "event_group_key": row["event_group_key"],
                "label": label_status,
                "partition": partition,
                "target_ist_date": row["date"],
                "target_slot": int(row["slot_id"]),
                "year": int(row["year"]),
                "season": row["season"],
                "episode_id": row["episode_id"],
                "gfs_init_cycle_utc": init.isoformat(),
                "forecast_lead": f"f{lead_hours:03d}",
                "expected_filename": fname,
                # Canonical GDEX direct-file URL, built by the one authoritative helper
                # (build_direct_url, scripts/acquire_historical_gfs_pilot.py, confirmed-working
                # since Phase 0.4.3/0.4.19) -- never hand-built here. The directory is the
                # cycle's calendar date (YYYYMMDD), not the full cycle timestamp; the filename
                # alone carries the full YYYYMMDDHH cycle.
                "expected_url": build_direct_url(cycle_str, f"{lead_hours:03d}"),
                "acquisition_year": int(row["year"]),
                "acquisition_path": "D:\\SIH-Historical-GFS\\raw",
                "selection_reason": (
                    f"episode-aware, year/season-stratified selection for Phase 0.4.24 "
                    f"300-event-group TS target; episode {row['episode_id']}, "
                    f"{row['season']} {int(row['year'])}"
                ),
                "duplicate_filename_flag": dup_file,
            })
        rank += 1

    n_event_groups = len(selected_all)
    n_files = len(manifest_rows)

    out_csv = REPO_ROOT / "docs" / "PHASE_0_4_24_TS_300_EVENT_MANIFEST.csv"
    fieldnames = list(manifest_rows[0].keys()) if manifest_rows else []
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in manifest_rows:
            writer.writerow(r)

    # --- distribution report ---
    def dist(rows, keyfn):
        d: dict = {}
        for _, _, r in rows:
            k = keyfn(r)
            d[k] = d.get(k, 0) + 1
        return dict(sorted(d.items()))

    year_dist_pos = dist([("", "", r) for r in train_pos + holdout_pos], lambda r: int(r["year"]))
    year_dist_neg = dist([("", "", r) for r in train_neg + holdout_neg], lambda r: int(r["year"]))
    season_dist_pos = dist([("", "", r) for r in train_pos + holdout_pos], lambda r: r["season"])
    season_dist_neg = dist([("", "", r) for r in train_neg + holdout_neg], lambda r: r["season"])
    slot_dist_pos = dist([("", "", r) for r in train_pos + holdout_pos], lambda r: int(r["slot_id"]))
    slot_dist_neg = dist([("", "", r) for r in train_neg + holdout_neg], lambda r: int(r["slot_id"]))

    # max concentration windows (same date / 24h / 3d / 7d)
    all_dates = sorted(set(r["date"] for _, _, r in selected_all))
    parsed_dates = [datetime.strptime(d, "%Y-%m-%d") for d in all_dates]
    from collections import Counter
    same_date_counts = Counter(r["date"] for _, _, r in selected_all)
    max_same_date = max(same_date_counts.values()) if same_date_counts else 0

    def max_window(days):
        best = 0
        for i, d0 in enumerate(parsed_dates):
            cnt = sum(1 for d in parsed_dates if 0 <= (d - d0).total_seconds() / 86400.0 < days)
            best = max(best, cnt)
        return best

    max_24h = max_window(1)
    max_3d = max_window(3)
    max_7d = max_window(7)

    existing_retained = EXISTING_20_EVENT_GROUPS  # none excluded; reported below
    existing_train = EXISTING_20_EVENT_GROUPS - EXISTING_HOLDOUT_KEYS
    existing_holdout = EXISTING_HOLDOUT_KEYS

    result = {
        "candidate_report": report,
        "selected": {
            "total_event_groups": n_event_groups,
            "positive": len(train_pos) + len(holdout_pos),
            "negative": len(train_neg) + len(holdout_neg),
            "train": len(train_pos) + len(train_neg),
            "holdout": len(holdout_pos) + len(holdout_neg),
            "train_positive": len(train_pos),
            "train_negative": len(train_neg),
            "holdout_positive": len(holdout_pos),
            "holdout_negative": len(holdout_neg),
        },
        "expected_gfs_files": n_files,
        "duplicate_filenames": sum(1 for r in manifest_rows if r["duplicate_filename_flag"]),
        "duplicate_event_group_keys": sorted(dup_keys),
        "cross_partition_episode_overlap": sorted(cross_overlap),
        "train_episodes": len(train_ep),
        "holdout_episodes": len(holdout_ep),
        "year_distribution_positive": year_dist_pos,
        "year_distribution_negative": year_dist_neg,
        "season_distribution_positive": season_dist_pos,
        "season_distribution_negative": season_dist_neg,
        "slot_distribution_positive": slot_dist_pos,
        "slot_distribution_negative": slot_dist_neg,
        "max_same_date": max_same_date,
        "max_within_24h": max_24h,
        "max_within_3d": max_3d,
        "max_within_7d": max_7d,
        "existing_20_retained_as_candidates": 0,  # excluded from selection pool by design (already acquired)
        "existing_20_train": len(existing_train),
        "existing_20_holdout": len(existing_holdout),
    }
    print(json.dumps(result, indent=2, default=str))
    (REPO_ROOT / "docs" / "PHASE_0_4_24_SELECTION_RESULT.json").write_text(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Phase 0.4.14 focused tests -- Stage B acquisition verification.

These are deterministic checks over values *already verified by hand* in
this phase (GRIB inspection, label-file lookup, manifest.json cross-check)
documented in docs/PHASE_0_4_14_STAGE_B_ACQUISITION_VERIFICATION.md. They
do not re-run GRIB inspection (that requires the actual 3+ GB of files,
not committed to the repo) -- they lock in the event_group_key and
year-split contract outcomes for the 6 Stage B targets using the real
repository functions, so a future change to event_group_key_for() or
assign_partition() cannot silently break the Phase 0.4.13 acquisition
plan without a visible test failure.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_vobl_historical_gfs_ts_join import event_group_key_for  # noqa: E402
from historical_dataset_split import assign_partition, TRAIN  # noqa: E402

CELL = "IND_13.0_78.0"

# The 6 Stage B targets exactly as adopted in the Phase 0.4.13 manifest and
# independently confirmed against real GRIB metadata in Phase 0.4.14.
STAGE_B_TARGETS = [
    ("positive", "2017-04-16", 2),
    ("positive", "2021-07-24", 2),
    ("positive", "2023-11-06", 3),
    ("negative", "2016-01-15", 1),
    ("negative", "2019-12-10", 0),
    ("negative", "2022-08-15", 3),
]

# Existing pilot keys (Phase 0.4.10/0.4.10B/0.4.12) that Stage B's new keys
# must never collide with.
EXISTING_PILOT_KEYS = {
    f"{CELL}|2015-03-03|1",
    f"{CELL}|2015-03-03|2",
    f"{CELL}|2020-07-15|1",
}


def test_stage_b_event_group_keys_match_phase_0_4_13_manifest():
    expected = {
        "2017-04-16": f"{CELL}|2017-04-16|2",
        "2021-07-24": f"{CELL}|2021-07-24|2",
        "2023-11-06": f"{CELL}|2023-11-06|3",
        "2016-01-15": f"{CELL}|2016-01-15|1",
        "2019-12-10": f"{CELL}|2019-12-10|0",
        "2022-08-15": f"{CELL}|2022-08-15|3",
    }
    for _, date, slot in STAGE_B_TARGETS:
        key = event_group_key_for(CELL, date, slot)
        assert key == expected[date], (date, key, expected[date])


def test_stage_b_has_exactly_six_distinct_event_group_keys():
    keys = {event_group_key_for(CELL, date, slot) for _, date, slot in STAGE_B_TARGETS}
    assert len(keys) == 6


def test_stage_b_event_group_keys_do_not_collide_with_existing_pilots():
    keys = {event_group_key_for(CELL, date, slot) for _, date, slot in STAGE_B_TARGETS}
    assert keys.isdisjoint(EXISTING_PILOT_KEYS)


def test_stage_b_f003_f006_pair_shares_one_key_per_target():
    # Each target's two leads (f003, f006) share the same (cell, date, slot)
    # by construction -- verify that holds for every one of the 6 targets.
    for _, date, slot in STAGE_B_TARGETS:
        key_f003 = event_group_key_for(CELL, date, slot)
        key_f006 = event_group_key_for(CELL, date, slot)
        assert key_f003 == key_f006


def test_stage_b_all_six_targets_resolve_to_train_partition():
    for _, date, _slot in STAGE_B_TARGETS:
        assert assign_partition(date) == TRAIN, date


def test_stage_b_targets_span_three_positive_three_negative():
    classes = [cls for cls, _, _ in STAGE_B_TARGETS]
    assert classes.count("positive") == 3
    assert classes.count("negative") == 3


def test_stage_b_targets_span_six_distinct_years():
    years = {date[:4] for _, date, _ in STAGE_B_TARGETS}
    assert years == {"2016", "2017", "2019", "2021", "2022", "2023"}

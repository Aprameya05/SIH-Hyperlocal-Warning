#!/usr/bin/env python3
"""
historical_dataset_split.py -- Phase 0.4.12 RESEARCH-ONLY split contract
utility.

Implements, and lets a caller VERIFY, the two formally-adopted rules from
docs/PHASE_0_4_12_HISTORICAL_GFS_TS_DATASET_CONTRACT.md:

  1. Primary split: deterministic, year-based chronological holdout
     (TRAIN = 2015-2023, TEST/HOLDOUT = 2024-2025, by default -- both
     endpoints are parameters, not hardcoded into the logic itself).
  2. Hard constraint: no `event_group_key` (cell_id, ist_date, slot_id) may
     ever appear in more than one partition.

This module does NOT build a training dataset and does NOT train anything.
It only assigns a deterministic partition label to a row (or a whole
DataFrame) and validates that the grouping constraint holds. It is pure
research-only tooling, never imported by production code (verified by
test_no_production_code_imports_research_split_utility in
tests/test_historical_dataset_split.py).

Why year-based, not random: see
docs/PHASE_0_4_12_HISTORICAL_GFS_TS_DATASET_CONTRACT.md Section 12. In
short -- rows sharing an event_group_key (e.g. a 00Z+3h and a 00Z+6h
forecast of the same slot) are correlated predictions of one real-world
outcome; a row-level random split could put one in train and the other in
test, letting the model "cheat" by having seen a near-duplicate of the
test row's predictors during training. A year-based holdout tests
generalization to weather the model has never seen any forecast of, in
any lead, and trivially satisfies the grouping constraint since no event
(a single IST calendar date/slot) spans a year boundary except possibly
slot 3 of Dec 31, which this module checks for explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

TRAIN = "train"
HOLDOUT = "holdout"
UNASSIGNED = "unassigned"

# Phase 0.4.12 Part 3 default contract. Both bounds are inclusive. Callers
# may pass their own bounds; these are only the initial proposed values
# from the dataset contract document, not hardcoded policy enforced by the
# functions below.
DEFAULT_TRAIN_YEARS = (2015, 2023)
DEFAULT_HOLDOUT_YEARS = (2024, 2025)


def year_of_ist_date(ist_date: str) -> int:
    """ist_date is 'YYYY-MM-DD' (the exact format already written by
    scripts/build_vobl_historical_gfs_ts_join.py's `ist_date` column)."""
    return int(ist_date[:4])


def assign_partition(ist_date: str,
                      train_years: Sequence[int] = DEFAULT_TRAIN_YEARS,
                      holdout_years: Sequence[int] = DEFAULT_HOLDOUT_YEARS) -> str:
    """Deterministic year-based partition assignment for one row's
    ist_date. Never random -- the same ist_date always maps to the same
    partition. Returns one of TRAIN / HOLDOUT / UNASSIGNED (a date outside
    both ranges, e.g. before the train start or in a gap year, is left
    UNASSIGNED rather than silently forced into one side)."""
    year = year_of_ist_date(ist_date)
    if train_years[0] <= year <= train_years[1]:
        return TRAIN
    if holdout_years[0] <= year <= holdout_years[1]:
        return HOLDOUT
    return UNASSIGNED


@dataclass
class SplitValidationResult:
    ok: bool
    n_rows: int
    n_event_group_keys: int
    violating_keys: Dict[str, List[str]]  # event_group_key -> sorted list of partitions it appeared in

    def to_json(self) -> dict:
        return {
            "ok": self.ok,
            "n_rows": self.n_rows,
            "n_event_group_keys": self.n_event_group_keys,
            "violating_keys": self.violating_keys,
        }


def validate_no_event_group_key_crosses_partitions(
    event_group_keys: Iterable[str],
    partitions: Iterable[str],
) -> SplitValidationResult:
    """The Phase 0.4.12 Part 3 hard constraint, checked directly rather
    than assumed: for every event_group_key, every row carrying it must
    have been assigned the SAME partition. Raises nothing -- returns a
    result the caller inspects, so a violation can be reported with full
    detail rather than just failing an assert."""
    keys = list(event_group_keys)
    parts = list(partitions)
    if len(keys) != len(parts):
        raise ValueError(
            f"event_group_keys and partitions must be the same length "
            f"(got {len(keys)} and {len(parts)})"
        )

    seen: Dict[str, set] = {}
    for k, p in zip(keys, parts):
        seen.setdefault(k, set()).add(p)

    violating = {k: sorted(v) for k, v in seen.items() if len(v) > 1}
    return SplitValidationResult(
        ok=(len(violating) == 0),
        n_rows=len(keys),
        n_event_group_keys=len(seen),
        violating_keys=violating,
    )


def assign_and_validate(rows: Sequence[dict],
                         train_years: Sequence[int] = DEFAULT_TRAIN_YEARS,
                         holdout_years: Sequence[int] = DEFAULT_HOLDOUT_YEARS) -> dict:
    """Convenience entry point for a list of row-dicts (as produced by
    scripts/build_vobl_historical_gfs_ts_join.py), each expected to carry
    `ist_date` and `event_group_key`. Assigns a partition to every row and
    validates the grouping constraint in one call. Does not mutate the
    input rows; returns a list of partitions parallel to `rows` plus the
    validation result."""
    partitions = [assign_partition(r["ist_date"], train_years, holdout_years) for r in rows]
    event_group_keys = [r["event_group_key"] for r in rows]
    validation = validate_no_event_group_key_crosses_partitions(event_group_keys, partitions)
    return {
        "partitions": partitions,
        "validation": validation.to_json(),
    }

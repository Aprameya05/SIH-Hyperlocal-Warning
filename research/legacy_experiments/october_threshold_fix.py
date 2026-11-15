# ── October post-monsoon threshold adjustment ─────────────────────────────
# Context: the v3 model applies a strong seasonal prior (DOY_sin) that
# suppresses Slot 2 probabilities in October even when CAPE and K-Index
# are favourable. Root cause confirmed via walk-forward SHAP analysis
# (CSIR_October_PostMonsoon_Analysis.ipynb, Aug 2026).
#
# Fix: lower Slot 2 operational threshold from 0.226 to 0.10 in October.
# Effect on 2015-2025 test set: POD 0.379 -> 0.621, FAR 0.167 -> 0.474.
# Tradeoff is accepted for airport safety (F-beta b=2 penalises misses 2x).
# Revisit when Himawari archive covers 2016-2025 October months.

from datetime import date as _date

def get_slot2_threshold(base_threshold: float = 0.226) -> float:
    """Return the operational threshold for Slot 2.
    Applies October-specific adjustment for post-monsoon convection."""
    today_month = _date.today().month
    if today_month == 10:
        return 0.10   # October: lower threshold, POD 0.38 -> 0.62
    return base_threshold

# Usage in forecast_action.py — replace the hardcoded threshold line:
#   OLD: threshold = 0.226
#   NEW: threshold = get_slot2_threshold()
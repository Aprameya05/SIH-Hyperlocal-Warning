"""The DRIFT live feed may only contain integrity-verified, honestly-labelled values."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from pipelines.publishing import build_live_feed as b  # noqa: E402
from models.evaluation import summarise_baseline_evaluations as ev  # noqa: E402


def test_live_feed_has_three_hazards_and_no_heuristic_probability():
    d = b.build()
    assert set(d["hazards"]) == {"thunderstorm", "extreme_rainfall", "flash_flood"}
    assert d["mode"] == "LIVE_OPERATIONAL" and "NOT IMD/NDMA" in d["disclaimer"]
    blob = json.dumps(d)
    for banned in ("thunderstorm_probability", "flash_flood_probability", "cloudburst_probability"):
        assert banned not in blob
    assert d["hazards"]["flash_flood"]["status"] == "UNAVAILABLE" and d["hazards"]["flash_flood"]["missing_for_live"]
    assert d["alerts"]["eligible"] == 0 and d["alerts"]["automated_dispatch"] == "DISABLED"


def test_nulls_are_preserved_not_zero_filled():
    d = b.build()
    cols = d["grid"]["columns"]
    assert cols[:2] == ["lat", "lon"] and "cape_0" in cols and "qpe_1" in cols and "ff_susceptibility_proxy" in cols
    assert len(d["grid"]["cells"]) == 992 and all(len(c) == len(cols) for c in d["grid"]["cells"])
    assert any(c[cols.index("ff_research_pu_rank")] is None for c in d["grid"]["cells"])   # absent outside coverage stays null
    assert d["grid"]["terrain"]["hydrology_cells"] < 992                                     # sparse hydrology is not filled
    assert d["hazards"]["extreme_rainfall"]["distinctions"][-1]["status"] == "UNAVAILABLE"  # no cloudburst prediction


def test_a_grid_failing_integrity_is_emitted_unavailable(monkeypatch):
    real = b._load
    def fake(p):
        return {"forged": True} if p == "data/pan_india_grid.json" else real(p)
    monkeypatch.setattr(b, "_load", fake)
    d = b.build()
    assert d["grid"]["cells"] == [] and d["problems"] and d["sources"][0]["state"] == "UNAVAILABLE"


def test_stale_sources_are_marked_stale():
    from datetime import datetime, timedelta, timezone
    d = b.build(now=datetime.now(timezone.utc) + timedelta(days=3))
    assert {s["state"] for s in d["sources"] if s["id"] not in ("vobl_station", "terrain_hydrology")} <= {"STALE", "UNAVAILABLE"}


def test_evaluation_includes_climatology_baseline_and_flags_uncalibrated_score():
    e = {"ts": ev.thunderstorm(), "cb": ev.extreme_rainfall(), "ff": ev.flash_flood()}
    assert all("bss_vs_climatology" in f for f in e["ts"]["folds"])
    assert e["cb"]["summary"]["uncalibrated_beats_climatology_brier"] is False
    assert e["ff"]["validated"] is False and "2-6 h" not in e["ts"]["scope"]

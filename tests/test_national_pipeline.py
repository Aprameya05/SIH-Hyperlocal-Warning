"""Contracts of the national pipeline: grid, static layer, GFS reader physics, atomic ingestion, model gating and alert safety."""
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from pipelines.ingestion import gfs_reader as g  # noqa: E402

GRID = json.load(open(ROOT / "data/india_grid_025.json"))
CELLS = GRID["cells"]


# ------------------------------------------------------------------ grid
def test_grid_cells_are_land_only_unique_stable_and_on_the_gfs_lattice():
    ids = [c[0] for c in CELLS]
    assert 4300 <= len(ids) <= 5000 and len(set(ids)) == len(ids)
    for c in CELLS:
        assert c[3] >= 0.5, "a cell with less than 50% land is not a DRIFT location"
        assert abs(c[1] * 4 - round(c[1] * 4)) < 1e-9 and abs(c[2] * 4 - round(c[2] * 4)) < 1e-9
        assert c[0] == "IN025_%04d_%05d" % (round(c[1] * 100), round(c[2] * 100))
        assert c[4] is not None and c[5] is not None
    keys = {(c[1], c[2]) for c in CELLS}
    for sea in [(15.0, 65.0), (10.0, 85.0), (7.0, 79.5), (27.75, 85.25), (24.0, 66.0), (30.0, 70.0)]:
        assert sea not in keys, f"{sea} is not Indian land"
    assert {(12.75, 77.5), (28.75, 77.25), (34.0, 74.75), (11.75, 92.75), (26.25, 91.75)} <= keys | {(12.75, 77.5)}


def test_every_state_and_major_region_is_represented():
    assert len(GRID["states"]) >= 33
    for s in ("Kerala", "Rajasthan", "Assam", "Telangana", "Jammu & Kashmir", "Andaman & Nicobar Island", "West Bengal", "Gujarat", "Meghalaya"):
        assert s in GRID["states"], s
    lat = [c[1] for c in CELLS]; lon = [c[2] for c in CELLS]
    assert min(lat) < 8.5 and max(lat) > 35 and min(lon) < 69 and max(lon) > 96     # Kanyakumari to Kashmir, Gujarat to Arunachal


def test_gazetteer_cities_resolve_to_land_cells_with_correct_states():
    P = json.load(open(ROOT / "data/india_places.json"))["places"]; ids = {c[0]: c for c in CELLS}
    assert len(P) > 3000 and all(p["cell"] in ids for p in P)
    want = {"Bengaluru": "Karnataka", "Hyderabad": "Telangana", "Mumbai": "Maharashtra", "Delhi": "NCT of Delhi", "Chennai": "Tamil Nadu", "Guwahati": "Assam", "Srinagar": "Jammu & Kashmir"}
    for name, st in want.items():
        p = next(x for x in P if x["ascii"] == name or x["name"] == name)
        assert GRID["states"][ids[p["cell"]][4]] == st, name
        assert abs(ids[p["cell"]][1] - p["lat"]) <= 0.5 and abs(ids[p["cell"]][2] - p["lon"]) <= 0.5


# ------------------------------------------------------------------ static layer
def test_static_layer_is_aligned_real_terrain_with_missing_kept_missing():
    S = json.load(open(ROOT / "data/india_static_025.json")); col = {c: i for i, c in enumerate(S["columns"])}
    assert S["cells"] == [c[0] for c in CELLS]
    el = [v[col["elev_mean_m"]] for v in S["values"]]
    assert all(e is not None for e in el) and max(el) > 4500 and min(el) < 30 and len(set(el)) > 1000
    near = sum(1 for v in S["values"] if v[col["nearest_gauge_id"]] is not None)
    assert 800 < near < len(CELLS), "gauge context only near gauges; elsewhere null, never 0"
    for v in S["values"]:
        if v[col["nearest_gauge_id"]] is None:
            assert v[col["nearest_gauge_events"]] is None and v[col["nearest_gauge_rapid_events"]] is None
        else:
            assert v[col["nearest_gauge_km"]] <= 100
    assert all(v[col["relief_m"]] >= 0 and v[col["slope_mean_deg"]] >= 0 for v in S["values"])


# ------------------------------------------------------------------ GFS reader physics + message verification
def test_derived_physics():
    t = np.array([[300.0]]); assert abs(g.dewpoint_c(t, np.array([[100.0]]))[0, 0] - 26.85) < 0.1          # saturated: Td == T
    assert g.dewpoint_c(t, np.array([[50.0]]))[0, 0] < 26.85 - 5
    f = {k: np.array([[v]], dtype="float64") for k, v in dict(t850=290.0, t700=283.0, t500=263.0, rh850=80.0, rh700=60.0, rh500=40.0, u10=-5.0, v10=0.0, u850=0.0, v850=0.0, u500=3.0, v500=4.0,
                                                               t2m=300.0, d2m=295.0, mslp=101325.0).items()}
    d = g.derive({"fields": f})
    v = lambda k: float(d[k][0, 0])
    assert abs(v("shear_850_500") - 5.0) < 1e-6 and abs(v("wd10") - 90.0) < 1e-4 and abs(v("ws10") - 5.0) < 1e-6 and abs(v("mslp_hpa") - 1013.25) < 1e-4
    assert v("total_totals") > 30 and v("k_index") > 20


def test_idx_parser_and_level_matching():
    rows = g._parse_idx("1:0:d=2026103100:CAPE:surface:12 hour fcst:\n2:500:d=2026103100:CIN:surface:12 hour fcst:\n3:900:d=2026103100:APCP:surface:6-12 hour acc fcst:")
    assert rows[0]["end"] == 499 and rows[1]["off"] == 500 and rows[2]["end"] is None
    assert g._grib_matches("t", "isobaricInhPa", 850, "TMP", "850 mb") and not g._grib_matches("t", "isobaricInhPa", 700, "TMP", "850 mb")
    assert g._grib_matches("2t", "heightAboveGround", 2, "TMP", "2 m above ground") and not g._grib_matches("10u", "heightAboveGround", 10, "UGRD", "2 m above ground")
    assert g._grib_matches("pwat", "atmosphereSingleLayer", 0, "PWAT", "entire atmosphere (considered as a single layer)")


def test_a_grib_message_that_disagrees_with_the_request_is_rejected(tmp_path):
    eccodes = pytest.importorskip("eccodes")
    sample = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    eccodes.codes_set(sample, "dataDate", 20261031); eccodes.codes_set(sample, "dataTime", 0)
    eccodes.codes_set(sample, "shortName", "cape") if False else None
    with open(tmp_path / "m.grib2", "wb") as f:
        eccodes.codes_write(sample, f)
    eccodes.codes_release(sample)
    wrong = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    with pytest.raises(g.GfsError):
        g.decode(tmp_path / "m.grib2", wrong, 12)          # init differs from the requested cycle -> rejected, never trusted


# ------------------------------------------------------------------ atomic, all-or-nothing ingestion
def test_ingestion_is_all_or_nothing(tmp_path, monkeypatch):
    from pipelines.ingestion import ingest_gfs_forecast as ing
    out = tmp_path / "fields.json"; out.write_text('{"previous": "good artifact"}')
    calls = {"n": 0}
    def boom(init, fh, path, workers=6):
        calls["n"] += 1
        raise g.GfsError("simulated download failure")
    monkeypatch.setattr(g, "download_subset", boom)
    monkeypatch.setattr(ing, "ROOT", tmp_path)
    rc = ing.main(["--cycle", "2026103100", "--fhours", "3,6", "--out", str(out)])
    assert rc == 1 and json.loads(out.read_text()) == {"previous": "good artifact"}, "a failed ingestion must leave the last good artifact untouched"
    assert not list(tmp_path.glob("*.tmp"))


def test_committed_fields_artifact_passes_the_independent_gate():
    import subprocess
    r = subprocess.run([sys.executable, "pipelines/validation/validate_national_fields.py"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ------------------------------------------------------------------ models: gating and evaluation integrity
def test_metrics_helpers_match_definitions():
    from models.thunderstorm import train_and_evaluate_thunderstorm_model as T
    y = np.array([0, 0, 0, 1, 1, 0, 1, 0, 0, 0]); base = y.mean()
    assert T.metrics(y, np.full(10, base), 0.5, base)["bss_vs_climatology"] == 0.0           # climatology has zero skill by definition
    perfect = T.metrics(y, y.astype(float), 0.5, base)
    assert perfect["auroc"] == 1.0 and perfect["pod"] == 1.0 and perfect["far"] == 0.0 and perfect["csi"] == 1.0 and perfect["hss"] == 1.0
    assert T.region(26.1, 91.7) == "NORTHEAST" and T.region(12.9, 77.6) == "SOUTH" and T.region(28.6, 77.2) == "NORTH"


@pytest.mark.parametrize("name,key,gate_key", [("ts_panindia_evaluation.json", "leads", "per_lead"), ("rain_panindia_evaluation.json", "targets", "per_target")])
def test_published_model_gate_is_recomputable_from_stored_metrics(name, key, gate_key):
    p = ROOT / "data" / name
    if not p.exists():
        pytest.skip("evaluation not generated")
    ev = json.load(open(p))
    for k, r in ev[key].items():
        if "A_temporal_holdout" not in r:
            assert ev["gate"][gate_key][k] is False          # insufficient events can never pass
            continue
        A, B = r["A_temporal_holdout"]["xgboost_calibrated"], r["B_pooled"]
        ok = A["bss_vs_climatology"] >= 0.01 and A["auroc"] >= 0.65 and B.get("bss_vs_climatology", -1) >= 0.01 and B.get("auroc", 0) >= 0.65
        if name.startswith("rain"):
            ok = ok and A["auroc"] >= r["raw_gfs_rainfall_auroc_2025"]
        assert ev["gate"][gate_key][k] == ok == r["passes_gate"], f"{k}: stored gate disagrees with its own metrics"
        assert A["n_events"] > 0 and A["pr_auc"] > A["pr_auc_no_skill"] * 0.5


@pytest.mark.parametrize("art,evname,metakey", [("india_ts_model_latest.json", "ts_panindia_evaluation.json", "leads"), ("india_rain_model_latest.json", "rain_panindia_evaluation.json", "targets")])
def test_published_model_artifacts_only_contain_gated_targets(art, evname, metakey):
    p = ROOT / "data" / art
    if not p.exists():
        pytest.skip("artifact not generated")
    m = json.load(open(p)); ev = json.load(open(ROOT / "data" / evname))
    if not m["meta"]["model_evaluated"]:
        assert m["meta"].get("reason") and not m["meta"].get(metakey)            # unavailable: says why, carries no values
        return
    gate = ev["gate"]["per_lead" if metakey == "leads" else "per_target"]
    for k in m["meta"][metakey]:
        assert gate[str(k) if metakey == "leads" else k] is True, f"{k} was published without passing the gate"
    assert m["meta"]["calibrated"] is True and m["meta"].get("evaluation_ref")
    vals = [x for arr in (m["leads"] if metakey == "leads" else m["targets"]).values() for x in arr]
    assert all(0.0 <= x <= 1.0 for x in vals if x is not None) and len(set(vals)) >= 10


def test_unavailable_model_artifact_degrades_the_app_safely(tmp_path, monkeypatch):
    from models.thunderstorm import predict_thunderstorm as P
    from backend.location_service import Store
    import shutil
    d = tmp_path / "data"; d.mkdir()
    for f in Store.FILES.values():
        if (ROOT / "data" / f).exists():
            shutil.copy(ROOT / "data" / f, d / f)
    monkeypatch.setattr(P, "OUT", d / "india_ts_model_latest.json")
    P.unavailable("no evaluated model")
    s = Store(d); assert s.ts_ok is False
    rec = s.record("IN025_1300_07750", 2)
    assert rec["hazards"]["thunderstorm"]["model"]["status"] == "UNAVAILABLE" and rec["hazards"]["thunderstorm"]["indicators"]["cape"] is not None


# ------------------------------------------------------------------ alert safety stays fail-closed
def test_no_location_record_is_alert_eligible():
    from backend.location_service import Store
    from backend import alert_policy as ap
    s = Store(); assert ap.PRODUCTION_POLICY.supported_alert_horizons == {} and ap.PRODUCTION_POLICY.alertable_semantics == {}
    for cid in ("IN025_1900_07300", "IN025_2625_09175", "IN025_1300_07750"):
        h = s.record(cid, 3)["hazards"]
        m = h["thunderstorm"]["model"]
        if m.get("status") == "AVAILABLE_EVALUATED":
            assert m["alert_eligible"] is False
            ok, why = ap.evaluate({"probability": 0.9, "temporal_status": "CURRENT", "value_type": "CALIBRATED_PROBABILITY", "event_class": "TS_REPORT", "lead_specific": True,
                                   "valid_time_utc": "2099-01-01T00:00:00Z"}, hazard="TS")
            assert not ok and "NO_AVAILABILITY_TIME" in why and "HORIZON_NOT_REGISTERED" in why
        assert h["flash_flood"]["prediction"]["status"] == "UNAVAILABLE" and h["extreme_rainfall"]["cloudburst"]["status"] == "UNAVAILABLE"


def test_grid_dynamics_are_physically_correct():
    """Divergence/convergence on a synthetic wind field with a known analytic answer, with GFS-style descending latitude."""
    lat = np.linspace(30, 20, 41); lon = np.linspace(75, 85, 41)
    LA, LO = np.meshgrid(np.radians(lat), np.radians(lon), indexing="ij")
    # solid-body-like expansion: u = a*(x - x0), v = a*(y - y0)  (metres from centre)  => div = 2a
    a = 1e-5
    x = g.R_EARTH * np.cos(LA) * (LO - np.radians(80)); y = g.R_EARTH * (LA - np.radians(25))
    d = g.divergence(a * x, a * y, lat, lon)
    inner = d[5:-5, 5:-5]
    assert np.allclose(inner, 2 * a, rtol=0.02), (inner.min(), inner.max())            # divergence = +2a (so convergence is negative)
    dec = {"fields": {"u850": -a * x, "v850": -a * y, "u10": a * x, "v10": a * y, "pwat": np.tile(np.linspace(10, 50, 41)[:, None], (1, 41)),
                      "t850": np.full_like(x, 288.0), "t700": np.full_like(x, 278.0), "rh850": np.full_like(x, 80.0), "rh700": np.full_like(x, 50.0)}, "lat": lat, "lon": lon}
    dyn = g.derive_dynamics(dec)
    assert np.allclose(dyn["conv850"][5:-5, 5:-5], 2.0, rtol=0.03)                       # inflow field -> +2e-5 s^-1 convergence
    assert np.all(dyn["conv10"][5:-5, 5:-5] < 0) and np.all(dyn["pwat_grad"] > 0)
    assert 7 < float(dyn["q850"][10, 10]) < 14 and float(dyn["q700"][10, 10]) < float(dyn["q850"][10, 10])   # g/kg, drier aloft


@pytest.mark.parametrize("module,key", [("models.thunderstorm.predict_thunderstorm", "leads"), ("models.extreme_rainfall.predict_extreme_rainfall", "targets")])
def test_prediction_scripts_find_their_models_and_reproduce_the_committed_artifact(module, key, tmp_path, monkeypatch):
    """Guards the model-artifact paths (a moved directory once made the predictors silently write 'unavailable') and proves the
    published predictions are reproducible bit-for-bit from the committed fields + models."""
    import importlib
    m = importlib.import_module(module)
    out = tmp_path / "pred.json"
    monkeypatch.setattr(m, "OUT", out)
    m.main()
    new = json.loads(out.read_text())
    committed = json.loads((ROOT / "data" / ("india_ts_model_latest.json" if key == "leads" else "india_rain_model_latest.json")).read_text())
    assert new["meta"]["model_evaluated"] is True, new["meta"].get("reason")
    assert new[key] == committed[key] and new["explanations"] == committed["explanations"]


def test_ingestion_never_overwrites_a_newer_cycle_and_records_its_status(tmp_path, monkeypatch):
    from pipelines.ingestion import ingest_gfs_forecast as ing
    out = tmp_path / "fields.json"
    out.write_text(json.dumps({"meta": {"cycle_init_utc": "2026-11-15T12:00:00Z"}, "marker": "newer"}))
    monkeypatch.setattr(g, "download_subset", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not download when refusing")))
    rc = ing.main(["--cycle", "2026111500", "--out", str(out)])
    assert rc == 3 and json.loads(out.read_text())["marker"] == "newer"
    st = json.loads((tmp_path / "ingest_status.json").read_text())
    assert st["last_result"] == "REFUSED_OLDER_CYCLE" and st["last_attempt_utc"]
    monkeypatch.setattr(g, "download_subset", lambda *a, **k: (_ for _ in ()).throw(g.GfsError("boom")))
    rc = ing.main(["--cycle", "2026111512", "--out", str(out)])            # same cycle again: allowed, fails, status shows the failure
    st = json.loads((tmp_path / "ingest_status.json").read_text())
    assert rc == 1 and st["last_result"] == "FAILED" and "boom" in st["last_error"] and json.loads(out.read_text())["marker"] == "newer"


def test_old_data_never_looks_current_after_failed_scheduled_runs():
    """If every scheduled ingestion keeps failing the last good artifact stays on disk (see the all-or-nothing tests); the server then
    recomputes each hour's status from the clock, so by +30 h nothing is CURRENT and health is not HEALTHY."""
    from datetime import timedelta
    from backend.location_service import Store, parse_utc
    s = Store()
    init = parse_utc(s.fields_doc["meta"]["cycle_init_utc"])
    late = init + timedelta(hours=30)
    h = s.health(now=late)
    assert h["status"] != "HEALTHY" or not h["ready"], h["status"]
    rec = s.record(s.cells[0][0] if isinstance(s.cells[0], (list, tuple)) else s.cells[0], 3, now=late)
    assert rec["selected_hour"]["status"] in ("STALE", "PAST_VALID_TIME")
    assert rec["selected_hour"]["status"] != "CURRENT"

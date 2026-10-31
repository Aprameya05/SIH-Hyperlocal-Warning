"""Two real GFS cycles through the same service: values follow the forecast cycle, and each cycle carries its own provenance.
Needs cache/alt/india_fields_latest.json (python scripts/ingest_india_fields.py --cycle <YYYYMMDDHH> --out cache/alt/india_fields_latest.json); skipped otherwise."""
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ALT = ROOT / "cache" / "alt" / "india_fields_latest.json"
from backend.location_service import Store, parse_utc  # noqa: E402

CITIES = ["IN025_1900_07300", "IN025_2875_07725", "IN025_1300_07750", "IN025_2250_08825", "IN025_2625_09175", "IN025_1750_07850", "IN025_2325_07750", "IN025_1300_08025", "IN025_3400_07475", "IN025_2700_07100"]


@pytest.fixture(scope="module")
def stores(tmp_path_factory):
    if not ALT.exists() or not (ROOT / "data/india_fields_latest.json").exists():
        pytest.skip("second cycle not ingested")
    d = tmp_path_factory.mktemp("alt") / "data"; d.mkdir()
    for f in Store.FILES.values():
        if (ROOT / "data" / f).exists():
            shutil.copy(ROOT / "data" / f, d / f)
    shutil.copy(ALT, d / "india_fields_latest.json")
    return Store(ROOT / "data"), Store(d)


def test_both_cycles_verify_and_have_distinct_provenance(stores):
    a, b = stores
    assert a.fields_ok and b.fields_ok, (a.fields_problems, b.fields_problems)
    ia, ib = a._hour_meta()[0], b._hour_meta()[0]
    assert ia != ib
    pa = [parse_utc(f["published_at_utc"]) for f in a.fields_doc["meta"]["files"]]; pb = [parse_utc(f["published_at_utc"]) for f in b.fields_doc["meta"]["files"]]
    assert min(pa) > ia and min(pb) > ib and max(pb) < min(pa)             # each publication follows its own init; the older cycle was published earlier


def test_same_valid_time_from_two_cycles_gives_different_forecasts_for_the_same_place(stores):
    a, b = stores
    fa = {f["valid_time_utc"]: k for k, f in enumerate(a.fields_doc["meta"]["files"])}
    fb = {f["valid_time_utc"]: k for k, f in enumerate(b.fields_doc["meta"]["files"])}
    common = sorted(set(fa) & set(fb)); assert common, "cycles share no valid time"
    v = common[-1]; diff = 0
    for cid in CITIES:
        ra, rb = a.record(cid, fa[v]), b.record(cid, fb[v])
        assert ra["selected_hour"]["valid_time_utc"] == rb["selected_hour"]["valid_time_utc"] == v
        assert ra["cycle"]["model_init_utc"] != rb["cycle"]["model_init_utc"]
        if (ra["fields"]["pwat"]["value"], ra["fields"]["t2m_c"]["value"]) != (rb["fields"]["pwat"]["value"], rb["fields"]["t2m_c"]["value"]):
            diff += 1
    assert diff >= 8, f"only {diff}/10 cities differ between cycles for the same valid time"


def test_each_cycle_ages_by_its_own_init(stores):
    a, b = stores
    ia, ib = a._hour_meta()[0], b._hour_meta()[0]
    from datetime import timedelta
    now = ib + timedelta(hours=20)                               # 20 h after the older cycle, 8 h after the newer
    ra, rb = a.record(CITIES[0], 0, now=now), b.record(CITIES[0], 0, now=now)
    assert rb["cycle"]["age_hours"] > ra["cycle"]["age_hours"] and rb["selected_hour"]["status"] == "STALE" and ra["selected_hour"]["status"] != "STALE"

"""The browser twin (assets/drift_location.js) must return the same data as the Python service for the same cell and clock."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
from backend.location_service import Store  # noqa: E402

CELLS = ["IN025_1300_07750", "IN025_1900_07300", "IN025_2875_07725", "IN025_3400_07475", "IN025_1300_08025", "IN025_2250_08825",
         "IN025_2625_09175", "IN025_1750_07850", "IN025_2700_07075", "IN025_2575_09200"]


def close(a, b, tol=2e-3, path=""):
    if isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), f"{path}: keys {set(a) ^ set(b)}"
        for k in a:
            close(a[k], b[k], tol, f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            close(x, y, tol, f"{path}[{i}]")
    elif isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)):
        assert abs(a - b) <= tol * max(1.0, abs(a)), f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def test_python_and_javascript_records_agree():
    if not (ROOT / "data/india_fields_latest.json").exists():
        pytest.skip("fields artifact not generated")
    s = Store()
    init, _ = s._hour_meta()
    now = init.replace(hour=init.hour) .__class__(init.year, init.month, init.day, 14, 20, tzinfo=timezone.utc)
    specs = [f"{c}:{h}" for c in CELLS for h in (0, 3, 7)]
    out = subprocess.run(["node", "tests/location_parity_runner.mjs", now.isoformat().replace("+00:00", "Z"), *specs], cwd=ROOT, capture_output=True, text=True, check=True)
    js = json.loads(out.stdout)
    assert js["fieldsOk"] is True
    for spec in specs:
        cid, h = spec.split(":")
        py = json.loads(json.dumps(s.record(cid, int(h), now=now)))
        j = js["records"][spec]
        for k in ("location", "static", "cycle", "selected_hour"):
            close(py[k], j[k], path=f"{spec}.{k}")
        for k in py["fields"]:
            close({x: py["fields"][k][x] for x in ("value", "series", "unit", "kind", "status", "safe_as_current")},
                  {x: j["fields"][k][x] for x in ("value", "series", "unit", "kind", "status", "safe_as_current")}, path=f"{spec}.fields.{k}")
        for hz in ("thunderstorm", "extreme_rainfall", "flash_flood"):
            pyh, jh = py["hazards"][hz], j["hazards"][hz]
            if hz == "thunderstorm":
                close(pyh["indicators"], jh["indicators"], path=f"{spec}.{hz}")
                close(pyh["model"], jh["model"], path=f"{spec}.{hz}.model")
            elif hz == "extreme_rainfall":
                close(pyh["heavy_rain_day_outlook"]["value"], jh["heavy_rain_day_outlook"]["value"], path=f"{spec}.cb")
                close(pyh["model_guidance"], jh["model_guidance"], path=f"{spec}.rain")
                close(pyh["short_duration_extreme_rainfall"], jh["short_duration_extreme_rainfall"], path=f"{spec}.short")
                assert jh["cloudburst"]["status"] == "UNAVAILABLE" == pyh["cloudburst"]["status"]
            else:
                close(pyh["screening_index"], jh["screening_index"], path=f"{spec}.ff")
    assert [r["cell_id"] for r in js["search"] if "cell_id" in r] == [r["cell_id"] for r in s.search("mumbai", 5) if "cell_id" in r]
    assert js["resolve"][0]["cell_id"] == s.resolve(12.97, 77.59)["cell_id"] and js["resolve"][1]["covered"] is False


def test_browser_twin_rejects_a_forged_artifact(tmp_path):
    src = ROOT / "data"
    doc = json.load(open(src / "india_fields_latest.json"))
    doc["meta"]["files"][1]["valid_time_utc"] = "2020-01-01T00:00:00Z"
    script = (
        "const L=require('./assets/drift_location.js'),fs=require('fs');const j=f=>JSON.parse(fs.readFileSync('data/'+f));"
        f"const f=JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'f.json'))}));"
        "const S=L.create({grid:j('india_grid_025.json'),places:j('india_places.json'),fields:f,statics:j('india_static_025.json')});"
        "console.log(JSON.stringify({ok:S.fieldsOk,p:S.problems,fields:S.record('IN025_1300_07750',2,Date.now()).fields}))")
    (tmp_path / "f.json").write_text(json.dumps(doc))
    out = json.loads(subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    assert out["ok"] is False and any("valid time" in p for p in out["p"]) and out["fields"] == {}

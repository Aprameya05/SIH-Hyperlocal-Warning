"""scripts/ci_publish_needed.py: decides whether the site must be republished and which feed is overdue."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("ci_publish_needed", Path(__file__).resolve().parent.parent / "scripts" / "ci_publish_needed.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _setup(monkeypatch, tmp_path, cycle_age_h, scan_age_min, live_same=True):
    now = datetime.now(timezone.utc)
    (tmp_path / "data").mkdir()
    fields = {"meta": {"cycle_init_utc": _iso(now - timedelta(hours=cycle_age_h)), "generated_at_utc": "G1"}}
    sat = {"meta": {"scene_nominal_utc": _iso(now - timedelta(minutes=scan_age_min)), "generated_at_utc": "S1"}}
    (tmp_path / "data" / "india_fields_latest.json").write_text(json.dumps(fields))
    (tmp_path / "data" / "india_satellite_ctt_latest.json").write_text(json.dumps(sat))
    (tmp_path / "forecast.json").write_text(json.dumps({"generated_at": "F1"}))
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def live(path):
        same = {"data/india_fields_latest.json": fields, "data/india_satellite_ctt_latest.json": sat, "forecast.json": {"generated_at": "F1"}}
        if path in same:
            if live_same:
                return same[path]
            d = json.loads(json.dumps(same[path]))
            if "meta" in d:
                d["meta"]["generated_at_utc"] = "OLD"
            else:
                d["generated_at"] = "OLD"
            return d
        raise OSError("not in the fixture")
    monkeypatch.setattr(mod, "_live", live)


def _run(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    mod.main()
    return dict(line.split("=", 1) for line in capsys.readouterr().out.strip().splitlines())


def test_up_to_date_site_needs_no_publish_and_nothing_is_overdue(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, cycle_age_h=5, scan_age_min=20)
    out = _run(monkeypatch, tmp_path, capsys)
    assert out["publish_needed"] == "false" and out["fields_overdue"] == "false" and out["satellite_overdue"] == "false"


def test_site_serving_older_artifacts_must_be_republished(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, cycle_age_h=5, scan_age_min=20, live_same=False)
    out = _run(monkeypatch, tmp_path, capsys)
    assert out["publish_needed"] == "true" and "site serves OLD" in out["reason"]


def test_missed_gfs_cycle_and_quiet_satellite_are_reported_overdue(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, cycle_age_h=12, scan_age_min=100)
    out = _run(monkeypatch, tmp_path, capsys)
    assert out["fields_overdue"] == "true" and out["satellite_overdue"] == "true"


def test_unreadable_live_site_means_publish(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, cycle_age_h=5, scan_age_min=20)
    monkeypatch.setattr(mod, "_live", lambda path: (_ for _ in ()).throw(OSError("down")))
    out = _run(monkeypatch, tmp_path, capsys)
    assert out["publish_needed"] == "true"

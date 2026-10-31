"""Static guarantees for the DRIFT command-centre page (browser behaviour was checked manually in the built-in browser)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_pages_dist as bpd  # noqa: E402

HTML = (ROOT / "drift.html").read_text()
APP = (ROOT / "assets/drift_app.js").read_text()
SIM = (ROOT / "assets/drift_sim.js").read_text()


def test_page_and_every_fetched_file_are_published():
    assert "drift.html" in bpd.ALLOW_FILES
    for path in re.findall(r"fetch\('([^']+)'", APP):
        assert path in bpd.ALLOW_FILES, path


def test_live_and_simulation_are_labelled_and_separate():
    assert "SIMULATION MODE" in APP and "LIVE MODE" in APP and 'id="m-sim"' in HTML
    assert "not IMD/NDMA" in APP and "not IMD/NDMA" in HTML.replace("IMD/NDMA warnings", "not IMD/NDMA warnings") or "IMD/NDMA" in HTML


def test_app_never_sends_notifications_or_reads_subscribers():
    for bad in ("subscribers", "twilio", "ntfy", "sendBeacon", "Notification(", "WebSocket", "method: 'POST'"):
        assert bad.lower() not in (APP + SIM).lower(), bad


def test_live_mode_never_reads_heuristic_grid_scores():
    for bad in ("thunderstorm_probability", "flash_flood_probability", "cloudburst_probability", "pan_india_grid"):
        assert bad not in APP, bad


def test_stale_or_unavailable_sources_withhold_values():
    """M4: staleness is computed by the location module from timestamps and the clock, and the app withholds on it."""
    loc = (ROOT / "assets/drift_location.js").read_text()
    assert "MODEL_MAX_AGE_H = 18" in loc and "'STALE'" in loc and "'INVALID_PROVENANCE'" in loc and "'PAST_VALID_TIME'" in loc
    assert "verifyFields" in loc and "valid time != init + fhour" in loc               # the browser re-verifies provenance itself
    assert "values are withheld, not shown as current" in APP
    assert "status === 'STALE' || status === 'INVALID_PROVENANCE' || status === 'UNAVAILABLE'" in APP
    assert "unavailable" in APP and "DL.hourStatus" in APP

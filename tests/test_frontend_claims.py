"""Claim lint for everything the public site ships: typed model metrics, invented feeds, operational/alert/dispatch wording and
unsupported hazard claims must not reappear. Provenance for what IS shown: docs/DASHBOARD_VALUE_PROVENANCE.md."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [ROOT / "frontend" / "index.html", ROOT / "frontend" / "drift.html", *sorted((ROOT / "frontend" / "assets").glob("*.js")), ROOT / "frontend" / "sw.js", ROOT / "frontend" / "404.html"]
BANNED = ["Doppler", "Blitzortung", "onrender.com", "corsproxy", "XGBoost v6", "Isotonic", "35-YEAR RECORD'", "ALL CLEAR", "IMD C-BAND", "OPERATIONAL ACTION REQUIRED",
          "Dispatched to", "Dispatch to", "MONSOON BASELINE", "FIX APPLIED IN PRODUCTION", "ALL SYSTEMS OPERATIONAL", "CB_METRICS", "FF_METRICS", "SYNOPTIC_REGIMES",
          "SHAP_DATA_FALLBACK", "inside the 2-6 h target", "Exceeds 2h minimum", "SIGMET/AIRMET DRAFT", "Subscribe to Alerts", "RUNWAY 09L", "Runway 09L", "S-BAND",
          "PRODUCTION MODEL", "Llama", "/rag/", "october_threshold_fix", "ATC-Grade", "official use by ATC", "IMD NOWCAST", "v4 Ensemble", "v5 Temporal", "NOWCAST ACTIVE", "HYPERLOCAL HAZARD BULLETIN", "S-BAND RADAR"]
TYPED_METRIC = re.compile(r"\b(auroc|auprc|pod|far|csi|hss|brier|bss)\s*:\s*'?0?\.\d{2,}", re.I)


def test_no_banned_claims_or_feeds_in_the_public_frontend():
    hits = []
    for f in FILES:
        t = f.read_text(errors="ignore")
        for b in BANNED:
            if b in t:
                hits.append((f.name, b))
    assert not hits, hits


def test_no_typed_model_metrics_in_the_public_frontend():
    hits = []
    for f in FILES:
        for n, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            if TYPED_METRIC.search(line):
                hits.append((f.name, n, line.strip()[:100]))
    assert not hits, hits


def test_no_invented_defaults_for_missing_values():
    t = (ROOT / "frontend" / "index.html").read_text()
    for bad in ["?? 0.5;", "|| 40) / 70", "initiation_risk ?? 'MINIMAL'", "|| 'STABLE'", "|| 'VFR'", "|| 0.30;"]:
        assert bad not in t, bad
    # the legacy station page lists no cloudburst / flash-flood selectable hazard and no dispatch controls
    assert "disabled={cfg.key !== 'ts'}" in t
    assert "handleSubscribe" not in t and "setSentTo" not in t


def test_unavailable_hazards_stay_unavailable_in_the_command_centre():
    app = (ROOT / "frontend" / "assets" / "drift_app.js").read_text()
    assert "Flash-flood prediction UNAVAILABLE" in app and "not a cloudburst" in app.lower()

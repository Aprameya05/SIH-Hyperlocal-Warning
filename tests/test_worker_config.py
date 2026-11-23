import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_worker_config", REPO / "scripts" / "check_worker_config.py")
cwc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cwc)


def test_committed_worker_passes_static_checks():
    assert cwc.static_problems() == []


def test_default_secret_and_secrets_in_vars_are_rejected(tmp_path, monkeypatch):
    w = tmp_path / "worker"
    w.mkdir()
    (w / "wrangler.toml").write_text('[[kv_namespaces]]\nbinding = "SUBSCRIBERS"\nid = "x"\n[vars]\nTOKEN_SECRET = "abc"\n')
    (w / "index.js").write_text("const t = env.TOKEN_SECRET || 'default'; env.ADMIN_KEY;")
    monkeypatch.setattr(cwc, "WORKER", w)
    probs = cwc.static_problems()
    assert any("must not hold secrets" in p for p in probs)
    assert any("default value" in p for p in probs)

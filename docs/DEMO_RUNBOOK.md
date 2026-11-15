# Demo runbook

1. Setup: `make install` (or `pip install -r backend/requirements.txt`), `cd tests/e2e && npm ci`.
2. Refresh data (network): `make ingest` then `make predict`.
3. Verify: `make test` and `make gate` (release-readiness gate must print PASS).
4. Serve: `make serve` and open `http://localhost:8000/drift.html`.
5. Walkthrough: search "Mumbai" -> cell, state/district, valid time, provenance badge; open the thunderstorm card (probability, calibration note, SHAP drivers); show cloudburst/flash-flood cards as **Unavailable** with reasons; open Compare; show the stale-data banner by loading a cycle > 18 h old.
6. Say plainly: alerting is disabled, no operational lead is claimed, simulation mode is visibly labelled and cannot alert.
7. Reset test-mutated files before committing: `git checkout -- data/pan_india_grid.json processed/indofloods/rainfall_context_preview.csv`.

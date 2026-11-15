PY ?= python3
.PHONY: help  install ingest predict test e2e gate bundle serve inventory docs
help: ; @grep -E '^[a-z-]+:' Makefile | cut -d: -f1
install: ; $(PY) -m pip install -r requirements/dev.txt
ingest: ; $(PY) pipelines/ingestion/ingest_gfs_forecast.py
predict: ; $(PY) models/thunderstorm/predict_thunderstorm.py && $(PY) models/extreme_rainfall/predict_extreme_rainfall.py
test: ; $(PY) -m pytest tests -q
e2e: ; cd tests/e2e && npm test
gate: bundle ; $(PY) pipelines/validation/check_release_readiness.py pages_dist
bundle: ; $(PY) pipelines/publishing/build_pages_bundle.py pages_dist
serve: ; cd frontend && $(PY) -m http.server 8000
inventory: ; $(PY) scripts/diagnostics/inventory_repository.py
docs: ; $(PY) scripts/utilities/generate_validation_docs.py && $(PY) scripts/utilities/run_location_acceptance.py

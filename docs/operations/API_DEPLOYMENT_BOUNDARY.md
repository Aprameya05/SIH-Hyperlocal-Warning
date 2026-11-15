# Static site vs the `/v1` API: what is deployed and what is not

| Capability | Where it runs | Deployed on Cloudflare Pages? | Tested how |
|---|---|---|---|
| Command centre UI (`/drift`), legacy station dashboard (`/`) | Browser, from static files | **Yes** | Playwright (local bundle and the preview URL) |
| Location search / resolve / region / compare / forecast / explain | Browser, `frontend/assets/location_engine.js` + `drift_location.js` reading the published JSON | **Yes** (client-side) | Playwright, JS tests, Python/JS parity test |
| `/v1/*` FastAPI service (`backend/location_api.py`, hostable app `backend/api_app.py`) | A server process | **No** | In-process `TestClient` tests only (`tests/test_location_api.py`, `tests/test_api_deployment_boundary.py`, `tests/test_location_acceptance.py`) |
| Alert dispatch | Not running | No (disabled, fail-closed) | `tests/test_national_pipeline.py::test_no_location_record_is_alert_eligible` |

Local API tests prove the API code, not a deployment. On the static host `/v1/*` now returns a real **404** (the bundle ships `404.html`); before, Pages' single-page fallback answered `200 text/html`.

## Recommended hosting for the API (not done; needs owner approval)
- A small container/VM or serverless Python host running `uvicorn backend.api_app:app` (read-only; needs `data/*.json` artifacts, about 60 MB, refreshed by the same workflow that builds the bundle).
- CORS is deny-by-default: set `DRIFT_CORS_ORIGINS=https://sih-hyperlocal-warning.pages.dev` (exact origins, no wildcard; the app refuses `*`), GET/HEAD/OPTIONS only, no credentials.
- Put it behind HTTPS on its own origin (for example `api.<domain>`), add rate limiting at the edge, and point the frontend at it through an explicit `DRIFT_API_BASE` only after the API is live. Until then the browser engine is the single source of truth.
- Health checks: `/v1/health` (dependency-level, server clock) and `/v1/ready`; alert on `UNHEALTHY` or a GFS cycle older than 18 h.
- Keep one artifact for both paths: the API and the browser engine read the same files, and `tests/test_location_parity.py` keeps their results equal.

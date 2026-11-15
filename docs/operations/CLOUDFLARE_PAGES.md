# Cloudflare Pages: how DRIFT is deployed and why the Git build check fails

## Two paths into the same Pages project (`sih-hyperlocal-warning`)
1. **Production, via GitHub Actions** (`.github/workflows/forecast_update.yml`): builds the allow-list bundle with
   `python3 pipelines/publishing/build_pages_bundle.py pages_dist`, runs the validators and release gate, then
   `wrangler pages deploy pages_dist`. The bundle slims `data/unified_forecast.json` (about 20 MB) and excludes datasets, models and subscriber data.
2. **Cloudflare's own Git integration** (the "Cloudflare Pages" check on every commit/PR): the project has **no build command and no output directory**,
   so Pages uploads the **repository root** as-is.

## Why the Git-integration build fails
Build log for commit `0d9367e` (PR #1): `No build command specified. Skipping build step.` then
`Error: Pages only supports files up to 25 MiB in size - data/unified_forecast.json is 30.5 MiB`.
The same check also failed on `main` (commits before this PR), so this is **not** caused by the repository restructuring. The tracked tree also holds
datasets far above the limit (e.g. `data/external/historical_gfs/phase20_full_predictor_dataset.csv`, 80 MiB), so the repository root can never be a valid Pages output, and since the
frontend now lives in `frontend/` it would not contain a root `index.html` either.

## Fix (project settings; needs the owner, not done by the agent)
In Cloudflare dashboard -> Workers & Pages -> `sih-hyperlocal-warning` -> Settings -> Builds:
- Build command: `python3 pipelines/publishing/build_pages_bundle.py pages_dist` (standard library only; Python is available on the Pages build image)
- Build output directory: `pages_dist`
- Leave the production branch as it is. Or disable automatic Git builds for the project and rely on the workflow path.

Verify any bundle before deploying: `python3 scripts/diagnostics/check_bundle_references.py pages_dist` (exact-case references, 25 MiB limit, forbidden files) and `make gate`.

## Settings applied (with the owner's approval, 2026-11-15)
- Branch control: production branch `main`; **automatic production deployments disabled**; preview deployments for all non-production branches stay enabled. Production is deployed only by the GitHub Actions Wrangler workflow.
- Build command `python3 pipelines/publishing/build_pages_bundle.py pages_dist`, output directory `pages_dist`, root directory = repository root.
- Pages' Python build image pip-installs a project when it finds `pyproject.toml`; `pyproject.toml` therefore declares no runtime dependencies (the bundle builder is standard-library only).

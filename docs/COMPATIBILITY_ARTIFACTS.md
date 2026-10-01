# Compatibility Artifacts — 2026-09-30

Per Part 5 of the request: the existing frontend (`index.html`) fetches `forecast.json`, `data/pan_india_grid.json`, `data/blr_terrain.json`, `data/gfs_multiday_43295.json`, `data/skill_scores.json`, and `alert_log.json` directly. None of that was broken or removed this phase.

## Current state (as of this phase)

`forecast.json` and `data/pan_india_grid.json` are **not yet derived from** `data/canonical_forecast.json` — they remain exactly what they were: `forecast.json` is written by `forecast_action.py` (the real VOBL XGBoost pipeline), `data/pan_india_grid.json` is written by `backend/pipeline.py` (the real pan-India physics-proxy engine). `canonical_forecast_writer.py` reads *from* both of these as its inputs and produces `data/canonical_forecast.json` as a new, additional, derived output.

This is the **opposite direction** from the target architecture described in Part 5 (`CANONICAL FORECAST → frontend compatibility artifacts`), and that is intentional for this phase: the instructions were explicit — *"do not redesign working code without a concrete reason"* and *"do not remove working frontend functionality unnecessarily."* Flipping the dependency direction (making `forecast.json`/`pan_india_grid.json` themselves become views derived from the canonical artifact) means changing `forecast_action.py` and `backend/pipeline.py`'s own write paths, and — more importantly — changing what the live frontend (`index.html`) reads, since it currently expects `forecast.json`'s and `pan_india_grid.json`'s existing shapes exactly. That is real surgery on the two production writers and the frontend's fetch/parse code, not something to do inside the same phase that just introduced the schema, without dedicated verification that nothing in `index.html`'s extensive parsing logic breaks.

## What this means concretely

- `forecast.json`: **source of truth for VOBL ML output**, unchanged, written by `forecast_action.py`.
- `data/pan_india_grid.json`: **source of truth for the pan-India physics-proxy engine**, unchanged, written by `backend/pipeline.py`.
- `data/canonical_forecast.json`: **new, additive, derived** — combines the two above into the schema documented in `docs/CANONICAL_FORECAST_SCHEMA.md`. Not yet consumed by the frontend (that migration is future work, tracked below).
- `data/pan_india_grid_slotrun.json`: pre-existing, unchanged, still written by `pan_india_gfs_fetcher.py`, still explicitly not merged into `pan_india_grid.json` (see `docs/PIPELINE_OWNERSHIP.md`).

## Recommended next step (not done this phase)

Once the canonical artifact has run in production for a period and its shape is confirmed stable against real data across multiple forecast cycles, the frontend can be migrated to fetch `data/canonical_forecast.json` as its single source, and `forecast_action.py`/`backend/pipeline.py` can be changed to call `canonical_forecast_writer.py` as their final step rather than a separate, disjoint invocation — at that point `forecast.json` and `pan_india_grid.json` would become genuinely derived compatibility views (e.g. `canonical_forecast_writer.py` could grow a `--emit-legacy-views` flag that writes them back out in their existing shapes from the canonical document, so nothing downstream has to change on the same day the source of truth flips). This is explicitly deferred, not forgotten.

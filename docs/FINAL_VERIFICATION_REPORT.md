# Final verification report

Run on branch `feat/drift-national-operational-readiness`, 2026-11-15, GFS cycle 2026-11-15T00Z.

| Check | Result |
|---|---|
| Python suite (`pytest tests`, 3 broken modules ignored) | **795 passed, 41 skipped, 9 failed** |
| The 9 failures | Pre-existing baseline: `test_indofloods_phase5` x3, `test_vobl_historical_gfs_ts_join` x6 (missing raw datasets in the checkout). Unrelated to this work. |
| Collection errors | 3 pre-existing (`test_phase21/22/27`: modules `phase22_terrain_panindia` etc. missing) |
| Node tests | drift_sim 11/11, location engine 1/1, m0 integrity 1/1, unified frontend 1/1 |
| Playwright e2e (system Chrome) | **15/15 pass** (desktop + mobile, simulation isolation, tour) |
| Location acceptance | 28 locations; 27/27 covered pass the independent eccodes nearest-point check; Kochi's coordinate NOT_COVERED |
| Bundle build + release gate | **READY** (14 required files, allow-list clean, fields verify independently, cycle age 15.1 h, models consistent, alert dispatch disabled) |
| Subscriber data in bundle | Not present (allow-list). Production still serves it until a deployment is approved. |

Not verified: GitHub Actions on the new branch (none run yet); production deployment (not authorised); any real alert delivery (disabled).
See `OPERATIONAL_LIMITATIONS.md` for what the system does not do.

# Operational limitations

1. **No operational lead time.** DRIFT does not record when a forecast becomes available to users, so `operational_lead_hours` is `null`. Only NWP forecast hours (+6/12/18/24 h thunderstorm, +9 h rain) are evaluated. No 2-6 h nowcast exists.
2. **Thunderstorm target is ordinary thunderstorm (METAR TS/VCTS)**, not "severe"; stations are airports, so rural skill is inferred, evaluated only by leave-region-out.
3. **Rain model covers one target** (>=20 mm/3 h at f09; pre-monsoon seasons Apr-Jun 2024/25 only). Higher thresholds failed the gate and are unpublished.
4. **Cloudburst and flash-flood predictions are unavailable** (no sub-hourly labels, no flood-event labels, no river/soil observations). The screening index is heuristic.
5. **Season coverage:** training is Apr-Jun; skill in monsoon/winter is unvalidated. Winter fields have CAPE near 0 by physics, not by error.
6. **Satellite, radar, lightning, IMDAA are not integrated** (see `docs/data_sources/SATELLITE_AND_REANALYSIS_ACCESS.md`).
7. **Shear is 850-500 hPa**, not 0-6 km.
8. **Kochi's gazetteer coordinate falls outside the land grid** (reported NOT_COVERED, not snapped).
9. **Alerts are disabled by design**; no delivery receipts, duplicate-suppression or audit store are claimed.
10. **Production exposure:** a subscribers JSON was found publicly served on the production site; the allow-list bundle builder in this branch excludes it, but this is not deployed (deployment is not authorised here). No data was downloaded.
11. **Known baseline test failures** unrelated to this work: `test_indofloods_phase5` x3, `test_vobl_historical_gfs_ts_join` x6, 3 collection errors (missing phase modules).
12. Static hosting means no server-side authentication on artifacts; `/v1` API CORS/auth must be set before any non-public deployment.

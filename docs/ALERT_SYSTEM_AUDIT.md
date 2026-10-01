# Alert System Audit — 2026-09-30

Per Part 9: trace both alert paths completely. Neither was deleted or merged this phase.

## Path 1: `alert_delivery.py` + `send_alerts.py` (WhatsApp, VOBL)

**Called by**: `forecast_update.yml`'s `"Send WhatsApp alerts"` step (`python send_alerts.py`), which runs 4x/day + 1 extra, right after `forecast_action.py` writes `forecast.json`.

**Trigger condition**: subscribers' individual probability thresholds against the current VOBL forecast (`forecast.json`), read live from a Cloudflare Worker subscriber list, with `data/subscribers.json` as a fallback if the Worker is unreachable.

**Truthfulness**: verified this phase by reading `alert_delivery.py` in full (already summarized in the Phase 1 audit) — four explicit states (`SUCCESS`, `FAILED`, `NOT_CONFIGURED`, `SKIPPED_NO_ALERT`), and `send_alerts.py` calls `send_whatsapp_tracked()` (not the old bare-bool `send_whatsapp()`, which the module's own docstring says it superseded) and persists the result via `persist_delivery_log()` to `data/alert_delivery_log.json`. **Cannot report false success**: an HTTP response is captured and classified, not assumed from "no exception raised."

**Can it silently fail?** No — every branch (missing config, HTTP failure, timeout, correct skip) is one of the four named states, and all four are written to the persisted log, not just printed.

## Path 2: `backend/dispatch_alerts.py` (Twilio SMS, pan-India)

**Called by**: `update_grid.yml`'s `"Dispatch alerts if hazard thresholds exceeded"` step (`python backend/dispatch_alerts.py`), which runs 4x/day right after `backend/pipeline.py` writes `data/pan_india_grid.json` — a **separate schedule** from `forecast_update.yml`.

**Trigger condition**: any of the 992 pan-India cells crossing a hazard threshold ("HIGH+"), independent of VOBL's own forecast.

**Truthfulness**: `send_sms()` returns `tuple[bool, str]` and the caller prints `"SMS sent"` vs `"SMS FAILED to {phone}: {err}"` distinctly — this is not the old bare-bool-collapsing bug `alert_delivery.py`'s docstring describes for the WhatsApp path. It also distinguishes "no recipients configured" from "no Twilio credentials" (two different warning messages, not conflated). **However**: it does not persist a structured delivery record anywhere (`data/alert_history.json` is written by `forecast_action.py`/`generate_alert_log.py` for the WhatsApp path's history; `dispatch_alerts.py` has no equivalent write) — its delivery outcome exists only in that run's CI log output, not as a queryable artifact. It also does not use the same four-state vocabulary as `alert_delivery.py` (no `NOT_CONFIGURED` vs `SKIPPED_NO_ALERT` distinction — both "no recipients" and "no credentials" print a similar `WARNING:` line and the function returns early without any state object at all).

**Can it silently fail?** Not silently in the sense of claiming success on failure — `send_sms()`'s `except` branch does return `(False, str(e))`, which the caller correctly reports as `FAILED`. But there is no persisted artifact a later run or a person could check without reading that specific CI run's raw logs.

## Can both fire for events near the same underlying weather?

Yes, structurally — they run on different schedules (`forecast_update.yml`'s slot-based crons vs `update_grid.yml`'s `30 4,10,16,22` cron) and trigger on different conditions (subscriber-threshold-vs-VOBL-forecast vs any-cell-vs-pan-India-grid), so a real severe event near VOBL could plausibly trigger a WhatsApp alert from Path 1 and, separately, an SMS from Path 2, since VOBL's own cell (`IND_13.0_78.0`) is scored by both the real XGBoost model (feeding Path 1) and the physics-proxy engine (feeding Path 2, since `backend/pipeline.py` doesn't special-case VOBL out of its 992-cell scoring — same finding as `docs/PAN_INDIA_HAZARD_COEFFICIENTS.md`). This is not "double alerting on the identical number" (the two paths compute different probabilities from different engines) but it is two independently-triggered notifications a subscriber to both could receive for the same storm. Not a truthfulness problem — a UX/deduplication question, out of scope for "no alert system may falsely claim successful delivery," which is the hard requirement this phase actually tests against.

## Verdict against this phase's requirement

**"No alert system may falsely claim successful delivery"**: true for both paths today, verified by reading both in full. **"Preserve the working truthful WhatsApp path"**: untouched this phase. **"If Twilio cannot be made equally truthful in this phase, isolate/document its limitations rather than pretending it is equivalent"**: done here — `dispatch_alerts.py`'s specific gap (no persisted structured delivery log, coarser state vocabulary) is named above rather than silently equated to `alert_delivery.py`'s four-state model. No code in either path was changed this phase.

/*
 * assets/forecast_integrity.js
 * M0 (fix/m0-forecast-integrity): pure helpers the dashboard uses so that
 *   - operational lead time is measured from ISSUE time, never from model init,
 *   - unavailable / withheld / historical values are shown as unavailable, never as numbers,
 *   - a legacy artifact (no temporal_status) can never be shown as a current forecast,
 *   - alert-log rows are classified (never presented as official or verified warnings).
 * Mirrors backend/forecast_integrity.py. Browser: window.DriftForecastIntegrity. Node: module.exports.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DriftForecastIntegrity = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var TARGET_LEAD_WINDOW_HOURS = [2, 6];
  var UNAVAILABLE = 'UNAVAILABLE';

  function parseUtc(v) {
    if (v == null || typeof v !== 'string' || !v.trim()) return null;
    var s = v.trim();
    // date-only and offset-less strings are read as UTC, like the backend
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) s += 'T00:00:00Z';
    else if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += 'Z';
    var t = Date.parse(s);
    return isNaN(t) ? null : t;
  }

  /** Hours from ISSUE to valid time. No model-init argument by design. null when either time is unknown. */
  function operationalLeadHours(validAt, issuedAt) {
    var v = parseUtc(validAt), i = parseUtc(issuedAt);
    if (v == null || i == null) return null;
    return Math.round(((v - i) / 36e5) * 1000) / 1000;
  }

  function meetsTargetLead(hours) {
    return hours != null && hours >= TARGET_LEAD_WINDOW_HOURS[0] && hours <= TARGET_LEAD_WINDOW_HOURS[1];
  }

  /** Never invents a lead: unknown -> 'UNAVAILABLE'; <= 0 -> not a forecast of the future. */
  function leadLabel(hours) {
    if (hours == null || typeof hours !== 'number' || isNaN(hours)) return UNAVAILABLE;
    if (hours <= 0) return 'NOT A FORECAST (valid time has passed)';
    return '+' + hours.toFixed(1) + 'h';
  }

  /** Prefer the backend's own operational_lead_hours; else compute from valid/issued; else null. */
  function resolveOperationalLead(meta) {
    if (!meta) return null;
    if (typeof meta.operational_lead_hours === 'number') return meta.operational_lead_hours;
    var computed = operationalLeadHours(meta.forecast_valid_at_utc || meta.validAt, meta.generated_at_utc);
    if (computed != null) return computed;
    return typeof meta.time_until_valid_hours === 'number' ? meta.time_until_valid_hours : null;
  }

  /** A hazard block may be shown as a current forecast only if the backend says so explicitly. */
  function isCurrentBlock(b) {
    return !!b && b.temporal_status === 'CURRENT' && b.presentable_as_current_forecast !== false &&
      b.probability != null;
  }

  /** Display state for one hazard block (artifact record or /forecast response). */
  function blockState(b) {
    if (!b) return { available: false, headline: UNAVAILABLE, reason: 'no data', designation: null };
    if (isCurrentBlock(b)) {
      return { available: true, value: b.probability, headline: Math.round(b.probability * 100) + '%',
        reason: null, designation: 'CURRENT' };
    }
    var ts = b.temporal_status || 'LEGACY_UNVERIFIED';
    var labels = {
      HISTORICAL_REPLAY: 'HISTORICAL REPLAY - not a forecast',
      NOT_LEAD_SPECIFIC: 'LEAD TIME NOT SUPPORTED',
      EXPIRED: 'EXPIRED', STALE: 'STALE', UNAVAILABLE: UNAVAILABLE,
      LEGACY_UNVERIFIED: 'UNAVAILABLE (no time provenance)'
    };
    return { available: false, value: null, headline: labels[ts] || UNAVAILABLE,
      reason: b.unavailable_reason || null, designation: ts };
  }

  /**
   * Client-side guard for an artifact that was NOT produced by the M0 builder (no schema_version >= 2 /
   * integrity.applied): every hazard block that lacks temporal_status === 'CURRENT' is shown unavailable,
   * so a legacy replay file can never be read as a current forecast. Returns a shallow-cloned artifact.
   */
  function guardUnifiedArtifact(art) {
    if (!art || !Array.isArray(art.records)) return art;
    // Never trust the artifact's own flags (schema_version / integrity.applied are self-declared): a block is only
    // allowed to carry a value if it is CURRENT AND carries the provenance a verified v3 block carries.
    function provenanced(b) {
      return b.temporal_status === 'CURRENT' && !!b.model_init_time_utc && !!b.valid_time_utc && !!b.generated_at_utc;
    }
    var clean = (art.schema_version || 1) >= 3 && art.records.every(function (r) {
      return ['TS', 'CB', 'FF'].every(function (hz) {
        var b = r[hz]; if (!b) return true;
        var hasValue = b.probability != null || (b.extra && b.extra.pu_ranking_score != null) || (b.xai && b.xai.top_contributions);
        return !hasValue || provenanced(b);
      });
    });
    if (clean) return art;
    var out = Object.assign({}, art, { client_guard: 'LEGACY_ARTIFACT_WITHHELD', lead_slots_available: false });
    out.records = art.records.map(function (r) {
      var rec = Object.assign({}, r);
      ['TS', 'CB', 'FF'].forEach(function (hz) {
        if (!r[hz]) return;
        var b = Object.assign({}, r[hz]);
        if (!provenanced(b)) {
          b.historical_reference = { designation: 'LEGACY_UNVERIFIED', probability: b.probability,
            pu_ranking_score: b.extra && b.extra.pu_ranking_score,
            model_init_time_utc: b.cb_init_time_utc || r.init_time || null,
            note: 'withheld by client guard: artifact has no time provenance' };
          b.probability = null; b.risk_category = 'NOT_AVAILABLE';
          if (b.extra) { b.extra = Object.assign({}, b.extra); delete b.extra.pu_ranking_score; }
          if (b.xai) b.xai = { status: 'WITHHELD' };
          b.temporal_status = 'LEGACY_UNVERIFIED'; b.presentable_as_current_forecast = false;
          // a withheld value must not keep claiming a live/trained provenance
          b.status = 'WITHHELD_NO_TIME_PROVENANCE'; b.source_status = 'LEGACY_UNVERIFIED';
        }
        rec[hz] = b;
      });
      return rec;
    });
    return out;
  }

  /** null/undefined predictor -> 'UNAVAILABLE'. Never 0, never a default. */
  function formatPredictor(v, digits, unit) {
    if (v == null || typeof v !== 'number' || isNaN(v)) return UNAVAILABLE;
    return v.toFixed(digits == null ? 1 : digits) + (unit ? ' ' + unit : '');
  }

  var PREDICTOR_KEYS = ['cape', 'cin', 'k_index', 'totals_totals', 'wind_shear_ms', 'pwat_mm', 'ctt_c',
    'convergence_s', 'ctt_drop_rate_c_hr', 'qpe_mm'];

  /** Predictors that are null in EVERY cell. Prefers the backend's list; else computed from cells. */
  function unavailablePredictors(doc, cells) {
    if (doc && Array.isArray(doc.unavailable_predictors)) return doc.unavailable_predictors.slice().sort();
    var list = Array.isArray(cells) ? cells : [];
    if (!list.length) return [];
    return PREDICTOR_KEYS.filter(function (k) {
      return list.every(function (c) { return c[k] == null; });
    }).sort();
  }

  /**
   * Alert-log row class. Distinguishes (a) a model alert that is still pending, (b) a historical model
   * alert with an outcome as recorded in the hand-maintained log. NEVER 'official' or 'verified warning'.
   */
  function classifyAlertEvent(ev) {
    var outcome = ev && ev.outcome ? String(ev.outcome).toUpperCase() : '';
    var base = { isOfficialWarning: false, isVerifiedWarning: false };
    if (!ev || outcome === 'PENDING' || ev.verified === false) {
      return Object.assign(base, { recordClass: 'MODEL_GENERATED_PENDING',
        label: 'MODEL ALERT - PENDING OBSERVATION' });
    }
    var human = { 'HIT': 'HIT (as recorded)', 'MISS': 'MISS (as recorded)', 'FALSE ALARM': 'FALSE ALARM (as recorded)',
      'CORRECT REJECTION': 'CORRECT REJECTION (as recorded)' };
    return Object.assign(base, { recordClass: 'HISTORICAL_MODEL_ALERT_WITH_RECORDED_OUTCOME',
      label: human[outcome] || (outcome ? outcome + ' (as recorded)' : 'OUTCOME NOT RECORDED') });
  }

  return {
    TARGET_LEAD_WINDOW_HOURS: TARGET_LEAD_WINDOW_HOURS,
    parseUtc: parseUtc, operationalLeadHours: operationalLeadHours, meetsTargetLead: meetsTargetLead,
    leadLabel: leadLabel, resolveOperationalLead: resolveOperationalLead, isCurrentBlock: isCurrentBlock,
    blockState: blockState, guardUnifiedArtifact: guardUnifiedArtifact, formatPredictor: formatPredictor,
    unavailablePredictors: unavailablePredictors, classifyAlertEvent: classifyAlertEvent
  };
});

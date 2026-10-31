/* DRIFT scenario simulation engine.
 * EVERYTHING produced here is SYNTHETIC. It exists to demonstrate warning, timeline and notification flows.
 * Nothing here is a forecast, nothing is sent anywhere, and no model or live data is involved.
 * Pure + deterministic (seeded) so it can be unit-tested in Node. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DriftSim = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var GRID = { latMin: 6, latMax: 37, lonMin: 68, lonMax: 98, step: 1 };
  var HOURS = 6;                         // simulated timeline T+0 .. T+6 h
  var TARGET_BAND = [2, 6];              // the 2-6 h lead window the product targets
  var LEVELS = ['NONE', 'WATCH', 'ALERT', 'WARNING'];
  var SIM_LABEL = 'SIMULATION - synthetic data, not a forecast';

  function hash(a, b, s) {
    var h = (Math.imul(a + 1, 374761393) + Math.imul(b + 1, 668265263) + Math.imul(s + 1, 2147483647)) | 0;
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
  }
  function cells() {
    var out = [];
    for (var la = GRID.latMin; la <= GRID.latMax; la += GRID.step)
      for (var lo = GRID.lonMin; lo <= GRID.lonMax; lo += GRID.step) out.push([la, lo]);
    return out;
  }
  var CELLS = cells();

  var HAZARDS = {
    thunderstorm: {
      label: 'Severe thunderstorm', unit: 'J/kg (simulated CAPE)', max: 3500, thresholds: [0.25, 0.45, 0.65],
      story: 'A synthetic convective cluster intensifies and drifts across the selected region.',
      defaults: { lat: 13.0, lon: 77.6, intensity: 0.85, radius: 1.6, speed: 0.35, bearing: 70, moisture: 0.5 }
    },
    extreme_rainfall: {
      label: 'Extreme rainfall / cloudburst-type', unit: 'mm per 3 h (simulated)', max: 140, thresholds: [0.25, 0.5, 0.8],
      story: 'A synthetic intense rain band stalls over steep terrain; short-duration accumulation grows quickly.',
      defaults: { lat: 30.4, lon: 78.4, intensity: 0.9, radius: 1.1, speed: 0.12, bearing: 40, moisture: 0.7 }
    },
    flash_flood: {
      label: 'Flash flood', unit: 'runoff index 0-1 (simulated)', max: 1, thresholds: [0.3, 0.55, 0.8],
      story: 'Synthetic heavy rain on saturated, steep catchments produces rapid runoff.',
      defaults: { lat: 25.6, lon: 91.8, intensity: 0.9, radius: 1.3, speed: 0.1, bearing: 200, moisture: 0.85 }
    }
  };

  var ACTIONS = {
    thunderstorm: [
      [],
      ['Watch: review the outlook for the next hours.', 'Secure loose outdoor items.'],
      ['Alert: postpone outdoor events and flights-line work.', 'Move to sturdy shelter if lightning is seen.', 'Avoid open ground and tall isolated objects.'],
      ['Warning: shelter indoors now.', 'Unplug sensitive electronics; stay away from windows.', 'Suspend outdoor operations until the cell passes.']
    ],
    extreme_rainfall: [
      [],
      ['Watch: monitor rainfall guidance and local drainage.', 'Review hill-road travel plans.'],
      ['Alert: avoid non-essential travel in hilly terrain.', 'Keep emergency kits ready; clear drains where safe.'],
      ['Warning: avoid slopes, stream beds and low crossings.', 'Prepare to move to higher ground.', 'Follow district authority instructions.']
    ],
    flash_flood: [
      [],
      ['Watch: know your nearest higher ground and evacuation route.', 'Monitor river and stream levels locally.'],
      ['Alert: do not enter or cross flowing water.', 'Move vehicles and valuables from low-lying areas.'],
      ['Warning: move to higher ground immediately.', 'Do not wait for water to rise.', 'Follow evacuation orders from authorities.']
    ]
  };

  function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }
  function createScenario(hazard, params) {
    var h = HAZARDS[hazard];
    if (!h) throw new Error('unknown hazard ' + hazard);
    var p = Object.assign({}, h.defaults, params || {});
    p.seed = p.seed == null ? 7 : p.seed;
    return { hazard: hazard, params: p, label: SIM_LABEL, simulated: true };
  }
  // Linear onset over 4 h (so an event visibly BUILDS through WATCH and ALERT before WARNING), then decay.
  function envelope(t) { return clamp(t <= 4 ? 0.1 + 0.9 * t / 4 : 1 - 0.25 * (t - 4), 0.05, 1); }
  function centre(p, t) {
    var b = p.bearing * Math.PI / 180;
    return [p.lat + p.speed * t * Math.cos(b), p.lon + p.speed * t * Math.sin(b)];
  }
  function terrain(la, lo) { return clamp(0.5 + 0.5 * Math.sin(la * 0.55) * Math.cos(lo * 0.42), 0, 1); }

  function base(sc, t, i) {
    var p = sc.params, c = centre(p, t), la = CELLS[i][0], lo = CELLS[i][1];
    var dy = la - c[0], dx = (lo - c[1]) * Math.cos(la * Math.PI / 180);
    var d2 = dx * dx + dy * dy;
    var noise = 1 + 0.16 * (hash(i, t, p.seed) - 0.5);
    return clamp(p.intensity * envelope(t) * Math.exp(-d2 / (2 * p.radius * p.radius)) * noise, 0, 1);
  }
  function value(sc, t, i) {
    var h = HAZARDS[sc.hazard], b = base(sc, t, i);
    if (sc.hazard === 'thunderstorm') return b * h.max;
    var rain3 = 0;
    for (var k = Math.max(0, t - 2); k <= t; k++) rain3 += base(sc, k, i) * 56;      // mm per hour -> 3 h accumulation
    if (sc.hazard === 'extreme_rainfall') return Math.min(rain3, h.max);
    var ter = 0.6 + 0.8 * terrain(CELLS[i][0], CELLS[i][1]);
    return clamp((rain3 / 140) * (0.4 + 0.6 * sc.params.moisture) * ter, 0, 1);
  }
  function levelOf(sc, v) {
    var h = HAZARDS[sc.hazard], x = v / h.max, th = h.thresholds;
    return x >= th[2] ? 3 : x >= th[1] ? 2 : x >= th[0] ? 1 : 0;
  }
  function frame(sc, t) {
    t = clamp(Math.round(t), 0, HOURS);
    var n = CELLS.length, values = new Float32Array(n), levels = new Uint8Array(n), counts = [0, 0, 0, 0], peak = 0;
    for (var i = 0; i < n; i++) {
      values[i] = value(sc, t, i); levels[i] = levelOf(sc, values[i]); counts[levels[i]]++;
      if (levels[i] > peak) peak = levels[i];
    }
    return { t: t, values: values, levels: levels, counts: counts, peakLevel: peak, centre: centre(sc.params, t),
             inTargetBand: t >= TARGET_BAND[0] && t <= TARGET_BAND[1], label: SIM_LABEL };
  }
  /* Simulated warnings: first hour at which each cell reaches ALERT or WARNING; simulated lead = that hour (issue = T+0). */
  function warnings(sc, max) {
    var first = {}, i, t, f;
    for (t = 0; t <= HOURS; t++) {
      f = frame(sc, t);
      for (i = 0; i < CELLS.length; i++)
        if (f.levels[i] >= 2 && (!first[i] || f.levels[i] > first[i].level))
          first[i] = { cell: i, lat: CELLS[i][0], lon: CELLS[i][1], level: f.levels[i], levelName: LEVELS[f.levels[i]],
                       validHour: t, simulatedLeadHours: t, value: f.values[i] };
    }
    var list = Object.keys(first).map(function (k) { return first[k]; })
      .sort(function (a, b) { return b.level - a.level || b.value - a.value || a.validHour - b.validHour; });
    return list.slice(0, max || 8).map(function (w) {
      w.hazard = sc.hazard; w.actions = ACTIONS[sc.hazard][w.level].slice();
      w.inTargetBand = w.validHour >= TARGET_BAND[0] && w.validHour <= TARGET_BAND[1];
      w.headline = 'SIMULATED ' + w.levelName + ' - ' + HAZARDS[sc.hazard].label + ' near ' + w.lat + 'N ' + w.lon + 'E';
      return w;
    });
  }
  /* Notification lifecycle, entirely simulated. No network call exists in this file. */
  function lifecycle(w, startMinute) {
    var m = startMinute || 0;
    var steps = [
      ['ELIGIBLE', 'Simulated warning met the SIMULATION alert rule', 0],
      ['RECORDED', 'Alert written to the simulated log', 0.2],
      ['DISPATCH_ATTEMPTED', 'Simulated dispatch to a fake recipient list (nobody contacted)', 0.5],
      ['PROVIDER_ACCEPTED', 'Simulated gateway accepted the message', 1.1],
      ['DELIVERY_CONFIRMED', 'Simulated delivery receipt (real DRIFT has no delivery receipts)', 2.4]
    ];
    return steps.map(function (s) {
      return { stage: s[0], note: s[1], atMinute: +(m + s[2]).toFixed(1), simulated: true, sent: false };
    });
  }
  /* End-to-end event narrative: per simulated hour, how the hazard evolves, which cells change level, and when a
   * WATCH / ALERT / WARNING is issued (with its simulated notification lifecycle). Purely synthetic. */
  var ISSUE_KIND = ['', 'WATCH', 'ALERT', 'WARNING'];
  function narrative(sc) {
    var out = [], maxSoFar = 0, prev = null, issuedLevel = 0, clock = 0;
    for (var t = 0; t <= HOURS; t++) {
      var f = frame(sc, t), changed = 0, newly = 0, esc = 0, i;
      for (i = 0; i < CELLS.length; i++) {
        var pl = prev ? prev.levels[i] : 0;
        if (f.levels[i] !== pl) changed++;
        if (f.levels[i] >= 1 && pl === 0) newly++;
        if (f.levels[i] > pl) esc++;
      }
      var stage = f.peakLevel === 0 ? (maxSoFar > 0 ? 'DECAYED' : 'BUILDING') :
                  f.peakLevel < maxSoFar ? 'WEAKENING' : f.peakLevel === 3 ? 'SEVERE' : f.peakLevel === 2 ? 'INTENSIFYING' : 'DEVELOPING';
      var issued = null;
      if (f.peakLevel > issuedLevel) {
        issuedLevel = f.peakLevel;
        var top = warnings(sc, 3).filter(function (w) { return w.validHour <= t; });
        issued = { kind: ISSUE_KIND[issuedLevel], level: issuedLevel, atHour: t,
                   headline: 'SIMULATED ' + ISSUE_KIND[issuedLevel] + ' issued - ' + HAZARDS[sc.hazard].label,
                   cells: f.counts[issuedLevel] + (issuedLevel < 3 ? f.counts[3] : 0), actions: ACTIONS[sc.hazard][issuedLevel].slice(),
                   examples: top, lifecycle: lifecycle({ level: issuedLevel }, clock) };
        clock += 4;
      }
      maxSoFar = Math.max(maxSoFar, f.peakLevel);
      out.push({ t: t, stage: stage, peakLevel: f.peakLevel, peakName: LEVELS[f.peakLevel], counts: f.counts.slice(),
                 cellsChanged: changed, cellsNewlyAffected: newly, cellsEscalated: esc, centre: f.centre,
                 inTargetBand: f.inTargetBand, issued: issued,
                 headline: 'T+' + t + ' h - ' + stage.toLowerCase() + ': ' + (f.counts[1] + f.counts[2] + f.counts[3]) +
                           ' cells at WATCH or above, ' + esc + ' escalated' });
      prev = f;
    }
    return out;
  }
  var PRESETS = { mild: { intensity: 0.45 }, severe: { intensity: 0.75 }, extreme: { intensity: 1.0, radius: 2.0 } };
  return { PRESETS: PRESETS, narrative: narrative, GRID: GRID, HOURS: HOURS, TARGET_BAND: TARGET_BAND, LEVELS: LEVELS, HAZARDS: HAZARDS, ACTIONS: ACTIONS,
           CELLS: CELLS, SIM_LABEL: SIM_LABEL, createScenario: createScenario, frame: frame, warnings: warnings,
           lifecycle: lifecycle, levelOf: levelOf };
});

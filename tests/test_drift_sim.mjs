// node --test tests/test_drift_sim.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
const S = createRequire(import.meta.url)('../assets/drift_sim.js');

test('992-cell grid matches the live pan-India grid', () => assert.equal(S.CELLS.length, 992));
test('three hazards, each with its own thresholds, unit and actions', () => {
  assert.deepEqual(Object.keys(S.HAZARDS).sort(), ['extreme_rainfall', 'flash_flood', 'thunderstorm']);
  const u = new Set(Object.values(S.HAZARDS).map(h => h.unit)); assert.equal(u.size, 3);
  for (const h of Object.keys(S.HAZARDS)) assert.equal(S.ACTIONS[h].length, 4);
});
test('simulation is deterministic and flagged synthetic', () => {
  const a = S.frame(S.createScenario('thunderstorm'), 3), b = S.frame(S.createScenario('thunderstorm'), 3);
  assert.deepEqual(Array.from(a.values), Array.from(b.values));
  assert.match(a.label, /SIMULATION/); assert.equal(S.createScenario('flash_flood').simulated, true);
});
test('storm moves and intensity responds to the slider', () => {
  const sc = S.createScenario('thunderstorm');
  const c0 = S.frame(sc, 0).centre, c6 = S.frame(sc, 6).centre;
  assert.notDeepEqual(c0, c6);
  const weak = S.frame(S.createScenario('thunderstorm', { intensity: 0.1 }), 3);
  const strong = S.frame(S.createScenario('thunderstorm', { intensity: 0.95 }), 3);
  assert.ok(strong.peakLevel > weak.peakLevel); assert.equal(weak.peakLevel, 0);
});
test('every hazard can produce a simulated WARNING with actions and a 2-6 h timeline', () => {
  for (const h of Object.keys(S.HAZARDS)) {
    const sc = S.createScenario(h), w = S.warnings(sc);
    assert.ok(w.length > 0, h); assert.equal(w[0].level, 3, h);
    assert.ok(w[0].actions.length >= 2); assert.match(w[0].headline, /^SIMULATED/);
    assert.ok(w.some(x => x.inTargetBand), h + ' has a warning inside the 2-6 h band');
  }
});
test('flash flood depends on antecedent moisture', () => {
  const dry = S.warnings(S.createScenario('flash_flood', { moisture: 0.0, intensity: 0.45 })).filter(x => x.level >= 3).length;
  const wet = S.warnings(S.createScenario('flash_flood', { moisture: 1.0, intensity: 0.45 })).filter(x => x.level >= 3).length;
  assert.ok(wet >= dry);
});
test('notification lifecycle is entirely simulated and nothing is sent', () => {
  const w = S.warnings(S.createScenario('extreme_rainfall'))[0], lc = S.lifecycle(w, 5);
  assert.deepEqual(lc.map(e => e.stage), ['ELIGIBLE', 'RECORDED', 'DISPATCH_ATTEMPTED', 'PROVIDER_ACCEPTED', 'DELIVERY_CONFIRMED']);
  assert.ok(lc.every(e => e.simulated === true && e.sent === false));
});
test('engine contains no network or storage calls', () => {
  const src = fs.readFileSync(new URL('../assets/drift_sim.js', import.meta.url), 'utf8');
  for (const bad of ['fetch(', 'XMLHttpRequest', 'sendBeacon', 'WebSocket', 'localStorage', 'twilio', 'ntfy']) assert.ok(!src.includes(bad), bad);
});

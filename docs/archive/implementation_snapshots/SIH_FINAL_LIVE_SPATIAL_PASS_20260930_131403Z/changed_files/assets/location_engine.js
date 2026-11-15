/**
 * location_engine.js — browser port of location_engine.py.
 *
 * Mirrors the Python reference implementation exactly (same VOBL domain
 * radius, same nearest-cell snap radius, same field names) so a value shown
 * in the UI can always be traced back to the same algorithm that is
 * separately unit-tested in test_location_engine.py. Do not change a
 * constant here without changing it there too.
 *
 * This module does NOT call any model and does NOT fetch anything itself —
 * it operates on already-loaded JSON (window's copies of
 * data/pan_india_grid.json, forecast.json, data/himawari_realtime.json),
 * matching this turn's "do not run inference in the browser / use the
 * existing static grid intelligently" requirement. Loading those JSON
 * files remains the existing fetch calls already in index.html; this file
 * only adds the coordinate -> multi-architecture bundle lookup on top.
 */
(function (global) {
  'use strict';

  const VOBL_LAT = 13.1979;
  const VOBL_LON = 77.7063;
  const VOBL_DOMAIN_RADIUS_KM = 25.0;
  const MAX_CELL_SNAP_DEG = 1.5;
  const HIMAWARI_RADIUS_KM = 50.0;
  const EARTH_RADIUS_KM = 6371.0;

  function haversineKm(lat1, lon1, lat2, lon2) {
    const toRad = (d) => (d * Math.PI) / 180;
    const p1 = toRad(lat1), p2 = toRad(lat2);
    const dphi = toRad(lat2 - lat1);
    const dlambda = toRad(lon2 - lon1);
    const a = Math.sin(dphi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dlambda / 2) ** 2;
    return 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(a));
  }

  function findNearestCell(grid, lat, lon) {
    // backend/pipeline.py writes the key "grid_cells" -- verified against
    // the actual data/pan_india_grid.json on disk, not assumed.
    const cells = (grid && (grid.grid_cells || grid.cells)) || [];
    if (!cells.length) {
      return { available: false, reason: 'pan-India grid has no cells loaded' };
    }
    let best = null, bestD = null;
    for (const c of cells) {
      const d = Math.hypot(c.lat - lat, c.lon - lon);
      if (bestD === null || d < bestD) { bestD = d; best = c; }
    }
    if (best === null || bestD > MAX_CELL_SNAP_DEG) {
      return {
        available: false, distance_deg: bestD, grid_step_deg: grid.grid_step_deg,
        reason: `nearest pan-India cell is ${bestD ? bestD.toFixed(2) : '?'} deg away -- outside grid coverage`,
      };
    }
    return {
      available: true, cell: best,
      distance_deg: Math.round(bestD * 10000) / 10000,
      distance_km: Math.round(haversineKm(lat, lon, best.lat, best.lon) * 10) / 10,
      grid_step_deg: grid.grid_step_deg,
      reason: 'nearest-neighbor match within pan-India grid coverage',
    };
  }

  function voblApplicability(lat, lon) {
    const d = haversineKm(lat, lon, VOBL_LAT, VOBL_LON);
    const inDomain = d <= VOBL_DOMAIN_RADIUS_KM;
    const reason = inDomain
      ? `${d.toFixed(1)} km from IMD VOBL 43295 -- within the ${VOBL_DOMAIN_RADIUS_KM.toFixed(0)} km station representativeness radius`
      : `${d.toFixed(1)} km from IMD VOBL 43295 -- outside the ${VOBL_DOMAIN_RADIUS_KM.toFixed(0)} km station representativeness radius; VOBL XGBoost is station-specific and not applicable to this location`;
    return { in_domain: inDomain, distance_km: Math.round(d * 10) / 10, radius_km: VOBL_DOMAIN_RADIUS_KM, reason };
  }

  /**
   * buildLocationBundle(lat, lon, panIndiaGrid, forecast, himawari)
   * Pure function -- same contract as Python's build_location_bundle().
   * Every field is either a real value traced to its source, or an
   * explicit {available: false, reason: "..."} -- never fabricated.
   */
  function buildLocationBundle(lat, lon, panIndiaGrid, forecast, himawari) {
    let panIndiaOut = { available: false, reason: 'pan-India grid not loaded' };
    if (panIndiaGrid) {
      const match = findNearestCell(panIndiaGrid, lat, lon);
      if (match.available) {
        const c = match.cell;
        panIndiaOut = {
          available: true,
          source: 'Pan-India Hazard Engine (backend/pipeline.py::hazard_probabilities)',
          ts_probability: c.thunderstorm_probability,
          cb_probability: c.cloudburst_probability,
          ff_probability: c.flash_flood_probability,
          ff_probability_terrain_adjusted: c.flash_flood_probability_terrain_adjusted,
          terrain: c.terrain,
          // Falls back to the older on-disk field names (pwat/apcp_mm) when
          // the newer ones aren't present yet (file predates this turn's
          // terrain/CTT pipeline fields) -- absent fields stay undefined,
          // never guessed.
          atmospheric: {
            cape: c.cape, cin: c.cin, k_index: c.k_index, totals_totals: c.totals_totals,
            wind_shear_ms: c.wind_shear_ms,
            pwat_mm: (c.pwat_mm !== undefined ? c.pwat_mm : c.pwat),
            ctt_c: c.ctt_c, convergence_s: c.convergence_s, ctt_drop_rate_c_hr: c.ctt_drop_rate_c_hr,
            qpe_mm: (c.qpe_mm !== undefined ? c.qpe_mm : c.apcp_mm),
          },
          cell: { lat: c.lat, lon: c.lon },
          distance_km: match.distance_km,
          grid_step_deg: match.grid_step_deg,
          generated_at_utc: panIndiaGrid.generated_at_utc,
          gfs_cycle: panIndiaGrid.gfs_cycle,
          gfs_fhour: panIndiaGrid.gfs_fhour,
        };
      } else {
        panIndiaOut = { available: false, reason: match.reason };
      }
    }

    const voblApp = voblApplicability(lat, lon);
    const voblOut = {
      in_domain: voblApp.in_domain, distance_km: voblApp.distance_km,
      radius_km: voblApp.radius_km, reason: voblApp.reason,
      source: 'IMD VOBL Station 43295 / VOBL XGBoost (forecast_action.py)',
      station_forecast: null,
    };
    if (forecast && forecast.slots) {
      voblOut.station_forecast = {
        slots: forecast.slots, peak_probability: forecast.peak_probability,
        peak_slot: forecast.peak_slot, generated_at_ist: forecast.generated_at_ist,
        gfs_cycle: forecast.gfs_cycle,
      };
    }

    let himawariOut = { available: false, reason: 'no Himawari data loaded' };
    if (himawari) {
      const dKm = haversineKm(lat, lon, VOBL_LAT, VOBL_LON);
      if (dKm <= HIMAWARI_RADIUS_KM) {
        himawariOut = {
          available: true, source: 'HIMAWARI-9 (fetch_himawari_realtime.py) -- NOT INSAT',
          vobl_bt_celsius: himawari.vobl_bt_celsius, min_bt_50km: himawari.min_bt_50km,
          bt_trend_1h: himawari.bt_trend_1h, storm_detected: himawari.storm_detected,
          timestamp: himawari.fetched_at_utc || himawari.timestamp,
          distance_km_from_vobl: Math.round(dKm * 10) / 10,
        };
      } else {
        himawariOut = {
          available: false, distance_km_from_vobl: Math.round(dKm * 10) / 10,
          reason: `${dKm.toFixed(0)} km from VOBL -- outside Himawari's ~50 km real-time crop radius`,
        };
      }
    }

    let gfsOut = { available: false, reason: 'no pan-India cell matched' };
    if (panIndiaOut.available) {
      gfsOut = { available: true, source: 'GFS (NOAA NOMADS, backend/pipeline.py)', ...panIndiaOut.atmospheric };
    }

    let leadTime = null, generatedAt = null;
    if (forecast && forecast.slots) {
      generatedAt = forecast.generated_at_ist;
      const primary = forecast.slots.find((s) => s.primary && s.lead_time);
      if (primary) leadTime = primary.lead_time;
    }

    return {
      latitude: lat, longitude: lon,
      pan_india: panIndiaOut, vobl: voblOut, himawari: himawariOut, gfs: gfsOut,
      lead_time: leadTime, generated_at: generatedAt,
    };
  }

  global.DriftLocationEngine = {
    VOBL_LAT, VOBL_LON, VOBL_DOMAIN_RADIUS_KM, MAX_CELL_SNAP_DEG,
    haversineKm, findNearestCell, voblApplicability, buildLocationBundle,
  };
})(window);

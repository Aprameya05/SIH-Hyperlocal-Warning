/* Command-centre interactions that wrap the map and the page chrome: notices popover, map controls (zoom, my location, layer, fullscreen),
 * the selected-cell pulse, and the mobile bottom sheet for the inspector. Everything here reads the live application state
 * (window.__drift); nothing is decorative: each control does one real thing and reports when it cannot. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  function D() { return window.__drift; }
  function map() { return D() && D().getMap && D().getMap(); }
  function toast(msg) {
    var w = $('mapwrap'); if (!w) return; var t = w.querySelector('.toast');
    if (!t) { t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status'); w.appendChild(t); }
    t.textContent = msg; t.classList.add('on'); clearTimeout(t._h); t._h = setTimeout(function () { t.classList.remove('on'); }, 3200);
  }

  /* ---------------- notices (real artifact status, recomputed from timestamps) ---------------- */
  function notices() {
    var d = D(), S = d && d.state && d.state.store, out = [], now = Date.now();
    if (!S) return [{ k: 'bad', t: 'The national grid could not be loaded.' }];
    if (!S.fieldsOk) out.push({ k: 'bad', t: 'Atmospheric fields failed verification: ' + ((S.problems && S.problems[0]) || 'unknown') + '. No model values are shown.' });
    else {
      var age = (now - Date.parse(S.fields.meta.cycle_init_utc)) / 36e5, cyc = S.fields.meta.cycle_init_utc.slice(0, 13).replace('T', ' ') + 'Z';
      out.push(age <= 18 ? { k: 'ok', t: 'GFS cycle ' + cyc + ' is current (' + age.toFixed(1) + ' h old).' } : { k: 'bad', t: 'GFS cycle ' + cyc + ' is ' + age.toFixed(1) + ' h old (limit 18 h): values are withheld as current.' });
    }
    if (S.satOk) {
      var m = Math.round((now - Date.parse(S.sat.meta.scene_nominal_utc)) / 6e4);
      out.push(m <= 90 ? { k: 'ok', t: 'Himawari-9 scan is ' + m + ' min old.' } : { k: 'warn', t: 'Himawari-9 scan is ' + (m >= 120 ? (m / 60).toFixed(1) + ' h' : m + ' min') + ' old (limit 90 min): satellite values are withheld.' });
    } else out.push({ k: 'warn', t: 'No usable Himawari-9 scan is available.' });
    if (!S.tsOk) out.push({ k: 'warn', t: 'The pan-India thunderstorm model is not published for this season (April to June only). Indicators are still shown.' });
    out.push({ k: 'warn', t: 'Operational alerting is off: DRIFT publishes no warnings. Advisory guidance only.' });
    return out;
  }
  function renderNotices() {
    var n = notices(), el = $('notif'); if (!el) return;
    el.innerHTML = '<h2>Data notices</h2>' + n.map(function (x) { return '<div class="it ' + x.k + '"><i></i><span>' + x.t.replace(/</g, '&lt;') + '</span></div>'; }).join('');
    var dot = $('belldot');
    if (dot) {
      var worst = n.some(function (x) { return x.k === 'bad'; }) ? 'bad' : n.some(function (x) { return x.k === 'warn'; }) ? 'warn' : 'ok';
      dot.className = 'dot' + (worst === 'ok' ? ' ok' : ''); dot.style.background = worst === 'warn' ? 'var(--warn)' : ''; dot.style.boxShadow = worst === 'warn' ? '0 0 8px var(--warn)' : '';
    }
  }
  var bell = $('bell'), pop = $('notif');
  if (bell && pop) {
    bell.addEventListener('click', function (e) { e.stopPropagation(); var on = !pop.classList.contains('on'); if (on) renderNotices(); pop.classList.toggle('on', on); bell.setAttribute('aria-expanded', on); });
    document.addEventListener('click', function (e) { if (pop.classList.contains('on') && !pop.contains(e.target) && e.target !== bell) { pop.classList.remove('on'); bell.setAttribute('aria-expanded', 'false'); } });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && pop.classList.contains('on')) { pop.classList.remove('on'); bell.setAttribute('aria-expanded', 'false'); bell.focus(); } });
    setInterval(renderNotices, 60000); window.addEventListener('drift-store-ready', renderNotices); setTimeout(renderNotices, 2500);
  }

  /* ---------------- map controls ---------------- */
  var z1 = $('zin'), z2 = $('zout'), loc = $('locate'), lyr = $('layerbtn'), fb = $('fullbtn'), wrap = $('mapwrap');
  if (z1) z1.addEventListener('click', function () { var m = map(); if (m) m.zoomIn(); });
  if (z2) z2.addEventListener('click', function () { var m = map(); if (m) m.zoomOut(); });
  if (loc) loc.addEventListener('click', function () {
    var d = D(); if (!d || !d.state.store) { toast('The grid is still loading.'); return; }
    if (!navigator.geolocation) { toast('This browser cannot provide your location.'); return; }
    loc.setAttribute('aria-busy', 'true'); toast('Finding your location...');
    navigator.geolocation.getCurrentPosition(function (p) {
      loc.removeAttribute('aria-busy'); var r = d.state.store.resolve(p.coords.latitude, p.coords.longitude);
      if (r.covered) { toast('Showing the cell at your location.'); d.selectCell(r.cell_id); } else toast('Your location is outside the DRIFT land grid (' + r.reason + ').');
    }, function (e) { loc.removeAttribute('aria-busy'); toast(e && e.code === 1 ? 'Location permission was denied.' : 'Your location could not be determined.'); }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
  });
  if (lyr) lyr.addEventListener('click', function () {
    var s = $('lyr'); if (!s) { toast('Layers are available in Live mode.'); return; }
    if (s.scrollIntoView) s.scrollIntoView({ block: 'center', behavior: 'smooth' });
    s.focus(); try { if (s.showPicker) s.showPicker(); } catch (e) { /* needs a gesture; focus is enough */ }
  });
  function setFull(on) {
    if (!wrap) return; wrap.classList.toggle('fs', on);
    if (fb) { fb.setAttribute('aria-pressed', on); fb.setAttribute('aria-label', on ? 'Exit fullscreen' : 'Fullscreen map'); }
    setTimeout(function () { var m = map(); if (m) m.resize(); }, 60);
  }
  if (fb) fb.addEventListener('click', function () { setFull(!wrap.classList.contains('fs')); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && wrap && wrap.classList.contains('fs')) setFull(false); });

  /* ---------------- selected-cell pulse (a real map marker on the selected cell) ---------------- */
  var marker = null;
  window.addEventListener('drift-sel', function (e) {
    var d = D(), m = map(), cid = e.detail && e.detail.cell_id; if (!d || !m || !window.maplibregl) return;
    if (!cid) { if (marker) { marker.remove(); marker = null; } return; }
    var S = d.state.store, c = S && S.cells[S.idx[cid]]; if (!c) return;
    if (!marker) { var el = document.createElement('div'); el.className = 'sel-pulse'; marker = new window.maplibregl.Marker({ element: el }); }
    marker.setLngLat([c[2], c[1]]).addTo(m);
    if (window.matchMedia('(max-width:760px)').matches) sheet('half');
  });

  /* ---------------- mobile bottom sheet ---------------- */
  var right = document.querySelector('section.right'), grab = $('sheetgrab');
  function sheet(state) { if (!right) return; right.classList.remove('half', 'full'); if (state === 'half' || state === 'full') right.classList.add(state); right.dataset.sheet = state; }
  if (right && grab) {
    var y0 = null, s0 = 'peek', order = ['peek', 'half', 'full'];
    grab.addEventListener('pointerdown', function (e) { y0 = e.clientY; s0 = right.dataset.sheet || 'peek'; try { grab.setPointerCapture(e.pointerId); } catch (x) { /* ignore */ } });
    grab.addEventListener('pointerup', function (e) {
      if (y0 == null) return; var dy = e.clientY - y0, i = order.indexOf(s0); y0 = null;
      if (Math.abs(dy) < 8) i = (i + 1) % 3; else if (dy < -30) i = Math.min(2, i + 1); else if (dy > 30) i = Math.max(0, i - 1);
      sheet(order[i]);
    });
    grab.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); var i = order.indexOf(right.dataset.sheet || 'peek'); sheet(order[(i + 1) % 3]); } });
    sheet('peek');
  }
  /* ---------------- left-rail card headings: icon + collapse chevron (collapse state survives re-renders) ---------------- */
  var SVG = function (d) { return '<svg class="hico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>'; };
  var HICON = { hazard: SVG('<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/>'), layers: SVG('<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/>'), status: SVG('<path d="M3 12h4l3-8 4 16 3-8h4"/>'), scen: SVG('<path d="M4 6h16M4 12h16M4 18h16"/>'), clock: SVG('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>') };
  var collapsed = {};
  function decorate() {
    Array.prototype.forEach.call(document.querySelectorAll('aside .card > h2, #timeline h2'), function (h) {
      if (h.classList.contains('cardh')) return;
      var t = (h.textContent || '').toLowerCase(), k = /hazard/.test(t) ? 'hazard' : /layer/.test(t) ? 'layers' : /status/.test(t) ? 'status' : 'scen';
      h.classList.add('cardh'); h.setAttribute('role', 'button'); h.tabIndex = 0;
      h.insertAdjacentHTML('afterbegin', HICON[k]); h.insertAdjacentHTML('beforeend', '<svg class="chv" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>');
      var card = h.parentNode, key = k + ':' + (card.id || ''); if (collapsed[key]) card.classList.add('collapsed'); h.setAttribute('aria-expanded', !collapsed[key]);
      var tog = function () { var c = card.classList.toggle('collapsed'); collapsed[key] = c; h.setAttribute('aria-expanded', !c); };
      h.addEventListener('click', tog); h.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); tog(); } });
    });
  }
  var aside = document.querySelector('aside');
  if (aside && window.MutationObserver) { new MutationObserver(function () { decorate(); }).observe(aside, { childList: true, subtree: true }); }
  decorate();
  window.__driftUx = { toast: toast, notices: notices, sheet: sheet, setFull: setFull };
})();

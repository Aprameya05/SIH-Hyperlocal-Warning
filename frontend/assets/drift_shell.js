/* Section navigation for the DRIFT command centre.
 * The command centre keeps all of its own panels (Location, Hazards, Compare, Alerts & actions, Sources, Evaluation).
 * The sections of the classic dashboard that it did not have (Forecast, Satellite, Models, Explain, Regimes, Scores, Graphs)
 * are added here as tabs. They are the classic dashboard's own pages, shown full-screen in one lazily-loaded frame, so every
 * value on them is the same real-data view (no second implementation to drift apart). The selected grid cell follows you
 * between the command centre and these sections; the URL carries ?tab= so back/forward and direct links work.
 */
(function () {
  'use strict';
  var TABS = [
    ['main', 'Command centre', null],
    ['forecast', 'Forecast', '#forecast'], ['satellite', 'Satellite', '#satellite'], ['models', 'Models', '#models'],
    ['explain', 'Explain', '#explainability'], ['regimes', 'Regimes', '#regimes'], ['scores', 'Scores', '#scores'], ['graphs', 'Graphs', '#graphs']
  ];
  var nav = document.getElementById('nav'), viewMain = document.getElementById('view-main'), viewFrame = document.getElementById('view-frame'),
      frame = document.getElementById('legacy-frame'), loading = document.getElementById('frame-load');
  if (!nav || !viewMain || !viewFrame || !frame) return;
  var current = 'main', frameReady = false, frameStarted = false;

  var SV = function (d) { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>'; };
  var NAVICON = {
    main: SV('<path d="M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10"/>'), forecast: SV('<circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"/>'),
    satellite: SV('<path d="M4 20l5-5M9 15l-2-2 3-3 2 2zM13 11l3-3 2 2-3 3zM15 5l4 4M18 2l4 4"/>'), models: SV('<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5M3 17.5l9 5 9-5"/>'),
    explain: SV('<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>'), regimes: SV('<circle cx="12" cy="6" r="2.5"/><circle cx="5" cy="18" r="2.5"/><circle cx="19" cy="18" r="2.5"/><path d="M10.5 8l-4 8M13.5 8l4 8M7.5 18h9"/>'),
    scores: SV('<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>'), graphs: SV('<path d="M3 17l6-6 4 4 8-9"/><path d="M15 6h6v6"/>')
  };
  var ind = document.createElement('span'); ind.className = 'ind'; nav.appendChild(ind);
  function placeInd() { var on = nav.querySelector('button[aria-selected=true]'); if (!on) return; ind.style.left = on.offsetLeft + 'px'; ind.style.width = on.offsetWidth + 'px'; try { on.scrollIntoView({ block: 'nearest', inline: 'center' }); } catch (e) { /* ignore */ } }
  window.addEventListener('resize', placeInd);
  TABS.forEach(function (t) {
    var b = document.createElement('button');
    b.type = 'button'; b.setAttribute('role', 'tab'); b.dataset.tab = t[0]; b.id = 'tab-' + t[0]; b.innerHTML = NAVICON[t[0]] + '<span>' + t[1] + '</span>';
    b.setAttribute('aria-selected', t[0] === 'main' ? 'true' : 'false'); b.tabIndex = t[0] === 'main' ? 0 : -1;
    b.addEventListener('click', function () { go(t[0], true); });
    nav.appendChild(b);
  });
  var sep = document.createElement('span'); sep.className = 'sep'; nav.appendChild(sep);
  var note = document.createElement('span'); note.className = 'note'; note.textContent = 'Research guidance, not an official warning'; nav.appendChild(note);
  nav.addEventListener('keydown', function (e) {
    var ids = TABS.map(function (t) { return t[0]; }), i = ids.indexOf(current), j = null;
    if (e.key === 'ArrowRight') j = (i + 1) % ids.length; else if (e.key === 'ArrowLeft') j = (i + ids.length - 1) % ids.length;
    else if (e.key === 'Home') j = 0; else if (e.key === 'End') j = ids.length - 1;
    if (j == null) return; e.preventDefault(); go(ids[j], true); document.getElementById('tab-' + ids[j]).focus();
  });

  function tabFromUrl() { var m = /[?&]tab=([a-z]+)/.exec(location.search); var id = m && m[1]; return TABS.some(function (t) { return t[0] === id; }) ? id : 'main'; }
  function urlFor(id) { var u = new URL(location.href); if (id === 'main') u.searchParams.delete('tab'); else u.searchParams.set('tab', id); return u.pathname + u.search + u.hash; }
  function selectedCell() { try { return window.__drift && window.__drift.state && window.__drift.state.sel || null; } catch (e) { return null; } }
  function childWin() { try { return frame.contentWindow; } catch (e) { return null; } }

  function pushCellToFrame() {
    var w = childWin(), cid = selectedCell(); if (!w || !cid) return;
    var tries = 0; (function wait() {
      var i = w.__driftInspector, s = w.__driftStore;
      if (i && s && s.fieldsOk) { try { if (i.state().cell_id !== cid) i.select(cid); } catch (e) { /* the frame may be reloading */ } return; }
      if (++tries < 150) setTimeout(wait, 200);
    })();
  }
  function pullCellFromFrame() {
    var w = childWin(); if (!w || !w.__driftInspector) return;
    try { var cid = w.__driftInspector.state().cell_id; if (cid && cid !== selectedCell() && window.__drift && window.__drift.selectCell) window.__drift.selectCell(cid, { fly: false }); } catch (e) { /* ignore */ }
  }

  function show(id) {
    var wasFrame = current !== 'main'; if (wasFrame && id === 'main') pullCellFromFrame();
    current = id;
    Array.prototype.forEach.call(nav.querySelectorAll('button'), function (b) { var on = b.dataset.tab === id; b.setAttribute('aria-selected', on ? 'true' : 'false'); b.tabIndex = on ? 0 : -1; });
    placeInd();
    if (id === 'main') {
      viewFrame.classList.remove('on'); viewMain.classList.remove('off');
      var m = window.__drift && window.__drift.getMap && window.__drift.getMap(); if (m && m.resize) setTimeout(function () { m.resize(); }, 50);
      document.title = 'DRIFT Command Centre'; return;
    }
    var def = TABS.filter(function (t) { return t[0] === id; })[0];
    viewMain.classList.add('off'); viewFrame.classList.add('on'); document.title = 'DRIFT · ' + def[1];
    if (!frameStarted) {
      frameStarted = true; loading.style.display = 'flex';
      frame.addEventListener('load', function () { frameReady = true; loading.style.display = 'none'; hideWhenMounted(); pushCellToFrame(); });
      frame.contentWindow.location.replace('legacy.html?embed=1' + def[2]);   // replace(): the frame adds no entries to the Back/Forward history
    } else {
      var w = childWin(); if (w && w.location.hash !== def[2]) w.location.replace('legacy.html?embed=1' + def[2]);   // (same document: hash only, no history entry)
      pushCellToFrame();
    }
  }
  function hideWhenMounted() {   // the frame's page compiles in the browser: keep the loading note until its own loading state is gone
    loading.style.display = 'flex';
    var t = 0; (function chk() {
      // also wait for the page's own data store, so a section never shows a half-loaded 'unavailable' state
      try { var w = childWin(); if (w && w.document && w.document.getElementById('root') && !w.document.getElementById('boot-loading') && w.document.body.innerText.length > 300 && (w.__driftStore || w.__driftStoreProblems !== undefined)) { setTimeout(function () { loading.style.display = 'none'; }, 700); return; } } catch (e) { loading.style.display = 'none'; return; }
      if (++t < 600) setTimeout(chk, 150); else loading.style.display = 'none';
    })();
  }

  function go(id, push) {
    if (id === current && !push) return;
    if (id === current) return;
    show(id); if (push) history.pushState({ tab: id }, '', urlFor(id));
  }
  window.addEventListener('popstate', function () { var id = tabFromUrl(); if (id !== current) show(id); });
  // In-frame navigation to another section (e.g. a link inside a page) keeps the tab bar in step.
  window.addEventListener('message', function (e) { if (e.origin !== location.origin || !e.data || e.data.type !== 'drift-embed-hash') return; var t = TABS.filter(function (x) { return x[2] === e.data.hash; })[0]; if (t && t[0] !== current) { current = t[0]; show(t[0]); history.replaceState({ tab: t[0] }, '', urlFor(t[0])); } });

  var first = tabFromUrl(); if (first !== 'main') show(first);
  placeInd(); if (document.fonts && document.fonts.ready) document.fonts.ready.then(placeInd); setTimeout(placeInd, 300);
  window.__driftShell = { go: function (id) { go(id, true); }, current: function () { return current; }, ready: function () { return frameReady; }, tabs: TABS.map(function (t) { return t[0]; }) };
})();

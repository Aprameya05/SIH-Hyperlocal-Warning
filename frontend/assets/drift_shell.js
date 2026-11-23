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

  TABS.forEach(function (t) {
    var b = document.createElement('button');
    b.type = 'button'; b.setAttribute('role', 'tab'); b.dataset.tab = t[0]; b.id = 'tab-' + t[0]; b.textContent = t[1];
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
      try { var w = childWin(); if (w && w.document && w.document.getElementById('root') && !w.document.getElementById('boot-loading') && w.document.body.innerText.length > 300) { loading.style.display = 'none'; return; } } catch (e) { loading.style.display = 'none'; return; }
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
  window.__driftShell = { go: function (id) { go(id, true); }, current: function () { return current; }, ready: function () { return frameReady; }, tabs: TABS.map(function (t) { return t[0]; }) };
})();

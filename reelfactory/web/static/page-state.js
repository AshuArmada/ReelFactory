/* Recover position after an action returns to the same workspace page.
   Only navigation state is stored; form values and API keys are never saved. */
(function () {
  'use strict';
  var storageKey = 'reelfactory:pending-position';
  var pageKey = document.body.dataset.pageKey;
  var pending = null;
  try {
    pending = JSON.parse(sessionStorage.getItem(storageKey));
    sessionStorage.removeItem(storageKey);
  } catch (_) { /* Storage can be disabled without affecting form actions. */ }

  function roots() { return Array.from(document.querySelectorAll('form.wizard, .tabbed')); }
  function rootKey(root, index) { return root.id || 'panel-' + index; }
  function detailsKey(details) {
    var summary = details.querySelector('summary');
    if (details.id) return details.id;
    if (!summary) return '';
    var label = summary.cloneNode(true);
    label.querySelectorAll('.summary-note, .chip').forEach(function (note) { note.remove(); });
    return label.textContent.replace(/\s+/g, ' ').trim();
  }
  function locate(reference) {
    if (!reference) return null;
    if (reference.id) return document.getElementById(reference.id);
    if (reference.name) return document.getElementsByName(reference.name)[reference.index] || null;
    if (reference.job) return Array.from(document.querySelectorAll('[data-job]')).find(function (card) {
      return card.dataset.job === reference.job;
    });
    return null;
  }
  function anchor() {
    var candidates = Array.from(document.querySelectorAll('main [id], main [name], main [data-job]'));
    var best = null;
    candidates.forEach(function (element) {
      if (!element.getClientRects().length || element.type === 'hidden') return;
      var box = element.getBoundingClientRect();
      if (box.top < 0 || box.top > innerHeight || box.height > innerHeight) return;
      if (best && box.top >= best.top) return;
      var reference = element.id ? {id: element.id}
        : element.name ? {name: element.name, index: Array.from(document.getElementsByName(element.name)).indexOf(element)}
        : {job: element.dataset.job};
      best = Object.assign(reference, {top: box.top});
    });
    return best;
  }
  function snapshot() {
    return {
      page: pageKey, at: Date.now(), x: scrollX, y: scrollY, anchor: anchor(),
      panels: roots().map(function (root, index) {
        var current = root.querySelector(root.matches('form.wizard') ? '[name="_ui_step"]' : '[name="_ui_tab"]');
        return {key: rootKey(root, index), index: current ? Number(current.value) : 0};
      }),
      details: Array.from(document.querySelectorAll('details')).map(function (details) {
        return {key: detailsKey(details), open: details.open};
      })
    };
  }
  var submitted = false;
  function remember(state) {
    try { sessionStorage.setItem(storageKey, JSON.stringify(state)); } catch (_) {}
  }
  // A refresh of the current page keeps its section too. Different pages use
  // a different key, so ordinary navigation still opens its intended view.
  window.addEventListener('pagehide', function () {
    if (!submitted) remember(snapshot());
  });
  window.addEventListener('pageshow', function () { submitted = false; });

  // Capture before submit handlers change labels, disable fields or show progress.
  // Wait until all handlers finish so validation and fetch-only forms are skipped.
  document.addEventListener('submit', function (event) {
    var form = event.target;
    var target = (event.submitter && event.submitter.formTarget) || form.target;
    if (form.method.toLowerCase() !== 'post' || (target && target !== '_self')) return;
    var state = snapshot();
    queueMicrotask(function () {
      if (event.defaultPrevented) return;
      submitted = true;
      remember(state);
    });
  }, true);

  if (!pending || pending.page !== pageKey || Date.now() - pending.at > 2 * 60 * 60 * 1000) return;
  roots().forEach(function (root, index) {
    var saved = pending.panels.find(function (panel) { return panel.key === rootKey(root, index); });
    if (saved) root.setAttribute(root.matches('form.wizard') ? 'data-start-step' : 'data-start-tab', saved.index);
  });
  document.querySelectorAll('details').forEach(function (details) {
    var saved = pending.details.find(function (entry) { return entry.key === detailsKey(details); });
    if (saved) details.open = saved.open;
  });

  var interrupted = false;
  ['wheel', 'touchstart', 'pointerdown', 'keydown'].forEach(function (name) {
    window.addEventListener(name, function () { interrupted = true; }, {once: true, passive: true});
  });
  function restore() {
    if (interrupted) return;
    var element = locate(pending.anchor);
    var y = element && element.getClientRects().length
      ? scrollY + element.getBoundingClientRect().top - pending.anchor.top : pending.y;
    window.scrollTo({left: pending.x, top: y, behavior: 'instant'});
  }
  // Layout settles after the wizard, images and fonts have initialized.
  document.addEventListener('DOMContentLoaded', function () {
    requestAnimationFrame(function () { requestAnimationFrame(restore); });
  });
  window.addEventListener('load', restore, {once: true});
  if (document.fonts) document.fonts.ready.then(restore);
})();

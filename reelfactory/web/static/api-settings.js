/* Provider switching and independent saves; native forms work without JS. */
(function () {
  'use strict';
  var root = document.querySelector('[data-api-settings]');
  if (!root) return;
  var dirty = new Set(), pending = new Set(), snapshots = new WeakMap();
  function fingerprint(form) { return JSON.stringify(Array.from(new FormData(form).entries())); }
  function prepare(panel) {
    panel.querySelectorAll('[data-reveal-key]').forEach(function (button) { button.hidden = false; });
    var form = panel.querySelector('form');
    snapshots.set(form, fingerprint(form));
    panel.querySelectorAll('[aria-invalid="true"]').forEach(function (input) {
      var details = input.closest('details');
      if (details) details.open = true;
    });
  }
  root.querySelectorAll('[data-provider-panel]').forEach(prepare);
  function select(key) {
    if (!root.querySelector('[data-provider-panel="' + key + '"]')) return;
    root.querySelectorAll('[data-provider-panel]').forEach(function (panel) { panel.hidden = panel.dataset.providerPanel !== key; });
    root.querySelectorAll('[data-provider-link]').forEach(function (link) {
      if (link.dataset.providerLink === key) link.setAttribute('aria-current', 'true');
      else link.removeAttribute('aria-current');
    });
    root.dataset.activeProvider = key;
  }
  function hashProvider() {
    var key = location.hash.replace('#provider-', '');
    return /^[a-z]+$/.test(key) && root.querySelector('[data-provider-panel="' + key + '"]') ? key : root.dataset.activeProvider;
  }
  select(hashProvider());
  window.addEventListener('hashchange', function () { select(hashProvider()); });
  root.addEventListener('click', function (event) {
    var link = event.target.closest('[data-provider-link]');
    if (link) {
      event.preventDefault();
      select(link.dataset.providerLink);
      history.replaceState(null, '', link.getAttribute('href'));
    }
    var reveal = event.target.closest('[data-reveal-key]');
    if (reveal) {
      var input = document.getElementById(reveal.getAttribute('aria-controls'));
      var showing = input.type === 'password';
      input.type = showing ? 'text' : 'password';
      reveal.textContent = showing ? 'Hide' : 'Show';
      reveal.setAttribute('aria-pressed', String(showing));
      reveal.setAttribute('aria-label', (showing ? 'Hide' : 'Show') + ' entered ' + input.closest('.api-key-field').querySelector('label').textContent.toLowerCase());
    }
  });
  function changed(event) {
    var form = event.target.closest('[data-provider-form]');
    if (!form) return;
    var key = form.dataset.providerForm;
    var changed = fingerprint(form) !== snapshots.get(form);
    if (changed) dirty.add(key); else dirty.delete(key);
    form.querySelector('[data-save-state]').textContent = changed ? 'Unsaved changes' : 'Changes apply to this provider only.';
  }
  root.addEventListener('input', changed);
  root.addEventListener('invalid', function (event) {
    var form = event.target.closest('[data-provider-form]');
    if (form) select(form.dataset.providerForm);
    var details = event.target.closest('details');
    if (details) details.open = true;
  }, true);
  root.addEventListener('change', function (event) {
    var checkbox = event.target.closest('[data-remove-key]');
    if (checkbox) document.getElementById(checkbox.dataset.removeKey).disabled = checkbox.checked;
    changed(event);
  });
  if (!window.fetch) return;
  window.addEventListener('beforeunload', function (event) {
    if (dirty.size || pending.size) { event.preventDefault(); event.returnValue = ''; }
  });
  root.addEventListener('submit', async function (event) {
    var form = event.target.closest('[data-provider-form]');
    if (!form) return;
    event.preventDefault();
    var key = form.dataset.providerForm;
    if (pending.has(key)) return;
    var data = new FormData(form);
    var panel = form.closest('[data-provider-panel]');
    var open = Array.from(panel.querySelectorAll('details[open]')).map(function (details) { return details.dataset.section; });
    pending.add(key);
    panel.setAttribute('aria-busy', 'true');
    var disabled = Array.from(form.elements).filter(function (element) { return !element.disabled; });
    disabled.forEach(function (element) { element.disabled = true; });
    form.querySelector('[data-save-state]').textContent = 'Saving settings…';
    try {
      var response = await fetch(form.action, { method: 'POST', body: data });
      var documentResult = new DOMParser().parseFromString(await response.text(), 'text/html');
      var replacement = documentResult.querySelector('[data-provider-panel="' + key + '"]');
      if (!replacement) throw new Error(response.status === 403 ? 'This page has expired. Reload it before saving again.' : 'Could not save settings. Please try again.');
      open.forEach(function (section) { var details = replacement.querySelector('[data-section="' + section + '"]'); if (details) details.open = true; });
      panel.replaceWith(replacement);
      prepare(replacement);
      dirty.delete(key);
      var newLink = documentResult.querySelector('[data-provider-link="' + key + '"]');
      root.querySelector('[data-provider-link="' + key + '"]').replaceWith(newLink);
      root.querySelector('[data-configured-count]').textContent = documentResult.querySelector('[data-configured-count]').textContent;
      var notice = documentResult.querySelector('[data-api-notice]');
      root.querySelector('[data-api-notice]').replaceWith(notice);
      select(root.dataset.activeProvider);
      if (!response.ok) {
        snapshots.set(replacement.querySelector('form'), snapshots.get(form));
        dirty.add(key);
        select(key);
        notice.focus();
      } else if (root.dataset.activeProvider === key) {
        replacement.querySelector('[type="submit"]').focus({ preventScroll: true });
      }
    } catch (error) {
      form.querySelector('[data-save-state]').textContent = error instanceof TypeError
        ? 'Could not reach Reel Factory. Make sure the app is running, then try again.'
        : (error.message || 'Could not save settings. Please try again.');
    } finally {
      pending.delete(key);
      panel.removeAttribute('aria-busy');
      disabled.forEach(function (element) { element.disabled = false; });
    }
  });
})();

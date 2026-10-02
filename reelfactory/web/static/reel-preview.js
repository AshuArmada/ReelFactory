/* Render and watch the exact draft, then open the scene at the playhead. */
(function () {
  'use strict';
  var form = document.getElementById('build-form');
  if (!form || !window.fetch) return;
  form.querySelectorAll('[data-reel-preview]').forEach(function (root) {
    root.hidden = false;
    var lang = root.dataset.lang;
    var panel = root.closest('.tab-panel');
    var list = panel.querySelector('.segment-rows');
    var render = root.querySelector('[data-render-preview]');
    var shape = root.querySelector('[data-preview-aspect]');
    var status = root.querySelector('[data-preview-status]');
    var player = root.querySelector('[data-preview-player]');
    var video = root.querySelector('video');
    var edit = root.querySelector('[data-edit-playing-scene]');
    var seek = root.querySelector('[data-preview-seek]');
    var scenes = [], snapshot = null, busy = false, failed = false;

    function draft() {
      var rows = Array.from(list.children).map(function (row) {
        return Array.from(row.querySelectorAll('select, textarea, input')).map(function (field) { return field.value; });
      });
      var settings = ['tts', 'template', 'voice_rate', 'voice_delivery', 'no_music'].map(function (name) {
        var field = form.querySelector('[name="' + name + '"]');
        return field.type === 'checkbox' ? field.checked : field.value;
      });
      return JSON.stringify([rows, settings, shape.value]);
    }
    function stale() { return snapshot !== null && snapshot !== draft(); }
    function indexAtPlayhead() {
      var index = 0;
      scenes.forEach(function (scene, i) { if (video.currentTime >= scene.start) index = i; });
      return index;
    }
    function updatePlayhead() {
      if (!scenes.length) return;
      var index = indexAtPlayhead();
      root.querySelector('[data-playing-scene]').textContent = 'Scene ' + (index + 1) + ' of ' + scenes.length;
      root.querySelector('[data-preview-end-card]').hidden = !scenes[index].end_card;
      edit.disabled = stale() || busy || failed;
      seek.querySelectorAll('button').forEach(function (button, i) {
        button.setAttribute('aria-current', String(index === i));
      });
    }
    function changed() {
      updatePlayhead();
      if (busy || snapshot === null || failed) return;
      var dirty = stale();
      root.classList.toggle('preview-outdated', dirty);
      status.textContent = dirty
        ? 'You have changes. Click Refresh preview to see them in the video.'
        : 'Preview ready. Play the reel, or jump to a scene and edit it.';
    }
    form.addEventListener('input', changed);
    form.addEventListener('change', changed);
    new MutationObserver(changed).observe(list, { childList: true });
    video.addEventListener('timeupdate', updatePlayhead);
    video.addEventListener('loadedmetadata', updatePlayhead);
    video.addEventListener('error', function () {
      if (!video.getAttribute('src')) return;
      failed = true;
      edit.disabled = true;
      status.textContent = 'The preview could not play. Click Refresh preview to try again.';
    });
    root.querySelector('[data-preview-settings]').addEventListener('click', function () {
      form.querySelectorAll('.step-btn')[2].click();
    });
    edit.addEventListener('click', function () {
      if (edit.disabled) return;
      video.pause();
      var row = list.children[indexAtPlayhead()];
      if (row) form.dispatchEvent(new CustomEvent('scene:edit', { detail: { row: row, trigger: edit } }));
    });
    render.addEventListener('click', async function () {
      if (busy) return;
      var requestedDraft = draft();
      var data = new FormData(form);
      data.set('preview_lang', lang);
      data.set('preview_aspect', shape.value);
      busy = true;
      render.disabled = edit.disabled = true;
      render.querySelector('span').textContent = 'Rendering preview…';
      root.setAttribute('aria-busy', 'true');
      video.pause();
      var start = Date.now();
      function elapsed() {
        status.textContent = 'Rendering your reel preview… ' + Math.floor((Date.now() - start) / 1000) + 's. Keep this page open.';
      }
      elapsed();
      var timer = window.setInterval(elapsed, 1000);
      var succeeded = false;
      try {
        var response = await fetch(form.dataset.reelPreviewUrl, { method: 'POST', body: data });
        var result;
        try { result = await response.json(); }
        catch (_) { throw new Error('The preview could not be rendered. Please try again.'); }
        if (!response.ok) throw new Error(result.error || 'Preview failed. Please try again.');
        scenes = result.scenes;
        snapshot = requestedDraft;
        failed = false;
        seek.replaceChildren();
        scenes.forEach(function (scene, index) {
          var button = document.createElement('button');
          button.type = 'button';
          button.className = 'button small';
          button.textContent = 'Scene ' + (index + 1);
          button.addEventListener('click', function () {
            video.pause();
            // Land inside the shot, beyond the cross-fade from its neighbor.
            var end = scenes[index + 1] ? scenes[index + 1].start : video.duration;
            video.currentTime = Number.isFinite(end) ? (scene.start + end) / 2 : scene.start;
            updatePlayhead();
          });
          seek.appendChild(button);
        });
        video.src = result.url;
        player.hidden = false;
        video.load();
        succeeded = true;
      } catch (error) {
        status.textContent = error.message || 'Preview failed. Please try again.';
      } finally {
        window.clearInterval(timer);
        busy = false;
        render.disabled = false;
        render.querySelector('span').textContent = snapshot === null ? 'Preview reel' : 'Refresh preview';
        root.removeAttribute('aria-busy');
        updatePlayhead();
        if (succeeded) changed();
      }
    });
  });
})();

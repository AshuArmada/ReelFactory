/* Visual scene choices enhance the existing, submitted photo selects. */
(function () {
  'use strict';
  var form = document.getElementById('build-form');
  var dialog = document.getElementById('scene-editor');
  if (!form || !dialog || typeof dialog.showModal !== 'function') return;

  var gallery = dialog.querySelector('[data-scene-gallery]');
  var preview = dialog.querySelector('[data-scene-preview]');
  var status = dialog.querySelector('[data-scene-status]');
  var upload = dialog.querySelector('[data-scene-upload]');
  var fileInput = dialog.querySelector('[data-scene-file]');
  var apply = dialog.querySelector('[data-scene-apply]');
  var activeRow = null, selected = '', opener = null, uploading = false;
  var photoUrl = form.getAttribute('data-photo-url');

  function media(name, controls) {
    var clip = /\.(mp4|mov|m4v|webm)$/i.test(name);
    var element = document.createElement(clip ? 'video' : 'img');
    element.src = photoUrl.replace('__NAME__', encodeURIComponent(name));
    if (clip) {
      element.muted = true;
      element.playsInline = true;
      element.preload = 'metadata';
      element.controls = !!controls;
    } else {
      element.alt = '';
      element.loading = controls ? 'eager' : 'lazy';
    }
    return element;
  }

  function refreshTimelines() {
    form.querySelectorAll('.segment-rows').forEach(function (list) {
      var timeline = list.parentNode.querySelector('.scene-timeline');
      if (!timeline) return;
      timeline.hidden = false;
      timeline.replaceChildren();
      Array.from(list.children).forEach(function (row, index) {
        var edit = row.querySelector('[data-edit-scene]');
        var pick = row.querySelector('.seg-photo-select');
        if (!pick) return;
        edit.hidden = false;
        edit.setAttribute('aria-label', 'Edit scene ' + (index + 1));
        var tile = document.createElement('button');
        tile.type = 'button';
        tile.className = 'scene-tile';
        tile.setAttribute('aria-label', 'Edit scene ' + (index + 1));
        tile.appendChild(media(pick.value));
        var label = document.createElement('span');
        label.textContent = 'Scene ' + (index + 1);
        tile.appendChild(label);
        tile.addEventListener('click', function () { openScene(row, tile); });
        timeline.appendChild(tile);
      });
    });
  }

  function choose(name) {
    selected = name;
    preview.replaceChildren(media(name, true));
    gallery.querySelectorAll('button').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.dataset.name === name));
    });
    apply.disabled = !name || uploading;
  }

  function fillGallery() {
    gallery.replaceChildren();
    Array.from(activeRow.querySelector('.seg-photo-select').options).forEach(function (option, index) {
      var tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'scene-media-tile';
      tile.dataset.name = option.value;
      tile.setAttribute('aria-label', 'Use media ' + (index + 1) + ': ' + option.value);
      tile.appendChild(media(option.value));
      var label = document.createElement('span');
      label.textContent = option.value;
      tile.appendChild(label);
      tile.addEventListener('click', function () { choose(option.value); });
      gallery.appendChild(tile);
    });
  }

  function openScene(row, trigger) {
    form.querySelectorAll('[data-reel-video]').forEach(function (video) { video.pause(); });
    activeRow = row;
    opener = trigger;
    var index = Array.from(row.parentNode.children).indexOf(row) + 1;
    document.getElementById('scene-editor-title').textContent = 'Edit scene ' + index;
    dialog.querySelector('[data-scene-narration]').value = row.querySelector('textarea').value;
    dialog.querySelector('[data-scene-caption]').value = row.querySelector('[name^="seg_overlay_"]').value;
    dialog.querySelector('[data-scene-overlay]').textContent = row.querySelector('[name^="seg_overlay_"]').value;
    var aspect = form.querySelector('[name="aspect"]:checked');
    dialog.querySelector('.scene-stage').style.aspectRatio = aspect ? aspect.value.replace(':', '/') : '9 / 16';
    status.textContent = '';
    fillGallery();
    choose(row.querySelector('.seg-photo-select').value);
    dialog.showModal();
  }

  form.addEventListener('click', function (event) {
    var edit = event.target.closest('[data-edit-scene]');
    if (edit) openScene(edit.closest('.segment-row'), edit);
  });
  form.addEventListener('scene:edit', function (event) {
    openScene(event.detail.row, event.detail.trigger);
  });
  dialog.querySelector('[data-scene-caption]').addEventListener('input', function (event) {
    dialog.querySelector('[data-scene-overlay]').textContent = event.target.value;
  });
  form.addEventListener('change', refreshTimelines);
  form.querySelectorAll('.segment-rows').forEach(function (list) {
    new MutationObserver(refreshTimelines).observe(list, { childList: true });
  });
  document.querySelectorAll('[data-edit-scenes]').forEach(function (button) {
    button.hidden = false;
    button.addEventListener('click', function () { form.querySelectorAll('.step-btn')[1].click(); });
  });
  dialog.querySelectorAll('[data-scene-cancel]').forEach(function (button) {
    button.addEventListener('click', function () { if (!uploading) dialog.close(); });
  });
  dialog.addEventListener('cancel', function (event) { if (uploading) event.preventDefault(); });
  dialog.addEventListener('close', function () {
    preview.replaceChildren(); // Stop any clip being played.
    if (opener && opener.isConnected) opener.focus();
    else if (activeRow && activeRow.isConnected) activeRow.querySelector('[data-edit-scene]').focus();
  });
  apply.addEventListener('click', function () {
    if (!selected || uploading) return;
    var pick = activeRow.querySelector('.seg-photo-select');
    pick.value = selected;
    activeRow.querySelector('textarea').value = dialog.querySelector('[data-scene-narration]').value;
    activeRow.querySelector('[name^="seg_overlay_"]').value = dialog.querySelector('[data-scene-caption]').value;
    pick.dispatchEvent(new Event('change', { bubbles: true }));
    dialog.close();
  });
  upload.addEventListener('click', function () { fileInput.click(); });
  fileInput.addEventListener('change', async function () {
    var file = fileInput.files[0];
    if (!file || uploading) return;
    uploading = true;
    upload.disabled = apply.disabled = true;
    dialog.querySelectorAll('[data-scene-cancel]').forEach(function (button) { button.disabled = true; });
    status.textContent = 'Uploading ' + file.name + '…';
    dialog.setAttribute('aria-busy', 'true');
    try {
      var data = new FormData();
      data.append('media', file);
      data.append('source_photo', activeRow.querySelector('.seg-photo-select').value);
      var response = await fetch(form.getAttribute('data-scene-upload-url'), { method: 'POST', body: data });
      var result;
      try { result = await response.json(); }
      catch (_) { throw new Error('Upload failed. Please try again with a smaller file.'); }
      if (!response.ok) throw new Error(result.error || 'Upload failed. Please try again.');
      // Include inert Add-a-line templates as well as every language's live rows.
      var selects = Array.from(form.querySelectorAll('.seg-photo-select'));
      form.querySelectorAll('.segment-template').forEach(function (template) {
        selects.push.apply(selects, template.content.querySelectorAll('.seg-photo-select'));
      });
      selects.forEach(function (select) {
        select.add(new Option(result.name, result.name));
      });
      fillGallery();
      choose(result.name);
      status.textContent = 'Uploaded to your library. Click Apply to scene to use it.';
    } catch (error) {
      status.textContent = error.message || 'Upload failed. Please try again.';
    } finally {
      uploading = false;
      upload.disabled = false;
      apply.disabled = !selected;
      dialog.querySelectorAll('[data-scene-cancel]').forEach(function (button) { button.disabled = false; });
      dialog.removeAttribute('aria-busy');
      fileInput.value = '';
    }
  });
  refreshTimelines();
})();

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
  var uploadVideo = dialog.querySelector('[data-scene-upload-video]');
  var filter = dialog.querySelector('[data-scene-filter]');
  var fileInput = dialog.querySelector('[data-scene-file]');
  var apply = dialog.querySelector('[data-scene-apply]');
  var member = dialog.querySelector('[data-scene-member]');
  var members = member ? JSON.parse(document.getElementById('collection-media').textContent) : {};
  var activeRow = null, selected = '', opener = null, uploading = false;
  var newScene = false, applied = false;
  var photoUrl = form.getAttribute('data-photo-url');

  function isVideo(name) { return /\.(mp4|mov|m4v|webm)$/i.test(name); }

  function uploadControls() {
    upload.disabled = uploadVideo.disabled = uploading || !!(member && !member.value);
    if (member) member.disabled = uploading;
  }

  function badge(name) {
    var label = document.createElement('span');
    label.className = 'scene-media-kind';
    label.textContent = isVideo(name) ? 'Video' : 'Picture';
    return label;
  }

  function media(name, controls) {
    var clip = isVideo(name);
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
        tile.appendChild(badge(pick.value));
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
    preview.replaceChildren();
    if (name) preview.appendChild(media(name, true));
    dialog.querySelector('[data-scene-video-info]').hidden = !isVideo(name);
    gallery.querySelectorAll('button').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.dataset.name === name));
    });
    apply.disabled = !name || uploading || (newScene && !isVideo(name));
  }

  function fillGallery() {
    gallery.replaceChildren();
    Array.from(activeRow.querySelector('.seg-photo-select').options).forEach(function (option, index) {
      if (member && (!member.value || members[member.value].media.indexOf(option.value) < 0)) return;
      if ((filter.value === 'video' && !isVideo(option.value)) || (filter.value === 'image' && isVideo(option.value))) return;
      var tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'scene-media-tile';
      tile.dataset.name = option.value;
      tile.setAttribute('aria-label', 'Use media ' + (index + 1) + ': ' + option.value);
      tile.appendChild(media(option.value));
      tile.appendChild(badge(option.value));
      var label = document.createElement('span');
      label.textContent = option.value;
      tile.appendChild(label);
      tile.addEventListener('click', function () { choose(option.value); });
      gallery.appendChild(tile);
    });
    if (!gallery.children.length) {
      var empty = document.createElement('p');
      empty.className = 'hint';
      empty.textContent = filter.value === 'video' ? 'No videos yet. Upload a video to use it in your reel.' : 'No pictures to show.';
      gallery.appendChild(empty);
    }
    gallery.querySelectorAll('button').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.dataset.name === selected));
    });
  }

  function openScene(row, trigger, addingVideo) {
    form.querySelectorAll('[data-reel-video]').forEach(function (video) { video.pause(); });
    activeRow = row;
    opener = trigger;
    newScene = !!addingVideo;
    applied = false;
    filter.value = newScene ? 'video' : 'all';
    apply.textContent = newScene ? 'Add video scene' : 'Apply to scene';
    var index = Array.from(row.parentNode.children).indexOf(row) + 1;
    document.getElementById('scene-editor-title').textContent = newScene ? 'Add video scene' : 'Edit scene ' + index;
    dialog.querySelector('[data-scene-narration]').value = row.querySelector('textarea').value;
    dialog.querySelector('[data-scene-caption]').value = row.querySelector('[name^="seg_overlay_"]').value;
    dialog.querySelector('[data-scene-overlay]').textContent = row.querySelector('[name^="seg_overlay_"]').value;
    var aspect = form.querySelector('[name="aspect"]:checked');
    dialog.querySelector('.scene-stage').style.aspectRatio = aspect ? aspect.value.replace(':', '/') : '9 / 16';
    status.textContent = '';
    if (member) {
      var current = row.querySelector('.seg-photo-select').value;
      member.value = newScene ? '' : (Object.keys(members).find(function (key) {
        return members[key].media.indexOf(current) >= 0;
      }) || '');
    }
    uploadControls();
    fillGallery();
    choose(newScene ? '' : row.querySelector('.seg-photo-select').value);
    dialog.showModal();
  }

  form.addEventListener('click', function (event) {
    var edit = event.target.closest('[data-edit-scene]');
    if (edit) openScene(edit.closest('.segment-row'), edit);
    var addVideo = event.target.closest('[data-add-video]');
    if (addVideo) {
      var lang = addVideo.dataset.addVideo;
      var template = form.querySelector('.segment-template[data-lang="' + lang + '"]');
      var list = form.querySelector('.segment-rows[data-lang="' + lang + '"]');
      var row = template.content.firstElementChild.cloneNode(true);
      // Preserve the closing scene: Bold/Premium can replace it with a brand card.
      list.insertBefore(row, list.lastElementChild);
      form.dispatchEvent(new Event('scene:rows-changed'));
      openScene(row, addVideo, true);
    }
  });
  form.querySelectorAll('[data-add-video]').forEach(function (button) { button.hidden = false; });
  filter.addEventListener('change', fillGallery);
  if (member) member.addEventListener('change', function () {
    choose('');
    fillGallery();
    uploadControls();
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
    if (newScene && !applied) {
      activeRow.remove();
      form.dispatchEvent(new Event('scene:rows-changed'));
    }
    if (opener && opener.isConnected) opener.focus();
    else if (activeRow && activeRow.isConnected) activeRow.querySelector('[data-edit-scene]').focus();
  });
  apply.addEventListener('click', function () {
    if (!selected || uploading) return;
    if (newScene && (!isVideo(selected) || !dialog.querySelector('[data-scene-narration]').value.trim())) {
      status.textContent = 'Choose a video and add the words for this scene before adding it.';
      dialog.querySelector('[data-scene-narration]').focus();
      return;
    }
    applied = true;
    var pick = activeRow.querySelector('.seg-photo-select');
    pick.value = selected;
    activeRow.querySelector('textarea').value = dialog.querySelector('[data-scene-narration]').value;
    activeRow.querySelector('[name^="seg_overlay_"]').value = dialog.querySelector('[data-scene-caption]').value;
    pick.dispatchEvent(new Event('change', { bubbles: true }));
    dialog.close();
  });
  upload.addEventListener('click', function () { fileInput.accept = fileInput.dataset.imageAccept; fileInput.click(); });
  uploadVideo.addEventListener('click', function () { fileInput.accept = fileInput.dataset.videoAccept; fileInput.click(); });
  fileInput.addEventListener('change', async function () {
    var file = fileInput.files[0];
    if (!file || uploading || (member && !member.value)) return;
    uploading = true;
    uploadControls();
    upload.disabled = uploadVideo.disabled = apply.disabled = true;
    dialog.querySelectorAll('[data-scene-cancel]').forEach(function (button) { button.disabled = true; });
    status.textContent = 'Uploading ' + file.name + '…';
    dialog.setAttribute('aria-busy', 'true');
    try {
      var data = new FormData();
      data.append('media', file);
      data.append('source_photo', activeRow.querySelector('.seg-photo-select').value);
      if (member) data.append('member_slug', member.value);
      var response = await fetch(form.getAttribute('data-scene-upload-url'), {
        method: 'POST', body: data,
        headers: { 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content }
      });
      var result;
      try { result = await response.json(); }
      catch (_) { throw new Error('Upload failed. Please try again with a smaller file.'); }
      if (!response.ok) throw new Error(result.error || 'Upload failed. Please try again.');
      if (member && members[result.member_slug]) members[result.member_slug].media.push(result.name);
      // Include inert Add-a-line templates as well as every language's live rows.
      var selects = Array.from(form.querySelectorAll('.seg-photo-select'));
      form.querySelectorAll('.segment-template').forEach(function (template) {
        selects.push.apply(selects, template.content.querySelectorAll('.seg-photo-select'));
      });
      selects.forEach(function (select) {
        select.add(new Option(result.name, result.name));
      });
      filter.value = isVideo(result.name) ? 'video' : 'image';
      fillGallery();
      choose(result.name);
      status.textContent = newScene ? 'Video uploaded. Add the words for this scene, then click Add video scene.' : 'Uploaded to your library. Click Apply to scene to use it.';
    } catch (error) {
      status.textContent = error.message || 'Upload failed. Please try again.';
    } finally {
      uploading = false;
      uploadControls();
      apply.disabled = !selected || (newScene && !isVideo(selected));
      dialog.querySelectorAll('[data-scene-cancel]').forEach(function (button) { button.disabled = false; });
      dialog.removeAttribute('aria-busy');
      fileInput.value = '';
    }
  });
  refreshTimelines();
})();

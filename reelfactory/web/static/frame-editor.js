/* Exact decoded-frame painting. Saving creates new media for the scene editor. */
(function () {
  'use strict';
  var form = document.getElementById('build-form'), dialog = document.getElementById('frame-editor');
  if (!form || !dialog || !window.fetch) return;
  function field(name) { return dialog.querySelector('[data-frame-' + name + ']'); }
  var canvas = field('canvas'), ctx = canvas.getContext('2d');
  var status = field('status'), seek = field('seek'), number = field('number'), file = field('file');
  var details = null, source = '', row = null, index = 0, original = null;
  var patches = new Map(), undo = [], busy = false, loading = false, drawing = false, last = null, serial = 0;

  function state() {
    dialog.querySelectorAll('button, input, select').forEach(function (element) { element.disabled = busy || loading || !details; });
    dialog.querySelectorAll('[data-frame-close]').forEach(function (element) { element.disabled = busy; });
    field('prev').disabled = busy || loading || !details || index <= 0;
    field('next').disabled = busy || loading || !details || index >= details.frame_count - 1;
    field('undo').disabled = busy || loading || !undo.length;
    field('reset').disabled = busy || loading || !patches.has(index);
    field('save').disabled = busy || loading || !patches.size;
    canvas.setAttribute('aria-disabled', String(busy || loading));
  }
  function listEdits() {
    field('edits').replaceChildren();
    Array.from(patches.keys()).sort(function (a, b) { return a - b; }).forEach(function (frame) {
      var button = document.createElement('button');
      button.type = 'button'; button.className = 'button small'; button.textContent = 'Frame ' + (frame + 1);
      button.addEventListener('click', function () { load(frame); });
      field('edits').appendChild(button);
    });
    state();
  }
  async function responseData(response) {
    var data;
    try { data = await response.json(); }
    catch (_) { throw new Error('The server did not confirm this request. Your frame edits are still open; try saving again.'); }
    if (!response.ok) throw new Error(data.error || 'Could not process the video. Try again.');
    return data;
  }
  function picture(url) {
    return new Promise(function (resolve, reject) {
      var image = new Image(); image.onload = function () { resolve(image); };
      image.onerror = function () { reject(new Error('Could not load this image. Choose a readable JPG, PNG or WebP.')); };
      image.src = url;
    });
  }
  function show(image) { ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.drawImage(image, 0, 0, canvas.width, canvas.height); }
  async function load(frame) {
    if (busy || loading || drawing || !details) return;
    frame = Math.max(0, Math.min(details.frame_count - 1, Math.round(frame)));
    if (!Number.isFinite(frame)) return;
    var request = ++serial;
    loading = true; state(); status.textContent = 'Loading frame ' + (frame + 1) + '…';
    try {
      var response = await fetch(form.dataset.frameEditorUrl + '/' + frame + '?source_photo=' + encodeURIComponent(source));
      if (!response.ok) { await responseData(response); return; }
      var url = URL.createObjectURL(await response.blob());
      try { var image = await picture(url); } finally { URL.revokeObjectURL(url); }
      if (request !== serial || !dialog.open) return;
      original = image; index = frame; undo = [];
      canvas.width = details.width; canvas.height = details.height;
      show(patches.has(frame) ? await picture(patches.get(frame)) : image);
      seek.value = index; number.value = index + 1;
      field('position').textContent = 'Frame ' + (index + 1) + ' of ' + details.frame_count + ' · ' + details.fps.toFixed(2) + ' fps';
      status.textContent = patches.has(index) ? 'This frame has edits.' : 'Draw on this frame or replace its image.';
    } catch (error) { if (request === serial) { status.textContent = error.message; original = null; } }
    finally { if (request === serial) { loading = false; state(); } }
  }
  function canEdit() {
    if (busy || loading || !original) return false;
    if (patches.size >= 30 && !patches.has(index)) { status.textContent = 'Save these 30 edited frames before editing more.'; return false; }
    return true;
  }
  function checkpoint() { undo.push({image: canvas.toDataURL('image/png'), edited: patches.has(index)}); if (undo.length > 10) undo.shift(); }
  function remember() { patches.set(index, canvas.toDataURL('image/png')); listEdits(); status.textContent = patches.size + ' edited frame(s). Save edited clip when ready.'; }
  function point(event) {
    var box = canvas.getBoundingClientRect();
    return {x: (event.clientX - box.left) * canvas.width / box.width, y: (event.clientY - box.top) * canvas.height / box.height};
  }
  function stroke(from, to) {
    var size = Math.max(1, Math.min(100, Number(field('size').value) || 12));
    ctx.save(); ctx.lineWidth = size; ctx.lineCap = ctx.lineJoin = 'round';
    if (field('tool').value === 'erase') {
      // Clip a round brush to restore the corresponding pixels of the source.
      var distance = Math.hypot(to.x - from.x, to.y - from.y), steps = Math.max(1, Math.ceil(distance / (size / 3)));
      ctx.beginPath();
      for (var i = 0; i <= steps; i++) {
        var x = from.x + (to.x - from.x) * i / steps, y = from.y + (to.y - from.y) * i / steps;
        ctx.moveTo(x + size / 2, y); ctx.arc(x, y, size / 2, 0, Math.PI * 2);
      }
      ctx.clip(); ctx.drawImage(original, 0, 0, canvas.width, canvas.height);
    } else {
      ctx.strokeStyle = ctx.fillStyle = field('color').value;
      ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y); ctx.stroke();
      ctx.beginPath(); ctx.arc(to.x, to.y, size / 2, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();
  }
  canvas.addEventListener('pointerdown', function (event) {
    if (!canEdit() || event.button !== 0) return;
    event.preventDefault(); checkpoint(); drawing = true; last = point(event);
    canvas.setPointerCapture(event.pointerId); stroke(last, last);
  });
  canvas.addEventListener('pointermove', function (event) {
    if (!drawing) return;
    var next = point(event); stroke(last, next); last = next;
  });
  function finish() { if (drawing) { drawing = false; remember(); } }
  canvas.addEventListener('pointerup', finish); canvas.addEventListener('pointercancel', finish);
  canvas.addEventListener('lostpointercapture', finish);
  field('prev').addEventListener('click', function () { load(index - 1); });
  field('next').addEventListener('click', function () { load(index + 1); });
  seek.addEventListener('change', function () { load(Number(seek.value)); });
  number.addEventListener('change', function () { load(Number(number.value) - 1); });
  field('undo').addEventListener('click', async function () {
    if (!canEdit() || !undo.length) return;
    var request = serial;
    loading = true; state();
    try {
      var previous = undo.pop(), image = await picture(previous.image);
      if (request === serial && dialog.open) {
        show(image);
        if (previous.edited) patches.set(index, previous.image);
        else patches.delete(index);
        listEdits(); status.textContent = 'Last stroke or replacement undone.';
      }
    } catch (error) { if (request === serial) status.textContent = error.message; }
    finally { if (request === serial) { loading = false; state(); } }
  });
  field('reset').addEventListener('click', function () {
    if (!original || busy || loading) return;
    show(original); patches.delete(index); undo = []; listEdits(); status.textContent = 'Original frame restored.';
  });
  field('replace').addEventListener('click', function () { if (canEdit()) file.click(); });
  file.addEventListener('change', async function () {
    var selected = file.files[0]; if (!selected || !canEdit()) return;
    if (selected.size > 16 * 1024 * 1024) { status.textContent = 'Choose an image smaller than 16 MB.'; file.value = ''; return; }
    loading = true; state();
    var request = serial;
    var url = URL.createObjectURL(selected);
    try {
      var replacement = await picture(url);
      if (request !== serial || !dialog.open) return;
      checkpoint();
      ctx.fillStyle = '#000'; ctx.fillRect(0, 0, canvas.width, canvas.height);
      var scale = Math.min(canvas.width / replacement.width, canvas.height / replacement.height);
      var width = replacement.width * scale, height = replacement.height * scale;
      ctx.drawImage(replacement, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
      remember();
    } catch (error) { if (request === serial) status.textContent = error.message; }
    finally { URL.revokeObjectURL(url); if (request === serial) { file.value = ''; loading = false; state(); } }
  });
  function close() {
    if (busy) return;
    if (patches.size && !window.confirm('Discard unsaved frame edits?')) return;
    serial++; patches.clear(); dialog.close();
  }
  dialog.querySelectorAll('[data-frame-close]').forEach(function (button) { button.addEventListener('click', close); });
  dialog.addEventListener('cancel', function (event) { event.preventDefault(); close(); });
  dialog.addEventListener('close', function () { serial++; original = null; ctx.clearRect(0, 0, canvas.width, canvas.height); });
  form.addEventListener('frames:open', async function (event) {
    source = event.detail.source; row = event.detail.row; details = null; patches.clear(); undo = [];
    original = null; index = 0; busy = false; loading = false; listEdits();
    dialog.showModal(); status.textContent = 'Reading video frames…';
    var request = ++serial;
    try {
      var result = await responseData(await fetch(form.dataset.frameEditorUrl + '?source_photo=' + encodeURIComponent(source)));
      if (request !== serial || !dialog.open) return;
      details = result; seek.max = details.frame_count - 1; number.max = details.frame_count;
      await load(0);
    } catch (error) { status.textContent = error.message; state(); }
  });
  field('save').addEventListener('click', async function () {
    if (busy || loading || !details || !patches.size) return;
    busy = true; state(); status.textContent = 'Saving an edited copy of the clip. Keep this editor open…';
    var data = new FormData(); data.append('source_photo', source); data.append('version', details.version);
    data.append('frames', JSON.stringify(Array.from(patches.keys())));
    try {
      for (var entry of patches) data.append('frame_' + entry[0], await (await fetch(entry[1])).blob(), entry[0] + '.png');
      var media = await responseData(await fetch(form.dataset.frameEditorUrl, {method: 'POST', body: data,
        headers: {'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content}}));
      patches.clear(); busy = false; dialog.close();
      form.dispatchEvent(new CustomEvent('frames:saved', {detail: {media: media, row: row}}));
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; state(); }
  });
})();

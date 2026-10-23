/* Optional generated scene media. Generation never changes submitted scene rows. */
(function () {
  'use strict';
  var form = document.getElementById('build-form');
  var dialog = document.getElementById('scene-editor');
  if (!form || !dialog || !window.fetch || !window.crypto) return;
  var tools = dialog.querySelector('[data-ai-tools]');
  if (!tools) return;
  tools.hidden = false;
  function field(name) { return tools.querySelector('[data-ai-' + name + ']'); }
  var enable = field('enable'), controls = field('controls'), source = field('source');
  var prompt = field('prompt'), generate = field('generate'), refresh = field('refresh');
  var jobsSelect = field('jobs'), message = field('status'), result = field('result'), video = field('video');
  var activeRow = null, original = '', allowed = [], jobs = [], current = null;
  var collection = false, memberSlug = '';
  var submitting = false, refreshing = false, timer = null, clientToken = '';
  var registered = new Set();
  var headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content };

  function register(job) {
    if (!job.name || registered.has(job.name)) return;
    registered.add(job.name);
    form.dispatchEvent(new CustomEvent('scene:media-added', {detail: job}));
  }

  function state() {
    controls.hidden = !enable.checked;
    generate.disabled = submitting || refreshing || !source.value;
    refresh.disabled = submitting || refreshing || !current;
    source.disabled = submitting;
  }
  async function call(url, options) {
    var response = await fetch(url, options);
    var data;
    try { data = await response.json(); }
    catch (_) { throw new Error('The server did not confirm this request. Refresh recent jobs before generating again.'); }
    if (!response.ok) throw new Error(data.error || 'Could not complete the request. Refresh recent AI clips and try again.');
    return data;
  }
  function choices() {
    var selected = current && current.id;
    jobsSelect.replaceChildren(new Option('Choose a generation', ''));
    jobs.filter(eligible).forEach(function (job) {
      var label = 'ElevenLabs' + ' · ' + job.source_photo + ' · ' + job.duration + 's · ' + job.status;
      jobsSelect.add(new Option(label, job.id));
    });
    jobsSelect.value = selected || '';
  }
  function eligible(job) { return !collection || (memberSlug && job.member_slug === memberSlug); }
  function remember(job) {
    jobs = [job].concat(jobs.filter(function (entry) { return entry.id !== job.id; }));
    choices();
  }
  function show(job) {
    current = job;
    remember(job);
    clearTimeout(timer);
    result.hidden = job.status !== 'completed' || !job.name;
    if (!result.hidden) {
      register(job);
      if (video.getAttribute('src') !== job.url) video.src = job.url;
      message.textContent = 'Clip ready. Preview it, then use it or keep your current media.';
    } else {
      video.pause();
      video.removeAttribute('src');
      var progress = {submitting: 'Submitting to ElevenLabs…', pending: 'Clip pending at ElevenLabs.', generating: 'ElevenLabs is generating this clip.'};
      message.textContent = job.error || (progress[job.status] || 'Generation ' + job.status + '.') + ' You can keep editing; the generation is saved.';
      if ((job.status === 'pending' || job.status === 'generating' || job.status === 'submitting') && dialog.open && enable.checked) timer = setTimeout(check, 5000);
    }
    state();
  }
  async function history() {
    var row = activeRow;
    try {
      var data = await call(form.dataset.aiVideoUrl);
      if (row !== activeRow || !dialog.open) return;
      jobs = data.jobs;
      choices();
      if (current) show(jobs.find(function (job) { return job.id === current.id; }) || current);
    } catch (error) { if (row === activeRow && dialog.open) message.textContent = error.message; }
  }
  async function check() {
    if (!current || refreshing || !dialog.open || !enable.checked) return;
    var id = current.id, row = activeRow;
    refreshing = true;
    state();
    try {
      var job = await call(form.dataset.aiVideoRefreshUrl.replace('__JOB__', encodeURIComponent(id)), {method: 'POST', headers: headers, body: '{}'});
      register(job);
      if (row === activeRow && current && current.id === id && dialog.open) show(job);
    } catch (error) {
      if (row === activeRow) message.textContent = error.message + ' Use Refresh selected generation to retry without a new generation.';
    } finally { refreshing = false; state(); }
  }
  form.addEventListener('scene:context', function (event) {
    var data = event.detail, changed = data.opening || data.row !== activeRow;
    if (changed) {
      clearTimeout(timer);
      activeRow = data.row;
      original = data.original;
      current = null;
      clientToken = '';
      enable.checked = false;
      result.hidden = true;
      video.pause();
      prompt.value = 'A subtle, steady camera move around the product, preserving its appearance and lighting. ' + data.row.querySelector('textarea').value.slice(0, 600);
      var aspect = form.querySelector('[name="aspect"]:checked');
      field('aspect').value = aspect && aspect.value === '16:9' ? '16:9' : '9:16';
      message.textContent = '';
    }
    allowed = data.names;
    collection = data.collection;
    memberSlug = data.memberSlug;
    if (current && !eligible(current)) {
      clearTimeout(timer);
      current = null;
      result.hidden = true;
      video.pause();
      message.textContent = '';
    }
    var previous = source.value;
    source.replaceChildren();
    allowed.forEach(function (name) { source.add(new Option(name, name)); });
    source.value = allowed.indexOf(previous) >= 0 && !changed ? previous : allowed.indexOf(data.selected) >= 0 ? data.selected : allowed[0] || '';
    choices();
    state();
  });
  enable.addEventListener('change', function () {
    state();
    clearTimeout(timer);
    if (enable.checked) history();
    else video.pause();
  });
  source.addEventListener('change', state);
  function token() {
    return Array.from(crypto.getRandomValues(new Uint8Array(16))).map(function (value) { return value.toString(16).padStart(2, '0'); }).join('');
  }
  generate.addEventListener('click', async function () {
    if (submitting || !enable.checked || !source.value) return;
    if (prompt.value.trim().length < 5) { message.textContent = 'Describe the clip before generating.'; prompt.focus(); return; }
    var row = activeRow;
    if (!clientToken) clientToken = token();
    submitting = true;
    state();
    message.textContent = 'Submitting one clip to ElevenLabs…';
    try {
      var job = await call(form.dataset.aiVideoUrl, {method: 'POST', headers: headers, body: JSON.stringify({
        allow_generation: true, provider: 'elevenlabs', request_id: clientToken, prompt: prompt.value, source_photo: source.value,
        use_reference: field('reference').checked, model: field('model').value, duration: field('duration').value,
        aspect: field('aspect').value, resolution: field('resolution').value
      })});
      clientToken = '';
      if (row === activeRow && dialog.open) {
        remember(job);
        if (eligible(job)) show(job);
        else message.textContent = 'Generation saved for its reference product. Select that product to view it in Recent AI clips.';
      }
    } catch (error) { if (row === activeRow) message.textContent = error.message + ' Retry uses the same submission ID.'; }
    finally { submitting = false; state(); }
  });
  jobsSelect.addEventListener('change', function () {
    var job = jobs.find(function (entry) { return entry.id === jobsSelect.value; });
    if (job) { show(job); check(); }
  });
  refresh.addEventListener('click', check);
  field('use').addEventListener('click', function () {
    if (!current || current.status !== 'completed') return;
    form.dispatchEvent(new CustomEvent('scene:media-select', {detail: {name: current.name}}));
    message.textContent = 'AI clip selected. Click Apply to scene to include it in this reel.';
  });
  field('keep').addEventListener('click', function () {
    form.dispatchEvent(new CustomEvent('scene:media-select', {detail: {name: original}}));
    message.textContent = 'Original media selected. Apply to scene or Cancel to keep it.';
  });
  form.addEventListener('scene:closed', function () { clearTimeout(timer); video.pause(); });
  state();
})();

(function () {
  'use strict';
  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  async function api(url, options) {
    var response = await fetch(url, Object.assign({headers: {'X-CSRF-Token': csrf}}, options || {}));
    var data = await response.json();
    if (!response.ok) throw new Error(data.error || 'The request failed. Reload the page and try again.');
    return data;
  }
  var jobs = document.getElementById('render-jobs');
  if (jobs) {
    var status = document.getElementById('queue-status');
    async function refresh() {
      try {
        var data = await api('/renders/status');
        var unknown = false;
        data.jobs.forEach(function (job) {
          var card = document.querySelector('[data-job="' + job.id + '"]');
          if (!card) { unknown = true; return; }
          ['status', 'phase', 'completed', 'attempt', 'elapsed_seconds', 'error'].forEach(function (key) {
            card.querySelector('[data-field="' + key + '"]').textContent = job[key];
          });
          card.querySelector('[data-action="cancel"]').hidden = job.status !== 'queued';
          card.querySelector('[data-action="retry"]').hidden = ['failed', 'interrupted', 'cancelled'].indexOf(job.status) < 0;
          card.querySelector('[data-action="repeat"]').hidden = job.status !== 'completed';
          var list = card.querySelector('[data-outputs]');
          job.outputs.forEach(function (output) {
            if (Array.from(list.children).some(function (li) { return li.dataset.output === output; })) return;
            var li = document.createElement('li');
            li.dataset.output = output;
            var link = document.createElement('a');
            link.href = '/out/' + output.split('/').map(encodeURIComponent).join('/');
            link.textContent = output.substring(output.indexOf('/') + 1);
            li.appendChild(link);
            if (output.endsWith('.mp4')) {
              li.appendChild(document.createTextNode(' · '));
              var schedule = document.createElement('a');
              schedule.href = '/growth?video=' + encodeURIComponent(output);
              schedule.textContent = 'Schedule this reel';
              li.appendChild(schedule);
            }
            list.appendChild(li);
          });
        });
        status.textContent = unknown ? 'New jobs were added. Refresh to see them.' : 'Status is up to date.';
      } catch (error) {
        status.textContent = 'Status could not refresh. Check that the Reel Factory server is running.';
      } finally {
        setTimeout(refresh, 3000);
      }
    }
    setTimeout(refresh, 1500);
  }

  var panel = document.getElementById('render-presets');
  if (!panel) return;
  var form = document.getElementById('build-form');
  var choice = document.getElementById('render-preset-choice');
  var message = document.getElementById('render-preset-status');
  var entries = [];
  async function reload(selected) {
    entries = (await api('/renders/presets')).presets;
    choice.replaceChildren(new Option('Choose a preset', ''));
    entries.forEach(function (p) { choice.add(new Option(p.name, p.id)); });
    choice.value = selected || '';
  }
  async function save(data) {
    var saved = await api('/renders/presets', {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)});
    await reload(saved.id);
    message.textContent = 'Preset saved. Apply it to use these settings.';
  }
  panel.addEventListener('click', async function (event) {
    var button = event.target.closest('[data-preset-action]');
    if (!button) return;
    button.disabled = true;
    try {
      var action = button.dataset.presetAction;
      if (action === 'save') {
        var values = new FormData(form), settings = {};
        ['tts', 'preset', 'template', 'voice_rate', 'voice_delivery'].forEach(function (key) { settings[key] = values.get(key) || ''; });
        settings.no_music = values.get('no_music') === 'on';
        settings.lang = values.getAll('lang');
        settings.aspect = values.getAll('aspect');
        await save({schema: 1, name: document.getElementById('render-preset-name').value, settings: settings});
        return;
      }
      var selected = entries.find(function (p) { return p.id === choice.value; });
      if (!selected) throw new Error('Choose a saved preset first.');
      if (action === 'export') { window.location.href = '/renders/presets/' + selected.id; return; }
      if (action === 'delete') {
        await api('/renders/presets/' + selected.id + '/delete', {method: 'POST'});
        await reload();
        message.textContent = 'Preset deleted.';
        return;
      }
      Object.keys(selected.settings).forEach(function (key) {
        form.querySelectorAll('[name="' + key + '"]').forEach(function (control) {
          var value = selected.settings[key];
          if (control.type === 'checkbox') control.checked = Array.isArray(value) ? value.includes(control.value) : value;
          else control.value = value;
          control.dispatchEvent(new Event('change', {bubbles: true}));
        });
      });
      message.textContent = 'Settings applied. Review the selected languages and scripts before building.';
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  });
  document.getElementById('render-preset-import').addEventListener('change', async function (event) {
    var file = event.target.files[0];
    if (!file) return;
    try {
      if (file.size > 32768) throw new Error('Preset files must be smaller than 32 KB.');
      await save(JSON.parse(await file.text()));
    } catch (error) { message.textContent = 'Could not import preset: ' + error.message; }
    finally { event.target.value = ''; }
  });
  reload().catch(function (error) { message.textContent = error.message; });
})();

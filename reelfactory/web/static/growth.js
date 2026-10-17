// Guard accidental double-clicks while keeping every workflow usable without JS.
document.querySelectorAll('.growth-workspace form').forEach(form => {
  form.addEventListener('submit', event => {
    if (form.dataset.submitting) { event.preventDefault(); return; }
    form.dataset.submitting = 'true';
    const button = event.submitter;
    if (button) { button.disabled = true; button.textContent = 'Working…'; }
  });
});
window.addEventListener('pageshow', event => {
  if (event.persisted) window.location.reload();
});

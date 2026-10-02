"""Local API configuration; credentials never enter rendered HTML."""
import os
from pathlib import Path
import secrets
import tempfile
from threading import Lock
from urllib.parse import urlsplit

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

from .. import gemini, local_llm
from ..config import Brand, read_yaml, write_yaml

bp = Blueprint('api_settings', __name__)
_lock = Lock()
# (environment name, label, aliases)
PROVIDERS = {
    'gemini': ('Gemini', 'Hindi and English scripts, photo analysis and narration.', [
        ('GEMINI_API_KEY', 'API key', gemini._ENV_KEY_NAMES),
        ('GEMINI_API_KEY_BACKUP', 'Backup key (optional)', gemini._ENV_BACKUP_KEY_NAMES)],
        [('gemini_script_model', 'Script model'), ('gemini_tts_model', 'Voice model')]),
    'inception': ('Inception', 'English scripts only.', [
        ('INCEPTION_API_KEY', 'API key', {'inception_api_key'})],
        [('INCEPTION_MODEL', 'Model'), ('INCEPTION_BASE_URL', 'API URL')]),
    'local': ('Local model', 'Ollama or LM Studio. Usually no API key is needed.', [
        ('LOCAL_LLM_API_KEY', 'API key (optional)', local_llm._ENV_KEY_NAMES)],
        [('local_script_model', 'Model'), ('local_base_url', 'Server URL')]),
    'elevenlabs': ('ElevenLabs', 'Narration. Choose voice IDs in Brand settings.', [
        (name, label, {name.lower()}) for name, label in [
            ('ELEVENLABS_API_KEY', 'API key'), ('ELEVENLABS_API_KEY_BACKUP', 'Backup key (optional)'),
            ('ELEVENLABS_API_KEYS', 'Additional keys (comma separated, optional)')]],
        [('elevenlabs_model', 'Voice model')]),
    'pexels': ('Pexels', 'Stock photos.', [('PEXELS_API_KEY', 'API key', {'pexels_api_key', 'pexels_key'})], []),
    'pixabay': ('Pixabay', 'Stock photos.', [('PIXABAY_API_KEY', 'API key', {'pixabay_api_key', 'pixabay_key'})], []),
}
DEFAULTS = {'INCEPTION_MODEL': 'mercury-2.5', 'INCEPTION_BASE_URL': 'https://api.inceptionlabs.ai/v1'}
CAPABILITIES = {
    'gemini': ['Scripts', 'Photo analysis', 'Narration'],
    'inception': ['English scripts'], 'local': ['Local scripts'],
    'elevenlabs': ['Narration'], 'pexels': ['Stock photos'], 'pixabay': ['Stock photos'],
}


def _entries(text):
    for line in text.splitlines():
        name, sep, value = line.strip().partition('=')
        if sep and not name.startswith('#'):
            yield name.strip().lower(), value.strip().strip('"\'')


def _read_env(path):
    return path.read_text(encoding='utf-8-sig') if path.exists() else ''


def _save_env(path, updates):
    text = _read_env(path)
    names = set().union(*(aliases for _, aliases in updates.values()))
    lines = [line for line in text.splitlines()
             if line.strip().partition('=')[0].strip().lower() not in names]
    lines.extend(f'{name}={value}' for name, (value, _) in updates.items() if value)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.api-settings-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write('\n'.join(lines) + '\n')
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _validate(value, label, url=False):
    if len(value) > 4096 or any(ord(c) < 32 or c in '\x85\u2028\u2029"\'' for c in value):
        raise ValueError(f'{label}: enter a single value without quotes or line breaks.')
    if url:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f'{label}: enter an HTTP or HTTPS URL without credentials or query parameters.')
        if parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError(f'{label}: use HTTPS, or HTTP for a local server.')


@bp.route('/settings/apis', methods=['GET', 'POST'])
def page():
    path = Path(current_app.config['API_ENV_PATH'])
    brand_path = Path(current_app.config['API_BRAND_PATH'])
    session.setdefault('api_csrf', secrets.token_urlsafe(32))
    error = None
    error_field = None
    active = request.form.get('provider') if request.method == 'POST' else request.args.get('provider')
    active = active if active in PROVIDERS else 'gemini'
    status = 200
    if request.method == 'POST':
        if not secrets.compare_digest(session['api_csrf'], request.form.get('csrf_token', '')):
            return 'Refresh the API settings page and try again.', 400
        provider = PROVIDERS.get(request.form.get('provider'))
        if not provider:
            return 'Unknown API provider.', 400
        try:
            updates, models = {}, {}
            for name, label, aliases in provider[2]:
                error_field = name
                value = request.form.get(name, '').strip()
                _validate(value, label)
                if value or request.form.get('clear_' + name) == '1':
                    updates[name] = ('' if request.form.get('clear_' + name) == '1' else value, aliases)
            for name, label in provider[3]:
                if name not in request.form:
                    continue
                value = request.form[name].strip()
                error_field = name
                if not value:
                    raise ValueError(f'{label} cannot be blank.')
                _validate(value, label, url='url' in name.lower())
                if name.isupper():
                    updates[name] = (value, {name.lower()})
                else:
                    models[name] = value
            error_field = None
            with _lock:
                if models:
                    data = read_yaml(brand_path) if brand_path.exists() else {}
                    data.update(models)
                    write_yaml(brand_path, data)
                if updates:
                    _save_env(path, updates)
            return redirect(url_for('.page', saved=active, provider=active))
        except ValueError as exc:
            error, status = str(exc), 400
        except OSError:
            error, status = 'Could not save settings. Check that the configuration files are writable.', 500
    try:
        entries = list(_entries(_read_env(path)))
        data = read_yaml(brand_path) if brand_path.exists() else {}
    except (OSError, ValueError):
        entries, data = [], {}
        error, status = 'Could not read configuration files.', 500
    cards = []
    defaults = Brand()
    for key, (title, note, keys, fields) in PROVIDERS.items():
        secrets_ui = []
        for name, label, aliases in keys:
            external = bool(os.environ.get(name))
            configured = external or any(value for alias, value in entries if alias in aliases)
            secrets_ui.append(dict(name=name, label=label, configured=configured, external=external))
        models_ui = []
        for name, label in fields:
            value = (next((v for k, v in entries if k == name.lower()), DEFAULTS.get(name, ''))
                     if name.isupper() else data.get(name, getattr(defaults, name, '')))
            if error and key == active and name in request.form:
                value = request.form[name][:4096]
            models_ui.append(dict(name=name, label=label, value=value, external=bool(os.environ.get(name))))
        configured = any(item['configured'] for item in secrets_ui)
        cards.append(dict(key=key, title=title, note=note, keys=secrets_ui, fields=models_ui,
                          configured=configured, capabilities=CAPABILITIES[key],
                          state='Key available' if configured else ('No key required' if key == 'local' else 'Not configured')))
    saved_provider = request.args.get('saved')
    saved_title = PROVIDERS[saved_provider][0] if saved_provider in PROVIDERS else None
    response = current_app.make_response((render_template('api_settings.html', cards=cards,
        csrf_token=session['api_csrf'], error=error, error_field=error_field, active=active,
        saved_title=saved_title, configured_count=sum(card['configured'] for card in cards)), status))
    response.headers['Cache-Control'] = 'no-store'
    return response


def register(app, brand_path):
    app.config['API_ENV_PATH'] = local_llm.ROOT / '.env'
    app.config['API_BRAND_PATH'] = brand_path
    app.register_blueprint(bp)

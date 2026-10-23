"""Product-scoped AI clips, explicitly selected through scene media."""
from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile

from flask import Blueprint, current_app, request, url_for

from .. import ai_video as provider
from ..config import Product, read_yaml, write_yaml
from ..growth import Store
from ..render import RenderError, validate_video
from ..storage import file_lock
from .diagnostics import record_failure

bp = Blueprint('ai_video', __name__)


def _folder(slug):
    from .app import _safe_product_dir
    folder = _safe_product_dir(current_app.config['AI_VIDEO_PRODUCTS'], slug)
    if folder is None or not (folder / 'product.yaml').is_file():
        raise ValueError('This product no longer exists.')
    return folder


def _public(job):
    result = {key: job.get(key, '') for key in ('id', 'status', 'error', 'source_photo', 'member_slug', 'model', 'duration', 'aspect', 'name')}
    result['provider'] = job.get('provider', 'elevenlabs')
    if result['name']:
        path = current_app.config['AI_VIDEO_PRODUCTS'] / job['slug'] / 'photos' / result['name']
        if path.is_file():
            result['url'] = url_for('product_photo', slug=job['slug'], filename=result['name'])
        else:
            result.update(status='deleted', name='', error='This imported clip was deleted. Choose another clip; it will not be restored automatically.')
    return result


def _store():
    return current_app.extensions['ai_video_store']


@bp.route('/products/<slug>/scenes/ai-video', methods=['GET', 'POST'])
def jobs(slug):
    try:
        folder = _folder(slug)
        store = _store()
        if request.method == 'GET':
            return {'jobs': [_public(job) for job in store.list('clip') if job['slug'] == slug][:30]}
        values = request.get_json(silent=True) or {}
        if not isinstance(values, dict) or values.get('allow_generation') is not True:
            raise ValueError('Enable optional AI video and choose Generate clip to use ElevenLabs credits.')
        token = values.get('request_id', '')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', token):
            raise ValueError('Refresh the editor and try generating again.')
        # A repeated client token returns the same job, even after a lost response.
        with file_lock(store.path.with_name('submissions')):
            previous = next((job for job in store.list('clip') if job['slug'] == slug
                             and job.get('provider', 'elevenlabs') == 'elevenlabs'
                             and job.get('request_id') == token), None)
            if previous:
                return _public(previous), 200
            payload = provider.settings(values)
            key, fingerprint = provider.credentials()
            with file_lock(folder / 'product.yaml'):
                product = Product.load(folder)
                source = str(values.get('source_photo', ''))
                media = {path.name: path for path in product.photos}
                if source not in media:
                    raise ValueError('Choose existing media for this scene before generating a clip.')
                member_slug = ''
                if product.collection_members:
                    owner = next((member for member in product.collection_members if source in member['media'].values()), None)
                    if owner is None:
                        raise ValueError('Choose media belonging to a product in this collection.')
                    member_slug = owner['slug']
                if values.get('use_reference', True) is not False:
                    payload['start_frame'] = provider.image_reference(media[source])
            payload['prompt'] += ('\nOne continuous shot with gentle, stable motion and consistent lighting. '
                                  'Preserve the reference product geometry, materials and colors. '
                                  'No cuts, added text, logos or speech. Narration and captions will be added separately.')
            job = store.add('clip', dict(slug=slug, request_id=token, status='submitting', error='',
                provider='elevenlabs',
                source_photo=source, member_slug=member_slug, model=payload['model_id'],
                duration=payload['duration_secs'], aspect=payload['aspect_ratio'], resolution=payload['resolution'],
                prompt=values['prompt'], key_fingerprint=fingerprint, remote_id='', name=''))
        try:
            remote_id = provider.create(payload, key)
            job = store.update(job['id'], dict(status='pending', remote_id=remote_id))
        except provider.VideoError as exc:
            record_failure(exc)
            job = store.update(job['id'], dict(status='uncertain' if exc.uncertain else 'failed', error=str(exc)))
        return _public(job), 202
    except (ValueError, provider.VideoError, OSError) as exc:
        if isinstance(exc, OSError):
            record_failure(exc)
            return {'error': 'Could not save this generation. Check disk space and permissions, then refresh existing jobs before generating again.'}, 500
        return {'error': str(exc)}, 400


@bp.post('/products/<slug>/scenes/ai-video/<id>/refresh')
def refresh(slug, id):
    try:
        if not re.fullmatch(r'[a-f0-9]{32}', id):
            raise ValueError('Choose an existing generation from recent AI clips.')
        folder = _folder(slug)
        store = _store()
        with file_lock(store.path.with_name('job-' + id)):
            job = store.get(id, 'clip')
            if job['slug'] != slug:
                return {'error': 'This generation belongs to another product.'}, 404
            if job['status'] in {'failed', 'uncertain'}:
                return _public(job)
            if job['status'] == 'submitting':
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(job['created_at'])).total_seconds()
                if elapsed > 180:
                    job = store.update(id, dict(status='uncertain', error='Submission was not confirmed. Check ElevenLabs history before generating again.'))
                return _public(job)
            if job.get('name'):
                if (folder / 'photos' / job['name']).is_file():
                    return _public(job)
                raise ValueError('This imported clip was deleted. It will not be restored automatically.')
            key, _ = provider.credentials(job['key_fingerprint'])
            result = provider.status(job['remote_id'], key)
            if result['status'] == 'failed':
                job = store.update(id, dict(status='failed', error=result['error']))
            elif result['status'] != 'completed':
                job = store.update(id, dict(status=result['status'], error=''))
            else:
                # Download first, then lock product metadata only for local import.
                with tempfile.TemporaryDirectory(prefix='clip-', dir=store.path.parent) as temporary:
                    clip = Path(temporary) / 'clip.mp4'
                    provider.download(result['content_url'], clip)
                    validate_video(clip)
                    if not (folder / 'product.yaml').is_file():
                        raise ValueError('The product was deleted while this clip was generating.')
                    name = 'ai-' + id + '.mp4'
                    with file_lock(folder / 'product.yaml'):
                        data = read_yaml(folder / 'product.yaml')
                        members = data.get('collection_members', [])
                        owner = None
                        if job['member_slug']:
                            owner = next((member for member in members if member['slug'] == job['member_slug']), None)
                            if owner is None:
                                raise ValueError('The source product is no longer in this collection.')
                        photo_dir = folder / 'photos'
                        photo_dir.mkdir(exist_ok=True)
                        destination = photo_dir / name
                        clip.replace(destination)
                        try:
                            if owner is not None:
                                owner['media'][name] = name
                                write_yaml(folder / 'product.yaml', data)
                        except OSError:
                            destination.unlink(missing_ok=True)
                            raise
                    job = store.update(id, dict(status='completed', name=name, error=''))
            return _public(job)
    except (ValueError, provider.VideoError, OSError, RenderError) as exc:
        record_failure(exc)
        message = 'Could not import the clip. Check disk space, permissions and FFmpeg, then refresh this generation.' if isinstance(exc, OSError) else str(exc)
        return {'error': message}, 400


def register(app, workspace, products_root):
    app.config['AI_VIDEO_PRODUCTS'] = Path(products_root)
    app.extensions['ai_video_store'] = Store(workspace, folder='ai_video')
    app.register_blueprint(bp)

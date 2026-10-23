"""Product-scoped AI clips, explicitly selected through scene media."""
import base64
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import re
import tempfile
import uuid

from flask import Blueprint, current_app, request, send_file, url_for

from .. import ai_video as provider
from ..config import Product, read_yaml, write_yaml
from ..growth import Store
from ..render import RenderError, validate_photos, validate_video
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
        if values.get('provider', 'elevenlabs') != 'elevenlabs':
            raise ValueError('Use Import Gemini image for the Gemini workflow. It does not use ElevenLabs credits.')
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


@bp.get('/products/<slug>/scenes/ai-video/reference')
def reference(slug):
    """Download a local still (or opening frame) for manual upload to Gemini."""
    try:
        product = Product.load(_folder(slug))
        source = next((path for path in product.photos if path.name == request.args.get('source_photo')), None)
        if source is None:
            raise ValueError('Choose existing reference media for this product.')
        data = provider.image_reference(source)
        extension = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}[data['mime_type']]
        return send_file(BytesIO(base64.b64decode(data['content_base64'])), mimetype=data['mime_type'],
                         as_attachment=True, download_name='gemini-reference' + extension)
    except (ValueError, provider.VideoError, OSError) as exc:
        return {'error': str(exc)}, 400


@bp.post('/products/<slug>/scenes/ai-video/gemini-import')
def gemini_import(slug):
    """Import an image made in Gemini Apps and animate it locally, with no key."""
    try:
        folder, store = _folder(slug), _store()
        token = request.form.get('request_id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', token):
            raise ValueError('Refresh the editor and try importing again.')
        aspect, duration = request.form.get('aspect', '9:16'), request.form.get('duration', '6')
        if aspect not in ('9:16', '16:9') or duration not in ('4', '6', '8'):
            raise ValueError('Choose a supported clip shape and length.')
        upload = request.files.get('image')
        extension = Path(upload.filename or '').suffix.lower() if upload else ''
        if extension not in ('.jpg', '.jpeg', '.png', '.webp'):
            raise ValueError('Import a JPG, PNG or WebP image downloaded from Gemini.')
        source = request.form.get('source_photo', '')
        # A lost import response can be retried without duplicating local media.
        with file_lock(store.path.with_name('gemini-submissions')):
            previous = next((job for job in store.list('clip') if job['slug'] == slug
                             and job.get('provider') == 'gemini' and job.get('request_id') == token), None)
            if previous:
                return _public(previous)
            with file_lock(folder / 'product.yaml'):
                product = Product.load(folder)
                if source not in {path.name for path in product.photos}:
                    raise ValueError('Choose existing reference media before importing.')
                owner = next((member for member in product.collection_members if source in member['media'].values()), None)
                if product.collection_members and owner is None:
                    raise ValueError('Choose reference media belonging to a product in this collection.')
                member_slug = owner['slug'] if owner else ''
            with tempfile.TemporaryDirectory(prefix='gemini-', dir=store.path.parent) as temporary:
                image = Path(temporary) / ('image' + extension)
                content = upload.stream.read(provider.MAX_REFERENCE_BYTES + 1)
                if not content or len(content) > provider.MAX_REFERENCE_BYTES:
                    raise ValueError('Choose a nonempty Gemini image smaller than 25 MB.')
                image.write_bytes(content)
                validate_photos([image])
                clip = Path(temporary) / 'clip.mp4'
                provider.animate_image(image, clip, aspect=aspect, duration=duration)
                validate_video(clip)
                name = 'ai-' + uuid.uuid4().hex + '.mp4'
                with file_lock(folder / 'product.yaml'):
                    data = read_yaml(folder / 'product.yaml')
                    owner = next((member for member in data.get('collection_members', [])
                                  if member['slug'] == member_slug and source in member['media'].values()), None)
                    if member_slug and owner is None:
                        raise ValueError('The reference product is no longer in this collection.')
                    destination = folder / 'photos' / name
                    clip.replace(destination)
                    try:
                        if owner is not None:
                            owner['media'][name] = name
                            write_yaml(folder / 'product.yaml', data)
                        job = store.add('clip', dict(slug=slug, request_id=token, status='completed', error='',
                            provider='gemini', source_photo=source, member_slug=member_slug,
                            model='Gemini Nano Banana + local motion', duration=int(duration), aspect=aspect,
                            resolution='720p', name=name))
                    except Exception:
                        destination.unlink(missing_ok=True)
                        if owner is not None:
                            owner['media'].pop(name, None)
                            write_yaml(folder / 'product.yaml', data)
                        raise
        return _public(job), 201
    except (ValueError, provider.VideoError, OSError, RenderError) as exc:
        record_failure(exc)
        message = 'Could not import this image. Check disk space, permissions and FFmpeg.' if isinstance(exc, OSError) else str(exc)
        return {'error': message}, 400


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

"""Frame edits produce new scene media; originals and scripts stay intact."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import uuid

from flask import Blueprint, current_app, request, send_file, url_for

from .. import frame_editor as frames
from ..config import Product, VIDEO_EXTS, read_yaml, write_yaml
from ..render import RenderError, probe_photos, validate_photos, validate_video
from ..storage import file_lock
from .diagnostics import record_failure

bp = Blueprint('frame_editor', __name__)


def _source(slug, name):
    from .app import _safe_product_dir
    folder = _safe_product_dir(current_app.config['FRAME_PRODUCTS'], slug)
    if folder is None:
        raise ValueError('Choose an existing product.')
    product = Product.load(folder)
    source = next((path for path in product.photos if path.name == name and path.suffix.lower() in VIDEO_EXTS), None)
    if source is None:
        raise ValueError('Choose an existing video clip in this product.')
    owner = next((member for member in product.collection_members if source.name in member['media'].values()), None)
    if product.collection_members and owner is None:
        raise ValueError('Choose a video belonging to a product in this collection.')
    return folder, source, owner['slug'] if owner else ''


@bp.get('/products/<slug>/scenes/frames')
def inspect(slug):
    try:
        _, source, _ = _source(slug, request.args.get('source_photo'))
        return frames.info(source)
    except (ValueError, OSError, RenderError) as exc:
        return {'error': str(exc)}, 400


@bp.get('/products/<slug>/scenes/frames/<int:index>')
def image(slug, index):
    try:
        _, source, _ = _source(slug, request.args.get('source_photo'))
        # read_frame returns no data for an index beyond the clip's last frame.
        if index > 1000000:
            raise ValueError('Choose a frame within this video.')
        return send_file(BytesIO(frames.read_frame(source, index)), mimetype='image/png', max_age=0)
    except (ValueError, OSError, RenderError) as exc:
        return {'error': str(exc)}, 400


@bp.post('/products/<slug>/scenes/frames')
def save(slug):
    try:
        name = request.form.get('source_photo', '')
        folder, source, member_slug = _source(slug, name)
        details = frames.info(source)
        if request.form.get('version') != details['version']:
            raise ValueError('The source video changed. Reopen the frame editor before saving.')
        try:
            indices = json.loads(request.form.get('frames', '[]'))
        except ValueError:
            raise ValueError('Choose valid frame numbers.') from None
        if (not isinstance(indices, list) or not 1 <= len(indices) <= frames.MAX_PATCHES
                or any(type(index) is not int or not 0 <= index < details['frame_count'] for index in indices)
                or len(set(indices)) != len(indices)):
            raise ValueError(f'Choose 1 to {frames.MAX_PATCHES} different frames within this video.')
        with tempfile.TemporaryDirectory(prefix='rf-frames-') as temporary:
            scratch = Path(temporary)
            patches = {}
            for index in indices:
                upload = request.files.get(f'frame_{index}')
                content = upload.stream.read(frames.MAX_IMAGE_BYTES + 1) if upload else b''
                if not content.startswith(b'\x89PNG\r\n\x1a\n') or len(content) > frames.MAX_IMAGE_BYTES:
                    raise ValueError('Each edited frame must be a PNG image smaller than 16 MB.')
                patch = scratch / f'{index}.png'
                patch.write_bytes(content)
                validate_photos([patch])
                if probe_photos([patch])[patch] != (details['width'], details['height']):
                    raise ValueError('Edited frame dimensions must match the original video.')
                patches[index] = patch
            destination = scratch / 'edited.mp4'
            frames.replace_frames(source, destination, patches)
            validate_video(destination)
            # Recheck the source and collection membership after encoding.
            with file_lock(folder / 'product.yaml'):
                _, current_source, current_member = _source(slug, name)
                stat = current_source.stat()
                if f'{stat.st_size}-{stat.st_mtime_ns}' != details['version'] or current_member != member_slug:
                    raise ValueError('The source video changed while saving. Reopen the frame editor.')
                output_name = 'frames-' + uuid.uuid4().hex + '.mp4'
                output = folder / 'photos' / output_name
                destination.replace(output)
                try:
                    if member_slug:
                        data = read_yaml(folder / 'product.yaml')
                        owner = next(member for member in data['collection_members'] if member['slug'] == member_slug)
                        owner['media'][output_name] = output_name
                        write_yaml(folder / 'product.yaml', data)
                except Exception:
                    output.unlink(missing_ok=True)
                    raise
        return dict(name=output_name, source_photo=name, member_slug=member_slug,
                    url=url_for('product_photo', slug=slug, filename=output_name)), 201
    except (ValueError, OSError, RenderError) as exc:
        record_failure(exc)
        message = 'Could not save the edited clip. Check disk space and file permissions.' if isinstance(exc, OSError) else str(exc)
        return {'error': message}, 400


def register(app, products_root):
    app.config['FRAME_PRODUCTS'] = Path(products_root)
    app.register_blueprint(bp)

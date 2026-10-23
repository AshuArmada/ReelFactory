"""Browser audit of local Gemini imports and mocked ElevenLabs; no credits spent.

Run with FFmpeg on PATH: python scripts/audit_ai_video.py
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler
from reelfactory.ai_video import VideoError
from reelfactory.config import write_yaml
from reelfactory.web.app import create_app


class QuietRequests(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix='rf_ai_video_audit_') as temporary:
        root = Path(temporary)
        photos = root / 'products/demo/photos'
        photos.mkdir(parents=True)
        write_yaml(root / 'brand.yaml', {'name': 'AI video demo'})
        write_yaml(photos.parent / 'product.yaml', {'name_en': 'Studio chair', 'name_hi': 'Chair',
                                                 'usp_en': ['Solid wood', 'Soft cushion']})
        for i, color in enumerate(('red', 'green', 'blue'), 1):
            Image.new('RGB', (360, 640), color).save(photos / f'{i}.jpg')
        other = root / 'products/other'
        (other / 'photos').mkdir(parents=True)
        write_yaml(other / 'product.yaml', {'name_en': 'Side table', 'name_hi': 'Table', 'usp_en': ['Sturdy legs']})
        shutil.copyfile(photos / '1.jpg', other / 'photos/1.jpg')
        clip = root / 'generated.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=s=180x320:r=15:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)],
                       check=True, capture_output=True)
        app = create_app(root / 'brand.yaml', root / 'products', root / 'out')
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequests)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        creations, builds = [], []

        def create(payload, key):
            creations.append(payload)
            if 'REJECT' in payload['prompt']:
                raise VideoError('ElevenLabs video returned HTTP 402. Check video credits.')
            return 'remote-' + str(len(creations))

        def build(prod, brand, lang, aspects, outroot, args, **kwargs):
            builds.append(kwargs)
            destination = outroot / prod.slug / 'demo.mp4'
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(clip, destination)
            return [destination]

        try:
            with patch('reelfactory.ai_video.credentials', lambda fingerprint=None: ('test-key', 'fingerprint')), \
                 patch('reelfactory.ai_video.create', create), \
                 patch('reelfactory.ai_video.status', lambda *a: {'status': 'completed', 'content_url': 'https://cdn.example/clip.mp4'}), \
                 patch('reelfactory.ai_video.download', lambda url, dest: shutil.copyfile(clip, dest)), \
                 patch('reelfactory.cli.build_one', build), sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base + '/products/demo/build')
                page.locator('[name="lang"][value="hi"]').uncheck()
                page.locator('.step-btn').nth(1).click()
                page.locator('#preview-btn').click()
                rows = page.locator('.segment-row')
                original_words = rows.first.locator('textarea').input_value()
                original_photo = rows.first.locator('.seg-photo-select').input_value()
                rows.first.locator('[data-edit-scene]').click()
                dialog = page.locator('#scene-editor')
                dialog.locator('[data-ai-tools] summary').click()
                expect(dialog.locator('[data-ai-enable]')).not_to_be_checked()
                expect(dialog.locator('[data-ai-controls]')).not_to_be_visible()
                assert not creations
                print('PASS: AI video is optional and ordinary editing makes no paid requests')

                dialog.locator('[data-ai-enable]').check()
                dialog.locator('[data-ai-provider]').select_option('elevenlabs')
                dialog.locator('[data-ai-generate]').click()
                expect(dialog.locator('[data-ai-status]')).to_contain_text('pending')
                expect(rows.first.locator('.seg-photo-select')).to_have_value(original_photo)
                dialog.locator('[data-ai-refresh]').click()
                expect(dialog.locator('[data-ai-result]')).to_be_visible()
                expect(dialog.locator('[data-ai-video]')).to_have_js_property('readyState', 4)
                name = dialog.locator('[data-ai-video]').get_attribute('src').split('/')[-1]
                expect(rows.first.locator('.seg-photo-select')).to_have_value(original_photo)
                assert creations[0]['generate_audio'] is False and creations[0]['start_frame']['mime_type'] == 'image/jpeg'
                print('PASS: generate, poll, validate and preview without changing the scene')

                dialog.locator('[data-ai-use]').click()
                expect(dialog.locator('[data-scene-preview] video')).to_be_visible()
                expect(rows.first.locator('.seg-photo-select')).to_have_value(original_photo)
                dialog.get_by_role('button', name='Cancel', exact=True).click()
                expect(rows.first.locator('.seg-photo-select')).to_have_value(original_photo)
                rows.first.locator('[data-edit-scene]').click()
                dialog.locator('[data-ai-enable]').check()
                expect(dialog.locator('[data-ai-jobs] option')).to_have_count(2)
                job_id = dialog.locator('[data-ai-jobs] option').nth(1).get_attribute('value')
                dialog.locator('[data-ai-jobs]').select_option(job_id)
                expect(dialog.locator('[data-ai-result]')).to_be_visible()
                dialog.locator('[data-ai-use]').click()
                dialog.locator('[data-ai-keep]').click()
                expect(dialog.locator('[data-scene-preview] img')).to_be_visible()
                dialog.locator('[data-ai-use]').click()
                dialog.locator('[data-scene-apply]').click()
                expect(rows.first.locator('.seg-photo-select')).to_have_value(name)
                expect(rows.first.locator('textarea')).to_have_value(original_words)
                expect(rows.nth(1).locator('.seg-photo-select')).to_have_value('2.jpg')
                assert len(creations) == 1
                print('PASS: Use, Keep current, Cancel and Apply preserve narration and other scenes')

                rows.first.locator('[data-edit-scene]').click()
                dialog.locator('[data-ai-enable]').check()
                dialog.locator('[data-ai-jobs]').select_option(job_id)
                dialog.locator('[data-ai-keep]').click()
                dialog.locator('[data-scene-apply]').click()
                expect(rows.first.locator('.seg-photo-select')).to_have_value(name)
                print('PASS: reopening a scene keeps its latest applied media as the original')

                page.locator('[name="tts"]').select_option('silent', force=True)
                page.locator('.step-btn').nth(2).click()
                page.locator('#build-btn').click()
                expect(page.locator('#done')).to_be_visible()
                assert builds[0]['photo_names'][0] == name
                assert len(creations) == 1
                print('PASS: explicitly chosen clip reaches rendering without another generation')

                page.goto(base + '/products/demo/build')
                page.locator('[name="lang"][value="hi"]').uncheck()
                page.locator('.step-btn').nth(1).click()
                page.locator('#preview-btn').click()
                rows = page.locator('.segment-row')
                assert name not in rows.locator('.seg-photo-select').evaluate_all('(picks) => picks.map(p => p.value)')
                rows.first.locator('[data-edit-scene]').click()
                dialog.locator('[data-ai-tools] summary').click()
                dialog.locator('[data-ai-enable]').check()
                dialog.locator('[data-ai-jobs]').select_option(job_id)
                expect(dialog.locator('[data-ai-result]')).to_be_visible()
                assert len(creations) == 1
                print('PASS: generation history survives reload; new scripts exclude unused AI clips')

                dialog.locator('[data-ai-provider]').select_option('elevenlabs')
                dialog.locator('[data-ai-prompt]').fill('REJECT this test request')
                dialog.locator('[data-ai-generate]').click()
                expect(dialog.locator('[data-ai-status]')).to_contain_text('402')
                expect(rows.first.locator('.seg-photo-select')).to_have_value('1.jpg')
                expect(dialog.locator('[data-scene-apply]')).to_be_enabled()
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                panel = dialog.locator('[data-ai-tools]')
                assert panel.evaluate('(el) => el.scrollWidth <= el.clientWidth')
                assert not errors, errors
                print('PASS: paid API errors preserve edits; mobile controls fit; no JavaScript errors')

                dialog.get_by_role('button', name='Cancel', exact=True).click()
                collection_url = page.evaluate('''async () => {
                  const response = await fetch('/collections/new', {
                    method: 'POST', headers: {'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content},
                    body: new URLSearchParams([['name', 'Studio range'], ['products', 'demo'], ['products', 'other']])
                  });
                  if (!response.ok) throw new Error('Collection creation failed');
                  return response.url;
                }''')
                page.goto(collection_url)
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.locator('.step-btn').nth(0).click()
                page.locator('[name="lang"][value="hi"]').uncheck()
                page.locator('.step-btn').nth(1).click()
                page.locator('#script-tab-en').click()
                rows = page.locator('.segment-rows[data-lang="en"] .segment-row')
                rows.nth(1).locator('[data-edit-scene]').click()
                expect(dialog.locator('[data-scene-member]')).to_have_value('demo')
                dialog.locator('[data-ai-tools] summary').click()
                dialog.locator('[data-ai-enable]').check()
                dialog.locator('[data-ai-provider]').select_option('elevenlabs')
                dialog.locator('[data-ai-generate]').click()
                expect(dialog.locator('[data-ai-status]')).to_contain_text('pending')
                collection_job = dialog.locator('[data-ai-jobs]').input_value()
                dialog.locator('[data-ai-refresh]').click()
                expect(dialog.locator('[data-ai-result]')).to_be_visible()
                collection_clip = dialog.locator('[data-ai-video]').get_attribute('src').split('/')[-1]
                dialog.locator('[data-ai-use]').click()
                dialog.locator('[data-scene-apply]').click()
                expect(rows.nth(1).locator('.seg-photo-select')).to_have_value(collection_clip)
                rows.nth(2).locator('[data-edit-scene]').click()
                expect(dialog.locator('[data-scene-member]')).to_have_value('other')
                dialog.locator('[data-ai-enable]').check()
                expect(dialog.locator('[data-ai-jobs]')).not_to_have_value(collection_job)
                expect(dialog.locator('[data-ai-jobs] option')).to_have_count(1)
                expect(dialog.locator('[data-name="' + collection_clip + '"]')).to_have_count(0)
                assert not errors, errors
                print('PASS: collection generation uses the selected product and never appears under another product')

                page.goto(base + '/products/other/build')
                page.locator('[name="lang"][value="hi"]').uncheck()
                page.locator('.step-btn').nth(1).click()
                page.locator('#preview-btn').click()
                rows = page.locator('.segment-row')
                words = rows.first.locator('textarea').input_value()
                original = rows.first.locator('.seg-photo-select').input_value()
                rows.first.locator('[data-edit-scene]').click()
                dialog.locator('[data-ai-tools] summary').click()
                dialog.locator('[data-ai-enable]').check()
                expect(dialog.locator('[data-ai-provider]')).to_have_value('gemini')
                expect(dialog.locator('[data-ai-generate]')).not_to_be_visible()
                with page.expect_download() as download_info:
                    dialog.locator('[data-ai-download-reference]').click()
                assert download_info.value.suggested_filename == 'gemini-reference.jpg'
                page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
                dialog.locator('[data-ai-copy-prompt]').click()
                expect(dialog.locator('[data-ai-status]')).to_contain_text('prompt copied')
                assert '9:16' in page.evaluate('navigator.clipboard.readText()')
                dialog.locator('[data-ai-duration]').select_option('4')
                paid_before = len(creations)
                with page.expect_file_chooser() as chooser:
                    dialog.locator('[data-ai-import]').click()
                chooser.value.set_files(photos / '1.jpg')
                expect(dialog.locator('[data-ai-result]')).to_be_visible(timeout=120000)
                expect(dialog.locator('[data-ai-status]')).to_contain_text('animated locally')
                expect(dialog.locator('[data-ai-video]')).to_have_js_property('readyState', 4)
                gemini_name = dialog.locator('[data-ai-video]').get_attribute('src').split('/')[-1]
                expect(rows.first.locator('.seg-photo-select')).to_have_value(original)
                dialog.locator('[data-ai-use]').click()
                expect(dialog.locator('[data-scene-preview] video')).to_be_visible()
                dialog.locator('[data-scene-apply]').click()
                expect(rows.first.locator('.seg-photo-select')).to_have_value(gemini_name)
                expect(rows.first.locator('textarea')).to_have_value(words)
                assert len(creations) == paid_before
                rows.first.locator('[data-edit-scene]').click()
                dialog.locator('[data-ai-enable]').check()
                expect(dialog.locator('[data-ai-jobs] option')).to_have_count(2)
                page.set_viewport_size({'width': 390, 'height': 844})
                panel = dialog.locator('[data-ai-tools]')
                assert panel.evaluate('(el) => el.scrollWidth <= el.clientWidth')
                assert not errors, errors
                print('PASS: Gemini default, reference download, clipboard prompt, real local animation, preview and Apply; no paid requests')
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            for handler in app.extensions['error_logger'].handlers:
                handler.close()


if __name__ == '__main__':
    main()

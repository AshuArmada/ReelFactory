"""Exercise exact-frame drawing and image replacement in a disposable project."""
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from reelfactory.config import write_yaml
from reelfactory.web.app import create_app
from audit_scene_editor import QuietRequests


def main():
    with tempfile.TemporaryDirectory(prefix='rf_frame_audit_') as temporary:
        root = Path(temporary)
        photos = root / 'products/demo/photos'
        photos.mkdir(parents=True)
        write_yaml(root / 'brand.yaml', {'name': 'Frame editor demo'})
        write_yaml(photos.parent / 'product.yaml', {'name_en': 'Studio chair', 'name_hi': 'Chair',
                                                   'usp_en': ['Solid wood', 'Soft cushion']})
        Image.new('RGB', (360, 640), 'green').save(photos / '1.jpg')
        replacement = root / 'replacement.png'
        Image.new('RGB', (360, 640), 'blue').save(replacement)
        video = photos / 'demo.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=lime:s=360x640:r=10:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)],
                       check=True, capture_output=True)
        app = create_app(root / 'brand.yaml', root / 'products', root / 'out')
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequests)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/products/demo/build')
                page.locator('[name="lang"][value="hi"]').uncheck()
                page.locator('.step-btn').nth(1).click()
                page.locator('#preview-btn').click()
                row = page.locator('.segment-row').first
                row.locator('.seg-photo-select').select_option('demo.mp4')
                words = row.locator('textarea').input_value()
                row.locator('[data-edit-scene]').click()
                scene = page.locator('#scene-editor')
                scene.locator('[data-scene-edit-frames]').click()
                editor = page.locator('#frame-editor')
                expect(editor.locator('[data-frame-position]')).to_contain_text('Frame 1 of 10')
                editor.locator('[data-frame-number]').fill('6')
                editor.locator('[data-frame-number]').press('Tab')
                expect(editor.locator('[data-frame-position]')).to_contain_text('Frame 6 of 10')
                canvas = editor.locator('canvas')
                box = canvas.bounding_box()
                x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2

                def draw():
                    page.mouse.move(x - 30, y)
                    page.mouse.down()
                    page.mouse.move(x + 30, y, steps=12)
                    page.mouse.up()

                def center():
                    return canvas.evaluate('(c) => Array.from(c.getContext("2d").getImageData(c.width/2,c.height/2,1,1).data)')

                draw()
                assert center()[0] > 200 and center()[1] < 100
                editor.locator('[data-frame-undo]').click()
                expect(editor.locator('[data-frame-status]')).to_contain_text('undone')
                expect(editor.locator('[data-frame-edits] button')).to_have_count(0)
                page.wait_for_function('''() => {
                  const c = document.querySelector('[data-frame-canvas]');
                  return c.getContext('2d').getImageData(c.width/2,c.height/2,1,1).data[1] > 200;
                }''')
                draw()
                editor.locator('[data-frame-tool]').select_option('erase')
                draw()
                assert center()[1] > 200
                editor.locator('[data-frame-undo]').click()
                expect(editor.locator('[data-frame-status]')).to_contain_text('undone')
                assert center()[0] > 200
                editor.locator('[data-frame-tool]').select_option('draw')
                editor.locator('[data-frame-next]').click()
                expect(editor.locator('[data-frame-position]')).to_contain_text('Frame 7 of 10')
                editor.locator('[data-frame-prev]').click()
                expect(editor.locator('[data-frame-position]')).to_contain_text('Frame 6 of 10')
                assert center()[0] > 200
                editor.locator('[data-frame-reset]').click()
                assert center()[1] > 200
                draw()
                editor.locator('[data-frame-number]').fill('10')
                editor.locator('[data-frame-number]').press('Tab')
                expect(editor.locator('[data-frame-position]')).to_contain_text('Frame 10 of 10')
                with page.expect_file_chooser() as chooser:
                    editor.locator('[data-frame-replace]').click()
                chooser.value.set_files(replacement)
                expect(editor.locator('[data-frame-edits] button')).to_have_count(2)
                assert center()[2] > 200 and center()[1] < 100
                editor.locator('[data-frame-edits] button').first.click()
                expect(editor.locator('[data-frame-position]')).to_contain_text('Frame 6 of 10')
                page.screenshot(path=str(ROOT / 'docs/screenshots/11-frame-editor.png'), full_page=True)
                editor.locator('[data-frame-save]').click()
                expect(editor).not_to_be_visible(timeout=120000)
                expect(scene.locator('[data-scene-status]')).to_contain_text('Edited clip saved')
                saved_url = scene.locator('[data-scene-preview] video').get_attribute('src')
                assert '/frames-' in saved_url
                expect(row.locator('.seg-photo-select')).to_have_value('demo.mp4')
                scene.locator('[data-scene-apply]').click()
                expect(row.locator('.seg-photo-select')).to_have_value(saved_url.split('/')[-1])
                expect(row.locator('textarea')).to_have_value(words)
                assert video.exists()
                print('PASS: exact-frame navigation, brush, undo, reset, replacement image, saved copy and Apply')
                # Check the pixels in the actual saved MP4, rather than just the canvas.
                result = subprocess.run(['ffmpeg', '-v', 'error', '-i',
                    str(photos / saved_url.split('/')[-1]), '-vf', 'crop=2:2:179:319,scale=1:1',
                    '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], check=True, capture_output=True).stdout
                pixels = [tuple(result[i:i+3]) for i in range(0, len(result), 3)]
                assert len(pixels) == 10 and pixels[5][0] > 180 and pixels[9][2] > 180, pixels
                assert all(pixels[i][1] > 180 for i in [0, 1, 2, 3, 4, 6, 7, 8]), pixels
                row.locator('[data-edit-scene]').click()
                scene.locator('[data-scene-edit-frames]').click()
                expect(editor.locator('[data-frame-position]')).to_contain_text('Frame 1 of 10')
                page.set_viewport_size({'width': 390, 'height': 844})
                assert editor.evaluate('(e) => e.scrollWidth <= e.clientWidth')
                assert not errors, errors
                print('PASS: only selected frames changed in decoded MP4; mobile layout fits; no JavaScript errors')
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            for handler in app.extensions['error_logger'].handlers:
                handler.close()


if __name__ == '__main__':
    main()

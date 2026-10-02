"""Watch a real rendered preview and edit it in a disposable browser project.

Run: python scripts/audit_reel_preview.py (requires FFmpeg and Playwright).
"""
from pathlib import Path
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
    with tempfile.TemporaryDirectory(prefix="rf_preview_audit_") as tmp:
        root = Path(tmp)
        photos = root / "products/demo/photos"
        photos.mkdir(parents=True)
        write_yaml(root / "brand.yaml", {"name": "Studio furniture", "watermark": False})
        write_yaml(photos.parent / "product.yaml", {
            "name_en": "Studio chair", "name_hi": "Chair", "usp_en": ["Solid wood", "Soft cushion"]})
        for i, color in enumerate(("#ba523f", "#307c65", "#3b6298"), 1):
            Image.new("RGB", (720, 1280), color).save(photos / f"{i}.jpg")
        app = create_app(root / "brand.yaml", root / "products", root / "out")
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietRequests)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/products/demo/build")
                page.locator('[name="lang"][value="hi"]').uncheck()
                page.locator('.step-btn').nth(1).click()
                page.locator('#preview-btn').click()
                rows = page.locator('.segment-row')
                while rows.count() > 2:
                    rows.last.locator('[data-remove-row]').click()
                for i, words in enumerate(('Meet your new chair.', 'Solid wood. Soft cushion.')):
                    rows.nth(i).locator('textarea').fill(words)
                    rows.nth(i).locator('[name^="seg_overlay_"]').fill(words)
                preview = page.locator('[data-reel-preview]')
                preview.locator('[data-preview-settings]').click()
                page.locator('[name="tts"]').select_option('silent')
                page.locator('summary').filter(has_text='Video appearance').click()
                page.locator('[name="template"]').select_option('classic')
                page.locator('[name="no_music"]').check()
                page.locator('.step-btn').nth(1).click()
                video = preview.locator('video')
                status = preview.locator('[data-preview-status]')
                preview.locator('[data-render-preview]').click()
                expect(status).to_contain_text('Preview ready', timeout=90000)
                page.wait_for_function('document.querySelector("[data-reel-video]").readyState >= 2')
                assert video.evaluate('(v) => v.videoWidth') == 360
                video.evaluate('(v) => v.play()')
                page.wait_for_function('document.querySelector("[data-reel-video]").currentTime > 0.25')
                preview.locator('[data-preview-seek] button').nth(1).click()
                expect(preview.locator('[data-playing-scene]')).to_have_text('Scene 2 of 2')
                old_url = video.get_attribute('src')
                preview.locator('[data-edit-playing-scene]').click()
                dialog = page.locator('#scene-editor')
                expect(dialog.locator('h2')).to_have_text('Edit scene 2')
                dialog.locator('[data-name="3.jpg"]').click()
                dialog.locator('[data-scene-narration]').fill('A comfortable place to relax.')
                dialog.locator('[data-scene-caption]').fill('Made for relaxing')
                dialog.locator('[data-scene-apply]').click()
                expect(rows.nth(1).locator('textarea')).to_have_value('A comfortable place to relax.')
                expect(rows.nth(1).locator('.seg-photo-select')).to_have_value('3.jpg')
                expect(status).to_contain_text('You have changes')
                expect(preview.locator('[data-edit-playing-scene]')).to_be_disabled()
                assert video.get_attribute('src') == old_url
                print('PASS: real video playback, scene seeking, and picture/narration/caption edits')

                preview.locator('[data-render-preview]').click()
                expect(status).to_contain_text('Preview ready', timeout=90000)
                assert video.get_attribute('src') != old_url
                page.wait_for_function('document.querySelector("[data-reel-video]").readyState >= 2')
                preview.locator('[data-preview-seek] button').nth(1).click()
                page.wait_for_function('document.querySelector("[data-reel-video]").seeking === false')
                playhead = video.evaluate('(v) => v.currentTime')
                video.evaluate('(v) => v.play()')
                page.wait_for_function('(start) => document.querySelector("[data-reel-video]").currentTime > start + 0.2', arg=playhead)
                video.evaluate('(v) => v.pause()')
                expect(preview.locator('[data-edit-playing-scene]')).to_be_enabled()
                artifacts = ROOT / "out/audit/reel-preview"
                artifacts.mkdir(parents=True, exist_ok=True)
                preview.evaluate('(el) => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 80)')
                page.screenshot(path=str(artifacts / 'desktop.png'))
                page.set_viewport_size({"width": 390, "height": 844})
                preview.scroll_into_view_if_needed()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.screenshot(path=str(artifacts / 'mobile.png'))
                print('PASS: refresh renders edits, restores scene editing, and fits mobile')

                # A failed refresh must keep the draft and last playable version.
                old_url = video.get_attribute('src')
                page.route('**/products/demo/preview', lambda route: route.fulfill(
                    status=400, content_type='application/json', body='{"error":"Voice service unavailable"}'))
                preview.locator('[data-render-preview]').click()
                expect(status).to_have_text('Voice service unavailable')
                expect(preview.locator('[data-render-preview]')).to_be_enabled()
                assert video.get_attribute('src') == old_url
                expect(rows.nth(1).locator('textarea')).to_have_value('A comfortable place to relax.')
                assert not errors, errors
                print('PASS: refresh errors preserve video and draft; no browser errors')
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            for handler in app.extensions['error_logger'].handlers:
                handler.close()


if __name__ == '__main__':
    main()

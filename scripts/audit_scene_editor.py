"""Exercise the visual scene editor on disposable data with Playwright.

Run: python scripts/audit_scene_editor.py
"""
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler
from reelfactory.config import write_yaml
from reelfactory.web.app import create_app


class QuietRequests(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix="rf_scene_audit_") as tmp:
        root = Path(tmp)
        photos = root / "products/demo/photos"
        photos.mkdir(parents=True)
        write_yaml(root / "brand.yaml", {"name": "Scene demo"})
        write_yaml(photos.parent / "product.yaml", {
            "name_en": "Studio chair", "name_hi": "Chair", "usp_en": ["Solid wood", "Soft cushion"]})
        for i, color in enumerate(("red", "green", "blue"), 1):
            Image.new("RGB", (720, 1280), color).save(photos / f"{i}.jpg")
        new_photo = root / "new.jpg"
        Image.new("RGB", (720, 1280), "yellow").save(new_photo)
        app = create_app(root / "brand.yaml", root / "products", root / "out")
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietRequests)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        builds = []

        def fake_build(prod, brand, lang, aspects, outroot, args, **kwargs):
            builds.append(kwargs)
            video = outroot / prod.slug / "demo.mp4"
            video.parent.mkdir(parents=True, exist_ok=True)
            video.write_bytes(b"fake video")
            return [video]

        try:
            with patch("reelfactory.cli.build_one", fake_build), sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/products/demo/build")
                page.locator('[name="lang"][value="hi"]').uncheck()
                page.locator('.step-btn').nth(1).click()
                page.locator('#preview-btn').click()
                rows = page.locator('.segment-row')
                original = rows.first.locator('textarea').input_value()
                expect(page.locator('.scene-tile')).to_have_count(rows.count())
                page.locator('.scene-tile').first.click()
                dialog = page.locator('#scene-editor')
                expect(dialog).to_be_visible()
                dialog.locator('[data-name="3.jpg"]').click()
                dialog.get_by_role('button', name='Cancel', exact=True).click()
                expect(rows.first.locator('select.seg-photo-select')).to_have_value('1.jpg')
                rows.first.locator('[data-edit-scene]').click()
                dialog.locator('[data-name="3.jpg"]').click()
                dialog.locator('[data-scene-apply]').click()
                expect(rows.first.locator('select.seg-photo-select')).to_have_value('3.jpg')
                expect(rows.nth(1).locator('select.seg-photo-select')).to_have_value('2.jpg')
                expect(rows.first.locator('textarea')).to_have_value(original)
                print('PASS: visual selection, apply, cancel and scene isolation')

                rows.first.locator('[data-edit-scene]').click()
                dialog.locator('[data-scene-file]').set_input_files(str(new_photo))
                expect(dialog.locator('[data-scene-status]')).to_contain_text('Uploaded to your library')
                uploaded = dialog.locator('[aria-pressed="true"]').get_attribute('data-name')
                dialog.locator('[data-scene-apply]').click()
                expect(rows.first.locator('select.seg-photo-select')).to_have_value(uploaded)
                page.locator('[data-add-row]').click()
                assert uploaded in rows.last.locator('select.seg-photo-select option').evaluate_all('(options) => options.map(o => o.value)')
                rows.last.locator('[data-remove-row]').click()
                rows.first.locator('[data-move-scene="down"]').click()
                expect(rows.nth(1).locator('select.seg-photo-select')).to_have_value(uploaded)
                expect(rows.nth(1).locator('textarea')).to_have_value(original)
                print('PASS: upload, new scenes and reorder preserve picture/word pairing')

                rows.nth(1).locator('[data-edit-scene]').click()
                dialog.locator('[data-scene-file]').set_input_files({"name": "bad.txt", "mimeType": "text/plain", "buffer": b"bad"})
                expect(dialog.locator('[data-scene-status]')).to_contain_text('Unsupported')
                expect(dialog.locator('[data-scene-apply]')).to_be_enabled()
                page.keyboard.press('Escape')
                expect(dialog).not_to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                rows.nth(1).locator('[data-edit-scene]').click()
                expect(dialog).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                artifacts = ROOT / "out/audit/scene-editor"
                artifacts.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(artifacts / "mobile.png"))
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.screenshot(path=str(artifacts / "desktop.png"))
                dialog.get_by_role('button', name='Cancel', exact=True).click()
                print('PASS: upload error recovery, Escape and mobile layout')

                page.locator('[name="tts"]').select_option('silent', force=True)
                page.locator('.step-btn').nth(2).click()
                page.locator('#build-btn').click()
                expect(page.locator('#done')).to_be_visible()
                assert builds[0]['photo_names'][1] == uploaded
                assert builds[0]['segments'][1].vo == original
                page.locator('[data-edit-scenes]').click()
                expect(rows.nth(1).locator('[data-edit-scene]')).to_be_visible()
                expect(rows.nth(1).locator('select.seg-photo-select')).to_have_value(uploaded)
                assert not errors, errors
                print('PASS: chosen media reaches build; finished reel returns to scene editor')
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            for handler in app.extensions["error_logger"].handlers:
                handler.close()


if __name__ == "__main__":
    main()

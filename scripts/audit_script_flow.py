"""Exercise script editing and navigation in Chromium using disposable data.

Run: python scripts/audit_script_flow.py
No cloud calls, publishing, or user product changes. Video rendering is stubbed.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server, WSGIRequestHandler

from reelfactory.config import read_yaml, write_yaml
from reelfactory.web.app import create_app


class QuietRequests(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def main():
    artifacts = ROOT / "out/audit/script-flow"
    artifacts.mkdir(parents=True, exist_ok=True)
    results, builds, errors = [], [], []

    def passed(name):
        results.append(name)
        print("PASS:", name, flush=True)

    def render(prod, brand, lang, aspects, outroot, args, **kwargs):
        builds.append({"lang": lang, **kwargs})
        output = outroot / prod.slug / f"draft-{len(builds)}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"stub video")
        return [output]

    with tempfile.TemporaryDirectory(prefix="rf_script_flow_") as temporary:
        root = Path(temporary)
        photos = root / "products/demo/photos"
        photos.mkdir(parents=True)
        write_yaml(root / "brand.yaml", {"name": "Flow review"})
        write_yaml(photos.parent / "product.yaml", {
            "name_en": "Studio chair", "name_hi": "Chair",
            "usp_en": ["Solid wood", "Soft cushion"], "usp_hi": ["Solid wood"]})
        for index, color in enumerate(("red", "green", "blue"), 1):
            Image.new("RGB", (720, 1280), color).save(photos / f"{index}.jpg")
        app = create_app(root / "brand.yaml", root / "products", root / "out")
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietRequests)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch("reelfactory.cli.build_one", render), sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(f"http://127.0.0.1:{server.server_port}/products/demo/build")
                page.locator('.step-btn').nth(2).click()
                expect(page.locator('#build-btn')).to_be_disabled()
                expect(page.locator('#queue-btn')).to_be_disabled()
                page.locator('[data-edit-script]').click()
                expect(page.locator('#preview-btn')).to_be_visible()
                passed("Empty drafts cannot bypass script review")

                page.locator('.step-btn').nth(0).click()
                page.locator('[name="lang"][value="hi"]').uncheck()
                page.get_by_role('button', name='Next: Script').click()
                page.locator('#preview-btn').click()
                narration = page.locator('[name="seg_vo_en"]').first
                expect(narration).to_be_visible()
                # The editor precedes the optional rendered preview.
                assert narration.bounding_box()['y'] < page.locator('[data-reel-preview]').bounding_box()['y']
                narration.fill('My edited opening')
                page.locator('.seg-photo-select').first.select_option('3.jpg')
                page.locator('[data-open-script-tool="save-script-panel"]').click()
                page.locator('[name="save_name"]').fill('Edited version')
                page.locator('#save-btn').click()
                expect(page.locator('[name="seg_vo_en"]').first).to_have_value('My edited opening')
                saved = read_yaml(photos.parent / 'saved_scripts.yaml')
                assert saved['en'][0]['segments'][0]['photo'] == '3.jpg'
                passed("Visible editor and save action preserve exact words and media")

                page.locator('.step-btn').nth(0).click()
                page.locator('[name="lang"][value="hi"]').check()
                page.locator('.step-btn').nth(1).click()
                expect(page.get_by_role('button', name='Next: Review')).to_be_disabled()
                expect(page.locator('[data-draft-status]')).to_contain_text('Hindi')
                page.locator('#missing-script-btn').click()
                page.get_by_role('tab', name='English', exact=True).click()
                expect(page.locator('[name="seg_vo_en"]').first).to_have_value('My edited opening')
                expect(page.get_by_role('button', name='Next: Review')).to_be_enabled()
                passed("Adding a language writes only its missing draft")

                page.get_by_role('button', name='Next: Review').click()
                page.locator('[data-edit-script]').click()
                page.get_by_role('tab', name='English', exact=True).click()
                row = page.locator('#script-panel-en .segment-row').first
                row.locator('[data-edit-scene]').click()
                page.locator('[data-scene-narration]').fill('Scene dialog edit')
                page.locator('[data-scene-caption]').fill('Edited title')
                page.locator('[data-scene-apply]').click()
                expect(row.locator('textarea')).to_have_value('Scene dialog edit')
                passed("Review returns to the live editor and scene dialog edits apply")

                page.locator('[data-open-script-tool="rewrite-script-panel"]').click()
                page.locator('#variants-btn').click()
                expect(page.get_by_role('button', name='Next: Review')).to_be_disabled()
                page.locator('[name="pick_hi"][value="1"]').check()
                page.locator('#pick-btn').click()
                version = page.locator('[name="ver1_seg_vo_hi"]').first
                expect(version).to_be_visible()
                version.fill('Edited comparison opening')
                card = version.locator('xpath=ancestor::div[contains(@class,"card")]')
                card.locator('[data-edit-scene]').first.click()
                page.locator('[data-scene-caption]').fill('Version title')
                page.locator('[data-name="2.jpg"]').click()
                page.locator('[data-scene-apply]').click()
                card.locator('[data-add-row]').click()
                card.locator('textarea').last.fill('Extra version scene')
                page.get_by_role('button', name='Change selected versions').click()
                expect(page.locator('#version-picker')).to_contain_text('Edited comparison opening')
                expect(page.locator('#version-picker')).to_contain_text('Extra version scene')
                page.locator('#pick-btn').click()
                passed("Selected versions remain editable and reselection retains edits")

                page.locator('[data-open-script-tool="save-script-panel"]').click()
                page.locator('[name="save_name"]').fill('Compared openings')
                page.locator('#save-btn').click()
                saved = read_yaml(photos.parent / 'saved_scripts.yaml')
                assert saved['hi'][1]['segments'][0]['vo'] == 'Edited comparison opening'
                assert saved['hi'][1]['segments'][0]['photo'] == '2.jpg'
                expect(page.locator('[name="ver1_seg_vo_hi"]').first).to_have_value('Edited comparison opening')
                passed("Every selected version saves independently without leaving the editor")

                page.set_viewport_size({"width": 390, "height": 844})
                expect(page.locator('[name="ver1_seg_vo_hi"]').first).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                page.screenshot(path=str(artifacts / 'script-mobile.png'))
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.screenshot(path=str(artifacts / 'script-desktop.png'))
                passed("Desktop and mobile editing have no horizontal overflow")

                page.get_by_role('button', name='Next: Review').click()
                page.locator('[name="tts"]').select_option('silent')
                page.locator('#build-btn').click()
                expect(page.locator('#done')).to_be_visible()
                edited = next(build for build in builds if build['variant_tag'] == '_v2')
                assert edited['segments'][0].vo == 'Edited comparison opening'
                assert edited['segments'][0].overlay == 'Version title'
                assert edited['photo_names'][0] == '2.jpg'
                assert edited['segments'][-1].vo == 'Extra version scene'
                page.locator('[data-edit-scenes]').click()
                expect(page.locator('[name="ver1_seg_vo_hi"]').first).to_be_visible()
                assert not errors, errors
                passed("Build receives each reviewed version exactly; no JavaScript errors")
                browser.close()
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
            for handler in app.extensions['error_logger'].handlers:
                handler.close()
        (artifacts / 'results.json').write_text(json.dumps({"passed": results, "javascript_errors": errors}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()

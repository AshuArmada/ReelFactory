"""Browser workflow audit on disposable data. Requires Playwright + Pillow.

Run: python scripts/audit_browser.py
No user products are changed and no cloud calls are made by this script.
"""
from __future__ import annotations

import json
from pathlib import Path
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
from reelfactory import ai_script, cli
from reelfactory.config import read_yaml, write_yaml
from reelfactory.gemini import GeminiError
from reelfactory.render import RenderError
from reelfactory.web.app import create_app

ARTIFACTS = ROOT / "out" / "audit" / "browser"


class QuietRequests(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    results = []

    def passed(name):
        results.append({"feature": name, "status": "PASS"})
        (ARTIFACTS / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print("PASS", name, flush=True)

    with tempfile.TemporaryDirectory(prefix="rf_browser_audit_") as tmp:
        root = Path(tmp)
        write_yaml(root / "brand.yaml", {"name": "Audit Demo"})
        media = root / "uploads"
        media.mkdir()
        for i, color in enumerate(("red", "green", "blue"), 1):
            Image.new("RGB", (1440, 2560), color).save(media / f"{i}.jpg")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=yellow:s=360x640:d=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media / "4.mp4")], check=True)
        app = create_app(root / "brand.yaml", root / "products", root / "out")
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietRequests)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        page = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000},
                                              permissions=["clipboard-read", "clipboard-write"])
                page = context.new_page()
                page.set_default_timeout(12000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())

                page.goto(base + "/")
                page.get_by_role("link", name="New product", exact=True).click()
                page.get_by_role("button", name="Next: Photos").click()
                expect(page.locator('[name="slug"]')).to_be_visible()
                page.locator('[name="slug"]').fill("demo")
                page.locator('[name="name_en"]').fill("Blue chair")
                page.locator('[name="name_hi"]').fill("नीली कुर्सी")
                page.locator('[name="usp_en"]').fill("Blue finish\nEasy to place")
                page.locator('[name="usp_hi"]').fill("नीला रंग\nरखने में आसान")
                page.get_by_role("button", name="Next: Photos").click()
                page.locator('[name="photos"]').set_input_files([str(p) for p in sorted(media.iterdir())])
                page.get_by_role("button", name="Next: Details").click()
                page.get_by_role("button", name="Create product").click()
                page.wait_for_url("**/products/demo/edit")
                passed("Create product: validation, names, selling points, image and clip uploads")

                page.locator('.step-btn').nth(1).click()
                first = page.locator('[data-photo-tile]').first
                first.locator('[data-move="down"]').click()
                page.locator('section.step:visible').get_by_role("button", name="Save", exact=True).click()
                page.wait_for_url("**/edit?step=photos&notice=**")
                expect(page.locator('#photo-grid')).to_be_visible()
                assert read_yaml(root / "products/demo/product.yaml")["photo_order"][0] == "2.jpg"
                passed("Photo order saves and returns to Photos")

                page.locator('.step-btn').nth(0).click()
                page.locator('[name="price"]').fill("999")
                page.locator('.step-btn').nth(1).click()
                analysis_summary = page.locator('summary:has-text("Photo understanding")')
                if analysis_summary.locator('..').get_attribute('open') is None:
                    analysis_summary.click()
                page.get_by_role("button", name="Analyze photos", exact=True).click()
                expect(page.locator('.analysis-guard')).to_be_visible()
                passed("Photo analysis protects unsaved product edits")

                page.goto(base + "/brand")
                page.get_by_role("tab", name="Voice", exact=True).click()
                page.get_by_role("button", name="Save settings").click()
                page.wait_for_url("**/brand?tab=2&saved=1")
                expect(page.get_by_role("tab", name="Voice", exact=True)).to_have_attribute("aria-selected", "true")
                passed("Brand settings save without leaving selected tab")

                page.goto(base + "/products/demo/build")
                page.get_by_role("button", name="Next: Script").click()
                page.locator('#preview-btn').click()
                expect(page.locator('#script-panel-hi')).to_be_visible()
                expect(page.locator('#script-panel-en')).to_be_hidden()
                page.get_by_role("tab", name="Hindi", exact=True).press("ArrowRight")
                expect(page.locator('#script-panel-en')).to_be_visible()
                passed("Generate both languages and switch tabs by keyboard")

                row = page.locator('#script-panel-en .segment-row').first
                original = row.locator('textarea').input_value()
                row.locator('.seg-photo-select').select_option("4.mp4")
                expect(row.locator('video.seg-thumb')).to_be_visible()
                row.locator('.seg-photo-select').select_option("3.jpg")
                expect(row.locator('img.seg-thumb')).to_be_visible()
                row.locator('[data-move-scene="down"]').click()
                assert page.locator('#script-panel-en .segment-row').nth(1).locator('textarea').input_value() == original
                page.locator('[data-add-row="en"]').click()
                page.locator('#script-panel-en .segment-row').last.locator('textarea').fill("Extra scene")
                page.locator('#script-panel-en .segment-row').last.locator('[data-remove-row]').click()
                passed("Scene add/remove/reorder and image-to-clip preview switching")
                page.evaluate('scrollTo(0, 0)')
                page.screenshot(path=str(ARTIFACTS / "script-desktop.png"), full_page=True)

                page.locator('#save-script-panel summary').click()
                page.locator('[name="save_name"]').fill("My version")
                page.locator('#save-btn').click()
                expect(page.locator('#script-panel-en')).to_be_visible()
                assert read_yaml(root / 'products/demo/saved_scripts.yaml')['en'][0]['name'] == 'My version'
                passed("Save exact words and images; preserve active language")

                page.locator('#rewrite-script-panel summary').click()
                page.locator('[name="steer"]').fill("Keep the opening, shorten the rest")
                page.locator('.step-btn').nth(0).click()
                page.locator('[name="script"][value="ai"]').check()
                page.locator('.step-btn').nth(1).click()
                with patch.object(ai_script, 'build', side_effect=GeminiError('Audit: service temporarily unavailable')):
                    page.locator('#rewrite-btn').click()
                    expect(page.get_by_role('alert')).to_contain_text('Audit: service temporarily unavailable')
                expect(page.locator('#script-panel-en')).to_be_visible()
                assert page.locator('[name="steer"]').input_value() == "Keep the opening, shorten the rest"
                passed("Rewrite error preserves draft, instructions, images and language")

                page.locator('[name="steer"]').fill("")
                page.locator('.step-btn').nth(0).click()
                page.locator('[name="script"][value="template"]').check()
                page.locator('.step-btn').nth(1).click()
                page.locator('#variants-btn').click()
                page.locator('[name^="pick_"]').evaluate_all('(els)=>els.forEach(el=>el.checked=false)')
                page.locator('#pick-btn').click()
                expect(page.locator('#version-picker')).to_be_visible()
                expect(page.get_by_role('alert')).to_contain_text('Choose at least one version')
                page.locator('[name="pick_hi"][value="0"]').check()
                page.locator('[name="pick_hi"][value="1"]').check()
                page.locator('[name="pick_en"][value="0"]').check()
                page.locator('#pick-btn').click()
                expect(page.locator('#multi-summary')).to_be_visible()
                page.locator('.step-btn').nth(2).click()
                with patch.object(cli, 'build_one', side_effect=RenderError('Audit: render failed')):
                    page.locator('#build-btn').click()
                    expect(page.get_by_role('alert')).to_contain_text('Audit: render failed')
                expect(page.locator('#multi-summary')).to_be_visible()
                passed("Compare selection validation and multi-build failure recovery")

                # A real short video through the browser, using a saved two-scene script.
                app.test_client().post('/products/demo/script/save', data={
                    'lang': 'en', 'save_name': 'Short render', 'seg_vo_en': ['See the chair.', 'Ask for a demo.'],
                    'seg_photo_en': ['1.jpg', '4.mp4'], 'seg_role_en': ['hook', 'custom'],
                })
                page.goto(base + '/products/demo/build')
                page.locator('.step-btn').nth(1).click()
                page.locator('summary:has-text("Saved scripts for")').click()
                page.locator('[name="load_pick"][value="en:1"]').click()
                page.locator('.step-btn').nth(2).click()
                page.locator('[name="tts"]').select_option('gemini')
                expect(page.locator('[name="voice_delivery"]')).to_be_visible()
                expect(page.locator('[name="voice_rate"]')).to_be_hidden()
                page.locator('[name="tts"]').select_option('edge')
                expect(page.locator('[name="voice_rate"]')).to_be_visible()
                expect(page.locator('[name="voice_delivery"]')).to_be_hidden()
                page.locator('[name="tts"]').select_option('silent')
                page.locator('summary:has-text("Video appearance")').click()
                page.locator('[name="preset"]').select_option('ultrafast')
                page.locator('[name="no_music"]').check()
                page.screenshot(path=str(ARTIFACTS / 'review-desktop.png'), full_page=True)
                page.locator('#build-btn').click()
                page.wait_for_function("document.querySelector('#done video') || document.querySelector('[role=alert]')", timeout=90000)
                (ARTIFACTS / 'build-result.html').write_text(page.content(), encoding='utf-8')
                if page.get_by_role('alert').count():
                    raise AssertionError(page.get_by_role('alert').inner_text())
                video = page.locator('#done video').first
                page.wait_for_function('document.querySelector("#done video").readyState >= 1')
                assert video.evaluate('(v)=>v.videoWidth') == 1080
                passed("Provider-specific voice controls and real browser video build/playback")

                page.locator('[data-copy-target]').first.click()
                expect(page.locator('[data-copy-target]').first).to_contain_text('Copied')
                clipboard = page.evaluate('navigator.clipboard.readText()').replace('\r\n', '\n')
                expected_caption = page.locator('#caption-0').text_content().replace('\r\n', '\n')
                assert clipboard == expected_caption, (repr(clipboard), repr(expected_caption))
                with page.expect_download() as download_info:
                    page.locator('#done a[download]').first.click()
                download = download_info.value
                assert Path(download.path()).stat().st_size > 1000
                passed("Copy caption to clipboard and download finished video")

                page.locator('.output-library > summary').click()
                expect(page.locator('.output-check')).not_to_have_count(0)
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator('.step-btn').nth(1).click()
                page.evaluate('scrollTo(0, 0)')
                page.screenshot(path=str(ARTIFACTS / 'script-mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                assert page.locator('.step-name').nth(1).is_visible()
                passed("Mobile layout has visible step names and no horizontal overflow")
                page.locator('.script-management').first.locator('summary').click()
                page.get_by_role('button', name='Clear current draft', exact=True).click()
                expect(page.locator('#preview-btn')).to_be_visible()
                page.locator('summary:has-text("Saved scripts for")').click()
                page.locator('summary:has-text("Manage saved scripts")').click()
                page.get_by_role('button', name='Clear all saved scripts', exact=True).click()
                expect(page.locator('#preview-btn')).to_be_visible()
                assert read_yaml(root / 'products/demo/saved_scripts.yaml') == {}
                passed("Clear draft/library stays on Script and respects separate storage")

                page.locator('.output-library > summary').click()
                output = page.locator('.output-check').first
                deleted_name = output.input_value()
                output.check()
                page.locator('#delete-btn').click()
                expect(page.locator('#outputs')).to_contain_text('Deleted ' + deleted_name)
                assert not (root / 'out/demo' / deleted_name).exists()
                passed("Select and delete a finished file with confirmation")

                nojs = browser.new_context(java_script_enabled=False)
                plain = nojs.new_page()
                plain.goto(base + '/products/demo/build')
                for step in plain.locator('section.step').all():
                    assert step.is_visible()
                plain.locator('#preview-btn').click()
                expect(plain.locator('#script-panel-hi')).to_be_visible()
                expect(plain.locator('#script-panel-en')).to_be_visible()
                passed("JavaScript-disabled script generation and editing fallback")
                nojs.close()
                assert not errors, errors
                passed("No uncaught browser JavaScript errors")
                browser.close()
        except Exception as exc:
            results.append({"feature": "Browser walkthrough", "status": "FAIL", "detail": str(exc)})
            (ARTIFACTS / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            if page and not page.is_closed():
                try:
                    page.screenshot(path=str(ARTIFACTS / 'failure.png'), full_page=True)
                except Exception:
                    pass
            raise
        finally:
            server.shutdown()
            for handler in app.extensions['error_logger'].handlers:
                handler.close()
            log = root / 'logs/reelfactory.log'
            if log.exists():
                (ARTIFACTS / 'application.log').write_bytes(log.read_bytes())


if __name__ == '__main__':
    main()

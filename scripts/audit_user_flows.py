"""Check draft recovery and product validation in Chromium on disposable data.

Run: python scripts/audit_user_flows.py
Requires Playwright and Pillow. Rendering is mocked; no provider calls are made.
"""
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

from reelfactory.config import write_yaml
from reelfactory.render import RenderError
from reelfactory.web.app import create_app


class QuietRequests(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix="rf_user_flows_") as temporary:
        root = Path(temporary)
        product = root / "products" / "chair"
        (product / "photos").mkdir(parents=True)
        Image.new("RGB", (720, 1280), "blue").save(product / "photos" / "1.jpg")
        write_yaml(product / "product.yaml", {"name_en": "Chair", "name_hi": "Kursi",
                                             "usp_en": ["Blue finish"], "usp_hi": ["Blue finish"]})
        write_yaml(root / "brand.yaml", {"name": "Demo"})
        app = create_app(root / "brand.yaml", root / "products", root / "out")
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietRequests)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        fail = False
        built = []

        def build(*args, **kwargs):
            built.append(args[2])
            if fail:
                raise RenderError("Audit render failed")
            return []

        try:
            with patch("reelfactory.cli.build_one", build), sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/products/chair/build")
                page.locator(".step-btn").nth(1).click()
                page.locator("#preview-btn").click()
                expect(page.locator("#script-panel-hi")).to_be_visible()
                hindi = page.locator('[name="seg_vo_hi"]').first
                hindi.fill("Keep this unchecked draft")
                page.get_by_role("tab", name="English", exact=True).click()
                page.locator('[name="seg_vo_en"]').first.fill("Keep this English draft")
                page.locator('[data-add-row="en"]').click()
                page.locator('[name="seg_overlay_en"]').last.fill("Unfinished scene")
                page.locator(".step-btn").nth(0).click()
                page.locator('[name="lang"][value="hi"]').uncheck()
                page.locator(".step-btn").nth(2).click()
                page.locator('[name="tts"]').select_option("silent")
                page.locator("#build-btn").click()
                expect(page.locator('[name="seg_vo_hi"]').first).to_have_value("Keep this unchecked draft")
                expect(page.locator('[name="seg_overlay_en"]').last).to_have_value("Unfinished scene")
                assert built == ["en"]
                print("PASS: successful build keeps unchecked drafts and unfinished scenes", flush=True)

                fail = True
                page.locator(".step-btn").nth(2).click()
                page.locator("#build-btn").click()
                expect(page.get_by_role("alert")).to_contain_text("Audit render failed")
                expect(page.locator('[name="seg_vo_hi"]').first).to_have_value("Keep this unchecked draft")
                expect(page.locator('[name="seg_overlay_en"]').last).to_have_value("Unfinished scene")
                print("PASS: failed build keeps all drafts", flush=True)

                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(base + "/products/chair/edit")
                page.locator(".step-btn").nth(1).click()
                page.locator('[name="delete_photo"]').check()
                page.locator(".step-btn").nth(0).click()
                page.locator('[name="name_en"]').fill("   ")
                page.locator("section.step:visible").get_by_role("button", name="Save", exact=True).click()
                expect(page.get_by_role("alert")).to_contain_text("Product name (English) is required")
                assert (product / "photos" / "1.jpg").exists()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                print("PASS: mobile invalid-name save preserves product and media", flush=True)

                page.goto(base + "/products/new")
                page.locator('[name="slug"]').fill("con")
                page.locator('[name="name_en"]').fill("Chair")
                page.locator('[name="name_hi"]').fill("Kursi")
                page.get_by_role("button", name="Next: Photos", exact=True).click()
                page.get_by_role("button", name="Next: Details", exact=True).click()
                page.get_by_role("button", name="Create product", exact=True).click()
                expect(page.get_by_role("alert")).to_contain_text("reserved")
                expect(page.locator('[name="slug"]')).to_have_value("con")
                print("PASS: reserved folder validation keeps entered details", flush=True)
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            for handler in app.extensions["error_logger"].handlers:
                handler.close()


if __name__ == "__main__":
    main()

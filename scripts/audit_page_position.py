"""Browser checks for action position recovery on disposable data.

Run: python scripts/audit_page_position.py (requires Playwright and Pillow).
No cloud requests or real renders are performed.
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
from werkzeug.serving import make_server
from audit_user_flows import QuietRequests
from reelfactory.config import write_yaml
from reelfactory.web.app import create_app


def settled(page):
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def click_in_place(page, button, marker, ready):
    button.scroll_into_view_if_needed()
    settled(page)
    before = marker.bounding_box()["y"]
    button.click()
    ready()
    page.wait_for_load_state("load")
    settled(page)
    after = marker.bounding_box()["y"]
    assert abs(before - after) < 16, (before, after, page.url)


def main():
    with tempfile.TemporaryDirectory(prefix="rf_position_") as temporary:
        root = Path(temporary)
        product = root / "products" / "chair"
        (product / "photos").mkdir(parents=True)
        Image.new("RGB", (720, 1280), "blue").save(product / "photos/1.jpg")
        write_yaml(product / "product.yaml", {"name_en": "Chair", "name_hi": "Kursi",
                                             "usp_en": ["Blue finish"], "usp_hi": ["Blue finish"]})
        write_yaml(root / "brand.yaml", {"name": "Demo"})
        app = create_app(root / "brand.yaml", root / "products", root / "out")
        queue = app.extensions["render_queue"]
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietRequests)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with patch.object(queue, "start"), patch("reelfactory.cli.build_one", return_value=[]), sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                for width in (1440, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    page.goto(base + "/products/chair/edit?step=details")
                    script = page.locator('[name="script_en"]')
                    script.evaluate("el => { for (let p = el.parentElement; p; p = p.parentElement) if (p.tagName === 'DETAILS') p.open = true; }")
                    script.fill("Keep this script")
                    save = page.locator("section.step:visible").get_by_role("button", name="Save", exact=True)
                    marker = page.locator('[name="overlay_hi"]')
                    click_in_place(page, save, marker, lambda: expect(page.get_by_role("status").first).to_contain_text("Product saved"))
                    expect(script).to_be_visible()
                    assert page.locator('[name="_ui_step"]').input_value() == "2"
                    print(f"PASS: product save retains step, nested panels and position at {width}px", flush=True)
                    page.reload()
                    settled(page)
                    expect(script).to_be_visible()
                    assert page.locator('[name="_ui_step"]').input_value() == "2"

                page.set_viewport_size({"width": 1440, "height": 900})
                page.goto(base + "/brand")
                page.get_by_role("tab", name="Voice", exact=True).click()
                click_in_place(page, page.get_by_role("button", name="Save settings"),
                               page.locator('[name="rate_en"]'),
                               lambda: expect(page.get_by_role("tab", name="Voice", exact=True)).to_have_attribute("aria-selected", "true"))
                print("PASS: brand save retains tab and position", flush=True)

                page.goto(base + "/products/chair/build")
                page.locator(".step-btn").nth(1).click()
                page.locator("#preview-btn").click()
                page.get_by_role("tab", name="English", exact=True).click()
                panel = page.locator("#save-script-panel")
                panel.locator("summary").click()
                page.locator('[name="save_name"]').fill("Position audit")
                click_in_place(page, page.locator("#save-btn"), page.locator('[name="save_name"]'),
                               lambda: expect(page.locator("#save-script-panel")).to_have_attribute("open", ""))
                expect(page.get_by_role("tab", name="English", exact=True)).to_have_attribute("aria-selected", "true")
                print("PASS: script save retains language, expanded tools and position", flush=True)

                page.locator(".step-btn").nth(2).click()
                page.locator('[name="tts"]').select_option("silent")
                click_in_place(page, page.locator("#queue-btn"), page.locator("#build-btn"),
                               lambda: expect(page.get_by_role("status").first).to_contain_text("background render queue"))
                assert page.locator('[name="_ui_step"]').input_value() == "2"
                assert page.locator("#build-form").count() == 1
                assert len(queue.jobs()) == 1
                print("PASS: queue action stays in Review with the current draft", flush=True)

                output = root / "out/chair/old_caption.txt"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("Old caption", encoding="utf-8")
                page.goto(base + "/products/chair/build")
                page.locator(".step-btn").nth(1).click()
                page.locator("#preview-btn").click()
                draft = page.locator('[name="seg_vo_en"]').first.input_value()
                library = page.locator(".output-library")
                library.locator("summary").click()
                page.locator('.output-check').check()
                page.on("dialog", lambda dialog: dialog.accept())
                page.locator("#delete-btn").click()
                expect(page.locator("#outputs")).to_be_visible()
                assert not output.exists()
                assert page.locator('[name="seg_vo_en"]').first.input_value() == draft
                print("PASS: deleting output keeps the output library open and working draft intact", flush=True)

                page.goto(base + "/products/chair/edit?manage=1")
                page.locator('[name="new_slug"]').fill("chair-copy")
                page.get_by_role("button", name="Duplicate", exact=True).click()
                expect(page.get_by_role("link", name="Open copy", exact=True)).to_be_visible()
                assert "/products/chair/edit" in page.url
                print("PASS: duplicate stays on the source product with an Open copy link", flush=True)
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            for handler in app.extensions["error_logger"].handlers:
                handler.close()


if __name__ == "__main__":
    main()

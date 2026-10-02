"""Browser regressions for request protection and collection video ownership.

Run: python scripts/audit_security_fixes.py (FFmpeg, Pillow and Playwright).
All files are disposable, and narration is silent.
"""
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

from audit_scene_editor import QuietRequests
from reelfactory.collections import create_draft
from reelfactory.config import Product, write_yaml
from reelfactory.web.app import create_app


def main():
    with tempfile.TemporaryDirectory(prefix="rf_security_audit_") as temporary:
        root = Path(temporary)
        write_yaml(root / "brand.yaml", {"name": "Demo", "default_tts": "silent", "default_template": "classic"})
        for slug, title in [("rack", "Rack"), ("chair", "Chair")]:
            folder = root / "products" / slug
            (folder / "photos").mkdir(parents=True)
            write_yaml(folder / "product.yaml", {"name_en": title, "name_hi": title})
            Image.new("RGB", (720, 1280), "red").save(folder / "photos/1.jpg")
        folder = root / "products/collection"
        folder.mkdir()
        create_draft([Product.load(root / "products/rack"), Product.load(root / "products/chair")], "Range", folder)
        clip = root / "upload.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=180x320:d=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True, capture_output=True)
        app = create_app(root / "brand.yaml", root / "products", root / "out")
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietRequests)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        dummy = root / "out/rack/keep.mp4"
        dummy.parent.mkdir(parents=True)
        dummy.write_bytes(b"dummy export")

        def attacker(environ, start_response):
            html = (f'<form method="post" action="{base}/products/rack/build/delete">'
                    '<input name="delete_file" value="keep.mp4"><button>Send test form</button></form>')
            start_response("200 OK", [("Content-Type", "text/html")])
            return [html.encode()]

        other = make_server("127.0.0.1", 0, attacker, request_handler=QuietRequests)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # Visit the workspace first to include an existing browser session.
                page.goto(base)
                page.goto(f"http://127.0.0.1:{other.server_port}")
                with page.expect_response(lambda response: response.url.endswith("/build/delete")) as response:
                    page.get_by_role("button", name="Send test form").click()
                assert response.value.status == 403
                assert dummy.read_bytes() == b"dummy export"
                print("PASS: foreign-origin form rejected; export preserved", flush=True)

                page.goto(base + "/products/collection/build")
                page.locator("#script-tab-en").click()
                page.locator('[data-add-video="en"]').click()
                dialog = page.locator("#scene-editor")
                expect(dialog.locator("[data-scene-member]")).to_have_value("")
                expect(dialog.locator("[data-scene-upload-video]")).to_be_disabled()
                dialog.locator("[data-scene-member]").select_option("chair")
                with page.expect_file_chooser() as chooser:
                    dialog.locator("[data-scene-upload-video]").click()
                chooser.value.set_files(str(clip))
                expect(dialog.locator("[data-scene-status]")).to_contain_text("Video uploaded")
                dialog.locator("[data-scene-narration]").fill("Chair has a comfortable seat.")
                dialog.locator("[data-scene-caption]").fill("Chair in motion")
                dialog.locator("[data-scene-apply]").click()
                panel = page.locator("#script-panel-en")
                panel.locator("[data-render-preview]").click()
                expect(panel.locator("[data-preview-status]")).to_contain_text("Preview ready", timeout=120000)
                expect(panel.locator("[data-preview-player]")).to_be_visible()
                members = Product.load(folder).collection_members
                chair = next(member for member in members if member["slug"] == "chair")
                assert any(name.endswith(".mp4") for name in chair["media"].values())
                assert not errors, errors
                print("PASS: new collection video belongs to chosen product and renders successfully", flush=True)
                browser.close()
        finally:
            other.shutdown()
            other.server_close()
            server.shutdown()
            server.server_close()
            for handler in app.extensions["error_logger"].handlers:
                handler.close()


if __name__ == "__main__":
    main()

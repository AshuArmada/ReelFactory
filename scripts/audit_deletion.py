"""Check deletion confirmations and disk effects with disposable products only."""
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server
from audit_scene_editor import QuietRequests
from reelfactory.config import read_yaml, write_yaml
from reelfactory.web.app import create_app


def main():
    with tempfile.TemporaryDirectory(prefix='rf_delete_audit_') as temporary:
        root = Path(temporary)
        write_yaml(root / 'brand.yaml', {'name': 'Deletion audit'})
        for slug in ('demo', 'other'):
            photos = root / 'products' / slug / 'photos'
            photos.mkdir(parents=True)
            write_yaml(photos.parent / 'product.yaml', {
                'name_en': slug, 'name_hi': slug, 'usp_en': ['Solid wood'],
                'photo_order': ['1.jpg', '2.jpg']})
            for index, color in enumerate(('red', 'blue'), 1):
                Image.new('RGB', (720, 1280), color).save(photos / f'{index}.jpg')
            output = root / 'out' / slug / 'final.mp4'
            output.parent.mkdir(parents=True)
            output.write_bytes(b'disposable export')
        app = create_app(root / 'brand.yaml', root / 'products', root / 'out')
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequests)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors, dialogs = [], []
                decision = {'accept': False}
                page.on('pageerror', lambda error: errors.append(str(error)))
                def confirm(dialog):
                    dialogs.append(dialog.message)
                    dialog.accept() if decision['accept'] else dialog.dismiss()
                page.on('dialog', confirm)

                page.goto(base + '/products/demo/build')
                page.locator('[name="lang"][value="hi"]').uncheck()
                page.locator('.step-btn').nth(1).click()
                page.locator('#preview-btn').click()
                name = 'Dad\'s "best" <draft>'
                page.locator('[data-open-script-tool="save-script-panel"]').click()
                page.locator('[name="save_name"]').fill(name)
                page.locator('#save-btn').click()
                page.locator('[name="seg_vo_en"]').first.fill('Keep my current working line')
                library = root / 'products/demo/saved_scripts.yaml'
                page.get_by_text('Saved scripts for demo', exact=False).first.click()
                delete = page.get_by_role('button', name='Delete ' + name, exact=True)
                original = library.read_bytes()
                delete.click()
                assert dialogs and name in dialogs[-1], 'A saved-script name broke its deletion confirmation'
                assert library.read_bytes() == original, 'Dismissed deletion changed the library'
                decision['accept'] = True
                delete.click()
                expect(page.get_by_text('Saved script deleted. Your current work is still here.', exact=True)).to_be_visible()
                assert read_yaml(library) == {}
                expect(page.locator('[name="seg_vo_en"]').first).to_have_value('Keep my current working line')
                print('PASS: script deletion confirms quoted names, cancellation preserves it, acceptance keeps the working draft', flush=True)

                page.locator('[data-open-script-tool="save-script-panel"]').click()
                page.locator('[name="save_name"]').fill('Reusable script')
                page.locator('#save-btn').click()
                page.locator('.script-management').first.locator('summary').click()
                page.get_by_role('button', name='Clear current draft', exact=True).click()
                expect(page.locator('#preview-btn')).to_be_visible()
                assert read_yaml(library)['en'][0]['name'] == 'Reusable script'
                page.get_by_text('Saved scripts for demo', exact=False).first.click()
                page.get_by_text('Manage saved scripts', exact=False).first.click()
                page.get_by_role('button', name='Clear all saved scripts', exact=True).click()
                assert read_yaml(library) == {}
                print('PASS: clearing the working draft and clearing the saved library remain separate actions', flush=True)

                page.goto(base + '/products/demo/edit?step=photos')
                page.locator('[name="delete_photo"][value="1.jpg"]').check()
                assert (root / 'products/demo/photos/1.jpg').exists()
                page.locator('section.step:visible').get_by_role('button', name='Save', exact=True).click()
                expect(page.locator('[name="delete_photo"][value="1.jpg"]')).to_have_count(0)
                assert not (root / 'products/demo/photos/1.jpg').exists()
                assert (root / 'products/demo/photos/2.jpg').exists()
                assert read_yaml(root / 'products/demo/product.yaml')['photo_order'] == ['2.jpg']
                assert (root / 'products/other/photos/1.jpg').exists()
                print('PASS: photo deletion happens on Save, updates order, and leaves other photos/products intact', flush=True)

                # Wrong confirmation must be rejected by both browser and server.
                manage = page.locator('#manage-product')
                manage.locator('summary').click()
                page.locator('[name="confirm_slug"]').fill('wrong-name')
                page.get_by_role('button', name='Delete', exact=True).click()
                assert (root / 'products/demo/product.yaml').exists()
                page.locator('[name="confirm_slug"]').fill('demo')
                page.get_by_role('button', name='Delete', exact=True).click()
                page.wait_for_url(base + '/?**')
                assert not (root / 'products/demo').exists()
                assert (root / 'out/demo/final.mp4').exists()
                assert (root / 'products/other/product.yaml').exists()
                print('PASS: product deletion requires its name and keeps finished videos by default', flush=True)

                page.goto(base + '/products/other/edit?manage=1')
                page.locator('[name="confirm_slug"]').fill('other')
                page.locator('[name="delete_outputs"]').check()
                page.get_by_role('button', name='Delete', exact=True).click()
                page.wait_for_url(base + '/?**')
                assert not (root / 'products/other').exists()
                assert not (root / 'out/other').exists()
                assert (root / 'out/demo/final.mp4').exists()
                assert not errors, errors
                print('PASS: explicit product+video deletion removes only that product\'s output folder; no JavaScript errors', flush=True)
                browser.close()
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
            for handler in app.extensions['error_logger'].handlers:
                handler.close()


if __name__ == '__main__':
    main()

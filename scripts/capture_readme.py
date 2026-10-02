"""Capture real UI screenshots on disposable, fictional data; no cloud calls.

Run: python scripts/capture_readme.py
Requires Pillow, Playwright and its Chromium browser, plus FFmpeg/ffprobe.
"""
from pathlib import Path
import hashlib
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server, WSGIRequestHandler
from reelfactory import gemini, local_llm, stock
from reelfactory.config import write_yaml
from reelfactory.web.app import create_app

DEST = ROOT / 'docs' / 'screenshots'


class QuietRequests(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def demo_media(path, kind, colour):
    """Simple labelled illustrations, not customer photos or AI product claims."""
    im = Image.new('RGB', (960, 1200), '#f2eee7')
    d = ImageDraw.Draw(im)
    d.ellipse((150, 950, 820, 1030), fill='#d7d2ca')
    if kind == 'rack':
        for x in (225, 690):
            d.rectangle((x, 210, x + 32, 975), fill=colour)
        for y in (230, 440, 650, 860):
            d.rectangle((220, y, 730, y + 35), fill=colour)
            d.rectangle((252, y + 35, 689, y + 48), fill='#a6aaa9')
    else:
        d.rounded_rectangle((175, 500, 790, 560), radius=12, fill=colour)
        for x in (220, 695):
            d.rectangle((x, 560, x + 32, 980), fill='#3e4854')
    d.text((55, 60), 'DEMO PRODUCT ILLUSTRATION', fill='#555555', font_size=30)
    d.text((55, 1100), 'Reel Factory documentation', fill='#555555', font_size=26)
    im.save(path)


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rf_readme_') as temporary:
        root = Path(temporary)
        write_yaml(root / 'brand.yaml', {'name': 'Demo Home Studio', 'city': 'Demo City',
                   'audience': 'People furnishing a home or small shop', 'default_tts': 'silent'})
        for slug, name, kind, colour in (
            ('display-rack', 'Display Rack', 'rack', '#526c62'),
            ('work-table', 'Work Table', 'table', '#bc8353'),
        ):
            folder = root / 'products' / slug
            (folder / 'photos').mkdir(parents=True)
            for index, tint in enumerate((colour, '#54657d'), 1):
                demo_media(folder / 'photos' / f'{index}.png', kind, tint)
            write_yaml(folder / 'product.yaml', {
                'name_en': name, 'name_hi': name, 'target_seconds': 15,
                'usp_en': ['Two colour options', 'Ask our team about available sizes'],
                'usp_hi': ['Two colour options', 'Ask our team about available sizes'],
                'audience': 'Home and small-shop owners', 'tone': 'trust',
                'script_en': ['Make room for the things you use every day.',
                              f'Explore the {name.lower()} from Demo Home Studio.',
                              'Message our team to ask about colours and sizes.'],
                'overlay_en': ['Room for everyday things', name, 'Ask about colours and sizes'],
            })
            rows = [{'name': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                     'summary': f'Demo illustration of the {name.lower()} in a different colour. '
                                'Use this view to introduce its shape; it does not prove performance.'}
                    for p in sorted((folder / 'photos').glob('*.png'))]
            write_yaml(folder / 'photo_analysis.yaml', {
                'version': 1, 'photos': rows, 'model': 'documentation-demo',
                'group_summary': f'Demo context: two views of the {name.lower()}. '
                                 'Introduce the product, show the colour options, then invite an enquiry.',
            })
        with patch.object(gemini, 'ROOT', root), patch.object(local_llm, 'ROOT', root), patch.object(stock, 'ROOT', root):
            app = create_app(root / 'brand.yaml', root / 'products', root / 'out')
            server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequests)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page = browser.new_page(viewport={'width': 1360, 'height': 980}, device_scale_factor=1)
                    page.set_default_timeout(20000)
                    base = f'http://127.0.0.1:{server.server_port}'

                    def capture(name):
                        page.evaluate("document.querySelectorAll('img').forEach(i => i.loading = 'eager')")
                        page.wait_for_function("Array.from(document.images).every(i => i.complete)")
                        page.evaluate('document.fonts.ready')
                        page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
                        page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                        page.screenshot(path=str(DEST / name), full_page=True, animations='disabled')
                        print('Captured', name, flush=True)

                    def post(path, pairs):
                        with page.expect_navigation(wait_until='networkidle', timeout=180000):
                            page.evaluate('''({url, pairs}) => {
                            const form = document.createElement('form'); form.method='POST'; form.action=url;
                            for (const [name,value] of pairs) {
                                const input=document.createElement('input'); input.type='hidden';
                                input.name=name; input.value=value; form.append(input);
                            } document.body.append(form); form.submit();
                            }''', {'url': base + path, 'pairs': pairs})

                    page.goto(base)
                    for checkbox in page.locator('input[name=products]').all():
                        checkbox.check()
                    capture('01-products.png')
                    page.get_by_role('button', name='Choose photos').click()
                    capture('02-collection-photos.png')
                    page.goto(base + '/products/display-rack/edit')
                    capture('03-product-details.png')
                    page.locator('.step-btn').nth(1).click()
                    page.get_by_text('Photo understanding', exact=False).first.click()
                    capture('04-photo-context.png')
                    page.goto(base + '/products/display-rack/build')
                    capture('05-build-options.png')
                    post('/products/display-rack/script', [('lang', 'en'), ('script', 'template')])
                    capture('06-script-editor.png')
                    page.get_by_role('button', name='Next: Review').click()
                    capture('09-review.png')
                    post('/products/display-rack/build', [('lang', 'en'), ('script', 'template'),
                         ('tts', 'silent'), ('aspect', '1:1'), ('preset', 'ultrafast'), ('no_music', 'on')])
                    page.locator('video').first.wait_for()
                    page.locator('video').first.evaluate('v => new Promise(resolve => {v.onseeked=resolve; v.currentTime=1;})')
                    capture('10-finished-video.png')
                    page.goto(base + '/settings/apis')
                    page.locator('details').filter(has=page.locator('input[name=INCEPTION_API_KEY]')).first.locator('summary').first.click()
                    capture('07-api-settings.png')
                    page.goto(base + '/brand')
                    capture('08-brand.png')
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
                for handler in app.extensions['error_logger'].handlers:
                    handler.close()


if __name__ == '__main__':
    main()

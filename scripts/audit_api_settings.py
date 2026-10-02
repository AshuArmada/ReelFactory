"""Check the API settings UI using disposable configuration and dummy keys.

Run: python scripts/audit_api_settings.py (requires Playwright).
No provider calls are made and real workspace settings are never changed.
"""
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from audit_scene_editor import QuietRequests
from reelfactory.config import write_yaml
from reelfactory.web.app import create_app
from reelfactory.web.api_settings import PROVIDERS


def main():
    names = {name: '' for provider in PROVIDERS.values() for name, *_ in provider[2] + provider[3]}
    with tempfile.TemporaryDirectory(prefix='rf_api_ui_') as temporary, patch.dict(os.environ, names):
        root = Path(temporary)
        write_yaml(root / 'brand.yaml', {'name': 'Studio demo'})
        (root / '.env').write_text('GEMINI_API_KEY=dummy-saved-gemini\n')
        app = create_app(root / 'brand.yaml', root / 'products', root / 'out')
        app.config['API_ENV_PATH'] = root / '.env'
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequests)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}/settings/apis'
        artifacts = ROOT / 'out/audit/api-settings'
        artifacts.mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1440, 'height': 1060})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                expect(page.locator('[data-provider-panel]:visible')).to_have_count(1)
                assert 'dummy-saved-gemini' not in page.content()
                page.screenshot(path=str(artifacts / 'desktop.png'), full_page=True)
                key = page.locator('[name="GEMINI_API_KEY"]')
                key.fill('dummy-edited-gemini')
                page.get_by_role('button', name='Show entered api key', exact=True).click()
                expect(key).to_have_attribute('type', 'text')
                page.get_by_role('button', name='Hide entered api key', exact=True).click()
                expect(key).to_have_attribute('type', 'password')
                page.locator('[data-provider-link="pexels"]').click()
                page.locator('[name="PEXELS_API_KEY"]').fill('dummy-pexels')
                page.get_by_role('button', name='Save Pexels').click()
                expect(page.locator('[data-api-notice]')).to_contain_text('Pexels settings saved')
                expect(page.locator('[data-configured-count]')).to_have_text('2 of 6 providers have keys')
                expect(page.locator('[name="PEXELS_API_KEY"]')).to_have_value('')
                page.locator('[data-provider-link="gemini"]').click()
                expect(key).to_have_value('dummy-edited-gemini')
                page.get_by_role('button', name='Save Gemini').click()
                expect(page.locator('[data-api-notice]')).to_contain_text('Gemini settings saved')
                expect(key).to_have_value('')
                models = page.locator('#provider-gemini [data-section="models"]')
                models.locator('summary').click()
                model = page.locator('[name="gemini_script_model"]')
                original_model = model.input_value()
                model.fill('')
                models.locator('summary').click()
                page.get_by_role('button', name='Save Gemini').click()
                expect(model).to_be_visible()
                expect(model).to_be_focused()
                model.fill(original_model)
                print('PASS: masked keys, reveal controls, independent saves and edits preserved across providers', flush=True)

                page.locator('[data-provider-link="local"]').click()
                page.locator('[name="local_script_model"]').fill('edited-local-model')
                page.locator('[name="local_base_url"]').fill('http://remote.example/v1')
                page.locator('[name="LOCAL_LLM_API_KEY"]').fill('dummy-local')
                page.get_by_role('button', name='Save Local model').click()
                expect(page.locator('[data-api-notice]')).to_contain_text('use HTTPS')
                expect(page.locator('[name="local_script_model"]')).to_have_value('edited-local-model')
                expect(page.locator('[name="LOCAL_LLM_API_KEY"]')).to_have_value('')
                page.locator('[name="local_base_url"]').fill('http://localhost:1234/v1')
                page.get_by_role('button', name='Save Local model').click()
                expect(page.locator('[data-api-notice]')).to_contain_text('Local model settings saved')
                print('PASS: validation highlights fields, preserves model edits, and permits correction', flush=True)

                page.locator('[data-provider-link="pexels"]').click()
                page.locator('[name="clear_PEXELS_API_KEY"]').check()
                expect(page.locator('[name="PEXELS_API_KEY"]')).to_be_disabled()
                page.get_by_role('button', name='Save Pexels').click()
                expect(page.locator('[data-api-notice]')).to_contain_text('Pexels settings saved')
                expect(page.locator('[data-provider-link="pexels"]')).to_contain_text('Not configured')
                assert 'dummy-pexels' not in (root / '.env').read_text()
                page.locator('[name="PEXELS_API_KEY"]').fill('dummy-network-retry')
                page.route('**/settings/apis', lambda route: route.abort())
                page.get_by_role('button', name='Save Pexels').click()
                expect(page.locator('#provider-pexels [data-save-state]')).to_contain_text('Could not reach Reel Factory')
                expect(page.locator('[name="PEXELS_API_KEY"]')).to_have_value('dummy-network-retry')
                expect(page.get_by_role('button', name='Save Pexels')).to_be_enabled()
                page.unroute('**/settings/apis')
                page.locator('[name="PEXELS_API_KEY"]').fill('')
                print('PASS: key removal and network-error recovery', flush=True)

                page.goto(url)
                for width in (768, 390, 320):
                    page.set_viewport_size({'width': width, 'height': 900})
                    for provider in PROVIDERS:
                        page.locator(f'[data-provider-link="{provider}"]').click()
                        expect(page.locator(f'#provider-{provider}')).to_be_visible()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, provider)
                    page.locator('[data-provider-link="gemini"]').click()
                    if width == 390:
                        page.screenshot(path=str(artifacts / 'mobile.png'), full_page=True)
                assert not errors, errors
                context = browser.new_context(java_script_enabled=False)
                plain = context.new_page()
                plain.goto(url)
                expect(plain.locator('[data-provider-panel]:visible')).to_have_count(6)
                plain.locator('[name="PIXABAY_API_KEY"]').fill('dummy-no-js')
                plain.get_by_role('button', name='Save Pixabay').click()
                expect(plain.locator('[data-api-notice]')).to_contain_text('Pixabay settings saved')
                print('PASS: tablet/mobile layouts, all six providers, and saving without JavaScript', flush=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            for handler in app.extensions['error_logger'].handlers:
                handler.close()


if __name__ == '__main__':
    main()

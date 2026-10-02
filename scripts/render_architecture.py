"""Render docs/architecture/*.mmd to SVG using pinned Mermaid and Chromium.

Run: python scripts/render_architecture.py
Requires requests, Playwright and its Chromium browser. Downloads Mermaid from
jsDelivr; no application data or API credentials are read or sent.
"""
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
MERMAID_URL = 'https://cdn.jsdelivr.net/npm/mermaid@10.9.3/dist/mermaid.min.js'


def main():
    sources = sorted((ROOT / 'docs' / 'architecture').glob('*.mmd'))
    if not sources:
        raise SystemExit('No Mermaid architecture sources found.')
    response = requests.get(MERMAID_URL, timeout=45)
    response.raise_for_status()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<html><body></body></html>')
            page.add_script_tag(content=response.text)
            page.evaluate("""mermaid.initialize({startOnLoad:false,
                securityLevel:'strict', theme:'neutral',
                flowchart:{htmlLabels:false}, sequence:{useMaxWidth:false}})""")
            for index, source in enumerate(sources):
                svg = page.evaluate(
                    'async ({code,id}) => (await mermaid.render(id,code)).svg',
                    {'code': source.read_text(encoding='utf-8'), 'id': f'architecture{index}'})
                source.with_suffix('.svg').write_text(svg, encoding='utf-8')
                print('Rendered', source.name)
        finally:
            browser.close()


if __name__ == '__main__':
    main()

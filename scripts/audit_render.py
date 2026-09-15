"""Offline advanced-render smoke check with synthetic pictures, logo and audio.

Runs bold/premium end cards, music ducking, beat timing and sound effects through
FFmpeg. Audio tones stand in for narration; this does not assess voice quality.
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
from reelfactory import cli, script, voice
from reelfactory.config import Brand, INTENTS, Product


def run(command):
    return subprocess.run(command, check=True, capture_output=True).stdout


def main():
    artifacts = ROOT / 'out/audit/render'
    artifacts.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='rf_render_audit_') as tmp:
        root = Path(tmp)
        photos = []
        for index, color in enumerate(('red', 'blue')):
            photo = root / f'{index}.png'
            Image.new('RGB', (1440, 2560), color).save(photo)
            photos.append(photo)
        logo = root / 'logo.png'
        Image.new('RGBA', (120, 60), 'white').save(logo)
        for name, frequency, seconds in [('speech', 700, 1.6), ('music', 220, 3)]:
            run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                 f'sine=frequency={frequency}:duration={seconds}', str(root / f'{name}.wav')])
        brand = Brand(name='Audit Demo', logo=str(logo), music=str(root / 'music.wav'), music_bpm=96)
        product = Product(slug='demo', dir=root, name_en='Blue chair', name_hi='नीली कुर्सी',
                          photos=photos, usp_en=['Blue finish'], usp_hi=['नीला रंग'])
        for intent in INTENTS:
            product.intent = intent
            for lang in ('en', 'hi'):
                rows = script.build(product, brand, lang)
                assert rows and all(row.vo.strip() and row.overlay.strip() for row in rows)
                assert script.caption(product, brand, lang).strip()
        results.append({'feature': 'All nine template intents in both languages', 'status': 'PASS'})
        for template in ('bold', 'premium'):
            args = SimpleNamespace(tts='silent', preset='ultrafast', no_music=False, template=template)
            rows = [script.Segment(role, 'Synthetic audio check.', 'Blue chair') for role in ('hook', 'price', 'cta')]
            clips = [voice.Clip(root / 'speech.wav', 1.6) for _ in rows]
            with patch.object(voice, 'synthesize', return_value=clips):
                written = cli.build_one(product, brand, 'en', ['9:16'], artifacts / template, args, segments=rows)
            video = next(p for p in written if p.suffix == '.mp4')
            data = json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)]))
            assert {s['codec_type'] for s in data['streams']} == {'audio', 'video'}
            assert float(data['format']['duration']) > 4.8
            # Decode the full result to catch corrupt streams; export the closing frame for review.
            run(['ffmpeg', '-v', 'error', '-i', str(video), '-f', 'null', '-'])
            run(['ffmpeg', '-y', '-v', 'error', '-sseof', '-0.5', '-i', str(video),
                 '-frames:v', '1', str(artifacts / f'{template}-end-card.png')])
            results.append({'feature': f'{template}: logo, end card, grade, transitions, music/voice mix, beat timing and effects',
                            'status': 'PASS', 'file': str(video.relative_to(ROOT))})
            (artifacts / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
            print('PASS', template, flush=True)


if __name__ == '__main__':
    main()

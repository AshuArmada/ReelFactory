"""Opt-in live service checks using synthetic data; may consume API quota.

Run from the repository root: python scripts/audit_services.py
Each provider gets a separate process and a 90-second budget.
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import replace
import json
import logging
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SERVICES = ["script-ai", "script-local", "voice-edge", "voice-gtts",
            "voice-gemini", "stock-pexels", "stock-pixabay", "photo-analysis"]
ARTIFACTS = ROOT / "out" / "audit" / "services"


def check(name):
    from PIL import Image, ImageDraw
    from reelfactory import ai_script, local_script, voice, stock, photo_analysis
    from reelfactory.config import Brand, Product, write_yaml
    from reelfactory.web.diagnostics import _RedactingFormatter

    dest = ARTIFACTS / name
    dest.mkdir(parents=True, exist_ok=True)
    photo_dir = dest / "photos"
    photo_dir.mkdir(exist_ok=True)
    image = Image.new("RGB", (720, 1280), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((200, 220, 520, 700), fill="blue")
    draw.rectangle((160, 690, 560, 770), fill="blue")
    draw.rectangle((190, 770, 230, 1040), fill="blue")
    draw.rectangle((490, 770, 530, 1040), fill="blue")
    image.save(photo_dir / "1.png")
    write_yaml(dest / "product.yaml", {
        "name_en": "Blue chair", "name_hi": "नीली कुर्सी", "price": "Rs 999",
        "usp_en": ["Blue finish"], "usp_hi": ["नीला रंग"], "target_seconds": 10,
    })
    product = Product.load(dest)
    configured = Brand.load(ROOT / "brand.yaml")
    brand = replace(Brand(), name="Audit Demo", gemini_script_model=configured.gemini_script_model,
                    gemini_tts_model=configured.gemini_tts_model,
                    local_script_model=configured.local_script_model, local_base_url=configured.local_base_url,
                    gemini_voice=configured.gemini_voice)
    result = {"feature": name, "status": "PASS"}
    try:
        if name.startswith("script-"):
            backend = name.split("-", 1)[1]
            writer = {"ai": ai_script, "local": local_script}[backend]
            options = {"model": getattr(brand, {"ai": "gemini", "local": "local"}[backend] + "_script_model")}
            if backend == "local":
                options["base_url"] = brand.local_base_url
            scripts = {}
            for lang in ("en", "hi"):
                scripts[lang] = [vars(s) for s in writer.build(product, brand, lang, **options)]
            (dest / "scripts.json").write_text(json.dumps(scripts, ensure_ascii=False, indent=2), encoding="utf-8")
            result["detail"] = "Generated and validated English and Hindi scripts"
        elif name.startswith("voice-"):
            backend = name.split("-", 1)[1]
            durations = {}
            for lang, line in (("en", "Take a look at this blue chair."), ("hi", "यह नीली कुर्सी देखिए।")):
                clips = voice.synthesize([line], lang, brand.voice(lang), "+0%", dest / lang,
                                         backend=backend, gemini_voice=brand.gemini_voice,
                                         gemini_model=brand.gemini_tts_model)
                durations[lang] = round(voice.probe_duration(clips[0].path), 2)
            result["detail"] = f"Playable English/Hindi audio; durations {durations} seconds"
        elif name.startswith("stock-"):
            source = name.split("-", 1)[1]
            found = stock.search("chair", count=1, sources=[source], timeout=15)
            if not found:
                raise ValueError("No search results")
            errors = []
            downloaded = stock.download(found[:1], dest / "downloaded", timeout=20,
                                        on_progress=lambda photo, path, error: errors.append(error) if error else None)
            if not downloaded:
                raise ValueError("Download failed: " + "; ".join(errors))
            stock.record_credits(dest, downloaded, "chair")
            result["detail"] = "Search, download and credit recording succeeded"
        else:
            analysis = photo_analysis.analyze(product, brand)
            if not analysis.get("group_summary"):
                raise ValueError("Photo analysis returned no summary")
            result["detail"] = "Analyzed synthetic chair image and saved its summary"
    except Exception as exc:
        result["status"] = "FAIL"
        record = logging.LogRecord("audit", logging.ERROR, "", 0, "%s: %s", (type(exc).__name__, exc), None)
        result["detail"] = _RedactingFormatter(ROOT / ".env").format(record)[-1800:]
    return result


def isolated(name):
    try:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), name],
                                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90)
        return json.loads(completed.stdout)
    except subprocess.TimeoutExpired:
        return {"feature": name, "status": "TIMEOUT", "detail": "Did not complete within 90 seconds"}
    except (ValueError, OSError):
        return {"feature": name, "status": "FAIL", "detail": "Service check process failed; inspect the provider configuration"}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 1:
        if sys.argv[1] not in SERVICES:
            raise SystemExit("Unknown service")
        print(json.dumps(check(sys.argv[1]), ensure_ascii=False))
    else:
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            for future in concurrent.futures.as_completed([pool.submit(isolated, name) for name in SERVICES]):
                item = future.result()
                results.append(item)
                (ARTIFACTS / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                print(item["feature"], item["status"], item["detail"], flush=True)

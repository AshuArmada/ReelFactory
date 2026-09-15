# Feature and user-flow checklist

Audited **15 September 2026** on Windows, Python 3.12, FFmpeg/ffprobe 9.0.1,
and headless Chromium. Checks used disposable products and synthetic media;
the user's products, saved scripts and finished videos were not cleared.

## Results and evidence

- **297 passing non-slow tests**, 13 deliberately deselected, in 49.88 seconds:
  `out/audit-tests-fast-final.xml`.
- **All 13 real-render tests passed** in the full run, covering actual output
  dimensions, Hindi, scene-to-image correspondence, captions and repeat builds.
  That run had 307 passes and three failures in newly added test fixtures that
  omitted Hindi selling points. The fixtures were corrected; all three passed
  in the final non-slow run. Evidence: `out/audit-tests-final.xml` and the final
  fast XML. Across these runs, all 310 current tests passed their final checks.
- **16 browser workflow checks passed**: `out/audit/browser/results.json`.
  Screenshots, resulting HTML and a synthetic failure log are beside it.
- **Three advanced checks passed**: all nine offline intents in both languages,
  plus real Bold and Premium renders with logo, end card, music, beat timing
  and generated sound effects. Evidence: `out/audit/render/results.json`.
- **Eight of nine live service features passed** after the local writer fix.
  Evidence: `out/audit/services/results.json`, superseded for local writing by
  `out/audit/services/local-recheck.json`. Pixabay downloading remains blocked.

PASS means the listed check succeeded, not that every possible input or provider
combination has been certified. **Partial** identifies an untested interaction
or a subjective criterion. Test names below refer to files under `tests/`.

## UI and product setup

| Feature | Result | Check and scope |
| --- | --- | --- |
| Dashboard, products and readiness | PASS | `test_web_products`: valid/missing/broken configurations; browser opens dashboard. |
| Create product, required fields, duplicate slug | PASS | Route tests and real browser validation/create flow. |
| Product names, Hindi/English selling points, optional facts, offers, CTA and script controls | PASS | Configuration/form round trips; browser fills names and selling points. |
| Edit and clear optional fields | PASS | `test_regressions_no_ffmpeg`, `test_web_products`. |
| Save without returning to the first product step | FIXED / PASS | Browser saves Photos and stays there; backend supports Basics/Photos/Details. |
| Upload images and video clips | PASS | Browser uploads three images and an MP4; unsupported uploads rejected by tests. |
| Image positions, arrows, removal and saved order | PASS | Browser arrows/save; configuration and route tests cover positions, missing files and deletion. |
| Drag photos to reorder | Partial | Handler reviewed; browser audit uses arrows. Native mouse/touch drag has not been exercised. |
| Photo dimensions, crop and quality advice | PASS | `test_photo_quality` and render preflight. Edit-photo and build-page details open in popups; browser verified Close, Escape, backdrop dismissal, restored focus, mobile overflow and collapsed no-JavaScript fallback. Screenshots: `out/audit/photo-popups/`. |
| Duplicate product, automatic name, avoid output copying | PASS | `test_web_products`; filesystem assertions on disposable data. |
| Delete product with typed slug; optionally delete outputs | PASS | Route tests verify confirmation, files kept/removed and path containment. |
| Brand identity, colour, fonts, voice, music and defaults | PASS | `test_web_brand` and configuration tests; real browser save. |
| Minimal brand saved with blank colours/rates | FIXED / PASS | Defaults now populated and invalid colours/rates refused before save; real render succeeds. |
| Brand save retains selected tab | FIXED / PASS | Browser saves Voice and remains on Voice. |
| Logo/music upload, preview, removal and invalid file types | PASS | `test_web_brand`; synthetic logo/music used in real advanced renders. |
| Repair malformed product/brand YAML | PASS | Repair editor, validation before overwrite, missing configuration and unknown-key handling tested. |

## Scripts and editing

| Feature | Result | Check and scope |
| --- | --- | --- |
| Offline writer in Hindi/English; nine intents | PASS | Advanced audit generates both languages for every intent. |
| Product-appropriate offline opening | FIXED / PASS | Removed rack-specific openings and unsupported durability/popularity/urgency/customisation claims; regression checks cover three tones in both languages. |
| Gemini writer | LIVE PASS | English and Hindi generated and validated using configured Gemini model. |
| Grok writer | LIVE PASS | English and Hindi generated and validated using configured Grok model. |
| Ollama local writer | FIXED / LIVE PASS | Server was stopped; after startup, missing response fields exposed a second failure. JSON schema plus one repair attempt now passes with `llama3.2:3b`. |
| Other OpenAI-compatible local endpoints | Partial | Must support JSON-schema responses. LM Studio/llama.cpp were not live-tested. |
| Shared product/brand/intent/fact/photo-summary context | PASS | Prompt and photo-analysis tests; live provider generation. Model factual accuracy still requires review. |
| Rewrite includes original script, media names and extra instructions | PASS | `test_web_script` checks complete current context passed to writers. |
| Rewrite one language while retaining the other | PASS | Route regression tests preserve untouched language and media. |
| Failed rewrite keeps current text and instructions | PASS | Browser injects provider failure and verifies recovery; logs retain traceback. |
| Malformed AI response and required/forbidden phrases | FIXED / PASS | One format correction retains original brief; persistent phrase-rule violations now raise a visible error instead of silently returning invalid copy. |
| Free-form rewriting with built-in writer | Unsupported by design | Shows actionable message to select an AI writer; keeps draft. |
| Manual narration/overlay edits and permanent overrides | PASS | Exact text passed to renderer; override round trips tested. |
| Hindi/English tabs and keyboard navigation | PASS | Browser changes tab with arrow key; active language survives save/rewrite error. |
| Add, remove and move scenes with their media | PASS | Real browser actions and route tests; media alignment verified by rendered frames. |
| Select image or clip for a scene | FIXED / PASS | Preview now switches between image and video elements; mixed media render opens in browser. |
| Compare opening variants; choose one or multiple | PASS | Route tests and browser comparison/pick flow. |
| Empty comparison selection | FIXED / PASS | Visible validation retains all versions; no silent loss of comparison state. |
| Build comparison without confirming choices | FIXED | Backend rejects and returns to selection; reviewed alongside comparison tests. |
| Multi-version render failure | FIXED / PASS | Browser injects render failure and confirms comparison state remains available. |
| Entire edited language blanked | FIXED / PASS | Returns validation error before rendering; does not silently generate replacement wording. |
| Save named versions and load without regeneration | PASS | File assertions and browser save/load preserve text, media and instructions. |
| Invalid/deleted saved-version selection | FIXED / PASS | Rejects negative/invalid indices, preserves current draft, returns to Script. |
| Delete a saved script without returning to page one | PASS | Regression tests preserve editor, comparison data and current step. |
| Clear current draft | PASS | Confirmation and browser action; remains on Script without deleting library. |
| Clear all saved scripts for this product | PASS | Confirmation, filesystem assertions and browser flow; current step retained. |
| Automatic save before navigating away | Not implemented | Use Save a version explicitly. Unsaved drafts are not a persistent library entry. |

## Media services and photo understanding

| Feature | Result | Check and scope |
| --- | --- | --- |
| Stock search, resolution filter and empty results | PASS | `test_web_stock` and `test_stock`; live Pexels/Pixabay searches. |
| Select exact stock image without repeating search | PASS | Route tests verify exact URL, selected files only and empty selection. |
| Pexels download and source credits | LIVE PASS | One image downloaded and provenance recorded. |
| Pixabay download | **EXTERNAL FAILURE** | Search succeeded, image CDN returned **HTTP 429 Too Many Requests**. Not considered working end to end in this audit. Use Pexels or uploaded media; retry later rather than repeatedly requesting the same blocked download. |
| Stock source validation and deleted-photo credit cleanup | PASS | Unapproved download hosts rejected; credit removal tested. |
| Analyze photos with Gemini | LIVE PASS | Synthetic blue-chair image analyzed; summary saved. This is not a recognition-accuracy benchmark. |
| Protect unsaved product edits before analysis | PASS | Browser sees guard when analysis would leave edited product fields behind. |
| Edit photo summary, retain fingerprints, exclude stale analysis | PASS | `test_photo_analysis` and route regression tests. |
| Save/restore/delete named analysis snapshots | PASS | Snapshot restoration requires matching photos; route and persistence tests. |

## Narration, rendering and finished files

| Feature | Result | Check and scope |
| --- | --- | --- |
| Edge, gTTS, Gemini narration in both languages | LIVE PASS | Each produced playable, nonzero-duration English/Hindi audio, checked with ffprobe. Samples are under `out/audit/services/voice-*/`. |
| Conversational Gemini delivery and Edge speaking pace | PASS | Request-level regression test and provider-specific controls in browser. |
| Voice naturalness, pronunciation and emotional fit | Partial | No human listening score assigned. Play samples and choose delivery based on the actual script. |
| Silent/offline preview | PASS | Real browser and automated renders use silent narration. |
| Four aspect ratios | PASS | Real FFmpeg renders checked against required output dimensions. |
| Classic, Bold and Premium | PASS | Classic in end-to-end/browser tests; Bold/Premium advanced real renders decode fully. |
| Image order, repeated images, explicit scene picks | PASS | End-to-end tests inspect frame colour to establish which image actually appears. |
| Missing selected image | PASS with fallback | Uses an available product image, as documented in README; test verifies render survives. |
| Last image in Bold/Premium | Intended end-card replacement | End-card output inspected. Classic retains scene media; README explains the difference. |
| Logo, watermark, fonts and Hindi shaping | PASS | Asset/settings tests, rendered logo/end cards, font tests and actual Hindi render. |
| Word-timed captions | Partial | Subtitle tests and Edge integration paths checked; no word-by-word listening/sync evaluation of the live samples. |
| Music volume/ducking, beat snapping and generated effects | PASS (smoke) | Real Bold/Premium renders mix synthetic speech/music tones at 96 BPM. Full streams decode. Perceptual mix quality and exhaustive tempo combinations were not scored. |
| Quality presets, colour conversion, grain and web-ready encoding | PASS | `test_encoding`; actual playable files plus filter/encoding assertions. |
| Build both languages and multiple shapes/versions | PASS | Route call assertions; individual shape/language renders verified separately. |
| Failed render preserves working script/options | PASS | Route tests and browser-injected failure. |
| Rebuild without overwriting older videos | PASS | Actual end-to-end render and output-path tests. Caption is shared per product/language and rewritten. |
| Finished video playback and download | PASS | Browser verifies dimensions and metadata, downloads a nonempty MP4. |
| Caption file and Copy button | PASS | Browser clipboard text equals caption, allowing Windows CRLF newline conversion. |
| Select/delete finished files | PASS | Browser confirms deletion and filesystem result; route tests reject paths outside output directory. |

## Reliability, accessibility and operations

| Feature | Result | Check and scope |
| --- | --- | --- |
| Internal-server-error page and persistent logs | PASS | Injected 500 yields request reference and matching route/traceback in `logs/reelfactory.log`. |
| Caught provider/build errors logged | PASS | Failure injection tests and browser log artifact. |
| Log rotation, restart persistence and key redaction | PASS | `test_web_diagnostics`; configured environment/.env keys absent from log. |
| Invalid paths, missing products and malformed inputs | PASS | Route/configuration regression suites; this is not a penetration test. |
| Less crowded default UI | PASS | One language editor, collapsed Save/Rewrite/library management/appearance, optional photo analysis and product management. Desktop/mobile screenshots inspected. |
| Mobile width and visible step names | PASS | Chromium at 390px; no horizontal document overflow. Actual mobile devices not tested. |
| JavaScript errors and JavaScript-disabled fallback | PASS | No uncaught errors; generation/editing remains usable without JavaScript. |
| Full assistive-technology/browser matrix | Partial | Keyboard tabs checked; screen readers, Safari, Firefox and touch gestures not audited. |
| CLI parsing, output paths, batch building | PASS | CLI/preflight/configuration tests and real `build_one` renders. |
| Calendar creation, validation and queue state | PASS | `test_scheduler` checks dates, supported values, corruption and missed entries. |
| Scheduled render preparation and retry handling | PASS | Disposable scheduler tests; rendering/publishing substitutes used for state transitions. |
| Folder publisher | PASS | Actual file copy of video/caption and checklist verified by tests. |
| Dry-run and unsupported-platform messages | PASS | State/retry tests; dry-run never represents a real upload. |
| Facebook/Instagram/YouTube upload | Not implemented | Connectors are explicitly unwired; manual upload required. |
| Windows launcher conflict handling | FIXED / PASS | New helper checks executable and command before stopping a listener. Verified wrong-service refusal leaves the process running, then used matching-service restart successfully on the local app. Batch launcher starts from its own directory; full double-click startup/model-download flow was not run. |
| OS installation and Task Scheduler registration | Partial | Existing preflight tests pass; installer scripts and actual OS scheduled-task registration were not run. macOS/Linux were not tested. |
| Avatar generation, catalog imports and analytics | Not implemented | Proposed scope only; not represented as working features. |

## Reproduce the audit

Final app restart verified HTTP 200 and request IDs on the dashboard, Brand,
and both existing product build pages. The new Narration/Video appearance UI
was present. Python compilation, JavaScript syntax, dependency consistency and
`git diff --check` also passed.

See [README testing instructions](README.md#checking-it-still-works).
`scripts/audit_browser.py` and `scripts/audit_render.py` work with temporary
products and do not call cloud services. `scripts/audit_services.py` is an
explicit live check and may consume quota. Generated artifacts are ignored by
Git; this checklist is the durable, reviewable record of the results.

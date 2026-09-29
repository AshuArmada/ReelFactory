# Reel Factory: engineering audit and market assessment

Audit date: 5 September 2026. Scope: this repository and its local Windows workflow. This is a code/test audit and desk-research comparison, not a production security certification or a measured advertising-performance benchmark.

## Outcome

The complete automated suite passed with **276 tests and zero skips** using real FFmpeg. The local product-to-video pipeline works under the tested scenarios. Several correctness, safety, scheduling, and maintenance issues were fixed. This does not establish that every possible input, browser, or external provider works.

The product is a credible local-first reel builder for Indian retailers, not yet a feature-equivalent replacement for the leading cloud ad-creation platforms. Its best opportunity is to make accurate, affordable, repeatable product advertising exceptionally easy for that specific customer.

## Changes made

| Area | Finding and correction |
| --- | --- |
| Web filesystem safety | Centralized rejection of unsafe product/output slugs and guarded output deletion with resolved-path containment checks. Added traversal regressions that preserve sentinel files. |
| Render metadata | Dimension and signal-stat caches could remain stale when a media file was replaced under the same name. They now check modification time and size before reuse. This is not a content-hash guarantee for deliberately preserved timestamps and sizes. |
| Calendar round-trip | YAML 1.1 interprets unquoted aspect ratios such as `4:5` as integers. New CLI calendars quote ratios; the loader supports old generated values and rejects unsupported values. |
| Input errors | Added calendar product/language/aspect validation, clearer malformed-YAML errors, and rejection of non-object queue-state JSON. |
| Publishing retries | Unwired publishers now raise a specific permanent error; temporary publishing failures remain retryable. No real social connector was added. |
| Credential handling | Gemini authentication moved from URL parameters to the API-key header. Stock connection errors no longer echo the raw exception URL. Regression tests cover these paths; this is not an exhaustive log-redaction audit. |
| Unnecessary code | Removed the shadowed duplicate Windows-font lookup, its unused cache, the unused voice-gap helper, unused imports/locals, and an unused CSS selector. Shared configuration now supplies web tone/language choices and CLI render presets. |
| UI | Dashboard card actions can wrap to avoid crowding at narrow card widths. |
| Integration tests | Corrected obsolete render-plan tuple unpacking; timing probes now use production role-based pauses and automatically clean up their temporary audio directory. |
| Documentation | Corrected the claim that Edge TTS works offline. Silent output can be offline; Edge requires internet access. |

No product assets, brand configuration, credentials, or existing rendered outputs were intentionally deleted or rewritten. Removed source code is recoverable through the Git diff/history. Changes were not committed or pushed.

## Verification and limits

- Full command: `python -m pytest -o addopts= -q --tb=short`. FFmpeg/ffprobe were explicitly available on PATH. The final run, after the last timing-helper/CSS adjustment, returned `276 passed in 179.23s (0:02:59)` with no skips or failures.
- Tests exercise component behavior, Flask routes, input handling, script/media selection, rendering, real decoded video frames, calendar/state handling, folder/dry-run publishing, and retry classification. External responses are mocked where applicable.
- Python compilation, JavaScript syntax checking, dependency consistency (`pip check`), and Git whitespace checking passed during the audit. No project-wide type-checker or dedicated linter was configured/run.
- Browser inspection covered the dashboard, brand form, product editor, build page, and stock-photo page at 390px and 1440px widths. Visible controls were checked for labels and duplicate IDs; the pages had no document-level horizontal overflow. This was not an exhaustive keyboard, screen-reader, all-wizard-state, or cross-browser accessibility audit.
- A small internal dashboard action-row overflow prompted the wrapping fix. The post-change browser launch was blocked by the execution policy, so that final CSS adjustment is source-reviewed but not browser-reverified.
- Cloud script generation, cloud TTS, stock downloads, quotas, and real credentials were **not tested live**. No paid generation, public posting, or ad spend was performed.
- Facebook, Instagram, and YouTube publishers remain explicit stubs. Folder and dry-run publishing do not prove real social publishing works.
- Further hardening should cover timezone-aware schedules, malformed nested queue-state rows, atomic state persistence/concurrent runners, and hostile or unusually large media inputs. Keep the Flask development app local; this audit does not authorize exposing it publicly.

The Windows FFmpeg package was upgraded during setup. Fresh shells may be needed for PATH changes; this audit used the explicit installed binary directory.

## Similar products and where they are ahead

Features below are vendor-documented as checked on the audit date, not independently benchmarked. Plan availability, prices, and marketing claims may change. No claim of better visual quality, conversion rate, or return on ad spend is supported by this research alone.

| Product | Relevant competing capabilities | Where Reel Factory currently trails |
| --- | --- | --- |
| Creatify | Product-ad creation, AI actors, large template library, multiple languages, competitor tracking and ad-launch/performance tooling on applicable plans. Starter is advertised at $39/month with 100 credits. [Official pricing and feature comparison](https://creatify.ai/pricing) | Creative variety, actor-led ads, competitor intelligence, distribution/measurement, and team workflows. |
| Pippit by CapCut | Commerce-focused content creation, product-link workflows, avatars, editing and business-content tools. [Official product overview](https://www.pippit.ai/resource/help-center/about-pippit) | Merchant onboarding, catalog-to-content convenience, editor breadth, and integrated commerce workflows. Publishing coverage should be checked by account/region: official pages are not fully consistent about supported platforms. |
| Predis.ai | Product-link/image video generation and ecommerce-oriented social content. [Official product-video page](https://predis.ai/product-video-maker/) | Product ingestion and broader social-content workflow; Reel Factory is more narrowly focused on locally rendered reels. |
| Invideo | Product-link/manual inputs, AI actors, generated product imagery/B-roll, 50+ languages, and natural-language editing. [Official product-video page](https://invideo.io/make/product-video/) | Generated scenes, actor-led creative, language breadth, and editing convenience. |
| Arcads | AI-actor-led advertising and scalable ad-creative production. [Official product site](https://www.arcads.ai/) | UGC-style actor creative and ad-variation breadth; a template reel is not an equivalent substitute for an actor demonstration. |
| HeyGen | Avatar-led UGC ads and scalable creator-style creative. [Official UGC-ad page](https://www.heygen.com/business/marketing/ugc-video-ads) | Presenter-led advertising and avatar workflows. |

## Defensible strengths today

These are advantages for particular customer needs, not claims of exclusive capabilities or universal superiority.

1. **Local rendering and explicit provider choices.** Users can keep rendering and their source media on their own machine. Selecting cloud analysis, scripting, stock, or TTS can send data externally; the entire application is not automatically offline/private.
2. **No per-export local credit meter.** Repeated local template/silent renders do not consume a hosted video-generation allowance. Hardware, setup, operating time, and optional cloud services still cost money. Total cost has not been benchmarked against competitors.
3. **Direct control over real product assets.** Saved scene/photo selections, scripts, and configuration make revisions inspectable and repeatable. Competitors also support product uploads, so this is a control/reproducibility advantage rather than an exclusive feature.
4. **Indian-retail orientation.** Hindi/Indian-English copy and call, visit, WhatsApp, and festival-oriented messaging fit a narrower merchant use case. Hindi support itself is not a competitive moat.
5. **Transparent, extensible pipeline.** Local Python/FFmpeg code, media-quality warnings, restrained color matching, and replaceable providers/templates are useful for technical users and custom workflows. They do not yet replace polished merchant onboarding.

## Major wins to build next

| Priority | Proposed win | Smallest useful delivery and success measure |
| --- | --- | --- |
| 1 | First reel without technical setup | Package installation and FFmpeg checks; guided brand setup; drag-and-drop photos; CSV/catalog import with an explicit preview. Measure median time to first approved export and setup failure rate. |
| 2 | India-specific merchant workflow | Test Hinglish plus two regional languages with native speakers; add festival/offer packs and WhatsApp-ready exports. Measure merchant approval rate and correction time, not just generation count. |
| 3 | Trustworthy product facts | Lock approved price/model/specification fields; show script claims against source facts; require approval for unsupported claims; preserve a per-export source manifest. Measure factual error rate on a merchant-reviewed sample. Current prompts are not factual verification. |
| 4 | Useful creative variety | Add meaningfully different demo, review, split-screen and product-detail layouts; generate controlled hook × offer × CTA variants with stable IDs. Measure how many variants merchants approve as genuinely distinct. |
| 5 | Learn which ads bring enquiries | Add tagged links/variant IDs and manual results import first; connect platform analytics later. Track enquiries and conversion alongside CTR. Run matched-budget comparisons before claiming performance superiority. |
| 6 | Reliable agency workflow | Multi-brand projects, review/approval, resumable batches, and properly authenticated social connectors. Measure successful unattended batch completion and publish reliability. Platform approval and account permissions require separate work. |

Recommended positioning: **“Local-first product reels for Indian businesses: real products, approved facts, repeatable campaigns.”** Validate this with 5–10 merchants using the same product assets and brief in Reel Factory and competing tools. Compare setup effort, correction effort, cost per approved export, factual errors, and blind creative preference. Broad avatar/generative parity is unlikely to be the most efficient first investment.

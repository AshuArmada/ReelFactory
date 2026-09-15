# Reel Factory

Add product photos and a few facts in the browser, or use the CLI. You get a
narrated vertical video with on-screen text and a ready-to-paste Facebook
caption — in Hindi and English, from the same source material.

Video rendering happens on your machine. Cloud script writers, online voices,
stock searches, and optional photo analysis use external services. The default
`edge` voice requires internet access; see [Privacy and offline use](#privacy-and-offline-use).

Start the browser workspace after installation: `python -m reelfactory serve`,
then open `http://127.0.0.1:5000`.

See the [feature checklist](FEATURE_CHECKLIST.md) for the 15 September 2026
audit: verified flows, bugs fixed, live provider results, and remaining limits.

---

## 1. Install (once)

Run commands from the repository root. You need Python (the audit used 3.12),
FFmpeg and ffprobe on PATH, and fonts for the languages you render.

**Windows:** double-click `setup_windows.bat`. It checks Python, installs
FFmpeg via winget if missing, and installs the Python packages. If it installs
FFmpeg, open a new terminal and run the setup script again.

**macOS / Linux:** install FFmpeg first, then run `bash setup.sh`.
Use `python3` instead of `python` below if that is your interpreter command.

For an isolated Python environment (recommended), create and activate it before
running setup or installing requirements:

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Check the installation:

```text
python --version
ffmpeg -version
ffprobe -version
python -m reelfactory --help
```

If `brand.yaml` does not exist, copy `brand.example.yaml` to `brand.yaml` and
edit it. Do not overwrite an existing client's configuration. The product paths
in this guide are examples, not bundled demo assets: create a product in the
browser or follow step 3 before running a build.

### What works today

- Hindi and English scripts, narration, on-screen text, and captions.
- Four output shapes: `9:16`, `1:1`, `4:5`, and `16:9`.
- Three visual templates, product photos and clips, music, and brand styling.
- Browser editing, named saved scripts, photo analysis snapshots, and hook variants.
- Batch CLI rendering and a calendar with folder export or dry-run publishing.

Facebook, Instagram, and YouTube publishing are **not connected**. Upload the
finished files manually. Avatar generation, catalog imports, and performance
analytics are not implemented; proposed work is documented in the
[audit and market assessment](AUDIT_REPORT.md).

## Prefer a browser?

Start the local workspace instead of editing YAML by hand:

```
python -m reelfactory serve
```

Open `http://127.0.0.1:5000`. It lets you create products, upload and order
photos or clips, find stock images, edit scripts line by line, choose a visual
look, compare opening-line variants, and build videos. Everything still stays
on your machine except the cloud features you explicitly choose: AI script/TTS
requests, stock-photo searches, and the **Analyze photos** action described
below.

On Windows, `start_windows.bat` also starts Ollama when installed. Its restart
helper checks the listening process before stopping it; an unrelated program
on the required port is left running and startup stops with an explanation.
Use `python -m reelfactory serve --port 5001` if another program needs port 5000.

Keep the server bound to `127.0.0.1`. This is a local workspace, not a hardened
public hosting service; do not expose the development server or debug mode to
the internet.

Failures are recorded in `logs/reelfactory.log` beside `brand.yaml`, including
timestamp, request reference, route and traceback. Internal error pages show
the matching reference; every response also has an `X-Request-ID` header.
Logs survive restarts and rotate at 2 MB, keeping five older files. Request
bodies, query strings and headers are not logged, and configured secrets are
redacted. Logs stay local and are excluded from Git.

### Create and revise a reel

1. Create a product through **Basics → Photos → Details**. Add selling points
   for the languages you need. Upload images or clips, order them with arrows,
   drag them, or edit their position numbers, then save. Saving keeps the current step.
2. Open **Build a reel**. Choose the language and writer, then generate a draft
   on **Script**. Hindi and English have separate tabs so only one editor is
   visible at a time. Both remain part of the draft.
3. Edit spoken lines and on-screen text. Select an image or clip for each scene;
   scene arrows move its words and media together. Add or remove scenes as needed.
4. Open **Rewrite with instructions**, describe the change, and choose whether
   to rewrite one language or both. Gemini, Grok and the local writer receive
   the original draft, scene media names, product facts, brand details, and
   current photo analysis as context. A failed rewrite keeps your working draft
   and instructions. The built-in writer uses fixed patterns and cannot follow
   free-form instructions; choose an AI writer for this action.
5. Open **Save a version** to name and save the draft. Load it later from
   **Saved scripts for this product**. Saving retains words, image choices,
   writer and instructions. Deleting a saved version keeps your current draft
   and returns to Script. Saving is explicit; do it before leaving the page.
6. On **Review**, choose output shapes and narration. **Video appearance** holds
   quality, visual look and music options. Build, play the result, save the video,
   and copy its caption. Earlier outputs are under **Finished videos and captions**.

To compare openings, generate variants, tick at least one for each selected
language, and confirm the selection. Selecting multiple versions builds each
one; a render failure keeps those selections available to retry.

**More script actions → Clear current draft** clears the editor.
**Saved scripts for this product → Manage saved scripts → Clear all saved scripts**
clears that product's saved library. Both ask for confirmation and stay on Script;
neither deletes product media or finished videos. Finished files have their own
selection and delete controls.

Only chosen scene media appears in an edited script's video. If a chosen file
has since been deleted, the renderer falls back to an available product image.
Bold and Premium replace the last scene's image with a brand end card; choose
Classic to keep the selected image in that scene.

### More natural narration

Under **Narration**, try Normal or Relaxed pace for Edge.
Gemini defaults to conversational delivery and accepts custom tone and pace
instructions (requires a Gemini API key). Voice quality still depends on the
provider, chosen voice and script; listen to a rendered sample to judge it.
Only controls supported by the selected provider are shown. Brand settings
hold the persistent voice names; the build page can override pace and delivery.

Live checks produced playable English and Hindi audio from Edge, gTTS and
Gemini. This confirms service operation, not a subjective naturalness score.

### Privacy and offline use

| Feature | Network/data behavior |
| --- | --- |
| Template scripts and FFmpeg rendering | Run locally. |
| `--script local` | Sends product context to the configured model endpoint. It stays local only when that endpoint is local; download the model beforehand. |
| `--script ai` / `--script grok` | Send script context to the selected cloud provider. |
| `--tts edge` / `gtts` / `gemini` | Send narration text to an online voice service. |
| Analyze photos | Sends selected supported images to Gemini when requested. |
| Stock search/download | Contacts Pexels/Pixabay and downloads selected media. |

For a visual-only draft without network services:

```text
python -m reelfactory build products/iron-shelf-5-tier --script template --tts silent --no-music --preset ultrafast
```

`silent` produces no spoken narration. Cloud service availability, costs, and
quotas depend on the provider. Review generated copy and photo descriptions
before publishing: prompt instructions are not factual verification. Keep keys
in environment variables or a private `.env` beside `brand.yaml`; never commit
credentials. See `.env.example` for supported settings.

**For Hindi on-screen text** you need a Devanagari font. Windows 10/11 already
has *Nirmala UI*. Otherwise install
[Noto Sans Devanagari](https://fonts.google.com/noto/specimen/Noto+Sans+Devanagari).
If the font is missing, the tool warns you and the Hindi text renders as boxes —
the voiceover is unaffected.

For the English look, install [Montserrat](https://fonts.google.com/specimen/Montserrat)
or [Poppins](https://fonts.google.com/specimen/Poppins). Without them it falls
back to a system font, which is fine but plainer.

---

## 2. Set up the client (once per client)

Edit `brand.yaml`: business name, city, phone, colours, and optionally a logo
PNG and a background music track.

Use music you have permission to include in the intended advertisement and
distribution channels. Review the track's license and retain its source details.

---

## 3. Add a product (once per product)

```
products/
  iron-shelf-5-tier/
    product.yaml
    photos/
      1.jpg   2.jpg   3.jpg   4.jpg   5.jpg
```

Photos are used in filename order, so number them in the order you want them to
appear. Five to eight good photos is the sweet spot.

**Photo size and shape is the single biggest thing you control.** A reel is
tall (9:16), and each photo is centre-cropped to fill it and then slowly zoomed
into. So:

- **Shoot portrait.** A landscape photo keeps only about a third of its width —
  whatever was at the sides is simply not in the video.
- **Shoot big.** 1404×2496 or larger stays sharp all the way through the zoom.
  Anything smaller is being enlarged, and "the video looks blurry" is nearly
  always this.

The tool tells you when a photo falls short — on the product page next to the
photo itself, on the build page before you spend the time, and in the terminal
during a build. It never stops you; a soft photo still makes a video.

To use a different order without renaming files, list the filenames under
`photo_order:` in `product.yaml` (the web UI writes this for you when you drag
the photos around). Anything you leave out of the list follows it in filename
order, so adding a photo never means rewriting the list.

### Let the script writer understand the photos

Uploading a photo does not silently send it anywhere. After saving the product,
use **Photo understanding → Analyze photos** on its edit page when you want
Gemini to describe the still images. This requires `GEMINI_API_KEY` and uses
the `gemini_script_model` setting (`gemini-2.5-flash` by default).

The analysis produces a short description for every JPG, PNG or WebP and one
combined visual summary. Review and edit the combined summary on the same page.
The result is cached in `photo_analysis.yaml` beside `product.yaml`; API quota
is used only when you press **Analyze** or **Refresh**, not on every script.

Use **Save for future** to give the current analysis a name. These product-local
snapshots are kept in `saved_photo_summaries.yaml` with the per-photo summaries
and SHA-256 fingerprints, and can be restored or deleted from the Photos step.
Restoring a snapshot made from different photo files marks it **Refresh needed**
and keeps it out of prompts, even if the filenames happen to be the same.

Every AI writer (Gemini, Grok, or a local model) receives a fresh combined
summary through the shared script prompt. The offline template writer does not
use it. The prompt labels the descriptions as visual observations and forbids
turning them into unsupported claims about material, capacity, price, warranty,
or performance.

The cache records a SHA-256 fingerprint for every analyzed image. Adding,
deleting, or replacing a photo—even under the same filename—marks the analysis
**Refresh needed** and keeps it out of prompts until it is regenerated. Video
clips and BMP files remain usable in the reel but are not sent for analysis.
Inline analysis accepts files below 12 MB each.

### No photos of your own? Fetch free ones

`reelfactory photos` searches Pexels and Pixabay and drops the results
straight into a product's `photos/` folder. Review each asset's license and
restrictions before publishing; a search result is not blanket clearance for an
advertisement. Use actual product photos when a stock image could misrepresent
what the client sells:

```
# see what a search finds, download nothing
python -m reelfactory photos products/iron-shelf-5-tier -q "steel shelving" --list

# fetch six, skipping anything too small to stay sharp in a reel
python -m reelfactory photos products/iron-shelf-5-tier -q "steel shelving" -n 6 --sharp

# just fill a folder, no product involved
python -m reelfactory photos --to ./scratch -q "grocery store aisle" -n 20
```

Every result is measured *as it will arrive on disk* and run through the same
"will this look soft in a reel" rule as the photos already in a product, so
the sizes printed next to the results mean exactly what the warnings on the
product page mean. Photos are added after the ones already there and never
overwrite them. Where each came from is recorded in `photo_credits.yaml` next
to `product.yaml` so you retain the source information for later review.

The web UI has the same thing on the product's **Photos** step: *Find free
stock photos* → search → tick the ones you want.

| Flag | Default | Notes |
|---|---|---|
| `--query` / `-q` | required | plain words work best: `"grocery store aisle"` |
| `--count` / `-n` | `8` | how many to fetch |
| `--source` | both | `pexels`, `pixabay`, or both |
| `--orientation` | `portrait` | reels are tall; `landscape`, `square` and `any` also work |
| `--sharp` | off | skip anything the render would have to blow up |
| `--list` | off | show the results and download nothing |
| `--to` | — | a plain folder instead of a product |

**Set up a key once.** Both are free and take about a minute
([Pexels](https://www.pexels.com/api/),
[Pixabay](https://pixabay.com/api/docs/)). Either one on its own is enough;
with both, results from the two are interleaved. Put them in the `.env` next
to `brand.yaml` (see `.env.example`) or set them as environment variables:

```
PEXELS_API_KEY=your-key-here
PIXABAY_API_KEY=your-key-here
```

Like every other key here, they are never read from `brand.yaml`.

One thing to know: Pixabay's public download is capped at 1280px on the long
side, so its photos are usually flagged as too small for a reel even though
its API describes a much larger original. Pexels serves the full-size file.
With `--sharp` on, most of what survives will be from Pexels.

**Short clips work too.** Drop an `.mp4`, `.mov`, `.m4v` or `.webm` into the same
`photos/` folder and it is used like any other shot — three seconds of someone
handling the product is worth several stills. A clip keeps its own movement
instead of getting a camera move, is trimmed to fit its slot (or looped if it is
shorter), and its sound is dropped, since the voiceover owns the soundtrack.

Create `products/iron-shelf-5-tier/product.yaml` with your own verified facts:

```yaml
name_en: "Five-tier shelf"
name_hi: "पाँच शेल्फ वाला रैक"
usp_en:
  - "Five shelves for everyday storage"
usp_hi:
  - "रोज़मर्रा के सामान के लिए पाँच शेल्फ"
```

Both names are required. Supply benefit lines in each language you intend to
render, and place your media in the sibling `photos/` directory.

**Preview the copy before spending render time:**

```
python -m reelfactory script products/iron-shelf-5-tier
```

That prints the full narration, the on-screen text and the caption for both
languages. Edit `product.yaml` and re-run until it reads well.

---

## 4. Build

```
# both languages, vertical
python -m reelfactory build products/iron-shelf-5-tier

# every product in the folder, vertical + square
python -m reelfactory build products --aspect 9:16,1:1

# Hindi only, quick draft to check the timing
python -m reelfactory build products/iron-shelf-5-tier --lang hi --preset ultrafast
```

No logo ships with the project. Drop the client's logo (a transparent PNG works
best) next to `brand.yaml` and point `logo:` at its filename, or leave
`logo: null` to go without — `watermark: true` then shows the brand name
faintly instead.

Output lands in `out/<product>/`:

```
iron-shelf-5-tier_hi_9x16.mp4      <- Facebook Reel / Story
iron-shelf-5-tier_en_9x16.mp4
iron-shelf-5-tier_hi_caption.txt   <- paste into the post
iron-shelf-5-tier_en_caption.txt
```

Building the same thing again never overwrites what is already there — the
second render of a product/language/shape is saved as `..._9x16_2.mp4`, the
third as `_3`, and so on. Tweaking a line and rebuilding therefore cannot cost
you the take you preferred; delete the ones you don't want when you're done
(the web UI has a button for it).

Expect roughly one to three minutes per video on a normal laptop. Use
`--preset ultrafast` for drafts and the default for the version you post.

### Options

| Flag | Default | Notes |
|---|---|---|
| `--lang` | `hi,en` | `hi`, `en`, or both |
| `--aspect` | `9:16` | `9:16` reels, `1:1` feed, `4:5` feed, `16:9` |
| `--tts` | `edge` | `edge`, `gtts`, and `gemini` need internet; `silent` makes a visual draft without narration |
| `--script` | `template` | `template` (offline, free), `ai` (Gemini-written), `grok` (Grok-written) or `local` (written by a model running on your machine) |
| `--preset` | `medium` | `ultrafast` for drafts, `slow` for final quality. Each preset carries its own quality level, so slower really does look better, not just take longer |
| `--crf` | from preset | override that quality. Lower is better and bigger: `16` excellent, `23` a rough draft |
| `--template` | inherited | explicit flag, then product setting, then brand default, then `classic`; bundled looks: `classic`, `bold`, `premium` |
| `--variants` | `1` | render N versions with different opening lines |
| `--no-music` | off | skip the background track |
| `--out` | `out/` | where finished files go |
| `--keep-temp` | off | keep intermediates when something looks wrong |

---

## Build in the browser

The Build page previews the script before rendering. You can edit narration,
on-screen text, line roles, and the photo or clip assigned to each line; the
result uses those exact edits. Use **See versions to compare** to choose one or
more script variants, then build the selected versions separately.

After writing or editing a script, use **Save to this product** to store a named
copy for later. Saved scripts live in that product's `saved_scripts.yaml` and
retain the language, writer, narration, overlays, roles, and photo selected for
every line. **Saved scripts for _product-name_** remains available on the Script
step so a stored draft can be loaded or deleted without generating it again.

The page also exposes the same render choices as the command line: language,
aspect ratio, script writer, voice, picture-quality preset, music, and visual
look. Leave **Visual look** on its default to use the product setting, then the
brand default, then `classic`.

---

## AI scripts and voice (optional)

By default the tool writes copy from offline templates and speaks it with the
free `edge` voices. You can swap either piece for Gemini, independently:

```
# Gemini writes the script, edge-tts still speaks it (free)
python -m reelfactory build products/iron-shelf-5-tier --script ai

# templates write the script, Gemini speaks it
python -m reelfactory build products/iron-shelf-5-tier --tts gemini

# both
python -m reelfactory build products/iron-shelf-5-tier --script ai --tts gemini
```

**Set up the key once** (never put it in `brand.yaml` — it isn't read from
there, so it can't end up committed alongside a client's file). Easiest is a
`.env` file next to `brand.yaml`:

```
gemini_key=your-key-here
```

Or set an environment variable instead: `setx GEMINI_API_KEY "your-key-here"`
(then open a new terminal). Either way it can also be passed per-run with
`--gemini-key`.

**Backup key (optional).** Free-tier Gemini keys have low daily quotas,
especially for TTS -- add a second key as `key_backup` in the same `.env`
file and it's used automatically, but *only* as a fallback when the primary
key specifically hits a quota / rate-limit error (HTTP 429), not for other
failures:

```
gemini_key=your-primary-key
key_backup=your-second-key
```

**Grok (xAI) is also supported for scripts**, as another `--script` choice
alongside `template` and `ai`:

```
python -m reelfactory build products/iron-shelf-5-tier --script grok
```

Same idea: the key comes from `GROK_API_KEY`, a `.env` entry, or `--grok-key`
-- never `brand.yaml`. `.env` accepts either `GROK_API_KEY` or `grok_api_key`.
The model name is a normal (non-secret) setting in `brand.yaml`:

```yaml
grok_script_model: "grok-4-latest"
```

Grok is a script-only option for now -- there is no `--tts grok` voice
backend, only `--tts gemini` for AI voice.

**A local model is also supported for scripts.** With a downloaded model and a
local endpoint, script generation can stay on your machine. It talks to an
OpenAI-compatible local server, such as
[Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai):

```
# one-time setup:
winget install --id Ollama.Ollama -e   # installs Ollama and starts it as a background service
ollama pull llama3.2:3b                # ~2GB, a good fit for a 4GB laptop GPU

# then, any time:
python -m reelfactory build products/iron-shelf-5-tier --script local
```

Make sure the model server is running before using `--script local`.
By default it is called at Ollama's OpenAI-compatible
endpoint, `http://localhost:11434/v1`, and asked for the `llama3.2:3b`
model. Change either in `brand.yaml` (not secrets, so safe to commit/share):

```yaml
local_script_model: "llama3.2:3b"
local_base_url: "http://localhost:11434/v1"   # LM Studio default: http://localhost:1234/v1
```

If your GPU has more headroom, swap in a larger model
(`ollama pull llama3.1:8b`, then set `local_script_model: "llama3.1:8b"`)
for better writing quality at the cost of speed.

or override per-run with `--local-model` / `--local-url`. No key is needed
for most local servers; if yours requires one, pass `--local-key` or set
`LOCAL_LLM_API_KEY`. Like Grok, this is a script-only option. Pair it with
`--tts edge` for free narration that requires internet, or `--tts silent`
for a fully offline visual draft without narration.

**What each does:**

- `--script ai` sends the product's facts (price, warranty, USPs, phone...)
  to Gemini and asks it to write the hook/reveal/USP/proof/price/CTA lines --
  it's told never to invent facts, only to phrase the given ones. `script_hi`
  / `script_en` overrides in `product.yaml` still take priority over both
  modes, same as before.
- `--tts gemini` uses Gemini's own text-to-speech instead of edge-tts. The
  voice persona and both Gemini model names are set in `brand.yaml`:
  ```yaml
  gemini_script_model: "gemini-2.5-flash"
  gemini_tts_model: "gemini-2.5-flash-preview-tts"
  gemini_voice: "Kore"     # try: Puck, Charon, Fenrir, Aoede, Leda, Orus...
  ```

Preview an AI script without rendering (same as the normal preview, just add
the flag): `python -m reelfactory script products/iron-shelf-5-tier --script ai`

---

## 5. Post to Facebook

1. Facebook Page → **Create post** → **Reel** (or Photo/Video for the square cut)
2. Upload the `_9x16.mp4`
3. Paste the matching `_caption.txt`
4. Post the Hindi cut to the local audience; keep the English cut for a
   second post, a different Page, or a boosted ad

Post one language at a time rather than both at once — you learn which one your
audience responds to.

---

## What this video is for (intent)

`tone` only changes *how* the ad talks. `intent` changes *what it says and in
what order* -- which beats appear, what the hook leans on, how it closes. Set
it per product:

```yaml
intent: offer   # sell | offer | launch | awareness | footfall | enquiry |
                # restock | educate | festival
```

or as a brand-wide default that products inherit unless they say otherwise:

```yaml
# brand.yaml
default_intent: sell
```

or per run, without touching either file: `--intent footfall`. Run
`python -m reelfactory build --help` for supported options.

This tool was originally built around one kind of business (hardware /
furniture), with fixed fields for `material`, `sizes`, `warranty` and
`delivery`. It now generalises past that:

- **`specs` / `specs_hi`** — any other facts as a `label: value` mapping, for
  a business that isn't selling hardware:
  ```yaml
  specs:
    seats: 40
    cuisine: "Authentic Tamil Nadu style"
  ```
- **`category`** (also settable brand-wide) — picks better hashtags than the
  generic `#smallbusiness` set, e.g. `category: restaurant` pulls in
  `#restaurant #foodie #dineout`.
- **`audience`** — who the ad is speaking to, fed to the AI script modes as
  context (`--script ai` / `grok` / `local`).
- **`offer`, `offer_ends`, `urgency`** — a deal and its deadline / scarcity;
  adds "offer" and "urgency" beats to the video automatically.
- **`proof_points`** — ready-made credibility lines ("4.8 stars from 200+
  reviews") for the "proof" beat, instead of only being built from `warranty`
  etc.
- **`cta_action`** — what the closing line asks for: `call`, `whatsapp`,
  `visit` (uses `brand.address` / `brand.hours`), `dm` (uses
  `brand.instagram`), `order_online` (uses `brand.website`), `book`,
  `comment`, or `auto` (the original phone/WhatsApp behaviour).
- **`must_say`** / **`avoid`** — phrases the AI script modes must work in or
  must never use.

None of this is required — a `product.yaml` with just `name_en` / `name_hi` /
one `usp_en` still works exactly as before, defaulting to `intent: sell` and
`cta_action: auto`.

---

## Changing how the ads look

`tone` and `intent` change the words. **Templates change the picture** -- how the
camera moves over each photo, how shots cut into one another, and how the photos
are graded. Three come with the tool:

| Template | Feels like |
|---|---|
| `classic` | Slow drift, soft crossfade, photos untouched. The original look. |
| `bold` | Fast slides, punchy colour, closes on a brand card. Suits offers and value ads. |
| `premium` | Slow dissolves, restrained colour, closes on a brand card. Suits premium and trust ads. |

`bold` and `premium` end on a **brand card** rather than on whichever photo the
slideshow happened to reach: the closing line lands centred and large on a card
in your `secondary_color`. `classic` keeps the original ending. Turn it on or
off per template with `end_card`.

Set it per product, as a brand-wide default, or for a single build:

```yaml
# product.yaml
template: bold
```

```yaml
# brand.yaml -- used by any product that does not pick its own
default_template: premium
```

```
python -m reelfactory build products/my-rack --template premium
```

Each one is a file in `templates/`. Copy any of them, change the numbers, and
the new name is available immediately -- no code to touch:

```yaml
description: "What this look is for"
moves: [in_center, out_center, in_left, pan_right, in_right, pan_left]
zoom: 0.30                    # how far the camera travels, as a fraction
transitions: [fade]           # cycled in order; any ffmpeg xfade name
transition_seconds: 0.5
grade: "eq=contrast=1.1:saturation=1.15"   # blank for no colour treatment
scrim: 0.78                   # darkness behind the text, 0 turns it off
crop_budget: 0.35             # how much of a photo a crop may discard
end_card: false               # close on a brand card instead of a photo
whoosh: 0.0                   # swish on each cut, 0 silences it
accent_hit: 0.0               # soft thump on the price beat, 0 silences it
match: 0.6                    # pull photos toward each other, 0 leaves them alone
```

**`match` is the one worth knowing about.** Client photos arrive from different
phones at different times of day: one warm, the next cool, one under-exposed.
Each is fine alone; cut together they look like several different shoots. Every
photo is measured, the set's middle becomes the target, and each is moved part
of the way there — part, and capped, so a photo that is *meant* to look
different is nudged rather than flattened. On the sample photos it pulls the
brightness spread in by about 60%. Set `match: 0` to leave photos exactly as
shot.

**Sound effects** are generated, not sampled -- there is no audio file to
license or ship. The swish is three bands of noise crossfaded low to high; the
accent hit is two low sines with a percussive decay. `bold` uses both, `premium`
only the hit, `classic` neither. Both are volumes from 0 to 1, so if they sit
too loud or too quiet under your voiceover, change the number.

The gradient behind the text is tinted with `secondary_color` from
`brand.yaml`, so the backdrop belongs to the brand rather than being flat black.

### Cutting on the beat

If you know your music track's tempo, say so and the cuts will land on it:

```yaml
# brand.yaml
music: music/upbeat.mp3
music_bpm: 96
music_offset: 0.0     # only if the track does not start on beat one
```

The pacing still follows the voice — only the silence between lines is
stretched or trimmed, by at most a quarter of a second, to bring each cut onto
the nearest beat. Words stay on their own pictures. Leave `music_bpm` at 0 and
nothing changes.

### Testing two openings

The first three seconds decide whether anyone keeps watching, so it is the part
worth testing:

```
python -m reelfactory build products/my-rack --variants 2
```

That writes the usual `_9x16.mp4` plus a `_9x16_v2.mp4` that differs only in its
opening line. Post one each week and keep the better one. Preview them without
rendering using `python -m reelfactory script products/my-rack --variants 2`.
If a product has a fixed `script_en` / `script_hi`, there is no opening to vary
and the extra variants are skipped.

---

## Changing how the ads sound

`tone: value | premium | trust` in `product.yaml` switches the opening hook.

To take full control of a specific product, set `script_hi` / `script_en` — a
list of lines, one per shot. That bypasses the templates entirely:

```yaml
script_en:
  - "This rack survived a full monsoon on an open terrace."
  - "Two millimetre iron, powder coated twice."
  - "Four thousand four hundred and ninety nine rupees, delivered free."
  - "WhatsApp us on 98765 43210."
overlay_en:
  - "One monsoon. No rust."
  - "2mm, double coated"
  - "₹4,499 delivered"
  - "98765 43210"
```

Different voices: `edge-tts --list-voices` lists every option. Set `voice_hi` /
`voice_en` in `brand.yaml`. `hi-IN-SwaraNeural` and `en-IN-NeerjaNeural` are
female; `hi-IN-MadhurNeural` and `en-IN-PrabhatNeural` are male.

---

## When something goes wrong

**"ffmpeg was not found"** — FFmpeg is not on PATH. On Windows,
`winget install Gyan.FFmpeg`, then open a *new* terminal.

**Hindi text shows as boxes** — no Devanagari font installed. See step 1, or set
`font_hi` in `brand.yaml` to a font you already have.

**"Edge TTS failed"** — no internet, or a firewall is blocking it. Try
`--tts gtts`, or `--tts silent` to check the visuals without a voice.

**Text runs off the screen** — shorten that USP in `product.yaml`, or set an
explicit short line in `overlay_hi` / `overlay_en`.

**Video feels too fast or too slow** — the pacing follows the voiceover, so
lengthen or shorten the lines. `rate_hi` / `rate_en` in `brand.yaml` change the
speaking speed (`+8%` is default; `-5%` is slower and calmer).

---

## How it works

1. `script.py` turns product facts into a hook → reveal → benefits → proof →
   price → call-to-action narration, plus a short line for the screen.
2. `voice.py` speaks each line separately, so the exact length of every line is
   known before any video is rendered.
3. `render.py` gives each photo a slow zoom or pan lasting exactly as long as
   its line, cross-fades between them, lays down a gradient, the logo and the
   burned-in text, then mixes the voice over music that ducks automatically.

The pacing is driven by the audio, which is why the text always lands on the
right photo.

If you use the scheduler, three more pieces join in: `calendar.py` reads the
queue and works out what is due, `runner.py` renders and hands each due post to
a publisher, and `publish.py` decides where it actually goes. Adding a platform
later requires a publisher implementation plus authentication, platform access,
error handling, and integration tests. See [PHASE2.md](PHASE2.md).

---

## Checking it still works

```text
python -m pip install -r requirements.txt
python -m pytest -o addopts= -q --tb=short
python -m pytest -m "not slow"
python -m pytest tests/test_end_to_end.py
python -m pytest tests/test_scheduler.py tests/test_regressions_no_ffmpeg.py
```

The complete suite needs FFmpeg **and ffprobe** on PATH. Rendering-dependent
tests skip when those binaries are unavailable, so a green result with skips
is not a full integration check. `not slow` selects a faster subset; some tests
in that subset still require media tools.

The slow ones render real videos and then read the frames back, so a change
that quietly points a line at the wrong photo, or lets a rebuild overwrite
yesterday's video, fails the suite rather than showing up weeks later in
something you posted. Tests work in a temporary folder — your own products and
finished videos are never touched.

Latest audit (15 September 2026, Windows / Python 3.12 / FFmpeg 9.0.1):
**297 passed in the final non-slow run, plus all 13 real-render tests passed
in the full run.** The full run also exposed three incomplete Hindi test
fixtures; those fixtures were corrected and passed in the final run.
The separate Chromium walkthrough passed 16 workflow checks. Live service
checks passed for eight of nine features after restarting Ollama and fixing
its response format; Pixabay downloads returned HTTP 429. See the
[full checklist and evidence](FEATURE_CHECKLIST.md).

Repeat the browser and advanced render audits on disposable data:

```text
python -m pip install playwright Pillow
python -m playwright install chromium
python scripts/audit_browser.py
python scripts/audit_render.py
```

The browser audit uses simulated AI/render failures and a real silent
photo-and-clip render. The advanced render audit uses synthetic audio to check
music, effects and end cards. Neither sends cloud requests. Screenshots,
videos and result JSON are written under `out/audit/` (ignored by Git).

Optional live checks **send synthetic data and may consume provider quota**:

```text
python scripts/audit_services.py
python scripts/audit_services.py script-local
```

Configure the appropriate keys first. Local writing also needs a running
model server and a downloaded model; its endpoint must support JSON-schema
structured output. Ollama with `llama3.2:3b` passed the live check. Other local
servers were not verified. See [Ollama structured output support](https://docs.ollama.com/capabilities/structured-outputs).

Additional checks:

```text
python -m compileall -q reelfactory tests
python -m pip check
node --check reelfactory/web/static/app.js
git diff --check
```

Node.js is only needed for that JavaScript syntax check, not to run the app.
See [AUDIT_REPORT.md](AUDIT_REPORT.md) for changes, verification limits,
competitor comparisons, and proposed priorities.

---

## Scheduling (optional)

Once the videos look right, schedule rendering and folder preparation. Actual
social posting remains manual until real platform connectors are implemented.

### The queue

`calendar.yaml` is a plain list you edit yourself. The tool never rewrites it —
status is kept separately in `out/queue_state.json` — so your comments and
ordering survive every run.

```yaml
- product: iron-shelf-5-tier
  lang: hi
  platform: folder
  aspect: '9:16'
  when: 2026-09-07 19:30
  note: first post of the week
```

Generate a starting schedule instead of typing dates:

```
python -m reelfactory plan products --start tomorrow --time 19:30 --days mon,wed,fri --lang hi --platform folder --write calendar.yaml
```

`--write` appends entries; inspect the calendar before repeating the command.
Use local machine time without a timezone suffix. Quote aspect ratios in YAML:
unquoted values such as `4:5` can be parsed as numbers. The loader accepts older
generated ratios for compatibility and rejects unsupported languages or shapes.

Then check it:

```
python -m reelfactory queue
```

### Platforms

| `platform:` | What happens |
|---|---|
| `dryrun` | Logs a simulated publication without uploading; the runner can still render files and records completion in queue state. |
| `folder` | Copies video + caption into `to_post/<date>/` with a tick-list, ready for you to upload |
| `facebook` / `instagram` / `youtube` | Not connected. Fails with a clear message — see `PHASE2.md` |

`folder` is the honest sweet spot: everything is rendered, named and organised
for you, and the thirty seconds of uploading stays under your control. Plenty of
people never move past it.

### Running it

```
python -m reelfactory run                          # publish what is due
python -m reelfactory run --prepare-only           # just render ahead of time
python -m reelfactory run --now "2026-09-07 19:30" # override the clock; still writes files/state
```

`--now` is not a read-only simulation. Use `queue` to inspect the schedule.
For isolated experiments, pass a separate `--calendar`, `--state`, `--out`, and
`--drop`, and choose `dryrun` entries; use `--tts silent --script template` to
avoid cloud generation. Completed dry-run entries are recorded as `published`
with a `dry-run:` result; that status does not mean an upload occurred.

`run` renders anything due in the next two days first, so posting time is not
render time. Re-running is safe: anything already published is left alone.

**To automate it on Windows,** run `schedule_windows.bat` and pick a time. It
registers a daily task and logs to `out/logs/run.log`.

Run it with `dryrun` for a week before connecting anything real. Watch the log,
confirm it picks the right posts at the right times, and only then switch
entries over to `folder`.

### When a run fails

A post that fails for a fixable reason (a missing photo, a bad render) is
retried on the next two runs before being marked failed. A post to a platform
that is not connected fails immediately, because retrying cannot help.

Anything more than 48 hours late is skipped rather than posted, so a laptop
that was switched off for a week does not wake up and fire off a burst of stale
posts. Change that window with `--grace`.

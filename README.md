# Reel Factory

Drop product photos in a folder, fill in a few facts, run one command. You get a
narrated vertical video with on-screen text and a ready-to-paste Facebook
caption — in Hindi and English, from the same source material.

Nothing is uploaded anywhere. Everything renders on your machine.

```
python -m reelfactory build products/sample-iron-shelf
```

---

## 1. Install (once)

**Windows:** double-click `setup_windows.bat`. It checks Python, installs
FFmpeg via winget, and pulls the Python packages.

**Mac / Linux:** `./setup.sh`

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

Music must be royalty-free. Facebook mutes or blocks videos using commercial
tracks. Safe sources: YouTube Audio Library, Pixabay Music, Mixkit.

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
appear. Shoot or crop them tall (portrait) — a 9:16 video crops the sides off
a landscape photo. Five to eight good photos is the sweet spot.

Copy `products/sample-iron-shelf/product.yaml` and edit it. Only `name_en`,
`name_hi` and one `usp_` list are required.

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

`logo_test.png` in the project root is a throwaway example logo used to check
the overlay position. Replace it with the client's real logo, or set
`logo: null` in `brand.yaml` to leave it off.

Output lands in `out/<product>/`:

```
iron-shelf-5-tier_hi_9x16.mp4      <- Facebook Reel / Story
iron-shelf-5-tier_en_9x16.mp4
iron-shelf-5-tier_hi_caption.txt   <- paste into the post
iron-shelf-5-tier_en_caption.txt
```

Expect roughly one to three minutes per video on a normal laptop. Use
`--preset ultrafast` for drafts and the default for the version you post.

### Options

| Flag | Default | Notes |
|---|---|---|
| `--lang` | `hi,en` | `hi`, `en`, or both |
| `--aspect` | `9:16` | `9:16` reels, `1:1` feed, `4:5` feed, `16:9` |
| `--tts` | `edge` | `edge` (best, free, needs internet), `gtts`, `gemini`, `silent` |
| `--script` | `template` | `template` (offline, free), `ai` (Gemini-written), `grok` (Grok-written) or `local` (written by a model running on your machine) |
| `--preset` | `medium` | `ultrafast` for drafts, `slow` for final quality |
| `--no-music` | off | skip the background track |
| `--out` | `out/` | where finished files go |
| `--keep-temp` | off | keep intermediates when something looks wrong |

---

## AI scripts and voice (optional)

By default the tool writes copy from offline templates and speaks it with the
free `edge` voices. You can swap either piece for Gemini, independently:

```
# Gemini writes the script, edge-tts still speaks it (free)
python -m reelfactory build products/sample-roofing-sheets --script ai

# templates write the script, Gemini speaks it
python -m reelfactory build products/sample-roofing-sheets --tts gemini

# both
python -m reelfactory build products/sample-roofing-sheets --script ai --tts gemini
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
python -m reelfactory build products/sample-roofing-sheets --script grok
```

Same idea: the key comes from `GROK_API_KEY`, a `.env` entry, or `--grok-key`
-- never `brand.yaml`. `.env` accepts either `GROK_API_KEY` or `grok_api_key`.
The model name is a normal (non-secret) setting in `brand.yaml`:

```yaml
grok_script_model: "grok-4-latest"
```

Grok is a script-only option for now -- there is no `--tts grok` voice
backend, only `--tts gemini` for AI voice.

**A local model is also supported for scripts**, for fully offline / free /
private script writing -- no account, no API key, nothing sent over the
internet. It talks to any OpenAI-compatible local server, such as
[Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai):

```
# one-time setup:
winget install --id Ollama.Ollama -e   # installs Ollama and starts it as a background service
ollama pull llama3.2:3b                # ~2GB, a good fit for a 4GB laptop GPU

# then, any time:
python -m reelfactory build products/sample-roofing-sheets --script local
```

Ollama runs as a background Windows service once installed, so there's
nothing to start manually -- it's just there the next time you use
`--script local`. By default it's called at Ollama's OpenAI-compatible
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
`LOCAL_LLM_API_KEY`. Like Grok, this is a script-only option -- pair it with
`--tts edge` (the default) for a completely offline, free pipeline.

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
the flag): `python -m reelfactory script products/sample-roofing-sheets --script ai`

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
later means writing one small class in `publish.py` — nothing else changes.

---

## Scheduling (optional)

Once the videos look right, you can put the posting on a calendar instead of
doing it by hand each time.

### The queue

`calendar.yaml` is a plain list you edit yourself. The tool never rewrites it —
status is kept separately in `out/queue_state.json` — so your comments and
ordering survive every run.

```yaml
- product: iron-shelf-5-tier
  lang: hi
  platform: folder
  when: 2026-08-04 19:30
  note: first post of the week
```

Generate a starting schedule instead of typing dates:

```
python -m reelfactory plan products --start tomorrow --time 19:30 \
       --days mon,wed,fri --lang hi --platform folder --write calendar.yaml
```

Then check it:

```
python -m reelfactory queue
```

### Platforms

| `platform:` | What happens |
|---|---|
| `dryrun` | Logs what it would post. Changes nothing. **Start here.** |
| `folder` | Copies video + caption into `to_post/<date>/` with a tick-list, ready for you to upload |
| `facebook` / `instagram` / `youtube` | Not connected. Fails with a clear message — see `PHASE2.md` |

`folder` is the honest sweet spot: everything is rendered, named and organised
for you, and the thirty seconds of uploading stays under your control. Plenty of
people never move past it.

### Running it

```
python -m reelfactory run                          # publish what is due
python -m reelfactory run --prepare-only           # just render ahead of time
python -m reelfactory run --now "2026-08-04 19:30" # pretend, for testing
```

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

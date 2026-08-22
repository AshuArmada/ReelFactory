# Enhancements — making the output more than a slideshow

Phase 1 produces a working narrated video. It also produces the *same* video
every time: Ken Burns motion, crossfade, bottom-centre text, fade out. One look,
no variation. A client's tenth reel is visually identical to their first, and
that is the ceiling this document is about raising.

Ordered by visual impact per unit of work. Tier 1 is what actually changes how
the output reads; everything below is polish on top of it.

---

## Already done

Two bugs found and fixed while getting the project running. Listed here only so
nobody re-investigates them.

- **`render.py`** — `"\,"` in a non-raw string raised a `SyntaxWarning` on every
  command. Now a raw string; the FFmpeg escape is unchanged.
- **`subtitles.py`** — the Windows font check compared family names against
  *filenames*, so it never found Nirmala UI (ships as `Nirmala.ttc`) and warned
  "Hindi will render as boxes" on machines that render Hindi perfectly. Now reads
  the font registry. Side effect: English correctly resolves to Segoe UI
  **Semibold** instead of silently falling back to regular weight.

---

## Verified capabilities

Both confirmed on this machine before planning around them — do not re-check.

**edge-tts returns per-word timings.** `Communicate(..., boundary="WordBoundary")`
then `.stream()` yields `WordBoundary` chunks with `offset` and `duration` in
100ns units. Default is `SentenceBoundary`, which is why we see nothing today.
Works in Hindi including conjuncts:

```
दस     0.096s  0.301s
मिनट   0.397s  0.324s
में    0.721s  0.162s
फिट    0.883s  0.312s
```

**FFmpeg 9.0 has the full xfade set** — `wipeleft/right/up/down`,
`slideleft/right/up/down`, `circlecrop`, `fadeblack`, `smoothleft/right`, and
more. We currently hardcode `fade`.

---

## Tier 1 — the three that change how it reads

### 1. Kinetic word-by-word captions — **done**

Shipped as a hybrid: `KARAOKE_ROLES` in `subtitles.py` word-times the
conversational beats (hook, reveal, usp, proof) while price / offer / urgency /
cta stay as static accent cards, because a number reads better as a poster than
as a sentence. Measured drift across a line is under 5ms.

Two supporting changes went in with it:

- `render.plan()` now returns `(start, end, speech_start)`. Karaoke is timed from
  when the audio begins, which is not recoverable from `start` — that one is
  nudged early by `LEAD` and clamped to zero on the first shot.
- `voice.concat()` pads each clip to exactly the duration `plan()` was given.
  A clip shorter than `MIN_SEG` used to let the audio run ahead of the subtitle
  timeline, and the error accumulated. Invisible with static text, obvious the
  moment words are individually timed.

Original notes below.

The single biggest "modern reel vs. slideshow" signal, and the data is already
in the TTS response — we throw it away.

- **Where:** `voice.py:124` uses `comm.save()`. Switch to `comm.stream()` with
  `boundary="WordBoundary"`, accumulate audio bytes *and* word timings, return
  them on the `Clip` dataclass. Then `subtitles.py` emits per-word `Dialogue`
  lines (or ASS `\k` karaoke tags) instead of one static block per segment.
- **Why:** text currently sits motionless for 3–5 seconds per beat. Words
  lighting up as they are spoken is what every performing Reel does.
- **Gotcha:** the final word's reported duration absorbs trailing silence
  (`₹4,499।` came back as 2.211s). Clamp the last word to clip length minus its
  start.
- **Gotcha:** only the `edge` backend provides timings. `gtts`, `gemini` and
  `silent` must fall back to the current whole-line rendering — so this has to
  be optional per clip, not assumed.

### 2. Stop centre-cropping landscape photos — **done**

Built as a *partial* crop rather than the full fit originally sketched. A plain
centre crop still runs whenever it would discard less than `MAX_CROP_LOSS`
(35%). Past that, `_framing()` crops only as far as the budget reaches and fills
the leftover with a blurred, darkened copy of the photo. A naive fit-the-whole-
photo left the subject filling 32% of a 9:16 frame; cropping to the budget first
gets that to 49% while still keeping 65% of the picture.

Worth knowing: **at 9:16 nothing changes.** Every sample photo is portrait and
still crops full-bleed, so existing reels render exactly as before. The change
shows up on the 1:1 and 4:5 cuts, where the very tall roofing photos (0.45
aspect) were losing more than half their height.

Original notes below.

A correctness problem wearing an aesthetics costume.

- **Where:** `render.py:98-99`, `scale=increase,crop`.
- **Why:** fine for portrait input. For the landscape phone shots clients
  actually send, it discards ~60% of the frame and can slice the product in
  half.
- **Fix:** scale-to-fit the whole photo over a blurred, darkened copy of itself.
  `split` → one branch `scale=increase,crop,boxblur`, other branch
  `scale=decrease` → `overlay=centered`. ~6 lines of filtergraph.

### 3. A visual template layer — **done**

`templates/*.yaml`, loaded by `templates.py`, chosen by `template:` in
product.yaml → `default_template:` in brand.yaml → `classic`. Also `--template`
on the CLI and a "Look" selector in the web UI. `classic` is additionally
hardcoded as the dataclass defaults, so the tool still renders correctly with
the templates folder deleted.

A template controls: camera moves, zoom travel, transition list, transition
length, colour grade, scrim strength, crop budget. Shipped with `classic`
(the original look), `bold` and `premium`.

**One deliberate change to `classic`:** the scrim is now tinted with
`brand.secondary_color` rather than always pure black, which makes a previously
dead setting mean something. At the default `#0B0B0F` that shifts the darkest
part of the gradient by about 9/255 — subtle, but not byte-identical to before.
Set `secondary_color: "#000000"` for the old behaviour exactly.

Original notes below.

The enabler. Without it every item below is another hardcoded branch.

- **Where:** extract the hardcoded choices in `render.py` into `templates/*.yaml`
  — transition set, motion set, text position/style, colour grade, scrim
  treatment, end card on/off.
- **Selected by:** `template:` in `product.yaml`, falling back to `brand.yaml`,
  falling back to a default. Same precedence pattern as `intent` /
  `resolve_intent()`.
- **Why:** one look means every client's videos are interchangeable. This is
  also what lets a template be added without touching render code.

---

## Tier 2 — cheap, disproportionate effect

### Transition variety — **done**
Came with the template layer. `transitions:` is a list, cycled in order across
cuts (deterministic, so no RNG needed). Any of ffmpeg's ~55 xfade names is
valid and the name is checked at load time.

### Colour grade pass — **partly done**
`grade:` on a template applies one filter string per shot, and `bold` /
`premium` use it. What is *not* done is the original point of this item:
**matching photos to each other**. The grade is currently the same for every
photo, so a warm photo next to a cool one is still warm next to cool. Per-photo
auto-white-balance is a separate, harder job.

### A real end card
The CTA currently lands on whatever photo the cycle happens to reach. A
dedicated final frame — brand colour, logo, phone, CTA — is ~30 lines and is the
difference between the video ending and the video finishing.

### Sound design
A soft whoosh on transitions, one accent hit on the price reveal, mixed at
offsets `plan()` already computes. Trivial effort, real perceived polish.

### Photo repetition
`cli.py:384` assigns photos with `photos[i % len(photos)]`. Five photos across
nine segments means photos 1–4 each appear twice — and because `MOVES` cycles on
a different period, sometimes with similar motion. Pair each repeat with a
deliberately different move, or bias repeats toward the hero shots.

---

## Tier 3 — bigger swings

### Beat-synced cutting
Pacing is 100% voice-driven today, which is exactly why it feels narrated rather
than produced. Declare `bpm` next to the track in `brand.yaml`, then in
`render.py:40` `plan()` nudge each transition to the nearest beat by adjusting
the inter-segment pause within ±0.25s. Text still lands on the voice; cuts land
on the music. **Only worth doing once music is actually in use** — `music` is
`null` today.

### Accept video clips, not just stills
Allow `.mp4` in `photos/`. Three seconds of someone flexing the shelf beats any
five stills. Bypass zoompan, trim/loop to the segment duration.

### Two hooks per build
The first three seconds decide retention. The seeded RNG makes variants cheap —
render two openings and let the client post whichever performs.

---

## Suggested order

1. ~~Kinetic captions~~ done
2. ~~Blurred fill~~ done
3. ~~Template layer~~ done
4. ~~Transitions~~ done; colour grade partly — per-photo matching still open
5. End card, sound design — **next**
6. Re-evaluate Tier 3 against what the first client actually reacts to

---

## Open questions

- ~~**Do captions replace or complement the current overlay text?**~~ Settled:
  hybrid by role. Conversational beats karaoke the full spoken line; price,
  offer, urgency and cta keep the short static card. See `KARAOKE_ROLES`.
- **Is the dim/bright contrast right?** Unsung words sit at alpha `0x78`. Legible
  and clearly distinct in both languages, but it is one number in
  `subtitles.write()` if it wants to be stronger.
- **Is 35% the right crop budget?** It is one constant, `MAX_CROP_LOSS`. Lower
  means more photos get blurred bands; higher means more gets cut off. Worth
  revisiting once real client photos are in, not before.
- **Should crop-vs-fill be a template choice?** A full-bleed crop is more
  immersive when the subject is centred. Once templates exist this could be per
  template rather than one global constant.
- **How many templates before it stops looking samey?** Guessing three. Worth
  building one properly and judging from frames rather than committing to a
  number up front.
- **Does the template pick the tone, or does the tone pick the template?**
  `tone` already exists and means something adjacent. Risk of two overlapping
  concepts.

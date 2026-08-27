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

### Colour grade pass — **done**
Two separate things, both now in place.

`grade:` applies one filter per template, giving each look its character.

`match:` (default 0.6, on for all three templates) does the harder half:
`_match_colours()` measures every photo with `signalstats`, takes the set's
**median** as the target, and moves each photo a fraction of the way there.
Capped at ±18/255 brightness and ±10 colour so a deliberately different photo
is nudged, not flattened.

Measured on the seven roofing photos: brightness spread 61% tighter (sd
12.1 → 4.7), U 63% tighter, V 54% tighter — and the reddest photo is still the
reddest, which is the cap doing its job.

**This changes `classic` too.** It is a correction to bad input rather than a
style, so it is on everywhere, the same call made earlier for the blurred fill.
`match: 0` restores exactly-as-shot.

Notes: a single-photo product is skipped (nothing to match against), the end
card is excluded, and clips are matched from their first frame.

### A real end card — **done**
`end_card: true` on a template swaps the final photo for a card in the brand's
`secondary_color`, and the closing line renders centred and large on it via a
new `EndCard` subtitle style. On `bold` and `premium`; `classic` keeps the
original ending.

The card is only ever the *ground* — the logo, the brand-name kicker and the
scrim are laid over it by the normal composite, so nothing needed special
casing. `Shot.still` marks it so it skips framing, camera move and grade.

Worth remembering: a gradient this gentle spans about 30 brightness levels over
1920px and bands visibly. It is dithered with a seeded `noise` pass, which is
the only reason it looks smooth.

### Sound design — **done**
`whoosh` and `accent_hit` on a template, both 0-1 volumes. Effects are
*synthesised* by ffmpeg rather than shipped as samples, so there is nothing to
license: the swish is three pink-noise bands crossfaded low to high (a real
rising sweep -- measured, the low band leads by 12dB at the start and the high
band by 10dB at the end), and the hit is two sines with an exponential decay.

They are rendered into a single `sfx_*.wav` placed at the right offsets, then
mixed as one extra input, which keeps the composite filtergraph readable. When
both volumes are 0 no track is built at all and the audio graph is byte-for-byte
what it was, which is how `classic` stays unchanged.

**Not verified by ear.** Placement and levels were checked numerically (every
cut carries the swish, silence between, final mix peaks at -7.7dBFS so nothing
clips) but whether it actually *sounds* good is still an open question. The two
volumes are the knobs.

Also worth knowing: a segment boundary is also a cut, so on `bold` the price
beat gets the swish and the hit together, about 2dB louder than a plain cut. It
reinforces rather than clashes, but it is not one sound.

### Photo repetition — **done**
`_assign_moves()` in `render.py` gives every shot a camera move, starting from
where the plain rotation would land and stepping on only if that photo has had
that move before. Verified across 3 templates x 9 photo counts x 11 segment
counts: **zero** cases where a photo gets the same move on two appearances in a
row, and when there are enough photos the assignment is byte-identical to the
old rotation.

The round-robin photo order was left alone — it already biases repeats toward
the earlier (usually better) photos and guarantees no photo appears twice in a
row. The build now also says how many photos will be reused, which is the real
fix: it tells you to go and shoot more.

Unavoidable limit: `premium` has only two moves, so a photo appearing three
times must reuse one. It alternates rather than repeating back to back, which
is the best available.

---

## Tier 3 — bigger swings

### Beat-synced cutting — **done**
`music_bpm` (and `music_offset`) on the brand. `plan()` pulls each cut onto the
nearest beat by stretching or trimming only the *silence* between lines, capped
at ±0.25s and never below a 0.08s floor, so the speech is never cut into.

Measured on a real segment set at 120bpm: cuts land **0.000s** from the beat,
8 of 8, against a 0.064s mean error and 2 of 8 on-beat without it. Gaps moved
between 0.12s and 0.44s around the 0.28s default.

This forced a real change: `plan()` now returns the pauses it chose and
`voice.concat()` takes them, because the audio has to be joined with exactly the
gaps the plan assumed. Planning therefore happens *before* the voice track is
built, not after.

Only engages when a track is actually set and `--no-music` is not passed.

### Accept video clips, not just stills — **done**
`MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS` in `config.py`; `render.is_video()` picks
the branch. A clip gets the same framing and grade as a still but no camera move
(`_assign_moves` skips it — it already moves), is normalised to 30fps, looped
with `-stream_loop -1` when shorter than its slot, trimmed when longer, and has
its audio dropped.

Verified: a 3s clip in a 4.03s slot loops (40.6dB PSNR between the 0.20s and
3.20s frames, against 14.1dB for genuinely different moments); a 1600x1200
landscape clip gets the blurred fill automatically; every shot comes out at
exactly its slot length, 30fps, at frame size. The web UI accepts clip uploads
and shows them as `<video>`.

Open: **looping is a visible jump** back to the first frame. Fine for a short
handling shot, less so for anything with a clear beginning and end. Freezing the
last frame instead may read better — worth trying against a real client clip
rather than deciding now.

### Two hooks per build — **done**
`--variants N`. `script.build(..., variant=n)` offsets the choice within the same
hook pool, leaving every other line alone. The first variant keeps the usual
filename so nothing downstream changes; the rest get `_v2`, `_v3`. One caption
serves them all, since it never quotes the opening.

`randrange` replaced `choice` for the hook pick so the draw is the same one
`choice()` would have made — variant 0 is byte-for-byte what it always was,
verified against the previously recorded output.

Asking for more variants than there are openings, or using it on a product with
a fixed `script_en`, produces duplicates — those are detected by comparing the
spoken lines and skipped rather than rendered twice.

---

## Suggested order

1. ~~Kinetic captions~~ done
2. ~~Blurred fill~~ done
3. ~~Template layer~~ done
4. ~~Transitions~~ done; colour grade partly — per-photo matching still open
5. ~~End card~~ done; ~~sound design~~ done
6. ~~Photo repetition~~ done
7. Everything above is done. What is left is not more features but real client
   photos: `match` strength, `MAX_CROP_LOSS` and the sound levels are all
   calibrated against sample data and want tuning against a real shoot.

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

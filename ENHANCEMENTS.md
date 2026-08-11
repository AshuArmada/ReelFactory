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

### 1. Kinetic word-by-word captions

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

### 2. Stop centre-cropping landscape photos

A correctness problem wearing an aesthetics costume.

- **Where:** `render.py:98-99`, `scale=increase,crop`.
- **Why:** fine for portrait input. For the landscape phone shots clients
  actually send, it discards ~60% of the frame and can slice the product in
  half.
- **Fix:** scale-to-fit the whole photo over a blurred, darkened copy of itself.
  `split` → one branch `scale=increase,crop,boxblur`, other branch
  `scale=decrease` → `overlay=centered`. ~6 lines of filtergraph.

### 3. A visual template layer

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

### Transition variety
`render.py:189` hardcodes `transition=fade`. Pick from the template's set using
the existing seeded RNG so builds stay reproducible. Premium → slow dissolves,
value → fast slides. Nearly free once Tier 1.3 exists.

### Colour grade pass
Client photos come from different phones in different light — photo 1 warm,
photo 3 blue. That mismatch is most of what reads as amateur. One
`eq` / `colorbalance` per shot, tuned per template, makes a random pile of phone
photos look like one shoot. ~10 lines.

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

1. Kinetic captions (biggest perceived change, data already available)
2. Blurred fill (fixes real damage to real client photos)
3. Template layer (unblocks everything else)
4. Transitions + colour grade (nearly free once 3 exists)
5. End card, sound design
6. Re-evaluate Tier 3 against what the first client actually reacts to

---

## Open questions

- **Do captions replace or complement the current overlay text?** The overlay is
  a *shortened* line (`_shorten()`), not the full VO. Word-by-word implies
  showing the whole spoken line. Those are different editorial choices and the
  answer changes the subtitle code.
- **How many templates before it stops looking samey?** Guessing three. Worth
  building one properly and judging from frames rather than committing to a
  number up front.
- **Does the template pick the tone, or does the tone pick the template?**
  `tone` already exists and means something adjacent. Risk of two overlapping
  concepts.

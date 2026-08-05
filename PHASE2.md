# Phase 2 — multi-platform posting and scheduling

Phase 1 (what you have now) ends with a finished MP4 and a caption file that you
upload by hand. Phase 2 is about removing that last manual step and putting the
posting on a calendar.

Read this before building any of it. The hard part is not the code.

---

## What actually changes

Phase 1 pipeline:

```
photos + product.yaml  ->  script  ->  voice  ->  video  ->  you upload
```

Phase 2 pipeline:

```
photos + product.yaml  ->  script  ->  voice  ->  video  ->  queue  ->  publisher  ->  FB / IG / YT
                                                              ^
                                                        calendar.yaml
```

Three new pieces, in the order worth building them:

**1. A queue.** A single `calendar.yaml` listing what goes out, where, and when.
Plain text, editable, reviewable by the client before anything is published.

```yaml
- product: iron-shelf-5-tier
  lang: hi
  platform: facebook
  when: 2026-08-04 19:30
  status: pending
```

**2. A publisher per platform.** One small module each, sharing an interface
(`publish(video, caption) -> post_url`). Build Facebook first. Add others only
once Facebook is running reliably for a few weeks.

**3. A runner.** A scheduled task that wakes up, finds due entries, publishes
them, writes back the resulting post URL and marks them done. On Windows this is
Task Scheduler running one command — no server needed.

Build the queue and runner with a "dry run" publisher first. Watch it pick the
right things at the right times for a week before you connect a real account.

---

## The platform reality

This is where the effort actually goes, and it differs sharply per platform.
**Verify the current requirements in each platform's developer documentation
before you start** — these programmes change their rules regularly, and anything
written here could be out of date by the time you build it.

### Facebook Pages
Publishing video to a Page you manage is the well-trodden path. You will need a
Meta developer account, an app, and a long-lived Page access token. Expect an app
review step before the app can act on Pages you do not personally own — which
matters the moment you have a second client. Budget real time for review, and
have a privacy policy page ready, since reviews usually demand one.

### Instagram
Requires the client's Instagram account to be a Business or Creator account
linked to their Facebook Page. Publishing goes through the same Meta app, so if
you have done Facebook you are most of the way there. There are daily posting
caps — check the current number before designing anything that posts in bulk.

### YouTube Shorts
A separate world: Google Cloud project, OAuth consent screen, and a quota system
where each upload costs a meaningful slice of a daily allowance. Fine for a few
posts a day, restrictive beyond that. Worth doing only if the client actually
wants a YouTube presence.

### WhatsApp Status
No API for Status. It cannot be automated. The realistic version is that the
tool drops the file into a synced folder and someone posts it from the phone.
Say this to the client upfront rather than promising automation.

**The honest summary:** the video generation was the easy half. Platform access
is paperwork, review queues and token management. Plan for weeks, not days, and
do not promise a client a date until the app review has actually cleared.

---

## Token handling

The one part worth getting right the first time.

- Never put tokens in `brand.yaml` — that file is meant to be copied and shared.
  Use a separate `secrets/` folder that is excluded from any backup or repo.
- Page tokens expire. Build refresh in from the start, and make the failure mode
  a clear message telling you which client needs reconnecting — not a silent
  skipped post.
- One token set per client, keyed by the client folder, so a mistake with one
  client cannot post to another.

If you end up managing several clients, this is also the moment `brand.yaml`
should move from a single file at the root to `clients/<name>/brand.yaml`.

---

## Suggested order

1. `calendar.yaml` + runner + a dry-run publisher that only logs. **One evening.**
2. Windows Task Scheduler entry, watched for a week. **Ten minutes plus patience.**
3. Meta app, Facebook publisher, tested on a throwaway Page. **The long pole.**
4. Instagram, once Facebook has been stable for a few weeks. **A day or two.**
5. A results log — post URL, views, comments — pulled back into a weekly summary
   so the client can see what the money bought.

Step 5 is what turns this from a video tool into something a client renews.

---

## Things worth deciding before you build

- **Approval step or not?** Auto-posting on a schedule is convenient right up
  until a wrong price goes out. A "client approves in WhatsApp, then it posts"
  step is slower but has saved a lot of people from that phone call.
- **What happens when a post fails at 7:30pm?** Retry, skip, or alert you.
  Decide now; the default of "silently do nothing" is the worst option.
- **Who owns the Meta app** — you or the client? If you own it, you keep control
  but you also carry the review burden for everyone. If the client owns it, you
  need their developer account, and moving on gets messy.

None of these are technical questions, which is exactly why they are the ones
that decide whether phase 2 survives contact with a real client.

**Security and behavior review — 2 October 2026**

Eight application issues were reproduced. The first two concern access to the
local web service; the others affect uploads, collections, concurrent builds,
preview storage and input validation. The findings below describe the original
review snapshot; their source line numbers refer to that snapshot. All eight
have since been addressed in the working tree, along with the upload-size limit
and stale photo-warning assertions.

**Implemented fixes**

- Brand asset downloads resolve paths and require the controlled `logo/` or
  `music/` folder and an allowed extension. External rendering assets are not
  exposed through the browser download route.
- Every mutating request requires a session token. Foreign origins, cross-site
  Fetch Metadata and unknown hosts are rejected. Forms and fetch uploads send
  the token. Requests are limited to 256 MB.
- Final video names are reserved exclusively, rendered into separate temporary
  files and published by atomic replacement. Failed renders remove temporary
  files and reservations. Shared captions use a file lock; atomic text writes
  retry transient Windows sharing violations without truncating the destination.
- Images and videos are validated before uploads are accepted. Edited builds
  probe selected images, so unused corrupt images cannot block the draft.
- Collection scenes offer an explicit product selector. Upload ownership uses
  that selection; per-product file locks serialize metadata updates across
  requests, and YAML writes replace files atomically.
- Successful previews retain at most three completed jobs per product, pruning
  completed jobs older than a day. Failed jobs are removed, and a browser control
  clears completed previews without touching active renders or final exports.
- Final builds validate language, aspect, voice provider, preset and template
  before rendering, returning HTTP 400 and preserving the edited draft.
- Photo-warning assertions now check the current controls and warning content.

Regression coverage is in `tests/test_security_fixes.py`. The browser checks in
`scripts/audit_security_fixes.py` exercise a foreign-origin deletion attempt and
a collection video assigned to the second product. The preview browser audit
also checks explicit cache cleanup.

**Validation after fixes**

- Full suite: `python -m pytest -o addopts='' -q --tb=short` — **478 passed**,
  including all 17 real-render tests, in 235 seconds on Windows.
- `python scripts/audit_security_fixes.py` — passed: cross-origin deletion is
  blocked and the chosen collection product owns its successfully rendered clip.
- `python scripts/audit_reel_preview.py` — passed: upload, playback, scene edits,
  refresh, failed-refresh recovery, mobile layout and explicit cache cleanup.
- Python compilation and `git diff --check` passed. No live cloud providers were
  called. Test projects and dummy files were disposable.

All reproductions used disposable products, dummy files and local test servers.
No real credentials were read and no cloud providers were called. The default
server listens on loopback. The file-read finding requires an attacker who can
reach the service; the cross-origin finding was also reproduced in Chromium
from a different local web origin. Internet-to-loopback attacks and DNS rebinding
were not tested, and browser restrictions can affect those attack paths.

1. **P1 — Brand repair and asset serving allow arbitrary local file reads.**

   Sources: [app.py](../reelfactory/web/app.py), `brand_repair()` at line 326 and
   `brand_asset()` at line 382.

   The repair endpoint accepts a `logo` or `music` path in YAML without checking
   that it is an approved media asset. The asset endpoint then serves that path,
   including absolute paths outside the workspace, without authentication.
   Posting a dummy external text-file path as `logo` returned a redirect; fetching
   `/brand/asset/logo` returned HTTP 200 and the dummy file's contents.
   A reachable client could use the same chain to read files accessible to the
   server account, including credentials whose paths it knows.

   Fix: restrict served assets to validated media in controlled asset directories
   and resolve containment before serving. If external source files remain a
   supported configuration option, import them into controlled storage rather
   than turning the asset endpoint into a general file-serving route. Protect
   configuration mutations as described below.

2. **P1 — Destructive POST routes accept cross-origin requests without a token.**

   Sources: [app.py](../reelfactory/web/app.py), `create_app()` at line 146 and
   `output_delete()` at line 1268. The API settings page has its own CSRF token,
   but this protection does not cover the rest of the app.

   A plain form served on another local origin successfully posted to the output
   deletion route in Chromium and deleted a dummy export. A Flask request with
   a foreign Origin and `Sec-Fetch-Site: cross-site` also returned HTTP 200.
   Confirmation dialogs in the legitimate page do not protect these endpoints.
   An arbitrary Host header was accepted as well.

   Fix: apply CSRF protection to every state-changing route, including JSON/fetch
   uploads and previews, and validate Origin/Fetch Metadata and allowed Host
   values. Keep loopback binding as an additional restriction, not the sole
   request-authorization mechanism.

3. **P2 — Concurrent builds can write to the same final video.**

   Source: [cli.py](../reelfactory/cli.py), `_free_path()` at line 741.

   Filename selection checks whether a path exists without reserving it. Two
   overlapping requests can both choose the same unused filename before FFmpeg
   creates it. In a controlled reproduction using the real build orchestration
   with a synchronized dummy renderer, both requests returned HTTP 200 but used
   one identical output path. Real FFmpeg invocations can overwrite or interfere
   with one another, losing a version or corrupting its output.

   Fix: reserve output names atomically, use unique per-build names, or serialize
   builds for a product. Publish completed renders by atomic rename.

4. **P2 — An invalid image upload blocks rendering even when it is not selected.**

   Sources: [app.py](../reelfactory/web/app.py), lines 627–632;
   [cli.py](../reelfactory/cli.py), line 562.

   Video uploads are decoded for validation, but images are checked only for
   an accepted extension and nonempty content. Uploading text named `bad.jpg`
   returned HTTP 201. A preview using only an original valid image then failed
   with HTTP 400 because the build probes every image in the product library.
   Cancelling the editor does not remove the uploaded file, so it continues to
   block unrelated scenes until removed through product media management.

   Fix: validate image contents before retaining an upload and remove rejected
   files. Validate the media actually selected for an edited render instead of
   allowing unused library files to prevent the build.

5. **P2 — New collection videos can be assigned to the wrong product.**

   Sources: [scene-editor.js](../reelfactory/web/static/scene-editor.js), lines
   140 and 204; [app.py](../reelfactory/web/app.py), line 618.

   A new scene clones a selector defaulting to the collection's first photo.
   Uploads send that value as `source_photo`, and the server assigns the uploaded
   clip to that photo's product. There is no product selection in the new-video
   dialog. In Chromium, adding a video with narration about Chair to a Rack/Chair
   collection assigned it to Rack. Preview then failed with: “Scene 4 talks
   about Chair, but its photo belongs to another product.”

   Fix: explicitly select the collection product for a new scene and upload,
   rather than inferring ownership from a hidden default media choice. Validate
   that selection server-side and preserve it when scenes are reordered.

6. **P2 — Concurrent collection uploads lose a media association.**

   Sources: [app.py](../reelfactory/web/app.py), lines 611–637;
   [config.py](../reelfactory/config.py), `write_yaml()` at line 409.

   Each request reads the complete product YAML, adds one media mapping, then
   rewrites the complete file. Two requests synchronized after reading the old
   state both returned HTTP 201 and saved their files, but only one new filename
   remained registered to a collection member. The reproduction serialized the
   writes to isolate the lost-update problem; simultaneous writes can also leave
   partial or inconsistent YAML because writes directly truncate the target.

   Fix: lock the complete read/modify/write operation per product and replace
   YAML atomically. Unique uploaded filenames alone do not protect metadata.

7. **P2 — Preview refreshes accumulate files without a cleanup path.**

   Sources: [app.py](../reelfactory/web/app.py), preview creation at line 978
   and `_list_outputs()` at line 1346.

   Every preview uses a new token directory. Three successful requests retained
   three separate preview files. They are excluded from the finished-files list,
   and there is no expiration or preview cleanup control. Failed jobs can also
   leave directories or partial results. Repeated editing can consume increasing
   disk space while the UI offers no way to remove those cached previews.

   Fix: bound retained previews by age/count or session, remove failed results,
   and offer explicit preview cleanup. Keep the current playable preview until
   its replacement succeeds.

8. **P3 — Invalid final-build shapes produce an unhandled server error.**

   Sources: [app.py](../reelfactory/web/app.py), line 881;
   [cli.py](../reelfactory/cli.py), line 566.

   Unlike the preview endpoint, the final-build endpoint does not validate posted
   aspect ratios. Posting `aspect=invalid` reaches `ASPECTS[aspects[0]]` and raises
   `KeyError`, which is not handled by the route. With testing exceptions enabled
   the exception propagates; normal serving returns a generic HTTP 500 instead
   of preserving the draft with a useful validation message.

   Fix: validate all final-build enum fields before dispatch, using the same
   allowed values as preview and the CLI. Return HTTP 400 with the edited draft.

**Original review validation results (before fixes)**

- Non-slow suite: **441 passed, 2 failed**, 17 deselected.
- Real-render suite: **17 passed**, 443 deselected.
- The two failures are in `tests/test_photo_quality.py` at lines 162 and 169.
  They expect old phrases such as “will lose quality” and “Replace the photos.”
  The current pages still contain photo-quality warnings and the popup controls;
  these are stale copy assertions, not evidence that warnings are missing.
- The upload size limit is unset (`MAX_CONTENT_LENGTH=None`). This is an
  additional resource-exhaustion concern; a large-upload or load test was not run.
- Local reproduction scripts and JSON evidence are under `out/audit/review/`
  (ignored by Git). They use dummy files, temporary projects and mocked render
  output only where needed to isolate races. Browser checks use real clip uploads.

The application remains a local single-user tool; these fixes do not introduce
account authentication or make it suitable for public hosting. Preview cleanup
can be retried if Windows has a file open. A process terminated during a render
may leave a marked active cache folder; remove it only after stopping the server.

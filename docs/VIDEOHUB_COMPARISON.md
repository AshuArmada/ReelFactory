# Reel Factory vs. VideoHub

**Reviewed:** 22 November 2026  
**Compared:** this Reel Factory checkout and `TensaikunYT/Videohub` `main` source visible on GitHub.

## What VideoHub is

VideoHub is a local review studio and render manager for films authored as code with a coding agent. The project supplies a live player, review notes, project/version tracking, render queue and MCP tools so an agent can read notes and update the film source. It is not a product-ad-to-reel generator: it does not provide Reel Factory's product facts → marketing script → narration/subtitles → finished social reel workflow.

## Feature comparison

| Capability | VideoHub: checked in source? | Reel Factory today | Gap / relevance |
| --- | --- | --- | --- |
| Create a video from code / local film projects | **Yes.** Project scanner and project contract support watched film folders and code-authored projects. | Product projects and templated FFmpeg reels from photos/clips, script and brand data. | Different production model. Supporting arbitrary code-authored films would be a separate product mode. |
| Live preview without rendering | **Yes.** `server/live.js` detects `window.renderOut(t)`, serves the page, reads audio and watches source revisions; `hub_live` opens it. | **No.** Reel Factory builds a preview MP4 through `build_one` and caches the output. | Strong candidate if we ever support code-authored video: immediate preview is faster than rendering. It does not replace our rendered reel preview without a new renderer contract. |
| Click/draw/range notes on frames | **Yes.** `server/notes.js` stores timed notes, drawings/images, ranges and note state; MCP tools expose notes and updates. | **No equivalent.** Our frame editor can revise a frame/scene in the reel workflow, but it does not create a timecoded review note for an agent to consume. | Useful for collaborative or agent-assisted review; less valuable for our current scene editor unless linked to scenes and revisions. |
| Agent integration (MCP) | **Yes.** `server/mcp.js` registers tools from `server/tools.js`; tools include `hub_live`, project, notes, render and job workflows. | **No.** We expose a CLI and local browser routes, but no MCP server/general agent tool API is present in this checkout. | A practical integration opportunity for agents that create or revise Reel Factory scripts/reels. Need a bounded, safe API rather than exposing the app's local routes wholesale. |
| Agent plan, questions, wait-for-feedback loop | **README claims yes, but the exposed tool code does not confirm it.** `server/mcp.js` registers only entries exported in `server/tools.js`; the inspected `TOOLS` object has project, notes, render, cleanup and thumbnail tools, but no `hub_plan`, `hub_ask` or `hub_wait`. Treat these named MCP capabilities as **not present in the checked server tool implementation**; the README may describe UI/agent behavior beyond the code tools. | **No.** Human script review and editing exist; no agent inbox/wait loop. | Do not prioritize copying these named MCP tools until VideoHub reconciles the README/code mismatch. A smaller agent workflow API remains a potential Reel Factory feature. |
| Render queue and job tracking | **Yes.** `server/jobs.js` persists queue state and runs one job at a time. | **Yes.** `reelfactory/render_jobs.py` has a persisted serial worker, status, cancel-waiting, stop-after-current, retry and repeat actions. | Mostly parity. VideoHub additionally coordinates external renders and offers more render controls. Our worker requires the local server to stay running. |
| Partial/chunk re-render | **Yes, with project requirements.** `server/jobs.js` detects chunked render recipes and rerenders chunks touched by notes, then reassembles a version. | **No.** Reel Factory renders the reel again; it has cached previews and saved input recipes, not changed-range re-rendering. | Could save time for longer films. Reel Factory scenes are assembled through FFmpeg and do not currently have the code/chunk manifest contract VideoHub expects. |
| Render CPU/RAM safeguards | **Yes.** `server/guard.js` applies low process priority/core limits and adapts render speed to available RAM; encoder capability detection is in the server. | **Partial.** Serial queue avoids competing Reel Factory jobs, but it has no adaptive CPU/RAM throttle, per-job slow/stop controls, or hardware encoder probing UI. | Good reliability improvement for large or concurrent workloads; lower priority than editing/review improvements for short reels. |
| Sound timeline / stems | **Yes, implementation is wired.** README describes per-stem mute/solo/levels, music removal/ducking and adding sounds; `server/hub.js` imports `sound.js` and `soundedit.js`. | **Partial.** We mix narration, music and generated effects at render time; no editable multistem timeline. | A useful editor upgrade if users need fine audio control. |
| Project/version/disk cleanup studio | **Yes.** VideoHub scans projects, stores version/note history, reports disk use and moves cleanup candidates to Trash/archive with approval. | **Partial.** Products, saved scripts, outputs and render snapshots/recipes are managed, but no unified film version/disk cleanup studio. | Could improve lifecycle management as project history and output volume grow. |
| AI script, TTS, subtitles, captions, social publishing | **No equivalent end-to-end workflow shown in VideoHub's code/product contract.** It can preview film audio/transcripts and edit sound, but its core is review/render for code-authored films. | **Yes.** Reel Factory has AI/local script writers, multiple narration providers, subtitle rendering, captions and Growth/Postiz scheduling/analytics. | Reel Factory's core advantage; retain it. VideoHub does not make this workflow redundant. |

## Code evidence

VideoHub features above were checked against implementation, not only its README:

- [`server/live.js`](https://github.com/TensaikunYT/Videohub/blob/main/server/live.js) implements live-page capability checks, audio selection and source watching.
- [`server/notes.js`](https://github.com/TensaikunYT/Videohub/blob/main/server/notes.js) stores frame/range notes and derived images/chunk associations.
- [`server/mcp.js`](https://github.com/TensaikunYT/Videohub/blob/main/server/mcp.js) registers the MCP tool definitions.
- [`server/tools.js`](https://github.com/TensaikunYT/Videohub/blob/main/server/tools.js) implements agent-facing live/project/workspace and note/render tools.
- **README/code discrepancy:** VideoHub's README advertises `hub_plan`, `hub_ask` and `hub_wait`, but those names are absent from the `TOOLS` object in the inspected [`server/tools.js`](https://github.com/TensaikunYT/Videohub/blob/main/server/tools.js). [`server/mcp.js`](https://github.com/TensaikunYT/Videohub/blob/main/server/mcp.js) registers only that object, so those MCP tools are not exposed by this source. The README may describe adjacent UI behavior, but these specific MCP commands are not confirmed.
- [`server/jobs.js`](https://github.com/TensaikunYT/Videohub/blob/main/server/jobs.js) implements the persistent serial render queue and chunk-aware partial renders.
- [`server/guard.js`](https://github.com/TensaikunYT/Videohub/blob/main/server/guard.js) implements CPU priority/limits and RAM-triggered slow mode.
- [`server/hub.js`](https://github.com/TensaikunYT/Videohub/blob/main/server/hub.js) wires live, notes, jobs, sound, cleanup, transcript and other server modules.
- [VideoHub `package.json`](https://github.com/TensaikunYT/Videohub/blob/main/package.json) lists MCP SDK, Puppeteer Core and Zod dependencies; this supports the MCP/headless render architecture but is not, by itself, feature proof.

Reel Factory code checked locally:

- [Preview route](../reelfactory/web/app.py) calls `build_one` to generate a preview video; [preview UI](../reelfactory/web/static/reel-preview.js) submits the render request and plays the returned file.
- [Render queue](../reelfactory/render_jobs.py) implements saved jobs and retry/repeat; [queue web routes](../reelfactory/web/render_jobs.py) expose job controls.
- [Frame editor routes](../reelfactory/web/frame_editor.py) serve Reel Factory's frame-edit workflow, not VideoHub-style timecoded agent review notes.
- [Voice synthesis](../reelfactory/voice.py), [subtitle generation](../reelfactory/subtitles.py), [FFmpeg composition](../reelfactory/render.py), and [Growth integration](../reelfactory/growth.py) implement our reel-production and publishing focus.

## Suggested priorities for Reel Factory

1. **MCP/agent API for the existing workflow:** create or select product, read/update script and scene choices, request preview/render, and report job state. This extends Reel Factory's actual purpose without adopting a separate code-film format.
2. **Timecoded review notes:** let reviewers attach a note to a scene/time and optionally a frame; link it to the current saved script/render version and let an agent turn it into an edit suggestion.
3. **Render resource controls:** expose slow/normal/stop for queued work and add basic resource-aware throttling for long jobs. Keep current safe serial worker behavior.
4. **Explore partial re-render only after measuring:** prototype scene/chunk reuse for our FFmpeg pipeline and compare output correctness, audio transitions and time saved before promising changed-range renders.
5. **Defer the code-authored live-preview engine and full film cleanup studio** unless we decide to serve animation/film creators beyond product reels. Those require a new project contract and review model.

## Retrieval and verification limits

The local `git clone` attempt failed because this environment could not connect to `github.com:443`; the user's parent-folder clone was therefore not created. The comparison used the repository's public `main` README and source files served directly by GitHub, and checked the current Reel Factory source locally. This is a source-level feature verification, not a runtime installation or test of VideoHub. VideoHub's README also says Linux/macOS support has not yet been exercised on real machines.

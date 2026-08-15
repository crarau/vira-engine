# vira console

A local Next.js app for seeing and driving the engine. Inspection tool, not a
product surface — dense, dark, and biased toward showing the real numbers.

## It needs the API up

Every page fetches in the browser; nothing is prerendered against live data.
With the API down the pages still render, and each panel shows the failing
endpoint instead of going blank. Start the engine first:

```bash
cd ..                                   # repo root
uvicorn vira.api.app:app --port 8720
```

Then:

```bash
npm install
npm run dev            # http://localhost:3120
```

The nav bar polls `/healthz` every 15s and shows a green dot plus the base URL
it is talking to, so a misconfigured base is visible immediately.

## Pointing it somewhere else

`NEXT_PUBLIC_API_BASE`, default `http://127.0.0.1:8720`. Set it in
`.env.local` (see `.env.local.example`). It is read at build time as well as in
the browser, so change it and restart the dev server.

```
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8720
```

## Routes

| Route | What it is for |
|---|---|
| `/` | Pick a company, type a product, pick a lane and mode, POST `/v1/videos`. Lanes show their voice and look so the choice means something. Jobs you started are remembered in localStorage. |
| `/corpus` | Browse the Lovable corpus. Trends with author, caption, cover, views, engagement, trend_score and age; companies with category, bio, website and whether enrichment produced anything. Age distribution corpus-wide and per page. |
| `/jobs/[id]` | The live view. Subscribes to `GET /v1/jobs/{id}/stream` over SSE and renders the trace grouped by stage as it arrives. Falls back to `/v1/jobs/{id}/events`, then to the job row. Shows the video inline when it lands. |
| `/videos/[id]` | One video: the mp4, hook/CTA/caption/hashtags, all five score dimensions with the evidence gate called out, beat-by-beat script with real timings, the source trends it cited, the generated frames with their prompts, and a Recipe tab with the verbatim prompts. Regenerate with notes from here. |
| `/videos` | Everything generated, filterable by company, lane and disposition. |

## Two things the UI insists on

**A dropped video is not an error.** Nearly everything generated so far is
dropped on evidence, and that is the gate doing its job. Dropped rows are drawn
in flat slate with the reason stated — never in red, which is reserved for a job
that actually failed.

**Numbers over adjectives.** Scores to two decimals, durations, event counts,
elapsed time, corpus ages, prompt sizes. If the engine knows a number, this
shows it.

## Notes for whoever ports this to Lovable

- All API access is in `lib/api.ts`. Nothing else calls `fetch`.
- List reads go through `unwrap()`, which accepts a bare array or an
  `items` / `data` / `results` / `<name>` envelope.
- `lib/api.ts` also mirrors the gate constants from `vira/config.py`
  (evidence floor 3.0, watchlist 3.5, surface 4.5, freshness 90d). Per-video
  they are overridden by the recipe's own settings snapshot, which is what was
  actually in force.
- Every page is a client component; there is no server-side data access to port.

## Known gap

Generated frames are recorded in `assets` by bare filename (`shot00.jpg`) and
live under `video/public/shots/<job_id>/`. Only `out/` is mounted at `/media`,
so there is no URL for them — the Frames tab shows the prompt, the credit and
the vision description, with a placeholder where the image would be. Mounting
`video/public` (or writing frames under `out/`) would fill them in with no
client change.

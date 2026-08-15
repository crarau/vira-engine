# Vira as a service

The engine becomes a standalone REST API with its own database. Lovable calls
it; it calls nothing back. Nothing about it depends on Lovable Cloud except one
read-only borrow of the trends corpus.

## Why it owns its own database

The corpus lives in Lovable Cloud, and that was fine while the engine was a
script. As a service it needs to store things Lovable Cloud cannot hold:

- **Recipes.** Every prompt, verbatim, per video. That is the tweak loop, and
  it is the reason a generated ad is a starting point rather than a lottery
  ticket. Lovable Cloud has no table for it and shouldn't.
- **Jobs.** Generation takes 74–350 seconds. That is a queue, with status,
  progress and failure, not a request/response.
- **Review batches.** Judges arriving from Terac need a public, unauthenticated
  page. Lovable Cloud's RLS is built around `auth.uid()`; modelling anonymous
  external reviewers there fights the grain.
- **Independence.** Lovable Cloud exposes no Postgres connection string and no
  service_role. A service that has to run migrations, join across its own
  tables and back itself up cannot live inside that constraint.

So: Postgres owned by this service. Lovable keeps its own database for the
marketplace; the two talk over HTTP.

```
┌── Lovable app ──────────┐        ┌── Vira API ────────────────┐
│  marketplace, auth,     │  HTTP  │  FastAPI + its own Postgres│
│  company profiles       │ ─────▶ │  jobs · videos · recipes   │
│  Lovable Cloud (its DB) │        │  llm_calls · assets        │
└─────────────────────────┘        │  review batches + votes    │
                                   └────────────┬───────────────┘
                                                │ read-only
                                                ▼
                                   Lovable Cloud `trends` (PostgREST)
```

## The data model, and what each table is for

| Table | Holds | Why it exists |
|---|---|---|
| `companies` | this service's own copy of a brand | the engine must work if Lovable is down |
| `jobs` | queued / running / done / failed | generation is minutes, not milliseconds |
| `videos` | hook, CTA, duration, mp4 path, score | the artefact and its verdict |
| `recipes` | plan, settings, corpus, beats | **the tweakable record** |
| `llm_calls` | verbatim system + user prompts, responses | trace any output back to the exact string that caused it |
| `assets` | per-beat image, its prompt, its credit, and what a vision model says it *actually* shows | intent vs reality, which is where the bugs are |
| `review_batches` / `review_votes` | the judge flow | human feedback, measurable before/after |

`llm_calls` and `assets.description` are the two that make this more than a job
queue. Together they answer "why does this video look like this?" without
guessing.

## Endpoints

```
GET  /healthz
GET  /v1/lanes                              creative angles a UI can offer
GET  /v1/companies          POST /v1/companies
POST /v1/videos                             → 202 {job_id}
GET  /v1/jobs/{job_id}                      status, progress, video_id when done
GET  /v1/jobs/{job_id}/stream               SSE · the live trace, as it happens
GET  /v1/jobs/{job_id}/events               the same trace as JSON, for pollers
GET  /v1/videos/{id}                        metadata + mp4 url
GET  /v1/videos/{id}/recipe                 prompts, corpus, settings
POST /v1/videos/{id}/regenerate             {notes[]} → new job from stored recipe
GET  /v1/companies/{slug}/videos
POST /v1/review-batches                     {video_ids[]} → {public_token, judge_url}
GET  /v1/review-batches/{token}             PUBLIC judge view
POST /v1/review-batches/{token}/votes       {reviewer_ref, rating, picked, comment}
GET  /v1/review-batches/{id}/results        aggregated
GET  /media/...                             the rendered mp4s
```

Two rules encoded in that list:

**`POST /v1/videos` returns 202, never a video.** Generation is 74s at best.
Anything that blocks a request for that long is a timeout waiting to happen —
in the browser, in a proxy, or in Lovable's own fetch.

**The judge view never returns a score.** `GET /v1/review-batches/{token}` omits
`score`, `disposition` and `drop_reason` deliberately. The whole point of asking
humans is to get a signal independent of the engine's own opinion; showing them
the engine's grade first would anchor them to it.

## The review loop

This is the piece that makes the human-feedback story real rather than
aspirational.

```
generate N videos
  → POST /v1/review-batches            → judge_url with an unguessable token
  → send that one link to Terac
  → judges watch, rate 1-5, pick favourites, comment
  → GET .../results                    → aggregated ratings + comments
  → POST /v1/videos/{id}/regenerate    → new video, same recipe, human notes applied
  → the diff between the two recipes IS the before/after
```

The reviewer is identified by an opaque `reviewer_ref` supplied by the judging
platform. This service stores no personal data about them and never needs to.

## Deployment

Two targets, and they are not equivalent.

**chipdev** — 32 cores, idle, and roughly 3× faster at rendering than the
laptop. This is where it should actually run. Postgres in Docker bound to
localhost, uvicorn under systemd, Caddy terminating TLS.

**Render** — kept as the fallback and for the demo, because a hackathon demo
that depends on an SSH box behind someone's home network is a bad bet. Slower,
but it is a URL that works from anywhere.

The API is identical on both; only `DATABASE_URL` and the render concurrency
change. Note that Render's smaller instances will render one video at a time —
fine for a demo, not for five.

## Design rules that survive the move to a service

Unchanged from the CLI, and worth restating because a public API is exactly
where they get quietly dropped:

- **The evidence gate runs server-side, after generation, always.** No request
  parameter can skip it or move the threshold.
- **Timings come from the synthesiser.** No endpoint accepts frame numbers.
- **Nothing is stored without a verified `source_url`.**
- **Every video writes its recipe in the same transaction.** A video without a
  recipe is not a video, it is an orphan; the write is atomic so that state
  cannot exist.

## Open

- **Auth.** Currently none. Fine while the only caller is a demo frontend and
  the judge tokens are unguessable. Before anything real: an API key on the
  write endpoints, and rate limiting on `POST /v1/videos`, which costs money
  every time it is called.
- **Media hosting.** mp4s are served off local disk. That is correct on chipdev
  and wrong on Render, whose disks are ephemeral — object storage is needed
  there.
- **Concurrency.** One render saturates several cores. The job queue is
  currently in-process; a real worker pool with a shared queue is the next step
  if more than a couple of requests ever overlap.

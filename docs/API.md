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

## Live progress

`POST /v1/videos` returns in milliseconds and the video arrives 74–350 seconds
later. A spinner over that gap is a bad trade, because the interesting thing
about this engine is exactly what happens inside it: the Director writes a
script, looks at the frames that actually came back, finds that two of them
contradict their own captions, and regenerates one. That trace already exists —
`Production.note()` has always written it to the log. `GET /v1/jobs/{id}/stream`
puts it on the wire.

Server-Sent Events rather than a WebSocket, for three reasons that all point the
same way: progress is one-directional so a duplex channel buys nothing; SSE is
plain HTTP that every proxy already forwards; and reconnection with resumption
is built into the browser rather than being something the client has to write.

```
GET /v1/jobs/{job_id}/stream        text/event-stream
GET /v1/jobs/{job_id}/events        application/json
```

### The event

Every frame is one JSON object. Same shape on both endpoints.

```json
{
  "seq": 12,
  "ts": "2026-08-15T22:41:38.204Z",
  "job_id": "0f7ec28d-cc95-447a-8daa-f386503f6565",
  "stage": "cohesion",
  "message": "cohesion: two beats contradict their scripted descriptions (3 mismatches)",
  "level": "warn",
  "data": { "mismatches": 3 }
}
```

| Field | Meaning |
|---|---|
| `seq` | per-job, starts at 1, monotonic. Sent as the SSE `id:`, which is what makes resumption work. |
| `ts` | ISO 8601, UTC, milliseconds. |
| `job_id` | echoed so one handler can serve several streams. |
| `stage` | machine-readable, from the vocabulary below. |
| `message` | the human sentence. **Render this.** |
| `level` | `debug` · `info` · `warn` · `error`. |
| `data` | stage-specific structure — `beat_index`, `mismatches`, `video_id`. Always present, often empty. |

**Render `message`, switch on `stage`.** The vocabulary grows every time a stage
is added, and a UI that can only draw the stages it was taught goes blank on the
next release. Treat an unrecognised stage as a plain trace line — never drop it.

### The stage vocabulary

| Stage | What is happening | Path |
|---|---|---|
| `queued` | job accepted, nothing started | both |
| `select` | shortlisting candidate trends from the corpus | both |
| `verify` | fetching every source URL before it reaches a prompt | both |
| `analyze` | working out what the surviving corpus says | both |
| `plan` | choosing the shape of the film | both |
| `write` | script written or revised | both |
| `motion` | caption treatments and camera moves | agentic |
| `critique` | hostile first viewer | both |
| `voice` | narration synthesised | both |
| `imagery` | frames generated, or one regenerated | both |
| `cohesion` | what the frames **actually** show vs the script | agentic |
| `tool` | a Director tool call starting — "regenerating frame 3" | agentic |
| `director` | the Director's own reasoning and budget decisions | agentic |
| `crew` | a crew line with no more specific stage | agentic |
| `score` | the evidence gate | both |
| `render` | Remotion | both |
| `done` | **terminal.** `data.video_id`, `data.hook`, `data.mp4_path` | both |
| `failed` | **terminal.** `data.error` | both |

`tool` events are published *before* the call runs, not after. "regenerating
frame 3…" is only useful while the eight seconds it takes are still passing.

### Consuming it

Six lines. Every event, terminal ones included, arrives on `onmessage` — they
are not named SSE events, so one handler sees the whole run.

```js
const es = new EventSource(`${API}/v1/jobs/${jobId}/stream`);

es.onmessage = (m) => {
  const e = JSON.parse(m.data);           // {seq, ts, job_id, stage, message, level, data}
  append(e.stage, e.message, e.level);
  if (e.stage === "done")   { show(e.data.video_id); es.close(); }
  if (e.stage === "failed") { fail(e.data.error);    es.close(); }
};
```

Do **not** close on `onerror`. That fires while the browser is reconnecting,
which is normal on a mobile network; closing turns a two-second blip into a dead
page. Show "reconnecting" and let it recover.

A working reference client is `examples/watch.html` — one file, no
dependencies, no build step. Open it, paste a base URL and a job id.

### Reconnection

Each frame carries `id: <seq>`. When the connection drops, `EventSource`
reconnects on its own after ~3s and sends the last id back as `Last-Event-ID`;
the server replays everything after it from a 400-event ring buffer. The client
writes nothing for this.

Three consequences worth knowing:

- **Connecting late is fine.** A tab opened four minutes into a run replays the
  whole trace, then goes live. There is no gap between the replay and the live
  feed — the handover is atomic.
- **Streams close themselves after 15 minutes.** The browser reconnects and
  resumes; an abandoned tab stops costing a connection.
- **A gap in `seq` means a frame was dropped.** Only happens to a client that
  has stopped reading — the server drops the event rather than block a render.
  Repair it with `GET /v1/jobs/{id}/events?after=<last seq you have>`.

Heartbeat comments (`: ping`) go out every 15 seconds so a proxy does not idle
the connection out during the two silent minutes of a Remotion render. They
arrive as comments, so no handler ever sees them.

### Polling, and the multi-worker caveat

`GET /v1/jobs/{id}/events?after=<seq>` returns the same events as JSON:

```json
{
  "job_id": "...", "source": "memory", "status": "running",
  "complete": false, "next_after": 12, "events": [ ... ]
}
```

Poll it with `after=next_after` for a client that cannot hold a connection open.

**`source` is the honest part, and it is a real limitation.** The event bus is
in-process. The service is deployed with two uvicorn workers, so a client can
land on a worker that is not the one running its job — and that worker has
nothing to say about it.

- `source: "memory"` — this worker is running the job. Full trace, every crew
  line, `next_after` pages through it.
- `source: "database"` — it is not. The response falls back to the `jobs` row,
  which every worker shares, and returns a **snapshot rather than a log**: one
  event holding the coarse `progress_note`, reusing `seq` 1 while running and 2
  once terminal. Key your events by `seq` and it replaces the line instead of
  appending duplicates. `data.source` is `"database"` on those events.

`/stream` degrades the same way, but it holds state across polls and so keeps
its side of the contract: on the wrong worker it watches the job row every 2
seconds and emits one event per **change**, numbered monotonically like any
other stream. A client written against the live feed keeps working unchanged,
including one that deduplicates on `seq`. It hears sentences instead of the
crew's commentary, and it still gets a correct terminal event.

The fix is not more buffering, it is crossing the process boundary: Postgres
`LISTEN`/`NOTIFY` (no new infrastructure — the database is already there) or
Redis pub/sub (better if the worker pool ever leaves this box). Either one turns
`publish` into a broadcast and lets every worker serve every job's stream.
Until then, `GET /v1/jobs/{id}` and `GET /v1/jobs/{id}/events` are correct on
every worker and `/stream` is best-effort-plus-fallback.

### What is not in the stream

Events are conversation, not record. They live in memory, they are capped at 400
per job, and the oldest job is evicted once 128 are tracked. The durable account
of a run is the recipe — verbatim prompts, corpus, settings — written atomically
with the video and read back through `GET /v1/videos/{id}/recipe`. Nothing that
matters after the mp4 exists is only in an event.

Publishing is also, by contract, unable to affect generation: it never blocks,
never raises, and is a no-op when nobody is watching. A progress feed that can
kill a 350-second render would be a poor trade for a nicer loading state.

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
- **Cross-worker progress.** The event bus is in-process too, and for the same
  reason. `LISTEN`/`NOTIFY` or Redis pub/sub fixes both at once — see the
  multi-worker caveat under Live progress.

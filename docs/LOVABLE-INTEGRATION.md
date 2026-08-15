# Lovable → Vira: the integration contract

*Written against the API as it stands on 2026-08-15 22:58 UTC
(`vira/api/routes/*.py`, `vira/api/schemas.py`) and against a live survey of the
Lovable corpus — see `docs/CORPUS-SURVEY.md` for every number cited here.*

What a Lovable frontend must send to generate a video, what it gets back, what to
draw at each stage, and which failures are not failures.

---

## 0. The one-paragraph version

`POST /v1/videos` returns **202 and a job id in milliseconds**. Generation takes
**74–350 seconds**. Poll `GET /v1/jobs/{job_id}` or subscribe to
`GET /v1/jobs/{job_id}/stream`, and when `status` is `done`, fetch
`GET /v1/videos/{video_id}`. The video may come back with
`disposition: "dropped"`. **That is a successful job.** The mp4 exists and plays;
the engine is telling you its own sources did not support the claim. A UI that
renders that as an error is lying about the most interesting thing the engine
does.

---

## 1. Minimum viable input vs. good input

Generation needs a **company** and a **product**. The company must already exist
(created via `POST /v1/companies`, or already present in Lovable Cloud).

### Minimum viable — what the API will accept

```jsonc
// POST /v1/videos
{ "company_slug": "sunday-oats", "product": "the vanilla-cinnamon jar" }
```

Plus, for the company itself, the schema floor from `CompanyIn`:

| Field | Required | Constraint |
|---|---|---|
| `slug` | yes | `^[a-z0-9][a-z0-9-]*$`, 2–64 |
| `name` | yes | 1–120 |
| `category` | yes | must be one of **exactly 8** slugs (§3) |
| `bio` | yes | **min 20 chars** |
| `mission` | yes | **min 20 chars** |
| `website` | **no** | and this is the problem |
| `owner_name` | no | defaults to `"vira"` |

This will produce a video. It will very likely be dropped.

### Good input — what materially changes output quality

Ranked by measured effect, not by intuition.

**1. `website` — a real, resolving URL. The single highest-leverage field.**

Not optional in practice. Here is the mechanism, confirmed live:

- All **6** `company_insights` rows in the database have `sources: []` and
  `raw: null`. **No scrape has ever run**, because signup never captured a URL.
- The enrichment is therefore an LLM paraphrase of the user's own two sentences.
  `bio: "Selling chips"` / `mission: "More chips"` produces
  *"A highly focused snack brand dedicated to the straightforward goal of
  delivering more chips to consumers."*
- That paraphrase is templated into `company_knowledge.content` and **embedded**.
  So the retrieval **query vector** is downstream of the paraphrase.
- Measured consequence: `recommend_company_trends` for those companies returns
  top similarity **0.61–0.67**, and the sources it returns are generic
  founder-story clips.
- Measured consequence at the other end: `chips` ("Selling chips") scored
  **2.6 overall / 1.0 evidence**. `eli-health` (470-char bio, real URL) scored
  **3.8 / 2.0**. Same pipeline, same day.

**Only one company in the entire database has a website that resolves.** Four of
the five populated `website` values are `.example` placeholders. If Lovable
starts collecting one real field, collect this one.

**2. `bio` — 300+ characters that name a mechanism, not a vibe.**

The bios that scored are specific about *how the product works*:

> *"Overcast is a mineral SPF 50 serum that dries clear on every skin tone. The
> non-nano zinc is milled fine and suspended in a squalane and niacinamide base,
> so it sinks in instead of sitting on top: no grey cast, no pilling under
> makeup, no swimming-pool smell."* (445 chars)

versus `"I am the best sports drink ever"` (31 chars). The 20-char minimum is a
schema floor, not a quality bar. **Ask for 2–4 sentences and say why in the UI.**

**3. `product` — the specific SKU or claim, not the company again.**

`product` is a free-text field on the video request (2–200 chars) and it is what
the script is written about. `"the vanilla-cinnamon jar"` gives the writer
something to demonstrate; `"oats"` gives it nothing.

**4. `category` — because retrieval currently falls back to it.**

The corpus was scraped as **8 broad category buckets and nothing finer** — the
`trends.query` column contains only the 8 category slugs, and the real TikTok
search terms were never recorded. So category is not a tag, it is the retrieval
key of last resort. Getting it wrong is not cosmetic: `testcompanyqa` has a
skincare bio filed under `food-beverage`, and every source it will ever be handed
is about snacks.

**5. `mission` — least leverage of the five.** It feeds the same paraphrase. Keep
it, don't fight for it.

### Summary table

| Field | Minimum | Good | Why it matters |
|---|---|---|---|
| `company_slug` | required | — | routing only |
| `product` | 2 chars | the specific SKU + the claim | what the script demonstrates |
| `bio` | 20 chars | **300+, names a mechanism** | 2.6 → 3.8 measured |
| `website` | omitted | **a URL that resolves** | decides whether enrichment is research or paraphrase |
| `category` | required | correct, not nearest | retrieval falls back to it |
| `mission` | 20 chars | one clear sentence | small |
| `lane` | defaults `founder-story` | let the user pick, or fan out all 5 | changes voice, look and copy direction |
| `mode` | defaults `fast` | `fast` for iteration | `agentic` is ~4× slower |

---

## 2. What Lovable already has vs. what it must start collecting

Measured against the live `companies` table (n = 10).

| Field | In Lovable today | Populated | Action |
|---|---|---|---|
| `name`, `slug` | ✅ | 10/10 | none |
| `category_id` | ✅ | 10/10 | none |
| `bio` | ✅ | 10/10 — but **6 of 10 are one line** | **add a length hint and an example**; the field exists, the guidance does not |
| `mission` | ✅ | 10/10 | none |
| `owner_name` | ✅ | 10/10 | none |
| **`website`** | column exists, **signup never asks** | **5/10, and 4 of those are `.example`** | **START COLLECTING. This is the one.** |
| `logo_url` | ✅ | 5/10 | note: these are **storage object paths** (`<uuid>/<uuid>`), not URLs — resolve through Supabase storage before rendering |
| `product` | ❌ **does not exist anywhere** | — | **START COLLECTING**, per video, free text |
| `lane` | ❌ | — | **START COLLECTING** — offer the 5 from `GET /v1/lanes` |
| `mode` | ❌ | — | optional; default `fast` |

Two new inputs, then: **`website`** on the company, and **`product` + `lane`** on
the generate action. Everything else Lovable already holds.

One more, if the review loop is used: an opaque **`reviewer_ref`** per judge.
The API stores no personal data about reviewers and never needs to.

---

## 3. Reference data the UI must fetch, not hardcode

```
GET /v1/lanes                → [{name, brief, voice_note, look}]
GET /v1/corpus/categories    → [{id, name, slug, trend_count}]
```

Lanes are exactly five: **`problem-first`, `demo-first`, `founder-story`,
`social-proof`, `contrarian`**. A lane owns copy direction *and* voice *and*
look, which is why the UI should present the `brief` and `look` text rather than
just the name. `voice_id` is deliberately not exposed.

Categories are exactly eight and fixed: `apparel-accessories`, `baby-kids`,
`beauty-personal-care`, `electronics-gadgets`, `fitness-wellness`,
`food-beverage`, `home-living`, `pets`. A category outside that list is a 422
with the valid list in the message.

---

## 4. The full flow, copy-pasteable

### 4.1 Create the company (once)

```http
POST /v1/companies
Content-Type: application/json
```
```json
{
  "slug": "overcast",
  "name": "Overcast",
  "category": "beauty-personal-care",
  "bio": "Overcast is a mineral SPF 50 serum that dries clear on every skin tone. The non-nano zinc is milled fine and suspended in a squalane and niacinamide base, so it sinks in instead of sitting on top: no grey cast, no pilling under makeup, no swimming-pool smell.",
  "mission": "End the white cast. Sunscreen someone actually reapplies beats sunscreen someone owns.",
  "website": "https://overcast.co",
  "owner_name": "Chip Rarau"
}
```

**`201`**
```json
{
  "id": "17381b1e-d427-44f4-8d8b-e9a08c925dce",
  "slug": "overcast",
  "name": "Overcast",
  "category": "Beauty & Personal Care",
  "bio": "Overcast is a mineral SPF 50 serum that dries clear…",
  "mission": "End the white cast…",
  "website": "https://overcast.co",
  "video_count": null
}
```

This writes to **both** Lovable Cloud and the engine's own Postgres, in that
order. Failure modes: `409` slug taken · `422` unknown category (the response
lists the valid ones) · `502` the Lovable write failed · `503` `AGENT_USER_ID`
is unset on the server, so no RLS-acceptable owner exists.

### 4.2 Start the job

```http
POST /v1/videos
```
```json
{
  "company_slug": "overcast",
  "product": "the SPF 50 mineral serum, 30ml",
  "lane": "problem-first",
  "mode": "fast"
}
```

**`202`** — always 202, never a video.
```json
{
  "job_id": "6f1c2e0a-9a3d-4a51-9a2f-0d9f1a2b3c4d",
  "status": "queued",
  "poll": "https://api.example.com/v1/jobs/6f1c2e0a-9a3d-4a51-9a2f-0d9f1a2b3c4d",
  "estimated_seconds": 90
}
```

`estimated_seconds` is **90** for `fast` and **360** for `agentic`. Measured
reality: 74 s deterministic, ~350 s with the crew. Drive the progress bar off
this number, not off a guess.

Failure modes: `404` unknown `company_slug` · `422` unknown lane (message lists
the valid ones).

### 4.3 Watch it — pick one of three

**Best — SSE.** `GET /v1/jobs/{job_id}/stream`

```js
const es = new EventSource(`${API}/v1/jobs/${jobId}/stream`);
es.onmessage = (e) => {
  const ev = JSON.parse(e.data);   // {seq, ts, job_id, stage, message, level, data}
  setStage(ev.stage);
  setLine(ev.message);             // a human sentence, always renderable
  if (ev.stage === "done")   { es.close(); loadVideo(ev.data.video_id); }
  if (ev.stage === "failed") { es.close(); showError(ev.data.error); }
};
```

Every frame carries `id: <seq>`, so the browser's automatic reconnect resumes
from `Last-Event-ID` for free. Heartbeat comments arrive every 15 s (a Remotion
render is two silent minutes). The stream self-closes after 900 s.

**`stage`** is a growing vocabulary — `queued · select · verify · analyze · plan
· write · motion · critique · voice · imagery · cohesion · tool · director ·
crew · score · render · done · failed`. **Render unknown stages as a plain trace
line.** Never switch on the enum exhaustively; the next stage added will blank
your UI.

**`message`** is always a human sentence and is the thing to show:
`"verifying 18 source URLs"`, `"scoring against the cited sources"`,
`"rendering"`.

**Fallback — JSON polling.** `GET /v1/jobs/{job_id}/events?after={seq}`

```json
{
  "job_id": "6f1c2e0a-…",
  "source": "memory",
  "status": "running",
  "complete": false,
  "next_after": 14,
  "events": [
    {"seq": 13, "ts": "2026-08-15T23:01:04Z", "job_id": "6f1c2e0a-…",
     "stage": "verify", "message": "verifying 18 source URLs", "level": "info",
     "data": {"count": 18}},
    {"seq": 14, "ts": "2026-08-15T23:01:22Z", "job_id": "6f1c2e0a-…",
     "stage": "analyze", "message": "analysing 18 verified sources", "level": "info",
     "data": {}}
  ]
}
```

**Watch `source`.** `"memory"` means this process is running the job and you are
getting the full trace. `"database"` means another worker owns it and you are
getting one synthesised event off the shared job row — coarse, but it will still
reach `done`. Do not treat `"database"` as an error.

**Simplest — status polling.** `GET /v1/jobs/{job_id}`

```json
{
  "job_id": "6f1c2e0a-…",
  "status": "running",
  "progress_note": "scoring against the cited sources",
  "video_id": null,
  "error": null,
  "company_slug": "overcast",
  "lane": "problem-first",
  "mode": "fast",
  "created_at": "2026-08-15T23:00:41Z",
  "updated_at": "2026-08-15T23:01:58Z"
}
```

`status` ∈ `queued | running | done | failed`. Poll every 2–3 s; the job row is
the only surface shared across workers.

### 4.4 Fetch the result

```http
GET /v1/videos/{video_id}
```
```json
{
  "id": "b21d7c88-0f77-4a3e-9b1e-7c6a5d4e3f21",
  "job_id": "6f1c2e0a-…",
  "company_slug": "overcast",
  "product": "the SPF 50 mineral serum, 30ml",
  "lane": "problem-first",
  "mode": "fast",
  "hook": "You own sunscreen. You just don't reapply it.",
  "caption": "The reapply problem, solved. #spf #mineralsunscreen #nowhitecast",
  "hashtags": ["spf", "mineralsunscreen", "nowhitecast"],
  "cta": "Try it for a week of mornings.",
  "duration_s": 24.4,
  "mp4_url": "https://api.example.com/media/overcast/v003-20260815-2301/ad.mp4",
  "score": {
    "relevance": 4.0,
    "specificity": 3.5,
    "actionability": 3.5,
    "differentiation": 3.0,
    "evidence": 2.0,
    "overall": 3.2
  },
  "disposition": "dropped",
  "drop_reason": "not supported by the cited source videos",
  "created_at": "2026-08-15T23:02:55Z"
}
```

**`mp4_url` is present and playable regardless of `disposition`.** The file was
rendered before it was judged.

Two more, both useful in a UI:

```
GET /v1/videos/{id}/recipe          → {video_id, recipe: {...}}  verbatim prompts,
                                       corpus in scope, settings in force
POST /v1/videos/{id}/regenerate     → 202 {job_id, poll, estimated_seconds}
     body: {"notes": ["shorter hook", "no music bed"], "lane": "demo-first"}
```

`recipe` is deliberately untyped — pass it through, render it as a tree. Pinning
a schema would silently drop any stage added later, which is exactly the field
someone would then be missing.

`regenerate` **re-selects the corpus** rather than replaying it. Sources age out
and some are dead by now; an ad grounded in a video that no longer exists should
fail verification. What it pins is the creative input.

### 4.5 The review loop (Terac panel)

```http
POST /v1/review-batches           {"video_ids": ["…","…"], "title": "Overcast, five lanes"}
  → 201 {"batch_id": "…", "public_token": "…", "judge_url": "https://…/review/…"}

GET  /v1/review-batches/{public_token}          PUBLIC — the judge view
POST /v1/review-batches/{public_token}/votes    {"reviewer_ref","video_id","rating":1-5,"picked","comment"}
GET  /v1/review-batches/{batch_id}/results      operator view, aggregated
```

**The judge payload has no score field and cannot grow one.** `JudgeVideo` is a
separate type from `VideoOut` — no `score`, no `disposition`, no `drop_reason`,
**no lane name**. A judge told the engine gave a cut 4.2 ranks the engine; a
judge told a cut is the "contrarian" one ranks the label. If the frontend renders
the judge view, it must not fetch `/v1/videos/{id}` alongside it and reunite
what the API deliberately separated.

`video_ids` must contain **2–20** entries.

---

## 5. What the UI should show at each stage

| Stage | State | Show |
|---|---|---|
| before submit | — | the input-quality nudge: **"a real website roughly doubles the score"**, and a bio counter that turns green past ~300 chars |
| `POST /v1/videos` | 202 in ms | switch to the progress view immediately; never spin on the POST |
| `queued` | | *"queued"* + the `estimated_seconds` bar |
| `select` | | *"selecting candidate trends"* — this is retrieval over 4,617 posts |
| `verify` | | *"verifying N source URLs"* — **make this visible.** Every source is fetched before a model sees it. It is the most defensible thing the engine does and it takes real seconds |
| `analyze` | | *"analysing N verified sources"* |
| `plan` / `write` / `critique` | | the sentence; optionally the lane name and brief |
| `voice` / `imagery` | | these run **concurrently** — one row, not two sequential steps |
| `cohesion` / `motion` / `tool` / `director` | agentic only | plain trace lines |
| `score` | | *"scoring against the cited sources"* — **do not present this as a formality.** It can drop the video |
| `render` | | ~40–120 s of silence. The heartbeat keeps the connection alive; keep the bar moving off `estimated_seconds` |
| `done` | | fetch the video. Player + hook + the five score dimensions + **the disposition banner (§6)** |
| `failed` | | `error` string, and a retry that re-POSTs the same body |

After `done`, three affordances earn their place: **play**, **view recipe**
(the tweak loop — the verbatim prompts are the product), and **regenerate with
notes**.

---

## 6. Failure modes a frontend must handle

### 6.1 A dropped video is not an error — this is the important one

`disposition` ∈ `surfaced | watchlist | dropped`.

| Disposition | Condition | Frontend |
|---|---|---|
| `surfaced` | `overall ≥ 4.5` | green. Ship it. |
| `watchlist` | `overall ≥ 3.5` | amber. Playable, worth a human look. |
| `dropped` | `evidence < 3.0` → `"not supported by the cited source videos"` | **neutral, explanatory.** Not red, not an error toast. |
| `dropped` | `overall < 3.5` → `"scored 3.2, below the watchlist threshold"` | same. |

The evidence check is a **gate, not an average**: below 3.0 the video is dropped
no matter how good the other four dimensions are. As of this writing **every
video the engine has ever produced has been dropped** — 15 variants across three
companies. That is the gate working, not the pipeline breaking.

The job is `status: "done"`. The mp4 renders and plays. `score.evidence` tells
you why. Suggested copy:

> **Not shipped — the sources don't back the claim.**
> The engine scored evidence 2.0 of 5. The video is here to watch, and the
> recipe shows exactly which sources it was given.
> *[Play] [View recipe] [Regenerate with notes]*

**Do not** add a "force publish" or a threshold slider. The floor is
server-side, no request parameter can move it, and that is the point.

### 6.2 Everything else

| Failure | Signal | Frontend |
|---|---|---|
| unknown company | `404` on POST /v1/videos | *"create the company first"* |
| unknown lane / category | `422` with the valid list in `detail` | render the list |
| slug taken | `409` on POST /v1/companies | suggest a suffix |
| Lovable write failed | `502` | retryable; the corpus DB is a third party |
| server has no `AGENT_USER_ID` | `503` | configuration, not user error |
| malformed uuid | `422` `"malformed identifier: …"` | shouldn't reach a user |
| job `failed` | `status: "failed"` + `error` | show the string; offer retry |
| SSE dropped | browser auto-reconnects with `Last-Event-ID` | do nothing |
| SSE says `source: "database"` | another worker owns the job | keep going, expect coarse updates |
| job never terminates | stream self-closes at **900 s** | fall back to `GET /v1/jobs/{id}`; do not assume failure |
| `score` is `null` | video stored before scoring | render "unscored", not 0.0 |
| `mp4_url` 404s | ephemeral disk (Render), local disk (chipdev) | media is served off local disk; a redeploy loses it |

### 6.3 Corpus-side caveats the UI will trip over

From the live survey — these are not hypothetical:

- **Every `raw.coverUrl` thumbnail expires 2026-08-17 18:00–22:00 UTC.** They are
  signed TikTok CDN URLs, not stored assets. 4,614 of them. **Ship an `onError`
  fallback tile now**, or the corpus browser is a wall of broken images in two
  days.
- **"4,617 trends" overstates what generation can use.** `MAX_AGE_DAYS=90` cuts
  it to **2,541**. If the UI shows a corpus size, show both.
- **`trends.query` is the category slug, never a search term.** Don't label it
  one.
- **`companies.logo_url` is a storage path, not a URL.** 5 of 10 rows; all 5 will
  break in an `<img src>`.
- **13 trends have neither caption nor title.** Fall back to `trend_key`.
- **97 trends belong to no category** and never appear in a category-filtered
  view. The counts will not add up; that is real.
- **`company_insights.sources` is `[]` on all 6 rows.** If the UI shows an
  "enriched" badge, gate it on `sources` being non-empty — the corpus endpoint
  already computes `enriched: bool(sources)` for exactly this reason.
- **`pets` and `home-living` have zero word-of-mouth rows.** Any "customer
  voice" panel must render an honest empty state for those two categories.

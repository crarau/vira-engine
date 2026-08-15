# Context retrieval — spec

*Measured against the live database on 2026-08-15, 21:30.*

How we get from "we have a pile of TikToks, tweets, Reddit threads, cover images
and 50 hours of video" to "here are the eleven things that should shape *this*
ad for *this* company."

## 1. Why this exists

Every variant we have generated has been dropped, and almost all of them on the
same dimension:

| Company | Lanes | Overall | Evidence | Outcome |
|---|---|---|---|---|
| Sunday Oats | 5 | 2.8 – 3.6 | — | all dropped |
| Bramble | 5 | 3.2 – 3.6 | 2.0 (one at 3.0) | all dropped |
| Overcast | 5 | 3.2 – 3.4 | **1.0 across all five** | all dropped |

The scorer asks whether the cited source videos support the claim the ad makes.
They don't — because the twenty "sources" were selected by **category alone**.
Overcast sells a mineral SPF serum; it was handed twenty videos whose sole
qualification was *beauty*. Fragrance, lash glue, shampoo. Nothing about white
cast.

The ads are not the problem. The retrieval is. `beauty-personal-care` is 380
videos, and picking twenty of them by recency and format quota is not evidence
for a specific product claim.

**Do not lower the evidence floor.** It is the only thing in the system telling
the truth right now.

## 2. What we actually have

Measured, not assumed.

| Asset | Count | Retrievable today |
|---|---|---|
| `trends` — TikTok posts | 2,999 | `embedding` 1536-dim, **100% populated**, **zero readers** |
| `trends.raw.coverUrl` — cover image | 2,999 | URL only, never fetched |
| `trends.raw.videoUrl` — the video | 2,999 · **50.2 h**, median 37 s | URL only, never fetched |
| captions | 2,993 non-empty, median 154 chars | inside the embedding |
| `word_of_mouth` | 333 → twitter 316 · reddit 17 | **no embedding column at all** |
| `category_word_of_mouth` | 334 rows, only **3** of 8 categories | coarse |
| `prescripts` | 100 | synthetic (`CROSS JOIN` of 10 formats × 10 angles) |
| `company_knowledge` | **0** | pgvector table, built, never populated |

Two things stand out. The embeddings we already paid for are unread. And the
word-of-mouth corpus — the only place customers speak in their own words — has
no vector column, so it cannot be retrieved at all.

### Known data-quality blockers

- **Reddit ingestion is pulling the wrong content.** The 17 rows came from
  r/pettyrevenge, r/revengestories, r/SubredditDrama, r/funny, r/cats — none of
  which appear in `reddit-sources.ts`. The actor is returning front-page viral
  posts rather than the configured subreddit listings.
- **Reddit engagement is all zero.** `likes` and `replies` are 0 on every row, so
  `buzz_score` is computed from freshness and length alone.
- **`author_handle` is doubled** (`r/r/GirlDinnerDiaries`) and empty on 5 rows.
- **Twitter queries are skewed**: 321 of 333 rows are apparel/clothing.

Fix these before embedding anything. A vector index over r/pettyrevenge is worse
than no index — it retrieves confidently and wrongly.

## 3. Context is three different jobs

This is the framing the current design is missing. "Context" is not one thing,
and the three kinds live in different modalities.

| Job | Question it answers | Where it lives | Status |
|---|---|---|---|
| **Evidence** | Has this format demonstrably worked for a problem like ours? | caption, hashtags, engagement | done badly (category-matched) |
| **Craft** | How is it actually shot — pacing, framing, on-screen text? | **the video and the cover image** | unavailable |
| **Voice** | How do customers describe this problem unprompted? | Reddit / Twitter threads | unavailable (no embeddings) |

The score gate tests Evidence. The remix prompt needs Craft. The hook needs
Voice. Right now we approximate one and have neither of the others.

## 4. The design

### 4.1 One table, every modality

Everything becomes text plus a vector. Images become text. Videos become text.
Then there is exactly one retrieval path instead of four.

```sql
create table context_chunks (
  id            uuid primary key default gen_random_uuid(),
  source_table  text not null,        -- trends | word_of_mouth | company
  source_key    text not null,        -- trend_key | wom_key | slug
  modality      text not null,        -- caption | cover | frames | transcript | thread
  tier          smallint not null,    -- 0 cheap … 2 expensive
  text          text not null,        -- what got embedded, human-readable
  embedding     vector(1536),
  meta          jsonb,                -- engagement, age, format, subreddit, url
  created_at    timestamptz default now()
);
create index on context_chunks using hnsw (embedding vector_cosine_ops);
```

`text` stays readable on purpose: whatever we retrieve, we can paste into a
recipe and a human can check whether it deserved to be there.

### 4.2 The ingestion funnel — do not process 50 hours of video

The instinct is a pipeline that transcribes every video and captions every
image. That is 50 hours of video and 2,999 vision calls before anyone sees an
ad. Wrong shape. **Cheap modalities run on everything; expensive modalities run
only on what already survived retrieval.**

```
Tier 0  ALL 2,999 + 333     caption · hashtags · title · thread text
        cost: zero, already embedded for trends
        → gets us from 380 category matches down to ~40 candidates

Tier 1  ALL 2,999           cover image → one vision call →
        "what is physically on screen" in ~40 words → embed
        one-time backfill, then incremental
        → captures what captions never say: hands, lighting,
          product in frame, on-screen text, face vs no face

Tier 2  ~20 per run         the ones that survived Tier 0+1 for THIS company:
        sample 4–6 frames + transcribe audio →
        beat structure (0–2 s hook, 3–8 s demo, 9–14 s proof)
        → this is Craft context, and it is what the remix
          prompt has never had
```

Tier 2 costs twenty videos per run rather than 2,999. That is the whole trick:
the expensive modality is a *function of the query*, not of the corpus.

### 4.3 Retrieval

```
query vector = embed(company.bio + mission + product + the specific beat need)
```

Then `match_context(query_embedding, filters, k)` — filters being age, category,
platform, modality, minimum engagement.

### CORRECTION (21:55) — the vector RPC already exists

An earlier draft of this document claimed no vector RPC existed. **That was
wrong**, and the error is worth recording because it nearly caused us to build
something that ships already.

I probed with guessed names (`match_trends`, `search_trends`, `match_documents`)
and got `PGRST202`. The two RPCs that do exist returned **401, not 404** — and I
read a permission error as an absence. They are `REVOKE`d from `anon` and
`GRANT`ed to `authenticated`, and I was probing with the publishable key.

What is actually in the database, confirmed by reading the migrations in
`company-essence-lab` and then calling both as the signed-in agent:

```sql
recommend_company_trends(_company_id uuid, _limit int, _query_embedding vector(1536))
match_company_knowledge(query_embedding vector(1536), match_count int, exclude_company uuid)
```

`recommend_company_trends` is precisely the retrieval this document proposed:

```sql
1 - (t.embedding <=> q.v) AS similarity
...
0.8 * similarity + 0.2 * percent_rank() OVER (ORDER BY trend_score) AS combined_score
```

Cosine over the HNSW index, over-fetching 4× then re-ranking with a 0.8/0.2
blend of similarity and trend score. Verified live: seeded with a protein-shake
video's embedding it returns protein-shake videos at similarity 0.84, 0.81,
0.78. It works.

**So step 1 is not "build local cosine". It is "pass a query embedding".**

The reason it currently returns zero rows is the fallback:

```sql
SELECT COALESCE(_query_embedding, k.embedding) FROM companies c
LEFT JOIN company_knowledge k ON k.company_id = c.id
```

With no `_query_embedding` it falls back to `company_knowledge.embedding`, and
`company_knowledge` has rows for only 6 of 10 companies — Chips, rebull, TestQA,
Glowry, squirt, vira. Nothing for bramble, overcast, sunday-oats or eli-health.
`q.v IS NULL` → `WHERE q.v IS NOT NULL` → no rows.

Two ways to fix, both small:

- **Pass `_query_embedding` explicitly** from the engine — embed
  `bio + mission + product` with the same 1536-dim model. Needs nothing from
  anyone and works today.
- **Populate `company_knowledge`** for the missing four, which also fixes Jesh's
  UI, since it hits the same fallback.

Local cosine remains a valid fallback (200 rows in 1.4 s / 3.8 MB; the full
corpus is ~75 MB at 3,976 rows) but is no longer the first move.

### 4.4 What the remix prompt receives

Instead of twenty category-matched captions:

```
EVIDENCE   8 posts semantically near this product's problem, with
           engagement, age, verified URL
CRAFT      beat structure of the 3 highest-scoring of those, from
           actual frames and audio
VOICE      5 Reddit/Twitter excerpts where customers describe this
           problem in their own words, verbatim
WHITESPACE what the corpus does NOT do (already working — it produced
           the timed-lick-session and layering-test findings)
```

Every line traceable to a URL a judge can open.

## 5. What we win

**The evidence gate stops being a wall.** Right now it rejects everything, which
proves it works but ships nothing. Retrieval is the only honest way through it:
give the model sources that genuinely support the claim, and the score moves for
a real reason rather than a tuned threshold.

**Terac — this is the strongest card we have.** The track wants real human input
that measurably improves the project, with a clear before and after. The
*before* is already on disk and was recorded before we knew the answer: fifteen
variants across three companies, every prompt captured verbatim in `RECIPE.md`,
every score and drop reason saved. So:

1. Panel ranks the five lanes for a company. That ranking is the human input.
2. Ranking feeds retrieval weights and the lane briefs.
3. Re-run. Same company, same product, new corpus.
4. Diff the two `RECIPE.md` files — the change *is* the diff.

Most teams will collect feedback and assert it helped. We can show the prompt
that changed, the sources that changed, and the score that moved. The before/
after is credible precisely because we did not tune anything to make it look
good.

**Render track.** Tier 1 and Tier 2 are batch jobs with a queue — the Render
worker already exists and is running on a 30-minute tick.

**The demo line.** "We don't summarise a corpus. We retrieve from it, we verify
every source before a model sees it, and we refuse to ship an ad the sources
don't support." Everyone else will show their wins. We can show what we threw
away and why.

## 6. Build order

Cheapest first; each step is independently useful and independently
demonstrable.

| # | Step | Effort | Unlocks |
|---|---|---|---|
| 1 | Local cosine retrieval over `trends.embedding` | hours | kills the category-matching problem outright |
| 2 | Fix Reddit ingest (subreddit targeting, engagement mapping, `r/r/`) | hours | stops poisoning everything downstream |
| 3 | `embedding` column + backfill on `word_of_mouth` (333 rows) | small | **Voice** context |
| 4 | Tier 1 cover-image vision pass (2,999) | one batch | **Craft** signal at scale |
| 5 | `context_chunks` + `match_context` RPC | medium | one retrieval path, scales past local cosine |
| 6 | Tier 2 lazy frame/transcript on top-20 | medium | beat-level craft in the remix prompt |
| 7 | Populate `company_knowledge` from bio + website | medium | company side of the match |

Step 1 alone is testable tonight: re-run Overcast against a semantically
retrieved corpus and see whether evidence moves off 1.0. If it doesn't, the
hypothesis in this document is wrong, and that is worth knowing before building
steps 2–7.

## 7. Open questions

- Does the panel rank *lanes* (creative angle) or *ads* (execution)? Ranking
  lanes is the cleaner signal, but only if imagery quality is held constant —
  and it currently is not.
- Whose embedding model? `trends.embedding` is 1536-dim; the company-side query
  vector must come from the same model or the geometry is meaningless. This is
  worth confirming with Jesh before backfilling anything.
- Reddit content licence for anything we quote verbatim on screen.

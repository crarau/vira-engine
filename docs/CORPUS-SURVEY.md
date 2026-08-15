# Corpus survey — Lovable Cloud, project `otsqjpmsiysitpkqoejr`

*All numbers measured live on **2026-08-15, 22:42–22:58 UTC**. Read-only. Nothing
was written, created or migrated.*

Every count in this document came from a query printed next to it. Re-run them
and you should get the same shape and larger numbers — the corpus is being
actively scraped and grew by 641 `trends` rows and 842 `word_of_mouth` rows
during the hours before this survey.

**Headline: three of the four things `CONTEXT-RETRIEVAL.md` said were missing
now exist.** Embeddings are populated on 100% of `trends`. `company_knowledge`
is no longer empty. Reddit is no longer garbage. What is still missing is the
company-side query vector for the four engine-created companies, and any vector
at all on `word_of_mouth`.

---

## 0. How to re-run any of this

```python
# .venv/bin/python
import asyncio, sys
sys.path.insert(0, "/Users/ciprian.rarau/ideaplaces-meta/experiments/vira-engine")
from vira.supa import Supa

async def main():
    anon  = Supa()                 # publishable key, RLS-bound
    agent = await Supa.signed_in() # AGENT_EMAIL / AGENT_PASSWORD → JWT, 60-min TTL
    ...
asyncio.run(main())
```

Two traps that cost time here, both worth knowing before you re-run anything:

- **`GET /rest/v1/` (the PostgREST OpenAPI root) returns `401 Secret API key
  required`.** There is no schema introspection with the publishable key, so the
  table list below was reconstructed from the migrations in
  `company-essence-lab` (`git show origin/main:supabase/migrations/*.sql`, 15
  files) and then confirmed by counting each one live. To rule out tables added
  outside migrations I probed **61 plausible names** (`context_chunks`,
  `tweets`, `reddit_posts`, `video_transcripts`, `trend_enrichment`, `assets`,
  `images`, `chunks`, `embeddings`, …). **All 61 returned 404.** There is no
  hidden table.
- **`Supa.select_all` breaks if the caller also passes `limit`** —
  `TypeError: select() got multiple values for keyword argument 'limit'`. It
  pages in units of 1000 itself. Don't pass one.

The local clone of `company-essence-lab` is **148 commits behind `origin/main`**.
`git fetch` first, and read migrations with `git show origin/main:<path>` rather
than off the working tree.

---

## 1. Every table, its row count, and what it is for

```python
# per table:
r = await client.get(f"{s.url}/rest/v1/{t}",
      headers={**s._headers(), "Prefer": "count=exact", "Range": "0-0"},
      params={"select": "*"})
n = int(r.headers["content-range"].split("/")[-1])
```

| Table | anon | agent | What it is | Movement |
|---|---:|---:|---|---|
| `trends` | 4,617 | 4,617 | scraped TikTok posts — **the corpus** | 2,999 → 3,976 → **4,617** |
| `category_trends` | 4,530 | 4,530 | category ↔ trend join | grew with `trends` |
| `word_of_mouth` | 1,175 | 1,175 | Reddit + Twitter posts — **the Voice corpus** | 333 → **1,175** |
| `category_word_of_mouth` | 1,189 | 1,189 | category ↔ wom join | 334 → **1,189** |
| `companies` | 10 | 10 | brands; 4 real-ish, 6 test | unchanged |
| `company_insights` | 6 | 6 | LLM enrichment, one per Lovable-created company | unchanged |
| `company_knowledge` | **0** | **6** | pgvector company-side query vector | **was 0, now 6** |
| `categories` | 8 | 8 | fixed consumer taxonomy | unchanged |
| `prescripts` | 100 | 100 | synthetic script library (`CROSS JOIN` 10 formats × 10 angles) | unchanged, still synthetic |
| `category_prescripts` | 309 | 309 | assigned by `hashtext() % 100 < 40`, i.e. random | unchanged |
| `company_remixes` | 0 | 0 | finished ads. **Never produced a row.** | still empty |
| `remix_chats` | 0 | 0 | Jesh's chat threads — RLS hides other users' rows | invisible to us |
| `remix_chat_messages` | 0 | 0 | same | invisible to us |
| `profiles` | **401** | 1 | user profiles; anon has no grant at all | our agent's row only |
| `subscriptions` | 0 | 0 | Stripe state — RLS-scoped to `user_id` | invisible to us |

`anon` vs `agent` differs on exactly two rows of that table: `company_knowledge`
(0 vs 6, RLS) and `profiles` (401 vs 1, no grant). Everything else is
`GRANT SELECT ... TO anon`, so the corpus browser can read the corpus with the
publishable key and no login.

### Appeared recently

- **`word_of_mouth` more than tripled** and flipped platform mix. Two ingest
  batches are visible in `created_at`: 333 rows at 21:xx (Twitter-heavy, the
  batch documented as broken) and **842 rows at 22:xx** (Reddit, and good — §5).
- **1,618 new `trends` rows** in two batches (159 at 21:xx, 1,459 at 22:xx) on
  top of the original 2,999 at 18:xx.
- **`company_knowledge` went from 0 → 6 populated rows with real 1536-dim
  vectors** (§4). This is the single most consequential change since
  `CONTEXT-RETRIEVAL.md` was written.

### Nothing new exists beyond these

No `context_chunks`. No transcript table. No image-description table. No
per-frame table. The multimodal design in `CONTEXT-RETRIEVAL.md` §4.1 is still
entirely unbuilt.

---

## 2. `trends` — 4,617 rows

```python
await s.count("trends")                                   # 4617
await s.count("trends", embedding="not.is.null")          # 4617
await s.count("trends", **{"raw->>coverUrl": "not.is.null"})  # 4617
rows = await s.select_all("trends", select="trend_key,platform,format,query,views,"
        "likes,comments,shares,engagement_rate,trend_score,posted_at,created_at,"
        "caption,title,hashtags")
```

### Age distribution (from `posted_at`, 0 nulls)

| Bucket | Rows | Share |
|---|---:|---:|
| 0–7 days | 776 | 16.8% |
| 7–30 days | 879 | 19.0% |
| 30–90 days | 886 | 19.2% |
| **≤ 90 days (the selection window)** | **2,541** | **55.0%** |
| 90–180 days | 659 | 14.3% |
| 180–365 days | 618 | 13.4% |
| 1–2 years | 572 | 12.4% |
| > 2 years | 227 | 4.9% |

Median age **71.2 days**, oldest **2,294 days** (Aug 2020). `MAX_AGE_DAYS=90`
therefore throws away 45% of the corpus — deliberately, and correctly, but it
means the effective corpus for generation is **2,541 rows, not 4,617**. A UI
showing "4,617 trends" is overstating what the engine can actually use by 82%.

### Per-category distribution

`category_trends` maps 4,520 of 4,617 trends (**97 trends belong to no
category** and are invisible to any category-joined query). 10 trends are mapped
to two categories.

| Category slug | `category_trends` rows |
|---|---:|
| food-beverage | 692 |
| electronics-gadgets | 668 |
| apparel-accessories | 650 |
| beauty-personal-care | 650 |
| baby-kids | 627 |
| fitness-wellness | 483 |
| home-living | 380 |
| pets | 380 |

### What `query` values were used to scrape

**`query` is the category slug and nothing else.** 8 distinct values, exactly the
8 category slugs:

```
732 food-beverage · 668 electronics-gadgets · 650 beauty-personal-care
650 apparel-accessories · 626 baby-kids · 535 fitness-wellness
379 pets · 377 home-living
```

`raw` carries only `{id, author, coverUrl, duration, videoUrl}` — **the actual
TikTok search term is not recorded anywhere.** This matters: the corpus was built
by scraping eight broad category buckets, so there is no product-level scrape to
fall back on. Semantic retrieval over the embeddings is the *only* way to get
below category granularity. That is the failure in `CONTEXT-RETRIEVAL.md` §1,
restated as a property of the ingest rather than of the selector.

### `raw.coverUrl` — usable, but expires in under 48 hours

| Check | Result |
|---|---|
| `raw.coverUrl` present | **4,614 / 4,617 (99.9%)** |
| `raw.videoUrl` present | 4,617 / 4,617 (100%) |
| `raw.duration` present | 4,617 / 4,617 (100%) — total **80.9 h**, median 39 s, max 1,041 s |
| coverUrl live fetch, 12 random | **12 × HTTP 200** |
| coverUrl carries an `x-expires` signature | 4,614 / 4,614 |
| Earliest expiry | **2026-08-17 18:00 UTC** |
| Latest expiry | 2026-08-17 22:00 UTC |
| Already expired at survey time | 0 |

**Every cover image in the corpus dies on 17 August 2026.** They are signed
TikTok CDN URLs, not stored assets. A corpus browser that renders thumbnails
from `raw.coverUrl` works today and shows 4,614 broken images the day after
tomorrow. Either the scraper must refresh them, or thumbnails must be
re-hosted, or the UI needs an `onError` fallback tile. This is not a maybe.

### Format mix

| Format | Rows |
|---|---:|
| UGC social proof | 2,367 |
| Review / testimonial | 568 |
| Unboxing / haul | 461 |
| Tutorial / hack | 422 |
| Offer / launch | 311 |
| GRWM routine | 287 |
| Before / after | 95 |
| Behind the scenes | 63 |
| POV / storytime | 43 |

`MAX_PER_FORMAT=4` exists because half the corpus is one format.

### Source URLs are live

Running the real `vira.verify` logic (same UA, same `GONE_MARKERS`, body
truncated to 20 kB) over **40 random rows: 40 verified, 0 dropped.** The corpus
is not rotten. Note that a naive check without the 20 kB truncation and with a
non-browser UA reports false "removed" on every row — TikTok's fallback HTML
contains the marker strings. Use `vira.verify`, not your own loop.

---

## 3. Field population — what a corpus browser can actually display

n = 4,617. This is the table the UI should be built against.

| Field | Populated | Verdict for the UI |
|---|---:|---|
| `trend_key` | 100% | safe — primary display key |
| `author` | 100% | safe |
| `source_url` | 100% | safe, and live (§2) |
| `format` | 100% | safe — every row has one of 9 values |
| `posted_at` | 100% | safe — never null, so `age_days` is always computable |
| `views` > 0 | 100% | safe |
| `trend_score` > 0 | 100% | safe |
| `music` | 99.9% | safe |
| `raw.coverUrl` | 99.9% | **safe today, 100% broken after 2026-08-17 18:00 UTC** |
| `raw.videoUrl` / `raw.duration` | 100% | safe |
| `likes` > 0 | 98.8% | safe; 57 rows legitimately have 0 |
| `engagement_rate` > 0 | 98.8% | safe; same 57 rows |
| `caption` | 99.7% (4,604) | safe, but **13 rows have no caption and no title** — render the trend_key |
| `title` | 99.7% | duplicates `caption` in practice; prefer `caption` |
| `comments` > 0 | 89.8% | 473 rows show 0 — real, not missing |
| `shares` > 0 | 87.9% | 557 rows show 0 — real, not missing |
| `hashtags` | non-empty on most | fine as a chip row; cap the render |
| `query` | 100% | **only ever the category slug** — do not label it "search term" |
| `embedding` | 100% | never send it to a browser; ~30 kB of text per row |

Caption length: median 152 chars, p90 459.

**Language.** Only **29.6% of captions are ASCII-only** (1,367 / 4,617). The
engine's `ENGLISH_ONLY=true` filter is throwing away a large, unmeasured share of
the corpus — some genuinely non-English, some English captions carrying emoji.
Nobody has measured which. A UI filter labelled "English only" would be
misleading; call it what it is.

### `companies` field population (n = 10)

| Field | Populated |
|---|---|
| `bio` | 10/10 |
| `mission` | 10/10 |
| `owner_name` | 10/10 |
| `status` | 10/10, all `published` |
| `website` | **5/10** |
| `logo_url` | 5/10 (Supabase storage paths, not URLs — `<uuid>/<uuid>`) |
| `company_insights` row | 6/10 |
| `company_knowledge` row | 6/10 |

`logo_url` is a storage object path, **not** a fetchable URL. A UI that drops it
into `<img src>` gets a broken image on all five.

---

## 4. Embeddings — the answer is yes, mostly

### Is `company_knowledge` still empty? **No. 6 rows, all with real vectors.**

```python
ck = await agent.select("company_knowledge", select="*")   # 6 rows
len(json.loads(ck[0]["embedding"]))                        # 1536
```

| Company | `content` length | Embedding | Source text |
|---|---:|---|---|
| chips | 719 ch | 1536-dim ✓ | templated block, below |
| rebull | 927 ch | 1536-dim ✓ | same |
| testqa-energy-drinks | 1,353 ch | 1536-dim ✓ | same |
| testcompanyqa | 1,270 ch | 1536-dim ✓ | same |
| squirt | 815 ch | 1536-dim ✓ | same |
| vira | 1,130 ch | 1536-dim ✓ | same |

**The source text is a fixed template**, verbatim from the `content` column:

```
Company: {name}
Owner: {owner_name}
Category: {category_name}
Who they are (bio): {bio}
Mission / intention: {mission}
Positioning: {positioning}
Brand tone: {tone}
Summary: {summary}
Keywords: {keywords}
Ad themes: {ad_themes}
```

Everything after `Mission` comes from `company_insights`, which is itself an LLM
paraphrase of the bio and mission (§6). So the query vector for "Chips" is, in
substance, an embedding of *"Selling chips / More chips"* expanded to 719
characters. **A longer paraphrase of two sentences is not more information.**

**The four companies the engine created — `eli-health`, `sunday-oats`,
`bramble`, `overcast` — have no `company_knowledge` row.** They were inserted
straight into `companies` via the agent JWT, which never triggers the Lovable
enrichment edge function. That is exactly why retrieval returns nothing for
them, and it is unchanged since `DATA-BOUNDARY.md` was written.

### Does any other table have vectors?

| Table | Vector column | State |
|---|---|---|
| `trends` | `embedding vector(1536)`, HNSW `vector_cosine_ops` | **4,617 / 4,617 populated (100%)** |
| `company_knowledge` | `embedding vector(1536)`, HNSW | 6 / 6 populated |
| `word_of_mouth` | **none** | `column word_of_mouth.embedding does not exist` (42703) |
| everything else | none | — |

The `trends` embedding source text is not recorded in the schema. Its behaviour
(§4, seeded test) is consistent with caption + hashtags. The model is still
unconfirmed — **this remains the one open question that blocks writing any query
vector of our own.** 1536 dims is consistent with `text-embedding-3-small`,
`text-embedding-ada-002` and others; dimension agreement is not model agreement,
and a mismatched model produces similarity numbers that look plausible and mean
nothing.

### Do the `match_*` RPCs actually return rows?

| RPC | anon | agent | Notes |
|---|---|---|---|
| `company_trends(_company_id,_limit)` | OK | OK | category join, the known-stale path |
| `company_prescripts(_company_id,_limit)` | OK | OK | synthetic library |
| `company_word_of_mouth(_company_id,_limit)` | OK | OK | category join, **no vectors involved** |
| `recommend_company_trends(_company_id,_limit,_query_embedding)` | **401** | OK | the real retrieval path |
| `match_company_knowledge(query_embedding,match_count,exclude_company)` | **401** | OK | company↔company similarity |
| `match_context` / `match_trends` / `match_word_of_mouth` / `recommend_company_word_of_mouth` / `search_trends` | 404 | 404 | **do not exist** |

`recommend_company_trends` with **no** explicit embedding, per company:

| Company | Rows | Top similarity |
|---|---:|---|
| chips | 5 | 0.609 |
| rebull | 5 | 0.642 |
| testqa-energy-drinks | 5 | 0.653 |
| testcompanyqa | 5 | 0.646 |
| squirt | 5 | 0.668 |
| vira | 5 | 0.623 |
| **eli-health** | **0** | — |
| **sunday-oats** | **0** | — |
| **bramble** | **0** | — |
| **overcast** | **0** | — |

Two things to read off that. The four zeros are the missing
`company_knowledge` rows, as documented. And the six non-zeros are **weak** —
top similarity 0.61–0.67, and the actual returned captions are generic
founder-story clips ("Not just a brand. A mindset. A movement."), because the
query vector is an embedding of a paraphrase of "Selling chips". Retrieval is
*wired* but the company side of the match is near-empty of signal.

**Seeded test — the retrieval itself is sound.** Feeding a real trend's own
embedding in as `_query_embedding`:

```python
seed = await agent.select("trends", select="trend_key,caption,embedding",
                          caption="ilike.*clothing brand*", limit=1)
v = json.loads(seed[0]["embedding"])
await agent.rpc("recommend_company_trends",
                {"_company_id": <any>, "_limit": 8, "_query_embedding": v})
```

Seed: *"We're not another soulless clothing brand… we're a woman-owned small
business"*. Returns, in order: itself at similarity 1.000, then 0.679 / 0.659 /
0.654 / 0.644 / 0.637 / 0.631 / 0.624 — every one of them a small-brand
kids/clothing post. **The vector index works.** What is missing is a query
vector worth sending it.

`match_company_knowledge` seeded with the `chips` vector returns all 6 companies
ranked (chips 1.000, testqa 0.754, testcompanyqa 0.742, squirt 0.737, vira
0.723, rebull 0.711). Functional, but with 6 rows — 4 of them test accounts —
it has no practical use today.

### Hard limits on `recommend_company_trends`

`LIMIT LEAST(GREATEST(_limit,1),24)`, over a candidate pool of `_limit * 4`.
**It cannot return more than 24 rows, from a pool of at most 96.** The engine's
`SHORTLIST_SIZE=20` fits, but there is no headroom for over-fetch-then-filter,
and it applies **no freshness filter** — the pool is nearest-neighbour over the
whole 4,617, 45% of which is older than the 90-day window. Semantic retrieval
and the freshness rule currently cannot both be enforced server-side.

---

## 5. Better retrieval than category-alone — what is available now

Ranked by what is actually usable today.

1. **`recommend_company_trends` with an explicit `_query_embedding`.** Works,
   verified, needs nothing from Jesh except confirmation of the embedding model.
   This is the fix for the failure in `CONTEXT-RETRIEVAL.md` §1. Caps at 24
   rows, no freshness filter.
2. **Local cosine over `trends.embedding`.** All 4,617 vectors are readable with
   the publishable key. ~30 kB/row of text encoding, so ~140 MB for the full
   pull — heavy but one-off, and it lets freshness, format quota and category
   be applied *before* the top-k cut, which the RPC cannot do.
3. **`word_of_mouth` as Voice context — retrievable only by category join.**
   `company_word_of_mouth` works but is category-granular, and the categories
   are lopsided (below). No vector column exists, so there is no semantic path.
   Keyword filtering over 1,175 rows of `content` is crude and works.
4. **`company_knowledge` ↔ `match_company_knowledge`** for "what did similar
   brands do" — dead on arrival with 6 rows.

### `word_of_mouth` — the Reddit ingest has been fixed

| | Rows |
|---|---:|
| **reddit** | **859** |
| twitter | 316 |

The failure documented in `CONTEXT-RETRIEVAL.md` §2 is **gone from the new
batch**. `author_handle` is no longer doubled (`r/findfashion`, not
`r/r/findfashion`; 10 rows still empty), and the subreddits are on-topic:

```
62 r/findfashion · 59 r/advertising · 58 r/SkincareAddiction · 50 r/ThriftStoreHauls
45 r/branding · 45 r/marketing · 43 r/PPC · 39 r/gadgets · 37 r/streetwear
35 r/BuyItForLife · 31 r/NewParents · 30 r/daddit · 25 r/nutrition
24 r/Indiemakeupandmore · 24 r/BabyBumps · 24 r/beyondthebump · 20 r/toddlers
20 r/malefashionadvice · 19 r/MakeupAddiction · 19 r/fragrance · 18 r/headphones
18 r/Sneakers · 15 r/energydrinks · 13 r/Supplements · 12 r/smarthome
12 r/Mommit · 12 r/FacebookAds · 11 r/Coffee · 7 r/Fitness
```

Residue from the old broken ingest is still present (`r/cats`,
`r/revengestories` turn up in keyword searches) but is now a minority.

Caveats that still apply:

- **Engagement is mostly zero.** `views` 0 on 872/1,175, `likes` 0 on 693,
  `replies` 0 on 778, `reposts` 0 on 967, `quotes` 0 on 1,053. `buzz_score` is
  therefore computed largely from freshness and length. Do not rank by it.
- **The Twitter half is junk.** 316 rows from 3 queries
  (`clothing brand ad -filter:replies`, `why I love this clothing brand`,
  `outfit trend everyone is wearing`); samples are bare `t.co` links, K-pop
  birthday posts and AI-tool spam. The Reddit half is where the signal is.
- **`category_word_of_mouth` is lopsided**: apparel-accessories 509,
  beauty-personal-care 383, baby-kids 147, electronics-gadgets 79,
  fitness-wellness 45, food-beverage 26. **`pets` and `home-living` have zero
  Voice coverage at all.**
- `topic` is only 3 values (trends 903, advertising 140, identity 132) and
  `theme` is dominated by "General mention" (579). Neither is a useful filter.
- Content is substantial: 1,175/1,175 non-empty, median 342 chars. These are
  real posts, not titles.

### Content types beyond TikTok

| Type | Present? |
|---|---|
| TikTok posts | **4,617** — `platform` is `tiktok` on 100% of `trends` |
| Reddit threads | **859** in `word_of_mouth` |
| Tweets | **316** in `word_of_mouth`, low quality |
| Cover images | **4,614 URLs** — never fetched, never described, expire 2026-08-17 |
| Video files | **4,617 URLs, 80.9 h** — never fetched, never transcribed |
| Video transcripts | **none** — no table, no column |
| Image descriptions | **none** — no table, no column |
| YouTube / Instagram | none |

So: two text modalities live, two media modalities present as URLs only. Craft
context (`CONTEXT-RETRIEVAL.md` §3) remains entirely unavailable.

---

## 6. `companies` — 10 rows, and the enrichment problem is confirmed

```python
await agent.select("companies", select="*", order="created_at.asc")
```

| slug | category | website | insights | knowledge | Real? |
|---|---|---|---|---|---|
| `chips` | food-beverage | **null** | ✓ | ✓ | test — bio is "Selling chips" |
| `rebull` | food-beverage | **null** | ✓ | ✓ | test — bio is "I am the ceo" |
| `eli-health` | fitness-wellness | **`https://www.eli.health`** | ✗ | ✗ | **real company, real URL** |
| `sunday-oats` | food-beverage | `https://sundayoats.example` | ✗ | ✗ | fictional demo, placeholder URL |
| `bramble` | pets | `https://bramble.example` | ✗ | ✗ | fictional demo, placeholder URL |
| `overcast` | beauty-personal-care | `https://overcast.example` | ✗ | ✗ | fictional demo, placeholder URL |
| `testqa-energy-drinks` | food-beverage | **null** | ✓ | ✓ | QA fixture |
| `testcompanyqa` | food-beverage | `https://qa-test.example.com` | ✓ | ✓ | QA fixture; bio says skincare, category says food |
| `squirt` | food-beverage | **null** | ✓ | ✓ | test — bio is "i am the founder" |
| `vira` | food-beverage | **null** | ✓ | ✓ | test — "I am the best sports drink ever" |

**Four real-ish companies** (eli-health, sunday-oats, bramble, overcast — all
owned by `55968844-…`, the engine agent, all with 370–470 char bios). **Six test
rows**, five of which have one-line bios.

**Only one company in the entire database has a website that resolves:**
`eli-health`. Four of the five populated `website` values are `.example`
placeholders.

### The enrichment evidence, confirmed live

All six `company_insights` rows have **`sources: []` and `raw: null`**. Not one
scrape has ever run — including for `testcompanyqa`, which *has* a website value
(a fake one). The output is fluent and cites nothing:

> **chips** — bio "Selling chips", mission "More chips" →
> *"A highly focused snack brand dedicated to the straightforward goal of
> delivering more chips to consumers."*
> keywords: `['selling chips', 'more chips', 'food and beverage', 'snack
> products', 'simple snacks', 'chip brand']`

That paraphrase is then templated into `company_knowledge.content` and embedded,
so the *retrieval query vector itself* is downstream of a two-sentence input.
This is the mechanism behind the `BUILD-LOG.md` finding, and it is worse than
documented: it does not just produce a vague brief, it produces a vague **query
vector**, which then retrieves generic sources, which then fails the evidence
gate. One null field propagates through the whole pipeline.

**`website` is the highest-leverage field in the system.** Everything downstream
of enrichment is a function of it.

---

## 7. Blunt list of what is empty or unusable

- `company_remixes`: **0 rows.** The Lovable app's headline artefact has never
  produced one. There is nothing to read here.
- `prescripts` / `category_prescripts`: 100 + 309 rows, **synthetic**. Titles are
  `"{angle} — {format}"` over a 10×10 cross join; `trend_score` is
  `round(0.55 + ((fi*7 + ai*3) % 40)/100, 2)`, a hash of loop indices. Ignore
  them; `trends` supersedes them.
- `word_of_mouth.embedding`: **does not exist.** No semantic Voice retrieval.
- `company_insights.sources` / `.raw`: **empty on all 6.** No scrape has ever run.
- `trends.query`: category slug only. **The real search terms were not recorded.**
- Cover images: **all expire 2026-08-17.**
- Video and audio: 80.9 h of URLs, **zero processed.**
- `remix_chats`, `remix_chat_messages`, `subscriptions`: 0 visible, and they will
  stay 0 — RLS scopes them to other users. Not a bug.
- `logo_url`: storage paths, not URLs.
- 97 trends map to no category and are invisible to every category-joined query.
- `match_context`, `match_trends`, `match_word_of_mouth`: **do not exist.** Do not
  write code against them.

---

## 8. Three candidate demo companies

Not Sunday Oats. Each is scored on what the corpus can actually ground: TikTok
evidence in the 90-day window, and Reddit voice in the matching subreddits.

Method — reproducible, run over the full local pull:

```python
rx = re.compile(PATTERN)
text  = lambda r: (r["caption"]+" "+r["title"]+" "+" ".join(r["hashtags"])).lower()
wtext = lambda r: (r["title"]+" "+r["content"]).lower()
tiktok = [r for r in trends if rx.search(text(r))]
fresh  = [r for r in tiktok if age_days(r) < 90]
voice  = [r for r in wom if r["platform"]=="reddit" and rx.search(wtext(r))]
```

Density across 24 candidate niches (TikTok / fresh<90d / >10k views / Reddit):

```
streetwear-clothing 224/118/132/33   coffee-matcha 196/115/103/21
home-gym            156/ 64/ 88/ 9   perfume-fragrance 129/76/60/34
kitchen-gadget      114/ 75/ 94/13   protein-supplement 110/61/36/21
haircare            106/ 54/ 45/ 9   cleaning 90/58/69/10
snack-bar-oats       90/ 36/ 62/ 6   sparkling-kombucha 86/21/27/4
sunglasses-eyewear   78/ 51/ 39/ 8   smart-home 76/37/43/6
skincare-acne-serum  73/ 51/ 63/37   toddler-feeding 73/48/58/16
cat-products         66/ 41/ 53/ 8   phone-accessory 61/30/48/3
baby-sleep-newborn   48/ 30/ 42/21   candles 46/20/19/1
energy-sports-drink  37/ 24/ 17/ 6   headphones 32/11/25/10
sunscreen-SPF        31/ 24/ 27/24   dog-enrichment 11/ 9/  7/ 2
stroller-carrier      9/  7/  8/ 4   jewellery 5/3/3/8
```

---

### Candidate 1 — **Overcast** (already in the DB): mineral SPF 50 serum

**Push:** the serum itself, on the "no white cast, so you actually reapply" claim.

**Why this one first: it is the documented failure, and the corpus that caused
it has changed.** Overcast scored evidence **1.0 across all five lanes** because
it was handed twenty videos whose only qualification was *beauty*. That was
against 2,999 rows with no Voice corpus. Today:

- **31 TikTok posts** match `spf|sunscreen|sunblock|white cast|zinc`, **24 of
  them inside the 90-day window**, median views **127,300**. **22 of 27 are
  `GRWM routine`** — the exact format an SPF ad should imitate.
  - `https://www.tiktok.com/@tenishaward1/video/7664674479871659278` — 26 d,
    4.3 M views, er 0.086, GRWM, a morning routine ending in sunscreen
  - `https://www.tiktok.com/@kbeauty_gem/video/7658830857431420173` — 41 d,
    2.5 M views, *"Oily Skin? Try THIS ⭕, avoid THAT ❌ … from cleanser to
    sunscreen"*
  - `https://www.tiktok.com/@sasainseoul/video/7669681716176456980` — 12 d,
    358.9 k views, teen routine, no-ad
- Broadening to `acne|serum|niacinamide|retinol|pores|moisturizer|toner`:
  **73 posts, 51 fresh, 63 above 10 k views.**
- **Voice: 24 Reddit posts, concentrated in r/SkincareAddiction (58 rows total in
  corpus).** These are the highest-scoring posts in the whole `word_of_mouth`
  table:
  - `r/SkincareAddiction` 1,394 likes — *"[PSA] Reality"*
  - `r/SkincareAddiction` 749 likes — *"6 years of extreme sun protection [B&A]"*,
    a first-person account of daily sunscreen over seven years
  - `r/SkincareAddiction` 788 likes — *"[B&A] Just over a year on Arazlo…"*,
    routine listed line by line including *"BoJ Aqua Fresh SPF"*
  - `r/SkincareAddiction` 1,170 likes — *"Sunscreen every day"* in the body
- `beauty-personal-care` also has the second-largest Voice coverage in
  `category_word_of_mouth` (**383 rows**).

**The falsification test in `CONTEXT-RETRIEVAL.md` §6 is now runnable.** Same
company, same product, same recipe on disk from the 1.0 run — new corpus, new
retrieval. If evidence does not move, the retrieval hypothesis is wrong and we
learn that cheaply. No other candidate offers a before/after that already exists.

**Blocker:** Overcast has no `company_knowledge` row and `website` is
`overcast.example`, so it inherits the null-website enrichment problem. A real
query vector must be passed explicitly.

---

### Candidate 2 — **an indie fragrance brand** (new): a discovery set

**Push:** a 5 × 2 ml discovery set — "find your scent before you commit to a
100 ml bottle".

**Corpus evidence — the strongest two-sided coverage in the database:**

- **129 TikTok posts** match `perfume|fragrance|scent|parfum|cologne`; **76 in
  the 90-day window**; median views 6,715 with a long tail into the millions.
  Format mix is unusually good: 75 UGC social proof, **16 Offer / launch**, 12
  Review, 6 Behind the scenes, 5 Before / after.
  - `https://www.tiktok.com/@miniluxury.perfume/video/7670541561071193347` —
    10 d, 3.3 M views
  - `https://www.tiktok.com/@fbfragrances/video/7672310064866250004` — 5 d,
    293.1 k views, er 0.110, *"The best fragrance of 2026 so far"*
  - `https://www.tiktok.com/@derekscents/video/7663527139878145294` — 29 d,
    273.6 k views, er 0.071
  - Category hashtag density confirms it: `perfume:78`, `fragrance:59`,
    `perfumetiktok:35`, `fragrances:32`, `perfumes:28` inside
    `beauty-personal-care`.
- **Voice: 34 Reddit posts**, and this is the part that makes it a candidate
  rather than just a big bucket. **r/Indiemakeupandmore (24 rows) is an indie
  fragrance community writing at length**, with high scores:
  - 272 likes — *"IMAM is really special"*, on a community that welcomes
    *"scents reminiscent of diabetic patches and amniotic fluid"*
  - **270 likes — *"sorce blowing up on tik tok and the overconsumption that
    followed"*** — a thread about someone buying three bottles of one scent
    after a TikTok. That is a customer describing the exact purchase behaviour
    a discovery set is designed to intercept, unprompted, in their own words.
  - 230 likes — *"Bespoke Perfume from Lovesick Witchery – 'Sun Rays'"*, a
    detailed first-order experience
  - plus **r/fragrance (19 rows)**: `fragrance:14`, `smell:7`, `scent:5`
- **Whitespace is visible and defensible.** The high-view fragrance TikToks are
  overwhelmingly bottle-porn and ASMR ("Let's shake 2 scoops", "Which bag do you
  want?"). Almost nothing addresses *choosing wrongly and wasting £90*, which is
  precisely what the Reddit threads are about. Evidence for the format, evidence
  for the gap, from two independent modalities.

**Cost:** this is a new company row, so it needs a real bio, a real mission and
— to avoid the null-website trap — a real website. Fictional is fine (Sunday
Oats is), but the website must resolve or enrichment degrades to paraphrase.

---

### Candidate 3 — **a matcha / at-home coffee brand** (new): a starter kit

**Push:** a ceremonial-grade matcha starter kit (tin, whisk, measured scoop) —
"café matcha at home in 40 seconds".

**Corpus evidence — the largest fresh, high-engagement cluster in
food-beverage:**

- **196 posts** match `coffee|matcha|espresso|latte|cold brew|iced`; **115 in
  the 90-day window**; **103 above 10 k views**. Narrowing to the matcha/craft
  core: 94 posts, 65 fresh, **median views 13,700** — the highest median of any
  niche measured.
  - `https://www.tiktok.com/@cierahudson/video/7645705482610494733` — 77 d,
    **8.5 M views, er 0.112**
  - `https://www.tiktok.com/@_angelomarasigan/video/7669860820926401805` — 12 d,
    **4.4 M views**, *"Here's my Golden Ratio for a matcha latte!"* — a recipe /
    ratio video, i.e. a demo format a product can slot into
  - `https://www.tiktok.com/@quinlyn_rose/video/7673699929705041183` — **1 day
    old**, 2.3 M views, *"i like matcha not sugary milk thats tinted green"* —
    a contrarian hook, already validated at scale, in the corpus, this week
  - `https://www.tiktok.com/@tudulcematcha/video/7641417634445118750` — 88 d,
    3.8 M views
  - Format mix is 49 UGC social proof + **32 Review / testimonial**, which suits
    both `social-proof` and `demo-first` lanes.
  - Hashtag density in `food-beverage`: `coffee:124`, `matcha:49`,
    `tastetest:63`, `iced:30`.
- **Freshness is the standout property.** 65 of 94 inside 90 days, several under
  a fortnight. Every other candidate leans on older material.

**The honest caveat: the Voice side is thin.** Only **21 Reddit matches**, and
inspecting them shows r/Coffee has just **11 rows** in the whole corpus, with the
top hit being *"From berry to cup – processed coffee from my trees"* — a
home-growing post, not a customer complaint. `food-beverage` has the **weakest
Voice coverage of any category: 26 rows in `category_word_of_mouth`.** So this
candidate is strong on Evidence and Craft-adjacent format signal, and weak on
Voice. Pick it if the demo is about *retrieval quality and freshness*; do not
pick it if the demo is about *customer language*.

---

### Considered and rejected — where the corpus cannot support the company

- **Bramble (dog enrichment / lick mat), already in the DB.** `lick mat|
  enrichment|dog toy|chew|puzzle feeder|slow feeder` matches **11 TikTok posts,
  9 fresh, and 2 Reddit posts**. `pets` has **380 trends and zero rows in
  `category_word_of_mouth`.** The lick-mat concept the ad is built on is simply
  not in the corpus. Bramble's evidence score of 2.0 was correct, and re-running
  it against today's corpus will not move it. **Do not use Bramble for the
  before/after.**
- **Eli Health.** The only company with a real, resolving website — so it is the
  only one where enrichment could actually scrape, and it scored the highest
  input-quality result on record (3.8). But it sells an at-home saliva hormone
  test, and `fitness-wellness` (483 trends) is home-gym equipment, supplements
  and — through a tokenisation accident — **29 posts about *data recovery*
  software**. There is no hormone-testing, cycle-tracking or diagnostics content
  in the corpus at all. Eli Health is the best *input* and the worst *grounding*.
  Worth flagging to whoever owns the scrape: one targeted category would change
  that.
- **A stroller / baby-carrier brand.** 9 TikTok matches. Too thin. The broader
  baby space is genuinely strong — **baby-sleep 48/30/42 with 21 Reddit,
  toddler-feeding 73/48/58 with 16 Reddit**, and the largest Voice cluster in
  the corpus (r/NewParents 31 + r/daddit 30 + r/BabyBumps 24 + r/beyondthebump
  24 + r/toddlers 20 + r/Mommit 12 = **141 posts**, several over 1,000 likes) —
  but the Reddit content is overwhelmingly emotional-support threads (PPD, sleep
  deprivation, in-laws) rather than product language. It would be a fourth
  candidate on volume; it is not one on fit, and putting an ad next to a post
  about CPS involvement is a judgement call nobody should make by accident.

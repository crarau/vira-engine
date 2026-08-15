# Data boundary — Lovable vs the Python side

*Verified against the live project on 2026-08-15, 22:00.*

## The rule

> **Lovable Cloud is the source of truth for all scraped content and its
> vectors. The Python database holds only what Python derives and Lovable
> cannot hold. Nothing is copied that could instead be queried.**

If a row exists in Lovable, Python does not keep its own copy. Python keeps a
`trend_key` and asks.

## Why, in one paragraph

Two Postgres instances holding the same corpus is not redundancy, it is drift.
The corpus is live — `trends` went from 2,999 to 3,976 rows in about an hour
while we were working — so a mirror is stale the moment it lands, and every
downstream score computed against the stale half is quietly wrong. On top of
that, the vector search is *already built* on the Lovable side, on pgvector with
an HNSW index. Copying 3,976 embeddings out in order to re-run the same cosine
query in a second database buys nothing and costs correctness.

The access model settles the argument anyway. See §2.

## 1. What Lovable owns

Content and vectors. All of it. Python never writes here.

| Table | Rows | Our grant |
|---|---|---|
| `trends` | 3,976 | `SELECT` only |
| `category_trends` | 3,985 | `SELECT` only |
| `word_of_mouth` | 333 | `SELECT` only |
| `category_word_of_mouth` | 334 | `SELECT` only |
| `prescripts` | 100 | `SELECT` only |
| `category_prescripts` | 309 | `SELECT` only |
| `categories` | 8 | `SELECT` only |
| `companies` | 10 | insert/update own |
| `company_insights` | 6 | read |

Plus the two vector functions, both `REVOKE`d from `anon` and `GRANT`ed to
`authenticated`:

```sql
recommend_company_trends(_company_id uuid, _limit int, _query_embedding vector(1536))
match_company_knowledge(query_embedding vector(1536), match_count int, exclude_company uuid)
```

`recommend_company_trends` is the retrieval path: cosine over the HNSW index,
over-fetch 4×, then re-rank `0.8 * similarity + 0.2 * percent_rank(trend_score)`.
Verified live — seeded with a protein-shake video it returns protein-shake
videos at similarity 0.84 / 0.81 / 0.78.

**Python calls this. Python does not reimplement it.**

## 2. The access model makes this non-negotiable

This is not only a design preference — the permissions enforce it.

- **There is no `service_role` key and no Postgres connection string.** Lovable
  Cloud exposes neither. No logical replication, no CDC, no `pg_dump`. A true
  full replica is impossible, so "mirror everything" was never on the table.
- **The content tables are `SELECT`-only for us.** `GRANT SELECT ON
  public.trends TO anon, authenticated` — writes are `service_role` only.
  Python *cannot* write scraped content even if it wanted to.
- **RLS hides rows from us permanently.** Measured anon vs. agent:

  | Table | anon | agent | note |
  |---|---|---|---|
  | `company_knowledge` | 0 | **6** | RLS-scoped; anon sees nothing |
  | `profiles` | 0 | **1** | our agent's row only |
  | `remix_chats` | 0 | 0 | other users' rows invisible to us |
  | `subscriptions` | 0 | 0 | same |

  Jesh's chats, profile and subscription rows exist and we will never see them.
  That is RLS working correctly, not an obstacle to route around.

## 3. What Python owns

Only things that do not exist in Lovable and cannot reasonably be added there:
**derived multimodal enrichment**, keyed back to Lovable by `trend_key`.

```sql
-- Python-side Postgres. Every row references a Lovable row it does not copy.
create table trend_enrichment (
  trend_key    text primary key,     -- FK in spirit to lovable.trends.trend_key
  tier         smallint not null,    -- 1 cover-image, 2 frames+transcript
  cover_desc   text,                 -- vision pass over raw.coverUrl
  frame_descs  jsonb,                -- 4-6 sampled frames, Tier 2 only
  transcript   text,                 -- audio, Tier 2 only
  beats        jsonb,                -- 0-2s hook, 3-8s demo, 9-14s proof
  model        text not null,        -- which vision/ASR model produced this
  created_at   timestamptz default now()
);

create table render_runs (        -- production records: purely ours
  run_id       text primary key,
  company_slug text not null,
  lane         text not null,
  score        jsonb,
  recipe_path  text,
  mp4_path     text,
  created_at   timestamptz default now()
);
```

Note what is **absent**: no `trends` copy, no `embedding` column, no captions, no
engagement metrics, no `word_of_mouth`. Those are queried, never stored.

Rule of thumb for adding a Python table: *could Lovable answer this?* If yes, it
does not belong here.

## 4. The join key

`trend_key` (e.g. `VIRA-TR-7656083604723731720`), `wom_key` for word-of-mouth,
`slug` for companies. Stable, human-readable, and present on every row. Python
stores keys; Lovable resolves them.

## 5. How Python reads Lovable

Already implemented in `vira/supa.py`; the gaps are noted.

- **Auth.** `Supa.signed_in()` exchanges `AGENT_EMAIL` / `AGENT_PASSWORD` for a
  JWT. **The token expires in 60 minutes** (measured) — a long batch job will
  start returning 401 mid-run. *Gap: no refresh handling. Needs a refresh before
  expiry or a re-sign on 401.*
- **Reads.** PostgREST caps responses at 1000 rows regardless of `limit`;
  `select_all` pages past it.
- **Retrieval.** Call `recommend_company_trends` with an explicit
  `_query_embedding`. Do not fall back to local cosine unless the RPC is
  unavailable.
- **Verification stays.** Every `source_url` is still fetched before it reaches
  a prompt. Being in the database is not evidence that the video still exists.

## 6. The two tables Python may write

Both are `authenticated`-writable and scoped to rows we own. This is the entire
write surface.

| Table | Policy | Use |
|---|---|---|
| `company_knowledge` | `knowledge_write_own` — `owner_id = auth.uid()` | the company-side query vector |
| `company_remixes` | `company_remixes_owner_all` | finished ads, once the FK issue is resolved |

`company_knowledge` is the immediate win. It is `UNIQUE (company_id)` with a
`content` text field and a `vector(1536)` embedding, and it is what
`recommend_company_trends` falls back to:

```sql
SELECT COALESCE(_query_embedding, k.embedding) FROM companies c
LEFT JOIN company_knowledge k ON k.company_id = c.id
```

Six of ten companies have a row. **bramble, overcast, sunday-oats and eli-health
do not**, which is exactly why the RPC returns zero rows for them. Writing those
four rows fixes retrieval for the engine *and* for Jesh's UI, which hits the
same fallback.

## 7. Anti-drift rules

1. **One direction.** Lovable → Python for content. Python → Lovable only for
   `company_knowledge` and `company_remixes`. Never content write-back.
2. **No caching of rows, only of vectors during a single run.** If a batch needs
   the same 20 trends across 5 lanes, hold them in memory for that run. Do not
   persist them.
3. **Embedding model must match.** `trends.embedding` is 1536-dim. Any query
   vector Python generates must come from the same model, or the geometry is
   meaningless and the similarity numbers are noise that looks like signal.
   **Confirm the exact model with Jesh before writing any `company_knowledge`
   row.**
4. **Enrichment is derived, therefore disposable.** If `trend_enrichment` is
   lost, it can be regenerated from Lovable. That is the test of whether a table
   belongs on the Python side.

## 8. Immediate actions

| # | Action | Blocked on |
|---|---|---|
| 1 | Confirm the embedding model behind `trends.embedding` | Jesh |
| 2 | Write `company_knowledge` rows for the 4 missing companies | #1 |
| 3 | Switch selection to `recommend_company_trends` with explicit embedding | #1 |
| 4 | Re-run Overcast; check whether evidence moves off 1.0 | #3 |
| 5 | Add JWT refresh to `Supa` | nothing |
| 6 | Fix the Reddit ingest before embedding `word_of_mouth` | Jesh |

Step 4 is the falsification test. If evidence does not move, the premise in
`CONTEXT-RETRIEVAL.md` is wrong and we should hear that rather than tune a
threshold to hide it.

# Build log — Zero-Human Company Hackathon, 2026-08-15

Everything built, everything found, everything still open. Written so a new
session can pick this up cold.

## What exists

Three moving parts, one database.

```
┌─ LOVABLE (they host it) ────────────────────────────────────┐
│  company-essence-lab.lovable.app        TanStack Start UI    │
│  Lovable Cloud (Supabase)               ← the ONLY database  │
│    postgres · auth · storage · Jobs · Edge functions         │
│    connector gateway → Apify   ai gateway → gemini           │
└──────────────────────────────────────────────────────────────┘
                          ▲  HTTPS / PostgREST only
                          │  (no postgres:// exists)
┌─────────────────────────┴────────────────────────────────────┐
│  vira-engine            Python, Render background worker      │
│  select → verify → analyze → remix → score → voice → render   │
└───────────────────────────────────────────────────────────────┘
```

| Thing | Where | State |
|---|---|---|
| Frontend + DB | `jp-215/company-essence-lab` → Lovable Cloud | Jesh's, live |
| Engine | `crarau/vira-engine` (public) | live on Render |
| Worker | `srv-da0c6me1egvs738d33t0` | running, 30-min tick |
| Secrets | Azure KV `kv-zerohuman-hack` | 12 secrets, Chip-only ACL |
| Earlier spike | `crarau/zero-human-company` | reference design, not running |

## The database

Lovable Cloud, project `otsqjpmsiysitpkqoejr`.

**There is no Postgres connection string and there never will be** — Lovable
Cloud does not expose one, does not expose `service_role`, and has no supported
migration path off it. Everything goes through PostgREST over HTTPS with the
publishable key (public by design, RLS-bound; Lovable itself commits it to
`.env` in the frontend repo).

Tables, as of the last check:

| Table | Rows | Notes |
|---|---|---|
| `trends` | 2,999 | real scraped TikToks, real URLs and engagement |
| `category_trends` | 3,008 | category ↔ trend mapping |
| `prescripts` | 100 | **synthetic** — a `CROSS JOIN` of 10 formats × 10 angles |
| `category_prescripts` | 309 | assigned by `hashtext() % 100 < 40`, i.e. random |
| `categories` | 8 | consumer-product taxonomy |
| `companies` | 5+ | Foodbot, Chips, vira, Vira 2.0, Eli Health, Sunday Oats |
| `company_insights` | 1 per company | |
| `company_knowledge` | **0** | pgvector RAG built but never populated |
| `company_remixes` | **0** | the centrepiece has never produced a row |

## What running it actually found

Five real findings, all discovered by executing rather than reading.

**1. `company_trends()` returns 100% stale rows.** The RPC caps at 200 ordered
by `trend_score`, and since that score is half reach (`log10(views)/8 * 0.5`),
the window fills with old megaviral clips. Every one of the 200 rows it returned
for Food & Beverage was over 90 days old — while **56% of the corpus (1,670
videos) is under 90 days**. The age filter has to run in the database *before*
the cap. Fixed in `supa.fresh_company_trends`. **Jesh's UI has the same bug.**

**2. Enrichment never scrapes.** `company_insights` came back `status: done`
with polished positioning, tone, keywords and ad themes — and `sources: []`,
`raw: null`, `website: null`. Signup never captures a URL, so `scrapeSite()` (170
lines, already written) has never run. The "enrichment" is an LLM paraphrase of
the user's own two sentences. Typing "Selling chips" / "More chips" yields *"A
highly focused snack brand dedicated to the straightforward goal of delivering
more chips to consumers."*

**3. The prescript library is synthetic.** `trend_score` for the 100 seeded
prescripts is `round(0.55 + ((fi*7 + ai*3) % 40)/100, 2)` — a hash of loop
indices. The `trends` table Jesh added later is real and fixes this, but both
libraries still exist side by side with separate RPCs.

**4. A Remotion `<Sequence>` renders `useCurrentFrame()` relative to the
sequence.** Subtracting `startFrame` again drove the entrance spring negative and
pinned every caption at `opacity: 0`. The render "succeeded" — exit code 0,
plausible file size, correct duration, real audio — and was 24 seconds of black.
**Only extracting a frame and looking at it caught this.**

**5. VS Code lies about media.** See `vscode-video-audio.md`. It reported no
audio on a provably good file. Never validate a render there.

## The pipeline

`select → verify → analyze → remix → score → voice → render`

Staged deliberately: one prompt over 2,999 rows produces confident claims about
videos it never read.

- **select** — category join, age filter in the DB, English-only, format-diversity
  quota. Returns ~20 from ~159 candidates, and returns *why* it rejected the rest.
- **verify** — GETs every `source_url`. TikTok soft-404s removed videos with a
  200, so the body is checked for removal markers too.
- **analyze** — corpus pass (what works here, cited) + competitor pass (allowed
  to return "not in this corpus", which beats an invented paragraph).
- **remix** — output is a timed shooting script, not advice. `grounded_in` is
  mandatory and validated against the shortlist.
- **score** — A–E, 0–5 on relevance/specificity/actionability/differentiation/
  evidence. **Evidence is a gate, not an average**: below 3.0 it is dropped no
  matter what the other four say.
- **voice** — ElevenLabs with character timestamps. Beat *and word* timings come
  from the synthesiser. No frame number is authored by hand anywhere.
- **render** — Remotion 1080×1920: Ken Burns on stock stills, word-level karaoke
  captions, cross-dissolves, film grain, vignette, progress bar, CC credit line.

### The rule the whole thing turns on

> The voice track is the master clock.

Lifted from `ideaplaces-docs/docs/active-projects/video-as-code`. Synthesize
first, take character timestamps from the same call, compute every frame offset
from them. A copy change re-times the video for free, and localisation becomes
the same feature instead of a new project.

## Scores so far

| Company | Input quality | Overall | Evidence | Verdict |
|---|---|---|---|---|
| Chips | "Selling chips" | 2.6 | 1.0 | dropped |
| Foodbot | one sentence | 2.8 | 1.0 | dropped |
| Eli Health | real bio + URL | **3.8** | 2.0 | dropped |

Real input moves the score. Nothing has cleared the evidence gate yet, and
**that threshold has deliberately not been tuned** — lowering it until output
passes is how the gate stops meaning anything.

## Hackathon requirements

From the guidebook (event 08:30–21:00, submit at hackathoncompany.com):

- **"All projects must use the Terac MCP"** and *"Using the Terac MCP is required
  to submit your project!"* — **HARD GATE, still not integrated.** Key is in the
  vault.
- Stripe individual account required for *Best Overall Agent-Run Company*
  ($2,500), which wants **real revenue earned during the event**. Test key is
  valid, balance $0.00. One judge is Stripe's Head of Advanced AI.
- *Best Overall Project* $2,500. *Best use of Render* is its own track — which is
  why the worker is on Render at all.
- Terac track: use real human input collected during the event to make the
  project measurably better, and **show a clear before and after**. Terac
  recruits the panel through their API.

## Why five variants

The Terac track needs a before/after. One ad gives a panel nothing to compare.
`variants.py` builds five ads from deliberately different creative lanes —
problem-first, demo-first, founder-story, social-proof, contrarian — over one
shared corpus and one shared analysis, so a human ranking them is ranking the
*angle* rather than noise. That ranking is the signal that feeds back into the
scoring weights, and the re-run is the "after".

## Still open

1. **Terac MCP** — blocks submission entirely.
2. **Writes to Supabase** — the agent account works, but `company_remixes` has a
   `prescript_key` FK that trend keys cannot satisfy. Needs the `observed_ads`
   migration in `SPEC.md`, plus the agent RLS policy for writing other people's
   companies.
3. **Real revenue** — nothing charged yet.
4. **Stock image quality** — Openverse CC is mostly amateur Flickr. Tiered to
   prefer StockSnap/rawpixel/nappy, but coverage is thin. A Pexels or Unsplash
   key would fix it in one line. No image-gen deployment exists on the Azure
   OpenAI resource (checked: gpt-4.1, gpt-5, gpt-5.4, embeddings only).
5. **`company_knowledge` is empty** — the pgvector RAG has never been populated.

## Operational notes

- Python **3.12+** required; system python is 3.9 and cannot import the models.
- `.env` is gitignored here. It is **not** gitignored in the frontend repo —
  Lovable's bot commits it.
- Secrets: `az keyvault secret show --vault-name kv-zerohuman-hack --name <n> --query value -o tsv`
- Worker logs: `GET /v1/logs?ownerId=tea-csp95lrgbbvc73f29mc0&resource=srv-da0c6me1egvs738d33t0`
- Remotion's free licence covers companies of ≤3 people and **collaborators
  aggregate**. This team is four. Fine for a demo; read the licence before
  anything commercial.

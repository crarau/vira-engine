# vira-engine — spec

A Python process that turns a corpus of real TikTok ads into a shootable — and
then *rendered* — ad for one specific company.

Reads the Lovable Cloud database that `company-essence-lab` already populated
(2,999 scraped TikToks with real URLs and engagement). Writes its output back to
the same database. Renders video with Remotion.

Runs locally for now. Nothing here needs a host until it needs a schedule.

## Why this exists separately from the Lovable app

The Lovable app already has a remix path: `prescripts → LLM → company_remixes`.
It works on 100 synthetic templates and produces text.

This engine does the parts that are awkward inside Lovable:

- **Corpus analysis across thousands of rows.** Clustering 2,999 videos by
  format/angle and extracting what actually works in a category is a data job,
  not a request handler.
- **Competitor-specific analysis.** Given a named competitor, find what *they*
  are running and what mechanism makes it work.
- **Video rendering.** Remotion is a Node render pipeline with real CPU cost and
  minutes-long runtimes. That does not belong in a serverless function.

Everything it produces lands back in Postgres, so the Lovable UI renders it with
no integration work.

## Connection

Lovable Cloud exposes no Postgres connection string, so **there is no
`postgresql://`**. All access is PostgREST over HTTPS:

```
https://otsqjpmsiysitpkqoejr.supabase.co/rest/v1/<table>
apikey: <publishable key from company-essence-lab/.env>
```

Reads work anonymously for `trends`, `category_trends`, `categories`, published
`companies`, and their `company_insights` — verified against the live database.

Writes need a JWT. Sign in once as a dedicated agent account
(`POST /auth/v1/token?grant_type=password`) and use that bearer token. RLS scopes
writes to rows the agent owns, so writing engine output for *other people's*
companies needs one migration on the Lovable side — see "Open dependencies".

## Pipeline

```
  select → verify → analyze → remix → score → voice → render
   │        │        │         │       │       │       │
   │        │        │         │       │       │       └─ Remotion → mp4
   │        │        │         │       │       └───────── ElevenLabs → mp3 + char timings
   │        │        │         │       └───────────────── A–E eval, evidence gate
   │        │        │         └───────────────────────── the ad, as timed beats
   │        │        └─────────────────────────────────── what works in this category, and why
   │        └──────────────────────────────────────────── source still live?
   └───────────────────────────────────────────────────── candidate trends for this company
```

Staged, not one prompt. A single call over 2,999 rows produces confident claims
about videos it never looked at. Each stage narrows and each stage is auditable.

### 1. select

Input: `company_id`, `product`, optional `competitors[]`.

Pull candidates via the category join, then filter hard:

- `posted_at > now() - 90 days` — a 2021 mop is not a trend. The corpus has
  plenty of these and they survive on reach alone.
- `trend_score` descending, never `views` descending, for the same reason.
- English-only unless the company says otherwise (the corpus has Indonesian
  snack ads that produce nonsense remixes).
- Format diversity: cap at N per `format` so the shortlist isn't six unboxings.

Output: 15–25 candidate trends.

### 2. verify

`HEAD` every `source_url`. TikTok URLs rot — videos get deleted and accounts go
private. A recommendation whose proof 404s during a demo is worse than no
recommendation. Dropped candidates are recorded with a reason, never silently
skipped.

### 3. analyze

The competitor analysis. Two passes:

**Corpus pass** — over the verified shortlist, in one structured call: which
formats dominate this category, which hooks recur, what the top performers have
in common, what nobody is doing. Every claim must cite `trend_key`s. Claims
without citations are dropped.

**Competitor pass** — for each named competitor, search the corpus by author and
hashtag, and report what they are running. Honest empty result when they aren't
in the corpus: "Blizzard does not appear in the current corpus" beats an
invented paragraph.

### 4. remix

The output is not a paragraph. It is a **timed shooting script**:

```json
{
  "hook": "One line, under 90 chars, spoken in the first 2 seconds",
  "beats": [
    {"t": 0.0, "say": "...", "show": "...", "shot": "close on the can, handheld"},
    {"t": 2.4, "say": "...", "show": "...", "shot": "cut to pour, top-down"}
  ],
  "caption": "...",
  "hashtags": ["..."],
  "cta": "...",
  "grounded_in": ["VIRA-TR-...", "VIRA-TR-..."],
  "why_this_works": "the mechanism borrowed, and from which video"
}
```

`grounded_in` is mandatory and must reference verified trends from stage 2. The
`t` values here are a *draft*; stage 6 replaces them with real timings.

### 5. score

A–E eval, evidence-gated:

| | |
|---|---|
| A | what the source video did |
| B | the transferable mechanism |
| C | how it applies to this product |
| D | the artifact — the actual video URL |
| E | source |

Scored 0–5 on relevance, specificity, actionability, differentiation, evidence.
**Evidence is a gate, not an average** — below 3 and it's dropped regardless of
the rest. Surfaced ≥4.5, watchlist ≥3.5, else dropped with a reason.

### 6. voice — the master clock

Lifted from `ideaplaces-docs/docs/active-projects/video-as-code`: synthesize
narration first, get character-level timestamps back in the same call, and
compute every visual timing from them. Never hand-author frame numbers.

```
beats[].say  →  ElevenLabs  →  mp3 + char timestamps
                                    ↓
                         beat start/end frames = f(timestamps, fps)
```

This is what makes the video re-time itself for free when the copy changes, and
it is why the same pipeline localizes later at near-zero cost.

### 7. render

Emit a props JSON, hand it to Remotion, get an mp4.

```bash
npx remotion render AdVideo out/ad.mp4 --props=out/props.json
```

Vertical 1080×1920, 30fps, duration derived from the audio via
`calculateMetadata` rather than hardcoded.

**Licensing flag:** Remotion is free for companies of 3 or fewer, and
collaborators aggregate toward the threshold. This project currently has four
people on it. For a hackathon demo that is unlikely to matter; before anything
commercial ships, read the license.

## Data written back

| Table | Written | Notes |
|---|---|---|
| `company_remixes` | the remix | existing table, existing UI |
| `observed_ads` *(new)* | verified + scored candidates with drop reasons | needs a migration |
| `company_insights` | corpus + competitor analysis | needs the agent RLS policy |

Nothing is written that lacks a `source_url`.

## Layout

```
vira-engine/
├── SPEC.md
├── vira/
│   ├── config.py     env + thresholds
│   ├── supa.py       PostgREST client (read/write, auth)
│   ├── models.py     pydantic DTOs mirroring the schema
│   ├── select.py     stage 1
│   ├── verify.py     stage 2
│   ├── analyze.py    stage 3
│   ├── remix.py      stage 4
│   ├── score.py      stage 5
│   ├── voice.py      stage 6
│   ├── render.py     stage 7
│   └── cli.py        vira select|analyze|remix|render|run
└── video/            Remotion project
    └── src/AdVideo.tsx
```

## Open dependencies

1. **Agent account** — sign up `agent@…` in the app; needs the UUID + password.
2. **Migration** — RLS policy letting that UUID write `company_insights` /
   `company_knowledge`, plus the `observed_ads` table.
3. **ElevenLabs key** — Azure Key Vault `kv-ideaplaces`, secret
   `elevenlabs-api-key`, per the video-as-code prerequisites.

Stages 1–5 need none of these to run read-only against the live corpus. Start
there.

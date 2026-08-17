# Briefs

`POST /v1/videos` takes a company slug, a product and a lane. That is the whole
input, and it is much less than Lovable already knows by the time a user has
picked their references.

`POST /v1/briefs` takes the rest of it — the brand's guardrails, the assets that
were chosen and the ones that were rejected, the beats somebody actually wrote,
the palette, the constraints — and answers **identically**: 202, a job id, a
poll URL. Existing poll and stream code does not change. What changes is what
the engine is told.

**Base URL:** `https://vira.ideaplaces.com`

| | `POST /v1/videos` | `POST /v1/briefs` | `POST /v1/ads/image` |
|---|---|---|---|
| Input | slug + product + lane | the full brief | either |
| Output | a video | a video | a static ad |
| Returns | 202 + job id | 202 + job id | the ad, inline |
| Grounding | category selection | **the brief's own references** | either |
| Length | ~25s, engine's choice | **4, 6 or 8 seconds** | one frame |

The static ad is specified in [`IMAGE-API.md` §2](./IMAGE-API.md), which is its
contract. This document covers the brief, and the two things a brief adds to
either endpoint.

---

## 1. The payload

Everything is camelCase, as Lovable emits it. snake_case is accepted too, so a
hand-written `curl` does not have to translate.

```jsonc
POST /v1/briefs
{
  "durationSeconds": 6,                 // 4 | 6 | 8
  "aspectRatio": "9:16",                // only 9:16 renders — see §6

  "brand": {
    "name": "Sunday Oats",
    "slug": "sunday-oats",              // optional; derived from name if absent
    "bio": "overnight oats that set in the fridge in four hours",
    "mission": "breakfast that is already made when you wake up",
    "category": "Food & Beverage",
    "toneGuardrails": ["dry", "never chirpy"],
    "palette": ["#0B0B0F", "#F5C518"],
    "mustSay": ["four hours"],
    "neverSay": ["superfood", "game-changing"]
  },

  "references": [                       // up to 6, weighted, lead dominates
    { "trendKey": "VIRA-TR-7643127…",   // a corpus row → EVIDENCE
      "platform": "tiktok",
      "hook": "I gave it ten SECONDS",
      "format": "unboxing",
      "whyItWorks": "withholds the result until the midpoint",
      "weight": 0.9 },

    { "imageKey": "IMG-1",              // an asset → DIRECTION
      "sourceUrl": "…", "imageUrl": "…",
      "weight": 0.6,
      "ocr":         { "text": "…", "headline": "…", "cta": "…", "confidence": 0.91 },
      "sentiment":   { "tone": "urgent", "score": 0.7, "emotionTags": ["fomo"],
                       "intent": "conversion", "urgency": "high" },
      "texture":     { "palette": ["#0B0B0F"], "lighting": "hard side light",
                       "surfaceTexture": "brushed steel", "finish": "matte",
                       "contrast": "high", "saturation": "low", "noiseLevel": "fine" },
      "composition": { "framing": "tight crop", "subject": "one hand",
                       "focalDepth": "shallow", "textPlacement": "lower third",
                       "negativeSpace": "generous" },
      "motion":      { "impliedMotion": "a hand entering frame",
                       "suggestedCamera": "push", "suggestedBeats": ["reveal"] },
      "keep":  ["the steel counter"],
      "avoid": ["stock-photo smiles"] }
  ],

  "narrative": {
    "hook": "I stopped buying breakfast for two YEARS",
    "beats": [ { "t": 0.0, "shot": "close on the jar", "onScreenText": "four hours" },
               { "t": 3.0, "shot": "the lid coming off" } ],
    "voiceover": "It sets while you sleep.",
    "cta": "Tap to try the first batch",
    "textOverlayPolicy": "one line per beat"
  },

  "style": { "look": "cold kitchen at 6am", "palette": ["#0B0B0F"],
             "pace": "slow burn", "musicMood": "ambient", "captions": true },

  "constraints": { "noRealPeopleLikeness": true, "noCompetitorMarks": true,
                   "language": "en", "safetyNotes": ["no health claims"] },

  "excluded": ["IMG-4"],                // rejected assets, by key
  "signalQuality": "high",              // high | low

  // the three fields the engine needs and the brief does not carry
  "product": "cocoa hazelnut overnight oats",   // defaults to brand.name
  "lane": "founder-story",                      // GET /v1/lanes
  "mode": "fast"                                // fast | agentic
}
```

Every field is optional except `brand.name`. A reference must carry either a
`trendKey` or an `imageKey`; one carrying neither is a 422 rather than an asset
that silently does nothing.

### The response

```json
{
  "job_id": "54473e6a-7ee6-4e70-a222-802050d34ddf",
  "status": "queued",
  "poll": "https://vira.ideaplaces.com/v1/jobs/54473e6a-…",
  "estimated_seconds": 90,

  "company_slug": "sunday-oats",
  "product": "cocoa hazelnut overnight oats",
  "duration_seconds": 4,
  "beats": 2,
  "grounded_on": "brief references",
  "references_used": 2,
  "signal_quality": "low",
  "warnings": ["…"]
}
```

The first four fields are `POST /v1/videos`'s response, unchanged — poll and
stream exactly as before. The rest is the engine telling you what it decided,
and `warnings` is where it tells you what it could not do (§6).

---

## 2. Where each field goes

| Brief | Lands on | Effect |
|---|---|---|
| `brand.*` | `models.Company` | the context block in **every** prompt — plan, write, critique, imagery, score |
| `brand.mustSay` | `Company.keywords` | printed into that same context, so every stage sees the phrases |
| `brand.neverSay` | the writer's brief | a stated prohibition, not a post-hoc filter |
| `references[].trendKey` | the verified corpus | **replaces category selection** — §3 |
| `references[].imageKey` | the imagery style contract | palette, lighting, framing, `keep`/`avoid` |
| `narrative.hook` | required opening line | the writer opens on it or a faithful variant |
| `narrative.beats[]` | the plan and the writer's brief | shot list executed in order, one beat each |
| `narrative.voiceover` | the writer's brief | content to deliver, trimmed to the clock |
| `narrative.cta` | the closing line | printed on the outro card |
| `style.look`, `*.palette` | the imagery style contract | what the frames look like |
| `style.pace` | the plan's pacing | |
| `constraints.*` | hard prohibitions | in copy and in the frames |
| `durationSeconds` | beat count + word budget | §4 |
| `excluded[]` | dropped before anything runs | never reaches a prompt |
| `signalQuality` | the evidence gate | §5 |

Mechanically, the brief is folded into the **lane** — the one object the
planner, the writer, the voice and the imagery director all already read. That
is why a stage added next month inherits the constraints without anyone wiring
them up again.

The whole brief is written into the recipe, in your field names, so
`GET /v1/videos/{id}/recipe` hands back something you can post straight back to
this endpoint.

---

## 3. `trendKey` references are better retrieval than we have

This is the most valuable thing in the payload and it is worth being explicit
about why.

`vira.select.shortlist` reaches the corpus through exactly one join: the
company's **category**. It then ranks by `trend_score` and caps per format. It
never sees the product. "Food & Beverage" is one bucket for an energy drink and
a sourdough starter, so the shortlist is category-plausible and product-blind.

Lovable picks its references against the actual brand and the actual asset. So a
brief carrying `trendKey`s arrives with a shortlist this engine could not have
produced, and when it does, **selection is bypassed entirely**:

```
grounded_on: "brief references"     the corpus is your references
grounded_on: "category selection"   no trendKey given, so we fall back
```

Bypassed rather than blended. Mixing curated references with category leftovers
would let the weaker half of the corpus back into a prompt that had already been
curated past it.

Three things do not change:

- **The rows are still fetched from the corpus and still verified.** `hook`,
  `format` and `whyItWorks` are read as your reading of the video; the video
  itself is checked. A `trendKey` the corpus does not have is reported in the
  recipe's rejection panel, not quietly dropped.
- **Order is dominance.** References are sorted lead-first, then by `weight`.
  The writer reads the corpus in that order, and when it cites nothing the
  engine grounds it on the first entry — so being first is what "dominates"
  means here.
- **Image references are direction, never evidence.** Nothing about a colour
  palette supports a claim, so they shape the frames and never enter the
  scorer's cited-sources list.

If every cited key is missing from the corpus, the job fails loudly rather than
falling back — a brief that names its sources and gets none of them should not
quietly become a category ad.

---

## 4. `durationSeconds`, and what four seconds costs

The engine's own films run about 25 seconds. Four is a different product, and
the brief is respected rather than approximated:

| `durationSeconds` | beats | spoken words | measured narration |
|---|---|---|---|
| 4 | 2 | ~10 | **4.5s** |
| 6 | 3 | ~16 | ~6s |
| 8 | 4 | ~21 | ~8s |

An authored `narrative.beats[]` overrides the beat column — you know how many
shots you want; the table is only a default.

**The clock is enforced twice, because stating it once does not work.** Told
"4 seconds, 2 beats, 10 words" as hard as the prompt can put it, gpt-5.4 came
back with 3 beats and 23–27 words — about nine seconds — repeatedly. The writer
is solving for a good ad and length is one constraint among twenty. So after the
script is written, the engine measures it in Python, and if it overran it runs
one pass whose only job is cutting. Measured on the same brief: 27 words → 23
with a harder prompt → **9 words and 4.5 seconds** with the cutting pass. If it
is still over afterwards, the miss is published on the job feed and written into
the recipe as `duration_overrun`, rather than left for you to find on playback.

The cut happens **before** narration is synthesised. It has to: every timing in
the film comes from the ElevenLabs character timestamps, so a script shortened
after the read would be a short caption track over a long one.

### The tradeoff, stated plainly

Four seconds is not a shorter version of the 25-second ad. It is a worse-served
brief, and the loss is structural rather than cosmetic:

- **The film's whole grammar disappears.** `vira.director` exists because
  structure carries a short-form ad: a device, a turn, a withheld reveal. None
  of those fit in two beats. What survives is a hook and a payoff.
- **The critic has nothing to work with.** The hostile-first-viewer pass still
  runs, but "beat 3 restates beat 2" is not available when there is no beat 3.
- **The evidence gate gets harder, not easier.** The scorer grades a concept
  against its sources, and nine words carry less that can be traced back to a
  cited video. Short briefs are dropped more often, and that is the gate working
  — it is not a bug to route around.
- **The cut copy reads clipped.** "Breakfast's made." is a fragment, and a
  fragment is exactly what the hook grammar rejects in first position. It is
  acceptable in beat two and it is what ten words buys.
- **The CTA card is not in the budget.** Every render appends a fixed 2.4s
  call-to-action card, so a 4-second brief produces a ~6.9-second file with 4.5
  seconds of narration. `duration_s` on the video is the narration length.

Six and eight seconds are materially better served than four. If the platform
allows it, ask for eight.

---

## 5. `signalQuality: "low"`

Lovable saying its own references are thin. It is a claim about the evidence, so
it is applied to the **evidence dimension**, and only downward:

```
evidence -= 1.0     before disposition() is consulted
```

Three consequences:

- A brief can make the gate **harder** to pass and has no way to make it easier.
  `EVIDENCE_FLOOR` is not reachable from any request parameter; this is a
  penalty on a score, not a move of the threshold.
- The writer is told as well, in the brief: claim less, a vaguer ad that survives
  the gate beats a confident one that does not.
- The result carries `confidence: "low"` even when it clears the gate — an ad
  built on thin references that scored well is still an ad the engine is less
  sure about, and `disposition` alone cannot say that.

Measured: a brief scoring `evidence 3.5` surfaces at `signalQuality: high` and is
**dropped** at `low`. The flag is not decorative.

---

## 6. What the engine will not pretend to do

Returned in `warnings` on the 202, or refused outright where the output would be
wrong rather than merely worse.

| You send | What happens |
|---|---|
| `aspectRatio` other than `9:16` | **422.** The composition is 1080×1920 and the caption band is derived from that height. Rendering 9:16 and calling it 1:1 would be a lie. |
| `style.musicMood` | recorded in the recipe, **ignored**. There is no music track; the engine renders narration only. |
| `constraints.language` other than `en` | honoured in the copy, **warned**. The hook grammar was measured on 2,669 English TikToks and becomes guidance, not law. Selection also filters to English captions. |
| `mode: "agentic"` with `narrative.beats` | **warned.** The Director shapes its own film and reads your beats as direction rather than as a fixed plan. Use `fast` to pin them. |
| `durationSeconds: 4` | **warned**, with §4's tradeoff. |
| no `trendKey` references | **warned.** Falls back to category selection, which is coarser than what you can pick. |
| an unknown `brand.slug` | **created.** The brief carries the brand, so no separate `POST /v1/companies` is needed. |

---

## 7. Static ads from a brief

`POST /v1/ads/image` takes the simple shape documented in
[`IMAGE-API.md` §2](./IMAGE-API.md) — `{brand, product, lane}` — **or** the whole
brief above, posted at the top level. The two are told apart by `brand`: a
string is the simple shape, an object is a brief.

```bash
curl -X POST https://vira.ideaplaces.com/v1/ads/image \
  -H 'Content-Type: application/json' \
  -d '{ "brand": {"name": "Sunday Oats", "neverSay": ["superfood"]},
        "references": [{"trendKey": "VIRA-TR-7643127…"}],
        "style": {"look": "cold kitchen at 6am"},
        "product": "cocoa hazelnut overnight oats" }'
```

Everything in §2, §3 and §5 applies unchanged: the references ground it, the
constraints reach the prompts, `signalQuality` reaches the gate. `durationSeconds`
does not — a still has no clock — and `narrative.hook`, if you send one, becomes
the printed headline and is checked against the hook rules like any other
supplied headline.

---

## 8. Auth

**The engine is open by default and that is deliberate.** With
`VIRA_ENGINE_TOKEN` unset on the server, nothing below applies and every
endpoint behaves exactly as it does today.

When the token **is** set, five endpoints require it:

```
Authorization: Bearer <token>
```

| Gated | Never gated |
|---|---|
| `POST /v1/videos` | every `GET` |
| `POST /v1/videos/{id}/regenerate` | `GET /healthz`, `GET /` |
| `POST /v1/briefs` | `GET /v1/review-batches/{token}` |
| `POST /v1/ads/image` | `POST /v1/review-batches/{token}/votes` |
| | `POST /v1/companies` |

The gated set is written out endpoint by endpoint rather than derived from the
HTTP method, and that is the important part. **The judge flow can never be
gated.** A panellist arriving from Terac holds a batch token and no credential —
the batch token *is* their credential — so a rule like "all POSTs need a bearer"
would 401 their votes and waste the panel spend before anyone noticed. The five
above are the endpoints that start paid generation and nothing else.

Failure is `401` with `WWW-Authenticate: Bearer` and a JSON `detail`. CORS runs
outside the gate, so a rejection reaches a browser as a readable 401 rather than
an unexplained network error. Comparison is constant-time.

```bash
# server
export VIRA_ENGINE_TOKEN='…'      # then restart uvicorn

# client
curl -X POST https://vira.ideaplaces.com/v1/briefs \
  -H "Authorization: Bearer $VIRA_ENGINE_TOKEN" \
  -H 'Content-Type: application/json' -d @brief.json
```

There is no per-caller identity, no scopes and no rotation endpoint. One shared
secret over the endpoints that cost money is the whole feature; anything more is
a login system, and the judge flow is the reason this service does not have one.

---

## Related

- [`IMAGE-API.md`](./IMAGE-API.md) — both image endpoints, `POST /v1/ads/image` in full
- [`API.md`](./API.md) — jobs, streaming, recipes, the review loop
- [`LOVABLE-INTEGRATION.md`](./LOVABLE-INTEGRATION.md) — what makes output good, with the measurements
- [`HOOK-CRAFT.md`](./HOOK-CRAFT.md) — where the hook rules come from
- `GET /v1/lanes` — the five creative lanes

# V2 — prompt-first video generation

**Status:** specification. Nothing here is built.
**Supersedes:** the hackathon prototype in this repo.
**Target home:** `Ideaplaces/<name>` (name TBD), replacing `crarau/vira-engine`.

---

## 1. What changes, in one paragraph

The prototype starts from a *company row* in someone else's database and works
outward to a video. V2 starts from **a sentence the user types** and works
outward to a video they can then edit. Everything that made the prototype good —
voice-as-master-clock, per-lane creative identity, verbatim recipes, an evidence
gate written in Python where no model can reach it — survives. Everything that
made it fragile — a borrowed corpus, category-only retrieval, a `companies`
table as the entry point — goes.

## 2. The problem with V1's grounding, stated honestly

This is the decision that shapes the whole rebuild, so it goes first.

`vira/select.py::shortlist` resolves a company to its `category_id` and returns
every fresh trend in that category. **The product string is never part of the
query.** With 692 rows under "Food & Beverage", an energy drink and a sourdough
starter retrieve the same references.

The measured consequence: **every ad the prototype generated was dropped on
evidence**, scoring 1.0–2.0 against a 3.0 floor. The gate was not
malfunctioning. It was correctly reporting that a TikTok about someone else's
snack brand cannot support a claim about your oats.

So "is the corpus useful?" splits into two questions with different answers:

| | Useful? |
|---|---|
| As **evidence for product claims** | **No, and it never can be.** A stranger's video knows nothing about your product. This was a category error in V1's design, not a retrieval bug to be fixed. |
| As **reference for format** — what hooks, pacing and structure perform in a vertical | It **was**, and it has already been spent. |

### Decision: nothing migrates from Lovable

The corpus does not come along. Not the rows, not the embeddings, not a seed
file — V2 never reads Lovable Cloud, and there is no import step to get wrong
later.

This is defensible because **the value has already been extracted.** The 2,669
ranked videos produced fourteen prescriptive hook rules, seven permitted opening
grammars, and a set of measured failure modes — and those now live in
`vira/remix.py`, `vira/director.py::HOOK_SHAPES` and `docs/HOOK-CRAFT.md` as
code and prose. **The rules are the extract; the rows were the ore.** Keeping
4,617 TikTok rows around to re-derive conclusions we already hold would be
carrying the ore after smelting it.

What that costs, stated plainly: we lose the ability to show a user *"here are
three real videos shaped like the one we just made for you."* That is a real
feature and it is not in V1. When it matters, it comes back as a live per-prompt
search (§8), which is better than the snapshot anyway — current instead of
frozen, and specific to the request instead of to a category.

### The fix: split the gate in two

V1 has one `evidence` dimension doing both jobs and failing at both.

**V2 has two gates, with different sources and different consequences:**

| | Claim gate | Format gate |
|---|---|---|
| Asks | "can this assertion be traced to something the user gave us?" | "does this structure resemble what performs in this vertical?" |
| Source | **the user's own material** — their page, their copy, their spec | reference videos + the measured hook rules |
| On failure | **the claim is cut from the script.** Hard. | advisory — logged, shown, never blocking |
| Runs in | Python, unreachable by any model | Python |

This is strictly better than V1 and it is *less* work, not more. Claim
grounding against the user's own material actually succeeds — the protein
content of their product is on their product page — so the gate finally passes
things while still refusing to invent. And format guidance stops being a gate it
was never suited to be.

So the format gate is not a retrieval problem at all in V1 — it is
`hook_faults()` plus the lane's brief, both already written, both needing no
data. **No claim ever cites a TikTok again, and no TikTok is stored.**

## 3. V1 scope — deliberately one shot

One prompt, one video, then editing. Not five lanes, not a batch, not a judge
panel.

**In scope**

1. A single text input: *what do you want to achieve?*
2. Optional grounding: paste one or more URLs (product page, brand site). We
   fetch and read them.
3. The agents run, visibly, with live progress.
4. One 15–30s vertical video comes out, with the recipe attached.
5. **The user can then adjust it** — see §7. This is the feature that makes it a
   product rather than a slot machine.

**Explicitly out of scope for V1**

- Five-lane batch generation (keep the code, do not surface it)
- The human ranking / judge flow (`review_batches` and friends) — this existed
  for the hackathon
- Terac integration
- Apify / Firecrawl accounts (see §8 — the seam is defined, the integration is
  not built)
- Auth, billing, multi-tenancy
- Static ad images (`/v1/ads/image`) — keep, do not feature

## 4. What gets deleted

Being specific here is the point; a rewrite that carries its predecessor's
dependencies is not a rewrite.

| Delete | Why |
|---|---|
| `vira/supa.py` | Lovable Cloud client. We do not own that database, cannot migrate off it, and reach it only through a publishable key bound by RLS. It cannot be the foundation of a product. |
| `vira/api/routes/corpus.py` | Proxies live Lovable reads to the browser |
| `vira/api/routes/companies.py`, `new_company.py` | The company-first entry point is gone |
| `vira/api/routes/suggest.py` | Suggests products for a company; no companies now |
| `vira/api/routes/reviews.py`, the four `review_*` tables | Hackathon judge flow |
| `vira/api/routes/terac.py` | Hackathon sponsor integration |
| `vira/select.py` | Replaced by §6 retrieval. Its rejection-counting is worth keeping in spirit. |

Nineteen files import `Supa` today. With no corpus to carry, the cut is simply
**delete `supa.py`, delete the modules above, and keep the one Postgres we
already own** for generations, videos and recipes. There is no seed step, no
loader, and no embedding index to build or keep aligned.

## 5. Architecture

```
  browser
    │  POST /v1/generations   { prompt, urls[] }
    ▼
  FastAPI ──── 202 + generation_id ────► SSE /v1/generations/{id}/stream
    │
    ▼  background task
  ┌─────────────────────────────────────────────────────────┐
  │ 1. UNDERSTAND   prompt → intent, product, claims, goal  │
  │ 2. GROUND       fetch user URLs → extractable facts     │
  │                 retrieve format references (vector)     │
  │ 3. DIRECT       shape: length, beats, pacing, hook form │
  │ 4. WRITE        script, every claim tagged to a source  │
  │ 5. CLAIM GATE   Python. Untraceable claims are cut.     │
  │ 6. VOICE ‖ IMAGERY   ElevenLabs timestamps ‖ Gemini     │
  │ 7. RENDER       Remotion, props derived from timings    │
  └─────────────────────────────────────────────────────────┘
    │
    ▼
  out/<generation_id>/v001/  ad.mp4 · props.json · RECIPE.md · shots/
```

Stages 6–7 and the whole Remotion layer carry over essentially unchanged. The
new work is stages 1, 2 and 5, plus the edit loop.

### Stage 1 — UNDERSTAND (new)

The prototype had a `Company` model with fields someone had already filled in.
V2 must derive the same information from one sentence.

```python
class Intent(BaseModel):
    product: str            # "cocoa hazelnut overnight oats"
    brand: str | None       # may be absent; the script must cope
    audience: str           # who this is aimed at
    goal: str               # awareness | trial | conversion
    claims: list[Claim]     # every factual assertion, extracted
    tone_hints: list[str]
    missing: list[str]      # what we would need to do better
```

`missing` is a product feature, not a diagnostic: the UI shows *"tell me the
price and I'll make the CTA concrete"* rather than silently guessing.

A `Claim` is the unit the gate operates on:

```python
class Claim(BaseModel):
    text: str
    kind: str               # factual | comparative | subjective | promotional
    source_id: str | None   # set by GROUND, checked by the CLAIM GATE
```

Only `factual` and `comparative` claims need sources. "Tastes incredible" is
subjective and passes freely; "12g of protein" and "cheaper than Huel" do not.

### Stage 2 — GROUND (rebuilt)

Two independent retrievals, run concurrently:

**a. The user's material.** Every URL is fetched, converted to text, and mined
for extractable facts. V1: `httpx` + `readability`/`trafilatura`. V2: Firecrawl
(§8). Each fact keeps its source URL and the sentence it came from, so a
recipe can show the provenance of every number in the script.

**b. Format guidance — no retrieval.** There is nothing to retrieve. The lane
supplies the visual grade and copy brief, `HOOK_SHAPES` supplies the opening
grammar, and `hook_faults()` audits the result against the fourteen measured
rules. All three are code. This stage is therefore *one* network fan-out — the
user's URLs — not two.

That is a simplification with teeth: GROUND now fails only if the user gave us a
URL we could not read, which is a condition we can explain to them. V1's
grounding could fail silently by retrieving 300 irrelevant rows.

### Stage 5 — CLAIM GATE (rebuilt)

Runs in Python. No agent can call it or read its threshold.

```
for each claim in the written script:
    if kind in (subjective, promotional):        pass
    elif claim.source_id is None:                CUT the sentence
    elif not substring_or_entailment(claim, source):  CUT the sentence
```

**Cutting, not dropping.** V1 threw away the whole video on an evidence failure,
which is correct for a judged demo and useless for a product — a user who waited
40 seconds gets nothing. V2 removes the unsupported sentence, re-times the film
(free, because timing derives from the voice track), and **tells the user what
it cut and why**. That panel is the most trust-building surface in the product:
*"I removed 'clinically proven' — nothing on your page supports it."*

If cutting leaves fewer than two beats, only then do we fail the generation.

## 6. Data model

One owned Postgres. No Lovable, no external database.

```sql
generations (
  id uuid pk, prompt text, urls text[],
  intent jsonb,                    -- stage 1
  status text,                     -- queued|running|done|failed
  video_id uuid, created_at, elapsed_ms int
)

sources (                          -- what we fetched for the user
  id uuid pk, generation_id uuid, url text, title text,
  text text, fetched_at, ok bool
)

facts (                            -- extractable assertions from a source
  id uuid pk, source_id uuid, text text, sentence text
)

videos (
  id uuid pk, generation_id uuid, version int,
  props jsonb, duration_s numeric, out_dir text,
  parent_video_id uuid            -- edit lineage, see §7
)

claims (
  id uuid pk, video_id uuid, text text, kind text,
  fact_id uuid null, verdict text  -- kept|cut, with reason
)

llm_calls, assets, recipes         -- carried over unchanged
```

`claims` is queryable history: *"what have we refused to say, across every
generation?"* is a real question a user will ask, and V1 could not answer it.

## 7. The edit loop

The prototype can already do this and never exposed it. It is the cheapest
large feature in the document.

Three edit classes, in increasing cost:

| Edit | Cost | Mechanism |
|---|---|---|
| **Re-render** — grade, caption style, colour | **~40s, zero API spend** | `npx remotion render --props=props.json`. Props are saved. |
| **Reword one beat** | **~1 TTS call + a render** | Re-synthesize that beat, take new character timestamps, re-derive frames. *Every downstream timing updates for free* — this is what "the voice track is the master clock" buys. |
| **Replace one image** | **~8s + a render** | `vira/agentic/crew.py::t_regenerate_frame`. Pin the style contract and `name_offset` or neighbouring frames drift — this cost six wasted turns in the prototype and the fix is already in the code. |

Editing is **versioned, never destructive**: every edit writes a new `videos`
row with `parent_video_id` set and a new `v<NNN>` directory. The user can always
go back, and a recipe always describes exactly one artifact.

UI: click any caption word to reword that beat. Click any frame to regenerate it.
A slider for grade. A "revert" on every version.

## 8. Apify and Firecrawl — the seam, not the integration

Both replace something V1 does crudely. Neither is needed for V1 to work, and
both are behind one interface each so they can land without touching the
pipeline.

```python
class PageReader(Protocol):          # the user's own material
    async def read(self, url: str) -> Page: ...
# V1: httpx + trafilatura.  V2: FirecrawlReader — JS rendering, structured
# extraction, and it handles the Shopify/Squarespace pages that defeat readability.

class ReferenceSource(Protocol):     # format references — NOT IMPLEMENTED IN V1
    async def search(self, query: str, *, limit: int) -> list[Reference]: ...
# There is no V1 implementation, by choice: the corpus did not migrate (§2) and
# the measured rules cover the same ground without any data. When live reference
# *examples* become worth showing a user, ApifyReferenceSource fills this in with
# a per-prompt TikTok/IG search — which is the honest version of "scraping":
# on demand, for this request, rather than once, for everything.
```

Cost note before committing: Apify bills per compute unit and a TikTok search
actor run is materially more expensive per generation than every LLM call in the
pipeline combined. Metered per generation, cached by query, or it becomes the
dominant unit cost.

## 9. API surface

```
POST   /v1/generations            { prompt, urls[] }        → 202 { id }
GET    /v1/generations/{id}                                 → status + intent + video
GET    /v1/generations/{id}/stream                          → SSE progress
GET    /v1/generations/{id}/claims                          → kept and cut, with reasons
POST   /v1/videos/{id}/edit       { beat, say? , image? }    → 202 new version
GET    /v1/videos/{id}/versions                             → the edit tree
GET    /v1/videos/{id}/recipe                               → verbatim prompts
GET    /healthz  ·  GET /
```

Nine operations, against twenty-nine today. That reduction is the point.

## 10. What carries over unchanged

The prototype's genuinely good parts, none of which need rework:

- **Voice as master clock** (`vira/voice.py`) — ElevenLabs character timestamps →
  word timings → frame numbers. Nothing hand-authors a frame. This is what makes
  the edit loop cheap.
- **The Remotion layer** (`video/`) — `Captions.tsx`, one-word bottom-third
  captions, `AdStill`, the four motion energies.
- **Creative lanes** (`vira/lanes.py`) — brief + voice + performance tags + grade.
  Not surfaced in V1's UI; used to pick a default identity.
- **Hook grammar** (`docs/HOOK-CRAFT.md`, `remix.py`) — 14 rules from 2,669
  ranked videos, `hook_faults()` as an auditor. The most defensible asset here.
- **Recipes** (`vira/provenance.py`) — verbatim prompts per artifact.
- **The agentic crew** (`vira/agentic/`) — Director + specialists, and the
  Gemini-vision cohesion check that compares what frames *contain* against what
  was asked for.
- **Parallelism discipline** (`CLAUDE.md`) — the rules that took one video from
  18 minutes to 74 seconds.

## 11. Phases

| | | Ships |
|---|---|---|
| **0** | New repo under `Ideaplaces`, own Postgres, delete `supa.py` and the six dead route modules. No data migrates. | nothing user-visible; the dependency is severed |
| **1** | UNDERSTAND + GROUND(user URLs) + the claim gate, wired to the existing render path. CLI only. | a video from a sentence |
| **2** | `POST /v1/generations` + SSE + the web page: one input, live progress, a player | **the demo** |
| **3** | The edit loop — reword a beat, regenerate a frame, versions, revert | the product |
| **4** | The cut-claims panel, `missing` prompts, richer fact extraction | trust and quality |
| **5** | Firecrawl, then Apify, behind the §8 protocols | current, not snapshotted |

Phases 1–3 are the prototype-to-product line. 0 is a day. 4 onward is polish
with real leverage.

## 12. Decisions needed from Chip

1. **Name and repo.** Everything else can start; this blocks phase 0.
2. ~~**Does the corpus come along?**~~ **Decided: no.** Nothing migrates from
   Lovable. See §2.
3. ~~**Firecrawl or plain fetching for V1?**~~ **Decided: `trafilatura`.**
   Faster, free, no account, no rate limit, and it handles the majority of
   product pages. Firecrawl stays behind the `PageReader` protocol for the pages
   it cannot parse.
4. **Is a login needed before this is shown to anyone?** Generation costs real
   money per call, and an open box on the internet is a different risk from an
   open box during a hackathon. **This is the only open question left.**

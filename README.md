# vira-engine

Turns the real TikToks already winning in a category into finished video ads —
and refuses to ship any it cannot ground in real evidence.

Reads the live corpus over PostgREST. Verifies every source is reachable before
a model sees it. Plans the film, writes a timed shooting script, generates the
imagery, narrates it with ElevenLabs, and renders it with Remotion.

| | |
|---|---|
| **Live API** | https://vira.ideaplaces.com · [Swagger](https://vira.ideaplaces.com/docs) · [openapi.json](https://vira.ideaplaces.com/openapi.json) — open, no key needed |
| **Front end** | [jp-215/company-essence-lab](https://github.com/jp-215/company-essence-lab) — the Lovable app that calls this engine |
| **Design** | [SPEC.md](./SPEC.md) · [ARCHITECTURE.md](./docs/ARCHITECTURE.md) · [API.md](./docs/API.md) |

## Watch it first

Five ads for one brand, one per creative lane. Same corpus, same product,
different creative direction — not the same ad reworded.

| lane | | |
|---|---|---|
| problem-first | 20.5s | [mp4](https://vira.ideaplaces.com/media/sunday-oats/v001-20260816-003714-agentic/problem-first/sunday-oats-problem-first.mp4) |
| demo-first | 25.5s | [mp4](https://vira.ideaplaces.com/media/sunday-oats/v002-20260816-003715-agentic/demo-first/sunday-oats-demo-first.mp4) |
| founder-story | 26.5s | [mp4](https://vira.ideaplaces.com/media/sunday-oats/v003-20260816-003720-agentic/founder-story/sunday-oats-founder-story.mp4) |
| social-proof | 22.5s | [mp4](https://vira.ideaplaces.com/media/sunday-oats/v004-20260816-003721-agentic/social-proof/sunday-oats-social-proof.mp4) |
| contrarian | 21.5s | [mp4](https://vira.ideaplaces.com/media/sunday-oats/v005-20260816-003722-agentic/contrarian/sunday-oats-contrarian.mp4) |

Every one carries a `RECIPE.md` next to it with the verbatim prompts that
produced it.

## Status

Running end to end against live data. 333 tests passing.

| Stage | |
|---|---|
| 1 select · 2 verify | corpus → verified shortlist |
| 3 analyze · 3.5 direct | what works in the category → the shape of this film |
| 4 remix · critique | write, then a hostile first viewer revises it |
| 5 score | the evidence gate |
| 6 voice · 6.5 imagery | ElevenLabs timestamps ‖ Gemini frames |
| 7 render | Remotion |

One text provider: **Azure gpt-5.4**. Imagery is Gemini, voice is ElevenLabs.

| Job | Time |
|---|---|
| One video, deterministic | 74s |
| Five videos | 314s |
| One video, agentic crew | ~350s |
| Re-render from saved props | ~40s, zero API cost |

## Run it

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                   # reads work with the defaults

.venv/bin/python -m vira.cli companies
.venv/bin/python -m vira.cli select chips --product "spicy chips" --verify
.venv/bin/python variants.py chips --product "spicy chips"    # five lanes, parallel
```

Or against the live API, no install:

```bash
curl -X POST https://vira.ideaplaces.com/v1/videos \
  -H 'Content-Type: application/json' \
  -d '{"company_slug":"sunday-oats","product":"cocoa hazelnut overnight oats"}'
```

Rendering locally:

```bash
cd video && npm install
npx remotion studio                    # live preview
npx remotion render AdVideo out/ad.mp4 --props=../out/props.json
```

## What it found on the first run

Two things worth keeping, both discovered by running the thing rather than
reading the code.

**The corpus is not stale — the query was.** Jesh's `company_trends()` RPC caps
at 200 rows ordered by `trend_score`, and since that score is half reach, the
window fills entirely with old megaviral clips. Every single one of the 200 rows
it returned for Food & Beverage was over 90 days old, while **56% of the corpus
(1,670 videos) is under 90 days**. The fix is filtering on `posted_at` in the
database, before the cap — `fresh_company_trends` in `supa.py`. Worth pushing
back into the Lovable app, because its UI has the same blind spot.

**Never sort by views.** Same root cause. Sorted by views you get a 2021 mop
video at 104M views; sorted by `trend_score` with a freshness filter you get
coffee-shop launches and snack drops from the last week.

## Design notes

**Staged, not one prompt.** A single call over 2,999 rows produces confident
claims about videos it never read. Each stage narrows, and each stage's output
is inspectable on its own via the CLI.

**Verify before you reason.** Every source is fetched before it reaches a model.
TikTok URLs rot, and a proof link that 404s in front of a judge is worse than no
proof at all.

**Evidence is a gate, not an average.** A concept the cited videos don't support
is dropped regardless of how good the other four dimensions look.

**Rejections are output, not logs.** The CLI prints what it threw away and why.
Every other team will show only their wins.

**The voice track is the master clock.** Lifted from
`ideaplaces-docs/docs/active-projects/video-as-code`: synthesize narration
first, take character-level timestamps from the same call, derive every frame
offset from them. Copy changes re-time the video for free, and localisation
becomes the same feature.

## Licensing

Remotion is free for companies of 3 or fewer people, and collaborators aggregate
toward the threshold. This project currently has four. Fine for a hackathon
demo; read the license before anything commercial ships.

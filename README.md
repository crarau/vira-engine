# vira-engine

Turns the 2,999 real TikToks in `company-essence-lab`'s database into a
shootable — then rendered — ad for one company.

Reads Lovable Cloud over PostgREST. Analyses the corpus. Writes a timed shooting
script. Narrates it with ElevenLabs. Renders it with Remotion.

Full design in [SPEC.md](./SPEC.md).

## Status

| Stage | State |
|---|---|
| 1 select | **working against live data** |
| 2 verify | **working against live data** |
| 3 analyze | written, untested — needs `ANTHROPIC_API_KEY` |
| 4 remix | written, untested — needs `ANTHROPIC_API_KEY` |
| 5 score | written, untested — needs `ANTHROPIC_API_KEY` |
| 6 voice | written, untested — needs `ELEVENLABS_API_KEY` |
| 7 render | written, untested — needs `npm install` in `video/` |

Stages 1–2 have been run end to end against the live corpus. Everything from 3
onward compiles and is wired, but no API key was available to execute it.

## Run it

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # reads work with the defaults; add keys for 3+

.venv/bin/python -m vira.cli companies
.venv/bin/python -m vira.cli select chips --product "spicy chips" --verify
.venv/bin/python -m vira.cli remix  chips --product "spicy chips" --out out/remix.json
```

Rendering:

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

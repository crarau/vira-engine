# Recipes — how a video is traced, tweaked, and re-made

A generated ad is useless as a starting point if you cannot see how it was
generated. "Make the hook punchier" needs the exact prompt that produced the
original hook, not a reconstruction of it.

Every video therefore ships with a **recipe**: the complete, verbatim record of
what produced it.

## Where it lives

```
out/<company-slug>/
├── manifest.json                 all variants, scores, dispositions
├── <company>-<variant>.mp4       the video
└── <variant>/
    ├── recipe.json               complete machine-readable record
    ├── RECIPE.md                 the same thing, readable and diffable
    ├── props.json                exactly what Remotion was handed
    └── narration.mp3             the voice track
```

## What a recipe contains

| Section | Why it's there |
|---|---|
| **Provenance** | timestamp, git commit of the code, product, voice id |
| **Settings in force** | model, `max_age_days`, `shortlist_size`, `max_per_format`, `english_only`, and all three score thresholds |
| **Corpus in scope** | every verified trend the ad was allowed to borrow from — key, author, score, age, URL |
| **Output** | hook, per-beat timings, caption, hashtags, CTA, `grounded_in`, the mechanism claimed |
| **Score** | all five A–E dimensions |
| **Imagery** | the stock query per beat, plus creator and licence |
| **Prompts, verbatim** | every system + user prompt sent, the model, the token budget, the stop reason, and the raw response |

The prompts are the point. Everything else is context for reading them.

## How it's captured

`vira/provenance.py` exposes a `Recorder` bound to a context variable.
`vira.llm.complete()` checks for an active recorder on every call and, if there
is one, stores the full system prompt, user prompt, model, budget, stop reason,
and response. No call site has to remember to log anything — routing through
`vira.llm` is enough.

```python
async with Recorder(out_dir / name) as rec:
    rec.note("lane", "contrarian")
    remix = await build_remix(steered, product, picked, corpus)   # captured
    score = await score_remix(company, product, remix, picked)    # captured
    rec.finish(company=..., remix=..., score=..., sources=...,
               settings_snapshot={...})
```

When no recorder is active the capture is a no-op, so the CLI and the Render
worker are unaffected.

## The tweak loop

1. Watch the five videos, pick the one closest to right.
2. Open its `RECIPE.md` and find the prompt that produced the weak part.
   - Hook or beats wrong → the **remix** call (`vira/remix.py`, `SYSTEM` + `PROMPT`)
   - Wrong videos in scope → **selection** (`max_age_days`, `max_per_format`, `english_only`)
   - Right idea, bad score → the **score** call (`vira/score.py`)
   - Bad photos → the **stock query** call (`vira/stock.py`, `QUERY_SYSTEM`)
   - Bad creative angle → the lane brief in `variants.py` `LANES`
3. Edit that string.
4. Re-run. The recipe for the new video records the new prompt, so the diff
   between two `RECIPE.md` files *is* the change you made.

Because `RECIPE.md` is plain markdown, `diff` between two runs shows exactly
which prompt moved and what the output did in response.

## What is NOT reproducible, and why

Re-running the same recipe does **not** yield a byte-identical video:

- The model is sampled, not deterministic. Same prompt, different wording.
- The corpus is live. `posted_at > now() - 90d` is a moving window, and TikToks
  get deleted between runs.
- Openverse returns different images as its index changes.

Deterministic *within* a render: `random(seed)` drives the Ken Burns drift, so
the same props always produce the same motion. Timings are fully determined by
the narration audio.

If you need an exact re-render, use the saved `props.json` — that is frozen and
will reproduce the video frame for frame, as long as `public/shots/` still holds
the same images.

## Known gap

Videos generated before provenance was wired in (the first Sunday Oats run) have
`props.json` and the variant JSON but no `RECIPE.md`. Their prompts are
recoverable from the templates in `vira/remix.py` and `vira/score.py` plus the
saved corpus, but they were not captured verbatim. Everything generated from
commit `provenance` onward has a full recipe.

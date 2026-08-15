# Spec — agentic video generation

Turning the current fixed pipeline into a crew of specialists built on
`@openai/agents`, running against gpt-5.4.

## Why, and where the line sits

The pipeline today is one straight line: select → verify → analyze → plan →
write → critique → revise → score → voice → imagery → render. It works, it does
a video in 74 seconds, and it is fully traceable.

What it cannot do is **react**. If the imagery comes back wrong for the script,
nothing notices. If the voice lands at 34 seconds against a 24-second plan,
nothing re-cuts. If two beats say the same thing, only the critic catches it and
only once. Every stage runs exactly once, in exactly one order, whatever
happened upstream.

That is the case for agents. But not for all of it:

| Keep deterministic | Make agentic |
|---|---|
| Corpus retrieval and dedup | Choosing the creative angle |
| Source verification (HTTP) | Writing and rewriting the script |
| **The evidence gate** | Choosing motion and camera per beat |
| Word timing from TTS timestamps | Judging whether imagery matches the script |
| Remotion render | Deciding a beat needs regenerating |
| Recipe capture | Deciding the film is finished |

The rule: **an agent may never skip the evidence gate or invent a timing.**
Those exist precisely because a fluent model will otherwise produce a confident,
ungrounded, badly-timed ad. Agents get creative latitude; they do not get to
mark their own homework on grounding.

## The crew

One orchestrator, seven specialists. Each specialist is a tool on the
orchestrator, and several own tools of their own.

```
                       ┌───────────────┐
                       │   DIRECTOR    │  owns the film, decides when it's done
                       └───────┬───────┘
        ┌───────────┬──────────┼──────────┬───────────┬──────────┐
        ▼           ▼          ▼          ▼           ▼          ▼
   ┌────────┐  ┌────────┐ ┌────────┐ ┌────────┐  ┌────────┐ ┌────────┐
   │RESEARCH│  │NARRATIVE│ │ MOTION │ │IMAGERY │  │ VOICE  │ │COHESION│
   └────────┘  └────────┘ └────────┘ └────────┘  └────────┘ └────────┘
        │           │          │          │           │          │
   corpus.*    script.*   motion.*    image.*     voice.*    check.*
                                                                  │
                                                            ┌─────▼─────┐
                                                            │  CRITIC   │
                                                            └───────────┘
```

### DIRECTOR — orchestrator

Owns the plan (structure, device, beat count, target seconds, pacing) and the
stop condition. Runs a loop: plan → delegate → inspect → decide. Ends when
COHESION and CRITIC both pass, or after `maxTurns`.

Does not write copy, generate images, or render. It decides *what needs doing
next*, which is the entire point.

### RESEARCH — grounding

Owns the corpus. Retrieves category-matched, in-date trends, verifies each URL
is live, and answers "is this claim actually supported?" when CRITIC or the
evidence gate asks.

`corpus.shortlist(company, product, filters)` · `corpus.verify(urls)` ·
`corpus.supports(claim, trend_keys) -> {supported, why}`

### NARRATIVE — the writer

Writes and rewrites beats inside the director's plan. Owns voice-of-brand, hook
construction, and the CTA. Never chooses motion or camera — it writes what is
said and what is on screen.

`script.write(plan, corpus, lane)` · `script.revise(script, notes)` ·
`script.tighten(script, target_seconds)`

### MOTION — the animator

Assigns a caption treatment and camera move per beat, and knows what the
Remotion composition can actually do. This is the specialist that today is
`i % 5` — an arbitrary rotation with no idea what the line says.

`motion.assign(script)` · `motion.explain(treatment)` ·
`motion.capabilities()` → the real list the composition implements

Constraint it enforces: no two consecutive beats share a treatment, and the
treatment matches the line's function (a confession is not `punch`).

### IMAGERY — the photographer

Owns the style contract and generates one frame per beat via Gemini. Can
regenerate a single beat on note without touching the others — which the current
pipeline cannot do.

`image.style_contract(brand, lane_look)` · `image.generate(beat, contract)` ·
`image.regenerate(beat_index, note)` · `image.describe(path)` → what is *actually*
in the frame, for COHESION to check against the script

### VOICE — the performer

Synthesizes with per-beat performance tags and returns real character timings.
**The only source of truth for timing in the whole system.**

`voice.cast(lane, script)` → recommended voice · `voice.synthesize(script, voice_id)`
→ `{mp3, word_timings, duration}` · `voice.reperform(beat, tag)`

### COHESION — the continuity checker

The specialist with no counterpart today, and the one that fixes the most. Reads
the *rendered inputs* — script, image descriptions, audio duration — and reports
mismatches.

`check.duration(plan, actual)` · `check.image_matches_beat(description, beat)` ·
`check.style_consistent(descriptions)` · `check.no_text_in_frames(paths)`

Typical finding: *"beat 4 says 'twelve hours later' but the image is the same
kitchen at the same time of day as beat 3 — the time jump does not read."*
Routes to IMAGERY as a regenerate note.

### CRITIC — the hostile viewer

Already exists in `vira/director.py`. Promoted to an agent so it can run more
than once and see the imagery, not just the words.

`critic.watch(script, image_descriptions, duration)` →
`{weakest_beat, scroll_risk, verdict, notes}`

## The loop

```
DIRECTOR: plan the shape
  → RESEARCH.shortlist + verify
  → NARRATIVE.write
  → MOTION.assign
  → parallel: VOICE.synthesize | IMAGERY.generate
  → COHESION.check(all)
  → CRITIC.watch
  ↺ while (notes exist and turns < max):
        route each note to its owner
        NARRATIVE.revise / IMAGERY.regenerate / MOTION.assign / VOICE.reperform
        re-check only what changed
  → evidence gate (deterministic, non-negotiable)
  → RENDER
```

The `re-check only what changed` line is the efficiency that makes this
affordable. Regenerating one image is 8 seconds; regenerating the film is 74.

## Implementation

Load the SDK behind a thin, hand-typed boundary rather than importing its types
directly. The SDK's declarations pull in zod v4, whose `.d.cts` uses TypeScript
5 syntax; a project on an older `tsc` cannot parse it. Requiring the CommonJS
build and re-exporting behind local minimal types means the project
type-checker never touches the SDK's declarations, while runtime stays the real
SDK.

```ts
// video/agents/sdk.ts
const agents = require('@openai/agents')
export const { Agent, run, tool, setDefaultOpenAIClient, setOpenAIAPI } = agents
```

Wiring:

```ts
setDefaultOpenAIClient(azureClient)
setOpenAIAPI('chat_completions')

const director = new Agent({
  name: 'Director',
  instructions: DIRECTOR_INSTRUCTIONS,
  model: 'gpt-5.4',                  // deployed on openai-ideaplaces
  tools: [narrative, motion, imagery, voice, cohesion, critic, research],
})

const result = await run(director, brief, { maxTurns: MAX_TURNS })
```

Every specialist is a `tool({name, description, parameters, execute})` whose
`execute` calls the **existing Python stage over HTTP**. The Python keeps doing
the work; the agents decide when and how often. Nothing already built is thrown
away.

```ts
export const imagery = tool({
  name: 'image_regenerate',
  description: 'Regenerate the photograph for one beat, given a correction note.',
  parameters: {
    type: 'object',
    properties: {
      beat_index: { type: 'integer', description: '0-based beat to redo.' },
      note: { type: 'string', description: 'What was wrong with the frame.' },
    },
    required: ['beat_index', 'note'],
  },
  execute: async (args) => post('/imagery/regenerate', args),
})
```

Model: **gpt-5.4**, already deployed on `openai-ideaplaces` alongside gpt-5 and
gpt-4.1. No new key needed — `azure-openai-api-key` and `azure-openai-endpoint`
are in `kv-ideaplaces`, and `kv-zerohuman-hack` has the rest.

## Skills, not just tools

Each specialist should also exist as a **standalone skill** — invocable on its
own, testable on its own, reusable outside this system. The agent is a caller of
the skill, not the owner of it.

```
skills/
├── narrative/     write, revise, tighten          + evals
├── motion/        assign, capabilities            + evals
├── imagery/       style_contract, generate, describe
├── voice/         cast, synthesize, reperform
├── cohesion/      duration, image_matches, style_consistent
├── research/      shortlist, verify, supports
└── critic/        watch
```

That structure is what lets a second product — a feature explainer, a product
demo, a launch video — use `motion` and `voice` without inheriting an ad engine.
Keep tool definitions, guardrails and prompts in separate modules for the same
reason: the guardrails outlive any one agent, and the prompts change far more
often than the tool contracts.

## Honest cost

| | Now | Agentic |
|---|---|---|
| One video | 74s | est. 3–6 min |
| LLM calls | 6 | 15–40, unbounded until `maxTurns` |
| Reproducible | prompts captured verbatim | plus a turn-by-turn trace |
| Fixes its own mistakes | no | yes |

**Agentic is slower and costs more.** It buys self-correction, which matters
when output goes in front of judges — and buys nothing at all if the first pass
was already fine.

Recommendation: **hybrid, not replacement.** Keep the deterministic path as the
fast lane (`--fast`), and run the agent loop when quality matters more than the
74 seconds. Same skills underneath both, so there is one implementation of
"generate an image" and one of "assign motion", not two.

## Migration order

1. Extract the current stages behind a local HTTP surface — no behaviour change.
2. Write the seven skills as thin wrappers over those endpoints, each with evals.
3. Build COHESION first. It is the only genuinely new capability, and it fixes
   the most visible failures on its own — no orchestrator needed.
4. Add the Director loop with `maxTurns=8` and a hard wall-clock budget.
5. Keep `--fast` permanently. Most runs will not need the crew.

## Guardrails

- The evidence gate runs **after** the loop, in Python, and no agent can call it
  or see its threshold.
- Hard caps: `maxTurns`, wall-clock, and a per-run image-generation budget. An
  unbounded loop on a metered image API is a bill, not a feature.
- Every tool call lands in the recipe, so `RECIPE.md` becomes a full transcript
  of who decided what and why.

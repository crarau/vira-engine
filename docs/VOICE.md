# Voice — what actually makes it sound like a person

*Measured against the live ElevenLabs API on 2026-08-15. 76 renders,
`eleven_v3`, PCM 22.05 kHz. Every number below is a mean over 3–4 runs of the
same condition, reported against the run-to-run noise floor of that condition.*

The brief was "make it more humanistic". The finding is that the setting we were
proudest of does nothing, the tags work for a different reason than the code
claimed, and the remaining headroom is in the writing.

---

## 1. How this was measured

No ffmpeg on this machine, so the request asks ElevenLabs for
`output_format=pcm_22050` and the raw int16 is analysed directly. No decoder, no
transcoding loss.

The first metric tried was the obvious one — loudness spread across the clip,
p90 minus p10 over 50 ms frames. **It is useless.** It returned 72.0–72.4 dB for
every condition tested, because it measures the gap between speech and silence,
which is a property of the encoder, not the performance. Anything reported on
that basis is measuring nothing.

Four metrics that do discriminate:

| metric | what it is | what it means |
|---|---|---|
| `vsd` | std dev of frame loudness, **voiced frames only** (≥ −32 dB) | dynamics |
| `vrange` | p95 − p05 loudness, voiced frames only | dynamics |
| `gapCV` | coefficient of variation of inter-word gaps | **phrasing / breathing** |
| `durCV` | coefficient of variation of per-word duration | **stress** |
| `longgap` | count of inter-word gaps ≥ 300 ms | phrasing |

`gapCV` and `durCV` come from the API's own character timestamps, not from our
decode. They are the good ones: a narrator is metronomic, a person talking is
not, and rhythmic variability is exactly that difference.

Two measurement traps, both hit and both fixed:

- **Bracketed tags** occupy characters in the alignment. Skipped, as
  `voice._words_from_alignment` already does.
- **A bare em dash arrives as its own alignment token** with near-zero duration.
  Left in, it inflates `durCV` by itself and manufactures an orthography effect
  that is not there. All rhythm metrics exclude tokens with no alphanumerics.
  The orthography result in §3 survives this control; it was re-run to confirm.

### The noise floor

TTS is stochastic. Three runs of the identical request:

```
vsd     [6.949, 6.797, 6.997]   half-spread 0.100
vrange  [22.26, 21.84, 22.64]   half-spread 0.400
gapCV   [1.681, 1.584, 1.680]   half-spread 0.048
durCV   [0.839, 0.803, 0.796]   half-spread 0.021
dur     [32.88, 33.36, 34.40]   half-spread 0.760
```

Nothing smaller than roughly 2× these is an effect.

---

## 2. Two claims in the code were wrong

### `stability` does nothing

`voice.py` said, of `stability: 0.0`:

> Measured 25% more dynamic range than 1.0 on the same line — which is the whole
> difference between a read and a performance.

Three runs each, identical text and tags:

| stability | vsd | vrange | gapCV | durCV |
|---|---|---|---|---|
| 0.0 | 6.914 | 22.25 | 1.648 | 0.813 |
| 0.5 | 6.830 | 22.32 | 1.738 | 0.760 |
| 1.0 | 6.771 | 21.87 | 1.693 | 0.748 |
| *noise floor* | *±0.10* | *±0.40* | *±0.048* | *±0.021* |

Every difference is at or inside the noise. **The 25% claim does not
reproduce.** 0.0 holds a marginal edge on `durCV` (0.813 vs 0.748, ~3× noise)
and costs nothing, so it stays — but as a default, not as a lever, and the
comment now says so.

### `speed` is silently ignored by v3

Sent as a `voice_settings` field on the same short line. The API accepts it,
returns 200, and does nothing:

| speed | duration |
|---|---|
| 0.7 | 5.60 s |
| 1.0 | 5.80 s |
| 1.2 | 5.64 s |
| *(omitted)* | 6.04 s |

0.7 should have run ~40% long. It did not. **Do not add `speed` to the v3 body.**
Separately confirmed against `/v1/models`: `eleven_v3` reports
`can_use_style: false` and `can_use_speaker_boost: false`, so `stability` is the
only voice setting v3 exposes at all.

---

## 3. What does work: tags and orthography are different levers

Isolated, 3 runs each, identical wording throughout, punctuation tokens excluded
from rhythm metrics. Baseline = plain prose, no tags.

| condition | vsd | vrange | gapCV | durCV | longgap |
|---|---|---|---|---|---|
| plain, no tags | 6.802 | 21.70 | 0.810 | 0.669 | 2.3 |
| **+ performance tags** | 6.694 | 22.22 | **1.726** | 0.788 | **7.3** |
| **+ orthography only** (CAPS, em dash) | 7.020 | **23.09** | 0.869 | **0.777** | 3.0 |
| **+ both** | 6.932 | 22.44 | **1.689** | **0.871** | **9.3** |

Read as deltas from baseline:

| lever | gapCV (phrasing) | durCV (stress) | vrange (loudness) |
|---|---|---|---|
| performance tags | **+113%** | +18% | +2.4% |
| orthography | +7% | **+16%** | **+6.4%** (+1.4 dB) |
| both | **+109%** | **+30%** | +3.4% |

Three conclusions.

**1. Tags do not do what the code said they do.** They move loudness by nothing
(`vsd` 6.80 → 6.69, inside noise). They more than double the variability of
gaps between words and take the count of >300 ms pauses from 2 to 7 across a
30-second read. The mechanism is *breathing and phrasing*, not dynamic range.
The Billy Mays framing in the module docstring was the wrong model, and it has
been corrected.

**2. Orthography is the missing lever, and it is orthogonal.** A word in CAPS
and an em-dash aside — same words, same tags — add 16% to word-stress variation
and 1.4 dB to voiced loudness range, both several times noise. Tags cannot do
this and orthography cannot do what tags do. **Using both is additive:** stress
variation goes to +30%, higher than either alone.

**3. Ellipses do nothing.** `...` produced no pause and no measurable change.
`gapCV` with ellipsis-heavy text was 0.869 against a no-tag baseline of 0.810 —
and the >300 ms pause count went 2.3 → 3.0. If you want a pause, use a full stop
or a tag. The prompt now says so explicitly, and rule 13 in HOOK-CRAFT bans the
trailing ellipsis on the hook for this reason *and* because the corpus
over-represents it in the bottom cohort.

### Non-verbal tags add a further real increment

Swapping a palette of pure moods (`[thoughtful] [quietly] [warm]`) for one
containing sounds (`[sighs] [exhales] [whispers]`), 3 runs each:

| palette | gapCV | longgap |
|---|---|---|
| moods only | 1.648 | 6.7 |
| with non-verbals | **1.843** | 6.7 |

+12% on phrasing variability against a 3% noise floor. Small, real, free. Every
lane palette now carries at least one, asserted in `tests/test_lanes.py`.

Verified safe: word counts in the returned alignment are identical with and
without tags (73 words in every condition), so v3 is not speaking them aloud and
the timing spine is unaffected.

---

## 4. Is the writing the problem?

The brief asked directly. The answer is **partly, and less than expected.**

Rewriting the copy for the mouth — a restart (*"Okay so —"*), a hesitation
(*"for, what, years?"*), a sentence fragment as a beat (*"So."*), an aside —
with **no tags**, against plain prose with no tags:

| | gapCV | durCV | duration |
|---|---|---|---|
| prose, no tags | 0.777 | 0.671 | 32.1 s |
| rewritten for speech, no tags | 0.730 | 0.698 | 35.9 s |

**No improvement in phrasing.** The synthesiser does not infer hesitation from
written hesitation; it just reads more words. `durCV` moved +0.027, barely above
noise. The rewrite cost 12% more runtime for nothing measurable.

So the honest split:

- **Written-in disfluency (restarts, fillers, "so", "what"): no measured
  effect.** Not adopted. It reads as human on the page and not in the audio.
- **Written-in orthography (CAPS on the stressed word, em-dash aside):
  measurably effective**, §3. Adopted, and it is a `remix.py` change, not a
  `voice.py` one — the prompt now requires one CAPS word per line and an
  em-dash aside somewhere in the middle. A mechanical injector in `voice.py`
  would have to guess which word carries the stress; the writer knows.

That is the answer to "is the fix in the writing": yes for *how the words are
spelled*, no for *how disfluent they are*.

---

## 5. Before / after, as shipped

Two lanes, 4 runs each, real copy from the pipeline. BEFORE is the palette and
prose style on `main`; AFTER is the new palette (non-verbals) plus copy written
to the new orthography rule — same claims, same order, same beat count.

**founder-story** (George)

| metric | before | after | Δ | noise |
|---|---|---|---|---|
| gapCV | 1.738 | 1.818 | +4.6% | ±0.193 |
| durCV | 0.782 | 0.835 | +6.6% | ±0.041 |
| vsd | 6.795 | 7.066 | +4.0% | ±0.208 |
| vrange | 22.09 | 22.63 | +2.4% | ±1.095 |
| longgap | 7.25 | 8.00 | +10.3% | ±1.000 |
| duration | 33.60 s | 36.28 s | +8.0% | ±0.800 |

**contrarian** (Adam)

| metric | before | after | Δ | noise |
|---|---|---|---|---|
| gapCV | 1.312 | 1.338 | +2.0% | ±0.097 |
| durCV | 0.776 | 0.801 | +3.2% | ±0.050 |
| vsd | 7.235 | 7.404 | +2.3% | ±0.250 |
| vrange | 23.21 | 23.66 | +1.9% | ±0.900 |
| longgap | 6.00 | 7.00 | +16.7% | ±1.000 |
| duration | 23.76 s | 29.44 s | +23.9% | ±0.520 |

### Read this honestly

**No individual metric clears 2× its noise floor.** One-sided permutation tests
(n=4 vs 4, all 70 arrangements) put most at p = 0.07–0.33; only founder `vsd`
reaches p = 0.043.

What *is* significant is the consistency. **All 12 lane × metric comparisons
moved in the intended direction. Sign test: p = 0.00024.** The change is real
and it is small.

That is expected, and the reason matters: **the big win — performance tags,
+113% on phrasing — was already shipped before this work started.** What this
change adds is the two remaining increments, non-verbals (+12% isolated) and
orthography (+16% isolated), on top of a baseline that already had the main
lever. A few percent each is what that should look like.

### The cost, and a bug it exposed

The AFTER copy is ~10% longer in words (73→80, 59→65) and 8–24% longer in
runtime. Words per second dropped from 2.49 to 2.21 on the contrarian lane —
tags and stress spend real time.

Which surfaced a live bug: `remix.py` told the writer to budget **2.6 words per
second**. Measured across 40 tagged renders the median is **2.19**, and it never
exceeded 2.30. Every film was being written ~15% over its `target_seconds`. The
prompt now says 2.2.

---

## 6. What changed in the code

| file | change |
|---|---|
| `vira/voice.py` | Docstring corrected — tags buy phrasing, not dynamic range; loudness comes from orthography. `stability` comment corrected with the re-measured result. Note that `speed` is inert on v3 and must not be sent. `[exhales]` added to the default middle palette. |
| `vira/lanes.py` | Every lane palette gains a non-verbal (`[sighs]`, `[exhales]`, `[inhales]`, `[laughs]`, `[scoffs]`). |
| `vira/remix.py` | New `# WRITING FOR THE MOUTH` section: one CAPS word per line, em-dash aside in the middle, ellipses explicitly do not mean pause. Words-per-second budget 2.6 → 2.2. Beat `delivery` guidance now explains that tags create pauses and recommends a non-verbal where earned. |
| `tests/test_lanes.py` | Asserts every lane can breathe. |

Not changed, deliberately: `stability` stays 0.0, `speed` is not added, no
mechanical CAPS injection in `voice.py`, no `eleven_multilingual_v2` fallback.

---

## 7. Limits

- **Nobody listened.** These are numbers, not a listening test. `gapCV` and
  `durCV` are proxies for "sounds like a person" and they are decent ones, but
  the correlation with human judgement is assumed, not measured. The Terac panel
  is the right instrument for that and it is not wired to audio.
- **One voice per lane, two lanes in the final A/B.** George and Adam. Effects
  may differ on the other three.
- **One script.** Overcast mineral SPF, ~30 s, 7 beats. A 12-second five-beat
  film may behave differently.
- **v3 only.** None of this was checked on `eleven_multilingual_v2`, which has a
  different settings surface (`style` and `use_speaker_boost` exist there).
- The rewrite-for-speech condition was tested as *written disfluency*. A
  different rewrite — shorter beats, more sentence boundaries — might move
  phrasing where fillers did not. Untested.

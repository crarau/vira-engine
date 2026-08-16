# Hook craft — what the corpus actually says

*Measured against the live Lovable database on 2026-08-15. 4,617 scraped
TikToks, of which 2,669 qualify. Reproduce with the scripts described in §2.*

The hooks we shipped were fine and identical. Four consecutive real examples:

```
Stop saying mineral sunscreen isn't for dark skin.
I stopped wearing sunscreen. All of it.
This was breakfast ten seconds ago.
Ten seconds. That's it.
```

Three of the four open on a banned class and three of the four contain a
verbless fragment. That is not a vocabulary problem, it is one clause shape
repeated. This document is the evidence for replacing it, and the rules that
replaced it.

---

## 1. What the corpus is, and what it is not

`trends` holds 4,617 TikTok posts with `views`, `likes`, `comments`, `shares`
and a precomputed `engagement_rate`. Nobody had looked at the language.

**The hook surface analysed here is the first sentence of the post caption.**
That is a real limitation and it should be stated before any number: we have no
transcripts, so we cannot analyse the *spoken* hook. The caption's first
sentence is the nearest available proxy — it is the string the creator
front-loads, it is what a scrolling viewer reads, and it is written to the same
job. It is not the same thing as the voiceover's first line.

Eligibility, applied in this order:

| Filter | Kept |
|---|---|
| all rows | 4,617 |
| non-empty caption | 4,604 |
| latin-script (<4% non-latin after stripping emoji/URLs) | — |
| `views >= 10,000` | — |
| first sentence parses to >= 2 words, not a hashtag block | **2,669** |

### The view floor is load-bearing

Raw `engagement_rate` tops out at 271% — a video with 102 views and 3 likes.
Without a floor the "top" cohort is just "small accounts". With the 10,000-view
floor the confound disappears:

```
spearman(views, engagement_rate) = 0.004     # above the floor, ER is view-independent
spearman(views, hook word count) = -0.027
spearman(engagement_rate, hook word count) = -0.004
```

Corpus median ER = **5.19%**. Cohorts below are the top 250 and bottom 250 by
ER, plus population medians with bootstrap CIs (2,000 resamples).

### Classifier honesty

Opening class and clause type are assigned by a rule-based classifier (no spaCy
or NLTK in this venv, and a hand-auditable rule set is arguably better here). A
blind audit of 45 random classifications found 3–5 debatable calls, so treat
every proportion as **≈90% accurate**. That is enough to separate a 56%/44%
split; it is not enough to trust a 2-point difference.

---

## 2. Results

### 2.1 The single biggest finding: labels lose to clauses

Verbless fragments — noun phrases with no tensed verb, like *"Ten seconds.
That's it."* or *"mini clothing haul"* — are **49% of the whole corpus**, and
they are distributed the wrong way.

| | top 250 | bottom 250 | z |
|---|---|---|---|
| verbless fragment | 44.4% | 56.0% | **−2.59** |
| full finite clause | 46.8% | 38.0% | **+1.99** |

Population medians:

| clause type | n | median ER | index |
|---|---|---|---|
| question | 151 | 5.51% | 106 |
| full clause | 1,119 | 5.46% | 105 |
| command | 85 | 5.13% | 99 |
| **verbless fragment** | **1,314** | **4.95%** | **95** |

### 2.2 The second: an impersonal hook under-performs

| | n | median ER | index | 95% CI | verdict |
|---|---|---|---|---|---|
| has I/my | 700 | 5.98% | 115 | [5.58, 6.40] | **robust +** |
| has we/our | 216 | 5.99% | 115 | [5.13, 6.76] | + |
| has you/your | 442 | 5.19% | 100 | [4.72, 5.78] | null |
| **none of the three** | **1,477** | **4.84%** | **93** | **[4.59, 5.10]** | **robust −** |

The CI on "no person marker" excludes the corpus median. This is the cleanest
result in the study, and it holds in 8 of 8 category verticals (first-person
index by vertical: pets 134, fitness 125, food 123, electronics 118, home 118,
baby-kids 110, beauty 101, apparel 93).

### 2.3 Combining the two gives the rule

| shape | n | median ER | index | 95% CI |
|---|---|---|---|---|
| finite clause + I/we/you | 869 | 5.66% | 109 | [5.24, 6.06] ✔ |
| fragment + person | 323 | 5.78% | 111 | [5.12, 6.42] |
| finite clause, no person | 486 | 5.07% | 98 | [4.69, 5.59] |
| **fragment, no person** | **991** | **4.73%** | **91** | **[4.29, 5.03]** ✔ |

**37% of the corpus sits in the worst cell, and it is the cell our generator was
writing in.**

### 2.4 Opening word class

| opening | n | median ER | index | 95% CI |
|---|---|---|---|---|
| first-person (I/we) | 289 | 6.43% | **124** | [5.70, 7.11] ✔ |
| question word / aux | 114 | 5.99% | 115 | [5.02, 7.28] |
| number | 107 | 5.57% | 107 | — |
| conditional (if/when) | 41 | 5.47% | 105 | — |
| article NP ("The …") | 167 | 5.36% | 103 | — |
| adjective | 160 | 5.26% | 101 | — |
| bare noun / other | 1,197 | 5.05% | 97 | — |
| demonstrative | 199 | 4.96% | 96 | [4.28, 6.23] |
| gerund | 184 | 4.92% | 95 | — |
| **imperative** | 128 | 4.73% | **91** | [4.26, 5.52] |
| **negation** | 42 | 4.44% | **86** | [2.70, 5.68] |

### 2.5 Everything else, ranked by z (top 250 vs bottom 250)

| feature | top | bottom | z |
|---|---|---|---|
| ≥1 ALLCAPS word | 26.8% | 14.0% | **+3.55** |
| we/our anywhere | 10.0% | 4.0% | **+2.63** |
| verbless fragment | 44.4% | 56.0% | **−2.59** |
| contraction | 21.2% | 14.0% | **+2.11** |
| I/my anywhere | 28.4% | 20.4% | **+2.08** |
| you/your anywhere | 20.4% | 13.6% | **+2.02** |
| ≤6 words | 32.0% | 40.0% | −1.86 |
| opens imperative | 6.4% | 3.2% | +1.67 |
| opens demonstrative | 4.8% | 8.4% | −1.62 |
| ends "..." | 3.6% | 6.4% | −1.44 |
| contains a digit | 24.0% | 22.0% | +0.53 |
| ends "!" | 26.8% | 25.6% | +0.31 |
| ends "." | 19.2% | 19.6% | −0.11 |
| no terminal punctuation | 44.8% | 44.8% | **0.00** |
| throat-clearing ("Hey", "So") | 1.2% | 1.2% | **0.00** |

### 2.6 Naming the brand — the most surprising result

| | n | median ER | index | 95% CI |
|---|---|---|---|---|
| first sentence contains an @handle | 394 | 6.62% | **128** | [5.99, 7.38] ✔ |
| does not | 2,275 | 4.98% | 96 | [4.76, 5.18] ✔ |

The strongest positive effect measured, and it contradicts the standard "never
lead with the brand" advice. **Caveat, and it is a serious one:** an @mention
mechanically recruits the tagged account's audience into the engagement
denominator's numerator. Part of this is distribution, not copy. It supports
"name the thing" but it does not prove "naming it caused the lift".

---

## 3. Five things that turned out not to matter

Worth as much as the positives, because each one is folklore we were free to
stop obeying.

1. **Length is not the lever.** ρ(ER, word count) = −0.004. Median hook is 9
   words in the top cohort and 8 in the bottom. Short hooks are *over*-
   represented at the bottom (≤6 words: 32% top vs 40% bottom). Short-and-
   verbless is the failure mode; short-and-finite is fine.
2. **Numbers do nothing.** Contains a digit: index 101, CI [4.86, 5.62],
   straddles the median. Digit-plus-unit: index 106, CI straddles. The "use a
   specific number" rule is not supported here.
3. **Terminal punctuation does nothing.** Full stop, exclamation mark and no
   punctuation at all are within a rounding error (z = −0.11, +0.31, 0.00). Only
   the trailing ellipsis is negative, and weakly.
4. **Throat-clearing is not a differentiator.** "Hey/So/Okay" openings are 1.2%
   in *both* cohorts. Still banned — it costs nothing and the first word should
   be load-bearing — but it is not why our hooks were flat.
5. **Brochure language is already absent.** 0.8% top, 1.2% bottom. Same
   reasoning: keep the ban, stop crediting it.

---

## 4. External research, and where it disagrees

Full source ledger in the research pass; only the load-bearing items here. The
critical structural point: **there is no published word-class analysis of video
hooks.** Every word-level number in the literature comes from *text headlines*
(news, content-recommendation widgets). Every video-platform number is
*element*-level (text present, face present, logo present). So the corpus above
is the only direct evidence we have, and it wins ties.

### Where they agree

| Claim | External | Corpus |
|---|---|---|
| Imperatives under-perform | Outbrain, N>100k paid links: imperative modals −20% | index 91 |
| Positive superlatives under-perform | Outbrain, N=65k: "best"/"always" −29% | 0.8% top vs 1.2% bottom |
| Don't open on a brand *label* | VidMob × TikTok, N=1,678 ads / 7.3B impressions: logo-alone −14% on 6s VTR; brand-as-protagonist +1.5× hooking | @handle index 128 |
| Question hooks are not magic | Backlinko N=4M: 16.3% vs 15.5%, not significant | index 115, wide CI |

### Where they disagree — corpus wins

**Negation.** Robertson et al., *Nature Human Behaviour* 2023 (N≈105,000
headline variants, 370M impressions, 22,743 RCTs) measured **+2.3% CTR per
negative word**; Outbrain measured negative superlatives at **+30%**. Our corpus
measures negation *as the opening word* at index **86 — the lowest class in the
table**.

These are reconcilable and the reconciliation is the rule: **the disagreement is
about position, not about negation.** Negative content inside the clause is
well-evidenced and permitted; the first slot is where it fails. Hence rule 8
bans `Stop/Don't/Never` in position 1 and explicitly allows negation elsewhere.

**Second person.** Outbrain measured "you/your" at **−21%** (N>100k, 2012
content-recommendation widgets, mechanism proposed = ad-detection). VidMob
measured direct-to-camera second-person *framing* at **+14%** on 2-second
view-through. Our corpus: "you" anywhere is **null** (index 100), "you" as the
opening word is rare (n=24) and flat. Resolution shipped: `you` is permitted as
one of the three person markers, but `first-person-admission` and
`first-person-plural-claim` are the shapes the director is steered toward,
because those are what the corpus actually rewards (index 124).

**Numbers.** BuzzSumo (N=100M) favours single digits 3–10. Corpus says null.
Not banned, not required.

### Where external research is stronger than the corpus

**Specificity has an optimum, and it is not "maximum".** Aubin Le Quéré &
Matias, *Scientific Reports* 2025 — a meta-analysis of **8,977 randomised A/B
tests / 35,910 headline arms** from the Upworthy archive — found an inverted U
in word-level concreteness: below a baseline of 2.58, adding concreteness gains
**+5.5%**; above 3.06, adding it costs **−9.9%**. The penalty for
over-specifying is roughly double the reward for under-specifying. This
converges with Loewenstein's information-gap theory (1994), which predicts the
same curve from a different direction.

Our corpus cannot see this — it has no concreteness scoring — so this is adopted
on external evidence alone, as rule 14: **one concrete anchor, one withheld
element.** It is also the reason rule 14 does *not* demand a number.

---

## 5. The rules, and what each one rests on

Shipped in `vira/remix.py::SYSTEM` under `# HOOK GRAMMAR`.

| # | Rule | Evidence |
|---|---|---|
| 1 | Hook is a **finite clause**, not a label | corpus, z=−2.59 |
| 2 | Contains **I / we / you** | corpus, CI-clean, 8/8 verticals |
| 3 | **4–14 words**; do not chase brevity | corpus, ≤6w over-represented at bottom |
| 4 | **Contractions** | corpus, z=+2.11 |
| 5 | **Exactly one CAPS word** (acronyms don't count) | corpus z=+3.55, **and** measured to move delivery (docs/VOICE.md) |
| 6 | Name the product **as subject of a verb** | corpus index 128 (confounded); VidMob brand-as-protagonist |
| 7 | No **imperative** first | corpus index 91; Outbrain −20% |
| 8 | No **negation** first (allowed inside) | corpus index 86; position-only, see §4 |
| 9 | No **demonstrative** first | corpus index 96, 4.8% vs 8.4% |
| 10 | No **brand name as label** first | VidMob −14% |
| 11 | No **throat-clearing** | free; not a differentiator |
| 12 | No **positive superlatives** | Outbrain −29% |
| 13 | No **trailing ellipsis** | corpus 3.6% vs 6.4%; also produces no pause |
| 14 | **One anchor, one gap** | *Scientific Reports* 2025, N=8,977 |

Deliberately **not** written as rules, because the data says they are noise:
terminal punctuation, digit presence, hook length below 14 words, exclamation
marks.

---

## 6. Before / after

### The prompt

**Before** — `remix.py::SYSTEM`, the entire hook guidance:

```
- Spoken lines are for saying out loud. Short. Contractions. No brochure copy.
```

and in `PROMPT`:

```
"hook": "spoken in the first 2 seconds, under 90 chars, must stop a scroll",
```

Two adjectives and a character count. Nothing grammatical, nothing bannable,
nothing checkable — which is why five lanes produced one clause shape.

**After** — a 14-rule `# HOOK GRAMMAR` block with required forms, banned
openings and the specificity rule (§5), plus:

```
"hook": "the first line, spoken. A finite clause of 4-14 words carrying I/we/you
         and exactly one CAPS word. Obeys the hook shape above and every rule in
         HOOK GRAMMAR."
```

### Three more changes the analysis forced

1. **The director now picks the hook's grammar.** `VideoPlan.hook_shape`, chosen
   from seven `HOOK_SHAPES` in `director.py`, each a permitted opening class with
   a worked example. This is the actual fix for "samey": nothing previously
   varied clause structure across lanes, so five directors independently
   converged on the safest one.
2. **`hook_faults()` audits every generated hook** and logs each violation. A
   prompt rule nobody checks is a suggestion. It is warn-only — the scorer is the
   gate, not this — and the critic in `director.py` is handed the fault list, so
   a bad hook gets a revision pass aimed at it.
3. **Two lane briefs were steering into banned openings** and were rewritten.
   `problem-first` said *"Name the pain in the first three words"* (a recipe for
   a verbless label); `contrarian` said *"State the popular advice, reject it"*
   (a recipe for `Stop saying…`). Both now specify a person and a finite verb.

### The output

Five lanes, one company, real Azure calls, `hook_faults` run on each:

| lane | shape chosen | hook | faults |
|---|---|---|---|
| problem-first | first-person-admission | I thought reapplying SPF meant a WHITE cast. | clean |
| demo-first | counted-anchor | I did one MORE layer at hour four. | clean |
| founder-story | first-person-admission | I thought reapplying mineral SPF looked WORSE by lunch. | clean |
| social-proof | reported-speech | People said mineral SPF turns me WHITE — watch this. | clean |
| contrarian | reported-speech | They told me mineral SPF turns CHALKY at reapply. | clean |

5/5 conforming. All four original samples fail the checker; `tests/test_hooks.py`
asserts that they do, so the rules cannot silently stop excluding the thing they
were written to exclude.

---

## 7. Known limitations

- **Caption ≠ voiceover.** The whole study is on written first sentences. If
  transcripts ever land (`docs/CONTEXT-RETRIEVAL.md` Tier 2 proposes sampling
  audio on the top ~20 per run), rerun this against spoken hooks first.
- **Engagement rate ≠ retention.** ER is likes+comments+shares over views. The
  hook's real job is stopping a scroll, which is a *view-through* metric we do
  not have. A hook could raise comments and lose viewers.
- **Only three distinct shapes across five lanes** in the verification run, since
  lanes plan in parallel and cannot see each other's choice. Passing the sibling
  lanes' shapes into the plan prompt would fix it; not done, because lane brief
  and hook shape *should* correlate.
- **The @handle result is confounded** by mention-driven distribution (§2.6).
- **n=42 for negation openings.** CI [2.70, 5.68]. There is no evidence it helps
  and directional evidence it hurts, but it is not proven harmful.
- **The Upworthy Research Archive is open** — 32,487 experiments, 150,817 arms,
  538M assignments, with per-arm impressions and clicks. Every word-class
  question here is directly computable on it with a POS tagger. Nobody has done
  the by-position analysis. That is the obvious next study.

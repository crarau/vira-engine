"""Stage 3 — what is actually working in this category, and what competitors run.

Two passes rather than one, because they fail differently. The corpus pass is
pattern-finding over verified rows. The competitor pass is a lookup that must be
allowed to come back empty — "Blizzard is not in this corpus" is a correct
answer, and a model asked to describe competitor ads it cannot see will invent
them fluently.
"""

from __future__ import annotations

import logging

from vira.llm import complete_json
from vira.models import Company, CompetitorFinding, CorpusAnalysis, Trend

log = logging.getLogger(__name__)

CORPUS_SYSTEM = """You analyse short-form video advertising corpora. You are \
specific and evidence-bound.

Rules:
- Every claim must cite the trend keys it came from. A claim you cannot cite \
does not go in the output.
- Never invent a video, a metric, or a brand that is not in the material.
- "Post consistently", "use trending audio", "engage your audience" are \
worthless. Say what these specific videos did.
- Keep every string under 300 characters. Terse beats thorough.\n- JSON only."""

CORPUS_PROMPT = """# Brand this analysis is for
{company}

# Verified corpus ({n} videos, all live-checked, all under 90 days old)
{corpus}

# Task
Return JSON:
{{
  "dominant_formats": ["format — what makes it work here", ...],   // 2-4, each under 200 chars
  "recurring_hooks": ["the actual hook pattern, quoted where possible", ...],  // 3-5, each under 150 chars
  "what_top_performers_share": "the shared mechanism, under 300 chars",
  "whitespace": "what nobody here is doing that this brand could own, under 300 chars",
  "citations": ["VIRA-TR-...", ...]   // every key you drew on
}}"""

COMPETITOR_SYSTEM = """You report what a named competitor is running, using only \
the supplied corpus.

If the competitor does not appear, say so plainly and set present_in_corpus \
false. Do not describe ads you cannot see. An empty finding is correct and \
useful; a fabricated one is worse than nothing. JSON only."""

COMPETITOR_PROMPT = """# Competitor
{competitor}

# Corpus (search this for the competitor by author handle, caption, or hashtag)
{corpus}

# Task
Return JSON:
{{
  "present_in_corpus": true|false,
  "what_they_run": "what their videos actually do — format, hook, angle. Empty string if absent.",
  "citations": ["VIRA-TR-...", ...]
}}"""


def _corpus_block(trends: list[Trend]) -> str:
    return "\n\n".join(t.brief() for t in trends)


async def analyze_corpus(
    company: Company, product: str, trends: list[Trend]
) -> CorpusAnalysis:
    if not trends:
        return CorpusAnalysis(whitespace="No verified trends to analyse.")

    data = await complete_json(
        CORPUS_PROMPT.format(
            company=company.context(product),
            n=len(trends),
            corpus=_corpus_block(trends),
        ),
        system=CORPUS_SYSTEM,
    )

    valid = {t.trend_key for t in trends}
    citations = [c for c in data.get("citations", []) if c in valid]
    if dropped := set(data.get("citations", [])) - valid:
        log.warning("model cited %d keys not in the corpus: %s", len(dropped), dropped)

    return CorpusAnalysis(
        dominant_formats=data.get("dominant_formats", []),
        recurring_hooks=data.get("recurring_hooks", []),
        what_top_performers_share=data.get("what_top_performers_share", ""),
        whitespace=data.get("whitespace", ""),
        citations=citations,
    )


async def analyze_competitors(
    company: Company, competitors: list[str], trends: list[Trend]
) -> list[CompetitorFinding]:
    findings: list[CompetitorFinding] = []
    valid = {t.trend_key for t in trends}

    for name in competitors:
        # Pre-filter locally so the model sees a focused slice, and so an
        # obviously-absent competitor never costs a call.
        needle = name.lower()
        hits = [
            t for t in trends
            if needle in t.author.lower()
            or needle in t.caption.lower()
            or any(needle in h.lower() for h in t.hashtags)
        ]
        if not hits:
            findings.append(
                CompetitorFinding(competitor=name, present_in_corpus=False)
            )
            continue

        data = await complete_json(
            COMPETITOR_PROMPT.format(competitor=name, corpus=_corpus_block(hits)),
            system=COMPETITOR_SYSTEM,
            max_tokens=1200,
        )
        findings.append(
            CompetitorFinding(
                competitor=name,
                present_in_corpus=bool(data.get("present_in_corpus")),
                what_they_run=data.get("what_they_run", ""),
                citations=[c for c in data.get("citations", []) if c in valid],
            )
        )
    return findings

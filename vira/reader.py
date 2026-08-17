"""Read the user's own material — the only source a claim may cite.

This is the load-bearing change in V2. The prototype grounded claims in scraped
TikToks, which cannot possibly know whether a product contains 12g of protein,
so the evidence gate correctly rejected everything (docs/V2-SPEC.md §2). Claims
are now checked against pages the user themselves pointed us at: their product
page, their about page, their spec sheet.

`trafilatura` rather than Firecrawl for V1. It is a local library — no account,
no per-page cost, no rate limit, no network hop beyond the fetch itself — and it
was built for exactly this: pulling the main content out of a page and leaving
the nav, cookie banner and footer behind. Firecrawl stays behind `PageReader` for
the JS-heavy pages trafilatura cannot see (§8).

Two disciplines carried over from the prototype, both hard-won:

**Verify before you reason.** A page is fetched and parsed before a single token
reaches a model. A URL that 404s is reported to the user, not quietly dropped —
they chose that URL and they are the only one who can fix it.

**A fact keeps its sentence.** Every extracted fact stores the sentence it came
from and the URL it came from, so the claim gate can check containment and the
recipe can show provenance for every number in the script.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

# Real browser UA. Several ecommerce platforms serve a challenge page to obvious
# bots, and a challenge page parses into plausible-looking garbage rather than an
# error — the worst failure mode available, because it is silent.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

MAX_BYTES = 4_000_000
MAX_TEXT_CHARS = 40_000
FETCH_TIMEOUT = 20.0
CONCURRENCY = 6


@dataclass(frozen=True)
class Fact:
    """One assertion, with the sentence and page it came from."""

    text: str
    sentence: str
    url: str


@dataclass
class Page:
    url: str
    ok: bool
    title: str = ""
    text: str = ""
    error: str | None = None
    facts: list[Fact] = field(default_factory=list)


# A sentence is worth extracting as a fact when it carries something checkable:
# a number, a unit, a currency, a percentage, or a comparative. Prose without any
# of those ("we care deeply about quality") supports no factual claim, and
# treating it as though it did is how V1's gate got fooled.
_CHECKABLE = re.compile(
    r"""(
        \d                                  # any digit
      | [$£€]                               # price
      | \b(?:free|guaranteed|certified|organic|vegan|gluten[- ]free)\b
      | \b(?:more|less|fewer|faster|cheaper|stronger|higher|lower)\s+than\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Splits after .!? on whitespace OR directly before a capital, because
# trafilatura concatenates block elements with no separator when the source HTML
# is minified: three <p> tags become "...per jar.A box of twelve costs $39...".
# Requiring whitespace turned that into one 200-char pseudo-sentence and cost
# every fact on the page. Minified HTML is the norm on Next.js and Shopify, so
# this is the common case, not the edge case.
#
# It over-splits initialisms ("U.S.A." -> "U.", "S.", "A."), which is harmless:
# the fragments fall below the three-word floor and are discarded.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])(?:\s+|(?=[A-Z]))")


def extract_facts(text: str, url: str) -> list[Fact]:
    """Sentences that could support a factual claim, deduped, order preserved."""
    seen: set[str] = set()
    out: list[Fact] = []
    for raw in _SENTENCE_SPLIT.split(text):
        s = " ".join(raw.split())
        # Filtered on WORDS, not characters. "Ships in 2 days." is 16 chars and
        # a perfectly good fact; a 20-char floor rejected it. Under three words
        # is a fragment ("12g."), and over 400 chars is a parsing failure that
        # swallowed a whole section — neither works as provenance.
        if len(s) > 400 or len(s.split()) < 3:
            continue
        if not _CHECKABLE.search(s):
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(Fact(text=s, sentence=s, url=url))
    return out


async def read_one(client: httpx.AsyncClient, url: str) -> Page:
    """Fetch and parse one URL. Never raises — a bad URL is data, not an error."""
    import trafilatura

    try:
        r = await client.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True,
                             headers={"User-Agent": UA})
    except httpx.HTTPError as exc:
        return Page(url=url, ok=False, error=f"could not reach it: {exc}")

    if r.status_code >= 400:
        return Page(url=url, ok=False, error=f"the page returned {r.status_code}")

    ctype = r.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return Page(url=url, ok=False, error=f"not a web page ({ctype or 'unknown type'})")

    body = r.content[:MAX_BYTES]

    # Parsing is CPU-bound lxml work. Off the event loop, or one slow page
    # stalls every other fetch in the same generation.
    def parse() -> tuple[str, str]:
        extracted = trafilatura.extract(
            body.decode(r.encoding or "utf-8", errors="replace"),
            include_comments=False,
            include_tables=True,
            favor_recall=True,          # product pages are short; prefer more text
            url=url,
        ) or ""
        meta = trafilatura.extract_metadata(body)
        return (getattr(meta, "title", "") or "") if meta else "", extracted

    try:
        title, text = await asyncio.to_thread(parse)
    except Exception as exc:  # noqa: BLE001 — a parser crash is a bad page, not an outage
        return Page(url=url, ok=False, error=f"could not parse it: {type(exc).__name__}")

    text = text[:MAX_TEXT_CHARS].strip()
    if len(text) < 80:
        # Almost always a JS-rendered page. Name the cause, because the fix is
        # Firecrawl and the user should know why we came up empty.
        return Page(
            url=url, ok=False, title=title,
            error="almost no readable text — the page probably renders with JavaScript",
        )

    return Page(url=url, ok=True, title=title, text=text, facts=extract_facts(text, url))


async def read_all(urls: list[str]) -> list[Page]:
    """Read every URL concurrently. Order matches the input, so the first URL the
    user typed stays the lead source."""
    if not urls:
        return []

    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient() as client:
        async def guarded(u: str) -> Page:
            async with sem:
                return await read_one(client, u)

        pages = await asyncio.gather(*(guarded(u) for u in urls))

    ok = sum(1 for p in pages if p.ok)
    log.info("read %d/%d pages, %d facts", ok, len(pages), sum(len(p.facts) for p in pages))
    return list(pages)

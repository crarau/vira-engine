"""Stage 6.5 — find a real photograph for each beat.

Typography on black reads as a slide deck. Every beat needs an image behind it.

Source is Openverse: no API key, searchable, and filtered to commercially
licensed work. That last part matters — this is an ad. CC-BY requires
attribution, so every downloaded image carries its creator and licence through
to the render, where the composition prints a credit line.

Deliberately NOT used: the `coverUrl` values sitting in `trends.raw`. Those are
other creators' TikTok thumbnails. They are fine as a moodboard of what the
engine learned from; putting them inside the brand's own ad is someone else's
content, and the signed URLs expire in about two days anyway.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from vira.llm import complete_json
from vira.models import Company, Remix

log = logging.getLogger(__name__)

OPENVERSE = "https://api.openverse.org/v1/images/"
UA = "vira-engine/0.1 (hackathon; +https://github.com/crarau/vira-engine)"

QUERY_SYSTEM = """You turn film shot directions into stock photo search queries.

Rules:
- 2 to 4 plain words. Nouns and adjectives only.
- Describe the SUBJECT of the frame, not the camera move. "close on face,
  handheld" is not a query; "woman smiling portrait" is.
- Searchable, literal, and common. Stock libraries have "morning coffee";
  they do not have "existential dread about cortisol".
- ALWAYS anchor on a PERSON or a PHYSICAL SCENE. "person eating breakfast",
  "hands holding jar", "woman kitchen morning". Never query an abstract noun,
  a brand name, a recipe, or a document — those return scans of printed text,
  which look terrible behind a caption.
- Prefer people over objects. A face carries an ad; a product shot does not.
- JSON only."""

QUERY_PROMPT = """Brand: {brand} — {category}
Product: {product}

Beats:
{beats}

Return JSON: {{"queries": ["query for beat 1", "query for beat 2", ...]}}
Exactly {n} queries, in order."""


async def derive_queries(company: Company, product: str, remix: Remix) -> list[str]:
    beats = "\n".join(
        f"{i + 1}. say: {b.say}  |  show: {b.show}  |  shot: {b.shot}"
        for i, b in enumerate(remix.beats)
    )
    data = await complete_json(
        QUERY_PROMPT.format(
            brand=company.name, category=company.category, product=product,
            beats=beats, n=len(remix.beats),
        ),
        system=QUERY_SYSTEM,
        max_tokens=800,
    )
    queries = [str(q) for q in data.get("queries", [])]
    # Pad or trim so the caller always gets one query per beat.
    while len(queries) < len(remix.beats):
        queries.append(f"{company.category} lifestyle")
    return queries[: len(remix.beats)]


# Tried in order. StockSnap/rawpixel/nappy are modern CC0 stock and look like
# advertising; Flickr's CC archive is mostly amateur and vintage, so it is the
# fallback rather than the default. Never filter on aspect_ratio — it cuts
# coverage roughly in half and `object-fit: cover` handles framing anyway.
TIERS: list[dict[str, str]] = [
    {"source": "stocksnap,rawpixel,nappy"},
    {"license_type": "commercial"},
]


async def _try(client: httpx.AsyncClient, query: str, extra: dict, taken: set[str]) -> dict | None:
    try:
        r = await client.get(
            OPENVERSE,
            params={"q": query, "page_size": 12, "mature": "false", **extra},
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        log.debug("openverse %r %s failed: %s", query, extra, exc)
        return None

    for hit in r.json().get("results", []):
        url = hit.get("url")
        if not url or url in taken:
            # Avoid repeating the same photo across beats — it reads as a mistake.
            continue
        if _is_junk(hit):
            continue
        taken.add(url)
        return hit
    return None


# Openverse's CC corpus is full of scanned documents, book pages, maps, logos
# and screenshots. Behind a caption they look like a mistake, so reject them on
# title before they reach the render.
JUNK = (
    "recipe", "page", "text", "document", "scan", "manuscript", "letter",
    "poster", "map", "diagram", "chart", "cover", "book", "newspaper",
    "label", "logo", "screenshot", "sign", "menu", "card", "print ad",
)


def _is_junk(hit: dict) -> bool:
    title = (hit.get("title") or "").lower()
    return any(word in title for word in JUNK)


async def _search(client: httpx.AsyncClient, query: str, taken: set[str]) -> dict | None:
    """Quality tier first, then coverage tier, then a progressively shorter query.

    A four-word query with no hits is usually one over-specific word away from a
    good one, so dropping the last word beats giving up.
    """
    words = query.split()
    for attempt in (words, words[:-1], words[:1]):
        if not attempt:
            continue
        q = " ".join(attempt)
        for tier in TIERS:
            if hit := await _try(client, q, tier, taken):
                if q != query:
                    log.info("  (broadened %r → %r)", query, q)
                return hit
    return None


async def fetch_shots(
    company: Company, product: str, remix: Remix, dest: Path
) -> list[dict]:
    """Download one image per beat. Returns per-beat records with attribution."""
    queries = await derive_queries(company, product, remix)
    log.info("stock queries: %s", queries)
    dest.mkdir(parents=True, exist_ok=True)

    shots: list[dict] = []
    taken: set[str] = set()

    async with httpx.AsyncClient(timeout=45, headers={"User-Agent": UA},
                                 follow_redirects=True) as client:
        for i, query in enumerate(queries):
            hit = await _search(client, query, taken)
            if not hit:
                log.warning("no image for beat %d (%r)", i + 1, query)
                shots.append({"file": None, "query": query, "credit": None})
                continue

            name = f"shot{i:02d}.jpg"
            try:
                img = await client.get(hit["url"])
                img.raise_for_status()
                (dest / name).write_bytes(img.content)
            except httpx.HTTPError as exc:
                log.warning("download failed for beat %d: %s", i + 1, exc)
                shots.append({"file": None, "query": query, "credit": None})
                continue

            creator = hit.get("creator") or "unknown"
            lic = f"CC {(hit.get('license') or '').upper()} {hit.get('license_version') or ''}".strip()
            shots.append({
                "file": name,
                "query": query,
                "title": hit.get("title") or "",
                "credit": f"{creator} · {lic}",
                "source": hit.get("foreign_landing_url") or hit.get("url"),
            })
            log.info("beat %d ← %r by %s", i + 1, query, creator)

    return shots

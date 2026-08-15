"""Single entry point for beat imagery.

Callers should not care whether a frame was generated or found. They ask for
imagery; this decides how to get it and guarantees a uniform record back.
"""

from __future__ import annotations

import logging
from pathlib import Path

from vira.config import settings
from vira.models import Company, Remix

log = logging.getLogger(__name__)


async def fetch_or_generate(
    company: Company, product: str, remix: Remix, dest: Path, look: str = ""
) -> list[dict]:
    s = settings()
    if s.image_source == "gemini" and s.gemini_api_key:
        from vira.imagegen import generate_shots

        try:
            return await generate_shots(company, product, remix, dest, look)
        except Exception as exc:  # noqa: BLE001 - never lose a video over imagery
            log.warning("generation failed (%s); falling back to stock", exc)

    from vira.stock import fetch_shots

    return await fetch_shots(company, product, remix, dest)

"""The reader is the only source a claim may cite, so its failure modes matter
more than its happy path. A page that silently parses to plausible garbage would
let the claim gate approve an invented number — the exact failure V2 exists to
prevent.
"""

from __future__ import annotations

import httpx
import pytest

from vira.reader import (
    MAX_TEXT_CHARS,
    Page,
    extract_facts,
    read_all,
    read_one,
)

# --- fact extraction ------------------------------------------------------


def test_a_sentence_with_a_number_is_a_fact():
    facts = extract_facts("Each serving contains 12g of plant protein.", "u")
    assert len(facts) == 1
    assert "12g" in facts[0].text


def test_prose_with_nothing_checkable_is_not_a_fact():
    """'We care deeply about quality' supports no factual claim. Treating it as
    though it did is how a gate gets fooled into approving anything."""
    assert extract_facts("We care deeply about quality and craft.", "u") == []


@pytest.mark.parametrize("sentence", [
    "Our subscription costs $39 per month for twelve servings.",
    "It is certified organic by the Soil Association board.",
    "This blend is cheaper than most competing meal replacements.",
    "Delivery is free on every order over a certain amount.",
])
def test_prices_certifications_comparatives_and_free_all_count(sentence):
    assert extract_facts(sentence, "u"), sentence


def test_a_fact_keeps_its_sentence_and_url():
    """Provenance is the whole point: the recipe has to be able to show where a
    number in the script came from."""
    f = extract_facts("Ships in 2 days.  ", "https://example.com/p")[0]
    assert f.url == "https://example.com/p"
    assert f.sentence == f.text == "Ships in 2 days."


def test_fragments_and_runaway_blocks_are_both_rejected():
    assert extract_facts("12g.", "u") == [], "too short to be provenance"
    assert extract_facts("x" * 200 + " 12g " + "y" * 300 + ".", "u") == [], \
        "a 500-char 'sentence' is a parsing failure, not a fact"


def test_duplicates_collapse_but_order_is_kept():
    text = "Contains 12g protein. Ships in 2 days. contains 12g protein."
    facts = extract_facts(text, "u")
    assert [f.text for f in facts] == ["Contains 12g protein.", "Ships in 2 days."]


# --- fetching -------------------------------------------------------------


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_real_page_yields_text_and_facts():
    html = (
        "<html><head><title>Sunday Oats</title></head><body><article>"
        "<p>Our overnight oats contain 12g of protein per jar.</p>"
        "<p>A box of twelve costs $39 and ships in 2 days.</p>"
        "<p>We think mornings should be easier than they are.</p>"
        "</article></body></html>"
    )
    async with _client(lambda r: httpx.Response(200, html=html)) as c:
        page = await read_one(c, "https://example.com/oats")

    assert page.ok
    assert "12g of protein" in page.text
    assert len(page.facts) >= 2
    assert all(f.url == "https://example.com/oats" for f in page.facts)


async def test_a_404_is_reported_not_swallowed():
    """The user chose this URL and is the only one who can fix it."""
    async with _client(lambda r: httpx.Response(404)) as c:
        page = await read_one(c, "https://example.com/gone")
    assert not page.ok
    assert "404" in (page.error or "")


async def test_an_unreachable_host_is_data_not_an_exception():
    def boom(request):
        raise httpx.ConnectError("no dns", request=request)

    async with _client(boom) as c:
        page = await read_one(c, "https://nope.example/")
    assert not page.ok and page.error


async def test_a_javascript_shell_names_javascript_as_the_cause():
    """An empty React root parses to nothing. Reporting 'no text' would send the
    user hunting; naming JS points them at the actual fix."""
    async with _client(lambda r: httpx.Response(200, html="<html><body><div id='root'></div></body></html>")) as c:
        page = await read_one(c, "https://spa.example/")
    assert not page.ok
    assert "JavaScript" in (page.error or "")


async def test_a_pdf_is_refused_by_content_type():
    async with _client(lambda r: httpx.Response(200, content=b"%PDF-1.7",
                                                headers={"content-type": "application/pdf"})) as c:
        page = await read_one(c, "https://example.com/spec.pdf")
    assert not page.ok
    assert "not a web page" in (page.error or "")


async def test_enormous_pages_are_truncated():
    body = (
        "<html><body><article>"
        + ("<p>It contains 12g of protein per serving.</p>" * 20000)
        + "</article></body></html>"
    )
    async with _client(lambda r: httpx.Response(200, html=body)) as c:
        page = await read_one(c, "https://example.com/huge")
    assert len(page.text) <= MAX_TEXT_CHARS


# --- concurrency and ordering --------------------------------------------


async def test_no_urls_means_no_network(monkeypatch):
    async def explode(*a, **k):
        raise AssertionError("read_all must not open a client for an empty list")

    monkeypatch.setattr(httpx, "AsyncClient", explode)
    assert await read_all([]) == []


async def test_order_matches_input_so_the_first_url_stays_the_lead(monkeypatch):
    """The first URL the user typed is the lead source. asyncio.gather preserves
    input order, and this test pins that rather than trusting it."""
    async def fake(client, url):
        return Page(url=url, ok=True, text="x" * 100)

    monkeypatch.setattr("vira.reader.read_one", fake)
    urls = [f"https://e{i}.example/" for i in range(6)]
    pages = await read_all(urls)
    assert [p.url for p in pages] == urls

"""Stage 2 — source verification.

The load-bearing case is the soft 404: TikTok answers 200 with a normal-looking
page for a video that has been removed, so a status-code-only check would let a
dead link reach a judge. Everything else here is guard rails around that.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from vira.verify import GONE_MARKERS, verify_all
from tests.conftest import make_trend

LIVE_HTML = "<html><title>a real video</title><body>watch this</body></html>"


def url_for(key: str) -> str:
    return f"https://www.tiktok.com/@someone/video/{key}"


@respx.mock
async def test_healthy_200_is_verified():
    respx.get(url_for("ok")).mock(return_value=httpx.Response(200, text=LIVE_HTML))
    verified, dropped = await verify_all([make_trend("ok")])

    assert dropped == []
    assert verified[0].verified is True
    assert verified[0].drop_reason is None


@respx.mock
async def test_404_is_dropped_with_a_reason():
    respx.get(url_for("gone")).mock(return_value=httpx.Response(404))
    verified, dropped = await verify_all([make_trend("gone")])

    assert verified == []
    assert dropped[0].verified is False
    assert "404" in dropped[0].drop_reason


@respx.mock
async def test_403_is_dropped_as_private_or_blocked():
    respx.get(url_for("priv")).mock(return_value=httpx.Response(403))
    _, dropped = await verify_all([make_trend("priv")])
    assert "403" in dropped[0].drop_reason


@respx.mock
@pytest.mark.parametrize("marker", GONE_MARKERS)
async def test_soft_404_body_beats_a_200_status(marker):
    """TikTok returns 200 for removed videos, so the status alone is not enough."""
    body = f"<html><body><p>{marker.capitalize()}</p></body></html>"
    respx.get(url_for("soft")).mock(return_value=httpx.Response(200, text=body))

    verified, dropped = await verify_all([make_trend("soft")])

    assert verified == []
    assert dropped[0].verified is False
    assert marker in dropped[0].drop_reason


@respx.mock
async def test_soft_404_detection_is_case_insensitive():
    respx.get(url_for("soft")).mock(
        return_value=httpx.Response(200, text="<h1>VIDEO CURRENTLY UNAVAILABLE</h1>")
    )
    verified, dropped = await verify_all([make_trend("soft")])
    assert (verified, len(dropped)) == ([], 1)


@respx.mock
async def test_a_page_merely_mentioning_video_is_not_dropped():
    """The markers must be specific enough not to fire on ordinary page copy."""
    respx.get(url_for("ok")).mock(
        return_value=httpx.Response(200, text="This video is unavailable in 4K only")
    )
    verified, dropped = await verify_all([make_trend("ok")])
    assert len(verified) == 1 and dropped == []


@respx.mock
async def test_other_4xx_and_5xx_are_dropped_with_the_status():
    respx.get(url_for("teapot")).mock(return_value=httpx.Response(418))
    respx.get(url_for("boom")).mock(return_value=httpx.Response(503))

    verified, dropped = await verify_all([make_trend("teapot"), make_trend("boom")])

    assert verified == []
    assert {d.drop_reason for d in dropped} == {"HTTP 418", "HTTP 503"}


@respx.mock
async def test_a_transport_error_drops_rather_than_raises():
    """One dead host must not take the other nineteen verifications with it."""
    respx.get(url_for("dead")).mock(side_effect=httpx.ConnectError("no route"))
    respx.get(url_for("ok")).mock(return_value=httpx.Response(200, text=LIVE_HTML))

    verified, dropped = await verify_all([make_trend("dead"), make_trend("ok")])

    assert [t.trend_key for t in verified] == ["ok"]
    assert dropped[0].drop_reason.startswith("unreachable: ConnectError")


@respx.mock
async def test_redirect_to_a_live_page_still_verifies():
    respx.get(url_for("moved")).mock(
        return_value=httpx.Response(301, headers={"Location": "https://tiktok.com/x"})
    )
    respx.get("https://tiktok.com/x").mock(
        return_value=httpx.Response(200, text=LIVE_HTML)
    )
    verified, dropped = await verify_all([make_trend("moved")])
    assert len(verified) == 1 and dropped == []


@respx.mock
async def test_redirect_to_a_removal_page_is_dropped():
    """A dead video often 302s to a generic page, so follow then read the body."""
    respx.get(url_for("moved")).mock(
        return_value=httpx.Response(302, headers={"Location": "https://tiktok.com/x"})
    )
    respx.get("https://tiktok.com/x").mock(
        return_value=httpx.Response(200, text="this post is unavailable")
    )
    verified, dropped = await verify_all([make_trend("moved")])
    assert verified == [] and len(dropped) == 1


@respx.mock
async def test_a_mixed_batch_partitions_without_mixing_up_the_trends():
    good = [make_trend(f"ok{i}") for i in range(6)]
    bad = [make_trend(f"no{i}") for i in range(6)]
    for t in good:
        respx.get(t.source_url).mock(return_value=httpx.Response(200, text=LIVE_HTML))
    for t in bad:
        respx.get(t.source_url).mock(return_value=httpx.Response(404))

    # Interleaved, and more items than the concurrency cap, so an off-by-one in
    # the gather/zip pairing would attach a verdict to the wrong trend.
    mixed = [x for pair in zip(good, bad) for x in pair]
    verified, dropped = await verify_all(mixed, concurrency=3)

    assert sorted(t.trend_key for t in verified) == [f"ok{i}" for i in range(6)]
    assert sorted(t.trend_key for t in dropped) == [f"no{i}" for i in range(6)]


async def test_an_empty_batch_is_a_no_op():
    assert await verify_all([]) == ([], [])

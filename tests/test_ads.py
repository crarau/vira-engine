"""`POST /v1/ads/image` — the static ad, and the frame it is printed on.

The contract is `docs/IMAGE-API.md` §2. What these tests hold in place is the
part of it that is easy to lose in a refactor: that a static ad goes through the
same grounding and the same gate as a film rather than becoming a decorated
Gemini call, that a drop is a 409 carrying the ad, and that the frame Remotion
is told to grab is one where the caption has finished arriving.

That last one is not a style point. A spring at frame 0 has produced nothing, so
a still taken there is a successful render of an invisible headline — exit code
zero, right dimensions, no text. `test_the_still_frame_lands_after_the_spring`
is what stops it coming back.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from vira import adimage
from vira.api.app import app
from vira.api.routes import ads as ads_routes
from vira.api import imagelimit
from vira.models import Beat, Remix, Score
from vira.still import build_still_props, stressed_index
from tests.conftest import make_company, make_score, make_trend

HOOK = "I stopped buying breakfast for two YEARS"


def one_beat_remix(hook: str = HOOK) -> Remix:
    return Remix(
        hook=hook,
        beats=[Beat(say=hook, show="a jar on a dark counter",
                    shot="close, single warm lamp", motion="punch")],
        caption="We made the breakfast we could not find.",
        hashtags=["oats"],
        cta="Tap to try the first batch",
        why_this_works="withholds the result",
        grounded_in=["t1"],
    )


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test", timeout=30
    )


@pytest.fixture(autouse=True)
def fresh_rate_limit():
    """The burst window is process-wide, so it leaks between tests."""
    imagelimit.reset()
    yield
    imagelimit.reset()


# --- the printed frame ----------------------------------------------------


def test_the_stressed_word_is_the_one_the_hook_grammar_marked():
    """Rule 5 puts exactly one word in CAPS, on the stress. Nothing is guessed."""
    assert stressed_index(HOOK.split()) == 6
    assert HOOK.split()[6] == "YEARS"


def test_an_acronym_free_hook_falls_back_to_the_longest_word():
    assert stressed_index("i stopped buying breakfast".split()) == 3


def test_the_still_frame_lands_after_the_spring_and_inside_the_hold(cfg):
    props = build_still_props(make_company(), one_beat_remix(), image="a/shot00.jpg")
    words = props["beat"]["words"]
    stressed = words[stressed_index(HOOK.split())]
    frame = props["stillFrame"]

    assert frame >= stressed["startFrame"] + 12, "every entry spring must have settled"
    assert frame < stressed["endFrame"], "the word must still be the active one"
    # Captions.tsx picks the last word that has started, so nothing may have
    # begun between the stressed word and the frame we grab.
    assert [w for w in words if w["startFrame"] <= frame][-1] == stressed


def test_the_stressed_word_is_held_long_enough_to_win_the_emphasis_test(cfg):
    """Captions.tsx only draws the accent and the underline above 1.7x the median."""
    words = build_still_props(make_company(), one_beat_remix(), image=None)["beat"]["words"]
    held = sorted(w["endFrame"] - w["startFrame"] for w in words)
    median = held[len(held) // 2]
    longest = held[-1]
    assert longest >= median * 1.7


def test_the_composition_outlives_the_frame_it_is_asked_for(cfg):
    props = build_still_props(make_company(), one_beat_remix(), image=None)
    assert props["durationInFrames"] > props["stillFrame"]
    assert props["beat"]["endFrame"] - props["stillFrame"] > 4, "no exit fade on the still"


def test_the_writers_own_caption_treatment_is_carried_through(cfg):
    remix = one_beat_remix()
    remix.beats[0].motion = "stack"
    assert build_still_props(make_company(), remix, image=None)["beat"]["motion"] == "stack"


# --- the pipeline ---------------------------------------------------------


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Every network stage stubbed; the orchestration is what is under test."""
    state: dict[str, Any] = {
        "row": {"id": "lovable-id", "name": "Sunday Oats", "slug": "sunday-oats",
                "categories": {"name": "Food & Beverage"}},
        "shortlisted": [make_trend("t1"), make_trend("t2")],
        "score": make_score(relevance=5, specificity=5, actionability=5,
                            differentiation=5, evidence=5),
        "remix": one_beat_remix(),
        "grounded_via": [],
    }

    class FakeSupa:
        pass

    async def fake_get_company(_supa, slug):
        return dict(state["row"]) if state["row"] else None

    async def fake_shortlist(*_a, **_kw):
        state["grounded_via"].append("shortlist")
        return list(state["shortlisted"]), {}

    async def fake_refs(_supa, _brief):
        state["grounded_via"].append("brief references")
        return list(state["shortlisted"]), {}

    async def fake_verify(trends):
        return list(trends), []

    async def fake_analyze(*_a, **_kw):
        from vira.models import CorpusAnalysis
        return CorpusAnalysis(whitespace="nobody films the fridge")

    async def fake_build_remix(company, product, trends, corpus, plan=None):
        state["seen_plan"] = plan
        state["seen_mission"] = company.mission
        return state["remix"].model_copy(deep=True)

    async def fake_score(*_a, **_kw):
        return state["score"]

    async def fake_shots(_company, _product, _remix, dest, look=""):
        state["seen_look"] = look
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "shot00.jpg").write_bytes(b"jpeg")
        return [{"file": "shot00.jpg", "credit": "generated · Gemini",
                 "prompt": "a jar on a dark counter", "style_contract": "6am kitchen"}]

    def fake_render_still(props_path, out_file, **_kw):
        state["still_props"] = Path(props_path).read_text()
        out_file.write_bytes(b"jpeg")
        return out_file

    monkeypatch.setattr(adimage, "Supa", FakeSupa)
    monkeypatch.setattr(adimage, "get_company", fake_get_company)
    monkeypatch.setattr(adimage, "shortlist", fake_shortlist)
    monkeypatch.setattr(adimage, "resolve_trend_refs", fake_refs)
    monkeypatch.setattr(adimage, "verify_all", fake_verify)
    monkeypatch.setattr(adimage, "analyze_corpus", fake_analyze)
    monkeypatch.setattr(adimage, "build_remix", fake_build_remix)
    monkeypatch.setattr(adimage, "score_remix", fake_score)
    monkeypatch.setattr(adimage, "fetch_or_generate", fake_shots)
    monkeypatch.setattr(adimage, "render_still", fake_render_still)
    monkeypatch.setattr(adimage, "VIDEO_DIR", tmp_path / "video")
    state["root"] = tmp_path / "out"
    return state


async def produce(state, **kw):
    from vira.lanes import get as get_lane

    args = {
        "brand": "Sunday Oats",
        "product": "cocoa hazelnut overnight oats",
        "lane": get_lane("founder-story"),
        "out_root": state["root"],
        "out_dir": state["root"] / "sunday-oats" / "v001" / "founder-story",
    }
    args.update(kw)
    return await adimage.produce_ad_image(**args)


async def test_an_ad_is_grounded_verified_written_drawn_burned_and_scored(pipeline):
    ad = await produce(pipeline)
    assert pipeline["grounded_via"] == ["shortlist"]
    assert ad.headline == HOOK
    assert ad.disposition == "surfaced"
    assert ad.burned is True
    assert (pipeline["root"] / ad.ad_path).exists()
    assert (pipeline["root"] / ad.frame_path).exists()
    assert (pipeline["root"] / ad.recipe_path).exists(), "a recipe, same as a video"


async def test_the_recipe_carries_the_verbatim_prompts_like_a_videos_does(pipeline):
    ad = await produce(pipeline)
    recipe = (pipeline["root"] / ad.recipe_path).read_text()
    assert "Prompts, verbatim" in recipe
    assert "static-ad" in recipe


async def test_the_writer_is_asked_for_exactly_one_beat(pipeline):
    await produce(pipeline)
    assert pipeline["seen_plan"].beat_count == 1
    assert "PRINTED" in pipeline["seen_plan"].structure


async def test_the_photograph_is_told_to_leave_the_caption_band_alone(pipeline):
    await produce(pipeline)
    assert "LOWEST THIRD" in pipeline["seen_look"]


async def test_the_lane_picks_its_own_hook_grammar_when_the_caller_does_not(pipeline):
    ad = await produce(pipeline)
    assert ad.hook_shape == "first-person-admission"


async def test_a_supplied_headline_is_the_printed_line(pipeline):
    mine = "We tested this on FORTY people first"
    ad = await produce(pipeline, headline=mine)
    assert ad.headline == mine
    assert mine in pipeline["still_props"]
    assert pipeline["seen_plan"].hook_shape == "", "a fixed line takes no shape"


async def test_burn_text_off_returns_the_clean_frame_as_both_urls(pipeline):
    ad = await produce(pipeline, burn_text=False)
    assert ad.burned is False
    assert ad.ad_path == ad.frame_path
    assert "still_props" not in pipeline, "nothing should have gone to Remotion"


async def test_the_evidence_gate_drops_a_static_ad_the_same_way_it_drops_a_film(pipeline, cfg):
    pipeline["score"] = Score(relevance=5, specificity=5, actionability=5,
                              differentiation=5, evidence=2.0)
    ad = await produce(pipeline)
    assert ad.disposition == "dropped"
    assert ad.drop_reason == "not supported by the cited source videos"
    # The artefact still exists — the verdict is about the evidence, not the render.
    assert (pipeline["root"] / ad.ad_path).exists()


async def test_a_brief_grounds_on_its_own_references_instead_of_selection(pipeline):
    from vira.brief import Brief

    brief = Brief.model_validate({
        "brand": {"name": "Sunday Oats", "slug": "sunday-oats",
                  "neverSay": ["superfood"], "palette": ["#0B0B0F"]},
        "references": [{"trendKey": "t1", "whyItWorks": "withholds the result"}],
        "style": {"look": "cold kitchen at 6am"},
        "signalQuality": "low",
    })
    pipeline["score"] = make_score(evidence=3.5)
    ad = await produce(pipeline, brief=brief, brand="Sunday Oats")

    assert pipeline["grounded_via"] == ["brief references"]
    assert "cold kitchen at 6am" in pipeline["seen_look"]
    assert "#0B0B0F" in pipeline["seen_look"]
    assert 'never say "superfood"' in pipeline["seen_mission"]
    # signalQuality low costs a point of evidence, which drops it below the floor.
    assert ad.score.evidence == 2.5
    assert ad.disposition == "dropped"
    assert ad.confidence == "low"


async def test_an_unknown_brand_with_no_category_is_refused_rather_than_invented(pipeline):
    pipeline["row"] = None
    with pytest.raises(adimage.AdImageFailed, match="not in the corpus"):
        await produce(pipeline, brand="Nobody Has Heard Of This")


async def test_nothing_surviving_verification_stops_before_any_image_is_bought(pipeline, monkeypatch):
    async def kills_everything(_trends):
        return [], [make_trend("t1")]

    monkeypatch.setattr(adimage, "verify_all", kills_everything)
    with pytest.raises(adimage.AdImageFailed, match="no sources to ground on"):
        await produce(pipeline)


async def test_the_lane_is_stable_for_the_same_brand_and_product():
    """A recipe whose first decision is a coin toss cannot be re-run."""
    first = adimage.pick_lane("sunday-oats", "cocoa oats")
    assert adimage.pick_lane("sunday-oats", "cocoa oats").name == first.name
    names = {adimage.pick_lane(f"brand-{i}", "oats").name for i in range(40)}
    assert len(names) > 1, "and it must not always pick the same one"


# --- the route ------------------------------------------------------------


@pytest.fixture
def route(monkeypatch, tmp_path):
    """The route with the pipeline replaced, so only the HTTP contract is tested."""
    calls: list[dict] = []
    result: dict[str, Any] = {"disposition": "surfaced", "score": make_score()}

    def fake_out_dir(slug, lane, mode):
        return tmp_path / slug / lane

    async def fake_produce(**kw):
        calls.append(kw)
        return adimage.AdImage(
            ad_id="img_abc123",
            lane=kw["lane"].name,
            hook_shape="first-person-admission",
            ad_path="sunday-oats/v001/founder-story/ad.jpg",
            frame_path="sunday-oats/v001/founder-story/shot00.jpg",
            recipe_path="sunday-oats/v001/founder-story/RECIPE.md",
            headline=HOOK,
            cta="Tap to try the first batch",
            caption="a caption",
            hashtags=["oats"],
            score=result["score"],
            disposition=result["disposition"],
            drop_reason=("not supported by the cited source videos"
                         if result["disposition"] == "dropped" else None),
            confidence="high",
            grounded_in=[make_trend("t1", views=412000)],
            sources=[make_trend("t1", views=412000)],
            image_prompt="a jar on a dark counter",
            style_contract="6am kitchen",
            burned=True,
            elapsed_ms=34120,
        )

    monkeypatch.setattr(ads_routes, "produce_ad_image", fake_produce)
    monkeypatch.setattr(ads_routes.worker, "new_out_dir", fake_out_dir)
    return {"calls": calls, "result": result}


async def test_the_documented_simple_shape_returns_both_urls(client, route):
    async with client as c:
        r = await c.post("/v1/ads/image", json={
            "brand": "Sunday Oats", "product": "cocoa hazelnut overnight oats",
            "lane": "founder-story",
        })
    body = r.json()
    assert r.status_code == 200
    assert body["id"] == "img_abc123"
    assert body["url"].endswith("/media/sunday-oats/v001/founder-story/ad.jpg")
    assert body["image_url"].endswith("/media/sunday-oats/v001/founder-story/shot00.jpg")
    assert body["recipe_url"].endswith("/RECIPE.md")
    assert body["lane"] == "founder-story"
    assert body["hook_shape"] == "first-person-admission"
    assert body["grounded_in"][0]["views"] == 412000
    assert body["elapsed_ms"] == 34120


async def test_the_engine_picks_a_lane_when_the_caller_omits_one(client, route):
    async with client as c:
        r = await c.post("/v1/ads/image", json={"brand": "Sunday Oats", "product": "oats"})
    assert r.status_code == 200
    assert route["calls"][0]["lane"].name in {
        "problem-first", "demo-first", "founder-story", "social-proof", "contrarian"
    }


async def test_an_unknown_lane_is_a_422(client, route):
    async with client as c:
        r = await c.post("/v1/ads/image", json={
            "brand": "Sunday Oats", "product": "oats", "lane": "cinematic"})
    assert r.status_code == 422


async def test_a_supplied_headline_that_breaks_the_hook_rules_names_the_rule(client, route):
    async with client as c:
        r = await c.post("/v1/ads/image", json={
            "brand": "Sunday Oats", "product": "oats",
            "headline": "Ten seconds. That's it.",
        })
    detail = r.json()["detail"]
    assert r.status_code == 422
    assert detail["broken_rules"], "a caller who wrote the line deserves the reason"
    assert route["calls"] == [], "nothing was spent on a hook we know under-performs"


async def test_a_supplied_headline_that_obeys_the_rules_is_passed_through(client, route):
    async with client as c:
        r = await c.post("/v1/ads/image", json={
            "brand": "Sunday Oats", "product": "oats", "headline": HOOK})
    assert r.status_code == 200
    assert route["calls"][0]["headline"] == HOOK


async def test_a_dropped_ad_is_a_409_carrying_the_attempt(client, route):
    route["result"]["disposition"] = "dropped"
    async with client as c:
        r = await c.post("/v1/ads/image", json={"brand": "Sunday Oats", "product": "oats"})
    body = r.json()
    assert r.status_code == 409
    assert body["drop_reason"] == "not supported by the cited source videos"
    assert body["url"].endswith("ad.jpg"), "the best attempt comes back with the refusal"
    assert body["headline"] == HOOK


async def test_burning_into_a_ratio_the_caption_band_was_not_built_for_is_refused(client, route):
    async with client as c:
        r = await c.post("/v1/ads/image", json={
            "brand": "Sunday Oats", "product": "oats", "aspect_ratio": "1:1"})
    assert r.status_code == 422
    assert "burn_text: false" in r.json()["detail"]


async def test_a_clean_frame_may_be_any_ratio_the_proxy_accepts(client, route):
    async with client as c:
        r = await c.post("/v1/ads/image", json={
            "brand": "Sunday Oats", "product": "oats",
            "aspect_ratio": "1:1", "burn_text": False})
    assert r.status_code == 200
    assert route["calls"][0]["aspect_ratio"] == "1:1"


async def test_a_full_brief_posted_at_the_top_level_is_accepted_here_too(client, route):
    async with client as c:
        r = await c.post("/v1/ads/image", json={
            "durationSeconds": 8,
            "brand": {"name": "Sunday Oats", "slug": "sunday-oats",
                      "neverSay": ["superfood"]},
            "references": [{"trendKey": "VIRA-TR-1"}],
            "style": {"look": "cold kitchen"},
            "product": "cocoa oats",
        })
    assert r.status_code == 200
    brief = route["calls"][0]["brief"]
    assert brief is not None
    assert brief.brand.never_say == ["superfood"]
    assert [r.trend_key for r in brief.trend_refs] == ["VIRA-TR-1"]


async def test_the_rate_limit_still_applies_after_the_proxy_was_removed(client, route, monkeypatch):
    """The limiter used to live in the proxy module. Removing that route must
    not remove the ceiling from the endpoint that actually spends money."""
    monkeypatch.setattr(imagelimit, "BURST_PER_MINUTE", 1)
    async with client as c:
        first = await c.post("/v1/ads/image", json={"brand": "Sunday Oats", "product": "oats"})
        second = await c.post("/v1/ads/image", json={"brand": "Sunday Oats", "product": "oats"})
    assert first.status_code == 200
    assert second.status_code == 429, "the ceiling went with the proxy"

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from conftest import run_input_data
from productfoundry.core.competitors import CompetitorList
from productfoundry.core.errors import QuotaExhausted
from productfoundry.core.ids import review_id
from productfoundry.core.reviews import Review, ReviewSet
from productfoundry.core.run_input import RunInput
from productfoundry.llm import Gateway, LlmFailed, ProviderError, ProviderResponse, load_routing
from productfoundry.llm.fakes import FakeProvider, InMemoryCallStore, InMemoryUsageStore
from productfoundry.orchestrator import Services
from productfoundry.sources import RawReview, SourceError
from productfoundry.sources.app_store.reviews import AppStoreReviews
from productfoundry.sources.google_play.reviews import GooglePlayReviews
from productfoundry.stages.s2_reviews import ReviewSettings, ReviewStage
from productfoundry.stages.s2_reviews.cleaning import clean_text, dedup_key, drop_reason
from productfoundry.stages.s2_reviews.language import Detection, detect_language
from productfoundry.stages.s2_reviews.sentiment import batch_schema, batches, label_batch
from productfoundry.storage.memory import InMemoryReviewStore

FIXTURES = Path(__file__).parent / "fixtures"
PLAY = json.loads((FIXTURES / "sources/google_play_reviews.json").read_text("utf-8"))["reviews"]
APPLE = json.loads((FIXTURES / "sources/app_store_reviews.json").read_text("utf-8"))
LANGUAGE = json.loads((FIXTURES / "language_reviews.json").read_text("utf-8"))
USERNAMES = [item["userName"] for item in PLAY] + [
    entry["author"]["name"]["label"] for entry in APPLE["feed"]["entry"]
]
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def play_page(items=PLAY):
    """The library's shape: `at` is a naive local datetime."""
    return [item | {"at": datetime.fromisoformat(item["at"]), "repliedAt": None} for item in items]


# Cleaning


def test_cleaning_strips_markup_whitespace_and_profile_links():
    assert clean_text("Money got deducted   twice.<br>Fix   this!\n") == (
        "Money got deducted twice. Fix this!"
    )
    assert clean_text("Tom &amp; I can&#39;t log in") == "Tom & I can't log in"
    assert clean_text("see https://play.google.com/store/people/details?id=1 for proof") == (
        "see for proof"
    )
    assert clean_text("docs at https://example.com/help are wrong") == (
        "docs at https://example.com/help are wrong"
    )
    assert clean_text(None) == ""


@pytest.mark.parametrize(
    "text",
    [
        "app bahut slow hai, payment fail ho jata hai",
        "paisa kat gya refund nhi aaya plz help kro",
        "Bhot bekaar app h yrr, baar baar band ho jata h",
    ],
)
def test_cleaning_leaves_hinglish_exactly_as_written(text):
    assert clean_text(text) == text


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "empty"),
        ("👍👍", "empty"),
        ("Good", "too_short"),
        ("bekar app", "too_short"),
        ("kaam nahi karta", None),
        ("यह ऐप बहुत धीमा है", None),
    ],
)
def test_empty_and_very_short_reviews_are_dropped(text, reason):
    assert drop_reason(text, min_words=3) == reason


def test_the_dedup_key_ignores_case_punctuation_and_spacing():
    first = dedup_key("app bahut slow hai, payment fail ho jata hai")
    assert first == dedup_key("APP BAHUT SLOW HAI... payment fail ho jata hai!!")
    assert first != dedup_key("app bahut fast hai, payment fail ho jata hai")


# Language


def group(detection: Detection) -> str:
    return detection.language if detection.language in ("en", "hinglish", "hi") else "other"


def test_language_detection_on_the_hand_written_fixture():
    assert len(LANGUAGE) >= 60
    assert {row["expected"] for row in LANGUAGE} == {"en", "hinglish", "hi", "other"}
    detections = [(row, detect_language(row["text"])) for row in LANGUAGE]
    decided = [(row, d) for row, d in detections if not d.borderline]
    correct = [row for row, d in decided if group(d) == row["expected"]]

    # 2026-10-04: 72 of 74 decided and all 72 correct; 2 borderline go to the LLM.
    assert len(correct) / len(decided) >= 0.95
    assert len(detections) - len(decided) <= 0.1 * len(detections)


def test_hinglish_is_never_labelled_as_an_unrelated_language():
    for row in LANGUAGE:
        if row["expected"] == "hinglish":
            assert detect_language(row["text"]).language == "hinglish", row["text"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("यह ऐप बहुत धीमा है", Detection("hi")),
        ("बेकार app है, बार बार crash होता है", Detection("hi")),
        ("worst app ever", Detection("en")),
        ("bhai ye app kaam nahi kar raha", Detection("hinglish")),
        ("Paisa refund please", Detection("hinglish", borderline=True)),
        ("123 !!!", Detection("und")),
    ],
)
def test_language_cases(text, expected):
    assert detect_language(text) == expected


def test_language_detection_is_repeatable():
    text = "Aplikasi ini sangat membantu untuk membagi tagihan bersama teman kos saya."
    assert {detect_language(text) for _ in range(5)} == {Detection("id")}


# Google Play adapter


class PlayPages:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, app_id, lang, country, count, token):
        self.calls.append((app_id, lang, country, count, token))
        index = token or 0
        page = self.pages[index][:count] if index < len(self.pages) else []
        return page, (index + 1 if index + 1 < len(self.pages) else None)


def test_google_play_reviews_are_paged_paused_and_stripped_of_the_author():
    pages = PlayPages([play_page(PLAY[:5]), play_page(PLAY[5:])])
    sleeps = []
    source = GooglePlayReviews(pages, languages=["en"], pause_seconds=1.5, sleep=sleeps.append)
    reviews = source.fetch("com.example.app", "IN", since=None, limit=200)

    assert len(reviews) == 12 and source.requests == 2
    assert sleeps == [1.5]
    assert pages.calls[0] == ("com.example.app", "en", "in", 100, None)
    first = reviews[0]
    assert isinstance(first, RawReview)
    assert first.source_review_id == "gp-0001" and first.rating == 1
    assert first.url == (
        "https://play.google.com/store/apps/details?id=com.example.app&reviewId=gp-0001"
    )
    assert first.reviewed_at.utcoffset() == timedelta(0)
    assert first.reviewed_at == datetime.fromisoformat(PLAY[0]["at"]).astimezone(UTC)
    assert not any(name in repr(reviews) for name in USERNAMES)
    assert "googleusercontent" not in repr(reviews)


def test_google_play_reads_each_configured_language_and_respects_the_cap():
    pages = PlayPages([play_page()])
    source = GooglePlayReviews(pages, languages=["en", "hi"], sleep=lambda _: None)
    reviews = source.fetch("com.example.app", "IN", since=None, limit=8)
    assert [call[1] for call in pages.calls] == ["en", "hi"]
    assert [call[3] for call in pages.calls] == [4, 4]
    assert len(reviews) == 4  # the same four reviews under both languages, stored once


def test_google_play_incremental_fetch_stops_at_the_latest_stored_review():
    pages = PlayPages([play_page(PLAY[:5]), play_page(PLAY[5:])])
    source = GooglePlayReviews(pages, languages=["en"], sleep=lambda _: None)
    since = datetime.fromisoformat(PLAY[2]["at"]).astimezone(UTC)
    reviews = source.fetch("com.example.app", "IN", since=since, limit=200)
    assert [review.source_review_id for review in reviews] == ["gp-0001", "gp-0002"]
    assert source.requests == 1


def test_google_play_failures_are_source_errors():
    def broken(*args):
        raise RuntimeError("layout changed")

    with pytest.raises(SourceError, match="RuntimeError"):
        GooglePlayReviews(broken).fetch("com.example.app", "IN", since=None, limit=10)


# App Store adapter


def apple(pages, **kwargs):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        page = int(str(request.url).split("page=")[1].split("/")[0])
        return httpx.Response(200, json=pages.get(page, {"feed": {}}))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AppStoreReviews(client, sleep=lambda _: None, **kwargs), seen


def test_app_store_reviews_come_from_the_feed_without_the_author():
    source, seen = apple({1: APPLE})
    reviews = source.fetch("458023433", "IN", since=None, limit=200)

    assert seen[0] == (
        "https://itunes.apple.com/in/rss/customerreviews/page=1/id=458023433/sortby=mostrecent/json"
    )
    assert len(seen) == 2  # the empty second page ends the fetch
    assert [review.source_review_id for review in reviews] == [
        "as-9001", "as-9002", "as-9003", "as-9004",
    ]  # fmt: skip
    first = reviews[0]
    assert first.text.startswith("Daily limit ruined it. I used to love this app")
    assert reviews[1].text == "Best app for trips with friends, the balances are always correct."
    assert first.rating == 1
    assert first.reviewed_at == datetime(2026, 10, 3, 14, 17, 38, tzinfo=UTC)
    assert first.url == "https://apps.apple.com/in/app/id458023433?see-all=reviews"
    assert not any(name in repr(reviews) for name in USERNAMES)
    assert "itunes.apple.com/in/reviews" not in repr(reviews)


def test_app_store_incremental_fetch_and_cap():
    source, seen = apple({1: APPLE, 2: APPLE})
    since = datetime(2026, 10, 1, tzinfo=UTC)
    reviews = source.fetch("458023433", "IN", since=since, limit=200)
    assert [review.source_review_id for review in reviews] == ["as-9001", "as-9002"]
    assert len(seen) == 1

    source, seen = apple({1: APPLE, 2: APPLE})
    assert len(source.fetch("458023433", "IN", since=None, limit=3)) == 3
    assert len(seen) == 1


def test_app_store_handles_a_single_entry_and_errors():
    single = {"feed": {"entry": APPLE["feed"]["entry"][0]}}
    source, _ = apple({1: single})
    assert len(source.fetch("458023433", "IN", since=None, limit=10)) == 1

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403)))
    with pytest.raises(SourceError, match="403"):
        AppStoreReviews(client).fetch("458023433", "IN", since=None, limit=10)


# Sentiment batching


class Labeller:
    """A provider that labels each review in the request by simple rules."""

    def __init__(self, fail_after: int | None = None):
        self.batches: list[list[str]] = []
        self.fail_after = fail_after

    def complete(self, request):
        if self.fail_after is not None and len(self.batches) >= self.fail_after:
            raise ProviderError("rate_limit", "HTTP 429")
        reviews = json.loads(request.messages[1]["content"])
        self.batches.append([review["text"] for review in reviews])
        labels = [
            {
                "n": review["n"],
                "sentiment": sentiment(review["text"]),
                "language": lang(review["text"]),
            }
            for review in reviews
        ]
        return ProviderResponse(json.dumps({"labels": labels}), 100, 50)


def sentiment(text: str) -> str:
    lowered = text.lower()
    if " but " in lowered:
        return "mixed"
    if any(word in lowered for word in ("fail", "deducted", "limit", "nahi", "refund")):
        return "negative"
    return "positive"


def lang(text: str) -> str:
    return "hinglish" if any(w in text.lower().split() for w in ("hai", "paisa", "nahi")) else "en"


def gateway(**providers) -> Gateway:
    return Gateway(
        load_routing(),
        providers,
        InMemoryCallStore(),
        InMemoryUsageStore(),
        clock=lambda: NOW,
        sleep=lambda _: None,
    )


def review(n: int, text: str = "The app crashes whenever I open it") -> Review:
    return Review(
        id=review_id("google_play", f"r{n}"),
        product_id="prod_a",
        source="google_play",
        source_review_id=f"r{n}",
        reviewed_at=NOW - timedelta(days=n),
        language="en",
        text=f"{text} ({n})",
    )


@pytest.mark.parametrize(("count", "size"), [(95, 40), (40, 40), (41, 40), (7, 3), (1, 50)])
def test_n_reviews_make_ceil_n_over_batch_calls(count, size):
    labeller = Labeller()
    llm = gateway(groq=labeller)
    reviews = [review(n) for n in range(count)]
    labelled = [label for batch in batches(reviews, size) for label in label_batch(llm, batch)]
    assert len(labeller.batches) == math.ceil(count / size)
    assert len(labelled) == count
    assert max(len(batch) for batch in labeller.batches) <= size


def test_a_batch_with_a_malformed_answer_is_retried_not_silently_dropped():
    short = {"labels": [{"n": 1, "sentiment": "negative", "language": "en"}]}
    full = {"labels": [{"n": n, "sentiment": "negative", "language": "en"} for n in (2, 1, 3)]}
    provider = FakeProvider(short, "not json", full)
    fallback = FakeProvider(full)
    llm = gateway(groq=provider, gemini=fallback)

    labels = label_batch(llm, [review(n) for n in range(3)])
    assert [label.n for label in labels] == [1, 2, 3]
    assert len(provider.requests) == 2 and len(fallback.requests) == 1


def test_a_batch_no_provider_can_label_is_an_error():
    repeated = {"labels": [{"n": 1, "sentiment": "negative", "language": "en"}] * 2}
    llm = gateway(groq=FakeProvider(repeated))
    with pytest.raises(LlmFailed, match="schema_invalid"):
        label_batch(llm, [review(1), review(2)])


def test_the_batch_schema_wants_each_review_once():
    schema = batch_schema(2)
    label = {"sentiment": "mixed", "language": "hinglish"}
    schema.model_validate({"labels": [{"n": 2, **label}, {"n": 1, **label}]})
    for numbers in ([1], [1, 1], [1, 2, 3], [0, 1]):
        with pytest.raises(ValueError, match="one label for each"):
            schema.model_validate({"labels": [{"n": n, **label} for n in numbers]})


def test_the_recorded_sentiment_fixture_includes_hinglish_and_replays():
    fixture = json.loads((FIXTURES / "llm/review_sentiment_batch.json").read_text("utf-8"))
    texts = fixture["reviews"]
    assert sum(detect_language(text).language == "hinglish" for text in texts) >= 3
    llm = gateway(groq=FakeProvider(fixture["answer"]))
    labels = label_batch(llm, [review(n, text) for n, text in enumerate(texts)])
    assert [label.sentiment for label in labels] == fixture["expected_sentiments"]


def test_the_prompt_says_reviews_may_be_hinglish():
    provider = FakeProvider({"labels": [{"n": 1, "sentiment": "negative", "language": "en"}]})
    label_batch(gateway(groq=provider), [review(1)])
    request = provider.requests[0]
    assert request.task == "review_sentiment"
    assert "Hinglish" in request.messages[0]["content"]
    assert json.loads(request.messages[1]["content"]) == [{"n": 1, "text": review(1).text}]


# The stage


class FakeSource:
    def __init__(self, reviews: list[RawReview]):
        self.reviews = reviews
        self.calls = []

    def fetch(self, store_id, region, *, since, limit):
        self.calls.append((store_id, region, since, limit))
        fresh = [r for r in self.reviews if since is None or r.reviewed_at > since]
        return fresh[:limit]


def raw_play() -> list[RawReview]:
    pages = PlayPages([play_page()])
    source = GooglePlayReviews(pages, languages=["en"], sleep=lambda _: None)
    return source.fetch("com.Splitwise.SplitwiseMobile", "IN", since=None, limit=200)


def raw_apple() -> list[RawReview]:
    return apple({1: APPLE})[0].fetch("458023433", "IN", since=None, limit=200)


def competitors(**store_ids) -> CompetitorList:
    store_ids = store_ids or {
        "google_play": "com.Splitwise.SplitwiseMobile",
        "app_store": "458023433",
    }
    return CompetitorList.model_validate(
        {
            "competitors": [
                {
                    "id": "prod_splitwise",
                    "name": "Splitwise",
                    "url": "https://www.splitwise.com",
                    "positioning": "Bill splitting.",
                    "target_users": "Flatmates",
                    "store_ids": store_ids,
                    "reason": "Incumbent.",
                    "is_incumbent": True,
                }
            ]
        }
    )


class World:
    def __init__(self, labeller=None, settings=None, **competitor_ids):
        self.labeller = labeller or Labeller()
        self.store = InMemoryReviewStore()
        self.play, self.apple = FakeSource(raw_play()), FakeSource(raw_apple())
        self.services = Services(
            llm=gateway(groq=self.labeller),
            reviews=self.store,
            review_sources={"google_play": self.play, "app_store": self.apple},
        )
        self.stage = ReviewStage(settings or ReviewSettings(sentiment_batch_size=3))
        self.competitors = competitors(**competitor_ids)
        self.run_input = RunInput.model_validate(run_input_data())

    def run(self) -> ReviewSet:
        return self.stage(self.run_input, {"s1_competitors": self.competitors}, self.services)

    def stored(self) -> list[Review]:
        return self.store.for_product("prod_splitwise")


def test_the_stage_counts_what_it_stored_and_what_it_dropped():
    world = World()
    output = world.run()

    assert ReviewSet.model_validate(output.model_dump(mode="json")) == output
    counts = output.products[0]
    assert counts.product_id == "prod_splitwise"
    assert counts.total == 10 == output.total_reviews
    assert counts.by_source == {"google_play": 8, "app_store": 2}
    assert counts.by_language == {"hinglish": 4, "en": 4, "hi": 1, "id": 1}
    assert counts.not_analysed == 2
    assert counts.by_sentiment == {"negative": 5, "positive": 2, "mixed": 1}
    assert output.dropped == {"too_short": 3, "duplicate": 2, "empty": 1}


def test_no_username_reaches_the_repository():
    world = World()
    world.run()
    stored = json.dumps([r.model_dump(mode="json") for r in world.stored()], ensure_ascii=False)
    for name in USERNAMES:
        assert name not in stored
    assert "store/people" not in stored and "googleusercontent" not in stored
    assert "username" not in Review.model_fields and "author" not in Review.model_fields


def test_stored_text_is_cleaned_but_hinglish_is_untouched():
    world = World()
    world.run()
    texts = {r.source_review_id: r.text for r in world.stored()}
    assert texts["gp-0001"] == "app bahut slow hai, payment fail ho jata hai"
    assert texts["gp-0002"] == (
        "Money got deducted twice and support has not replied in a week. Fix this!"
    )
    assert texts["gp-0010"] == "Refund ka paisa abhi tak nahi aaya, see my profile for proof"
    assert "gp-0007" not in texts and "as-9003" not in texts  # duplicates of gp-0001


def test_only_analysed_languages_go_to_the_llm_and_borderline_ones_are_settled_by_it():
    world = World()
    world.run()
    sent = [text for batch in world.labeller.batches for text in batch]
    assert len(sent) == 8 and len(world.labeller.batches) == 3
    assert not any("यह" in text or "Aplikasi" in text for text in sent)

    by_id = {r.source_review_id: r for r in world.stored()}
    assert (by_id["gp-0012"].language, by_id["gp-0012"].sentiment) == ("hinglish", "negative")
    assert (by_id["gp-0005"].language, by_id["gp-0005"].sentiment) == ("hi", None)
    assert (by_id["gp-0011"].language, by_id["gp-0011"].sentiment) == ("id", None)
    assert by_id["gp-0001"].rating == 1 and by_id["gp-0001"].sentiment == "negative"


def test_a_borderline_review_the_llm_calls_another_language_is_flagged_not_analysed():
    class OtherLanguage(Labeller):
        def complete(self, request):
            response = json.loads(super().complete(request).text)
            for label, text in zip(response["labels"], self.batches[-1], strict=True):
                if text == "Paisa refund please":
                    label["language"] = "other"
            return ProviderResponse(json.dumps(response), 100, 50)

    world = World(labeller=OtherLanguage())
    counts = world.run().products[0]
    borderline = next(r for r in world.stored() if r.source_review_id == "gp-0012")
    assert (borderline.language, borderline.sentiment) == ("und", None)
    assert counts.not_analysed == 3 and counts.by_language["und"] == 1


def test_a_second_run_fetches_only_newer_reviews_and_labels_only_those():
    world = World()
    world.run()
    calls_before = len(world.labeller.batches)
    newest = max(r.reviewed_at for r in world.stored() if r.source == "google_play")

    world.play.reviews.insert(
        0,
        RawReview("gp-0099", "Naya update bahut accha hai, sab theek ho gaya", NOW, 5, None),
    )
    output = world.run()

    assert world.play.calls[-1][2] == newest
    assert world.apple.calls[-1][2] is not None
    assert output.products[0].total == 11
    assert len(world.labeller.batches) == calls_before + 1
    assert world.labeller.batches[-1] == ["Naya update bahut accha hai, sab theek ho gaya"]
    assert output.dropped == {}


def test_the_cap_and_store_ids_decide_what_is_fetched():
    world = World(settings=ReviewSettings(cap_per_store=5), google_play="com.example.only")
    output = world.run()
    assert world.play.calls == [("com.example.only", "IN", None, 5)]
    assert world.apple.calls == []
    assert set(output.products[0].by_source) == {"google_play"}


def test_a_quota_pause_keeps_finished_batches_and_the_next_run_does_the_rest():
    world = World(labeller=Labeller(fail_after=2))
    with pytest.raises(QuotaExhausted):
        world.run()
    assert sum(r.sentiment is not None for r in world.stored()) == 6

    world.labeller.fail_after = None
    output = world.run()
    assert len(world.labeller.batches) == 3
    assert sum(output.products[0].by_sentiment.values()) == 8


def test_two_competitors_with_the_same_store_app_share_their_reviews():
    world = World()
    twin = world.competitors.competitors[0].model_copy(
        update={"id": "prod_twin", "name": "Splitwise India", "is_incumbent": False}
    )
    world.competitors = CompetitorList(competitors=[*world.competitors.competitors, twin])
    output = world.run()
    assert [p.product_id for p in output.products] == ["prod_splitwise", "prod_twin"]
    assert output.products[0].total == output.products[1].total == 10
    assert len(world.labeller.batches) == 3

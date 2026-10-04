import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from conftest import run_input_data
from productfoundry.core.ids import review_id
from productfoundry.core.reviews import Review
from productfoundry.storage import ReviewRepository, StorageUnavailable, check_ready, make_engine
from productfoundry.storage.models import Base, ProductRow, ReviewRow

DAY = datetime(2026, 9, 1, tzinfo=UTC)


def review(n: int, **overrides) -> Review:
    data = {
        "id": review_id("google_play", f"gp-{n}"),
        "product_id": "prod_a",
        "source": "google_play",
        "source_review_id": f"gp-{n}",
        "url": f"https://play.google.com/store/apps/details?id=com.example&reviewId=gp-{n}",
        "reviewed_at": DAY + timedelta(days=n),
        "rating": 2,
        "language": "hinglish",
        "sentiment": "negative",
        "text": "app bahut slow hai, payment fail ho jata hai",
    }
    data.update(overrides)
    return Review.model_validate(data)


@pytest.fixture
def reviews(sessions) -> ReviewRepository:
    with sessions.begin() as session:
        session.add_all(
            [ProductRow(id="prod_a", name="Walnut"), ProductRow(id="prod_b", name="Rival")]
        )
    return ReviewRepository(sessions)


def count_rows(sessions) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(ReviewRow))


# Schema


def test_the_migration_builds_exactly_the_tables_the_models_describe(db_engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with db_engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_reviews_have_no_place_for_a_username():
    columns = set(ReviewRow.__table__.columns.keys())
    assert columns == {
        "id", "product_id", "source", "source_review_id", "url", "reviewed_at", "rating",
        "language", "sentiment", "text", "embedding", "fetched_at",
    }  # fmt: skip
    with pytest.raises(ValidationError, match="username"):
        Review.model_validate(review(1).model_dump() | {"username": "someone"})


def test_review_ids_are_stable():
    assert review_id("google_play", "gp-1") == review_id("google_play", "gp-1")
    assert review_id("google_play", "gp-1") != review_id("app_store", "gp-1")


# ReviewRepository


def test_inserting_the_same_review_twice_leaves_one_row(reviews, sessions):
    assert reviews.upsert([review(1)]) == 1
    assert reviews.upsert([review(1)]) == 0
    assert reviews.upsert([review(1), review(1)]) == 0
    assert count_rows(sessions) == 1
    assert reviews.for_product("prod_a") == [review(1)]


def test_a_review_seen_again_is_refreshed_but_keeps_its_analysis(reviews, sessions):
    reviews.upsert([review(1)])
    again = review(1, text="Edited: still slow", rating=1, language=None, sentiment=None)
    assert reviews.upsert([again, review(2)]) == 1

    stored = reviews.for_product("prod_a")
    assert [r.source_review_id for r in stored] == ["gp-1", "gp-2"]
    assert stored[0].text == "Edited: still slow"
    assert stored[0].rating == 1
    assert (stored[0].language, stored[0].sentiment) == ("hinglish", "negative")


def test_the_same_source_id_from_another_source_is_a_different_review(reviews, sessions):
    other = review(1, id=review_id("app_store", "gp-1"), source="app_store")
    assert reviews.upsert([review(1), other]) == 2
    assert count_rows(sessions) == 2


def test_fetch_by_product_and_newer_than_a_date(reviews):
    other_product = review(9, product_id="prod_b")
    app_store = review(4, id=review_id("app_store", "as-4"), source="app_store",
                       source_review_id="as-4")  # fmt: skip
    reviews.upsert([review(3), review(1), review(2), other_product, app_store])

    assert [r.source_review_id for r in reviews.for_product("prod_a")] == [
        "gp-1", "gp-2", "gp-3", "as-4",
    ]  # fmt: skip
    assert reviews.for_product("prod_b") == [other_product]

    newer = reviews.newer_than("prod_a", DAY + timedelta(days=2))
    assert [r.source_review_id for r in newer] == ["gp-3", "as-4"]
    newer = reviews.newer_than("prod_a", DAY + timedelta(days=2), source="google_play")
    assert [r.source_review_id for r in newer] == ["gp-3"]
    assert reviews.newer_than("prod_a", DAY + timedelta(days=30)) == []
    assert reviews.upsert([]) == 0


# Availability


def test_an_unreachable_database_is_reported_without_the_password():
    url = make_engine().url.set(host="127.0.0.1", port=1, password="not-for-printing")
    engine = make_engine(url, connect_timeout=1)
    with pytest.raises(StorageUnavailable) as error:
        check_ready(engine)
    assert "docker compose up -d db" in str(error.value)
    assert "not-for-printing" not in str(error.value)


# Two processes


def cli(db_engine, tmp_path, *args) -> str:
    env = os.environ | {"DATABASE_URL": db_engine.url.render_as_string(hide_password=False)}
    result = subprocess.run(
        [sys.executable, "-m", "productfoundry.cli", *args],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_a_run_paused_at_a_checkpoint_is_resumed_by_a_second_process(sessions, db_engine, tmp_path):
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps(run_input_data()), encoding="utf-8")

    first = cli(db_engine, tmp_path, "run", "--input", str(input_file))
    assert "status  awaiting_approval" in first
    run_id = first.split()[1]

    second = cli(db_engine, tmp_path, "approve", run_id)
    assert "s2_reviews       completed" in second
    assert "s3_pain_points   awaiting_approval" in second

    assert run_id in cli(db_engine, tmp_path, "status")
    assert not (tmp_path / ".productfoundry").exists()

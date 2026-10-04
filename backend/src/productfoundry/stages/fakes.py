"""Stand-in stages with canned, schema-valid outputs. Real stages replace them one by one."""

from collections.abc import Mapping

from pydantic import BaseModel

from productfoundry.core.competitors import Competitor, CompetitorList
from productfoundry.core.pain_points import (
    RANKING_FORMULA,
    LanguageCounts,
    PainPoint,
    PainPointReport,
    pain_point_score,
)
from productfoundry.core.reviews import ProductReviewCounts, ReviewSet
from productfoundry.core.run_input import RunInput, StoreIds
from productfoundry.orchestrator.protocols import Services, Stage


def fake_competitors(
    run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
) -> CompetitorList:
    incumbent = run_input.incumbent
    competitors = []
    if incumbent is not None:
        competitors.append(
            Competitor(
                id="prod_fake0",
                name=incumbent.name,
                url=incumbent.urls[0] if incumbent.urls else "https://incumbent.example",
                positioning="The product users already know.",
                target_users=run_input.target_users,
                store_ids=incumbent.store_ids,
                reason="The incumbent named in the run input.",
                is_incumbent=True,
            )
        )
    competitors.append(
        Competitor(
            id="prod_fake1",
            name="Fake Rival",
            url="https://rival.example",
            positioning="A cheaper tool for the same job.",
            target_users=run_input.target_users,
            store_ids=StoreIds(),
            reason="Solves the same problem for the same users.",
        )
    )
    return CompetitorList(competitors=competitors)


def fake_reviews(
    run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
) -> ReviewSet:
    competitors: CompetitorList = earlier_outputs["s1_competitors"]
    return ReviewSet(
        products=[
            ProductReviewCounts(
                product_id=competitor.id,
                total=10,
                by_source={"google_play": 6, "app_store": 4},
                by_language={"en": 6, "hinglish": 3, "hi": 1},
                by_sentiment={"positive": 3, "neutral": 1, "mixed": 1, "negative": 4},
                not_analysed=1,
            )
            for competitor in competitors.competitors
        ],
        dropped={"empty_or_too_short": 2, "duplicate": 1},
    )


def fake_pain_points(
    run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
) -> PainPointReport:
    reviews: ReviewSet = earlier_outputs["s2_reviews"]
    products = len(reviews.products)
    return PainPointReport(
        pain_points=[
            PainPoint(
                cluster_id="cl_fake0",
                rank=1,
                label="Payments fail",
                description="Payments fail or stay pending after money is debited.",
                severity=5,
                severity_reason="Users lose money and cannot finish the main task.",
                review_count=4 * products,
                negative_share=0.8,
                product_ids=[product.product_id for product in reviews.products],
                quote_review_ids=["rev_fake0", "rev_fake1", "rev_fake2"],
                score=pain_point_score(4 * products, 0.8, 5),
            )
        ],
        ranking_formula=RANKING_FORMULA,
        language_counts=LanguageCounts(
            english=6 * products, hinglish=3 * products, not_analysed=products
        ),
        clustered_reviews=5 * products,
        noise_reviews=products,
    )


FAKE_STAGES: dict[str, Stage] = {
    "s1_competitors": fake_competitors,
    "s2_reviews": fake_reviews,
    "s3_pain_points": fake_pain_points,
}

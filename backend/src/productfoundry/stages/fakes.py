"""Stand-in stages with canned, schema-valid outputs. Real stages replace them one by one."""

from collections.abc import Mapping

from pydantic import BaseModel

from productfoundry.core.acceptance import AcceptanceCriteria, Criterion, TaskCriteria
from productfoundry.core.competitors import Competitor, CompetitorList
from productfoundry.core.pain_points import (
    RANKING_FORMULA,
    LanguageCounts,
    PainPoint,
    PainPointReport,
    pain_point_score,
)
from productfoundry.core.prd import Prd, Requirement, market_gaps
from productfoundry.core.reviews import ProductReviewCounts, ReviewSet
from productfoundry.core.run_input import RunInput, StoreIds
from productfoundry.core.tasks import Epic, Task, TaskPlan
from productfoundry.orchestrator.protocols import Services, Stage
from productfoundry.stages.s6_roadmap import roadmap_stage


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


def fake_prd(
    run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
) -> Prd:
    report: PainPointReport = earlier_outputs["s3_pain_points"]
    gap = market_gaps(earlier_outputs["s1_competitors"])[-1]
    requirements = [
        Requirement(
            id=f"req_{number:03d}",
            statement=f"The product must fix: {point.label}.",
            priority="must",
            evidence=[point.cluster_id],
        )
        for number, point in enumerate(report.pain_points, start=1)
    ]
    requirements.append(
        Requirement(
            id=f"req_{len(requirements) + 1:03d}",
            statement=f"The product must cost less than {gap.product_name}.",
            priority="should",
            evidence=[gap.id],
        )
    )
    return Prd(
        problem=f"Users of the existing products are let down. Idea: {run_input.idea}",
        users=run_input.target_users,
        goals=["Fix the top pain points."],
        non_goals=["Anything no review asks for."],
        requirements=requirements,
        success_metrics=["Fewer complaints about the top pain points."],
        market_gaps=[gap],
    )


def fake_tasks(
    run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
) -> TaskPlan:
    prd: Prd = earlier_outputs["s4_prd"]
    tasks = [
        Task(
            id=f"task_{number:03d}",
            title=f"Build {requirement.id}",
            description=requirement.statement,
            requirement_ids=[requirement.id],
            depends_on=[f"task_{number - 1:03d}"] if number > 1 else [],
            effort="M",
        )
        for number, requirement in enumerate(prd.requirements, start=1)
    ]
    epic = Epic(id="epic_01", title="First version", description="Everything.", tasks=tasks)
    return TaskPlan(epics=[epic])


def fake_acceptance(
    run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
) -> AcceptanceCriteria:
    plan: TaskPlan = earlier_outputs["s5_tasks"]
    criteria = [
        Criterion(kind="happy_path", given="a signed-in user", when="they use it", then="it works"),
        Criterion(kind="failure_state", given="no network", when="they use it", then="it says so"),
    ]
    return AcceptanceCriteria(
        tasks=[TaskCriteria(task_id=task.id, criteria=criteria) for task in plan.tasks]
    )


FAKE_STAGES: dict[str, Stage] = {
    "s1_competitors": fake_competitors,
    "s2_reviews": fake_reviews,
    "s3_pain_points": fake_pain_points,
    "s4_prd": fake_prd,
    "s5_tasks": fake_tasks,
    "s6_roadmap": roadmap_stage,  # the pass-through makes no outside request
    "s7_acceptance": fake_acceptance,
}

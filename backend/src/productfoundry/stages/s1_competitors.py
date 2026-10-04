"""Stage 1: an incumbent or an idea becomes a competitor list for review."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, model_validator

from productfoundry.core.base import NonEmptyStr
from productfoundry.core.competitors import Competitor, CompetitorList, RejectedCandidate
from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.ids import product_id
from productfoundry.core.names import normalise_name, same_product
from productfoundry.core.run_input import Platform, RunInput, StoreIds
from productfoundry.orchestrator.protocols import Services
from productfoundry.sources import SourceError
from productfoundry.sources.search import SearchResult

TASK = "competitor_filtering"
PROMPT = (Path(__file__).parent / "prompts" / "competitor_filtering_v1.md").read_text(
    encoding="utf-8"
)
SHARED_HOSTS = {"play.google.com", "apps.apple.com", "itunes.apple.com", "github.com"}
STORE_OF_PLATFORM = {Platform.ANDROID: "google_play", Platform.IOS: "app_store"}


@dataclass(frozen=True)
class Limits:
    """Bounds on what one run of the stage may request."""

    max_queries: int = 3
    results_per_query: int = 8
    max_store_lookups: int = 6  # competitors looked up in the stores, incumbent excluded


class KeptCandidate(BaseModel):
    name: NonEmptyStr
    url: str | None = None
    positioning: NonEmptyStr
    target_users: NonEmptyStr
    reason: NonEmptyStr
    results: list[int] = []


class RejectedOut(BaseModel):
    name: NonEmptyStr
    url: str | None = None
    reason: NonEmptyStr


class CompetitorFilter(BaseModel):
    """What the LLM returns. `results` are the numbers of the search results cited."""

    kept: list[KeptCandidate]
    rejected: list[RejectedOut] = []


def filter_schema(result_count: int, exempt: Sequence[str]) -> type[CompetitorFilter]:
    """`CompetitorFilter` that also rejects a kept product with no real result behind it.

    Products named in `exempt` (the incumbent and the user's known competitors)
    need no citation: the user is their evidence.
    """

    class GroundedCompetitorFilter(CompetitorFilter):
        @model_validator(mode="after")
        def _grounded(self) -> "GroundedCompetitorFilter":
            for candidate in self.kept:
                unknown = [n for n in candidate.results if not 1 <= n <= result_count]
                if unknown:
                    raise ValueError(f"{candidate.name} cites results that do not exist: {unknown}")
                if not candidate.results and not _named(candidate.name, exempt):
                    raise ValueError(f"{candidate.name} cites no search result")
            return self

    GroundedCompetitorFilter.__name__ = CompetitorFilter.__name__
    return GroundedCompetitorFilter


def build_queries(run_input: RunInput, limit: int) -> list[str]:
    queries = []
    if run_input.incumbent is not None:
        name = run_input.incumbent.name
        queries += [f"{name} alternatives and competitors", f"apps like {name}"]
    queries.append(f"best apps for {run_input.target_users}: {run_input.idea}")
    queries.append(f"{run_input.idea} app")
    return queries[:limit]


class CompetitorStage:
    def __init__(self, limits: Limits | None = None) -> None:
        self._limits = limits or Limits()

    def __call__(
        self, run_input: RunInput, earlier_outputs: Mapping[str, BaseModel], services: Services
    ) -> CompetitorList:
        if services.search is None:
            raise ProductFoundryError("competitor research needs a search API key (TAVILY_API_KEY)")
        if services.llm is None:
            raise ProductFoundryError("competitor research needs the LLM gateway")

        results = self._gather(run_input, services)
        exempt = list(run_input.known_competitors)
        if run_input.incumbent is not None:
            exempt.append(run_input.incumbent.name)
        answer = services.llm.complete(
            TASK, _messages(run_input, results), filter_schema(len(results), exempt)
        )

        kept = _merge(answer.kept)
        competitors = []
        if run_input.incumbent is not None:
            competitors.append(_incumbent(run_input, kept, results, services))
        lookups_left = self._limits.max_store_lookups
        for candidate in kept:
            if run_input.incumbent and same_product(candidate.name, run_input.incumbent.name):
                continue
            store_ids, store_url = StoreIds(), None
            if lookups_left > 0:
                lookups_left -= 1
                store_ids, store_url = _store_ids(candidate.name, run_input, services)
            competitors.append(_competitor(candidate, results, store_ids, store_url))
        if not competitors:
            raise ProductFoundryError("no competitors were found in the search results")

        names = [competitor.name for competitor in competitors]
        return CompetitorList(
            competitors=competitors,
            rejected=[
                RejectedCandidate(
                    name=rejected.name,
                    url=_shown_url(rejected.url, results),
                    reason=rejected.reason,
                )
                for rejected in _unique(answer.rejected)
                if not _named(rejected.name, names)
            ],
        )

    def _gather(self, run_input: RunInput, services: Services) -> list[SearchResult]:
        seen: dict[str, SearchResult] = {}
        for query in build_queries(run_input, self._limits.max_queries):
            found = services.search.search(
                query, max_results=self._limits.results_per_query, region=run_input.region
            )
            for result in found:
                seen.setdefault(result.url, result)
        return list(seen.values())


def _messages(run_input: RunInput, results: Sequence[SearchResult]) -> list[dict[str, str]]:
    payload = {
        "product": {
            "idea": run_input.idea,
            "target_users": run_input.target_users,
            "platforms": [platform.value for platform in run_input.platforms],
            "region": run_input.region,
            "incumbent": run_input.incumbent.name if run_input.incumbent else None,
        },
        "known_competitors": run_input.known_competitors,
        "results": [
            {"n": n, "title": result.title, "url": result.url, "snippet": result.snippet}
            for n, result in enumerate(results, start=1)
        ],
    }
    return [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)},
    ]


def _named(name: str, names: Sequence[str]) -> bool:
    return any(same_product(name, other) for other in names)


def _unique(candidates: Sequence[RejectedOut]) -> list[RejectedOut]:
    seen: dict[str, RejectedOut] = {}
    for candidate in candidates:
        seen.setdefault(normalise_name(candidate.name), candidate)
    return list(seen.values())


def _merge(candidates: Sequence[KeptCandidate]) -> list[KeptCandidate]:
    """One entry per product: the first mention, with every mention's citations."""
    merged: list[KeptCandidate] = []
    for candidate in candidates:
        for index, existing in enumerate(merged):
            if same_product(existing.name, candidate.name) or _same_site(
                existing.url, candidate.url
            ):
                merged[index] = existing.model_copy(
                    update={
                        "url": existing.url or candidate.url,
                        "results": sorted({*existing.results, *candidate.results}),
                    }
                )
                break
        else:
            merged.append(candidate)
    return merged


def _host(url: str) -> str:
    return urlsplit(url).netloc.casefold().removeprefix("www.")


def _same_site(first: str | None, second: str | None) -> bool:
    """Two products on one website are one product, unless the website is a store."""
    if not first or not second:
        return False
    return _host(first) == _host(second) and _host(first) not in SHARED_HOSTS


def _shown_url(url: str | None, results: Sequence[SearchResult]) -> str | None:
    """The URL only if a search result is on the same host. An invented URL is dropped."""
    if url and _host(url) in {_host(result.url) for result in results}:
        return url
    return None


def _store_ids(
    name: str, run_input: RunInput, services: Services, known: StoreIds | None = None
) -> tuple[StoreIds, str | None]:
    """Store ids for the run's platforms. Ids the user gave are kept, not looked up."""
    found: dict[str, str] = known.model_dump(exclude_none=True) if known else {}
    url = None
    for platform in run_input.platforms:
        store = STORE_OF_PLATFORM.get(platform)
        lookup = services.app_lookups.get(store) if store else None
        if lookup is None or store in found:
            continue
        try:
            app = lookup.find(name, run_input.region)
        except SourceError:
            continue  # a store that cannot be read leaves the id empty; the user can add it
        if app is not None:
            found[store] = app.store_id
            url = url or app.url
    return StoreIds(**found), url


def _competitor(
    candidate: KeptCandidate,
    results: Sequence[SearchResult],
    store_ids: StoreIds,
    store_url: str | None,
) -> Competitor:
    cited = [results[n - 1].url for n in candidate.results]
    url = _shown_url(candidate.url, results) or store_url or (cited[0] if cited else None)
    if url is None:
        raise ProductFoundryError(f"no URL is known for {candidate.name}; add one and try again")
    return Competitor(
        id=_product_id(candidate.name, store_ids),
        name=candidate.name,
        url=url,
        positioning=candidate.positioning,
        target_users=candidate.target_users,
        store_ids=store_ids,
        reason=candidate.reason,
    )


def _incumbent(
    run_input: RunInput,
    kept: Sequence[KeptCandidate],
    results: Sequence[SearchResult],
    services: Services,
) -> Competitor:
    incumbent = run_input.incumbent
    described = next((c for c in kept if same_product(c.name, incumbent.name)), None)
    store_ids, store_url = _store_ids(incumbent.name, run_input, services, incumbent.store_ids)
    urls = [*incumbent.urls]
    if described is not None:
        shown = _shown_url(described.url, results)
        urls += [shown] if shown else []
        urls += [results[n - 1].url for n in described.results]
    urls += [store_url] if store_url else []
    if not urls:
        raise ProductFoundryError(
            f"no URL is known for the incumbent {incumbent.name}; add one to the run input"
        )
    return Competitor(
        id=_product_id(incumbent.name, store_ids),
        name=incumbent.name,
        url=urls[0],
        positioning=described.positioning if described else "The incumbent named by the user.",
        target_users=described.target_users if described else run_input.target_users,
        store_ids=store_ids,
        reason="The incumbent named in the run input.",
        is_incumbent=True,
    )


def _product_id(name: str, store_ids: StoreIds) -> str:
    """Stable across runs, so stored reviews of the same product are reused."""
    return product_id(store_ids.google_play or store_ids.app_store or normalise_name(name))


competitors_stage = CompetitorStage()

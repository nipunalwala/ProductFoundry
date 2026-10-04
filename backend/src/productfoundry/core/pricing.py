"""A product's public pricing at one moment, and the check that ties it to the page.

Every price in a snapshot must be written on the page it came from. The check is
a pure function of the page text, so an invented number never reaches storage.
"""

import re
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import Field, PositiveInt, StringConstraints, model_validator

from productfoundry.core.base import NonEmptyStr, Schema, Url
from productfoundry.core.ids import ProductId


class BillingPeriod(StrEnum):
    MONTH = "month"
    YEAR = "year"  # the amount is what a year costs, not the monthly equivalent
    ONE_TIME = "one_time"


Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]

# How a page may write each currency. A currency the table does not know is checked
# by its code alone.
CURRENCY_MARKS: dict[str, tuple[str, ...]] = {
    "INR": ("₹", "rs", "inr"),
    "USD": ("$", "usd"),
    "EUR": ("€", "eur"),
    "GBP": ("£", "gbp"),
}


class Price(Schema):
    amount: Decimal = Field(gt=0)
    currency: Currency
    period: BillingPeriod
    per_seat: bool = False  # charged per user or seat
    # True when the page shows a monthly figure for a plan billed once a year.
    billed_annually: bool = False


class Plan(Schema):
    name: NonEmptyStr
    prices: list[Price] = []  # one per currency and period the page shows
    is_free: bool = False  # costs nothing; it has no prices
    contact_sales: bool = False  # the page shows no price, only "contact us"
    limits: list[NonEmptyStr] = []  # usage limits, as the page writes them
    features: list[NonEmptyStr] = []

    @model_validator(mode="after")
    def _check(self) -> "Plan":
        if self.is_free and self.prices:
            raise ValueError(f"plan {self.name!r} is free, so it has no prices")
        if not (self.is_free or self.contact_sales or self.prices):
            raise ValueError(f"plan {self.name!r} needs a price, or is_free, or contact_sales")
        keys = [(p.currency, p.period, p.billed_annually) for p in self.prices]
        if len(set(keys)) != len(keys):
            raise ValueError(f"plan {self.name!r} repeats a price for one currency and period")
        return self


class PricingSnapshot(Schema):
    schema_version: Literal[1] = 1
    product_id: ProductId
    url: Url
    fetched_at: datetime
    text_hash: NonEmptyStr  # sha256 of the page text the plans were read from
    plans: list[Plan] = Field(min_length=1)
    free_tier: bool  # a plan that costs nothing exists
    free_trial: bool
    trial_days: PositiveInt | None = None

    @model_validator(mode="after")
    def _check(self) -> "PricingSnapshot":
        names = [plan.name.casefold() for plan in self.plans]
        if len(set(names)) != len(names):
            raise ValueError("plan names must be unique")
        if self.free_tier != any(plan.is_free for plan in self.plans):
            raise ValueError("free_tier must say whether a free plan is listed")
        if self.trial_days is not None and not self.free_trial:
            raise ValueError("trial_days needs free_trial")
        return self

    def currencies(self) -> list[str]:
        return sorted({price.currency for plan in self.plans for price in plan.prices})


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_on_page(text: str) -> set[Decimal]:
    """Every number written in the text. Thousands separators are dropped, Western
    ("1,499") and Indian ("1,49,999") alike."""
    found = set()
    for match in _NUMBER.findall(text):
        try:
            found.add(Decimal(match.replace(",", "")))
        except InvalidOperation:  # pragma: no cover - the pattern only matches numbers
            continue
    return found


def pricing_problems(plans: Sequence[Plan], trial_days: int | None, page_text: str) -> list[str]:
    """Every figure in `plans` that the page does not show.

    A price passes when its amount is written on the page and the page shows its
    currency, by symbol or by code. A plan's name must be on the page too.
    """
    numbers = numbers_on_page(page_text)
    folded = page_text.casefold()
    problems = []
    for plan in plans:
        if plan.name.casefold() not in folded:
            problems.append(f"the page names no plan {plan.name!r}")
        for price in plan.prices:
            if price.amount not in numbers:
                problems.append(f"{plan.name}: the page does not show the amount {price.amount}")
            marks = CURRENCY_MARKS.get(price.currency, (price.currency.casefold(),))
            if not any(mark in folded for mark in marks):
                problems.append(f"{plan.name}: the page does not show {price.currency}")
    if trial_days is not None and Decimal(trial_days) not in numbers:
        problems.append(f"the page does not show a trial of {trial_days} days")
    return problems


class PricingStore(Protocol):
    def add(self, snapshot: PricingSnapshot, product_name: str) -> None: ...

    def latest(self, product_id: str, url: str | None = None) -> PricingSnapshot | None: ...

    def history(self, product_id: str) -> list[PricingSnapshot]:
        """The product's snapshots, oldest first."""
        ...

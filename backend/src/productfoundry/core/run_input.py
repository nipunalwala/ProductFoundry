"""`RunInput`: what the user gives to start a run (ARCHITECTURE.md section 5)."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, StringConstraints, model_validator

from productfoundry.core.base import NonEmptyStr, Schema, Url
from productfoundry.core.regions import REGIONS


class Mode(StrEnum):
    NEW_IDEA = "new_idea"
    ALTERNATIVE = "alternative"
    NEW_FEATURE = "new_feature"


class Platform(StrEnum):
    ANDROID = "android"
    IOS = "ios"
    WEB = "web"


def _normalise_region(value: object) -> object:
    if not isinstance(value, str):
        return value
    code = value.strip().upper()
    if code not in REGIONS:
        raise ValueError("must be an ISO 3166-1 alpha-2 country code, for example 'IN'")
    return code


Region = Annotated[str, BeforeValidator(_normalise_region)]

GooglePlayId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$"
    ),
]
AppStoreId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]+$")]


class StoreIds(Schema):
    google_play: GooglePlayId | None = None  # package name: com.example.app
    app_store: AppStoreId | None = None  # numeric track id


class Incumbent(Schema):
    name: NonEmptyStr
    urls: list[Url] = []
    store_ids: StoreIds = StoreIds()


class RunInput(Schema):
    schema_version: Literal[1] = 1
    mode: Mode
    idea: NonEmptyStr
    target_users: NonEmptyStr
    platforms: list[Platform] = Field(min_length=1)
    region: Region
    incumbent: Incumbent | None = None
    known_competitors: list[NonEmptyStr] = []
    tech_stack: list[NonEmptyStr] = []

    @model_validator(mode="after")
    def _check(self) -> "RunInput":
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError("platforms must not repeat")
        if self.mode is Mode.ALTERNATIVE and self.incumbent is None:
            raise ValueError("mode 'alternative' requires an incumbent")
        return self

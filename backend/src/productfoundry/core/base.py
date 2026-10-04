"""Shared building blocks for every schema in `core`."""

from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints


class Schema(BaseModel):
    """Base for all contracts: unknown fields are an error and values are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _check_http_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("must be an http or https URL")
    return value


Url = Annotated[NonEmptyStr, AfterValidator(_check_http_url)]


def schema_version_of(model: type[BaseModel]) -> int:
    """The version a versioned schema declares as the default of `schema_version`."""
    return model.model_fields["schema_version"].default

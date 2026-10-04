"""Comparing product names that sources write differently. Pure functions."""

import re

_SUBTITLE = re.compile(r"\s+[-–—|]\s+|[:(,]")
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")
_MIN_PREFIX = 4


def normalise_name(name: str) -> str:
    """'Splitwise: Split Bills' and 'splitwise app' both become 'splitwise'."""
    head = _SUBTITLE.split(name.strip(), maxsplit=1)[0]
    key = _NOT_ALNUM.sub("", head.casefold())
    return key.removesuffix("app") or key


def same_product(first: str, second: str) -> bool:
    a, b = sorted((normalise_name(first), normalise_name(second)), key=len)
    if not a:
        return False
    return a == b or (len(a) >= _MIN_PREFIX and b.startswith(a))

"""Edits a user makes at a checkpoint, as pure functions on the stage output."""

from collections.abc import Mapping, Sequence
from typing import Any

from productfoundry.core.errors import ProductFoundryError
from productfoundry.core.ids import product_id
from productfoundry.core.names import normalise_name


def edit_competitors(
    output: Mapping[str, Any], *, remove: Sequence[str] = (), add: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """Remove competitors by name or id and add new ones. The result is validated on approval."""
    competitors = [dict(competitor) for competitor in output["competitors"]]
    for target in remove:
        key = normalise_name(target)
        kept = [c for c in competitors if c["id"] != target and normalise_name(c["name"]) != key]
        if len(kept) == len(competitors):
            names = ", ".join(c["name"] for c in competitors)
            raise ProductFoundryError(f"no competitor named {target!r}; the list has: {names}")
        competitors = kept
    for new in add:
        if not isinstance(new, Mapping) or not new.get("name"):
            raise ProductFoundryError("each added competitor is an object with at least a name")
        competitor = dict(new)
        store_ids = competitor.get("store_ids") or {}
        key = store_ids.get("google_play") or store_ids.get("app_store")
        competitor.setdefault("id", product_id(key or normalise_name(competitor["name"])))
        competitor.setdefault("reason", "Added by the user at the checkpoint.")
        competitors.append(competitor)
    return {**output, "competitors": competitors}

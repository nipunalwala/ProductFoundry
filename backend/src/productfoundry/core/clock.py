from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware UTC now. Storage never holds a naive datetime."""
    return datetime.now(UTC)
